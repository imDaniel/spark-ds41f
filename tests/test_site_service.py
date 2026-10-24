"""Offline faults for the host service identity boundary; no Docker/GPU operations."""

import builtins
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import runpy
import tempfile
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
BACKEND = runpy.run_path(str(ROOT / "host/site/deepseek-model-control.py"))


class Capture:
    def __init__(self):
        self.codes = {}

    def run(self, node, args, timeout):
        self.codes[node] = args[-1]
        return json.dumps(dict(present=False, running=False, valid=True, gpu_pids=[], errors=[]))


class SiteServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "cluster.json").write_text("{}")
        (self.root / "profile").write_bytes(b"profile")
        sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
        self.policy = dict(repository=str(self.root), cluster_config="cluster.json", config_sha256=sha(self.root / "cluster.json"), profile_path=str(self.root / "profile"), profile_sha256=sha(self.root / "profile"), revision="fixed-head", repository_files={"cluster.json": sha(self.root / "cluster.json")}, container="fixed", image_id="sha256:qualified", container_profile_path="/steering/profile", operator_version="operator", mounts={"/steering": {"source": str(self.root), "rw": False}})
        command = ["/models", "--additional-config", json.dumps({"ds41_steering": {"profile_path": "/steering/profile", "profile_sha256": self.policy["profile_sha256"], "operator_version": "operator"}})]
        self.policy["ranks"] = {str(n): dict(command=command, environment={"fixed": "1"}) for n in range(1, 5)}
        self.container = dict(Id="id", Image="sha256:qualified", State=dict(Running=True, OOMKilled=False, ExitCode=0), Config=dict(Cmd=command, Env=["fixed=1"]), Mounts=[dict(Destination="/steering", Source=str(self.root), RW=False)])

    def check(self, mutate=None, guard="active", missing=False):
        capture = Capture()
        BACKEND["fleet"](self.policy, capture)
        c = copy.deepcopy(self.container)
        if mutate:
            mutate(c)

        def run(args, **kwargs):
            docker = args[0] == "/usr/bin/docker"
            return types.SimpleNamespace(returncode=int(missing and docker), stdout=json.dumps([c]) if docker else guard)

        def output(args, **kwargs):
            if args[0] == "git":
                return "fixed-head\n" if "rev-parse" in args else ""
            return "PID\n11\n" if "top" in args else "11\n"

        real_import = builtins.__import__
        fake = types.SimpleNamespace(run=run, check_output=output)

        def importing(name, *args, **kwargs):
            return fake if name == "subprocess" else real_import(name, *args, **kwargs)

        namespace = {"__builtins__": dict(vars(builtins), __import__=importing)}
        with contextlib.redirect_stdout(io.StringIO()) as out:
            try:
                exec(capture.codes[1], namespace)
            except SystemExit as e:
                self.assertEqual(e.code, 0)
        return json.loads(out.getvalue())

    def test_qualified_fleet_recognizes_its_gpu_process(self):
        row = self.check()
        self.assertTrue(row["valid"])
        self.assertEqual(row["gpu_pids"], [11])

    def test_runtime_identity_faults_fail_closed(self):
        mutations = [lambda c: c.update(Image="other"), lambda c: c["Config"].update(Cmd=["other"]), lambda c: c["Config"].update(Env=["fixed=2"]), lambda c: c.update(Mounts=[]), lambda c: c["Mounts"][0].update(RW=True)]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.assertFalse(self.check(mutate)["valid"])
        self.assertFalse(self.check(guard="inactive")["valid"])

    def test_profile_and_repository_drift_fail_closed(self):
        (self.root / "profile").write_bytes(b"wrong profile")
        self.assertFalse(self.check()["valid"])
        (self.root / "profile").write_bytes(b"profile")
        (self.root / "cluster.json").write_text("changed")
        self.assertFalse(self.check()["valid"])

    def test_stopped_and_absent_containers_can_be_started_again(self):
        row = self.check(lambda c: c["State"].update(Running=False), guard="inactive")
        self.assertTrue(row["valid"])
        self.assertFalse(row["running"])
        self.assertTrue(self.check(missing=True, guard="inactive")["valid"])

    def test_start_uses_qualified_root_profile_and_coordinated_command(self):
        transition = BACKEND["transition"]
        calls = []
        policy = dict(repository=str(self.root), cluster_config="config/cluster.json")
        ds = types.SimpleNamespace(power=types.SimpleNamespace(utc=lambda: "now", ENV={}))
        executor = types.SimpleNamespace(run=lambda *a, **k: "")
        process = types.SimpleNamespace(poll=lambda: 0, returncode=0)
        fake = types.SimpleNamespace(
            run=lambda args, **kw: types.SimpleNamespace(stdout="live: no engine\nshape:   new engine\n"),
            Popen=lambda args, **kw: calls.append(args) or process,
        )
        initial = dict(errors={}, running=False, ready=False)
        final = dict(errors={}, running=True, ready=True)
        with mock.patch.dict(transition.__globals__, HOLD=self.root / "hold", subprocess=fake, observe=mock.Mock(side_effect=[initial, final])), mock.patch("signal.signal"), contextlib.redirect_stdout(io.StringIO()):
            transition(policy, ds, executor, "start")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1:], ["--shape", "tp4-ring", "--cluster-config", "config/cluster.json", "cluster", "start", "--replace", "--apply"])
        self.assertFalse((self.root / "hold").exists())

    def test_shape_change_refuses_before_any_launch(self):
        transition = BACKEND["transition"]
        fake = types.SimpleNamespace(run=lambda args, **kw: types.SimpleNamespace(stdout="live: stopped\nshape:   CHANGES nodes\n"), Popen=mock.Mock())
        ds = types.SimpleNamespace(power=types.SimpleNamespace(utc=lambda: "now", ENV={}))
        with mock.patch.dict(transition.__globals__, HOLD=self.root / "hold", subprocess=fake, observe=lambda *a: dict(errors={}, running=False, ready=False)), mock.patch("signal.signal"), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "different runtime shape"):
                transition(dict(repository=str(self.root), cluster_config="config/cluster.json"), ds, object(), "start")
        fake.Popen.assert_not_called()
        self.assertFalse((self.root / "hold").exists())


if __name__ == "__main__":
    unittest.main()
