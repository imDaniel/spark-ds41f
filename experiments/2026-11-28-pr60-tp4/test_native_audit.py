"""Exercise release receipt checks on the archived, completed 1126 gate."""
import json
from pathlib import Path
import tempfile
import unittest
from xml.etree import ElementTree

import native


def write_junit(path, cases):
    suite = ElementTree.Element("testsuite")
    for filename, classname, name, outcome in cases:
        case = ElementTree.SubElement(suite, "testcase", file=filename, classname=classname, name=name)
        if outcome != "passed":
            ElementTree.SubElement(case, outcome)
    ElementTree.ElementTree(suite).write(path)


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
        key = (native.GRAPH, native.GRAPH.removesuffix(".py").replace("/", "."),
               "test_profile_cudagraph_memory_frees_throwaway_pool[0]")
        with self.assertRaisesRegex(ValueError, "Missing mandatory"):
            native.require_cases({key: "skipped"}, [key])

    def test_a_missing_file_cannot_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "results.jsonl").write_text(json.dumps({"file": native.GRAPH}) + "\n")
            with self.assertRaisesRegex(ValueError, "Incomplete native"):
                native.audit_bulk(folder, {"files": [native.GRAPH, native.SALT]})

    def test_same_name_in_another_class_cannot_hide_a_regressed_skip(self):
        first = ("tests/test_reasoning.py", "tests.test_reasoning.A", "test_value[low]")
        second = ("tests/test_reasoning.py", "tests.test_reasoning.B", "test_value[low]")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chunk000.xml"
            write_junit(path, [(*first, "passed"), (*second, "skipped")])
            previous = native.test_outcomes(path)
            write_junit(path, [(*first, "skipped"), (*second, "passed")])
            current = native.test_outcomes(path)
            self.assertEqual(len(current), 2)
            with self.assertRaisesRegex(ValueError, r"Previously passing.*reasoning\.A"):
                native.check_regressed_skips(previous, current)

    def test_duplicate_full_identity_is_rejected(self):
        case = ("test_a.py", "test_a.A", "test_value[1]", "passed")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chunk000.xml"
            write_junit(path, [case, case])
            with self.assertRaisesRegex(ValueError, "duplicate JUnit case"):
                native.test_outcomes(path)

    def test_supplemental_xunit2_allows_only_unique_unnamed_module_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "parser.xml"
            case = ("", "", "test_recorded_tool_output[0-named-False]", "passed")
            second = ("", "", "test_recorded_tool_output[0-named-True]", "passed")
            write_junit(path, [case, second])
            self.assertEqual(len(native.test_outcomes(path, supplemental=True)), 2)
            with self.assertRaisesRegex(ValueError, "Missing or duplicate"):
                native.test_outcomes(path)
            write_junit(path, [case, case])
            with self.assertRaisesRegex(ValueError, "duplicate JUnit case"):
                native.test_outcomes(path, supplemental=True)
            qualified = ("", "other.module", case[2], "passed")
            for cases in ([case, qualified], [qualified, case]):
                write_junit(path, cases)
                with self.assertRaisesRegex(ValueError, "Ambiguous supplemental"):
                    native.test_outcomes(path, supplemental=True)

    def test_per_file_counts_are_checked_even_if_combined_totals_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            files = ["test_a.py", "test_b.py"]
            write_junit(folder / "chunk000.xml", [(files[0], "test_a", "test_one", "passed"),
                                                  (files[1], "test_b", "test_two", "skipped")])
            rows = [{"file": p, "status": "ok", "passed": n, "skipped": 1 - n, "failed": 0, "error": 0}
                    for p, n in zip(files, [0, 1])]
            (folder / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
            with self.assertRaisesRegex(ValueError, "JSON/JUnit count mismatch for test_a.py passed"):
                native.audit_bulk(folder, {"files": files})

    def test_fallback_requires_every_file_in_its_assigned_report(self):
        files = ["test_a.py", "test_b.py"]
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "chunk000.xml").write_text("<timed-out")
            case = ("test_a.py", "test_a", "test_value", "passed")
            write_junit(folder / "chunk000-00.xml", [case])
            with self.assertRaisesRegex(ValueError, "Incomplete per-file fallback"):
                native.component_cases(folder, files)
            write_junit(folder / "chunk000-01.xml", [case])
            with self.assertRaisesRegex(ValueError, "Wrong or missing JUnit files"):
                native.component_cases(folder, files)
            write_junit(folder / "chunk000-01.xml", [("test_b.py", "test_b", "test_value", "passed")])
            self.assertEqual(len(native.component_cases(folder, files)), 2)


if __name__ == "__main__":
    unittest.main()
