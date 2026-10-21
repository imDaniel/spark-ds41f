"""CPU fault injection, not site execution or proof of GPU/service readiness.

Run directly, or with unittest discovery. Q03_WINDOW_CORE_ROOT,
Q03_WINDOW_PREFLIGHT_ROOT and Q03_WINDOW_WATCHDOG_ROOT optionally select
read-only development worktrees. All command/process boundaries use fakes;
only temporary journal, capsule and hold files are written.
"""

from __future__ import annotations

import contextlib
import copy
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, role):
    root = Path(os.environ.get(f"Q03_WINDOW_{role}_ROOT", ROOT))
    path = root / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class PreflightFaultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guard = load_module("q03_window_preflight", "PREFLIGHT")

    def setUp(self):
        import datetime as dt
        self.now = dt.datetime(2026, 10, 21, tzinfo=dt.timezone.utc)
        self.expected = {"holder": "test-holder", "window_id": "test-window"}
        self.observed = {
            "phase": "before_hold", "publication_ok": True, "protected_ok": True,
            "node_count": 4, "requests_absent": True, "legacy_hold_absent": True,
            "hold": None, "now": self.now,
            "deepseek": {"failed": False, "running": True, "ready": True,
                         "gpu_ownership_verified": True,
                         "errors": {}, "nodes": {str(i): {"valid": True} for i in range(4)}},
            "glm": {"failed": False, "running": False,
                    "nodes": [{"running": False} for _ in range(4)]},
            "idle": True,
        }

    def owned(self):
        return {"holder": "test-holder", "window": "test-window",
                "heartbeat": self.now.isoformat(), "expected_end": None}

    def validate(self, observed):
        return self.guard.validate_preflight(self.expected, observed)

    def test_idle_requires_both_finite_nonnegative_zero_counters(self):
        prefix = 'vllm:num_requests_running{model_name="test"} '
        suffix = '\nvllm:num_requests_waiting 0\n'
        self.assertEqual(self.guard.parse_idle_metrics(prefix + "0" + suffix), 0)
        for value in ("1", "-1", "nan", "inf", "-inf", "invalid"):
            with self.subTest(value=value), self.assertRaises(self.guard.PreflightError):
                self.guard.parse_idle_metrics(prefix + value + suffix)
        for value in ("", prefix + "0", suffix, prefix + "0" + suffix + prefix + "1"):
            with self.subTest(value=value), self.assertRaises(self.guard.PreflightError):
                self.guard.parse_idle_metrics(value)

    def test_missing_unknown_or_failed_guard_blocks_before_hold(self):
        self.validate(self.observed)
        for key in ("publication_ok", "protected_ok", "requests_absent",
                    "legacy_hold_absent", "idle"):
            for value in (False, None, 1, "true"):
                observed = copy.deepcopy(self.observed)
                observed[key] = value
                with self.subTest(key=key, value=value):
                    with self.assertRaises(self.guard.PreflightError):
                        self.validate(observed)
        for count in (0, 3, 5):
            with self.assertRaises(self.guard.PreflightError):
                self.validate(dict(self.observed, node_count=count))

    def test_existing_hold_and_foreign_or_stale_owned_hold_fail(self):
        import datetime as dt
        with self.assertRaises(self.guard.PreflightError):
            self.validate(dict(self.observed, hold=self.owned()))
        observed = dict(self.observed, phase="before_stop", hold=self.owned())
        self.validate(observed)
        for key, value in (("holder", "other"), ("window", "other"),
                           ("heartbeat", "invalid"),
                           ("heartbeat", (self.now - dt.timedelta(seconds=901)).isoformat()),
                           ("heartbeat", (self.now + dt.timedelta(seconds=1)).isoformat()),
                           ("expected_end", self.now.isoformat())):
            changed = dict(observed, hold=dict(observed["hold"], **{key: value}))
            with self.subTest(key=key, value=value):
                with self.assertRaises(self.guard.PreflightError):
                    self.validate(changed)

    def test_glm_stays_stopped_and_deepseek_requires_complete_ready_state(self):
        for service, key, value in (("glm", "running", True), ("glm", "failed", True),
                                    ("glm", "nodes", [{"running": False}] * 3),
                                    ("glm", "nodes", [{"running": True}] * 4),
                                    ("deepseek", "ready", False),
                                    ("deepseek", "running", False),
                                    ("deepseek", "failed", None),
                                    ("deepseek", "nodes", {})):
            observed = copy.deepcopy(self.observed)
            observed[service][key] = value
            with self.subTest(service=service, key=key):
                with self.assertRaises(self.guard.PreflightError):
                    self.validate(observed)

    def test_partial_stop_can_restore_but_cannot_start_jobs(self):
        observed = copy.deepcopy(self.observed)
        observed.update(phase="before_restore", hold=self.owned())
        observed["deepseek"].update(failed=True, running=False, ready=False)
        self.validate(observed)
        observed["phase"] = "stopped"
        observed["gpus_empty"] = True
        with self.assertRaises(self.guard.PreflightError):
            self.validate(observed)
        observed["deepseek"]["errors"] = {}
        observed["deepseek"]["nodes"] = {str(i): {"running": False, "valid": True,
                                                    "memguard": "inactive"}
                                          for i in range(4)}
        self.validate(observed)
        observed["gpus_empty"] = False
        with self.assertRaises(self.guard.PreflightError):
            self.validate(observed)

    def test_recovery_allows_pending_request_and_expired_owned_window(self):
        import datetime as dt
        observed = copy.deepcopy(self.observed)
        observed.update(phase="before_restore", hold=self.owned(), requests_absent=False)
        observed["hold"]["heartbeat"] = (self.now - dt.timedelta(hours=1)).isoformat()
        observed["hold"]["expected_end"] = (self.now - dt.timedelta(seconds=1)).isoformat()
        self.validate(observed)
        observed["hold"]["holder"] = "foreign"
        with self.assertRaises(self.guard.PreflightError):
            self.validate(observed)

    def test_present_unknown_request_file_is_not_treated_as_absent(self):
        from types import SimpleNamespace
        for value in (None, [], "", 0):
            ops = SimpleNamespace(read_json=lambda path: value)
            with self.subTest(value=value), self.assertRaises(self.guard.PreflightError):
                self.guard._optional_json(ops, "fake-request")
        def absent(path):
            raise FileNotFoundError(path)
        self.assertIsNone(self.guard._optional_json(
            SimpleNamespace(read_json=absent), "fake-request"))

    def test_restored_requires_registered_pending_policy_not_old_live_options(self):
        observed = copy.deepcopy(self.observed)
        # The old live state need not have the pending registered option yet.
        observed["deepseek"]["prompt_tokens_details"] = False
        self.validate(observed)
        observed.update(phase="restored", hold=self.owned())
        with self.assertRaisesRegex(self.guard.PreflightError, "cache-details"):
            self.validate(observed)
        observed["deepseek"]["prompt_tokens_details"] = True
        self.validate(observed)
        for ownership in (False, None, 1):
            observed["deepseek"]["gpu_ownership_verified"] = ownership
            with self.assertRaisesRegex(self.guard.PreflightError, "ownership"):
                self.validate(observed)

    def test_before_restore_refuses_unknown_or_foreign_original_service(self):
        observed = copy.deepcopy(self.observed)
        observed.update(phase="before_restore", hold=self.owned())
        observed["deepseek"].update(failed=True, running=False, ready=False)
        self.validate(observed)
        for errors, valid in (({"node01": "unknown inspect"}, True), ({}, False)):
            changed = copy.deepcopy(observed)
            changed["deepseek"]["errors"] = errors
            changed["deepseek"]["nodes"]["0"]["valid"] = valid
            with self.subTest(errors=errors, valid=valid):
                with self.assertRaises(self.guard.PreflightError):
                    self.validate(changed)


class PublicationFaultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guard = load_module("q03_window_preflight", "PREFLIGHT")

    def setUp(self):
        from types import SimpleNamespace
        self.publication = {"url": "https://github.com/imDaniel/spark-ds41f.git",
                            "ref": "refs/heads/reviewed", "sha": "a" * 40}
        self.policy = {"repository": "/protected/original", "cluster_config": "profile.json",
                       "config_sha256": "b" * 64, "revision": "c" * 40,
                       "image_id": "sha256:" + "d" * 64, "container": "synthetic-service"}
        self.spec = {"publication": self.publication, "independent_checkout": "/private/test",
                     "policy_path": "/synthetic/policy.json",
                     "registered_policy": dict(self.policy),
                     "policy_sha256": "a" * 64}
        self.spec.update(hold_path="/synthetic/current-hold.json",
                         request_path="/synthetic/current-request.json",
                         legacy_hold_path="/synthetic/legacy-hold.json",
                         legacy_request_path="/synthetic/legacy-request.json")
        self.commands = []
        self.remote = self.publication["sha"] + "\t" + self.publication["ref"]
        self.failed_remote = False
        self.hash_drift = False
        def run(argv, **kwargs):
            self.commands.append(argv)
            if "get-url" in argv:
                output = self.publication["url"]
            elif "rev-parse" in argv:
                output = self.publication["sha"]
            elif "status" in argv:
                output = ""
            elif "ls-remote" in argv:
                return SimpleNamespace(returncode=1 if self.failed_remote else 0,
                                       stdout=self.remote)
            else:
                raise AssertionError("unexpected probe " + str(argv))
            return SimpleNamespace(returncode=0, stdout=output)
        self.ops = SimpleNamespace(
            run=run, resolve=lambda path: path, read_json=lambda path: self.policy,
            sha256=lambda path: "drift" if self.hash_drift else
            "a" * 64 if path == self.spec["policy_path"] else "b" * 64)

    def test_exact_ref_wrong_tip_and_unavailable_remote_refuse(self):
        for remote, failed in (("c" * 40 + "\t" + self.publication["ref"], False),
                               (self.publication["sha"] + "\trefs/heads/wrong", False),
                               ("", True)):
            self.remote, self.failed_remote = remote, failed
            with self.subTest(remote=remote, failed=failed):
                with self.assertRaises(self.guard.PreflightError):
                    self.guard.collect_preflight({"preflight": self.spec}, self.ops,
                                                 "before_hold")
        self.assertTrue(any("ls-remote" in argv for argv in self.commands))
        self.assertFalse(any("fetch" in argv for argv in self.commands))

    def test_protected_policy_hash_drift_refuses_before_remote_probe(self):
        self.hash_drift = True
        with self.assertRaisesRegex(self.guard.PreflightError, "Policy drift"):
            self.guard.collect_preflight({"preflight": self.spec}, self.ops, "before_hold")
        self.assertEqual(self.commands, [])

    def test_original_checkout_or_nested_alias_refused(self):
        for checkout in ("/protected/original", "/protected/original/test", "/protected"):
            self.spec["independent_checkout"] = checkout
            with self.subTest(checkout=checkout):
                with self.assertRaisesRegex(self.guard.PreflightError, "Independent"):
                    self.guard.collect_preflight({"preflight": self.spec}, self.ops,
                                                 "before_hold")
        self.assertEqual(self.commands, [])

    def test_restore_does_not_depend_on_remote_branch_availability(self):
        self.failed_remote = True
        self.spec["nodes"] = []
        # Stop at the next local guard; publication must not contact the remote.
        with self.assertRaisesRegex(self.guard.PreflightError, "four distinct"):
            self.guard.collect_preflight({"preflight": self.spec}, self.ops,
                                         "before_restore")
        self.assertFalse(any("ls-remote" in argv for argv in self.commands))


class FakeOps:
    """Inert boundary: commands are recorded, never executed."""
    def __init__(self, failure=None):
        self.events = []
        self.failure = failure
        self.hook = None
        self.stop_count = 0
        self.lock_depth = 0

    def read_json(self, path):
        import json
        return json.loads(Path(path).read_text())

    @contextlib.contextmanager
    def acquire_gpu_lock(self):
        self.lock_depth += 1
        try:
            yield
        finally:
            self.lock_depth -= 1

    def recovery_sources(self):
        return {name: "a" * 64 for name in ("q03_window.py", "q03_window_preflight.py",
                "q03_window_watchdog.py", "q03_original_spark_adapter.py",
                "q03_remote_operation.py", "q03_build_adapter.py",
                "q03_window_build.py", "q03_buildkit_plan.py", "q03_git_toolchain.py")}

    def validate_host(self, manifest):
        # Host authorization is replaced only at the explicit fake I/O boundary.
        return None

    def process_identity(self):
        return {"pid": 123456789, "start_ticks": "test-only"}

    def event(self, name):
        self.events.append(name)
        if self.hook:
            self.hook(name)
        if name == self.failure:
            raise RuntimeError("injected " + name)

    def check(self, phase, manifest):
        self.event("check:" + phase)
        return {"glm_identity": {"node01": {"exists": False}}}

    def prepare_build(self, manifest):
        self.event("prepare:build")
        return {"argv": ["inert-build"], "cwd": manifest["independent_root"],
                "uses_supervised_adapter": True}

    def prepare_candidate(self, session, name, argv):
        state = self.read_json(session.directory / "state.json")
        assert name in state["candidate_containers"], "name must precede create"
        assert argv[:2] == ["docker", "run"]
        return ["docker", "start", "--attach", "c" * 64]

    def validate_result(self, manifest, kind):
        self.event("result:" + kind)
        if kind == "build":
            return {"scope": "synthetic build receipt", "image": "sha256:" + "d" * 64}

    def spawn_watchdog(self, directory):
        self.event("watchdog:spawn")
        return object()

    def wait_watchdog_ready(self, directory, process):
        self.event("watchdog:ready")

    def execute(self, session, argv, *, cwd, timeout):
        assert self.lock_depth == 1, "commands require the shared GPU lock"
        self.commands.append(list(argv))
        # Stop intent must be durable before even the first mutating command.
        state = self.read_json(session.directory / "state.json")
        if "--action" in argv:
            action = argv[argv.index("--action") + 1]
            epoch = argv[argv.index("--epoch") + 1]
            transitions = state["transition_epochs"]
            assert transitions[-1] == {"epoch": epoch, "action": action}
            assert len({item["epoch"] for item in transitions}) == len(transitions)
            if action == "stop":
                self.stop_count += 1
                assert state["mutation_started"] is True
                self.event("stop:" + str(self.stop_count))
            else:
                self.event("restore:start")
        else:
            self.event("job:" + state["phase"].removeprefix("job-"))

    def cleanup_build(self, session):
        self.event("cleanup:build")

    def quiesce_original(self, session):
        self.event("quiesce")

    def cleanup_candidates(self, session):
        self.event("cleanup")

    def terminate_owned_process(self, record):
        self.event("terminate")


class WindowRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_module("q03_window_watchdog", "WATCHDOG")
        cls.core = load_module("q03_window", "CORE")

    def setUp(self):
        import subprocess
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "independent"
        self.root.mkdir()
        self.directory = self.root / "session"
        self.hold = self.root.parent / "hold.json"
        self.ops = FakeOps()
        self.ops.commands = []
        thread_patch = mock.patch.object(self.core.threading, "Thread")
        thread_patch.start()
        self.addCleanup(thread_patch.stop)
        self.manifest = {
            "window_seconds": 600, "independent_root": str(self.root),
            "preflight": {"hold_path": str(self.hold),
                          "policy_path": "/synthetic/protected/policy.json",
                          "protected_roots": ["/synthetic/protected"],
                          "spark_sha256": "a" * 64, "policy_sha256": "b" * 64,
                          "legacy_hold_path": str(self.root.parent / "legacy-hold.json"),
                          "request_path": str(self.root.parent / "request.json"),
                          "legacy_request_path": str(self.root.parent / "legacy.json")},
            "candidate": {"image": "sha256:" + "a" * 64,
                          "tests_path": str(self.root / "tests"),
                          "git_toolchain_path": str(self.root / "git-toolchain"),
                          "git_toolchain_manifest_sha256": "c" * 64,
                          "git_toolchain_base_path": "/usr/local/bin:/usr/bin:/bin",
                          "git_config_sha256": "d" * 64,
                          "tblib_path": str(self.root / "tblib"),
                          "tblib_manifest_sha256": "e" * 64,
                          "pytest_base_pythonpath": "/opt/native",
                          "cache_path": str(self.root / "cache"),
                          "output_path": str(self.root / "output"),
                          "runtime_manifest_path": str(self.root / "runtime.json"),
                          "runtime_manifest_sha256": "a" * 64,
                          "build_receipt_path": str(self.root / "build-receipt.json"),
                          "build_receipt_sha256": "a" * 64,
                          "build_inputs_path": str(self.root / "build-inputs.json"),
                          "build_inputs_sha256": "a" * 64,
                          "component_head": "b" * 40},
            "jobs": [{"kind": "p0", "timeout_seconds": 60},
                     {"kind": "g1", "timeout_seconds": 60}],
        }
        # Any accidental escape through real subprocess APIs fails immediately.
        for name in ("Popen", "run", "check_output", "check_call"):
            patch = mock.patch.object(subprocess, name,
                                      side_effect=AssertionError("real process forbidden"))
            patch.start()
            self.addCleanup(patch.stop)

    def run_window(self):
        return self.core.run_session(self.manifest, self.directory, self.ops)

    def state(self):
        return self.ops.read_json(self.directory / "state.json")

    def test_image_host_gate_failure_precedes_hold_and_session_creation(self):
        self.ops.validate_host = mock.Mock(side_effect=ValueError("candidate image identity drift"))
        with self.assertRaisesRegex(ValueError, "image identity"):
            self.run_window()
        self.ops.validate_host.assert_called_once_with(self.manifest)
        self.assertFalse(self.directory.exists())
        self.assertFalse(self.hold.exists())
        self.assertEqual(self.ops.events, [])
        self.assertEqual(self.ops.commands, [])

    def test_pidfd_shutdown_never_signals_recycled_numeric_group(self):
        record = {"pid": 424242, "start_ticks": "synthetic"}
        for ready in (True, False):
            with (self.subTest(ready=ready),
                  mock.patch.object(self.core, "owned_process", return_value=True),
                  mock.patch.object(self.core.os, "pidfd_open", return_value=88, create=True) as opened,
                  mock.patch.object(self.core.os, "getpgid", return_value=record["pid"]),
                  mock.patch.object(self.core.signal, "pidfd_send_signal", create=True) as signal,
                  mock.patch.object(self.core.select, "select", return_value=([88] if ready else [], [], [])),
                  mock.patch.object(self.core.os, "close") as close,
                  mock.patch.object(self.core.os, "killpg", side_effect=AssertionError("numeric signal forbidden")),
                  mock.patch.object(Path, "glob", return_value=[])):
                if ready:
                    self.core.RuntimeOps().terminate_owned_process(record)
                else:
                    with self.assertRaisesRegex(RuntimeError, "bounded group shutdown"):
                        self.core.RuntimeOps().terminate_owned_process(record)
                opened.assert_called_once_with(record["pid"], 0)
                signal.assert_called_once_with(88, self.core.signal.SIGTERM)
                close.assert_called_once_with(88)

    def test_daemon_unknown_cleanup_refuses_and_known_absence_records_uncertainty(self):
        from types import SimpleNamespace
        state = {"candidate_containers": ["synthetic-candidate"]}
        session = SimpleNamespace(state=state, capsule={"window_id": "synthetic-window"},
                                  update=lambda **values: state.update(values))
        facade = SimpleNamespace(run=mock.Mock(side_effect=AssertionError("unexpected removal")))
        for error in ("permission denied", "cannot connect to daemon"):
            with (self.subTest(error=error), mock.patch.object(self.core.subprocess, "run",
                  return_value=SimpleNamespace(returncode=1, stderr=error, stdout=""))):
                with self.assertRaisesRegex(RuntimeError, "cannot establish"):
                    self.core.RuntimeOps.cleanup_candidates(facade, session)
                self.assertNotIn("possible_inert_candidate_leftovers", state)
        with mock.patch.object(self.core.subprocess, "run", return_value=SimpleNamespace(
                returncode=1, stderr="No such container: synthetic-candidate", stdout="")):
            self.core.RuntimeOps.cleanup_candidates(facade, session)
        self.assertEqual(state["possible_inert_candidate_leftovers"], ["synthetic-candidate"])
        facade.run.assert_not_called()

    def test_actual_candidate_image_identity_and_receipt_drift(self):
        import json
        from types import SimpleNamespace
        build = load_module("q03_window_build", "PREFLIGHT")
        head, tree, deployment = build.SOURCE_HEAD, build.SOURCE_TREE, "c" * 40
        digest, base = "sha256:" + "d" * 64, "sha256:" + "e" * 64
        labels = {"org.opencontainers.image.revision": deployment,
                  "org.opencontainers.image.base.digest": base,
                  "local.spark3.vllm.patch-head": head, "local.spark3.vllm.tree": tree}
        image = {"Id": digest, "Config": {"Labels": labels}}
        official = {"tag": "q03-validation:example01", "image_id": digest,
                    "deployment_commit": deployment, "compile_jobs": 1,
                    "min_available_gib": 24, "built_utc": "2026-01-01T00:00:00Z"}
        receipt = {"result": "built-official-smoke-passed", "image_id": digest,
                   "source_head": head, "source_tree": tree, "image_labels": labels,
                   "official_receipt": official}
        records = {"runtime": {"source_head": head}, "receipt": receipt,
                   "inputs": {"inputs": {"base_image": "example@" + base,
                              "vllm": {"patch_head": head, "expected_tree": tree}}}}
        candidate = {"image": digest, "component_head": head, "runtime_manifest_path": "runtime",
                     "runtime_manifest_sha256": "f" * 64, "build_receipt_path": "receipt",
                     "build_receipt_sha256": "f" * 64, "build_inputs_path": "inputs",
                     "build_inputs_sha256": "f" * 64}
        publication = {"sha": deployment, "url": "https://github.com/imDaniel/spark-ds41f.git",
                       "ref": "refs/heads/reviewed-build"}
        manifest = {"candidate": candidate, "preflight": {"publication": publication}}
        selection = {"source_head": head, "source_tree": tree}
        commands = []
        def run(argv, **kwargs):
            commands.append(argv)
            if argv[:3] == ["git", "--no-optional-locks", "ls-remote"]:
                return SimpleNamespace(returncode=0, stdout=deployment + "\t" + publication["ref"], stderr="")
            self.assertEqual(argv[:3], ["docker", "image", "inspect"])
            return SimpleNamespace(returncode=0, stdout=json.dumps([image]), stderr="")
        facade = SimpleNamespace(read_json=lambda path: records[path],
                                 sha256=lambda path: "f" * 64, run=run)
        validate = lambda: self.core.RuntimeOps.validate_candidate_image(facade, manifest, selection)
        validate()
        self.assertTrue(commands)
        for target, key, invalid in ((candidate, "runtime_manifest_sha256", "0" * 64),
                                     (image, "Id", "sha256:" + "0" * 64),
                                     (receipt, "image_labels", {})):
            original = target[key]
            target[key] = invalid
            try:
                with self.subTest(key=key), self.assertRaises(ValueError):
                    validate()
            finally:
                target[key] = original

    def test_candidate_creation_persists_identity_before_returning_start(self):
        import json
        from types import SimpleNamespace
        state = {"candidate_containers": ["synthetic-candidate"]}
        journal = self.root / "candidate-state.json"
        def update(**values):
            state.update(values)
            journal.write_text(json.dumps(state))
        session = SimpleNamespace(directory=self.root, state=state, update=update, manifest=self.manifest,
                                  capsule={"window_id": "synthetic-window"})
        calls = []
        observed = [{"Id": "d" * 64, "State": {"Running": False},
                     "Config": {"Labels": {"q03.window": "synthetic-window"}}}]
        facade = SimpleNamespace(
            execute=lambda _, argv, **kw: calls.append(argv),
            run=lambda argv: SimpleNamespace(stdout=json.dumps(observed)))
        start = self.core.RuntimeOps.prepare_candidate(
            facade, session, "synthetic-candidate", ["docker", "run", "--name", "synthetic-candidate", "image"])
        self.assertEqual(calls[0][:2], ["docker", "create"])
        self.assertEqual(start, ["docker", "start", "--attach", "d" * 64])
        self.assertEqual(json.loads(journal.read_text())["candidate_container_ids"],
                         {"synthetic-candidate": "d" * 64})
        # Failure before identity cannot authorize a start command.
        facade.execute = mock.Mock(side_effect=RuntimeError("create failed"))
        facade.run = mock.Mock(side_effect=AssertionError("inspect after failed create"))
        with self.assertRaisesRegex(RuntimeError, "create failed"):
            self.core.RuntimeOps.prepare_candidate(
                facade, session, "synthetic-candidate", ["docker", "run", "image"])
        facade.run.assert_not_called()

    def test_host_environment_strips_remote_daemon_selectors(self):
        contaminated = {"DOCKER_HOST": "tcp://untrusted.invalid:2375",
                        "DOCKER_CONTEXT": "foreign", "BUILDX_BUILDER": "foreign",
                        "BUILDKIT_HOST": "tcp://foreign.invalid", "PATH": "/synthetic/bin",
                        "PYTHONPATH": "/image/baked"}
        with mock.patch.dict(os.environ, contaminated, clear=True):
            actual = self.core.command_environment()
        self.assertEqual(actual["DOCKER_HOST"], "unix:///var/run/docker.sock")
        self.assertEqual(actual["PYTHONPATH"], "/image/baked")
        self.assertEqual(actual["PATH"], "/synthetic/bin")
        self.assertEqual(actual["GIT_OPTIONAL_LOCKS"], "0")
        self.assertEqual(actual["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertEqual({key for key in actual if key.startswith(
            ("DOCKER_", "BUILDX_", "BUILDKIT_"))}, {"DOCKER_HOST"})

    def test_success_restores_registered_policy_then_releases_hold(self):
        state = self.run_window()
        self.assertTrue(state["restored"])
        self.assertFalse(self.hold.exists())
        self.assertEqual(self.ops.events, [
            "check:before_hold", "watchdog:spawn", "watchdog:ready",
            "check:before_stop", "stop:1", "quiesce", "check:stopped", "job:p0",
            "result:p0", "check:stopped", "job:g1", "result:g1",
            "check:stopped", "terminate", "quiesce", "cleanup",
            "check:before_restore", "stop:2", "quiesce", "check:restore_drained",
            "restore:start", "quiesce", "check:restored",
        ])
        restore = self.ops.commands[-1]
        self.assertEqual(restore[restore.index("--policy") + 1],
                         self.manifest["preflight"]["policy_path"])
        self.assertNotIn("acceptance.json", " ".join(restore))
        self.assertNotIn("glm", " ".join(sum(self.ops.commands, [])))

    def test_guard_or_watchdog_failure_never_stops_service(self):
        for fault in ("check:before_hold", "watchdog:spawn", "watchdog:ready",
                      "check:before_stop"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory(dir=self.root) as directory:
                self.directory = Path(directory) / "session"
                self.ops = FakeOps(fault)
                self.ops.commands = []
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    self.run_window()
                self.assertEqual(self.ops.commands, [])
                if (self.directory / "state.json").exists():
                    self.assertFalse(self.state()["mutation_started"])
                self.assertFalse(self.hold.exists())

    def test_partial_stop_and_failed_jobs_always_restore(self):
        for fault in ("stop:1", "job:p0", "job:g1"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory(dir=self.root) as directory:
                self.directory = Path(directory) / "session"
                self.ops = FakeOps(fault)
                self.ops.commands = []
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    self.run_window()
                self.assertIn("restore:start", self.ops.events)
                self.assertTrue(self.state()["restored"])
                self.assertIn("failure", self.state())
                self.assertFalse(self.hold.exists())
                if fault == "job:p0":
                    self.assertNotIn("job:g1", self.ops.events)

    def test_restore_failures_retain_hold_and_recovery_can_retry(self):
        for fault in ("cleanup", "check:before_restore", "stop:2", "restore:start",
                      "check:restored"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory(dir=self.root) as directory:
                self.directory = Path(directory) / "session"
                self.ops = FakeOps(fault)
                self.ops.commands = []
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    self.run_window()
                self.assertTrue(self.hold.exists())
                self.assertFalse(self.state()["restored"])
                self.assertEqual(self.state()["phase"], "recovery-failed")
                self.ops.failure = None
                state = self.core.recover(self.directory, self.ops)
                self.assertTrue(state["restored"])
                self.assertFalse(self.hold.exists())

    def test_foreign_hold_race_never_overwrites_or_stops(self):
        original = '{"holder":"another", "window":"foreign"}'
        def foreign_hold(event):
            if event == "check:before_hold":
                self.hold.write_text(original)
        self.ops.hook = foreign_hold
        with self.assertRaises(FileExistsError):
            self.run_window()
        self.assertEqual(self.hold.read_text(), original)
        self.assertEqual(self.ops.commands, [])

    def test_request_race_before_hold_refuses_without_stop(self):
        request = Path(self.manifest["preflight"]["request_path"])
        def request_cluster(event):
            if event == "check:before_hold":
                request.write_text("{}")
        self.ops.hook = request_cluster
        with self.assertRaisesRegex(RuntimeError, "requests"):
            self.run_window()
        self.assertFalse(self.hold.exists())
        self.assertEqual(self.ops.commands, [])

    def test_job_cancellation_and_timeout_restore(self):
        for exception in (KeyboardInterrupt("cancel"), TimeoutError("timeout")):
            with (self.subTest(exception=type(exception).__name__),
                  tempfile.TemporaryDirectory(dir=self.root) as directory):
                self.directory = Path(directory) / "session"
                self.ops = FakeOps()
                self.ops.commands = []
                def fail_job(event):
                    if event == "job:p0":
                        raise exception
                self.ops.hook = fail_job
                with self.assertRaises(type(exception)):
                    self.run_window()
                self.assertTrue(self.state()["restored"])
                self.assertFalse(self.hold.exists())

    def test_recovery_refuses_foreign_hold_without_cleanup_or_commands(self):
        self.ops.failure = "restore:start"
        with self.assertRaises(RuntimeError):
            self.run_window()
        self.hold.write_text('{"holder":"foreign", "window":"foreign"}')
        self.ops.events.clear()
        self.ops.commands.clear()
        with self.assertRaisesRegex(RuntimeError, "foreign"):
            self.core.recover(self.directory, self.ops)
        self.assertEqual(self.ops.events, [])
        self.assertEqual(self.ops.commands, [])
        self.assertTrue(self.hold.exists())

    def test_unknown_or_failed_stage_result_restores_and_stops_following_jobs(self):
        self.ops.failure = "result:p0"
        with self.assertRaisesRegex(RuntimeError, "result:p0"):
            self.run_window()
        self.assertNotIn("job:g1", self.ops.events)
        self.assertTrue(self.state()["restored"])
        self.assertNotEqual(self.state()["outcome"], "completed")

    def test_pending_request_after_stop_cancels_instead_of_reporting_success(self):
        request = Path(self.manifest["preflight"]["request_path"])
        self.ops.hook = lambda event: request.write_text("{}") if event == "check:stopped" else None
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            self.run_window()
        self.assertNotIn("job:p0", self.ops.events)
        self.assertEqual(self.state()["outcome"], "cancelled")
        self.assertTrue(self.state()["restored"])
        self.assertFalse(self.hold.exists())

    def test_empty_duplicate_or_invalid_job_list_refuses_before_hold(self):
        for jobs in ([], [{"kind": "unknown", "timeout_seconds": 1}],
                     [{"kind": "p0", "timeout_seconds": 1}] * 2,
                     [{"kind": "g1", "timeout_seconds": 0}]):
            with self.subTest(jobs=jobs):
                self.manifest["jobs"] = jobs
                with self.assertRaises(ValueError):
                    self.run_window()
                self.assertEqual(self.ops.events, [])
                self.assertFalse(self.directory.exists())
                self.assertFalse(self.hold.exists())

    def test_original_cache_immutable_overlap_and_symlink_paths_refused(self):
        original = copy.deepcopy(self.manifest)
        for key, path in (("cache_path", original["preflight"]["protected_roots"][0] + "/cache"),
                          ("output_path", original["candidate"]["tests_path"]),
                          ("cache_path", str(self.directory)),
                          ("cache_path", str(self.root / "comma,path"))):
            self.manifest = copy.deepcopy(original)
            self.manifest["candidate"][key] = path
            with self.subTest(key=key, path=path), self.assertRaises(ValueError):
                self.run_window()
            self.assertEqual(self.ops.events, [])
        alias = self.root / "alias"
        alias.symlink_to(original["preflight"]["protected_roots"][0])
        self.manifest = copy.deepcopy(original)
        self.manifest["candidate"]["cache_path"] = str(alias / "cache")
        with self.assertRaises(ValueError):
            self.run_window()
        self.assertFalse(self.hold.exists())

    def test_crash_after_restore_flag_before_release_is_recoverable(self):
        with mock.patch.object(self.core.Session, "release",
                               side_effect=RuntimeError("release interrupted")):
            with self.assertRaisesRegex(RuntimeError, "release interrupted"):
                self.run_window()
        self.assertTrue(self.state()["restored"])
        self.assertTrue(self.hold.exists())
        self.ops.events.clear()
        self.ops.commands.clear()
        self.core.recover(self.directory, self.ops)
        self.assertEqual(self.ops.events, ["check:restored"])
        self.assertEqual(self.ops.commands, [])
        self.assertFalse(self.hold.exists())

    def test_mutated_recovery_capsule_refuses_without_any_operations(self):
        self.ops.failure = "restore:start"
        with self.assertRaises(RuntimeError):
            self.run_window()
        capsule = self.directory / "capsule.json"
        capsule.write_bytes(capsule.read_bytes() + b" ")
        self.ops.events.clear()
        self.ops.commands.clear()
        with self.assertRaisesRegex(RuntimeError, "capsule"):
            self.core.recover(self.directory, self.ops)
        self.assertEqual(self.ops.events, [])
        self.assertTrue(self.hold.exists())

    def test_actual_p0_summary_requires_both_modes_and_expected_head(self):
        import json
        output = Path(self.manifest["candidate"]["output_path"]) / "p0"
        output.mkdir(parents=True)
        path = output / "summary.json"
        valid = {"result": "passed", "head": self.manifest["candidate"]["component_head"],
                 "results": {name: {"exit_code": 0, "passed": True}
                             for name in ("default", "string")}}
        path.write_text(json.dumps(valid))
        ops = self.core.RuntimeOps()
        ops.validate_result(self.manifest, "p0")
        variants = [dict(valid, result="failed"), dict(valid, head="c" * 40),
                    dict(valid, results={"default": valid["results"]["default"]})]
        for mode in ("default", "string"):
            for field, value in (("exit_code", 1), ("passed", False)):
                changed = copy.deepcopy(valid)
                changed["results"][mode][field] = value
                variants.append(changed)
        for changed in variants:
            path.write_text(json.dumps(changed))
            with self.subTest(changed=changed), self.assertRaises(RuntimeError):
                ops.validate_result(self.manifest, "p0")

    def test_build_failure_restores_and_success_receipt_remains_separate(self):
        self.manifest.pop("candidate")
        self.manifest["jobs"] = [{"kind": "build", "timeout_seconds": 60}]
        for fault in ("job:build", "result:build", None):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory(dir=self.root) as directory:
                self.directory = Path(directory) / "session"
                self.ops = FakeOps(fault)
                self.ops.commands = []
                if fault:
                    with self.assertRaisesRegex(RuntimeError, "injected"):
                        self.run_window()
                    self.assertEqual(self.state()["outcome"], "failed")
                else:
                    self.run_window()
                    receipt = self.ops.read_json(self.directory / "build-result.json")
                    self.assertEqual(receipt["scope"], "synthetic build receipt")
                    self.assertEqual(self.state()["job_results"], ["build"])
                self.assertTrue(self.state()["restored"])
                self.assertFalse(self.hold.exists())
                self.assertEqual(self.ops.lock_depth, 0)
                self.assertNotIn("job:p0", self.ops.events)

    def test_build_preparation_failure_precedes_hold_and_session_writes(self):
        self.manifest.pop("candidate")
        self.manifest["jobs"] = [{"kind": "build", "timeout_seconds": 60}]
        self.ops.failure = "prepare:build"
        with self.assertRaisesRegex(RuntimeError, "prepare:build"):
            self.run_window()
        self.assertFalse(self.directory.exists())
        self.assertFalse(self.hold.exists())
        self.assertEqual(self.ops.commands, [])

    def test_unreviewed_build_supervision_blocks_before_any_service_action(self):
        self.manifest.pop("candidate")
        self.manifest["jobs"] = [{"kind": "build", "timeout_seconds": 60}]
        self.ops.prepare_build = lambda manifest: {"requires_supervised_quiescence": True}
        with self.assertRaisesRegex(RuntimeError, "supervised quiescence"):
            self.run_window()
        self.assertFalse(self.directory.exists())
        self.assertFalse(self.hold.exists())
        self.assertEqual(self.ops.events, [])
        self.assertEqual(self.ops.commands, [])

    def test_uncertain_remote_barrier_keeps_hold_without_restore_start(self):
        self.ops.failure = "quiesce"
        with self.assertRaisesRegex(RuntimeError, "quiesce"):
            self.run_window()
        self.assertTrue(self.hold.exists())
        self.assertFalse(self.state()["restored"])
        self.assertEqual(self.state()["phase"], "recovery-failed")
        self.assertNotIn("restore:start", self.ops.events)
        self.assertNotIn("cleanup", self.ops.events)
        self.assertNotIn("job:p0", self.ops.events)
        # Repeating recovery with uncertainty must not invent quiescence.
        with self.assertRaisesRegex(RuntimeError, "quiesce"):
            self.core.recover(self.directory, self.ops)
        self.assertTrue(self.hold.exists())
        self.assertNotIn("restore:start", self.ops.events)

    def test_actual_receipt_guard_requires_window_epoch_and_strict_safe_boolean(self):
        import json
        path = self.root / "receipt.json"
        expected = {"window_id": "window", "epoch": "epoch", "safe_to_overlap": True}
        result = {"log_path": str(path)}
        ops = self.core.RuntimeOps()
        path.write_text(json.dumps(expected))
        ops.require_receipt(result, expected)
        for key, value in (("window_id", "foreign"), ("epoch", "foreign"),
                           ("safe_to_overlap", False), ("safe_to_overlap", 1),
                           ("safe_to_overlap", "true")):
            path.write_text(json.dumps(dict(expected, **{key: value})))
            with self.subTest(key=key, value=value), self.assertRaises(RuntimeError):
                ops.require_receipt(result, expected)
        path.write_text("x" * (1024 * 1024 + 1))
        with self.assertRaisesRegex(RuntimeError, "bounded"):
            ops.require_receipt(result, expected)

    def test_uncertain_build_quiescence_prevents_service_restoration(self):
        self.manifest.pop("candidate")
        self.manifest["jobs"] = [{"kind": "build", "timeout_seconds": 60}]
        self.ops.failure = "cleanup:build"
        with self.assertRaisesRegex(RuntimeError, "cleanup:build"):
            self.run_window()
        self.assertTrue(self.hold.exists())
        self.assertFalse(self.state()["restored"])
        self.assertNotIn("restore:start", self.ops.events)
        self.assertNotIn("cleanup", self.ops.events)

    def test_recovery_helper_source_drift_precedes_any_transition(self):
        self.ops.failure = "restore:start"
        with self.assertRaises(RuntimeError):
            self.run_window()
        original = self.ops.recovery_sources()
        self.ops.recovery_sources = lambda: dict(original, **{"q03_remote_operation.py": "b" * 64})
        self.ops.events.clear()
        self.ops.commands.clear()
        with self.assertRaisesRegex(RuntimeError, "dependencies changed"):
            self.core.recover(self.directory, self.ops)
        self.assertEqual(self.ops.events, [])
        self.assertEqual(self.ops.commands, [])
        self.assertTrue(self.hold.exists())

    def test_actual_adapter_unknown_receipts_block_core_restore_on_all_four_nodes(self):
        import json
        from types import SimpleNamespace
        adapter = load_module("q03_original_spark_adapter", "ADAPTER")
        nodes = {"nodes": [{"name": f"node0{i}"} for i in range(1, 5)]}
        cluster = {"container": {"name": "registered", "image": "sha256:" + "a" * 64},
                   "deployment": {"ready_url": "http://127.0.0.1:8000/health"}}
        import hashlib
        helper_digest = hashlib.sha256(Path(adapter.__file__).with_name(
            "q03_remote_operation.py").read_bytes()).hexdigest()
        recovery_sources = self.ops.recovery_sources()
        recovery_sources["q03_remote_operation.py"] = helper_digest
        self.ops.recovery_sources = lambda: recovery_sources
        self.manifest["preflight"].update(
            remote_helper_path=str(self.root / "q03_remote_operation.py"),
            remote_helper_sha256=helper_digest,
            remote_authority_path=str(self.root / "authority.json"),
            remote_authority_sha256="b" * 64)
        seals = []
        def quiesce(session):
            def execute_receipt(_, argv, **kwargs):
                self.assertEqual(argv[argv.index("--action") + 1], "quiesce")
                epoch = argv[argv.index("--epoch") + 1]
                def transport(node_map, node, *command):
                    if command[command.index("-c") + 4] == "verify":
                        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({
                            "window": session.capsule["window_id"], "epoch": epoch,
                            "helper_sha256": helper_digest, "authority_sha256": "b" * 64}))
                    self.assertEqual(command[command.index("-c") + 4], "seal")
                    seals.append((epoch, node["name"]))
                    value = {"window": session.capsule["window_id"], "epoch": epoch,
                             "sealed": True, "safe_to_overlap": False,
                             "operations": [{"status": "uncertain"}]}
                    return SimpleNamespace(returncode=0, stdout=json.dumps(value), stderr="")
                remote = adapter.RemoteOperations(
                    SimpleNamespace(), cluster, nodes, session.capsule,
                    session.capsule["window_id"], epoch, session.directory / "evidence",
                    transport=transport,
                    sleep=lambda _: self.fail("uncertain receipt must not be waited away"))
                remote.verify_helpers()
                report = {"window_id": session.capsule["window_id"], "epoch": epoch,
                          "sealed_epochs": remote.quiesce(), "safe_to_overlap": True}
                path = session.directory / "synthetic-transport-receipt.json"
                path.write_text(json.dumps(report))
                return {"log_path": str(path)}
            facade = SimpleNamespace(execute=execute_receipt,
                                     require_receipt=self.core.RuntimeOps().require_receipt)
            self.core.RuntimeOps.quiesce_original(facade, session)
        self.ops.quiesce_original = quiesce
        with self.assertRaisesRegex(RuntimeError, "uncertain"):
            self.run_window()
        self.assertTrue(self.hold.exists())
        self.assertFalse(self.state()["restored"])
        self.assertNotIn("restore:start", self.ops.events)
        self.assertNotIn("cleanup", self.ops.events)
        self.assertGreaterEqual(len(seals), 8)  # Initial stop and recovery both refuse.
        for index in range(0, len(seals), 4):
            self.assertEqual([node for _, node in seals[index:index + 4]],
                             [node["name"] for node in nodes["nodes"]])


class WatchOps:
    def __init__(self, module):
        self.events = []
        self.alive = True
        self.clock = 1000
        self.heartbeat = module.utc(1000)
        self.restored = False
        self.attempted = False
        self.failure = None

    def event(self, name):
        self.events.append(name)
        if self.failure == name:
            raise RuntimeError("injected " + name)

    def verify_capsule(self):
        self.event("verify")

    def state(self):
        return {"restored": self.restored, "hold_released": self.restored}

    def owned_hold(self):
        self.event("hold")
        return {"heartbeat": self.heartbeat}

    def now(self):
        return self.clock

    def core_alive(self):
        return self.alive

    def terminate_core(self):
        self.event("terminate")
        self.alive = False

    def journal(self, event, **fields):
        self.events.append("journal:" + event)

    def recovery_attempted(self):
        return self.attempted

    def mark_recovery_attempted(self):
        self.event("mark")
        self.attempted = True

    def recover(self):
        self.event("recover")
        self.restored = True


class WatchdogFaultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.watch = load_module("q03_window_watchdog", "WATCHDOG")

    def setUp(self):
        self.ops = WatchOps(self.watch)
        self.capsule = {"deadline_epoch": 2000, "heartbeat_timeout_s": 60}

    def step(self):
        return self.watch.Watchdog(self.capsule, self.ops).step()

    def test_fresh_live_core_waits_without_recovery(self):
        self.assertEqual(self.step(), "waiting")
        self.assertEqual(self.ops.events, ["verify", "hold"])
        self.assertFalse(self.ops.attempted)

    def test_stale_future_or_unknown_heartbeat_triggers_single_recovery(self):
        for heartbeat in (self.watch.utc(900), self.watch.utc(1010), "invalid", None):
            with self.subTest(heartbeat=heartbeat):
                self.ops = WatchOps(self.watch)
                self.ops.heartbeat = heartbeat
                watch = self.watch.Watchdog(self.capsule, self.ops)
                self.assertEqual(watch.step(), "restored")
                self.assertEqual(self.ops.events.count("recover"), 1)
                self.assertLess(self.ops.events.index("terminate"),
                                self.ops.events.index("recover"))
                with self.assertRaisesRegex(RuntimeError, "terminal"):
                    watch.step()

    def test_dead_core_recovers_without_signalling_reused_pid(self):
        self.ops.alive = False
        self.assertEqual(self.step(), "restored")
        self.assertNotIn("terminate", self.ops.events)
        self.assertEqual(self.ops.events.count("recover"), 1)

    def test_deadline_recovers_even_with_recent_heartbeat(self):
        self.capsule["deadline_epoch"] = 1000
        self.assertEqual(self.step(), "restored")
        self.assertIn("terminate", self.ops.events)

    def test_unknown_capsule_or_foreign_hold_prevents_recovery(self):
        for fault in ("verify", "hold"):
            with self.subTest(fault=fault):
                self.ops = WatchOps(self.watch)
                self.ops.failure = fault
                self.ops.alive = False
                self.assertEqual(self.step(), "failed")
                self.assertNotIn("recover", self.ops.events)
                self.assertNotIn("terminate", self.ops.events)

    def test_failed_or_duplicate_recovery_cannot_report_restored(self):
        for fault in ("recover", "terminate", "duplicate", "no-restoration"):
            with self.subTest(fault=fault):
                self.ops = WatchOps(self.watch)
                self.ops.heartbeat = self.watch.utc(900)
                if fault == "duplicate":
                    self.ops.attempted = True
                elif fault == "no-restoration":
                    self.ops.recover = lambda: self.ops.event("recover")
                else:
                    self.ops.failure = fault
                self.assertEqual(self.step(), "failed")
                self.assertFalse(self.ops.restored)
                self.assertNotIn("journal:restored", self.ops.events)

    def test_termination_completion_does_not_inspect_replacement_hold(self):
        for phase in ("restored", "aborted-before-mutation"):
            with self.subTest(phase=phase):
                self.ops = WatchOps(self.watch)
                self.ops.heartbeat = self.watch.utc(900)
                state = {"phase": "running", "restored": False,
                         "hold_released": False}
                self.ops.state = lambda: dict(state)
                def finish():
                    self.ops.event("terminate")
                    self.ops.alive = False
                    state.update(phase=phase, restored=phase == "restored",
                                 hold_released=True)
                    self.ops.failure = "hold"  # Foreign replacement must not be read.
                self.ops.terminate_core = finish
                self.assertEqual(self.step(), "restored")
                self.assertEqual(self.ops.events.count("hold"), 1)
                self.assertEqual(self.ops.events.count("verify"), 2)
                self.assertNotIn("mark", self.ops.events)
                self.assertNotIn("recover", self.ops.events)

    def test_termination_unfinished_state_still_requires_owned_hold(self):
        self.ops.heartbeat = self.watch.utc(900)
        def finish():
            self.ops.event("terminate")
            self.ops.alive = False
            self.ops.failure = "hold"
        self.ops.terminate_core = finish
        self.assertEqual(self.step(), "failed")
        self.assertEqual(self.ops.events.count("hold"), 2)
        self.assertNotIn("mark", self.ops.events)
        self.assertNotIn("recover", self.ops.events)

    def test_termination_restored_without_release_recomputes_reconcile(self):
        self.ops.heartbeat = self.watch.utc(900)
        state = {"restored": False, "hold_released": False}
        self.ops.state = lambda: dict(state)
        def finish():
            self.ops.event("terminate")
            self.ops.alive = False
            state.update(restored=True)
            self.ops.failure = "hold"
        def recover():
            self.ops.event("recover")
            state.update(hold_released=True)
        self.ops.terminate_core = finish
        self.ops.recover = recover
        self.assertEqual(self.step(), "restored")
        self.assertEqual(self.ops.events.count("hold"), 1)
        self.assertEqual(self.ops.events.count("recover"), 1)

    def test_termination_completion_still_revalidates_capsule(self):
        self.ops.heartbeat = self.watch.utc(900)
        def finish():
            self.ops.event("terminate")
            self.ops.alive = False
            self.ops.restored = True
            self.ops.failure = "verify"
        self.ops.terminate_core = finish
        self.assertEqual(self.step(), "failed")
        self.assertNotIn("recover", self.ops.events)

    def test_actual_hold_helpers_preserve_foreign_records_and_reject_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hold.json"
            record = {"holder": "owner", "window": "window"}
            self.watch.claim_hold(path, record)
            original = path.read_bytes()
            with self.assertRaises(FileExistsError):
                self.watch.claim_hold(path, {"holder": "foreign", "window": "foreign"})
            for operation in (self.watch.read_owned_hold, self.watch.refresh_hold,
                              self.watch.release_hold):
                with self.assertRaisesRegex(RuntimeError, "foreign"):
                    operation(path, "foreign", "foreign")
                self.assertEqual(path.read_bytes(), original)
            alias = Path(directory) / "alias.json"
            alias.symlink_to(path)
            with self.assertRaises(OSError):
                self.watch.refresh_hold(alias, "window", "owner")
            self.watch.refresh_hold(path, "window", "owner", 1000)
            self.assertEqual(self.watch.read_owned_hold(path, "window", "owner")["heartbeat"],
                             self.watch.utc(1000))
            self.watch.release_hold(path, "window", "owner")
            self.assertFalse(path.exists())

    def test_restoring_gets_bounded_recovery_budget_not_expired_job_window(self):
        self.capsule.update(deadline_epoch=900, recovery_timeout_s=100)
        self.ops.state = lambda: {"restored": self.ops.restored,
                                 "hold_released": self.ops.restored,
                                 "phase": "restoring", "restoring_started_epoch": 950}
        self.assertEqual(self.step(), "waiting")
        self.ops.clock = 1050
        self.ops.heartbeat = self.watch.utc(1050)
        self.assertEqual(self.step(), "restored")

    def test_heartbeat_inode_race_does_not_mutate_replacement_hold(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hold.json"
            old = Path(directory) / "old.json"
            self.watch.claim_hold(path, {"holder": "owner", "window": "window"})
            replacement = b'{"holder":"foreign","window":"foreign"}'
            pwrite = os.pwrite
            def replace_then_write(fd, data, offset):
                path.rename(old)
                path.write_bytes(replacement)
                return pwrite(fd, data, offset)
            with mock.patch.object(os, "pwrite", side_effect=replace_then_write):
                with self.assertRaisesRegex(RuntimeError, "path changed"):
                    self.watch.refresh_hold(path, "window", "owner", 1000)
            self.assertEqual(path.read_bytes(), replacement)

    def test_verified_restore_without_release_is_reconciled_after_core_death(self):
        self.ops.alive = False
        self.capsule["recovery_timeout_s"] = 100
        self.ops.state = lambda: {
            "restored": True, "hold_released": self.ops.restored,
            "phase": "restored", "restoring_started_epoch": 950,
        }
        self.assertEqual(self.step(), "restored")
        self.assertIn("recover", self.ops.events)
        self.assertNotIn("terminate", self.ops.events)


if __name__ == "__main__":
    unittest.main(verbosity=2)
