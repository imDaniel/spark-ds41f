"""The CPU gate must not count missing or skipped checks as validation."""

import importlib.util
import json
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "steering_cpu_gate", ROOT / "scripts/check_steering_cpu.py"
)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


class CpuGateTest(unittest.TestCase):
    def test_r6x_candidate_keeps_deployment_in_the_owner_fork_and_isolated(self):
        candidate = json.loads(
            (ROOT / "experiments/2026-10-19-r6x-steering/cluster.json").read_text()
        )
        parent = json.loads(
            (ROOT / "experiments/2026-10-18-steering-consume/cluster.json").read_text()
        )
        deployment = candidate["deployment"]
        self.assertEqual(deployment["repository"], parent["deployment"]["repository"])
        self.assertEqual(deployment["branch"], "perf/steering-consume")
        self.assertFalse(deployment["launch_enabled"])
        self.assertNotEqual(deployment["path"], parent["deployment"]["path"])
        self.assertNotEqual(candidate["container"]["name"], parent["container"]["name"])
        self.assertNotEqual(
            candidate["environment"]["TRITON_CACHE_DIR"],
            parent["environment"]["TRITON_CACHE_DIR"],
        )

    def test_candidate_keeps_runtime_contract_and_isolates_artifacts(self):
        parent = json.loads(
            (ROOT / "experiments/2026-10-18-request-steering/cluster.json").read_text()
        )
        candidate = json.loads(
            (ROOT / "experiments/2026-10-18-steering-consume/cluster.json").read_text()
        )
        self.assertNotEqual(candidate["container"]["name"], parent["container"]["name"])
        self.assertNotEqual(
            candidate["container"]["image"], parent["container"]["image"]
        )
        self.assertNotEqual(
            candidate["environment"]["TRITON_CACHE_DIR"],
            parent["environment"]["TRITON_CACHE_DIR"],
        )
        for key in ("promoted_baseline", "upstreams_config"):
            candidate[key] = parent[key]
        for key in ("name", "image", "expected_labels"):
            candidate["container"][key] = parent["container"][key]
        candidate["environment"] = {
            key: value.replace("/cache/steering-consume-20261018/", "/cache/kkref/")
            if isinstance(value, str)
            else value
            for key, value in candidate["environment"].items()
        }
        self.assertEqual(candidate, parent)

    def test_report_requires_executed_tests_without_skips_or_errors(self):
        expected = gate.load_expected_cases()
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.xml"
            root = ET.Element(
                "testsuite",
                tests=str(len(expected)),
                failures="0",
                errors="0",
                skipped="0",
            )
            for case in expected:
                module, name = case.split("::", 1)
                ET.SubElement(root, "testcase", classname=module, name=name)
            ET.ElementTree(root).write(report)
            self.assertEqual(
                gate.check_report(report),
                {"tests": len(expected), "failures": 0, "errors": 0, "skipped": 0},
            )
            # Keep every pinned identity and its count intact. Only the outcome
            # changes, so collection validation cannot mask a missing status guard.
            first = root.find("testcase")
            for status, tag in (
                ("failures", "failure"),
                ("errors", "error"),
                ("skipped", "skipped"),
            ):
                root.set(status, "1")
                outcome = ET.SubElement(first, tag, message="synthetic outcome")
                ET.ElementTree(root).write(report)
                with (
                    self.subTest(status=status),
                    self.assertRaisesRegex(
                        ValueError, "executed, passing, unskipped tests"
                    ),
                ):
                    gate.check_report(report)
                first.remove(outcome)
                root.set(status, "0")
            report.write_text("<testsuite tests='0'/>")
            with self.assertRaisesRegex(
                ValueError, "executed, passing, unskipped tests"
            ):
                gate.check_report(report)

    def test_one_selected_test_cannot_pass_complete_collection(self):
        expected = gate.load_expected_cases()
        module, name = expected[0].split("::", 1)
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "partial.xml"
            root = ET.Element(
                "testsuite", tests="1", failures="0", errors="0", skipped="0"
            )
            ET.SubElement(root, "testcase", classname=module, name=name)
            ET.ElementTree(root).write(report)
            with self.assertRaisesRegex(ValueError, "complete pinned test collection"):
                gate.check_report(report)

    def test_duplicate_or_substituted_cases_cannot_replace_missing_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "partial.xml"
            report.write_text(
                "<testsuite tests='2'><testcase classname='m' name='a'/>"
                "<testcase classname='m' name='a'/></testsuite>"
            )
            with self.assertRaisesRegex(ValueError, "complete pinned test collection"):
                gate.check_report(report, ["m::a", "m::b"])

    def test_external_selection_and_plugins_are_isolated(self):
        external = {
            "PYTEST_ADDOPTS": "-k only_one --deselect=anything",
            "PYTEST_PLUGINS": "external_filter",
            "PYTEST_CURRENT_TEST": "parent test",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "0",
        }
        with patch.dict(os.environ, external):
            env = gate.pytest_environment()
            for key in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTEST_CURRENT_TEST"):
                self.assertNotIn(key, env)
                self.assertEqual(os.environ[key], external[key])
            self.assertEqual(env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"], "1")

    def test_manifest_requires_every_cpu_module(self):
        self.assertEqual(
            {case.split("::", 1)[0] for case in gate.load_expected_cases()},
            {path[:-3].replace("/", ".") for path in gate.CPU_TESTS},
        )

    def test_explicit_experiment_manifest_is_used_without_changing_the_default(self):
        expected = gate.load_expected_cases()
        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder) / "cases.json"
            manifest.write_text(json.dumps({"schema_version": 1, "test_cases": expected[::-1]}))
            with patch.object(gate, "EXPECTED_CASES", Path(folder) / "absent.json"):
                self.assertEqual(gate.load_expected_cases(manifest), expected[::-1])
                with self.assertRaises(FileNotFoundError):
                    gate.load_expected_cases()
        self.assertEqual(gate.load_expected_cases(), expected)


if __name__ == "__main__":
    unittest.main()
