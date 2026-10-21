"""CPU-only preservation checks for the isolated mHC input candidate."""
import hashlib
import importlib.machinery
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "experiments/2026-10-21-mhc-input-quant"
NEW = ROOT / "experiments/2026-10-21-mhc-input-validation"
loader = importlib.machinery.SourceFileLoader("spark_mhc_validation_packaging", str(ROOT / "bin/spark"))
spec = importlib.util.spec_from_loader(loader.name, loader)
spark = importlib.util.module_from_spec(spec)
loader.exec_module(spark)


def read(path):
    return json.loads(path.read_text())


class MHCValidationPackagingTest(unittest.TestCase):
    def test_inherited_series_is_applied_once_in_original_order(self):
        old = [(OLD / "vllm" / name).resolve()
               for name in spark.read_series(OLD / "vllm/series")]
        entries = spark.read_series(NEW / "vllm/series")
        new = [(NEW / "vllm" / name).resolve() for name in entries]
        self.assertEqual(len(old), 192)
        self.assertEqual(new[:192], old)
        self.assertEqual(len(set(new)), len(new))
        for index, name in enumerate(entries[192:], 500):
            self.assertEqual(Path(name).parent, Path("."))
            self.assertTrue(name.startswith(f"{index:04d}-"), name)
            self.assertTrue((NEW / "vllm" / name).is_file())

    def test_inherited_fingerprint_and_file_map_are_unchanged(self):
        source = read(OLD / "upstreams.lock.json")["sources"]["vllm"]
        digest = spark.patchset_sha256(source)
        self.assertEqual(digest, "5a918aefd8a12c5c36aae9117ba7059e976a4503efae3362fd41da1df3dddd7e")
        mapping = read(NEW / "patch-map.json")
        self.assertEqual(mapping["inherited_patchset_sha256"], digest)
        self.assertEqual(len(mapping["mapping"]), 192)
        for ordinal, row in enumerate(mapping["mapping"], 1):
            self.assertEqual(row["ordinal"], ordinal)
            self.assertEqual(row["status"], "unchanged")
            self.assertEqual(hashlib.sha256((ROOT / row["path"]).read_bytes()).hexdigest(), row["sha256"])
            self.assertEqual((NEW / "vllm" / row["candidate_entry"]).resolve(), (ROOT / row["path"]).resolve())

    def test_local_profile_derivation_has_no_operational_defaults(self):
        spec = importlib.util.spec_from_file_location('local_profile', NEW / 'create_local_profile.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        baseline = {'deployment': {'launch_enabled': True},
                    'environment': {}, 'container': {},
                    'serve_args': ['--max-model-len', '1234']}
        before = json.dumps(baseline, sort_keys=True)
        tree = read(NEW / 'source.json')['vllm']['expected_tree']
        result = module.derive(baseline, tree)
        self.assertEqual(json.dumps(baseline, sort_keys=True), before)
        self.assertEqual(result['serve_args'], baseline['serve_args'])
        self.assertFalse(result['deployment']['launch_enabled'])
        self.assertEqual(result['environment']['VLLM_DS41_Q03_MHC_MXFP8'], '0')
        self.assertEqual(result['container']['expected_labels']['local.spark3.vllm.tree'], tree)
        self.assertFalse((NEW / 'cluster.json').exists())
        self.assertFalse((NEW / 'runs').exists())

    def test_frozen_candidate_source_identity_is_consistent(self):
        source = read(NEW / "source.json")["vllm"]
        lock = read(NEW / "upstreams.lock.json")
        self.assertNotIn("references", lock)
        self.assertNotIn("flashinfer", lock["sources"])
        build = spark.build_inputs(lock)
        self.assertEqual(build["vllm"]["patch_head"], source["patch_head"])
        self.assertEqual(build["vllm"]["expected_tree"], source["expected_tree"])
        base = read(NEW / "base.json")
        mapping = read(NEW / "patch-map.json")
        self.assertTrue(base["candidate_export_complete"])
        self.assertEqual(len(spark.read_series(NEW / "vllm/series")), 197)
        self.assertEqual(len(mapping["new_patches"]), 5)
        for key in ("patch_head", "expected_tree", "patchset_sha256"):
            self.assertTrue(source[key])
            self.assertEqual(source[key], base[key])
        self.assertEqual(spark.patchset_sha256(lock["sources"]["vllm"]), source["patchset_sha256"])
        for row in mapping["new_patches"]:
            self.assertEqual(hashlib.sha256((NEW / row["patch"]).read_bytes()).hexdigest(), row["sha256"])

    def test_other_component_sources_and_cpu_history_are_preserved(self):
        old, new = read(OLD / "source.json"), read(NEW / "source.json")
        for name in ("b12x", "nccl", "tilelang", "tile_kernels", "sparknet", "runtime_foundation"):
            self.assertEqual(new[name], old[name])
        self.assertEqual(read(NEW / "cpu-cases.json"), read(OLD / "cpu-cases.json"))


if __name__ == "__main__":
    unittest.main()
