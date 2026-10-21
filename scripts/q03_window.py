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

    def run(self, argv, *, cwd=None, timeout=60):
        return subprocess.run(argv, cwd=cwd, timeout=timeout, text=True,
                              capture_output=True, check=True,
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
                 "q03_window_build.py", "q03_buildkit_plan.py", "q03_git_toolchain.py")
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
        self.validate_candidate_image(manifest, selection)
        self.validate_git_toolchain(manifest)

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

    def execute(self, session, argv, *, cwd, timeout):
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
        report = receipt.check_evidence(
            kind.upper(), (experiment / expected["path"]).read_bytes(), expected["sha256"],
            selection["source_tree"], (output / (kind + ".xml")).read_bytes(), 0)
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

    def prepare_candidate(self, session, name, argv):
        # Create cannot run GPU work. Record its exact immutable container ID
        # before a separate start; removal then fences even delayed start RPCs.
        if argv[:2] != ["docker", "run"]:
            raise ValueError("unexpected typed container template")
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
    if build_only(session.manifest):
        session.ops.cleanup_build(session)
    session.ops.quiesce_original(session)
    session.ops.cleanup_candidates(session)
    session.check("before_restore")
    session.update(phase="restoring", active_process=None)
    # Clean a partial original launch through its coordinated policy command.
    transition_original(session, "stop", 210)
    session.check("restore_drained")
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


def job_command(manifest, kind, window_id):
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
    test_source = candidate["tests_path"] + ("/tests" if kind != "p0" else "")
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
        argv += ["--env", f"VLLM_Q03_RUN_{kind.upper()}_GPU=1"]
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
        argv += ["-m", "pytest", "-p", "no:cacheprovider", "-q", "tests/kernels/" + path,
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
    session_path = safe(directory)
    for value in (*paths.values(), session_path):
        if value == resolved_root or resolved_root not in value.parents:
            raise ValueError("all test and session paths must be inside independent root")
    for writable in (paths["cache_path"], paths["output_path"], session_path):
        if any(overlap(writable, immutable) for immutable in
               (paths["tests_path"], paths["runtime_manifest_path"],
                paths["build_receipt_path"], paths["build_inputs_path"],
                paths["git_toolchain_path"])):
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
    args = parser.parse_args()
    def interrupted(signum, frame):
        raise RuntimeError(f"session interrupted by signal {signum}")
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    if args.command == "recover":
        result = recover(args.session_dir)
    else:
        manifest = json.loads(Path(args.manifest).read_text())
        if not args.apply:
            print(json.dumps({"status": "plan-only", "jobs": manifest.get("jobs"),
                              "restore": original_command("start"), "no_operations": True}))
            return
        result = run_session(manifest, args.session_dir)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
