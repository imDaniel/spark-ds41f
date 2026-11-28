"""The fixed site's power path follows the qualified four-rank deployment."""
import copy
from pathlib import Path
import runpy
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
POWER = runpy.run_path(str(ROOT / "host/site/cluster-shutdown.py"))


SERVICE = {"repository": "/srv/ai-cluster/runtimes/1126-attention-fixes/source/spark-ds41f",
           "container": "ds41f-1126-attention-fixes", "image_id": "sha256:qualified"}


class Fleet:
    def __init__(self):
        self.calls = []
        self.repository = Path(SERVICE["repository"])
        self.container = SERVICE["container"]
        self.image = SERVICE["image_id"]
        self.rows = {n: dict(hostname=POWER["EXPECTED_HOSTS"][n],
                            admin_poweroff_authorized=True,
                            command=["--nnodes", "4", "--tensor-parallel-size", "4"],
                            state=dict(Running=True), image=self.image, memguard="active") for n in POWER["NODES"]}
        self.plan = "\n".join(f"spark{n:02}: stop exact container {self.container} and stop its memguard"
                              for n in POWER["NODES"])

    def snapshot(self, node):
        return copy.deepcopy(self.rows[node])

    def run(self, node, args, **kwargs):
        self.calls.append((node, args))
        if "--apply" in args:
            for row in self.rows.values():
                row["state"]["Running"] = False
                row["memguard"] = "inactive"
        return self.plan


class SiteShutdownTests(unittest.TestCase):
    def test_shared_site_clients_can_construct_their_executor_without_arguments(self):
        executor = POWER["Executor"]
        with mock.patch.dict(executor.__init__.__globals__, validate_installation=lambda: SERVICE):
            selected = executor()
        self.assertEqual(selected.repository, Path(SERVICE["repository"]))
        self.assertEqual(selected.container, SERVICE["container"])
        self.assertEqual(selected.image, SERVICE["image_id"])

    def test_dry_plan_checks_every_rank_without_stopping(self):
        fleet = Fleet()
        result = POWER["plan"](fleet)
        self.assertTrue(result["passed"])
        self.assertEqual(set(result["nodes"]), {1, 2, 3, 4})
        self.assertEqual(fleet.calls, [(1, [
            str(fleet.repository / "bin/spark"),
            "--cluster-config", "config/cluster.json", "cluster", "stop", "--parallel"])])

    def test_wrong_rank_or_incomplete_plan_refuses(self):
        for fault in ("host", "tp", "permission", "missing", "other_container", "image", "refused"):
            fleet = Fleet()
            if fault == "host":
                fleet.rows[4]["hostname"] = "other"
            elif fault == "tp":
                fleet.rows[4]["command"][-1] = "3"
            elif fault == "other_container":
                fleet.plan = fleet.plan.replace(fleet.container, fleet.container + "-unselected")
            elif fault == "image":
                fleet.rows[4]["image"] = "previous-image"
            elif fault == "permission":
                fleet.rows[4]["admin_poweroff_authorized"] = False
            elif fault == "missing":
                fleet.plan = fleet.plan.replace("spark04:", "other:")
            else:
                fleet.plan += "\n--apply would refuse: runtime drift"
            with self.subTest(fault=fault), self.assertRaises(RuntimeError):
                POWER["plan"](fleet)
            self.assertFalse(any("--apply" in args for _, args in fleet.calls))

    def test_remote_snapshot_uses_current_container_and_guard(self):
        executor = POWER["Executor"](SERVICE)
        with mock.patch.object(executor, "run", return_value="{}") as run:
            executor.snapshot(3)
        script, container = run.call_args.args[1][-2:]
        self.assertEqual(container, SERVICE["container"])
        self.assertIn("'inspect',name", script)
        self.assertIn("name+'-memguard.service'", script)
        self.assertNotIn("ds41f-1022", script)

    def test_guard_or_running_rank_blocks_all_power_timers(self):
        for field, value in (("memguard", "active"), ("state", dict(Running=True))):
            fleet = Fleet()
            original_run = fleet.run
            def run(*args, **kwargs):
                text = original_run(*args, **kwargs)
                fleet.rows[4][field] = value
                return text
            fleet.run = run
            fleet.queue = mock.Mock()
            fleet.cancel = mock.Mock()
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                POWER["execute"](fleet, {})
            fleet.queue.assert_not_called()

    def test_lost_worker_dispatch_cancels_all_attempted_timers(self):
        fleet = Fleet()
        fleet.queue = mock.Mock(side_effect=lambda n, *a: (_ for _ in ()).throw(
            RuntimeError("lost SSH reply")) if n == 3 else None)
        fleet.cancel = mock.Mock()
        report = {}
        with self.assertRaisesRegex(RuntimeError, "lost SSH reply"):
            POWER["execute"](fleet, report)
        self.assertEqual({call.args[0] for call in fleet.cancel.call_args_list}, {2, 3, 4})
        self.assertFalse(any(call.args[0] == 1 for call in fleet.queue.call_args_list))
        self.assertFalse(report["passed"])


if __name__ == "__main__":
    unittest.main()
