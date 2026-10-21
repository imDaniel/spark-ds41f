"""CPU-only adapter contracts; no original service module is loaded or run."""
from __future__ import annotations

import hashlib
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
import uuid
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load_adapter():
    root = Path(os.environ.get("Q03_WINDOW_ADAPTER_ROOT", ROOT))
    path = root / "scripts/q03_original_spark_adapter.py"
    spec = importlib.util.spec_from_file_location("q03_adapter_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AdapterContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.adapter = load_adapter()

    def test_transition_uses_exact_registered_flags(self):
        policy = {"cluster_config": "registered-pending.local.json"}
        prefix = ["--shape", "tp4-ring", "--cluster-config",
                  "registered-pending.local.json", "cluster"]
        self.assertEqual(self.adapter.transition_args(policy, "stop", True),
                         prefix + ["stop", "--apply"])
        self.assertEqual(self.adapter.transition_args(policy, "start", True),
                         prefix + ["start", "--replace", "--unqualified-shape", "--apply"])
        self.assertEqual(self.adapter.transition_args(policy, "start", False),
                         prefix + ["start", "--replace", "--unqualified-shape"])
        with self.assertRaises(RuntimeError):
            self.adapter.transition_args(policy, "restart", True)

    def test_no_started_nodes_produce_no_logs_and_return_none(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = SimpleNamespace(run_ssh=mock.Mock(), remote_shell=mock.Mock())
            writer = self.adapter.redirected_log_writer(original, root / "evidence")
            self.assertIsNone(writer({}, {}, [], "registered"))
            self.assertEqual(list(root.iterdir()), [])
            original.run_ssh.assert_not_called()
            original.remote_shell.assert_not_called()

    def test_failed_start_logs_remain_external_and_return_none_for_rollback(self):
        for exit_code in (0, 1):
            with self.subTest(exit_code=exit_code), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                result = SimpleNamespace(returncode=exit_code, stdout="output", stderr="error")
                original = SimpleNamespace(run_ssh=mock.Mock(return_value=result),
                                           remote_shell=mock.Mock(return_value=result))
                writer = self.adapter.redirected_log_writer(original, root)
                with contextlib.redirect_stdout(io.StringIO()) as stdout:
                    self.assertIsNone(writer({"container": {"name": "original"}}, {},
                                             [{"name": "node/unsafe"}], "registered"))
                receipt = json.loads(stdout.getvalue())
                target = Path(receipt["redirected_failure_evidence"])
                self.assertTrue(target.is_relative_to(root))
                logs = list(target.glob("*.log"))
                self.assertEqual(len(logs), 1)
                self.assertIn("inspect_returncode: " + str(exit_code), logs[0].read_text())
                self.assertEqual(logs[0].name, "0-node_unsafe.log")

    def test_log_capture_or_write_errors_never_interrupt_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = SimpleNamespace(run_ssh=mock.Mock(side_effect=OSError("probe failed")),
                                       remote_shell=mock.Mock())
            writer = self.adapter.redirected_log_writer(original, root)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertIsNone(writer({"container": {"name": "original"}}, {},
                                         [{"name": "node"}], "registered"))
            self.assertIn("probe failed", next(root.glob("*/*.log")).read_text())
            with mock.patch.object(Path, "mkdir", side_effect=OSError("disk full")):
                with contextlib.redirect_stderr(io.StringIO()) as stderr:
                    self.assertIsNone(writer({"container": {"name": "original"}}, {},
                                             [{"name": "node"}], "registered"))
            self.assertIn("rollback continues", stderr.getvalue())

    def test_evidence_rejects_protected_or_symlinked_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            independent, protected = root / "independent", root / "protected"
            independent.mkdir()
            protected.mkdir()
            manifest = {"independent_root": str(independent), "preflight": {
                "protected_roots": [str(protected)],
                **{key: str(protected / key) for key in ("hold_path", "request_path",
                   "legacy_hold_path", "legacy_request_path", "policy_path")},
                "registered_policy": {"repository": str(protected / "repository")}}}
            for path in ("relative", "/", root, protected / "logs", independent):
                with self.subTest(path=path), self.assertRaises(RuntimeError):
                    self.adapter.evidence_path(path, manifest)
            alias = independent / "alias"
            alias.symlink_to(protected)
            with self.assertRaises(RuntimeError):
                self.adapter.evidence_path(alias / "logs", manifest)
            self.assertEqual(self.adapter.evidence_path(independent / "evidence", manifest),
                             independent / "evidence")

    def test_hash_mismatch_rejects_bytes_before_import(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.write_bytes(b"not executable source")
            for expected in ("not-a-hash", "a" * 64):
                with self.subTest(expected=expected), self.assertRaises(RuntimeError):
                    self.adapter.verify_hash(source, expected)

    def test_wrong_policy_or_spark_expectations_never_load_module(self):
        args = SimpleNamespace(evidence_dir="unused", hold="unused", window_id="window",
                               policy="/synthetic/policy.json",
                               policy_sha256="c" * 64, spark_sha256="d" * 64)
        capsule = {"window_id": "window", "holder": "synthetic-holder", "manifest": {
            "preflight": {"hold_path": "unused", "policy_path": args.policy,
                          "policy_sha256": args.policy_sha256, "spark_sha256": args.spark_sha256}}}
        for field, value in (("policy", "/wrong/policy.json"),
                             ("policy_sha256", "a" * 64), ("spark_sha256", "b" * 64)):
            changed = SimpleNamespace(**vars(args))
            setattr(changed, field, value)
            with (self.subTest(field=field), mock.patch.dict(os.environ),
                  mock.patch.object(sys, "dont_write_bytecode", sys.dont_write_bytecode),
                  mock.patch.object(self.adapter, "read_capsule", return_value=capsule),
                  mock.patch.object(self.adapter, "evidence_path", return_value=Path("/fake")),
                  mock.patch.object(self.adapter, "owned_hold", return_value={}),
                  mock.patch.object(self.adapter, "verify_hash") as verify,
                  mock.patch.object(self.adapter.importlib.machinery, "SourceFileLoader") as loader):
                with self.assertRaises(RuntimeError):
                    self.adapter.run(changed)
                loader.assert_not_called()
                verify.assert_not_called()

    def test_hold_requires_exact_uuid_holder_and_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hold.json"
            record = {"window": "window", "holder": "q03-isolated:window"}
            path.write_text(json.dumps(record))
            with contextlib.nullcontext():
                self.assertEqual(self.adapter.owned_hold(path, "window", path, "q03-isolated:window"), record)
                for changed in (dict(record, window="foreign"), dict(record, holder="foreign")):
                    path.write_text(json.dumps(changed))
                    with self.assertRaises(RuntimeError):
                        self.adapter.owned_hold(path, "window", path, "q03-isolated:window")
                with self.assertRaises(RuntimeError):
                    self.adapter.owned_hold(path, "../window", path, "q03-isolated:window")
                target = Path(directory) / "target.json"
                path.rename(target)
                path.symlink_to(target)
                with self.assertRaises(OSError):
                    self.adapter.owned_hold(path, "window", path, "q03-isolated:window")

    def test_hold_inode_replacement_is_rejected_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hold.json"
            path.write_text('{"window":"window","holder":"q03-isolated:window"}')
            old = Path(directory) / "old.json"
            replacement = '{"window":"foreign","holder":"foreign"}'
            lstat = Path.lstat
            def replace_then_stat(current):
                if current == path:
                    path.rename(old)
                    path.write_text(replacement)
                return lstat(current)
            with mock.patch.object(Path, "lstat", replace_then_stat):
                with self.assertRaisesRegex(RuntimeError, "inode"):
                    self.adapter.owned_hold(path, "window", path, "q03-isolated:window")
            self.assertEqual(path.read_text(), replacement)

    def test_readonly_hooks_preserve_commands_and_unrelated_guards(self):
        ssh, shell, guard = mock.Mock(return_value="ssh"), mock.Mock(return_value="shell"), object()
        original = SimpleNamespace(run_ssh=ssh, remote_shell=shell, guard=guard)
        self.adapter.install_readonly_hooks(original, Path("/fake/evidence"))
        self.assertEqual(original.run_ssh("nodes", "node", "git", "status"), "ssh")
        ssh.assert_called_once_with("nodes", "node", "env", "GIT_OPTIONAL_LOCKS=0",
                                    "PYTHONDONTWRITEBYTECODE=1", "git", "status")
        self.assertEqual(original.remote_shell("nodes", "node", "guard && command"), "shell")
        shell.assert_called_once_with(
            "nodes", "node", "export GIT_OPTIONAL_LOCKS=0 PYTHONDONTWRITEBYTECODE=1; guard && command")
        self.assertIs(original.guard, guard)
        self.assertTrue(callable(original.preserve_failed_start_logs))


class RemoteAdapterContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.adapter = load_adapter()
        root = Path(os.environ.get("Q03_WINDOW_ADAPTER_ROOT", ROOT))
        path = root / "scripts/q03_remote_operation.py"
        spec = importlib.util.spec_from_file_location("q03_remote_operation", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.nodes = {"nodes": [{"name": f"node0{i}"} for i in range(1, 5)]}
        self.cluster = {"container": {"name": "registered", "image": "sha256:" + "a" * 64},
                        "deployment": {"ready_url": "http://127.0.0.1:8000/health"}}
        self.window, self.epoch = uuid.uuid4().hex, uuid.uuid4().hex
        self.helper_digest = hashlib.sha256(Path(self.adapter.__file__).with_name(
            "q03_remote_operation.py").read_bytes()).hexdigest()
        self.capsule = {
            "manifest": {"independent_root": str(self.root),
                         "preflight": {"remote_helper_path": str(self.root / "helper.py"),
                                       "remote_helper_sha256": self.helper_digest,
                                       "remote_authority_path": str(self.root / "authority.json"),
                                       "remote_authority_sha256": "c" * 64}},
            "recovery_source_sha256": {"q03_remote_operation.py": self.helper_digest},
            "recovery_timeout_s": 60,
        }
        self.calls = []
        self.unsafe_node = None
        self.hash_bad = False
        self.code = 0
        self.remote = self.adapter.RemoteOperations(
            SimpleNamespace(), self.cluster, self.nodes, self.capsule,
            self.window, self.epoch, self.root / "evidence", transport=self.transport)

    def transport(self, nodes, node, *argv):
        self.calls.append((node["name"], argv))
        if argv[4] == self.remote.helper_module.REMOTE_ENV_EXEC:
            return SimpleNamespace(returncode=0, stdout="read-only", stderr="")
        action = argv[argv.index("-c") + 4]
        value = {"window": self.window, "epoch": self.epoch}
        if action == "verify":
            value.update(helper_sha256="bad" if self.hash_bad else self.helper_digest,
                         authority_sha256="c" * 64)
        elif action == "seal":
            value.update(sealed=True, safe_to_overlap=node["name"] != self.unsafe_node)
        elif action == "launch":
            value.update(operation=argv[argv.index("--operation") + 1], status="terminal",
                         exit_code=self.code, stdout="original stdout", stderr="original stderr",
                         action_succeeded=self.code == 0)
        else:
            raise AssertionError("unexpected transport command")
        return SimpleNamespace(returncode=0, stdout=json.dumps(value), stderr="")

    def test_sibling_helper_loads_without_import_path_or_module_cache(self):
        sibling = str(Path(self.adapter.__file__).parent)
        with (mock.patch.dict(sys.modules), mock.patch.object(sys, "path", [
                path for path in sys.path if path != sibling])):
            sys.modules.pop("q03_remote_operation", None)
            remote = self.adapter.RemoteOperations(
                SimpleNamespace(), self.cluster, self.nodes, self.capsule,
                self.window, self.epoch, self.root / "isolated", transport=self.transport)
            remote.verify_helpers()
            self.assertNotIn("q03_remote_operation", sys.modules)
        self.assertEqual(len(self.calls), 4)
        with self.assertRaises(RuntimeError):
            self.adapter.load_remote_helper("0" * 64)

    def test_seal_visits_every_node_even_after_an_unsafe_result(self):
        self.unsafe_node = "node01"
        with self.assertRaisesRegex(RuntimeError, "uncertain"):
            self.remote.quiesce()
        self.assertEqual([name for name, _ in self.calls],
                         [node["name"] for node in self.nodes["nodes"]])
        count = len(self.calls)
        with self.assertRaisesRegex(RuntimeError, "prior remote operation"):
            self.remote.run_ssh(self.nodes, self.nodes["nodes"][0],
                                "docker", "start", "registered")
        self.assertEqual(len(self.calls), count)

    def test_terminal_command_preserves_results_and_blocks_after_failure(self):
        node = self.nodes["nodes"][0]
        result = self.remote.run_ssh(self.nodes, node, "docker", "stop", "registered")
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (0, "original stdout", "original stderr"))
        self.code = 1
        failed = self.remote.run_ssh(self.nodes, node, "docker", "stop", "registered")
        self.assertEqual(failed.returncode, 1)
        count = len(self.calls)
        with self.assertRaisesRegex(RuntimeError, "prior remote operation"):
            self.remote.run_ssh(self.nodes, node, "docker", "start", "registered")
        self.assertEqual(len(self.calls), count)

    def test_unknown_shell_is_supervised_not_a_readonly_bypass(self):
        node = self.nodes["nodes"][0]
        for command in (("bash", "-lc", "docker stop registered"),
                        ("docker", "inspect", "foreign-container")):
            self.assertFalse(self.adapter.readonly_probe(command, self.cluster))
            self.remote.run_ssh(self.nodes, node, *command)
            self.assertEqual(self.calls[-1][1][self.calls[-1][1].index("-c") + 4], "launch")
        self.remote.run_ssh(self.nodes, node, "docker", "inspect", "registered")
        self.assertEqual(self.calls[-1][1][:4], ("python3", "-I", "-B", "-c"))
        self.assertEqual(self.calls[-1][1][5:], ("docker", "inspect", "registered"))

    def test_helper_hash_mismatch_fails_before_any_launch(self):
        self.hash_bad = True
        with self.assertRaisesRegex(RuntimeError, "identity"):
            self.remote.verify_helpers()
        self.assertTrue(self.calls)
        self.assertTrue(all(argv[argv.index("-c") + 4] == "verify" for _, argv in self.calls))
        self.calls.clear()
        self.hash_bad = False
        self.remote.verify_helpers()
        self.assertEqual(len(self.calls), 4)

    def test_receipt_wrong_window_refuses_further_mutations(self):
        node = self.nodes["nodes"][0]
        self.remote.transport = lambda *args: SimpleNamespace(
            returncode=0, stdout=json.dumps({"window": "foreign"}), stderr="")
        with self.assertRaisesRegex(RuntimeError, "window mismatch"):
            self.remote.run_ssh(self.nodes, node, "docker", "stop", "registered")
        self.assertTrue(self.remote.blocked)

    def test_helper_outside_independent_or_capsule_hash_mismatch_refused(self):
        for path, digest in (("/synthetic/protected/helper.py", "b" * 64),
                             (str(self.root / "helper.py"), "c" * 64)):
            capsule = json.loads(json.dumps(self.capsule))
            capsule["manifest"]["preflight"].update(remote_helper_path=path,
                                                   remote_helper_sha256=digest)
            with self.subTest(path=path, digest=digest), self.assertRaises(RuntimeError):
                self.adapter.RemoteOperations(SimpleNamespace(), self.cluster, self.nodes,
                                              capsule, self.window, self.epoch,
                                              self.root / "evidence", transport=self.transport)
        self.assertEqual(self.calls, [])

    def test_running_sealed_operations_may_finish_within_fake_deadline(self):
        clock, calls = [0.0], []
        self.remote.now = lambda: clock[0]
        self.remote.sleep = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
        def helper(node, action, *args):
            calls.append(node["name"])
            safe = clock[0] >= .2
            return {"sealed": True, "safe_to_overlap": safe,
                    "operations": [{"status": "terminal" if safe else "running",
                                    "safe_to_overlap": safe}]}
        self.remote.helper_call = helper
        result = self.remote.quiesce()
        self.assertTrue(all(item["safe_to_overlap"] for item in result))
        self.assertEqual(calls[:4], [node["name"] for node in self.nodes["nodes"]])
        self.assertLess(clock[0], 60)
        self.assertTrue(self.remote.blocked)
        self.assertEqual(self.remote.rpc_timeout, 30)

    def test_running_operation_deadline_never_becomes_safe_by_timeout(self):
        clock = [0.0]
        self.remote.now = lambda: clock[0]
        self.remote.sleep = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
        self.remote.helper_call = lambda *args: {
            "sealed": True, "safe_to_overlap": False, "operations": [{"status": "running"}]}
        with self.assertRaisesRegex(RuntimeError, "bounded wait"):
            self.remote.quiesce()
        self.assertAlmostEqual(clock[0], 60)
        self.assertTrue(self.remote.blocked)
        self.assertEqual(self.remote.rpc_timeout, 30)


if __name__ == "__main__":
    unittest.main(verbosity=2)
