import unittest
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from trial_executor import Executor, TrialExecutor


class OwnerTrialContracts(unittest.TestCase):
    def executor(self, directory, phase="owner_trial"):
        x = object.__new__(TrialExecutor)
        x.hold = Path(directory) / "hold.json"
        x.hold.write_text("{}")
        x.state_path = Path(directory) / "state.json"
        x.state_path.write_text("{}")
        x.directory = Path(directory)
        x.state = {"phase": phase, "original_ingress": {"blocked": False}}
        x.m = {"retain_candidate_for_owner": True}
        x.authorized = Mock()
        x.lock = Mock(return_value=nullcontext())
        x.load_owned = Mock()
        x.heartbeat = Mock()
        x.owns_hold = Mock(return_value={"expected_end": None})
        return x

    def test_accepted_owner_trial_never_invokes_automatic_recovery(self):
        with (
            TemporaryDirectory() as directory,
            patch.object(Executor, "watch_once") as base,
        ):
            x = self.executor(directory)
            self.assertEqual(x.watch_once(), "owner-trial")
            x.heartbeat.assert_called_once()
            base.assert_not_called()

    def test_testing_phase_retains_original_watchdog_recovery(self):
        with (
            TemporaryDirectory() as directory,
            patch.object(Executor, "watch_once", return_value="restored") as base,
        ):
            x = self.executor(directory, "testing")
            self.assertEqual(x.watch_once(), "restored")
            base.assert_called_once()

    def test_incomplete_measurement_cannot_enter_owner_trial(self):
        with TemporaryDirectory() as directory:
            x = self.executor(directory, "testing")
            with (
                patch(
                    "trial_executor.read", return_value={"status": "failed", "runs": []}
                ),
                self.assertRaisesRegex(RuntimeError, "did not complete"),
            ):
                x.handoff({})
            self.assertEqual(x.state["phase"], "testing")

    def test_failed_gateway_probe_cannot_disable_recovery(self):
        with TemporaryDirectory() as directory:
            x = self.executor(directory, "testing")
            x.active = Mock(return_value=False)
            x.observe = Mock(
                return_value={
                    "nodes": [
                        {
                            "services": {
                                "s-loaded": {"running": True, "guard_active": True}
                            }
                        }
                    ]
                    * 4,
                    "ingress": x.state["original_ingress"],
                }
            )
            x.run = Mock(side_effect=["private passed", RuntimeError("gateway failed")])
            x.save, x.hook = Mock(), Mock()
            with (
                patch(
                    "trial_executor.read",
                    return_value={"status": "passed", "runs": [{}] * 6},
                ),
                self.assertRaisesRegex(RuntimeError, "gateway failed"),
            ):
                x.handoff({})
            self.assertEqual(x.state["phase"], "trial_handoff")

    def test_recovery_drains_open_ingress_before_stopping_service(self):
        with (
            TemporaryDirectory() as directory,
            patch.object(Executor, "_recover", return_value="restored") as base,
        ):
            x = self.executor(directory)
            x.observe = Mock(return_value={"ingress": {"blocked": False}})
            x.hook = Mock()
            self.assertEqual(x._recover(), "restored")
            x.hook.assert_called_once_with("block_ingress", recovering=True)
            base.assert_called_once()


if __name__ == "__main__":
    unittest.main()
