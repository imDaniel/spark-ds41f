#!/usr/bin/env python3
"""Bounded isolated Q03 session. No original checkout sync or policy mutation."""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import threading
import uuid
import importlib.util
import contextlib
import stat
import select

sys.dont_write_bytecode = True

G1_TEST_FILE = "tests/kernels/test_deepseek_v41_mhc_three_way.py"
G1_PRESERVED_FILES = (
    "tests/conftest.py", "tests/kernels/conftest.py",
    "tests/kernels/deepseek_v41_mhc_baseline.py",
    "tests/kernels/fixtures/mhc_188/mhc.py.txt",
    "tests/kernels/fixtures/mhc_188/provenance.json",
    "tests/kernels/test_deepseek_v41_q03_owner.py",
    "tests/kernels/test_deepseek_v41_tilelang_mhc.py",
    "tests/kernels/q03_result_receipt.py",
)


def g1_tests_path(candidate):
    return candidate["g1_tests"]["path"] if "g1_tests" in candidate else candidate["tests_path"]


def g1_artifact_evidence(candidate, selection):
    """Read pinned publication metadata, without importing test or artifact code."""
    test = candidate["g1_tests"]
    artifact = test["artifact"]
    relative = Path(artifact["source_path"])
    if relative.is_absolute() or ".." in relative.parts or relative.name != "source.json":
        raise ValueError("G1 artifact source must be a repository-relative source.json")
    source_path = Path(artifact["checkout"]) / relative
    if source_path.resolve() != source_path or not source_path.is_relative_to(Path(artifact["checkout"])):
        raise ValueError("G1 artifact source path is not canonical inside its checkout")
    raw = source_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != artifact["source_sha256"]:
        raise ValueError("G1 artifact source hash differs")
    source = json.loads(raw)
    proof = source["q03_g1_test"]
    if (source["vllm"]["patch_head"] != test["head"]
            or source["vllm"]["expected_tree"] != test["tree"]
            or proof["fixture_path"] != G1_TEST_FILE
            or proof["fixture_sha256"] != test["fixture_sha256"]
            or proof["runtime_head"] != selection["source_head"]
            or proof["runtime_tree"] != selection["source_tree"]
            or type(proof["count"]) is not int or proof["count"] != 20):
        raise ValueError("G1 publication does not bind test/runtime identities")
    expected_relative = Path(proof["expected_path"])
    if expected_relative.is_absolute() or ".." in expected_relative.parts:
        raise ValueError("G1 expected inventory must stay inside artifact directory")
    expected_path = source_path.parent / expected_relative
    if expected_path.resolve() != expected_path or not expected_path.is_relative_to(source_path.parent):
        raise ValueError("G1 expected inventory path is not canonical inside its artifact")
    expected_raw = expected_path.read_bytes()
    if hashlib.sha256(expected_raw).hexdigest() != proof["expected_sha256"]:
        raise ValueError("G1 published expected inventory hash differs")
    expected = json.loads(expected_raw)
    original_spec = selection["expected"]["G1"]
    original_path = Path(__file__).resolve().parents[1] / "experiments/2026-10-21-mhc-input-validation" / original_spec["path"]
    original_raw = original_path.read_bytes()
    if hashlib.sha256(original_raw).hexdigest() != original_spec["sha256"]:
        raise ValueError("original197 G1 expected inventory changed")
    original = json.loads(original_raw)
    if (expected.get("schema_version") != 1 or expected.get("stage") != "G1"
            or expected.get("source_tree") != test["tree"]
            or expected.get("nodeids") != original["nodeids"]
            or len(expected["nodeids"]) != 20):
        raise ValueError("G1 test revision changes the original20-case inventory")
    return source_path, expected_path, expected_raw, proof


