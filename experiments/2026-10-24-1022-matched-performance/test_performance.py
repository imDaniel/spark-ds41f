import gzip
import json
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch

import benchmark as b

HERE = Path(__file__).resolve().parent
window = runpy.run_path(str(HERE / "run_window.py"))


class PerformanceContracts(unittest.TestCase):
    def reference(self):
        history = json.loads((HERE / "history.json").read_text())
        row = history["runs"][0]
        return json.loads(
            gzip.decompress((HERE.parent.parent / row["path"]).read_bytes())
        ), [d["tokens"] for d in row["documents"]]

    def test_original_raw_reference_passes_current_measurement_guards(self):
        b.validate_run(*self.reference())

    def test_all_seven_historical_references_and_client_hashes_verified(self):
        self.assertEqual(window["validate_history"](), 7)
        window["validate_trial_config"]()

    def test_compute_change_cannot_hide_in_trial_configuration(self):
        fn = window["validate_trial_config"]
        original = fn.__globals__["read"]

        def changed(path):
            result = original(path)
            if path.parent == HERE:
                result["serve_args"].append("--fake-compute-change")
            return result

        with (
            patch.dict(fn.__globals__, read=changed),
            self.assertRaisesRegex(ValueError, "non-network"),
        ):
            fn()

    def test_changed_historical_receipt_cannot_pass(self):
        fn = window["validate_history"]
        original = fn.__globals__["sha"]

        def changed(path):
            return "changed" if path.suffix == ".gz" else original(path)

        with (
            patch.dict(fn.__globals__, sha=changed),
            self.assertRaisesRegex(ValueError, "receipt changed"),
        ):
            fn()

    def test_implicit_or_invalid_scale_is_rejected(self):
        for scale in (None, True, 0, 2, "1"):
            with (
                self.subTest(scale=scale),
                self.assertRaisesRegex(ValueError, "explicit scale"),
            ):
                b.validate_payload(
                    {"steering_profile": b.PROFILE, "steering_scale": scale}
                )
        for scale in (1, 4):
            b.validate_payload({"steering_profile": b.PROFILE, "steering_scale": scale})

    def test_failed_foreign_and_approximate_requests_cannot_pass(self):
        for field in ("failed", "foreign", "rejected", "approximate_token_times"):
            row, docs = self.reference()
            row["summary"]["requests"][field] = 1
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ValueError, "benchmark requests"),
            ):
                b.validate_run(row, docs)

    def test_changed_document_size_cannot_pass(self):
        row, docs = self.reference()
        with self.assertRaisesRegex(ValueError, "document sizes"):
            b.validate_run(row, [docs[0] + 1])

    def test_shortened_output_cannot_pass(self):
        row, docs = self.reference()
        row["requests"][0]["completion_tokens"] = 255
        with self.assertRaisesRegex(ValueError, "truncated"):
            b.validate_run(row, docs)

    def test_incomplete_windows_cannot_pass(self):
        row, docs = self.reference()
        row["summary"]["output_tps"]["n"] = 5
        with self.assertRaisesRegex(ValueError, "measurement windows"):
            b.validate_run(row, docs)

    def test_missing_latency_tail_cannot_pass(self):
        row, docs = self.reference()
        del row["summary"]["latency"]["ttft_s"]["p99"]
        with self.assertRaisesRegex(ValueError, "latency distribution"):
            b.validate_run(row, docs)

    def test_cold_workload_cannot_pass_warm_reference_comparison(self):
        row, docs = self.reference()
        row["summary"]["server"]["prefix_cache_hit_rate"]["mean"] = 0.1
        with self.assertRaisesRegex(ValueError, "warm-prefix"):
            b.validate_run(row, docs)


if __name__ == "__main__":
    unittest.main()
