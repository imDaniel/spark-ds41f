"""The fixed site's power path follows the qualified four-rank deployment."""
import copy
from pathlib import Path
import runpy
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
POWER = runpy.run_path(str(ROOT / "host/site/cluster-shutdown.py"))


class Fleet:
    def __init__(self):
        self.calls = []
        self.rows = {n: dict(hostname=POWER["EXPECTED_HOSTS"][n],
                            admin_poweroff_authorized=True,
                            command=["--nnodes", "4", "--tensor-parallel-size", "4"],
                            state=dict(Running=True), memguard="active") for n in POWER["NODES"]}
        self.plan = "\n".join(f"spark{n:02}: stop exact container ds41f-1126-attention"
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
    def test_dry_plan_checks_every_rank_without_stopping(self):
        fleet = Fleet()
        result = POWER["plan"](fleet)
        self.assertTrue(result["passed"])
        self.assertEqual(set(result["nodes"]), {1, 2, 3, 4})
        self.assertEqual(fleet.calls, [(1, [
            "/srv/ai-cluster/runtimes/1126-attention/source/spark-ds41f/bin/spark",
            "--cluster-config", "config/cluster.json", "cluster", "stop", "--parallel"])])

    def test_wrong_rank_or_incomplete_plan_refuses(self):
        for fault in ("host", "tp", "permission", "missing", "refused"):
            fleet = Fleet()
            if fault == "host":
                fleet.rows[4]["hostname"] = "other"
            elif fault == "tp":
                fleet.rows[4]["command"][-1] = "3"
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
        executor = POWER["Executor"]()
        with mock.patch.object(executor, "run", return_value="{}") as run:
            executor.snapshot(3)
        script = run.call_args.args[1][-1]
        self.assertIn("'ds41f-1126-attention'", script)
        self.assertIn("'ds41f-1126-attention-memguard.service'", script)
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
