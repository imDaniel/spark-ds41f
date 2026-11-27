"""Remote clock skew cannot reject fresh, cool site qualification samples."""
import json
from pathlib import Path
import runpy
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
THERMAL = runpy.run_path(str(ROOT / "experiments/2026-11-28-1126-tp4-qualification/thermal.py"))
NAMES = [f"spark{n:02}" for n in range(1, 5)]


def rows():
    return [dict(node=name, at=100.08, hottest_c=43,
                 query_started_monotonic=10, received_monotonic=10.4)
            for name in NAMES]


class SiteThermalTests(unittest.TestCase):
    def test_remote_wall_clock_skew_does_not_change_freshness(self):
        for remote in (100.08, 3_600, -3_600):
            samples = rows()
            for row in samples:
                row["at"] = remote
            self.assertTrue(THERMAL["samples_ready"](samples, NAMES, 10.5))

    def test_stale_hot_future_missing_and_duplicate_samples_refuse(self):
        mutations = [lambda r: r[0].update(hottest_c=55),
                     lambda r: r[0].update(hottest_c=float("nan")),
                     lambda r: r[0].update(query_started_monotonic=-10),
                     lambda r: r[0].update(query_started_monotonic=11),
                     lambda r: r[0].update(received_monotonic=11),
                     lambda r: r.pop(),
                     lambda r: r[0].update(node=r[1]["node"])]
        for change in mutations:
            samples = rows()
            change(samples)
            self.assertFalse(THERMAL["samples_ready"](samples, NAMES, 10.5))

    def test_query_records_local_bounds_and_keeps_ssh_timeout(self):
        sample = THERMAL["sample"]
        result = types.SimpleNamespace(stdout=json.dumps(dict(at=100.48, hottest_c=43)))
        with mock.patch.object(THERMAL["subprocess"], "run", return_value=result) as run, \
                mock.patch.object(THERMAL["time"], "monotonic", side_effect=[10, 10.4]), \
                mock.patch.object(THERMAL["time"], "time", return_value=100.4):
            row = sample(("spark03", "10.42.0.3"))
        self.assertEqual(run.call_args.kwargs["timeout"], 15)
        self.assertLess(row["received_at"] - row["at"], 0)
        self.assertEqual(row["query_started_monotonic"], 10)
        self.assertEqual(row["received_monotonic"], 10.4)

    def test_gate_keeps_temperature_threshold_and_records_every_poll(self):
        gate = THERMAL["gate"]
        hot, cool = rows(), rows()
        hot[0]["hottest_c"] = 55
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.dict(gate.__globals__, read_samples=mock.Mock(side_effect=[hot, cool])), \
                mock.patch.object(THERMAL["time"], "monotonic", side_effect=[0, 10.5, 15.5]), \
                mock.patch.object(THERMAL["time"], "sleep") as sleep:
            result = gate({"site_nodes": dict.fromkeys(NAMES)}, Path(temporary))
            self.assertEqual(result, cool)
            self.assertEqual(len((Path(temporary) / "thermal.jsonl").read_text().splitlines()), 2)
        sleep.assert_called_once_with(5)

    def test_gate_keeps_ten_minute_timeout(self):
        gate = THERMAL["gate"]
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.dict(gate.__globals__, read_samples=mock.Mock(return_value=rows())), \
                mock.patch.object(THERMAL["time"], "monotonic", side_effect=[0, 600]), \
                mock.patch.object(THERMAL["time"], "sleep") as sleep:
            with self.assertRaisesRegex(ValueError, "Four-node thermal baseline unavailable"):
                gate({"site_nodes": dict.fromkeys(NAMES)}, Path(temporary))
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
