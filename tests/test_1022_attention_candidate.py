"""The offline candidate must preserve recovery inputs and remain unlaunchable."""

import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "experiments/2026-10-23-1022-attention-steering"


def read(name):
    return json.loads((EXPERIMENT / name).read_text())


class Attention1022CandidateTest(unittest.TestCase):
    def test_retained_patches_are_byte_identical_and_new_patch_is_tests_only(self):
        mapping = read("patch-map.json")
        retained = [entry for entry in mapping["mapping"] if entry["old_patch"]]
        self.assertEqual(len(retained), 12)
        for entry in retained:
            before = (ROOT / entry["old_patch"]).read_bytes()
            after = (ROOT / entry["new_patch"]).read_bytes()
            self.assertEqual(before, after)
            self.assertEqual(hashlib.sha256(after).hexdigest(), entry["new_sha256"])
        additions = [entry for entry in mapping["mapping"] if not entry["old_patch"]]
        self.assertEqual(len(additions), 1)
        diff = (ROOT / additions[0]["new_patch"]).read_text()
        paths = [line.split(" b/", 1)[1] for line in diff.splitlines() if line.startswith("diff --git ")]
        self.assertTrue(paths)
        self.assertTrue(all(path.startswith("tests/models/") for path in paths))

    def test_candidate_cannot_launch_and_has_isolated_resources(self):
        candidate = read("cluster.json")
        previous = json.loads((ROOT / "experiments/2026-10-19-r6x-steering-gpu/followup/acceptance.json").read_text())
        self.assertIs(candidate["deployment"]["launch_enabled"], False)
        self.assertEqual(candidate["deployment"]["repository"], "https://github.com/imDaniel/spark-ds41f.git")
        for key in ("path", "ready_url"):
            self.assertNotEqual(candidate["deployment"][key], previous["deployment"][key])
        for key in ("name", "image"):
            self.assertNotEqual(candidate["container"][key], previous["container"][key])
        new_mounts = {target: source for source, target, _ in candidate["container"]["mounts"]}
        old_mounts = {target: source for source, target, _ in previous["container"]["mounts"]}
        self.assertNotEqual(new_mounts["/cache"], old_mounts["/cache"])
        self.assertEqual(new_mounts["/models"], old_mounts["/models"])
        self.assertIn("/steering-1022", new_mounts)
        self.assertEqual(candidate["environment"]["VLLM_LATE_ADMISSION_LAYER"], "30")
        self.assertEqual(candidate["environment"]["VLLM_SINK_REJECTION"], "1")

    def test_startup_pin_matches_rebound_profile_and_preserves_logical_name(self):
        candidate = read("cluster.json")
        receipt = read("profile-rebind.json")
        args = candidate["serve_args"]
        startup = json.loads(args[args.index("--additional-config") + 1])["ds41_steering"]
        self.assertEqual(startup["profile_sha256"], receipt["new_artifact_sha256"])
        self.assertEqual(receipt["profile_id"], "heretic-1017-attn-v1")
        self.assertNotEqual(receipt["old_template_identity"], receipt["new_template_identity"])
        self.assertNotEqual(receipt["old_profile_identity"], receipt["new_profile_identity"])
        self.assertEqual(receipt["layers"], list(range(2, 40)))
        self.assertEqual(receipt["alphas"], [2.5] * 38)
        self.assertEqual(receipt["direction_payload_bytes"], 778240)
        self.assertFalse(receipt["weights_modified"])
        self.assertFalse(receipt["directions_reextracted"])
        self.assertFalse(receipt["gpu_qualified"])
        self.assertIn("--enable-prompt-tokens-details", args)

    def test_no_native_dependency_migration_is_hidden_in_the_steering_series(self):
        source = read("source.json")
        upstream = json.loads((ROOT / "manifests/sources/2026-10-22-spark-ds41f-1022-source.json").read_text())
        for name in ("b12x", "nccl", "tilelang", "tile_kernels", "sparknet"):
            self.assertEqual(source[name], upstream[name])
        self.assertEqual(source["vllm"]["expected_tree"], read("patch-map.json")["expected_tree"])
        lock = read("upstreams.lock.json")
        self.assertEqual(lock["sources"]["sparknet"]["version"], "0.5.0")


if __name__ == "__main__":
    unittest.main()
