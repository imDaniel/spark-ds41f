"""Offline promotion and rollback contracts for the four-rank site transaction."""
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

EXPERIMENT = Path(__file__).resolve().parents[1] / "experiments/2026-11-28-pr60-tp4"
with mock.patch.dict(sys.modules), mock.patch.object(sys, "path", [str(EXPERIMENT), *sys.path]):
    for name in ("promote", "window", "run", "qualify", "gateway", "ingress", "site_adapter", "window_executor"):
        sys.modules.pop(name, None)
    import promote as p

w = p.w
ID = "aa8baa8a-841f-411a-a00a-99b802d58d13"
REV = "d" * 40


class SitePromotionTests(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.enter = stack.enter_context
        self.op = Path(self.enter(tempfile.TemporaryDirectory()))
        for key, value in {"OP": self.op, "HOLD": self.op / "test-hold", "OWNER": "pr60-tp4-promotion"}.items():
            self.enter(mock.patch.object(w, key, value))
        self.h = dict(holder=w.OWNER, window_id=ID, phase="awaiting_gateway", target="promoted",
                      challenge="ab" * 16, reopened_at=1, release_revision=REV,
                      direct_request_id="backend-new", success_before_gateway=1, ranks={"spark01": "new"})
        w.atomic_json(w.HOLD, self.h)
        self.m(w.site, "ingress_stage", return_value="open")
        self.m(w, "expired", return_value=False)
        self.m(w.gateway, "validate_receipt", return_value=True)
        self.m(p, "probe_controls", return_value={"nodes": {"1": {"valid": True, "running": True, "memguard": "active"}}})
        self.m(p, "verify_release", return_value=self.h["ranks"].copy())
        self.m(w, "success_count", return_value=3)
        w.atomic_json(self.op / ("gateway-" + self.h["challenge"] + ".json"), {"test": "new"})

    def m(self, instance, name, **kw):
        return self.enter(mock.patch.object(instance, name, **kw))

    def test_publish_requires_same_ranks_and_both_gateway_arrivals(self):
        p.verify_release.return_value = {"spark01": "restarted"}
        with self.assertRaisesRegex(RuntimeError, "rank restarted"):
            p.finalize(ID)
        p.verify_release.return_value = self.h["ranks"]
        w.success_count.return_value = 2
        with self.assertRaisesRegex(RuntimeError, "both gateway calls"):
            p.finalize(ID)
        self.assertTrue(w.HOLD.is_file())
        self.assertFalse((self.op / "promoted.json").exists())
        w.success_count.return_value = 3
        receipt = p.finalize(ID)
        w.gateway.validate_receipt.assert_called_with({"test": "new"}, ID, self.h["challenge"], 1)
        self.assertEqual((receipt["revision"], receipt["image"]), (REV, p.IMAGE))
        self.assertFalse(w.HOLD.exists())

    def test_watchdog_finishes_committed_decision_after_crash_before_lease_release(self):
        bound = w.owned
        def interrupt_after_publication(ident):
            if (self.op / "promoted.json").exists():
                raise InterruptedError("injected loss between receipt and lease release")
            return bound(ident)
        with mock.patch.object(w, "owned", side_effect=interrupt_after_publication):
            with self.assertRaises(InterruptedError):
                p.finalize(ID)
        self.assertEqual(w.stored()["phase"], "finalizing")
        w.expired.return_value = True
        restore = self.m(w, "restore_service")
        self.assertEqual(p.recover(ID, stale=True)["target"], "promoted")
        restore.assert_not_called()
        self.assertFalse(w.HOLD.exists())
        self.assertFalse((self.op / "restored.json").exists())

    def test_failed_promoted_receipt_does_not_mask_rollback_gateway(self):
        self.h.update(challenge="cd" * 16, target="restored", ranks=None)
        w.atomic_json(w.HOLD, self.h)
        fleet = [{"production": True, "production_guard": True, "candidate": False, "candidate_guard": False}] * 4
        self.m(w, "fleet", return_value=fleet)
        with self.assertRaises(FileNotFoundError):
            p.finalize(ID)
        w.atomic_json(self.op / ("gateway-" + self.h["challenge"] + ".json"), {"test": "old"})
        self.assertEqual(p.finalize(ID)["revision"], w.REV)
        w.gateway.validate_receipt.assert_called_with({"test": "old"}, ID, self.h["challenge"], 1)
        self.assertFalse((self.op / "promoted.json").exists())

    def test_stale_controller_is_fenced_before_controls_and_old_service_return(self):
        self.h.update(phase="rollback_requested")
        w.atomic_json(w.HOLD, self.h)
        (self.op / "control-adoption.json").write_text("{}")
        order = []
        self.m(p.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout="active\n", stderr=""))
        self.m(w, "cmd", side_effect=lambda *a, **k: order.append("stop-unit"))
        self.m(p, "controls", side_effect=lambda window, action: order.append(action))
        self.m(w, "restore_service", side_effect=lambda h: order.append("restore-engine") or "reply")
        self.m(p, "offer_gateway", side_effect=lambda *a: order.append(a[1]))
        p.recover(ID, stale=True)
        self.assertEqual(order, ["stop-unit", "rollback", "restore-engine", "restored"])
        w.cmd.assert_called_once_with(["sudo", "-n", "systemctl", "stop", f"{w.PREFIX}-{ID}-controller"], timeout=180)

    def test_healthy_lease_and_foreign_lease_are_never_recovered_by_watchdog(self):
        restore = self.m(w, "restore_service")
        p.recover(ID, stale=True)
        self.h.update(holder="other")
        w.atomic_json(w.HOLD, self.h)
        with self.assertRaisesRegex(RuntimeError, "Another holder"):
            p.recover(ID, stale=True)
        restore.assert_not_called()

    def test_controller_identity_must_match_own_transient_unit(self):
        output = self.m(p.run, "output", return_value=str(os.getpid() + 1))
        with self.assertRaisesRegex(RuntimeError, "main process"):
            p.execute(ID)
        output.return_value = str(os.getpid())
        p.require_unit(ID, "controller")
        self.assertEqual(output.call_args.args[0][2], f"{w.PREFIX}-{ID}-controller.service")


if __name__ == "__main__":
    unittest.main()
