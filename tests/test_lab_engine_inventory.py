"""Lab stops and boots require a complete inventory of the serving group."""

import contextlib
import io
import subprocess
import unittest
from unittest.mock import patch

from spark_ds41f import lab


class LabEngineInventoryTest(unittest.TestCase):
    nodes = {"nodes": [{"name": "dgx1", "head": True}, {"name": "dgx2", "head": False}]}
    cluster = {"container": {"name": "ds41f-1126-attention"}, "deployment": {"launch_enabled": True}}
    config = "config/cluster.json"
    peer = "ds41f-1126-attention-fixes\tother.json\n"

    def setUp(self):
        self.output = self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(patch.object(lab, "PRODUCTION_CONFIG", self.config))
        self.configurations = {self.config: (self.cluster, self.nodes, {}),
                               "other.json": ({"container": {"name": "ds41f-1126-attention-fixes"}}, self.nodes, {})}
        self.resolve = self.enterContext(patch.object(lab.spark, "configuration",
                                                      side_effect=lambda args: self.configurations[args.cluster_config]))
        self.enterContext(patch.object(lab, "nodes_config", return_value=self.nodes))
        self.enterContext(patch.object(lab.spark, "read_cluster", return_value=self.cluster))
        self.enterContext(patch.object(lab, "probe_boot", return_value={}))
        self.cli = self.enterContext(patch.object(lab, "spark_cli", return_value=0))
        self.ssh = self.enterContext(patch.object(lab.spark, "run_ssh"))

    def listings(self, first, second, returncode=0):
        self.ssh.side_effect = [
            subprocess.CompletedProcess([], 0, first, ""),
            subprocess.CompletedProcess([], returncode, second, "unavailable" if returncode else ""),
        ]

    def test_failed_rank_inventory_cannot_stop_or_start_any_engine(self):
        for label, operation in (("boot", lambda: lab.boot(self.config)),
                                 ("stop", lambda: lab.stop_cluster(remove=True))):
            with self.subTest(operation=label):
                self.listings(self.peer, self.peer, returncode=255)
                self.assertFalse(operation())
                self.cli.assert_not_called()
                self.assertIn("dgx2", self.output.getvalue())
                self.assertIn("unavailable", self.output.getvalue())

    def test_unlabelled_peer_leaves_the_current_engine_running(self):
        self.listings(self.peer, "dsv41-other\t\n")
        self.assertFalse(lab.stop_cluster())
        self.cli.assert_not_called()

    def test_stop_waits_for_both_listings_and_preserves_profile_order(self):
        def stop(*_args, **_kwargs):
            self.assertEqual(self.ssh.call_count, 2)
            return 0

        self.cli.side_effect = stop
        self.listings(self.peer, self.peer)
        self.assertTrue(lab.stop_cluster(remove=True))
        self.assertEqual([call.args[0].cluster_config for call in self.resolve.call_args_list], ["other.json"])
        self.assertEqual([call.args for call in self.cli.call_args_list], [
            ("--cluster-config", profile, "cluster", "stop", "--remove", "--apply", "--parallel")
            for profile in (self.config, "other.json")
        ])

    def test_empty_inventory_allows_the_requested_boot(self):
        self.listings("", "")
        self.assertTrue(lab.boot(self.config))
        self.assertEqual([c.args[2:4] for c in self.cli.call_args_list], [("cluster", "start")])

    def test_invalid_peer_profile_blocks_all_stops_and_the_requested_boot(self):
        observed = "dsv41-late"
        changed = {"container": {"name": "dsv41-renamed"}}
        for reason, value in (("missing", FileNotFoundError("moved.json")), ("invalid JSON", SystemExit("invalid JSON")),
                              ("renamed", (changed, self.nodes, {})),
                              ("wrong topology", ({"container": {"name": observed}}, {"nodes": []}, {}))):
            def resolve(args):
                if args.cluster_config != "moved.json":
                    return self.configurations[args.cluster_config]
                if isinstance(value, BaseException):
                    raise value
                return value

            self.resolve.side_effect = resolve
            for operation in (lab.stop_cluster, lambda: lab.boot(self.config)):
                with self.subTest(reason=reason, operation=operation):
                    self.listings(self.peer, observed + "\tmoved.json\n")
                    self.assertFalse(operation())
                    self.cli.assert_not_called()
                    self.assertIn("moved.json", self.output.getvalue())

    def test_current_name_can_be_stopped_without_its_historical_label(self):
        for old_label in ("", "missing.json"):
            with self.subTest(label=old_label):
                self.cli.reset_mock()
                self.listings("", self.cluster["container"]["name"] + "\t" + old_label + "\n")
                self.assertTrue(lab.stop_cluster())
                self.assertEqual([call.args[1] for call in self.cli.call_args_list], [self.config])
                self.resolve.assert_not_called()

    def execute_step(self, kind, *, remove=False):
        step = {"kind": kind, "remove": remove, "config": self.config, "dataset": "fixture",
                "seed": 1, "out": "unused.log"}
        with patch.object(lab, "plan", return_value=[step]), patch.object(lab, "plan_problems", return_value=[]), \
                patch.object(lab, "read_hold", return_value={}), patch.object(lab, "hold_is_ours", return_value=True), \
                patch.object(lab, "window_should_close", return_value=None), patch.object(lab, "beat"), \
                patch.object(lab, "window_close"), patch.object(lab, "container_running", return_value=False), \
                patch.object(lab, "agentx_prepared", side_effect=[False, True]), \
                patch.object(lab, "agentx_prepare_command", return_value=["fixture"]), \
                patch.object(lab, "run_logged", return_value=0) as rebuild:
            code = lab.execute({"run": "fixture"}, dry=False, keep_open=True, close_on_failure=False)
        return code, rebuild.call_count

    def test_stop_and_agentx_rebuild_check_workers_when_the_head_is_idle(self):
        for kind in ("stop", "agentx_prepare"):
            for worker, status, stopped in (("", 0, []), (self.peer, 0, [self.config, "other.json"]),
                                            (self.peer, 1, [])):
                with self.subTest(kind=kind, worker=worker, status=status):
                    self.cli.reset_mock()
                    self.ssh.reset_mock()
                    self.listings("", worker, returncode=status)
                    self.assertEqual(self.execute_step(kind), (status, int(kind == "agentx_prepare" and status == 0)))
                    self.assertEqual(self.ssh.call_count, 2)
                    self.assertEqual([c.args[1] for c in self.cli.call_args_list], stopped)

    def test_remove_still_removes_stopped_containers_after_an_empty_survey(self):
        self.listings("", "")
        self.assertEqual(self.execute_step("stop", remove=True), (0, 0))
        self.assertEqual(self.cli.call_args.args, ("--cluster-config", self.config, "cluster", "stop",
                                                  "--remove", "--apply", "--parallel"))

    def test_peer_recording_current_config_under_another_name_needs_matching_profile(self):
        self.listings("", f"dsv41-previous\t{self.config}\n")
        self.assertFalse(lab.stop_cluster())
        self.cli.assert_not_called()

    def test_restore_requiring_boot_keeps_current_engine_if_inventory_fails(self):
        self.listings(self.peer, "", returncode=1)
        with patch.object(lab, "GPU_CLOCKS_MARKER") as marker, \
                patch.object(lab, "production_live", return_value=False):
            marker.exists.return_value = False
            self.assertFalse(lab.restore_production())
        self.cli.assert_not_called()


if __name__ == "__main__":
    unittest.main()
