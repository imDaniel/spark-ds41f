"""CPU-only contracts for burst CLI summaries; no live service or performance claim."""

import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from servebench import __main__ as cli  # noqa: E402
from servebench import report  # noqa: E402


class BurstSummaryContractTest(unittest.TestCase):
    def raw_run(self):
        before = {"steps": 10, "generation_tokens": 20, "prefix_cache_queries": 30,
                  "prefix_cache_hits": 15, "requests_finished": 5}
        after = {"steps": 14, "generation_tokens": 28, "prefix_cache_queries": 40,
                 "prefix_cache_hits": 20, "requests_finished": 6}
        return {"start": 100.0, "window": None, "boundaries": [], "rejected": 0,
                "scrape_times": [100.0, 104.0], "scrapes": [before, after],
                "fences": {"times": [100.0, 104.0], "scrapes": [before, after]},
                "records": [{"sent": 100.0, "first": 101.0, "end": 103.0,
                             "events": [[101.0, 2], [103.0, 8]], "completion_tokens": 8,
                             "token_times": "exact", "error": None, "prompt_id": "fixture",
                             "slot": 0, "index": 0}]}

    def run_cli(self, raw, *, mode="burst", suffix=".json", expected_code=0):
        original = copy.deepcopy(raw)
        expected_summary = report.summarize(raw)
        stdout, stderr = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / ("run" + suffix)) if suffix else None
            argv = ["run", "http://fixture.invalid", "--model", "fixture-model", "--mode", mode,
                    "--concurrency", "1", "--warmup", "0", "--duration", "4", "--batches", "2",
                    "--max-tokens", "8", "--seed", "7", "--label", "fixture", "--save-requests",
                    "--extra-body", '{"seed":7}']
            if path:
                argv += ["--output", path]
            with patch.object(cli.load, "run", return_value=raw) as run, \
                    patch.object(cli.prompts, "load", return_value=[{"id": "fixture", "text": "Hi"}]), \
                    patch("urllib.request.urlopen", side_effect=AssertionError("no network in fixture")), \
                    redirect_stdout(stdout), redirect_stderr(stderr):
                code = cli.main(argv)
            self.assertEqual(code, expected_code)
            run.assert_called_once()
            self.assertEqual(run.call_args.args[:2], ("http://fixture.invalid", "fixture-model"))
            self.assertEqual(run.call_args.args[3].mode, mode)
            self.assertEqual(run.call_args.kwargs, {"vllm": True, "extra_body": {"seed": 7}})
            if path:
                saved = cli.read_json(path)
                self.assertEqual(saved["summary"], expected_summary)
                self.assertEqual(saved["workload"]["extra_body"], {"seed": 7})
                self.assertEqual(saved["workload"]["seed"], 7)
                self.assertEqual(saved["requests"][0]["events"], [[1.0, 2], [3.0, 8]])
        self.assertEqual(raw, original)
        line = json.loads(stdout.getvalue())  # Exactly one parseable JSON summary line.
        self.assertEqual(line["mode"], mode)
        self.assertEqual(line["failed"], expected_summary["requests"]["failed"])
        self.assertEqual(line["foreign_requests"], expected_summary["requests"]["foreign"])
        self.assertEqual(line["tps_mean"], expected_summary.get("output_tps", {}).get("mean"))
        self.assertEqual(line["windows"], expected_summary.get("output_tps", {}).get("n"))
        self.assertEqual(line["ci95_pct"], expected_summary.get("output_tps", {}).get("ci95_pct"))
        self.assertEqual(line["ttft_p50_s"], expected_summary["latency"]["ttft_s"].get("p50"))
        self.assertEqual(line["tpot_p50_ms"], expected_summary["latency"]["tpot_ms"].get("p50"))
        return line, stderr.getvalue()

    def test_burst_scalars_print_without_changing_saved_metrics(self):
        for suffix in (".json", ".json.gz", None):
            with self.subTest(suffix=suffix):
                line, stderr = self.run_cli(self.raw_run(), suffix=suffix)
                self.assertEqual(line["steps_per_s"], 1.0)
                self.assertEqual(line["tokens_per_step"], 2.0)
                self.assertEqual(line["prefix_cache_hit_rate"], 0.5)
                self.assertEqual(line["tps_mean"], 4.0)
                self.assertEqual(line["windows"], 1)
                self.assertIsNone(line["ci95_pct"])
                self.assertEqual(stderr, "")

    def test_burst_zero_ratios_are_not_missing(self):
        raw = self.raw_run()
        raw["scrapes"][1]["generation_tokens"] = raw["scrapes"][0]["generation_tokens"]
        raw["scrapes"][1]["prefix_cache_hits"] = raw["scrapes"][0]["prefix_cache_hits"]
        line, _ = self.run_cli(raw)
        self.assertEqual(line["steps_per_s"], 1.0)
        self.assertEqual(line["tokens_per_step"], 0.0)
        self.assertEqual(line["prefix_cache_hit_rate"], 0.0)

    def test_missing_server_counters_remain_null(self):
        for scrapes in ([None, None], [{}, {}]):
            with self.subTest(scrapes=scrapes):
                raw = self.raw_run()
                raw["scrapes"] = scrapes
                line, _ = self.run_cli(raw)
                for key in ("steps_per_s", "tokens_per_step", "prefix_cache_hit_rate"):
                    self.assertIsNone(line[key])

    def test_prefix_scalar_without_step_counters(self):
        raw = self.raw_run()
        for scrape in raw["scrapes"]:
            del scrape["steps"]
        line, _ = self.run_cli(raw)
        self.assertIsNone(line["steps_per_s"])
        self.assertIsNone(line["tokens_per_step"])
        self.assertEqual(line["prefix_cache_hit_rate"], 0.5)

    def test_missing_prefix_ratio_remains_null(self):
        raw = self.raw_run()
        for scrape in raw["scrapes"]:
            del scrape["prefix_cache_queries"]
        line, _ = self.run_cli(raw)
        self.assertEqual(line["steps_per_s"], 1.0)
        self.assertEqual(line["tokens_per_step"], 2.0)
        self.assertIsNone(line["prefix_cache_hit_rate"])

    def test_closed_and_poisson_distribution_means_are_unchanged(self):
        for mode in ("closed", "poisson"):
            with self.subTest(mode=mode):
                raw = self.raw_run()
                raw["window"] = [100.0, 104.0]
                raw["boundaries"] = raw["scrape_times"] = [100.0, 102.0, 104.0]
                raw["scrapes"].insert(1, {"steps": 11, "generation_tokens": 22,
                                         "prefix_cache_queries": 32, "prefix_cache_hits": 15})
                line, _ = self.run_cli(raw, mode=mode)
                self.assertEqual(line["steps_per_s"], 1.0)
                self.assertEqual(line["tokens_per_step"], 2.0)
                # Mean of per-window ratios, not the burst's whole-window ratio.
                self.assertEqual(line["prefix_cache_hit_rate"], 0.3125)
                self.assertEqual(line["windows"], 2)

    def test_failed_request_keeps_exit_one_and_receipt(self):
        raw = self.raw_run()
        raw["records"].append({"sent": 100.0, "first": None, "end": 103.5, "events": [],
                               "completion_tokens": 0, "token_times": "exact", "error": "fixture failure"})
        line, stderr = self.run_cli(raw, expected_code=1)
        self.assertEqual(line["failed"], 1)
        self.assertEqual(stderr, "")

    def test_foreign_requests_keep_exit_three_and_warning(self):
        raw = self.raw_run()
        raw["fences"]["scrapes"][1]["requests_finished"] += 2
        line, stderr = self.run_cli(raw, expected_code=3)
        self.assertEqual(line["foreign_requests"], 2)
        self.assertIn("2 foreign requests finished during the run", stderr)


if __name__ == "__main__":
    unittest.main()
