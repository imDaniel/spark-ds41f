"""Offline checks for qualification command and telemetry failure propagation."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import observe


def snapshot():
    return {f"spark{n:02}": {"boot_id": f"boot{n}", "gpu_uuid": f"gpu{n}",
                             "vm": {"pagesize": 65536, "pswpin": 5, "pswpout": 7,
                                    "pgmajfault": 9},
                             "thermal": {"thermal_slowdown_us": 0, "power_cap_us": 10},
                             "gpu_faults": {"existing": "previous Xid"}}
            for n in range(1, 5)}


class ObserveTests(unittest.TestCase):
    def test_measures_pages_and_only_new_faults(self):
        before = snapshot()
        after = copy.deepcopy(before)
        after["spark03"]["vm"]["pswpout"] += 1
        after["spark03"]["gpu_faults"]["new"] = "new Xid"
        delta = observe.differences(before, after)
        self.assertEqual(delta["vm_delta"]["spark03"]["pswpout"], 1)
        self.assertEqual(delta["new_gpu_faults"]["spark03"], ["new Xid"])
        self.assertFalse(delta["new_gpu_faults"]["spark01"])

    def test_rejects_uncomparable_boundaries(self):
        before = snapshot()
        for field, change in [
            ("boot_id", "other"), ("gpu_uuid", "other"), ("gpu_faults", {}),
            ("vm", dict(before["spark01"]["vm"], pswpout=0)),
        ]:
            with self.subTest(field=field):
                after = copy.deepcopy(before)
                after["spark01"][field] = change
                with self.assertRaises(ValueError):
                    observe.differences(before, after)
        after = copy.deepcopy(before)
        del after["spark03"]
        with self.assertRaises(ValueError):
            observe.differences(before, after)

    def test_preserves_child_code_and_requires_final_telemetry(self):
        nodes = {"nodes": [{"rank": rank} for rank in range(4)]}
        watch = Mock()
        watch.close.return_value = watch.thermal_summary.return_value = {}
        for code, after, expected in [(0, snapshot(), 0), (23, snapshot(), 23),
                                      (0, RuntimeError("missing node"), 125),
                                      (23, RuntimeError("missing node"), 23)]:
            with self.subTest(code=code, expected=expected), tempfile.TemporaryDirectory() as temp:
                receipt = Path(temp) / "result.json"
                with patch.object(observe, "configuration", return_value=({}, nodes, {})), \
                        patch.object(observe, "collect", side_effect=[snapshot(), after]), \
                        patch.object(observe, "MemoryWatch", return_value=watch):
                    result = observe.monitor(receipt, [sys.executable, "-c", f"raise SystemExit({code})"])
                self.assertEqual(result, expected)
                record = json.loads(receipt.read_text())
                self.assertEqual(record["exit_code"], code)
                self.assertEqual(record["status"], "failed" if expected else "passed")


if __name__ == "__main__":
    unittest.main()
