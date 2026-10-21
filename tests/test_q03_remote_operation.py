"""CPU sentinel fault injection; no real child, SSH, service or GPU operation.

Q03_WINDOW_REMOTE_ROOT may select the read-only sentinel development worktree.
Synthetic process identities and clocks test accounting, not site survivability.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[1]


def load_sentinel():
    root = Path(os.environ.get("Q03_WINDOW_REMOTE_ROOT", ROOT))
    path = root / "scripts/q03_remote_operation.py"
    spec = importlib.util.spec_from_file_location("q03_remote_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ChildOps:
    """No child is launched: explicit lifetime states drive actual supervisor."""
    def __init__(self, directory):
        self.directory = directory
        self.clock = 0
        self.events = []
        self.child = {"pid": 987654321, "start_ticks": "synthetic-child"}
        self.alive = False
        self.stubborn = False
        self.returncode = 0
        self.hook = None

    def launch_gated(self, manifest, log):
        self.events.append("launch-gated")
        return self.child

    def release_gate(self):
        state = json.loads((self.directory / "state.json").read_text())
        assert state["child"] == self.child, "child identity not durable before gate"
        assert state["phase"] == "running"
        self.events.append("release-gate")
        if self.hook:
            self.hook()

    def now(self):
        return self.clock

    def sleep(self, seconds):
        self.clock += seconds

    def exited(self):
        return not self.alive

    def group_live(self):
        return self.alive

    def terminate_group(self):
        self.events.append("terminate-group")
        if not self.stubborn:
            self.alive = False

    def reap(self):
        assert not self.alive, "cannot prove receipt while child group remains"
        self.events.append("reap")
        return self.returncode


class RemoteOperationFaults(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.remote = load_sentinel()

    def setUp(self):
        import subprocess
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.window, self.operation, self.epoch = (str(uuid.uuid4()) for _ in range(3))
        self.supervisor = {"pid": 123456789, "start_ticks": "synthetic-supervisor"}
        patch = mock.patch.object(self.remote, "identity", return_value=self.supervisor)
        patch.start()
        self.addCleanup(patch.stop)
        for name in ("Popen", "run", "check_output", "check_call"):
            patch = mock.patch.object(subprocess, name,
                                      side_effect=AssertionError("real process forbidden"))
            patch.start()
            self.addCleanup(patch.stop)

    def create(self, operation=None):
        return self.remote.create_operation(
            self.root, self.window, operation or self.operation,
            ["inert-test-command"], str(self.root), 1, self.epoch)

    def status(self, alive=None):
        return self.remote.status(self.root, self.window, self.operation, alive)

    def test_child_identity_is_durable_before_gate_and_receipt_after_reap(self):
        directory = self.create()
        ops = ChildOps(directory)
        result = self.remote.supervise(directory, ops)
        self.assertEqual(ops.events, ["launch-gated", "release-gate", "reap"])
        self.assertTrue(result["group_quiescent"])
        self.assertTrue(self.status()["action_succeeded"])
        self.assertFalse(self.status()["safe_to_overlap"])
        sealed = self.remote.seal_epoch(self.root, self.window, self.epoch)
        self.assertTrue(sealed["safe_to_overlap"])

    def test_dead_or_pid_reused_supervisor_without_receipt_remains_uncertain(self):
        directory = self.create()
        self.remote.atomic(directory / "state.json",
                           {"phase": "running", "supervisor": self.supervisor})
        seen = []
        def reused(record):
            seen.append(record)
            return False
        result = self.status(reused)
        self.assertEqual(seen, [self.supervisor])
        self.assertEqual(result["status"], "uncertain")
        replacement = dict(self.supervisor, start_ticks="reused-pid")
        with mock.patch.object(self.remote, "identity", return_value=replacement):
            self.assertEqual(self.status()["status"], "uncertain")
        self.assertIsNot(result.get("safe_to_overlap"), True)
        sealed = self.remote.seal_epoch(self.root, self.window, self.epoch, reused)
        self.assertEqual(sealed["status"], "blocked")
        self.assertFalse(sealed["safe_to_overlap"])

    def test_cancel_request_is_not_a_terminal_receipt(self):
        directory = self.create()
        result = self.remote.request_cancel(self.root, self.window, self.operation)
        self.assertEqual(result["status"], "uncertain")
        self.assertFalse((directory / "receipt.json").exists())
        self.assertFalse(self.remote.seal_epoch(self.root, self.window, self.epoch)["safe_to_overlap"])

    def test_interrupted_and_nonzero_operations_never_allow_overlap(self):
        for outcome in ("cancelled", "timeout", "nonzero", "exit137", "signal"):
            with self.subTest(outcome=outcome):
                self.operation = str(uuid.uuid4())
                directory = self.create()
                ops = ChildOps(directory)
                if outcome == "cancelled":
                    self.remote.request_cancel(self.root, self.window, self.operation)
                    ops.alive = True
                elif outcome == "timeout":
                    ops.alive = True
                else:
                    ops.returncode = {"nonzero": 1, "exit137": 137, "signal": -9}[outcome]
                result = self.remote.supervise(directory, ops)
                self.assertTrue(result["group_quiescent"])
                self.assertFalse(self.status()["safe_to_overlap"])
                self.assertFalse(self.status()["action_succeeded"])
                if outcome in ("cancelled", "timeout"):
                    self.assertIn("terminate-group", ops.events)
        self.assertFalse(self.remote.seal_epoch(self.root, self.window, self.epoch)["safe_to_overlap"])

    def test_unquiesced_group_never_gets_terminal_receipt(self):
        directory = self.create()
        ops = ChildOps(directory)
        ops.alive = ops.stubborn = True
        self.remote.request_cancel(self.root, self.window, self.operation)
        with self.assertRaisesRegex(RuntimeError, "quiesce"):
            self.remote.supervise(directory, ops)
        self.assertFalse((directory / "receipt.json").exists())
        self.assertEqual(self.status(lambda record: False)["status"], "uncertain")

    def test_foreign_cancel_and_corrupt_manifest_fail_without_receipt(self):
        directory = self.create()
        self.remote.atomic(directory / "cancel.json", {"window": "foreign"})
        with self.assertRaisesRegex(RuntimeError, "foreign"):
            self.remote.supervise(directory, ChildOps(directory))
        self.assertFalse((directory / "receipt.json").exists())
        path = directory / "manifest.json"
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(RuntimeError, "digest"):
            self.status()

    def test_duplicate_uuid_wrong_window_and_aliases_are_rejected(self):
        directory = self.create()
        original = (directory / "manifest.json").read_bytes()
        with self.assertRaises(FileExistsError):
            self.create()
        self.assertEqual((directory / "manifest.json").read_bytes(), original)
        with self.assertRaises(FileNotFoundError):
            self.remote.status(self.root, str(uuid.uuid4()), self.operation)
        for window in ("../escape", "wrong", ""):
            with self.assertRaises(ValueError):
                self.remote.operation_dir(self.root, window, self.operation)
        alias = self.root / "alias"
        alias.symlink_to(directory)
        with self.assertRaises(ValueError):
            self.remote.operation_dir(alias, self.window, self.operation)

    def test_sealed_empty_epoch_rejects_delayed_registration(self):
        result = self.remote.seal_epoch(self.root, self.window, self.epoch)
        self.assertTrue(result["safe_to_overlap"])
        self.assertEqual(result["operations"], [])
        with self.assertRaisesRegex(RuntimeError, "sealed"):
            self.create()
        self.assertFalse(self.remote.operation_dir(self.root, self.window, self.operation).exists())

    def test_terminal_receipt_must_match_owned_manifest_and_group(self):
        directory = self.create()
        result = self.remote.supervise(directory, ChildOps(directory))
        for field, value in (("window", str(uuid.uuid4())), ("operation", str(uuid.uuid4())),
                             ("manifest_sha256", "a" * 64), ("group_quiescent", False)):
            self.remote.atomic(directory / "receipt.json", dict(result, **{field: value}))
            with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, "receipt"):
                self.status()

    def test_client_disconnect_does_not_imply_supervised_operation_finished(self):
        directory = self.create()
        ops = ChildOps(directory)
        observations = []
        def disconnected_client_observation():
            # Client transport state is not an input to the still-live supervisor.
            alive = lambda record: record == self.supervisor
            observations.append(self.status(alive)["status"])
            observations.append(self.remote.seal_epoch(
                self.root, self.window, self.epoch, alive)["safe_to_overlap"])
        ops.hook = disconnected_client_observation
        self.remote.supervise(directory, ops)
        self.assertEqual(observations, ["running", False])
        # Only the later supervisor receipt establishes termination.
        self.assertTrue(self.remote.seal_epoch(
            self.root, self.window, self.epoch)["safe_to_overlap"])

    def test_cli_rejects_unapproved_root_or_protected_cwd_before_creation(self):
        approved = str(self.root / "remote-operations")
        authority = self.root / "authority.json"
        authority.write_text(json.dumps({"independent_root": str(self.root),
                                         "protected_roots": ["/synthetic/protected"]}))
        digest = hashlib.sha256(authority.read_bytes()).hexdigest()
        for root, cwd in (("/synthetic/protected/operations", str(self.root)),
                          (approved, "/synthetic/protected/original"),
                          (approved, "/synthetic/protected/config"), (approved, "/")):
            argv = ["sentinel", "launch", "--root", root, "--window", self.window,
                    "--operation", self.operation, "--epoch", self.epoch,
                    "--argv-json", '["inert-test-command"]', "--cwd", cwd,
                    "--timeout-seconds", "1", "--authority", str(authority),
                    "--authority-sha256", digest]
            with (self.subTest(root=root, cwd=cwd), mock.patch.object(sys, "argv", argv),
                  mock.patch.object(self.remote, "create_operation") as create):
                with self.assertRaises(ValueError):
                    self.remote.main()
                create.assert_not_called()

    def test_remote_environment_is_local_daemon_and_argv_is_unchanged(self):
        inherited = {"DOCKER_HOST": "tcp://foreign.invalid", "DOCKER_CONTEXT": "foreign",
                     "BUILDX_BUILDER": "foreign", "BUILDKIT_HOST": "foreign",
                     "PATH": "/synthetic/bin", "PYTHONPATH": "/unrelated/value"}
        original = dict(inherited)
        actual = self.remote.operation_environment(inherited)
        self.assertEqual(inherited, original)
        self.assertEqual(actual["DOCKER_HOST"], "unix:///var/run/docker.sock")
        self.assertEqual({key for key in actual if key.startswith(
            ("DOCKER_", "BUILDX_", "BUILDKIT_"))}, {"DOCKER_HOST"})
        self.assertEqual(actual["PATH"], inherited["PATH"])
        command = ["bash", "-lc", "printf '%s' 'two words'"]
        bound = self.remote.bound_command(command)
        self.assertEqual(bound[:4], ["python3", "-I", "-B", "-c"])
        self.assertEqual(bound[5:], command)

    def test_authority_hash_alias_and_shared_write_fail_closed(self):
        path = self.root / "authority.json"
        value = {"independent_root": str(self.root), "protected_roots": ["/synthetic/protected"]}
        path.write_text(json.dumps(value))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(self.remote.read_authority(path, digest), value)
        with self.assertRaisesRegex(RuntimeError, "digest"):
            self.remote.read_authority(path, "0" * 64)
        original_bytes = path.read_bytes()
        path.write_bytes(original_bytes + b"\n")
        with self.assertRaisesRegex(RuntimeError, "digest"):
            self.remote.read_authority(path, digest)
        path.write_bytes(original_bytes)
        alias = self.root / "alias.json"
        alias.symlink_to(path)
        with self.assertRaises(ValueError):
            self.remote.read_authority(alias, digest)
        path.chmod(0o666)
        with self.assertRaisesRegex(RuntimeError, "non-shared-writable"):
            self.remote.read_authority(path, digest)

    def test_uuid_alias_cannot_create_second_operation(self):
        directory = self.create()
        with self.assertRaises(FileExistsError):
            self.remote.create_operation(self.root, uuid.UUID(self.window).hex,
                                         uuid.UUID(self.operation).hex,
                                         ["inert-test-command"], str(self.root), 1,
                                         uuid.UUID(self.epoch).hex)
        self.assertEqual(directory, self.remote.operation_dir(
            self.root, uuid.UUID(self.window).hex, uuid.UUID(self.operation).hex))

    def test_relabelled_manifest_cannot_cross_window_even_with_matching_hash(self):
        directory = self.create()
        path = directory / "manifest.json"
        value = json.loads(path.read_text())
        value["window"] = uuid.uuid4().hex
        raw = json.dumps(value).encode()
        path.write_bytes(raw)
        (directory / "manifest.sha256").write_text(hashlib.sha256(raw).hexdigest())
        with self.assertRaisesRegex(RuntimeError, "ownership"):
            self.remote.read_manifest(directory)

    def test_terminal_status_preserves_stdout_and_stderr_separately(self):
        directory = self.create()
        ops = ChildOps(directory)
        def write_child_output():
            (directory / "output.log").write_text("normal output\n")
            (directory / "error.log").write_text("diagnostic output\n")
        ops.hook = write_child_output
        self.remote.supervise(directory, ops)
        result = self.status()
        self.assertEqual(result["stdout"], "normal output\n")
        self.assertEqual(result["stderr"], "diagnostic output\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
