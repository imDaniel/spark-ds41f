"""Exercise release receipt checks on the archived, completed 1126 gate."""
import json
from pathlib import Path
import tempfile
import unittest

import native


class NativeAuditTests(unittest.TestCase):
    def test_gate_keeps_all_historical_files_and_reviewed_changes(self):
        source = native.read(native.RC3 / "source.json")["vllm"]
        manifest = native.make_manifest(source)
        self.assertTrue(native.TARGETS.issubset(manifest["files"]))
        self.assertTrue(set(native.read(native.HIST_MANIFEST)["files"]).issubset(manifest["files"]))
        self.assertFalse(set(manifest["files"]) & set(manifest["scope_exclusions"]))
        self.assertEqual(manifest["source_tree"], source["expected_tree"])

    def test_archived_pre_fix_gate_cannot_claim_rc3_gpu_coverage(self):
        with self.assertRaisesRegex(ValueError, "warms_each_input_variant"):
            native.audit_bulk(native.HIST / "component-run-5", native.read(native.HIST_MANIFEST))

    def test_a_skipped_named_case_cannot_pass(self):
        key = (native.GRAPH, "test_profile_cudagraph_memory_frees_throwaway_pool[0]")
        with self.assertRaisesRegex(ValueError, "Missing mandatory"):
            native.require_cases({key: "skipped"}, [key])

    def test_a_missing_file_cannot_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "results.jsonl").write_text(json.dumps({"file": native.GRAPH}) + "\n")
            with self.assertRaisesRegex(ValueError, "Incomplete native"):
                native.audit_bulk(folder, {"files": [native.GRAPH, native.SALT]})

if __name__ == "__main__":
    unittest.main()
