"""Keep the maintenance recipe and its offline evidence tied to the same artifacts."""

import hashlib
import json
import unittest
from pathlib import PurePosixPath

import spark_ds41f as spark


class MaintenanceCandidateTest(unittest.TestCase):
    experiment = "experiments/2026-11-28-1126-attention-fixes-rc2"

    def setUp(self):
        self.cluster = spark.read_cluster(f"{self.experiment}/cluster.json")
        self.receipt = spark.read_json(f"{self.experiment}/profile-rebind.json")

    def test_offline_results_and_rebind_match_the_locked_component(self):
        lock = spark.read_json(self.cluster["upstreams_config"])["sources"]["vllm"]
        offline = spark.read_json(f"{self.experiment}/offline-results.json")
        for field, source_key in (("head", "patch_head"), ("tree", "expected_tree")):
            self.assertEqual(offline[f"component_{field}"], lock[source_key])
            self.assertEqual(self.receipt[f"source_{field}"], lock[source_key])
        for group in ("encoder", "cache", "graph"):
            result = offline["combined_candidate"][group]
            with self.subTest(group=group):
                self.assertGreater(result["tests"], 0)
                self.assertEqual((result["failures"], result["errors"], result["skipped"]), (0, 0, 0))
        self.assertEqual(self.receipt["source_patchset_sha256"], lock["patchset_sha256"])
        self.assertEqual(
            self.receipt["conversion"]["tool"], f"{self.experiment}/tools/rebind_profile.py"
        )
        tool = spark.ROOT / self.receipt["conversion"]["tool"]
        self.assertEqual(
            self.receipt["conversion"]["sha256"], hashlib.sha256(tool.read_bytes()).hexdigest()
        )

    def test_recipe_mounts_the_profile_named_by_its_rebind(self):
        args = self.cluster["serve_args"]
        profile = json.loads(args[args.index("--additional-config") + 1])["ds41_steering"]
        digest = self.receipt["new_artifact_sha256"]
        self.assertEqual(profile["profile_sha256"], digest)
        self.assertEqual(profile["operator_version"], self.receipt["operator_version"])
        parent = spark.read_cluster("releases/homelab-1126-attention/cluster.json", "tp4-ring")
        root, destination, mode = next(
            mount for mount in parent["container"]["mounts"] if mount[1] == "/steering-1126"
        )
        self.assertEqual(
            profile["profile_path"], str(PurePosixPath(destination) / self.receipt["artifact_file"])
        )
        self.assertIn(
            [str(PurePosixPath(root).with_name(digest[:12])), destination, mode],
            self.cluster["container"]["mounts"],
        )


if __name__ == "__main__":
    unittest.main()