def validate_g1_ast(original, corrected):
    import ast
    def functions(raw):
        nodes = [node for node in ast.parse(raw).body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        if len({node.name for node in nodes}) != len(nodes):
            raise ValueError("duplicate G1 function definitions")
        return {node.name: ast.dump(node, include_attributes=False) for node in nodes}
    before, after = functions(original), functions(corrected)
    def fixture(raw):
        return next(node for node in ast.parse(raw).body
                    if isinstance(node, ast.FunctionDef) and node.name == "three_way_kernels")
    old_fixture, new_fixture = fixture(original), fixture(corrected)
    dump = lambda node: ast.dump(node, include_attributes=False)
    if ([dump(node) for node in old_fixture.decorator_list] !=
            [dump(node) for node in new_fixture.decorator_list]
            or dump(old_fixture.args) != dump(new_fixture.args)):
        raise ValueError("G1 compilation fixture interface or scope changed")
    old_assertions = {dump(node) for node in ast.walk(old_fixture) if isinstance(node, ast.Assert)}
    new_assertions = {dump(node) for node in ast.walk(new_fixture) if isinstance(node, ast.Assert)}
    if not old_assertions.issubset(new_assertions):
        raise ValueError("G1 compilation fixture identity assertions removed or changed")
    if {name for name in after if name.startswith("test_")} != {
            name for name in before if name.startswith("test_")}:
        raise ValueError("G1 test-function inventory changed")
    for name, node in before.items():
        if name != "three_way_kernels" and after.get(name) != node:
            raise ValueError("G1 numerical test/helper body or decorators changed: " + name)


class SessionInterrupted(RuntimeError):
    """Signals close a held outer window; they are never a repair pause."""


def held_container_projection(item):
    """Normalize known unordered Docker lists; retain all fields and duplicates."""
    projection = {key: item[key] for key in ("Image", "Config", "HostConfig", "Mounts")}
    mounts = projection["Mounts"]
    if not isinstance(mounts, list) or not all(isinstance(mount, dict) for mount in mounts):
        raise ValueError("held container mount projection is invalid")
    # Docker 29 GetMountPoints iterates a Go map. sort_keys affects object keys,
    # not this array, so normalize before both pre-start and cleanup digests.
    projection["Mounts"] = sorted(mounts, key=lambda mount: json.dumps(
        mount, sort_keys=True, separators=(",", ":"), allow_nan=False))
    host = dict(projection["HostConfig"])
    if host.get("Ulimits") is not None:
        limits = host["Ulimits"]
        if not isinstance(limits, list) or not all(isinstance(limit, dict) for limit in limits):
            raise ValueError("held container ulimit projection is invalid")
        # Docker 29 merges daemon-default Ulimits by map iteration during inspect.
        host["Ulimits"] = sorted(limits, key=lambda limit: json.dumps(
            limit, sort_keys=True, separators=(",", ":"), allow_nan=False))
    projection["HostConfig"] = host
    return projection


def held_container_sha256(item):
    return hashlib.sha256(json.dumps(held_container_projection(item), sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


TBLIB_WHEEL = "tblib-3.1.0-py3-none-any.whl"
TBLIB_SHA256 = "670bb4582578134b3d81a84afa1b016128b429f3d48e6cbbaecc9d15675e984e"
# This fixed bootstrap is part of the already capsule-pinned controller source.
# Append only the genuine pure-Python wheel; never replace image PYTHONPATH.
TBLIB_BOOTSTRAP = r"""
import hashlib, os, pathlib, sys
if os.environ.get('PYTHONPATH') != os.environ['Q03_EXPECTED_PYTHONPATH']:
    raise RuntimeError('image PYTHONPATH binding mismatch')
wheel = pathlib.Path('/pytest-dependency/tblib-3.1.0-py3-none-any.whl')
if hashlib.sha256(wheel.read_bytes()).hexdigest() != '670bb4582578134b3d81a84afa1b016128b429f3d48e6cbbaecc9d15675e984e':
    raise RuntimeError('tblib wheel hash mismatch')
sys.path.append(str(wheel))
import tblib
if tblib.__version__ != '3.1.0' or tblib.__file__ != str(wheel) + '/tblib/__init__.py':
    raise RuntimeError('tblib version or import origin mismatch')
import pytest
sys.exit(pytest.main(sys.argv[1:]))
"""


def needs_tblib(manifest):
    return any(job.get("kind") in ("g1", "g2") for job in manifest.get("jobs", []))


def validate_tblib_dependency(candidate):
    """Read-only validation before hold/STOP; no dependency code is imported."""
    import io
    import zipfile
    root = Path(candidate["tblib_path"])
    if not root.is_absolute() or root.resolve() != root or not root.is_dir():
        raise ValueError("tblib bundle must be a canonical directory")
    for parent in (root, *root.parents):
        info = parent.stat()
        if info.st_uid not in (0, os.getuid()) or info.st_mode & 0o022:
            raise ValueError("tblib ancestry is not owner-controlled")
    if {p.name for p in root.iterdir()} != {TBLIB_WHEEL, "manifest.json"}:
        raise ValueError("tblib bundle inventory differs")
    def read(name):
        path = root / name
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, os.getuid())
                    or info.st_mode & 0o022):
                raise ValueError("tblib file ownership/type/mode differs")
            return stream.read()
    raw = read("manifest.json")
    if hashlib.sha256(raw).hexdigest() != candidate["tblib_manifest_sha256"]:
        raise ValueError("tblib manifest hash mismatch")
    record = json.loads(raw)
    wheel = read(TBLIB_WHEEL)
    if hashlib.sha256(wheel).hexdigest() != TBLIB_SHA256:
        raise ValueError("official tblib wheel hash mismatch")
    with zipfile.ZipFile(io.BytesIO(wheel)) as archive:
        members = {name: {"sha256": hashlib.sha256(archive.read(name)).hexdigest(),
                          "size": len(archive.read(name))} for name in archive.namelist()}
    if (record.get("schema_version") != 1 or record.get("package") != "tblib"
            or record.get("version") != "3.1.0" or record.get("wheel") != TBLIB_WHEEL
            or record.get("wheel_sha256") != TBLIB_SHA256
            or record.get("wheel_size") != len(wheel) or record.get("requires_dist") != []
            or record.get("license") != "BSD-2-Clause" or record.get("members") != members):
        raise ValueError("tblib manifest does not describe the pinned wheel")
    return record


# The leader remains alive until its local process group is quiescent. The
# actual command starts only after the parent has durably journalled this PID.
COMMAND_SUPERVISOR = r"""
import os, pathlib, signal, subprocess, sys, time
fd = int(sys.argv[1])
permit = os.read(fd, 1)
os.close(fd)
if permit != b'1':
    sys.exit(125)
interrupted = False
def stop(signum, frame):
    global interrupted
    interrupted = True
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
child = subprocess.Popen(sys.argv[2:])
while child.poll() is None and not interrupted:
    time.sleep(.1)
if interrupted:
    # Only the still-live group leader signals its own reserved group.
    os.killpg(os.getpgrp(), signal.SIGTERM)
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgrp(), signal.SIGKILL)
result = child.wait()
def members():
    found = []
    for path in pathlib.Path('/proc').glob('[0-9]*/stat'):
        try:
            pid = int(path.parent.name)
            fields = path.read_text().rsplit(')', 1)[1].split()
            if pid != os.getpid() and int(fields[2]) == os.getpgrp() and fields[0] != 'Z':
                found.append(pid)
        except (FileNotFoundError, ProcessLookupError):
            pass
        except BaseException:
            # Unknown local descendant state cannot be reported quiescent.
            os.killpg(os.getpgrp(), signal.SIGKILL)
    return found
remaining = members()
if remaining:
    os.killpg(os.getpgrp(), signal.SIGTERM)
    until = time.monotonic() + 10
    while members() and time.monotonic() < until:
        time.sleep(.1)
    if members():
        os.killpg(os.getpgrp(), signal.SIGKILL)
sys.exit(128 + signal.SIGTERM if interrupted else result)
"""


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def atomic(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def process_identity(pid=None):
    pid = os.getpid() if pid is None else pid
    value = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return {"pid": pid, "start_ticks": value[19]}


def owned_process(record):
    try:
        return process_identity(record["pid"]) == record
    except (OSError, KeyError, ValueError):
        return False


def command_environment():
    """Bind host Docker operations to the local daemon, not inherited selectors."""
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("DOCKER_", "BUILDX_", "BUILDKIT_"))}
    env.update(GIT_OPTIONAL_LOCKS="0", PYTHONDONTWRITEBYTECODE="1",
               DOCKER_HOST="unix:///var/run/docker.sock")
    return env


