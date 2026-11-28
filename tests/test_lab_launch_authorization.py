"""A disabled serving recipe must not displace the running engine."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from spark_ds41f import lab


class LabLaunchAuthorizationTest(unittest.TestCase):
    experiment = "experiments/2026-11-28-1126-attention-fixes-rc2"
    config = f"{experiment}/cluster.json"

    def setUp(self):
        cluster = lab.spark.read_cluster(self.config)
        nodes = {"nodes": [{"name": "head", "head": True}]}
        self.enterContext(mock.patch.object(lab.spark, "configuration", return_value=(cluster, nodes, {})))
        self.enterContext(mock.patch.object(lab.spark, "local_doctor", return_value=[]))
        self.enterContext(mock.patch.object(lab, "nodes_config", return_value=nodes))
        self.output = self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.spec = {"run": "disabled", "experiment": self.experiment, "jobs": [{
            "kind": "measure", "bench": False, "extras": [],
            "arms": [{"config": "cluster.json", "label": "arm", "memory_snapshot": True}],
        }]}

    def test_boot_refuses_before_inspecting_or_stopping_other_engines(self):
        with mock.patch.object(lab.spark, "run_ssh", side_effect=AssertionError("inspected a node")), \
                mock.patch.object(lab, "spark_cli", side_effect=AssertionError("issued a cluster command")):
            self.assertFalse(lab.boot(self.config))
        self.assertIn("not authorized for launch", self.output.getvalue())

    def test_only_boot_requires_a_launch_enabled_recipe(self):
        for kind in ("image", "fabric"):
            with self.subTest(kind=kind):
                self.assertEqual(lab.plan_problems([{"kind": kind, "config": self.config}]), [])
        problems = lab.plan_problems([{"kind": "boot", "config": self.config}])
        self.assertTrue(any("not authorized for launch" in p for p in problems), problems)

    def test_queue_rejects_disabled_arm_even_if_a_sync_is_scheduled_after_it(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(lab, "QUEUE", Path(tmp) / "queue"):
            path = Path(tmp) / "job.json"
            for trailing in ([], [{"kind": "sync"}]):
                with self.subTest(jobs=trailing):
                    path.write_text(json.dumps(dict(self.spec, jobs=self.spec["jobs"] + trailing)))
                    with self.assertRaisesRegex(SystemExit, "not authorized for launch"):
                        lab.queue_add(str(path))
            self.assertEqual(lab.queued(), [])

    def test_runner_refuses_before_touching_a_new_or_existing_window(self):
        # No window access means no acquisition, restore, stop, compact or sampling.
        self.spec["jobs"].append({"kind": "sync"})
        with mock.patch.object(lab, "read_hold", side_effect=AssertionError("touched window")):
            self.assertEqual(lab.execute(self.spec, dry=False, keep_open=False), 1)
        self.assertIn("not authorized for launch", self.output.getvalue())

    def test_checked_out_policy_after_a_sync_precedes_a_memory_snapshot(self):
        arm = self.config
        steps = [{"kind": "gpu_clocks", "lock": None}, {"kind": "sync"},
                 {"kind": "boot", "config": arm, "label": "arm", "memory_out": "unused"}]
        # Do not validate the future revision against the recipe on the old one.
        cluster = lab.spark.read_cluster(arm)
        cluster["deployment"]["launch_enabled"] = True
        self.enterContext(mock.patch.object(lab.spark, "configuration", return_value=(cluster, {}, {})))

        def sync():
            cluster["deployment"]["launch_enabled"] = False
            return True

        self.enterContext(mock.patch.object(lab, "plan", return_value=steps))
        self.enterContext(mock.patch.object(lab, "read_hold", return_value={}))
        self.enterContext(mock.patch.object(lab, "hold_is_ours", return_value=True))
        self.enterContext(mock.patch.object(lab, "window_should_close", return_value=None))
        self.enterContext(mock.patch.object(lab, "beat"))
        self.enterContext(mock.patch.object(lab, "set_gpu_clocks", return_value=True))
        synced = self.enterContext(mock.patch.object(lab, "sync_checkouts", side_effect=sync))
        self.enterContext(mock.patch.object(lab, "checkout_commit", return_value=lab.COMMIT_AT_START))
        stopped = self.enterContext(mock.patch.object(lab, "stop_cluster", side_effect=AssertionError("stopped")))
        snapshot = self.enterContext(mock.patch.object(lab, "memory_before_boot", side_effect=AssertionError("sampled")))
        closed = self.enterContext(mock.patch.object(lab, "window_close"))
        self.assertEqual(lab.execute(self.spec, dry=False, keep_open=True, close_on_failure=False), 1)
        synced.assert_called_once()
        stopped.assert_not_called()
        snapshot.assert_not_called()
        closed.assert_not_called()
        self.assertIn("not authorized for launch", self.output.getvalue())


if __name__ == "__main__":
    unittest.main()
