import contextlib
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import window


class LeaseTest(unittest.TestCase):
    def setUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(tmp)
        self.enterContext(patch.object(window, "OP", self.root))
        self.enterContext(patch.object(window, "HOLD", self.root / "hold"))
        self.enterContext(patch.object(window, "boot", return_value="boot"))
        self.set_hold(window.OWNER)

    def set_hold(self, holder):
        window.atomic_json(window.HOLD, {"holder":holder, "window_id":"mine", "boot_id":"boot",
                           "phase":"testing", "beat_monotonic":time.monotonic()})

    def test_unrelated_hold_is_untouched(self):
        self.set_hold("different")
        with self.assertRaises(RuntimeError):
            window.heartbeat("mine", "build")
        with self.assertRaises(RuntimeError):
            window.restore("mine")
        self.assertEqual(window.stored()["holder"], "different")

    def test_watchdog_rechecks_after_refresh(self):
        window.heartbeat("mine", "build")
        with patch.object(window, "four_inspect", side_effect=AssertionError):
            window.restore("mine", stale=True)
        self.assertEqual(window.stored()["phase"], "build")

    def test_recovering_window_cannot_be_revived(self):
        h = window.stored()
        h["phase"] = "recovering"
        window.atomic_json(window.HOLD, h)
        with self.assertRaises(RuntimeError):
            window.heartbeat("mine", "testing")

    def test_partial_launch_stopped_before_retained_start(self):
        commands = []
        with contextlib.ExitStack() as ctx:
            ctx.enter_context(patch.object(window.subprocess, "run", return_value=type("State", (), {"stdout":"inactive"})()))
            ctx.enter_context(patch.object(window, "four_inspect", side_effect=lambda n: ["ds41f-1126-attention"] if n == 2 else []))
            ctx.enter_context(patch.object(window, "spark", side_effect=lambda *args, **kwargs: commands.append(args)))
            ctx.enter_context(patch.object(window.site, "restore_ingress"))
            window.restore("mine")
        self.assertEqual(commands[1][2:], ("cluster","stop","--apply"))
        self.assertEqual(commands[3][2:], ("cluster","start","--replace","--apply"))
        self.assertEqual(commands[4][2:], ("doctor","--live"))
        self.assertFalse(window.HOLD.exists())

    def test_gpu_or_remote_failure_preserves_hold(self):
        with patch.object(window.subprocess, "run", return_value=type("State", (), {"stdout":"inactive"})()), \
             patch.object(window, "four_inspect", side_effect=RuntimeError("GPU fault")), \
             patch.object(window, "spark") as spark, self.assertRaises(RuntimeError):
            window.restore("mine")
        spark.assert_not_called()
        self.assertTrue(window.HOLD.exists())


if __name__ == "__main__":
    unittest.main()