class RuntimeOps:
    def read_json(self, path):
        return json.loads(Path(path).read_text())

    def sha256(self, path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def resolve(self, path):
        return str(Path(path).resolve())

    sleep = staticmethod(time.sleep)
    process_identity = staticmethod(process_identity)

    def run(self, argv, *, cwd=None, timeout=60, check=True):
        return subprocess.run(argv, cwd=cwd, timeout=timeout, text=True,
                              capture_output=True, check=check,
                              env=command_environment())

    def acquire_gpu_lock(self):
        fd = os.open("/tmp/spark-lab-gpu.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise RuntimeError("shared GPU lock is not a regular file")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return os.fdopen(fd, "r+")
        except BaseException:
            os.close(fd)
            raise

    def recovery_sources(self):
        names = ("q03_window.py", "q03_window_preflight.py", "q03_window_watchdog.py",
                 "q03_original_spark_adapter.py", "q03_remote_operation.py", "q03_build_adapter.py",
                 "q03_window_build.py", "q03_buildkit_plan.py", "q03_git_toolchain.py",
                 "q03_held_window.py")
        return {name: self.sha256(Path(__file__).with_name(name)) for name in names}

    def validate_host(self, manifest):
        checkout = Path(manifest["preflight"]["independent_checkout"]).resolve()
        if Path(__file__).resolve() != checkout / "scripts/q03_window.py":
            raise ValueError("running controller is not from the pinned independent checkout")
        root = Path(manifest["independent_root"])
        if not root.is_dir():
            raise ValueError("approved independent root must already exist")
        for parent in (root, *root.parents):
            info = parent.stat()
            if info.st_uid not in (0, os.getuid()) or info.st_mode & 0o022:
                raise ValueError("independent root ancestry is not owner-controlled")
        if build_only(manifest):
            return
        selection = json.loads((Path(__file__).resolve().parents[1] /
            "experiments/2026-10-21-mhc-input-validation/validation-selection.json").read_text())
        candidate = manifest["candidate"]
        if candidate["component_head"] != selection["source_head"]:
            raise ValueError("component identity differs from published selection")
        if capture_only(manifest):
            from q03_window_build import validate_candidate_build
            if not Path(candidate["output_path"]).is_dir():
                raise ValueError("capture output directory is missing")
            validate_candidate_build(manifest, self)
            return
        tests = candidate["tests_path"]
        for key in ("tests_path", "cache_path", "output_path"):
            if not Path(candidate[key]).is_dir():
                raise ValueError("candidate mount directory is missing: " + key)
        if not Path(candidate["runtime_manifest_path"]).is_file():
            raise ValueError("runtime manifest is missing")
        from q03_git_toolchain import validate_git_metadata
        validate_git_metadata(tests, candidate["git_config_sha256"])
        git = ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
               "-C", tests]
        head = self.run([*git, "rev-parse", "HEAD"]).stdout.strip()
        tree = self.run([*git, "rev-parse", "HEAD^{tree}"]).stdout.strip()
        dirty = self.run([*git, "status", "--porcelain"]).stdout.strip()
        if head != selection["source_head"] or tree != selection["source_tree"] or dirty:
            raise ValueError("tests must be the clean exact materialized component")
        if not (Path(tests) / ".git").is_dir():
            raise ValueError("test mount requires a self-contained git checkout")
        self.validate_g1_test_source(manifest, selection)
        self.validate_candidate_image(manifest, selection)
        self.validate_git_toolchain(manifest)
        if needs_tblib(manifest):
            validate_tblib_dependency(candidate)

    def validate_g1_test_source(self, manifest, selection):
        candidate = manifest["candidate"]
        if "g1_tests" not in candidate:
            return
        test = candidate["g1_tests"]
        if not isinstance(test, dict) or set(test) != {
                "path", "head", "tree", "fixture_sha256", "git_config_sha256", "artifact"}:
            raise ValueError("G1 test identity descriptor is incomplete")
        for key, size in (("head", 40), ("tree", 40), ("fixture_sha256", 64)):
            if not re.fullmatch(r"[0-9a-f]{%d}" % size, test.get(key, "")):
                raise ValueError("invalid G1 identity pin: " + key)
        if not any(job.get("kind") == "g1" for job in manifest["jobs"]):
            raise ValueError("G1 test override requires a G1 job")
        artifact = test["artifact"]
        if not isinstance(artifact, dict) or set(artifact) != {
                "checkout", "publication", "source_path", "source_sha256", "git_config_sha256"}:
            raise ValueError("G1 published artifact binding is incomplete")
        from q03_git_toolchain import validate_git_metadata
        from q03_window_preflight import verify_build_publication
        def git_at(path, config_sha):
            validate_git_metadata(path, config_sha)
            return ["git", "--no-replace-objects", "-c", "core.fsmonitor=false",
                    "-c", "core.hooksPath=/dev/null", "-C", path]
        def run(git, *args):
            return self.run([*git, *args]).stdout.strip()
        artifact_git = git_at(artifact["checkout"], artifact["git_config_sha256"])
        publication = artifact["publication"]
        verify_build_publication(publication, self)
        if (run(artifact_git, "remote", "get-url", "origin") != publication["url"]
                or run(artifact_git, "rev-parse", "HEAD") != publication["sha"]
                or run(artifact_git, "status", "--porcelain")):
            raise ValueError("G1 artifact checkout differs from published own-fork source")
        source_path, expected_path, _, _ = g1_artifact_evidence(candidate, selection)
        for path in (source_path, expected_path):
            relative = path.relative_to(Path(artifact["checkout"]))
            published = self.run([*artifact_git, "show", publication["sha"] + ":" + relative.as_posix()]).stdout.encode()
            if hashlib.sha256(published).hexdigest() != self.sha256(path):
                raise ValueError("G1 artifact bytes differ from published Git blob")
        git = git_at(test["path"], test["git_config_sha256"])
        if (run(git, "rev-parse", "HEAD") != test["head"]
                or run(git, "rev-parse", "HEAD^{tree}") != test["tree"]
                or run(git, "status", "--porcelain")):
            raise ValueError("G1 tests must be a clean exact materialized checkout")
        base = selection["source_head"]
        if run(git, "rev-parse", base + "^{tree}") != selection["source_tree"]:
            raise ValueError("G1 checkout lacks the exact197 base")
        if run(git, "diff", "--name-only", "--no-renames", base, "HEAD", "--").splitlines() != [G1_TEST_FILE]:
            raise ValueError("G1 correction changes more than its sole reviewed test file")
        root = Path(test["path"])
        if self.sha256(root / G1_TEST_FILE) != test["fixture_sha256"]:
            raise ValueError("G1 working-file bytes differ from the reviewed correction")
        original_root = Path(candidate["tests_path"])
        validate_g1_ast((original_root / G1_TEST_FILE).read_text(), (root / G1_TEST_FILE).read_text())
        for name in G1_PRESERVED_FILES:
            if self.sha256(root / name) != self.sha256(original_root / name):
                raise ValueError("G1 preserved source bytes differ: " + name)
        for name in ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini"):
            original = original_root / name
            if original.exists() and self.sha256(root / name) != self.sha256(original):
                raise ValueError("G1 project configuration differs: " + name)

    def validate_git_toolchain(self, manifest):
        from q03_git_toolchain import validate_toolchain
        candidate = manifest["candidate"]
        validate_toolchain(candidate, self)
        images = json.loads(self.run(
            ["docker", "image", "inspect", candidate["image"]]).stdout)
        if len(images) != 1 or images[0].get("Id") != candidate["image"]:
            raise ValueError("Git toolchain image binding is ambiguous")
        paths = [item[5:] for item in images[0].get("Config", {}).get("Env", [])
                 if item.startswith("PATH=")]
        if paths != [candidate.get("git_toolchain_base_path")]:
            raise ValueError("Git toolchain must preserve the exact image PATH")
        if needs_tblib(manifest):
            pythonpaths = [item[11:] for item in images[0].get("Config", {}).get("Env", [])
                           if item.startswith("PYTHONPATH=")]
            if pythonpaths != [candidate.get("pytest_base_pythonpath")]:
                raise ValueError("pytest must preserve the exact image PYTHONPATH")

    def validate_candidate_image(self, manifest, selection):
        from q03_window_build import validate_candidate_build
        validate_candidate_build(manifest, self)
        candidate = manifest["candidate"]
        expected = candidate.get("runtime_manifest_sha256", "")
        if (not re.fullmatch(r"[0-9a-f]{64}", expected)
                or self.sha256(candidate["runtime_manifest_path"]) != expected):
            raise ValueError("candidate runtime evidence hash mismatch")
        runtime = self.read_json(candidate["runtime_manifest_path"])
        if runtime.get("source_head") != selection["source_head"]:
            raise ValueError("candidate runtime identity mismatch")

    def check(self, phase, manifest):
        from q03_window_preflight import check
        return check(phase, manifest, self)

    def terminate_owned_process(self, record):
        if not record:
            return
        pid = record["pid"]
        def require_empty_group():
            for path in Path("/proc").glob("[0-9]*/stat"):
                try:
                    fields = path.read_text().rsplit(")", 1)[1].split()
                    if int(fields[2]) == pid and fields[0] != "Z":
                        raise RuntimeError("owned supervisor gone but process group is not proven empty")
                except (FileNotFoundError, ProcessLookupError):
                    continue
        if not owned_process(record):
            require_empty_group()
            return
        try:
            fd = os.pidfd_open(pid, 0)
        except ProcessLookupError:
            require_empty_group()
            return
        try:
            if not owned_process(record) or os.getpgid(pid) != pid:
                raise RuntimeError("supervisor identity changed before termination")
            # Pidfd targets the checked process, never a recycled numeric PGID.
            # Its handler signals and drains its own group while still alive.
            signal.pidfd_send_signal(fd, signal.SIGTERM)
            ready, _, _ = select.select([fd], [], [], 20)
            if not ready:
                raise RuntimeError("supervisor did not prove bounded group shutdown")
            require_empty_group()
        finally:
            os.close(fd)

    def spawn_watchdog(self, directory):
        from q03_window_watchdog import launch_watchdog
        return launch_watchdog(directory)

    def wait_watchdog_ready(self, directory, process):
        from q03_window_watchdog import wait_ready
        return wait_ready(directory, process)

    def execute(self, session, argv, *, cwd, timeout, poll=None):
        if poll is not None:
            poll()
        session.beat()
        limit = session.capsule["deadline_epoch"]
        if session.state.get("phase") == "restoring":
            limit = session.state["restoring_started_epoch"] + session.capsule["recovery_timeout_s"]
        if time.time() >= limit:
            raise TimeoutError("deadline reached before command launch")
        log_path = session.directory / ("command-" + uuid.uuid4().hex + ".log")
        session.update(command_history=session.state.get("command_history", []) + [
            {"argv": argv, "log": str(log_path), "phase": session.state["phase"]}])
        with log_path.open("xb") as log:
            # Child cannot execute an operation until its identity is durable.
            read_fd, write_fd = os.pipe()
            try:
                process = subprocess.Popen(
                    [sys.executable, "-I", "-B", "-c", COMMAND_SUPERVISOR, str(read_fd), *argv], cwd=cwd,
                    stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                    start_new_session=True, pass_fds=(read_fd,),
                    env=command_environment())
                record = process_identity(process.pid)
                session.update(active_process=record, active_command=argv)
                os.write(write_fd, b"1")
            finally:
                os.close(read_fd)
                os.close(write_fd)
            deadline = time.monotonic() + timeout
            try:
                while process.poll() is None:
                    session.beat()
                    if poll is not None:
                        poll()
                    if time.monotonic() >= deadline:
                        raise TimeoutError("command exceeded its reviewed timeout")
                    limit = session.capsule["deadline_epoch"]
                    if session.state.get("phase") == "restoring":
                        limit = session.state["restoring_started_epoch"] + session.capsule["recovery_timeout_s"]
                    if time.time() >= limit:
                        raise TimeoutError("bounded session/recovery deadline reached")
                    time.sleep(1)
                session.update(last_exit_code=process.returncode)
                if process.returncode:
                    raise RuntimeError(f"command exited {process.returncode}")
            finally:
                if process.poll() is None:
                    self.terminate_owned_process(record)
                process.wait(timeout=20)
                session.update(active_process=None)
        return {"exit_code": 0, "log_path": str(log_path)}

    def prepare_build(self, manifest):
        from q03_window_build import validate_build
        return validate_build(manifest, self)

    def validate_result(self, manifest, kind):
        if kind == "build":
            from q03_window_build import verify_build
            return verify_build(manifest, self)
        candidate = manifest["candidate"]
        output = Path(candidate["output_path"])
        if kind == "p0":
            report = self.read_json(output / "p0" / "summary.json")
            results = report.get("results", {})
            if (report.get("result") != "passed" or report.get("head") != candidate["component_head"]
                    or set(results) != {"default", "string"}
                    or any(r.get("exit_code") != 0 or r.get("passed") is not True for r in results.values())):
                raise RuntimeError("P0 receipt is incomplete or failed")
            return
        experiment = Path(__file__).resolve().parents[1] / "experiments/2026-10-21-mhc-input-validation"
        selection = self.read_json(experiment / "validation-selection.json")
        expected = selection["expected"][kind.upper()]
        source = Path(candidate["tests_path"]) / "tests/kernels/q03_result_receipt.py"
        spec = importlib.util.spec_from_file_location("q03_receipt", source)
        receipt = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(receipt)
        expected_bytes = (experiment / expected["path"]).read_bytes()
        expected_sha256 = expected["sha256"]
        test_tree = selection["source_tree"]
        if kind == "g1" and "g1_tests" in candidate:
            _, _, expected_bytes, proof = g1_artifact_evidence(candidate, selection)
            expected_sha256 = proof["expected_sha256"]
            test_tree = candidate["g1_tests"]["tree"]
        report = receipt.check_evidence(
            kind.upper(), expected_bytes, expected_sha256,
            test_tree, (output / (kind + ".xml")).read_bytes(), 0)
        if kind == "g1" and "g1_tests" in candidate:
            report.update(runtime_source_head=selection["source_head"],
                          runtime_source_tree=selection["source_tree"],
                          test_source_head=candidate["g1_tests"]["head"], test_source_tree=test_tree,
                          test_file_sha256=candidate["g1_tests"]["fixture_sha256"],
                          test_artifact_publication=candidate["g1_tests"]["artifact"]["publication"],
                          test_artifact_source_sha256=candidate["g1_tests"]["artifact"]["source_sha256"])
        path = output / (kind + "-receipt.json")
        with path.open("x") as stream:
            json.dump(report, stream, indent=2)

    def validate_capture_result(self, manifest, execution):
        from q03_window_build import validate_native_capture_output
        path = Path(execution["log_path"])
        if path.stat().st_size > 1024 * 1024:
            raise RuntimeError("native capture output exceeds bounded size")
        runtime = validate_native_capture_output(path.read_text())
        candidate = manifest["candidate"]
        if runtime.get("source_head") != candidate["component_head"]:
            raise RuntimeError("captured component identity mismatch")
        output = Path(candidate["output_path"]) / "runtime-manifest.json"
        # Exclusive output: a capture never replaces reviewed runtime evidence.
        with output.open("x") as stream:
            json.dump(runtime, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        return {"status": "captured-unreviewed", "image_id": candidate["image"],
                "source_head": candidate["component_head"], "runtime_manifest_path": str(output),
                "runtime_manifest_sha256": self.sha256(output),
                "requires_independent_review": True}

    def quiesce_original(self, session):
        # Sealing every prior generation prevents a delayed SSH launch from
        # appearing after an apparently empty process/GPU observation.
        for transition in session.state.get("transition_epochs", []):
            argv = original_command("quiesce", session, transition["epoch"])
            result = self.execute(session, argv, cwd=str(session.directory), timeout=360)
            self.require_receipt(result, {"window_id": session.capsule["window_id"],
                                         "epoch": transition["epoch"], "safe_to_overlap": True})

    def require_receipt(self, result, expected):
        path = Path(result["log_path"])
        if path.stat().st_size > 1024 * 1024:
            raise RuntimeError("quiescence receipt exceeds bounded size")
        report = json.loads(path.read_text())
        if not isinstance(report, dict) or any(
                (report.get(k) is not v if isinstance(v, bool) else report.get(k) != v)
                for k, v in expected.items()):
            raise RuntimeError("quiescence receipt is not safe and identity-bound")

    def cleanup_build(self, session):
        if not build_only(session.manifest):
            return
        argv = [sys.executable, "-I", "-B", str(Path(__file__).with_name("q03_build_adapter.py")),
                "quiesce", "--session-dir", str(session.directory)]
        result = self.execute(session, argv, cwd=str(session.directory), timeout=180)
        self.require_receipt(result, {"window_id": session.capsule["window_id"], "safe_to_restore": True})

    def prepare_candidate(self, session, name, argv, *, held_attempt=None):
        # Create cannot run GPU work. Record its exact immutable container ID
        # before a separate start; removal then fences even delayed start RPCs.
        if argv[:2] != ["docker", "run"]:
            raise ValueError("unexpected typed container template")
        if held_attempt is not None:
            from q03_held_window import is_held_manifest
            if not is_held_manifest(session.manifest):
                raise ValueError("held candidate requires a held outer window")
            if any(row["name"] == name for row in session.state.get("held_candidates", [])):
                raise ValueError("held candidate name cannot be reused")
            argv = [*argv[:2], "--label", "q03.plan=" + held_attempt["plan_sha256"], *argv[2:]]
            intent = dict(held_attempt, name=name, node_rank=1,
                          window_id=session.capsule["window_id"], container_id=None,
                          create_argv_sha256=hashlib.sha256(json.dumps(
                              argv, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                          removed=False)
            # Durable before even an inert Docker create can be dispatched.
            session.update(held_candidates=session.state.get("held_candidates", []) + [intent])
        create = ["docker", "create", *argv[2:]]
        self.execute(session, create, cwd=str(session.directory), timeout=90)
        observed = json.loads(self.run(["docker", "container", "inspect", name]).stdout)
        if len(observed) != 1:
            raise RuntimeError("candidate creation has ambiguous identity")
        item = observed[0]
        if (item["Config"].get("Labels", {}).get("q03.window") != session.capsule["window_id"] or
            item["State"].get("Running") is not False or
            not re.fullmatch(r"[0-9a-f]{64}", item.get("Id", ""))):
            raise RuntimeError("candidate creation identity/state differs")
        if held_attempt is not None:
            labels = item["Config"].get("Labels", {})
            if (item.get("Image") != held_attempt["image_id"] or
                    labels.get("q03.attempt") != held_attempt["attempt_id"] or
                    labels.get("q03.job") != held_attempt["kind"] or
                    labels.get("q03.plan") != held_attempt["plan_sha256"]):
                raise RuntimeError("held candidate image/attempt/plan identity differs")
            rows = [dict(row) for row in session.state["held_candidates"]]
            rows[-1].update(container_id=item["Id"], config_sha256=held_container_sha256(item))
            session.update(held_candidates=rows)
            # Even a rejected, inert configuration has a durable exact identity
            # for safe cleanup. No start is returned before plan validation.
            self.validate_held_container(item, argv)
        if capture_only(session.manifest):
            host = item.get("HostConfig", {})
            environment = item["Config"].get("Env", [])
            expected = {"NVIDIA_VISIBLE_DEVICES": "none", "NVIDIA_DRIVER_CAPABILITIES": "compute"}
            if (item.get("Image") != session.manifest["candidate"]["image"]
                    or host.get("Runtime") != "nvidia" or host.get("Devices")
                    or host.get("DeviceRequests") or host.get("Privileged") is not False
                    or host.get("NetworkMode") != "none"
                    or host.get("ReadonlyRootfs") is not True
                    or not isinstance(environment, list)
                    or not all(isinstance(value, str) for value in environment)
                    or any([value for value in environment if value.startswith(key + "=")]
                           != [key + "=" + value] for key, value in expected.items())):
                raise RuntimeError("capture container runtime/device/environment differs")
            projection = {key: item[key] for key in ("Image", "Config", "HostConfig", "Mounts")}
            identity = {"container_id": item["Id"], "image_id": item["Image"],
                        "config_sha256": hashlib.sha256(json.dumps(
                            projection, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                        "runtime": host["Runtime"], "environment": expected,
                        "no_gpu_devices_requested": True}
            session.update(capture_container_identity=identity)
        ids = dict(session.state.get("candidate_container_ids", {}))
        ids[name] = item["Id"]
        session.update(candidate_container_ids=ids)
        return ["docker", "start", "--attach", item["Id"]]

    def validate_held_container(self, item, argv):
        """Validate the closed P0/G1/G2 factory result, not arbitrary Docker argv."""
        config, host = item["Config"], item["HostConfig"]
        entry = argv.index("--entrypoint")
        image_index = entry + 2
        if (config.get("Entrypoint") != ["python3"] or config.get("Cmd") != argv[image_index + 1:]
                or config.get("WorkingDir") != "/validation"
                or host.get("NetworkMode") != "none" or host.get("Privileged") is not False
                or host.get("RestartPolicy", {}).get("Name") not in ("", "no")
                or host.get("AutoRemove") is not True or host.get("ShmSize") != 1073741824):
            raise RuntimeError("held candidate command/resource configuration differs")
        devices = host.get("DeviceRequests")
        if not (isinstance(devices, list) and len(devices) == 1 and devices[0].get("Count") == -1
                and devices[0].get("Capabilities") == [["gpu"]] and not host.get("Devices")):
            raise RuntimeError("held candidate GPU request differs")
        environment = config.get("Env", [])
        expected_mounts = []
        for index, token in enumerate(argv[:entry]):
            if token == "--env":
                value = argv[index + 1]
                key = value.split("=", 1)[0] + "="
                if [v for v in environment if v.startswith(key)] != [value]:
                    raise RuntimeError("held candidate environment differs")
            elif token == "--mount":
                parts = argv[index + 1].split(",")
                fields = dict(part.split("=", 1) for part in parts if "=" in part)
                expected_mounts.append((fields["src"], fields["dst"], "readonly" not in parts))
        actual = sorted((m.get("Source"), m.get("Destination"), m.get("RW"))
                        for m in item.get("Mounts", []) if m.get("Type") == "bind")
        if actual != sorted(expected_mounts) or len(actual) != len(item.get("Mounts", [])):
            raise RuntimeError("held candidate readonly/writable mount plan differs")

    def cleanup_held_candidates(self, session, attempt_id=None):
        """Positive exact-ID removal proof. Unknown daemon outcomes never pause."""
        rows = [dict(row) for row in session.state.get("held_candidates", [])]
        for row in rows:
            if attempt_id is not None and row["attempt_id"] != attempt_id:
                continue
            expected_id = row.get("container_id")
            if not re.fullmatch(r"[0-9a-f]{64}", expected_id or ""):
                raise RuntimeError("held candidate create has no durable exact ID; outcome uncertain")
            def exact_absence(result):
                missing = (r'(?i:(?:error: |error response from daemon: )?no such (?:object|container): )'
                           + re.escape(expected_id))
                return (result.returncode == 1 and result.stdout.strip() in ("", "[]")
                        and re.fullmatch(missing, result.stderr.strip()) is not None)
            def inspect():
                result = self.run(["docker", "container", "inspect", expected_id],
                                  timeout=30, check=False)
                if result.returncode:
                    if exact_absence(result):
                        return None
                    raise RuntimeError("held exact-ID daemon state is unknown")
                observed = json.loads(result.stdout)
                if not isinstance(observed, list) or len(observed) != 1:
                    raise RuntimeError("held exact-ID inspection is ambiguous")
                item = observed[0]
                labels = item.get("Config", {}).get("Labels", {})
                digest = held_container_sha256(item)
                if (item.get("Id") != expected_id or item.get("Image") != row["image_id"]
                        or labels.get("q03.window") != session.capsule["window_id"]
                        or labels.get("q03.attempt") != row["attempt_id"]
                        or labels.get("q03.job") != row["kind"]
                        or labels.get("q03.plan") != row["plan_sha256"]
                        or digest != row.get("config_sha256")):
                    raise RuntimeError("held candidate identity/configuration changed")
                return item
            if inspect() is not None:
                removed = self.run(["docker", "rm", "--force", expected_id], timeout=90, check=False)
                # --rm may finish between inspection and explicit removal. Only
                # the exact-ID no-such response gets a fresh absence check below;
                # permission/daemon/timeout/other failures remain UNKNOWN.
                if removed.returncode and not exact_absence(removed):
                    raise RuntimeError("held exact-ID removal outcome is unknown")
            if inspect() is not None:
                raise RuntimeError("held exact-ID candidate still exists after cleanup")
            row["removed"] = True
            session.update(held_candidates=rows)

    def cleanup_candidates(self, session):
        for name in session.state.get("candidate_containers", []):
            result = subprocess.run(["docker", "container", "inspect", name],
                                    text=True, capture_output=True, timeout=30, env=command_environment())
            if result.returncode:
                # Absence must be distinguished from daemon/permission errors.
                missing = (r'(?i:(?:error: |error response from daemon: )?no such (?:object|container): )'
                           + re.escape(name))
                if (result.returncode == 1 and result.stdout.strip() in ("", "[]")
                        and re.fullmatch(missing, result.stderr.strip())):
                    if name not in session.state.get("candidate_container_ids", {}):
                        # A create RPC may finish late, but no start was ever
                        # authorized without its durable ID. Report an inert
                        # leftover possibility rather than asserting cleanup.
                        pending = session.state.get("possible_inert_candidate_leftovers", [])
                        session.update(possible_inert_candidate_leftovers=sorted(set(pending + [name])))
                    continue
                raise RuntimeError("cannot establish candidate container absence")
            item = json.loads(result.stdout)[0]
            expected_id = session.state.get("candidate_container_ids", {}).get(name)
            if (item["Config"].get("Labels", {}).get("q03.window") != session.capsule["window_id"] or
                (expected_id is not None and item["Id"] != expected_id)):
                raise RuntimeError("candidate name belongs to another owner")
            self.run(["docker", "rm", "--force", item["Id"]], timeout=90)


class Session:
    def __init__(self, directory, ops):
        self.directory, self.ops = Path(directory), ops
        raw_capsule = (self.directory / "capsule.json").read_bytes()
        expected = (self.directory / "capsule.sha256").read_text().strip()
        if hashlib.sha256(raw_capsule).hexdigest() != expected:
            raise RuntimeError("immutable recovery capsule changed")
        self.capsule = json.loads(raw_capsule)
        if self.directory.resolve() != Path(self.capsule["session_dir"]).resolve():
            raise RuntimeError("capsule session directory changed")
        if self.capsule.get("recovery_source_sha256") != ops.recovery_sources():
            raise RuntimeError("pinned recovery dependencies changed")
        self.state = ops.read_json(self.directory / "state.json")
        self.manifest = self.capsule["manifest"]
        self.hold = Path(self.manifest["preflight"]["hold_path"])

    def check(self, phase):
        observation = self.ops.check(phase, self.manifest)
        serializable = json.loads(json.dumps(observation, default=str))
        atomic(self.directory / ("preflight-" + phase + "-" + uuid.uuid4().hex + ".json"), serializable)
        return observation

    def update(self, **fields):
        self.state.update(fields, updated_at=utc())
        atomic(self.directory / "state.json", self.state)

    def hold_status(self):
        if self.hold.is_symlink():
            raise RuntimeError("shared hold is an unsafe symlink")
        try:
            value = self.ops.read_json(self.hold)
        except FileNotFoundError:
            return "absent"
        if (not isinstance(value, dict) or not isinstance(value.get("window"), str)
                or not value["window"] or not isinstance(value.get("holder"), str)
                or not value["holder"]):
            raise RuntimeError("shared hold ownership is unknown")
        if (value["window"], value["holder"]) != (self.capsule["window_id"], self.capsule["holder"]):
            return "foreign"
        from q03_window_watchdog import read_owned_hold
        read_owned_hold(self.hold, self.capsule["window_id"], self.capsule["holder"])
        return "owned"

    def owns_hold(self):
        return self.hold_status() == "owned"

    def beat(self):
        from q03_window_watchdog import refresh_hold
        refresh_hold(self.hold, self.capsule["window_id"], self.capsule["holder"])

    def release(self):
        from q03_window_watchdog import release_hold
        release_hold(self.hold, self.capsule["window_id"], self.capsule["holder"])

    def requested(self):
        preflight = self.manifest["preflight"]
        return any(Path(preflight[key]).exists() for key in
                   ("request_path", "legacy_request_path"))


def original_command(action, session=None, epoch=None):
    if session is None:
        return ["registered-policy-adapter", action, "plan-only"]
    return [sys.executable, "-I", "-B",
            str(Path(__file__).with_name("q03_original_spark_adapter.py")),
            "--policy", session.manifest["preflight"]["policy_path"],
            "--policy-sha256", session.manifest["preflight"]["policy_sha256"],
            "--spark-sha256", session.manifest["preflight"]["spark_sha256"],
            "--evidence-dir", str(session.directory / "restore-evidence"),
            "--hold", str(session.hold), "--window-id", session.capsule["window_id"],
            "--epoch", epoch, "--action", action, "--apply"]


def transition_original(session, action, timeout):
    epoch = uuid.uuid4().hex
    transitions = session.state.get("transition_epochs", []) + [{"epoch": epoch, "action": action}]
    session.update(transition_epochs=transitions)  # Durable BEFORE any launch.
    session.ops.execute(session, original_command(action, session, epoch),
                        cwd=str(session.directory), timeout=timeout)
    session.ops.quiesce_original(session)


def restore(session):
    if not session.owns_hold():
        raise RuntimeError("recovery refuses foreign or missing hold")
    session.update(phase="restoring", restoring_started_epoch=session.state.get("restoring_started_epoch", time.time()))
    session.ops.terminate_owned_process(session.state.get("active_process"))
    if "held_window" in session.manifest:
        from q03_held_window import verify_outer
        verify_outer(session)
        session.ops.cleanup_held_candidates(session)
    if build_only(session.manifest):
        session.ops.cleanup_build(session)
    session.ops.quiesce_original(session)
    session.ops.cleanup_candidates(session)
    session.check("before_restore")
    session.update(phase="restoring", active_process=None)
    # Clean a partial original launch through its coordinated policy command.
    transition_original(session, "stop", 210)
    session.check("restore_drained")
    if "held_window" in session.manifest:
        interrupted = session.state.get("active_attempt")
        fields = {"active_attempt": None, "active_gate": None}
        if interrupted is not None:
            fields["interrupted_attempt"] = interrupted
        # Historical work is no longer active only after positive all-node drain.
        # A failed/unknown drain never clears its ownership or attempt evidence.
        session.update(**fields)
    transition_original(session, "start", 3650)
    session.check("restored")
    session.update(phase="restored", restored=True)
    session.release()
    session.update(hold_released=True)


def recover(directory, ops=None):
    ops = RuntimeOps() if ops is None else ops
    # Reading and verifying the capsule precedes creation/opening of any lock.
    session = Session(directory, ops)
    root = Path(session.manifest["independent_root"]).resolve()
    if root not in Path(directory).resolve().parents:
        raise RuntimeError("recovery directory escaped independent root")
    with open(Path(directory) / "session.lock", "a") as lock, contextlib.ExitStack() as locks:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        session = Session(directory, ops)
        locks.enter_context(ops.acquire_gpu_lock())
        if session.state.get("restored"):
            if session.owns_hold():
                session.check("restored")
                session.release()
            session.update(hold_released=True)
            return session.state
        if not session.state.get("mutation_started"):
            if session.owns_hold():
                session.release()
            session.update(phase="aborted-before-mutation", hold_released=True)
            return session.state
        try:
            restore(session)
        except BaseException as exc:
            session.update(phase="recovery-failed", failure=str(exc), outcome="failed")
            raise
        return session.state


def job_command(manifest, kind, window_id, *, attempt_id=None):
    if kind == "capture":
        if not capture_only(manifest):
            raise ValueError("native capture must be a separate capture-only window")
        from q03_window_build import native_capture_command
        return native_capture_command(manifest["candidate"]["image"], window_id)
    candidate = manifest["candidate"]
    if kind not in ("p0", "g1", "g2"):
        raise ValueError("only reviewed p0/g1/g2 job kinds are accepted")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", candidate["image"]):
        raise ValueError("candidate image must be an immutable OCI image ID")
    name = "q03-window-" + window_id[:12] + "-" + kind
    if attempt_id is not None:
        if not re.fullmatch(r"[0-9a-f]{32}", attempt_id):
            raise ValueError("invalid held attempt identity")
        name = "q03-held-" + window_id + "-" + attempt_id + "-" + kind
    selected_tests = g1_tests_path(candidate) if kind == "g1" else candidate["tests_path"]
    test_source = selected_tests + ("/tests" if kind != "p0" else "")
    test_destination = "/validation/tests" if kind != "p0" else "/validation"
    argv = ["docker", "run", "--pull", "never", "--rm", "--name", name, "--label", "q03.window=" + window_id,
            "--gpus", "all", "--network", "none", "--shm-size", "1g",
            "--mount", "type=bind,src=" + test_source + ",dst=" + test_destination + ",readonly",
            "--mount", "type=bind,src=" + candidate["cache_path"] + ",dst=/cache",
            "--mount", "type=bind,src=" + candidate["output_path"] + ",dst=/results",
            "--mount", "type=bind,src=" + candidate["runtime_manifest_path"] + ",dst=/runtime/runtime-manifest.json,readonly",
            "--workdir", "/validation", "--env", "HOME=/cache",
            "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--env", "XDG_CACHE_HOME=/cache", "--env", "TRITON_CACHE_DIR=/cache/triton",
            "--env", "VLLM_DS41_Q03_MHC_MXFP8=1"]
    if attempt_id is not None:
        argv += ["--label", "q03.attempt=" + attempt_id, "--label", "q03.job=" + kind]
    if kind == "p0":
        base_path = candidate["git_toolchain_base_path"]
        if (not isinstance(base_path, str) or not base_path
                or any(not item.startswith("/") or ".." in Path(item).parts
                       for item in base_path.split(":"))
                or any(c in base_path for c in ("\n", "\x00"))):
            raise ValueError("Explicit image PATH binding is required")
        digest = candidate["git_toolchain_manifest_sha256"]
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Pinned Git toolchain manifest required")
        config_digest = candidate["git_config_sha256"]
        if not re.fullmatch(r"[0-9a-f]{64}", config_digest):
            raise ValueError("Pinned source Git configuration required")
        argv += ["--mount", "type=bind,src=" + candidate["git_toolchain_path"]
                 + ",dst=/git-toolchain,readonly",
                 "--env", "PATH=/git-toolchain/bin:" + base_path,
                 "--env", "Q03_GIT_TOOLCHAIN_MANIFEST_SHA256=" + digest,
                 "--env", "Q03_GIT_CONFIG_SHA256=" + config_digest]
    if kind in ("g1", "g2"):
        if not re.fullmatch(r"[0-9a-f]{64}", candidate["tblib_manifest_sha256"]):
            raise ValueError("pinned tblib manifest required")
        pythonpath = candidate["pytest_base_pythonpath"]
        if not isinstance(pythonpath, str) or not pythonpath or any(c in pythonpath for c in ("\n", "\x00")):
            raise ValueError("explicit image PYTHONPATH binding required")
        argv += ["--env", "Q03_EXPECTED_PYTHONPATH=" + pythonpath,
                 "--env", f"VLLM_Q03_RUN_{kind.upper()}_GPU=1",
                 "--mount", "type=bind,src=" + candidate["tblib_path"]
                 + ",dst=/pytest-dependency,readonly"]
    argv += ["--entrypoint", "python3", candidate["image"]]
    if kind == "p0":
        argv += ["tests/standalone_tests/deepseek_v41_q03_registration.py", "--run",
                 "--allow-platform-initialization", "--expected-platform", "cuda",
                 "--expected-head", candidate["component_head"],
                 "--runtime-manifest", "/runtime/runtime-manifest.json",
                 "--output-dir", "/results/p0"]
    else:
        path = ("test_deepseek_v41_mhc_three_way.py" if kind == "g1" else
                "test_deepseek_v41_q03_owner.py")
        argv += ["-c", TBLIB_BOOTSTRAP, "-p", "no:cacheprovider", "-q", "tests/kernels/" + path,
                 "--junitxml=/results/" + kind + ".xml"]
    return name, argv


def capture_only(manifest):
    jobs = manifest.get("jobs")
    return (isinstance(jobs, list) and len(jobs) == 1 and
            isinstance(jobs[0], dict) and jobs[0].get("kind") == "capture")


def build_only(manifest):
    jobs = manifest.get("jobs")
    return (isinstance(jobs, list) and len(jobs) == 1 and
            isinstance(jobs[0], dict) and jobs[0].get("kind") == "build")


def validate_layout(manifest, directory):
    """Reject unsafe paths before creating even the session evidence directory."""
    if "held_window" in manifest:
        from q03_held_window import validate_held_policy
        validate_held_policy(manifest)
        if "g1_tests" in manifest.get("candidate", {}) and "g1_tests_path" not in globals():
            raise ValueError("reviewed G1 identity adapter is not integrated")
    root = Path(manifest["independent_root"])
    spec = manifest["preflight"]
    roots = spec.get("protected_roots")
    if not isinstance(roots, list) or not roots:
        raise ValueError("private site evidence must supply protected roots")
    guard_keys = ("hold_path", "request_path", "legacy_hold_path", "legacy_request_path")
    values = roots + [spec[key] for key in guard_keys]
    if any(not isinstance(p, str) or not Path(p).is_absolute() or
           ".." in Path(p).parts or any(c in p for c in ("\n", "\x00")) for p in values):
        raise ValueError("protected paths must be canonical absolute site paths")
    protected = [item for p in values for item in (Path(p), Path(p).resolve())]
    for key in ("policy_sha256", "spark_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", spec.get(key, "")):
            raise ValueError("private site evidence must pin " + key)

    def overlap(a, b):
        return a == b or a in b.parents or b in a.parents

    def safe(value):
        value = Path(value)
        if (not value.is_absolute() or value == Path("/") or ".." in value.parts
                or any(c in str(value) for c in (",", "\n", "\x00"))):
            raise ValueError("invalid independent path")
        resolved = value.resolve()
        if any(overlap(p, item) for p in (value, resolved) for item in protected):
            raise ValueError("independent path overlaps original protected namespace")
        return resolved

    resolved_root = safe(root)
    session_path = safe(directory)
    if session_path == resolved_root or resolved_root not in session_path.parents:
        raise ValueError("session evidence must be inside independent root")
    if build_only(manifest):
        job = manifest["jobs"][0]
        if type(job.get("timeout_seconds")) is not int or not 1 <= job["timeout_seconds"] <= 86400:
            raise ValueError("bounded build timeout required")
        return  # All build-specific paths are checked by prepare_build before writes.
    if capture_only(manifest):
        candidate = manifest["candidate"]
        paths = {key: safe(candidate[key]) for key in
                 ("output_path", "build_receipt_path", "build_inputs_path")}
        for value in paths.values():
            if value == resolved_root or resolved_root not in value.parents:
                raise ValueError("capture paths must be inside independent root")
        for writable in (paths["output_path"], session_path):
            if any(overlap(writable, paths[key]) for key in
                   ("build_receipt_path", "build_inputs_path")):
                raise ValueError("capture output overlaps immutable build evidence")
            checkout = safe(spec["independent_checkout"])
            immutable = [checkout / name for name in
                         (".git", "bin", "scripts", "config", "experiments", "docker")]
            prepared = paths["build_inputs_path"].parent
            immutable += [prepared / "src", prepared / "context"]
            if any(overlap(writable, item) for target in immutable
                   for item in (target, target.resolve())):
                raise ValueError("capture output overlaps immutable controller or prepared source")
        if overlap(paths["output_path"], session_path):
            raise ValueError("capture output must be separate from session evidence")
        if (paths["output_path"] / "runtime-manifest.json").exists():
            raise ValueError("capture output evidence already exists")
        if not re.fullmatch(r"[0-9a-f]{40}", candidate["component_head"]):
            raise ValueError("fixed component head required")
        timeout = manifest["jobs"][0].get("timeout_seconds")
        if type(timeout) is not int or not 1 <= timeout <= 120:
            raise ValueError("bounded capture timeout required (1..120)")
        job_command(manifest, "capture", "0" * 32)
        return
    candidate = manifest["candidate"]
    paths = {key: safe(candidate[key]) for key in
             ("tests_path", "cache_path", "output_path", "runtime_manifest_path",
              "build_receipt_path", "build_inputs_path", "git_toolchain_path")}
    if needs_tblib(manifest):
        paths["tblib_path"] = safe(candidate["tblib_path"])
    if "g1_tests" in candidate:
        paths["g1_tests_path"] = safe(candidate["g1_tests"]["path"])
        paths["g1_artifact_path"] = safe(candidate["g1_tests"]["artifact"]["checkout"])
    session_path = safe(directory)
    for value in (*paths.values(), session_path):
        if value == resolved_root or resolved_root not in value.parents:
            raise ValueError("all test and session paths must be inside independent root")
    for writable in (paths["cache_path"], paths["output_path"], session_path):
        if any(overlap(writable, immutable) for immutable in
               (paths["tests_path"], paths["runtime_manifest_path"],
                paths["build_receipt_path"], paths["build_inputs_path"],
                paths["git_toolchain_path"],
                *([paths["tblib_path"]] if "tblib_path" in paths else []),
                *([paths["g1_tests_path"], paths["g1_artifact_path"]] if "g1_tests_path" in paths else []))):
            raise ValueError("writable path overlaps immutable test/runtime evidence")
    if overlap(session_path, paths["cache_path"]) or overlap(session_path, paths["output_path"]):
        raise ValueError("session evidence must be separate from container writable mounts")
    for kind in ("p0", "g1", "g2"):
        if (paths["output_path"] / (kind + ".xml")).exists() or (paths["output_path"] / kind).exists():
            raise ValueError("test output evidence already exists")
    if not re.fullmatch(r"[0-9a-f]{40}", candidate["component_head"]):
        raise ValueError("fixed component head required")
    jobs = manifest.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise ValueError("nonempty reviewed jobs required")
    kinds = [job.get("kind") for job in jobs if isinstance(job, dict)]
    if len(kinds) != len(jobs) or any(k not in ("p0", "g1", "g2") for k in kinds):
        raise ValueError("unknown job kind")
    if kinds not in (["p0"], ["p0", "g1"], ["p0", "g1", "g2"]):
        raise ValueError("jobs must execute sequential P0/G1/G2 gates without omissions")
    for job in jobs:
        if type(job.get("timeout_seconds")) is not int or not 1 <= job["timeout_seconds"] <= 7200:
            raise ValueError("bounded job timeout required")
        job_command(manifest, job["kind"], "0"*32)


def run_session(manifest, directory, ops=None):
    ops = RuntimeOps() if ops is None else ops
    directory = Path(directory)
    validate_layout(manifest, directory)
    ops.validate_host(manifest)
    build_plan = ops.prepare_build(manifest) if build_only(manifest) else None
    if build_plan and (build_plan.get("requires_supervised_quiescence") or
                       build_plan.get("uses_supervised_adapter") is not True):
        raise RuntimeError("build execution blocked until its supervised quiescence adapter is reviewed")
    seconds = manifest.get("window_seconds")
    if type(seconds) is not int or not 1 <= seconds <= 86400:
        raise ValueError("explicit bounded window_seconds required (1..86400)")
    # Never resume/overwrite an old evidence directory implicitly.
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    window_id = uuid.uuid4().hex
    manifest = json.loads(json.dumps(manifest))
    manifest["preflight"].update(window_id=window_id, holder="q03-isolated:" + window_id)
    capsule = dict(window_id=window_id, holder=manifest["preflight"]["holder"],
                   manifest=manifest, session_dir=str(directory), core_process=ops.process_identity(),
                   deadline_epoch=time.time()+seconds, policy_sha256=manifest["preflight"]["policy_sha256"],
                   hold_path=manifest["preflight"]["hold_path"],
                   policy_path=manifest["preflight"]["policy_path"],
                   heartbeat_timeout_s=90, recovery_timeout_s=7200, build_plan=build_plan)
    initial = ops.check("before_hold", manifest)
    atomic(directory / "preflight-before_hold.json", json.loads(json.dumps(initial, default=str)))
    manifest["preflight"]["glm_identity"] = initial["glm_identity"]
    capsule["recovery_command"] = [sys.executable, str(Path(__file__).resolve()),
                                   "recover", "--session-dir", str(directory)]
    capsule["recovery_script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    capsule["recovery_source_sha256"] = ops.recovery_sources()
    atomic(directory / "capsule.json", capsule)
    with (directory / "capsule.sha256").open("x") as stream:
        stream.write(hashlib.sha256((directory / "capsule.json").read_bytes()).hexdigest() + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    parent_fd = os.open(directory, os.O_DIRECTORY)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
    atomic(directory / "state.json", dict(phase="preflight", mutation_started=False,
                                         restored=False, active_process=None, candidate_containers=[],
                                         outcome="incomplete", job_results=[]))
    session = Session(directory, ops)
    with open(directory / "session.lock", "a") as lock, contextlib.ExitStack() as locks:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if session.requested():
            raise RuntimeError("another operator requests the cluster")
        from q03_window_watchdog import claim_hold
        claim_hold(session.hold, dict(holder=capsule["holder"], window=window_id,
                   since=utc(), heartbeat=utc(),
                   expected_end=datetime.datetime.fromtimestamp(capsule["deadline_epoch"], datetime.timezone.utc).isoformat(),
                   rule="Exclusive Q03; restore registered policy before release",
                   session_dir=str(directory)))
        heartbeat_stop = threading.Event()
        def keepalive():
            while not heartbeat_stop.wait(5):
                try:
                    session.beat()
                except Exception:
                    return  # Watchdog will see stale ownership, never steal a foreign hold.
        heartbeat_thread = threading.Thread(target=keepalive, daemon=True)
        heartbeat_thread.start()
        try:
            watchdog = ops.spawn_watchdog(directory)
            ops.wait_watchdog_ready(directory, watchdog)
            locks.enter_context(ops.acquire_gpu_lock())
            session.check("before_stop")
            if time.time() >= capsule["deadline_epoch"]:
                raise TimeoutError("deadline reached before original stop")
            session.update(phase="stopping", mutation_started=True)
            transition_original(session, "stop", 210)
            session.check("stopped")
            if "held_window" in manifest:
                from q03_held_window import held_loop
                held_loop(session, sys.modules[__name__])
            else:
                for job in manifest["jobs"]:
                    if session.requested():
                        session.update(outcome="cancelled")
                        raise RuntimeError("cluster requested; unfinished jobs cancelled")
                    if time.time() >= capsule["deadline_epoch"]:
                        raise TimeoutError("bounded session deadline reached")
                    timeout = job["timeout_seconds"]
                    containers = session.state["candidate_containers"]
                    if job["kind"] == "build":
                        argv = [sys.executable, "-I", "-B", str(Path(__file__).with_name("q03_build_adapter.py")),
                                "run", "--session-dir", str(directory)]
                        cwd = str(directory)
                    else:
                        name, argv = job_command(manifest, job["kind"], window_id)
                        containers = containers + [name]
                        cwd = str(directory)
                    session.update(phase="job-" + job["kind"], candidate_containers=containers)
                    if job["kind"] != "build":
                        argv = ops.prepare_candidate(session, name, argv)
                    execution = ops.execute(session, argv, cwd=cwd, timeout=timeout)
                    result = (ops.validate_capture_result(manifest, execution)
                              if job["kind"] == "capture" else ops.validate_result(manifest, job["kind"]))
                    if result is not None:
                        atomic(directory / (job["kind"] + "-result.json"), result)
                    session.update(job_results=session.state["job_results"] + [job["kind"]])
                    session.check("stopped")
                session.update(outcome="completed")
        except BaseException as exc:
            session.update(failure=str(exc), outcome=("cancelled" if session.state.get("outcome") == "cancelled" else "failed"))
            raise
        finally:
            try:
                if session.state["mutation_started"]:
                    try:
                        restore(session)
                    except BaseException as exc:
                        session.update(phase="recovery-failed", failure=str(exc), outcome="failed")
                        raise
                elif session.owns_hold():
                    session.update(phase="aborted-before-mutation")
                    session.release()
                    session.update(hold_released=True)
            finally:
                heartbeat_stop.set()
                heartbeat_thread.join(timeout=10)
    return session.state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run-session")
    run.add_argument("manifest")
    run.add_argument("--session-dir", required=True)
    run.add_argument("--apply", action="store_true")
    recovery = sub.add_parser("recover")
    recovery.add_argument("--session-dir", required=True)
    plan = sub.add_parser("plan-attempt", help="write a typed immutable plan without running tests/services")
    plan.add_argument("--session-dir", required=True)
    plan.add_argument("--through", choices=("p0", "g1", "g2"))
    plan.add_argument("--g1-tests")
    plan.add_argument("--g1-tests-sha256")
    control = sub.add_parser("control", help="request a held action; never directly change services")
    control.add_argument("action", choices=("status", "pause", "resume", "close"))
    control.add_argument("--session-dir", required=True)
    control.add_argument("--expected-sequence", type=int)
    control.add_argument("--attempt")
    control.add_argument("--attempt-sha256")
    control.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    def interrupted(signum, frame):
        raise SessionInterrupted(f"session interrupted by signal {signum}")
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    if args.command == "plan-attempt":
        from q03_held_window import plan_attempt
        session = Session(args.session_dir, RuntimeOps())
        result = plan_attempt(session, sys.modules[__name__], through=args.through,
                              g1_tests_path=args.g1_tests, g1_tests_sha256=args.g1_tests_sha256)
    elif args.command == "control":
        from q03_held_window import read_status, submit_control
        session = Session(args.session_dir, RuntimeOps())
        if args.action == "status":
            result = read_status(session)
        elif not args.apply:
            result = {"status": "plan-only", "action": args.action, "no_operations": True}
        else:
            result = submit_control(session, args.action, args.expected_sequence,
                                    attempt_path=args.attempt, attempt_sha256=args.attempt_sha256)
    elif args.command == "recover":
        result = recover(args.session_dir)
    else:
        manifest = json.loads(Path(args.manifest).read_text())
        if not args.apply:
            print(json.dumps({"status": "plan-only", "jobs": manifest.get("jobs"),
                              "restore": original_command("start"), "no_operations": True,
                              "held_window": "held_window" in manifest,
                              "explicit_resume_required": "held_window" in manifest}))
            return
        result = run_session(manifest, args.session_dir)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
