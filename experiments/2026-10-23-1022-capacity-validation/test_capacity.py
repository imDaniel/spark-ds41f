import copy
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch

import capacity_requests as q

window = runpy.run_path(str(Path(__file__).with_name("run_window.py")))


class CapacityTests(unittest.TestCase):
    def row(self):
        return {
            "concurrency": 4,
            "input_tokens": 262144,
            "output_tokens": 768,
            "samples": [{"num_requests_running": 4}],
            "results": [
                {
                    "finish_reason": "length",
                    "usage": {
                        "prompt_tokens": 262144,
                        "completion_tokens": 768,
                        "prompt_tokens_details": {"cached_tokens": 0},
                    },
                }
                for _ in range(4)
            ],
        }

    def test_success_requires_usage_and_admitted_concurrency(self):
        q.check_capacity(self.row())

    def test_serialized_successes_do_not_prove_concurrency(self):
        row = self.row()
        row["samples"][0]["num_requests_running"] = 3
        with self.assertRaisesRegex(ValueError, "concurrency"):
            q.check_capacity(row)

    def test_cache_hit_cannot_pass_cold_capacity(self):
        row = self.row()
        row["results"][0]["usage"]["prompt_tokens_details"]["cached_tokens"] = 16
        with self.assertRaisesRegex(ValueError, "usage"):
            q.check_capacity(row)

    def test_truncated_or_missing_response_cannot_pass(self):
        for mutate in (
            lambda r: r["results"].pop(),
            lambda r: r["results"][0].update(finish_reason="stop"),
        ):
            row = self.row()
            mutate(row)
            with self.assertRaises(ValueError):
                q.check_capacity(row)

    def test_metrics_preserve_running_waiting_and_kv(self):
        metrics = q.parse_metrics(
            'vllm:num_requests_running{model="test"} 4\n'
            'vllm:num_requests_waiting{model="test"} 2\n'
            'vllm:kv_cache_usage_perc{model="test"} 4.5e-1\n'
        )
        self.assertEqual(
            metrics,
            {
                "num_requests_running": 4,
                "num_requests_waiting": 2,
                "kv_cache_usage_perc": 0.45,
            },
        )
        with self.assertRaisesRegex(ValueError, "Missing"):
            q.parse_metrics("")

    def test_explicit_scale_guard_precedes_network(self):
        with patch.object(q.urllib.request, "urlopen") as network:
            with self.assertRaisesRegex(ValueError, "explicit scale"):
                q.request("/v1/completions", {"model": q.client.MODEL})
            network.assert_not_called()

    def test_prior_gate_receipts_are_complete(self):
        self.assertEqual(
            window["validate_request_reuse"](),
            {"tool": 12, "followup": 2, "quality": 20, "cache": 13, "cold_c1": 5},
        )

    def test_changed_receipt_is_rejected(self):
        fn = window["validate_request_reuse"]
        with (
            patch.dict(fn.__globals__, sha=lambda _: "changed"),
            self.assertRaisesRegex(ValueError, "receipt changed"),
        ):
            fn()

    def test_changed_runtime_cannot_reuse_receipt(self):
        fn = window["validate_request_reuse"]
        original = fn.__globals__["read"]

        def changed(path):
            data = copy.deepcopy(original(path))
            if path.name == "base.json":
                data["candidate"]["image_id"] = "different"
            return data

        with (
            patch.dict(fn.__globals__, read=changed),
            self.assertRaisesRegex(ValueError, "identity differs"),
        ):
            fn()


if __name__ == "__main__":
    unittest.main()
