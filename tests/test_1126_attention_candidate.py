"""The two site profiles share a base and select one steering implementation."""

import json
import subprocess
import unittest
from unittest.mock import patch

import spark_ds41f as spark

EXPERIMENT = "experiments/2026-11-28-1126-attention"


class SteeringModesTest(unittest.TestCase):
    def setUp(self):
        self.attn = spark.read_cluster(f"{EXPERIMENT}/cluster.json")
        self.glp = spark.read_cluster(f"{EXPERIMENT}/glp.json")
        self.receipt = spark.read_json(f"{EXPERIMENT}/profile-rebind.json")

    def test_modes_share_the_locked_image_checkpoint_and_capacity(self):
        for cluster in (self.attn, self.glp):
            self.assertFalse(cluster["deployment"]["launch_enabled"])
            self.assertEqual(cluster["nodes_config"], "config/nodes-1022-attention.local.json")
            args = cluster["serve_args"]
            for key, value in {
                "--tensor-parallel-size": "4", "--max-num-seqs": "16",
                "--max-model-len": "1048576", "--max-num-batched-tokens": "8192",
                "--kv-cache-memory-bytes": "11274289152", "--mm-processor-cache-gb": "1",
                "--limit-mm-per-prompt": '{"image":999}',
            }.items():
                self.assertEqual(args[args.index(key) + 1], value)
            self.assertEqual(cluster["environment"]["SPARKNET_ROCE_GID_INDEX"], "5")
            self.assertEqual(cluster["container"]["expected_labels"]["local.spark-ds41f.vllm.tree"],
                             self.receipt["source_tree"])
            self.assertEqual([m for m in cluster["container"]["mounts"] if m[1] == "/models"],
                             [self.attn["container"]["mounts"][0]])
        self.assertEqual(self.attn["container"]["image"], self.glp["container"]["image"])
        self.assertEqual(self.attn["upstreams_config"], self.glp["upstreams_config"])

    def test_request_contract_and_directions_are_mode_specific(self):
        self.assertEqual(spark.served_model_names(self.attn), ["deepseek-v4.1-flash"])
        self.assertEqual(spark.served_model_names(self.glp),
                         ["deepseek-v4.1-flash", "deepseek-v4.1-flash-weightless"])
        self.assertFalse(any(k.startswith("WEIGHTLESS_STEER") for k in self.attn["environment"]))
        args = self.attn["serve_args"]
        profile = json.loads(args[args.index("--additional-config") + 1])["ds41_steering"]
        self.assertEqual(profile["profile_sha256"], self.receipt["new_artifact_sha256"])
        self.assertIn(["/srv/ai-cluster/profiles/deepseek-1017-attention-1126/releases/" +
                       profile["profile_sha256"][:12], "/steering-1126", "ro"],
                      self.attn["container"]["mounts"])
        self.assertNotIn("--additional-config", self.glp["serve_args"])
        self.assertEqual(self.glp["environment"]["WEIGHTLESS_STEER_ALPHA"], "2.0")
        self.assertEqual({m[1] for m in self.glp["container"]["mounts"]}, {"/models", "/cache", "/glp/vector.gguf"})
        self.assertEqual(self.glp["glp"]["sha256"],
                         spark.read_json("releases/spark-ds41f-1126/cluster.json")["glp"]["sha256"])

    def test_explicit_glp_mode_requires_every_rank_to_have_the_file(self):
        nodes = {"nodes": [{"name": f"rank-{i}"} for i in range(4)]}
        for failed in (1, 255):
            def probe(_nodes, node, *_):
                return subprocess.CompletedProcess([], failed if node["name"] == "rank-2" else 0)
            with self.subTest(status=failed), patch.object(spark, "run_ssh", side_effect=probe):
                with self.assertRaisesRegex(SystemExit, "rank-2"):
                    spark.without_missing_glp(self.glp, nodes)


if __name__ == "__main__":
    unittest.main()
