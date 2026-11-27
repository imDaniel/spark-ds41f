import contextlib
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import window

CHECK_PEERS = window.check_peer_reservations


class LeaseTest(unittest.TestCase):
    def setUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(tmp)
        self.enterContext(patch.object(window, "OP", self.root))
        self.enterContext(patch.object(window, "HOLD", self.root / "hold"))
        self.enterContext(patch.object(window, "boot", return_value="boot"))
        self.peers = self.enterContext(patch.object(window, "check_peer_reservations"))
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

    def test_peer_reservation_prevents_ingress_drain(self):
        self.peers.side_effect = RuntimeError("Spark02 has a reservation")
        with patch.object(window.site, "block") as drain:
            with self.assertRaisesRegex(RuntimeError, "Spark02"):
                window.block_ingress("mine")
        drain.assert_not_called()

    def test_peer_inventory_checks_all_workers_and_rejects_reservations(self):
        def answer(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, stdout="[]")
        with patch.object(window, "cmd", side_effect=answer) as remote:
            CHECK_PEERS()
        self.assertEqual({call.args[0][-2] for call in remote.call_args_list},
                         {"admin@10.42.0.2", "admin@10.42.0.3", "admin@10.42.0.4"})
        with patch.object(window, "cmd", return_value=subprocess.CompletedProcess(
                [], 0, stdout='["spark-hold.json"]')):
            with self.assertRaisesRegex(RuntimeError, "reservation"):
                CHECK_PEERS()

    def test_watchdog_rechecks_after_refresh(self):
        window.heartbeat("mine", "build")
        with patch.object(window, "four_inspect", side_effect=AssertionError):
            window.restore("mine", stale=True)
        self.assertEqual(window.stored()["phase"], "build")

    def test_dead_watchdog_receipt_cannot_drain(self):
        receipt = self.root / "watchdog-mine.json"
        script = (
            "import sys,window; from pathlib import Path; "
            "window.OP=Path(sys.argv[1]); window.HOLD=window.OP/'hold'; "
            "window.boot=lambda:'boot'; window.watchdog('mine')"
        )
        with subprocess.Popen([sys.executable, "-c", script, str(self.root)], cwd=window.HERE) as child:
            try:
                deadline = time.monotonic() + 5
                while not receipt.exists() and child.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                window.require_watchdog("mine")
            finally:
                child.kill()
        with patch.object(window.site, "block") as drain, self.assertRaisesRegex(RuntimeError, "watchdog"):
            window.block_ingress("mine")
        drain.assert_not_called()

    def test_watchdog_rejects_wrong_owner_or_expired_identity(self):
        ready = {"window_id": "mine", "pid": 424242, "birth": "birth", "boot_id": "boot",
                 "beat_monotonic": time.monotonic()}
        cases = ({"window_id": "other"}, {"boot_id": "old"}, {"pid": os.getpid()},
                 {"birth": "reused-pid"}, {"birth": None}, {"beat_monotonic": time.monotonic() - 60})
        with patch.object(window, "stamp", return_value="birth"):
            for change in cases:
                with self.subTest(change=change):
                    window.atomic_json(self.root / "watchdog-mine.json", {**ready, **change})
                    with self.assertRaisesRegex(RuntimeError, "watchdog"):
                        window.require_watchdog("mine")

    def test_watchdog_loss_during_drain_only_undoes_new_gate(self):
        for stage in ("open", "blocked"):
            with self.subTest(stage=stage), contextlib.ExitStack() as ctx:
                ctx.enter_context(patch.object(window, "require_watchdog",
                                              side_effect=[None, RuntimeError("watchdog stopped")]))
                ctx.enter_context(patch.object(window.site, "ingress_stage", return_value=stage))
                drain = ctx.enter_context(patch.object(window.site, "block"))
                reopen = ctx.enter_context(patch.object(window.site, "restore_ingress"))
                with self.assertRaisesRegex(RuntimeError, "watchdog stopped"):
                    window.block_ingress("mine")
                drain.assert_called_once()
                self.assertEqual(reopen.call_count, int(stage == "open"))

    def test_refresh_race_keeps_watchdog_polling(self):
        hold = window.stored()
        hold["beat_monotonic"] -= 901
        window.atomic_json(window.HOLD, hold)
        def refresh(*args, **kwargs):
            # A healthy refresh had committed before the restorer obtained
            # the lock; the watchdog's original read was the older snapshot.
            self.set_hold(window.OWNER)
        with patch.object(window, "restore", side_effect=refresh) as recovery, \
             patch.object(window.time, "sleep", side_effect=[None, StopIteration]) as sleep:
            with self.assertRaises(StopIteration):
                window.watchdog("mine")
        recovery.assert_called_once_with("mine", stale=True)
        self.assertEqual(sleep.call_count, 2)

    def test_recovering_window_cannot_be_revived(self):
        h = window.stored()
        h["phase"] = "recovering"
        window.atomic_json(window.HOLD, h)
        with self.assertRaises(RuntimeError):
            window.heartbeat("mine", "testing")

    def test_expired_heartbeat_does_not_revive_lease(self):
        h = window.stored()
        h["beat_monotonic"] -= 901
        window.atomic_json(window.HOLD, h)
        with self.assertRaisesRegex(RuntimeError, "priority"):
            window.heartbeat("mine")
        self.assertEqual(h, window.stored())

    def test_abandoned_recovery_resumes_but_recorded_failure_requires_manual_retry(self):
        h = window.stored()
        h["phase"] = "recovering"
        window.atomic_json(window.HOLD, h)
        with patch.object(window, "restore_service", side_effect=RuntimeError("fault")) as service:
            with self.assertRaisesRegex(RuntimeError, "fault"):
                window.restore("mine", stale=True)
            self.assertEqual(window.stored()["phase"], "recovery_failed")
            window.restore("mine", stale=True)
            service.assert_called_once()
        with patch.object(window, "restore_service") as service:
            window.restore("mine")
            service.assert_called_once()
        self.assertFalse(window.HOLD.exists())

    def test_recovery_actions_are_under_one_exclusive_lock(self):
        def check_lock(hold):
            with (self.root / "window.lock").open("a") as other:
                with self.assertRaises(BlockingIOError):
                    window.fcntl.flock(other, window.fcntl.LOCK_EX | window.fcntl.LOCK_NB)
            self.assertEqual(window.stored()["phase"], "recovering")
        with patch.object(window, "restore_service", side_effect=check_lock):
            window.restore("mine")

    def test_reopen_and_finish_cannot_interrupt_failed_recovery(self):
        h = window.stored()
        h["phase"] = "recovery_failed"
        window.atomic_json(window.HOLD, h)
        for action in ("reopen", "finish"):
            with self.subTest(action=action), contextlib.ExitStack() as ctx:
                ctx.enter_context(patch.object(sys, "argv", ["window.py", action, "mine"]))
                reopen = ctx.enter_context(patch.object(window.site, "restore_ingress"))
                with self.assertRaisesRegex(RuntimeError, "priority"):
                    window.main()
                reopen.assert_not_called()
        self.assertEqual(h, window.stored())

    def test_partial_launch_stopped_before_retained_start(self):
        commands = []
        with contextlib.ExitStack() as ctx:
            ctx.enter_context(patch.object(window.subprocess, "run", return_value=type("State", (), {"stdout":"inactive"})()))
            ctx.enter_context(patch.object(window, "stop_owned_component"))
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
             patch.object(window, "stop_owned_component"), \
             patch.object(window, "spark") as spark, self.assertRaises(RuntimeError):
            window.restore("mine")
        spark.assert_not_called()
        self.assertTrue(window.HOLD.exists())

    def test_component_cleanup_requires_window_and_image_identity(self):
        (self.root / "candidate-image.txt").write_text("sha256:tested 1234\n")
        c = {"State": {"Running": True}, "Image": "sha256:tested", "Config": {"Labels": {
            "local.spark-ds41f.qualification.window": "mine"}}}
        import json
        with patch.object(window.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, stdout=json.dumps([c]), stderr="")), patch.object(window, "cmd") as stop:
            window.stop_owned_component({"window_id": "mine"})
            self.assertEqual(stop.call_args.args[0],
                             ["docker", "stop", "--timeout", "30", "ds41f-1126-component-check"])
        for change in ("image", "window"):
            with self.subTest(change=change):
                other = json.loads(json.dumps(c))
                if change == "image":
                    other["Image"] = "sha256:other"
                else:
                    other["Config"]["Labels"]["local.spark-ds41f.qualification.window"] = "other"
                with patch.object(window.subprocess, "run", return_value=subprocess.CompletedProcess(
                        [], 0, stdout=json.dumps([other]), stderr="")), \
                     patch.object(window, "cmd") as stop, self.assertRaises(RuntimeError):
                    window.stop_owned_component({"window_id": "mine"})
                stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
