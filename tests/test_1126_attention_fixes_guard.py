"""A 1126 candidate and its release must detect each other as serving engines."""

import re
import subprocess
import unittest
from unittest.mock import patch

import spark_ds41f as spark


class SiteServingGuardTest(unittest.TestCase):
    def setUp(self):
        self.release = spark.read_cluster("releases/homelab-1126-attention/cluster.json", "tp4-ring")
        self.candidate = spark.read_cluster("experiments/2026-11-28-1126-attention-fixes-rc2/cluster.json")

    def test_build_refuses_either_running_engine(self):
        for current, other in ((self.release, self.candidate), (self.candidate, self.release)):
            running = other["container"]["name"]
            with self.subTest(running=running):
                listed = subprocess.CompletedProcess([], 0, stdout=running + "\n")
                with patch.object(spark.subprocess, "run", return_value=listed):
                    with self.assertRaisesRegex(SystemExit, "service is running"):
                        spark.require_idle_build_host(current)

    def test_peer_filters_are_exact_and_leave_nonserving_jobs_alone(self):
        for current, other in ((self.release, self.candidate), (self.candidate, self.release)):
            with self.subTest(profile=current["container"]["name"]):
                patterns = [p.removeprefix("name=") for p in spark.serving_filters(current)[1::2]]
                for name, expected in ((other["container"]["name"], True),
                                       (other["container"]["name"] + "-build", False),
                                       ("ds41f-release-2fb58ec6", False)):
                    self.assertEqual(any(re.search(p, name) for p in patterns), expected, name)
                    self.assertEqual(spark.serving_container(name, current), expected, name)

    def test_inspect_for_stop_still_targets_only_the_selected_container(self):
        self.assertNotEqual(self.release["container"]["name"], self.candidate["container"]["name"])
        head = {"name": "dgx1", "head": True}
        nodes = {"nodes": [head]}
        for current in (self.release, self.candidate):
            with self.subTest(profile=current["container"]["name"]):
                absent = subprocess.CompletedProcess([], 1, stderr="No such container")
                with patch.object(spark, "run_ssh", return_value=absent) as inspect:
                    self.assertIsNone(spark.live_engine(current, nodes))
                inspect.assert_called_once_with(nodes, head, "docker", "inspect", current["container"]["name"])


if __name__ == "__main__":
    unittest.main()
