"""Offline lease, partial-rank recovery and two-phase gateway acceptance faults."""
import contextlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import window


class QualificationWindowTest(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.enter = stack.enter_context
        self.op = Path(self.enter(tempfile.TemporaryDirectory()))
        self.enter(patch.object(window, "OP", self.op))
        self.enter(patch.object(window, "HOLD", self.op / "hold"))
        self.enter(patch.object(window, "boot", return_value="boot"))
        self.window = "24a46105-0bc6-4939-91ee-5eb9ce2b5b38"
        self.h = dict(holder=window.OWNER, window_id=self.window, phase="testing", since_epoch=9,
                      boot_id="boot", beat_monotonic=time.monotonic(),
                      work_deadline_monotonic=time.monotonic() + window.WORK_BUDGET)
        window.atomic_json(window.HOLD, self.h)

    def mock(self, name, **kwargs):
        return self.enter(patch.object(window, name, **kwargs))

    def test_other_holds_and_expired_leases(self):
        self.h["holder"] = "another"
        window.atomic_json(window.HOLD, self.h)
        with self.assertRaisesRegex(RuntimeError, "Another"):
            window.restore(self.window)
        self.h.update(holder=window.OWNER, beat_monotonic=time.monotonic() - 901)
        window.atomic_json(window.HOLD, self.h)
        with self.assertRaisesRegex(RuntimeError, "priority"):
            window.heartbeat(self.window)
        fresh = window.stored()
        self.assertEqual(self.h, fresh)

    def test_absolute_work_deadline_enforces_reserved_recovery_time(self):
        self.h["work_deadline_monotonic"] = time.monotonic() - 1
        window.atomic_json(window.HOLD, self.h)
        with self.assertRaisesRegex(RuntimeError, "recovery"):
            window.heartbeat(self.window)
        self.assertLess(time.monotonic() - self.h["beat_monotonic"], 10)

    def test_live_watchdog_requires_pid_birth_and_recent_monotonic_tick(self):
        ready = dict(window_id=self.window, pid=os.getpid() + 1, birth="birth", boot_id="boot",
                     beat_monotonic=time.monotonic())
        self.mock("stamp", return_value="birth")
        for delta in ({"pid": os.getpid()}, {"birth": "wrong"}, {"beat_monotonic": time.monotonic() - 50},
                      {"boot_id": "old"}, {"window_id": "other"}):
            window.atomic_json(self.op / f"watchdog-{self.window}.json", {**ready, **delta})
            with self.subTest(delta=delta), self.assertRaisesRegex(RuntimeError, "watchdog"):
                window.require_watchdog(self.window)
        window.atomic_json(self.op / f"watchdog-{self.window}.json", ready)
        window.require_watchdog(self.window)

    def test_lease_lost_after_plan_blocks_apply(self):
        plan = subprocess.CompletedProcess([], 0, stdout="live: stopped\nshape:   unchanged\n")
        spark = self.mock("spark", return_value=plan)
        def revoke():
            raise RuntimeError("lease expired")
        with self.assertRaisesRegex(RuntimeError, "lease expired"):
            window.transition(window.ROOT, window.CFG, "start", "--replace", pre_apply=revoke)
        self.assertEqual(spark.call_count, 1)

    def test_peer_conflict_or_watchdog_death_during_drain(self):
        self.mock("check_peer_reservations", side_effect=[RuntimeError("peer hold"), None])
        self.mock("require_watchdog", side_effect=[None, RuntimeError("watchdog dead")])
        drain = self.enter(patch.object(window.site, "block"))
        self.enter(patch.object(window.site, "ingress_stage", return_value="open"))
        undo = self.enter(patch.object(window.site, "restore_ingress"))
        with self.assertRaisesRegex(RuntimeError, "peer hold"):
            window.block_ingress(self.window)
        drain.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, "watchdog dead"):
            window.block_ingress(self.window)
        undo.assert_called_once()

    def test_stop_requires_gate_and_does_not_cross_recovery(self):
        self.mock("require_watchdog")
        self.mock("check_peer_reservations")
        self.enter(patch.object(window.site, "ingress_stage", return_value="open"))
        transition = self.mock("transition")
        with self.assertRaisesRegex(RuntimeError, "open ingress"):
            window.stop_production(self.window)
        self.h.update(phase="awaiting_gateway")
        window.atomic_json(window.HOLD, self.h)
        with self.assertRaisesRegex(RuntimeError, "priority"):
            window.stop_production(self.window)
        transition.assert_not_called()

    def test_partial_candidate_and_production_are_stopped_as_four_before_restore(self):
        prod = dict(production=True, production_guard=True, candidate=False, candidate_guard=False)
        none = dict(production=False, production_guard=False, candidate=False, candidate_guard=False)
        orphan = {**none, "candidate_guard": True}
        cand = {**orphan, "candidate": True}
        self.mock("stop_jobs")
        self.mock("fleet", side_effect=[[prod, none, cand, orphan], [prod, none, none, none], [none] * 4])
        self.enter(patch.object(window.site, "ingress_stage", return_value="blocked"))
        change = self.mock("transition")
        self.mock("spark")
        self.mock("production_probe")
        self.mock("direct_probe", return_value="request")
        self.assertEqual(window.restore_service(self.h), "request")
        self.assertEqual([c.args for c in change.call_args_list], [
            (window.ROOT, window.CFG, "stop", "--parallel"),
            (window.PROD, "config/cluster.json", "stop", "--parallel"),
            (window.PROD, "config/cluster.json", "start", "--replace")])

    def test_unreachable_node_or_direct_smoke_failure_retains_hold_and_gate(self):
        undo = self.enter(patch.object(window.site, "restore_ingress"))
        for message in ("remote unreachable", "wrong direct output"):
            with self.subTest(message=message), patch.object(window, "restore_service", side_effect=RuntimeError(message)):
                with self.assertRaisesRegex(RuntimeError, message):
                    window.restore(self.window)
                self.assertEqual(window.stored()["phase"], "recovery_failed")
                window.restore(self.window, stale=True)
        undo.assert_not_called()

    def test_stale_watchdog_rechecks_current_lease_and_resumes_abandoned_recovery(self):
        service = self.mock("restore_service", return_value="chat-id")
        self.mock("success_count", return_value=6)
        self.enter(patch.object(window.site, "restore_ingress"))
        self.enter(patch.object(window.site, "ingress_stage", return_value="open"))
        window.restore(self.window, stale=True)
        service.assert_not_called()
        self.h["phase"] = "recovering"
        window.atomic_json(window.HOLD, self.h)
        state = window.restore(self.window, stale=True)
        service.assert_called_once()
        self.assertEqual(state["phase"], "awaiting_gateway")
        self.assertEqual(state["success_before_gateway"], 6)
        self.assertEqual(window.restore(self.window, stale=True)["challenge"], state["challenge"])
        service.assert_called_once()

    def test_gateway_absent_invalid_or_not_seen_retains_hold(self):
        self.h.update(phase="awaiting_gateway", challenge="a" * 32, reopened_at=time.time(),
                      direct_request_id="chat-test", success_before_gateway=3)
        window.atomic_json(window.HOLD, self.h)
        self.enter(patch.object(window.site, "ingress_stage", return_value="open"))
        self.mock("production_probe")
        with self.assertRaises(FileNotFoundError):
            window.finalize(self.window)
        window.atomic_json(self.op / f"gateway-{self.window}.json", {})
        with self.assertRaises(ValueError):
            window.finalize(self.window)
        self.enter(patch.object(window.gateway, "validate_receipt", return_value=True))
        count = self.mock("success_count", side_effect=[4, 5])
        with self.assertRaisesRegex(RuntimeError, "counter"):
            window.finalize(self.window)
        self.assertTrue(window.HOLD.exists())
        window.finalize(self.window)
        self.assertEqual(count.call_count, 2)
        self.assertFalse(window.HOLD.exists())
        self.assertEqual(json.loads((self.op / f"recovered-{self.window}.json").read_text())["revision"], window.REV)

    def test_explicit_restore_after_gateway_wait_rechecks_a_failed_probe(self):
        self.h.update(phase="awaiting_gateway", challenge="previous")
        window.atomic_json(window.HOLD, self.h)
        self.mock("production_probe", side_effect=subprocess.CalledProcessError(1, ["probe"]))
        service = self.mock("restore_service", return_value="request")
        self.mock("success_count", return_value=7)
        self.enter(patch.object(window.site, "restore_ingress"))
        self.enter(patch.object(window.site, "ingress_stage", return_value="open"))
        state = window.restore(self.window)
        service.assert_called_once()
        self.assertEqual(state["phase"], "awaiting_gateway")
        self.assertNotEqual(state["challenge"], "previous")

    def test_firewall_check_fails_closed_when_state_cannot_be_read(self):
        missing = subprocess.CompletedProcess([], 1, "", "iptables: Bad rule (does a matching rule exist in that chain?).\n")
        check = self.enter(patch.object(window.site.subprocess, "run", return_value=missing))
        self.assertEqual(window.site.ingress_stage(self.h), "open")
        for status, error in ((1, "sudo: a password is required"), (4, "xtables lock busy"), (2, "syntax error")):
            with self.subTest(status=status):
                check.side_effect = [missing, subprocess.CompletedProcess([], status, "", error)]
                with self.assertRaisesRegex(RuntimeError, "Cannot inspect"):
                    window.site.ingress_stage(self.h)

    def test_missing_component_requires_successful_docker_inventory(self):
        for rc, listed in ((0, ""), (1, ""), (0, "ds41f-pr60-component-check")):
            def status(args, **kwargs):
                if args[0] == "systemctl":
                    return subprocess.CompletedProcess(args, 3, "inactive\n", "")
                if args[1] == "inspect":
                    return subprocess.CompletedProcess(args, 1, "", "error: no such object")
                self.assertIn("name=^/ds41f-pr60-component-check$", args)
                return subprocess.CompletedProcess(args, rc, listed, "")
            with self.subTest(rc=rc, listed=listed), patch.object(window.subprocess, "run", side_effect=status):
                if rc == 0 and not listed:
                    window.stop_jobs(self.h)
                else:
                    with self.assertRaisesRegex(RuntimeError, "Cannot inspect"):
                        window.stop_jobs(self.h)

    def test_component_process_cleanup_is_uuid_and_image_bound(self):
        (self.op / "candidate-image.txt").write_text("sha256:tested 1024\n")
        component = dict(State={"Running": True}, Config={"Labels": {"local.spark-ds41f.qualification.window": self.window}},
                         Id="owned", Image="sha256:tested")
        def status(args, **kwargs):
            if args[0] == "systemctl":
                return subprocess.CompletedProcess(args, 3, "inactive\n", "")
            return subprocess.CompletedProcess(args, 0, json.dumps([component]), "")
        stop = self.mock("cmd")
        self.enter(patch.object(window.subprocess, "run", side_effect=status))
        window.stop_jobs(self.h)
        stop.assert_called_once_with(["docker", "stop", "--timeout", "30", "owned"], timeout=60)
        component["Image"] = "different"
        with self.assertRaisesRegex(RuntimeError, "Unowned"):
            window.stop_jobs(self.h)
        self.assertEqual(stop.call_count, 1)


if __name__ == "__main__":
    unittest.main()
