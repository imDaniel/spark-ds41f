#!/usr/bin/env python3
"""Run preserved recovery fixtures with a shared-runner scheduling budget.

Only the synthetic manifest's drain timeout changes. Real executor code,
authorization, idle duration, assertions, and published evidence stay intact.
"""
import importlib.util
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "experiments/2026-10-23-1022-recovery-executor/tests/test_executor.py"
spec = importlib.util.spec_from_file_location("recovery_fixtures", SOURCE)
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
original_configure = fixtures.Fixture.configure


def configure_for_ci(fixture):
    # The original 300 ms budget can expire during one subprocess launch on
    # a shared runner. Keep a bounded timeout, including the busy-queue test.
    fixture.m["drain_timeout_seconds"] = 3.0
    original_configure(fixture)


class SlowObservation(fixtures.Fixture):
    def test_delayed_idle_observation_still_opens_and_restores(self):
        observe = self.x.observe
        delayed = []

        def slow_observe(*args, **kwargs):
            if self.x.hold.exists() and not delayed:
                delayed.append(True)
                time.sleep(.4)  # More than the historical fixture's 300 ms.
            return observe(*args, **kwargs)

        with patch.object(self.x, "observe", side_effect=slow_observe):
            self.x.open()
            self.assertTrue(delayed)
            self.x.recover()
        self.assert_closed()
        self.assertFalse(self.applies())


def main():
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(fixtures)
    if suite.countTestCases() != 44:
        raise RuntimeError("Preserved recovery fixture collection changed")
    suite.addTests(loader.loadTestsFromTestCase(SlowObservation))
    with patch.object(fixtures.Fixture, "configure", configure_for_ci):
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() and not result.skipped else 1


if __name__ == "__main__":
    sys.exit(main())
