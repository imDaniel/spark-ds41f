"""CPU-only preservation checks for the isolated mHC input candidate."""
import hashlib
import importlib.machinery
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "experiments/2026-10-19-r6x-steering"
NEW = ROOT / "experiments/2026-10-21-mhc-input-quant"
loader = importlib.machinery.SourceFileLoader("spark_mhc_packaging", str(ROOT / "bin/spark"))
spec = importlib.util.spec_from_loader(loader.name, loader)
spark = importlib.util.module_from_spec(spec)
loader.exec_module(spark)


def read(path):
    return json.loads(path.read_text())


class MHCInputPackagingTest(unittest.TestCase):
    def test_inherited_series_is_applied_once_in_original_order(self):
        old = [(OLD / "vllm" / name).resolve()
               for name in spark.read_series(OLD / "vllm/series")]
        entries = spark.read_series(NEW / "vllm/series")
        new = [(NEW / "vllm" / name).resolve() for name in entries]
        self.assertEqual(len(old), 188)
        self.assertEqual(new[:188], old)
        self.assertEqual(len(set(new)), len(new))
        for index, name in enumerate(entries[188:], 500):
            self.assertEqual(Path(name).parent, Path("."))
            self.assertTrue(name.startswith(f"{index:04d}-"), name)
            self.assertTrue((NEW / "vllm" / name).is_file())

    def test_inherited_fingerprint_and_file_map_are_unchanged(self):
        source = read(OLD / "upstreams.lock.json")["sources"]["vllm"]
        digest = spark.patchset_sha256(source)
        self.assertEqual(digest, "327d35a7e86cab34ade289e030d2fcf13e84b034a6a9a121c154702235594cf8")
        mapping = read(NEW / "patch-map.json")
        self.assertEqual(mapping["inherited_patchset_sha256"], digest)
        self.assertEqual(len(mapping["mapping"]), 188)
        for ordinal, row in enumerate(mapping["mapping"], 1):
            self.assertEqual(row["ordinal"], ordinal)
            self.assertEqual(row["status"], "unchanged")
            self.assertEqual(hashlib.sha256((ROOT / row["path"]).read_bytes()).hexdigest(), row["sha256"])
            self.assertEqual((NEW / "vllm" / row["candidate_entry"]).resolve(), (ROOT / row["path"]).resolve())

    def test_profile_preserves_steering_and_capacity(self):
        old, new = read(OLD / "cluster.json"), read(NEW / "cluster.json")
        self.assertEqual(new["serve_args"], old["serve_args"])
        self.assertEqual(new["container"]["mounts"], old["container"]["mounts"])
        self.assertEqual(new["shapes"]["tp4-ring"]["sizing"], old["shapes"]["tp4-ring"]["sizing"])
        self.assertEqual(new["deployment"]["launch_enabled"], old["deployment"]["launch_enabled"])
        self.assertEqual(new["environment"]["VLLM_DS41_Q03_MHC_MXFP8"], "0")
        self.assertEqual(new["host"], old["host"])

    def test_frozen_candidate_source_identity_is_consistent(self):
        source = read(NEW / "source.json")["vllm"]
        lock = read(NEW / "upstreams.lock.json")
        base = read(NEW / "base.json")
        mapping = read(NEW / "patch-map.json")
        self.assertTrue(base["candidate_export_complete"])
        self.assertEqual(len(spark.read_series(NEW / "vllm/series")), 192)
        self.assertEqual(len(mapping["new_patches"]), 4)
        for key in ("patch_head", "expected_tree", "patchset_sha256"):
            self.assertTrue(source[key])
            self.assertEqual(source[key], base[key])
        self.assertEqual(spark.patchset_sha256(lock["sources"]["vllm"]), source["patchset_sha256"])
        self.assertEqual(read(NEW / "cluster.json")["container"]["expected_labels"]["local.spark3.vllm.tree"], source["expected_tree"])
        for row in mapping["new_patches"]:
            self.assertEqual(hashlib.sha256((NEW / row["patch"]).read_bytes()).hexdigest(), row["sha256"])

    def test_other_component_sources_and_cpu_history_are_preserved(self):
        old, new = read(OLD / "source.json"), read(NEW / "source.json")
        for name in ("b12x", "nccl", "tilelang", "tile_kernels", "sparknet", "runtime_foundation"):
            self.assertEqual(new[name], old[name])
        self.assertEqual(read(NEW / "cpu-cases.json"), read(OLD / "cpu-cases.json"))


if __name__ == "__main__":
    unittest.main()
