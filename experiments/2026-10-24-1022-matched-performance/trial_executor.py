"""Explicit owner-trial handoff, keeping failure recovery before acceptance."""

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "2026-10-23-1022-recovery-executor"))
from window_executor import Busy, Executor, atomic_json, read, require


class TrialExecutor(Executor):
    def _recover(self):
        # An acceptance probe may fail after reopening the endpoint. Drain before
        # stopping any rank, including an explicit later owner-trial recovery.
        if not self.observe(recovering=True)["ingress"]["blocked"]:
            self.hook("block_ingress", recovering=True)
        return super()._recover()

    def handoff(self, files):
        with self.lock():
            self.load_owned()
            require(
                self.m.get("retain_candidate_for_owner") is True,
                "Owner-trial retention was not authorized",
            )
            require(
                self.state["phase"] == "testing", "No completed candidate test phase"
            )
            progress = read(self.directory / "performance/progress.json")
            require(
                progress["status"] == "passed" and len(progress["runs"]) == 6,
                "Performance window did not complete",
            )
            data = self.observe()
            require(
                all(
                    n["services"]["s-loaded"]["running"]
                    and n["services"]["s-loaded"]["guard_active"]
                    for n in data["nodes"]
                ),
                "Candidate fleet is not healthy",
            )
            require(
                not self.active(data, "retained"), "Original service overlaps candidate"
            )
            self.run(
                ["/usr/bin/python3", str(HERE / "trial_probe.py"), "private"],
                str(HERE),
                files,
                "owner-trial-private-probe",
                timeout=300,
            )
            self.state.update(phase="trial_handoff", ingress_reopened=True)
            self.save()
            self.hook("restore_ingress")
            require(
                self.observe()["ingress"] == self.state["original_ingress"],
                "Ingress did not reopen",
            )
            self.run(
                ["/usr/bin/python3", str(HERE / "trial_probe.py"), "gateway"],
                str(HERE),
                files,
                "owner-trial-gateway-probe",
                timeout=300,
            )
            self.state.update(phase="owner_trial", owner_trial_started_at=time.time())
            self.save()
            self.heartbeat()
            hold = self.owns_hold()
            hold.update(
                expected_end=None,
                rule="Owner personally testing final candidate through existing gateway; retain candidate until owner ends trial; no other GPU tests or service switches",
            )
            atomic_json(self.hold, hold)
            self.event(
                "owner_trial_handoff",
                candidate=self.m["services"]["s-loaded"]["identity"],
            )

    def watch_once(self):
        self.authorized()
        if self.hold.exists() and self.state_path.exists():
            try:
                with self.lock():
                    self.load_owned()
                    if self.state["phase"] == "owner_trial":
                        require(
                            self.m.get("retain_candidate_for_owner") is True,
                            "Owner-trial retention was not authorized",
                        )
                        # The owner explicitly superseded the test deadline and
                        # automatic restore policy. Keep the exclusive hold alive.
                        self.heartbeat()
                        hold = self.owns_hold()
                        if hold.get("expected_end") is not None:
                            hold["expected_end"] = None
                            atomic_json(self.hold, hold)
                        return "owner-trial"
            except Busy:
                return "busy"
        return super().watch_once()
