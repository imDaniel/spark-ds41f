"""Package/import smoke only; the previously reviewed fixture suite is not rerun.

Run explicitly from this experiment directory. No network transport, native runtime,
model, device, or existing repository scorer is imported or invoked.
"""
import copy
import hashlib
import importlib
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parent
ACCEPTED_MANIFEST_SHA256 = "455fe5e85630730d060002a89b4b6e9a11f7f5d400f5e8f17fb7e8edd910e426"


class PackagingSmokeTests(unittest.TestCase):
    def test_accepted_payloads_are_byte_identical(self):
        raw = (ROOT / "MANIFEST.json").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), ACCEPTED_MANIFEST_SHA256)
        for name, digest in json.loads(raw)["files"].items():
            with self.subTest(path=name):
                self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), digest)

    def test_standalone_imports_resolve_only_to_packaged_files(self):
        for name, path in (
            ("scoring_contract", "scoring_contract.py"),
            ("strict_producer", "strict_producer.py"),
            ("reference.pr8_projection", "reference/pr8_projection.py"),
            ("test_scoring_contract", "test_scoring_contract.py"),
            ("source_probe", "source_probe.py"),
        ):
            with self.subTest(module=name):
                module = importlib.import_module(name)
                self.assertEqual(Path(module.__file__).resolve(), ROOT / path)
        from scoring_contract import check_frozen, score_extra_body
        from strict_producer import check_frozen as producer_check_frozen
        from reference.pr8_projection import score_extra_body as preserved_projection
        self.assertIs(check_frozen, producer_check_frozen)
        self.assertIs(score_extra_body, preserved_projection)
        self.assertFalse(any(name == "servebench" or name.startswith("servebench.")
                             or name == "vllm" or name.startswith("vllm.")
                             or name == "torch" or name.startswith("torch.")
                             for name in sys.modules))

    def test_documented_api_uses_only_an_in_memory_transport(self):
        from scoring_contract import load_json, validate
        from strict_producer import collect_records
        from test_scoring_contract import fixture

        frozen, evidence = fixture()  # synthetic IDs and probabilities, not a model run
        by_prompt = {tuple(record["request"]["prompt"]): record["response"]
                     for record in evidence["records"]}
        calls = []

        def memory_post(path, body, timeout):
            calls.append((path, copy.deepcopy(body), timeout))
            # Exercise strict decoding without using a socket or an HTTP client.
            return load_json(json.dumps(by_prompt[tuple(body["prompt"])]))

        salts = iter(("package-fixture-0", "package-fixture-1"))
        evidence["records"] = collect_records(frozen, memory_post, timeout=1.0,
                                                salt_factory=lambda: next(salts))
        result = validate(frozen, evidence)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(path == "/v1/completions" and body["return_token_ids"] is True
                            and timeout == 1.0 for path, body, timeout in calls))
        self.assertEqual(result["status"], "COMPLETE_RECORDED_SCORING_ONLY")
        self.assertFalse(result["quality_pass"])
        self.assertEqual(result["output_positions"], 4)
        self.assertEqual(result["scores"][0]["returned_prompt_token_ids"], [10, 11, 12, 13])
        self.assertEqual(result["scores"][0]["tokens"], [12, 13])


if __name__ == "__main__":
    unittest.main(verbosity=2)
