"""Fault injection against real subprocess dispatch and shared filesystem state."""
import copy
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from window_executor import (ARMS, HOOKS, Busy, Executor, Refused, atomic_json,
                             cancel_group, digest, group_members, read, sha, stamp)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="spark-window-fixture-")
        self.root = Path(self.temp.name)
        self.adapter = self.root / "adapter.py"
        self.adapter.write_bytes((HERE / "tests/fake_site.py").read_bytes())
        self.manifest = self.root / "manifest.json"
        self.permit = self.root / "authorization.json"
        self.children = []
        self.monitor = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        self.children.append(self.monitor)
        self.m = {"schema_version": 1, "runtime_enabled": True, "fixture_root": str(self.root),
                  "window_id": str(uuid.uuid4()), "holder": "offline-fixture",
                  "controller_host": socket.gethostname(), "state_dir": str(self.root / "state"),
                  "hold_path": str(self.root / "spark-hold.json"),
                  "nodes": [f"spark0{i}" for i in range(1, 5)],
                  "heartbeat_seconds": 30, "poll_seconds": .02, "window_seconds": 90,
                  "restore_reserve_seconds": 15, "command_seconds": 10,
                  "observe_max_age_seconds": 5, "drain_seconds": .03, "drain_timeout_seconds": .3,
                  "services": {}, "hooks": {}, "implementation_files": {
                      str(HERE / p): sha(HERE / p) for p in ("window_executor.py", "command_worker.py")}}
        for name in ("retained", *ARMS):
            repo = self.root / ("retained" if name == "retained" else "candidate")
            (repo / "bin").mkdir(parents=True, exist_ok=True)
            (repo / "config").mkdir(exist_ok=True)
            script = repo / "bin/spark"
            script.write_text(f"#!{sys.executable}\n" + (HERE / "tests/fake_site.py").read_text().split("\n", 1)[1])
            script.chmod(0o755)
            config = repo / f"config/{name}.json"
            config.write_text(json.dumps({"fixture": True, "name": name}))
            identity = {"head": "a" * 40 if name == "retained" else "b" * 40,
                        "image_id": "sha256:" + digest(name), "profile_sha256": digest(name + "profile"),
                        "config_sha256": sha(config), "nodes_sha256": digest(self.m["nodes"]),
                        "mounts_sha256": digest(name + "mounts"), "model_identity": "c" * 64}
            self.m["services"][name] = {"repository": str(repo), "config": f"config/{name}.json",
                        "container": "fixture-" + name, "identity": identity,
                        "files": {str(script): sha(script), str(config): sha(config)}}
        for name in HOOKS:
            self.m["hooks"][name] = {"argv": [str(Path(sys.executable).resolve()), str(self.adapter), name],
                    "cwd": str(self.root), "files": {str(Path(sys.executable).resolve()): sha(sys.executable),
                                                     str(self.adapter): sha(self.adapter)}}
        self.world = {"observed_at": time.time(), "retained_ready": True,
                      "ingress": {"blocked": False, "aliases_sha256": "a" * 64},
                      "requests": {"running": 0, "waiting": 0}, "faults": {}, "nodes": []}
        for node in self.m["nodes"]:
            self.world["nodes"].append({"name": node, "reachable": True, "gpu_fault": False,
                    "foreign_jobs": [], "owned_jobs": [], "services": {
                        name: {"identity": service["identity"], "running": name == "retained",
                               "clean_checkout": True, "assets_verified": True,
                               "published_revision": True, "startup_guards_configured": True,
                               "guard_active": name == "retained", "owned_processes": int(name == "retained")}
                        for name, service in self.m["services"].items()}})
        self.write_world()
        self.configure()

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
        if self.x.state_path.exists():
            pending = read(self.x.state_path).get("pending")
            if pending:
                try:
                    cancel_group(pending)
                except (Refused, ProcessLookupError):
                    pass
        self.temp.cleanup()

    def configure(self):
        atomic_json(self.manifest, self.m)
        atomic_json(self.permit, {"window_id": self.m["window_id"], "manifest_sha256": digest(self.m),
                    "scope": "window_and_restore", "holder": self.m["holder"],
                    "owner_request": "synthetic local fixture ONLY", "open_before": time.time() + 60})
        self.x = Executor(self.manifest, self.permit)
        # Unit cases bind a separate live PID. The process-loss test below starts
        # the actual standalone watchdog rather than this readiness fixture.
        atomic_json(self.x.directory / "watchdog.json", {"pid": self.monitor.pid, "birth": stamp(self.monitor.pid),
                    "at": time.time(), "window_id": self.m["window_id"], "manifest_sha256": digest(self.m)})

    def write_world(self):
        atomic_json(self.root / "world.json", self.world)

    def load_world(self):
        self.world = read(self.root / "world.json")
        return self.world

    def fault(self, **values):
        self.load_world()["faults"].update(values)
        self.write_world()

    def calls(self):
        p = self.root / "calls.jsonl"
        return [json.loads(row) for row in p.read_text().splitlines()] if p.exists() else []

    def applies(self):
        return [x["argv"] for x in self.calls() if "--apply" in x["argv"]]

    def assert_closed(self):
        self.assertEqual(read(self.x.state_path)["phase"], "closed")
        self.assertFalse(self.x.hold.exists())
        world = self.load_world()
        self.assertFalse(world["ingress"]["blocked"])
        self.assertTrue(world["retained_ready"])
        for node in world["nodes"]:
            self.assertTrue(node["services"]["retained"]["running"])
            for arm in ARMS:
                self.assertFalse(node["services"][arm]["running"])
                self.assertFalse(node["services"][arm]["guard_active"])

    def start_cli(self, action, *extra):
        log = open(self.root / (action + ".log"), "w")
        child = subprocess.Popen([sys.executable, str(HERE / "window_executor.py"),
                    "--manifest", str(self.manifest), "--authorization", str(self.permit),
                    "--execute", action, *extra], stdout=log, stderr=subprocess.STDOUT)
        log.close()
        self.children.append(child)
        return child

    def wait_for(self, condition, seconds=10):
        deadline = time.monotonic() + seconds
        while not condition():
            if time.monotonic() >= deadline:
                self.fail("Timed out waiting for fixture state")
            time.sleep(.03)


class Admission(Fixture):
    def test_same_process_is_not_independent_watchdog(self):
        self.x.watch_once()
        with self.assertRaisesRegex(Refused, "Independent watchdog"): self.x.open()
        self.assertFalse(self.calls())

    def test_unverified_source_assets_are_rejected(self):
        self.world["nodes"][0]["services"]["u"]["published_revision"] = False
        self.write_world()
        with self.assertRaisesRegex(Refused, "unverified"): self.x.open()
        self.assertFalse(self.applies())

    def test_executor_hash_is_part_of_authorization(self):
        self.m["implementation_files"][str(HERE / "command_worker.py")] = "wrong"
        self.configure()
        with self.assertRaisesRegex(Refused, "Pinned input changed"): self.x.open()
        self.assertFalse(self.calls())

    def test_disabled_manifest_cannot_even_probe(self):
        self.m["runtime_enabled"] = False
        atomic_json(self.manifest, self.m)
        with self.assertRaisesRegex(Refused, "disabled"):
            Executor(self.manifest, self.permit).open()
        self.assertFalse(self.calls())

    def test_authorization_mismatch_or_expiry(self):
        for key, value in [("manifest_sha256", "wrong"), ("window_id", str(uuid.uuid4())),
                           ("scope", "plan"), ("open_before", 0)]:
            with self.subTest(key=key):
                permit = read(self.permit); permit[key] = value
                with patch.object(self.x, "auth", permit), self.assertRaises(Refused):
                    self.x.open()
        self.assertFalse(self.calls())

    def test_foreign_hold_is_preserved(self):
        atomic_json(self.x.hold, {"holder": "someone-else"})
        with self.assertRaises(Refused): self.x.open()
        self.assertEqual(read(self.x.hold), {"holder": "someone-else"})
        self.assertFalse(self.calls())

    def test_missing_watchdog(self):
        (self.x.directory / "watchdog.json").unlink()
        with self.assertRaises(FileNotFoundError): self.x.open()
        self.assertFalse(self.calls())

    def test_bad_initial_inventory(self):
        for field, value in [("reachable", False), ("gpu_fault", True), ("foreign_jobs", ["foreign"])]:
            with self.subTest(field=field):
                self.world["nodes"][2][field] = value; self.write_world()
                with self.assertRaises(Refused): self.x.open()
                self.world["nodes"][2][field] = [] if field == "foreign_jobs" else field == "reachable"
        self.assertFalse(self.applies())

    def test_drifted_recipe_refused(self):
        self.world["nodes"][0]["services"]["retained"]["identity"] = {"head": "wrong"}
        self.write_world()
        with self.assertRaisesRegex(Refused, "identity drift"): self.x.open()
        self.assertFalse(self.applies())

    def test_stale_or_missing_rank(self):
        for name in ("stale", "missing_rank"):
            self.fault(**{name: True})
            with self.assertRaises(Refused): self.x.open()
            self.fault(**{name: False})
        self.assertFalse(self.applies())

    def test_missing_queue_metrics_restores_without_stopping_user_service(self):
        self.world["requests"]["running"] = None; self.write_world()
        with self.assertRaisesRegex(Refused, "queue metrics"): self.x.open()
        self.assert_closed()
        self.assertFalse(self.applies())

    def test_busy_queue_never_kills_request(self):
        self.world["requests"]["running"] = 1; self.write_world()
        with self.assertRaisesRegex(Refused, "Drain timeout"): self.x.open()
        self.assert_closed()
        self.assertFalse(self.applies())

    def test_pinned_executable_drift(self):
        self.adapter.write_text("raise SystemExit('wrong adapter')\n")
        with self.assertRaisesRegex(Refused, "Pinned input changed"): self.x.open()
        self.assertFalse(self.calls())


class Recovery(Fixture):
    def test_gateway_acceptance_failure_reblocks_ingress(self):
        self.x.open(); self.fault(gateway_only=True)
        with self.assertRaises(Refused): self.x.recover()
        self.assertTrue(self.x.hold.exists())
        self.assertTrue(self.load_world()["ingress"]["blocked"])

    def test_retained_doctor_failure_never_reopens_ingress(self):
        self.x.open(); self.fault(doctor=True)
        with self.assertRaises(Refused): self.x.recover()
        self.assertTrue(self.x.hold.exists())
        self.assertTrue(self.load_world()["ingress"]["blocked"])

    def test_surviving_gpu_process_blocks_retained_start(self):
        self.x.open(); self.x.switch("u"); self.fault(**{"u:stop:process": True})
        count = len(self.applies())
        with self.assertRaisesRegex(Refused, "stop unverified"): self.x.recover()
        self.assertFalse(any("start" in a for a in self.applies()[count:]))

    def test_site_plan_refusal_is_not_overridden(self):
        self.x.open(); self.fault(refused_plan=True)
        with self.assertRaisesRegex(Refused, "Unsafe cluster plan"): self.x.switch("u")
        self.assert_closed(); self.assertFalse(self.applies())

    def test_gateway_alias_drift_blocks_mutation(self):
        self.x.open(); self.load_world(); self.world["ingress"]["aliases_sha256"] = "wrong"
        self.write_world()
        with self.assertRaisesRegex(Refused, "aliases changed"): self.x.recover()
        self.assertTrue(self.x.hold.exists()); self.assertFalse(self.applies())

    def test_full_four_arm_sequence_and_pinned_restore(self):
        self.x.open()
        for arm in ARMS: self.x.switch(arm)
        self.x.recover()
        self.assert_closed()
        starts = [a[a.index("--cluster-config") + 1] for a in self.applies() if "start" in a]
        self.assertEqual(starts, [f"config/{a}.json" for a in (*ARMS, "retained")])
        for call in self.applies():
            self.assertIn("--cluster-config", call)
            self.assertNotIn("--change-shape", call)
        self.assertTrue(all(c["context"]["window_id"] == self.m["window_id"] for c in self.calls()))

    def test_healthy_service_is_noop(self):
        self.x.open(); self.x.recover()
        self.assert_closed(); self.assertFalse(self.applies())

    def test_partial_candidate_launch_restores_all_ranks(self):
        self.x.open(); self.fault(**{"u:start:partial": True})
        with self.assertRaisesRegex(Refused, "Partial candidate"): self.x.switch("u")
        self.assert_closed()
        actions = [(a[a.index("--cluster-config") + 1], a[a.index("cluster") + 1]) for a in self.applies()]
        self.assertEqual(actions[-2:], [("config/u.json", "stop"), ("config/retained.json", "start")])

    def test_partial_retained_requires_coordinated_stop(self):
        self.x.open(); self.load_world()
        self.world["nodes"][0]["services"]["retained"]["running"] = False
        self.world["retained_ready"] = False; self.write_world()
        self.x.recover(); self.assert_closed()
        self.assertEqual([a[a.index("cluster") + 1] for a in self.applies()], ["stop", "start"])

    def test_failed_candidate_stop_never_starts_retained(self):
        self.x.open(); self.x.switch("u"); self.fault(**{"u:stop:fail": True})
        before = len(self.applies())
        with self.assertRaises(Refused): self.x.recover()
        self.assertTrue(self.x.hold.exists())
        self.assertEqual(read(self.x.state_path)["phase"], "manual")
        self.assertFalse(any("start" in a for a in self.applies()[before:]))
        count = len(self.calls()); self.assertEqual(self.x.watch_once(), "manual")
        self.assertEqual(len(self.calls()), count)

    def test_guard_or_gpu_process_survives_stop(self):
        self.x.open(); self.x.switch("u"); self.fault(**{"u:stop:guard": True})
        before = len(self.applies())
        with self.assertRaisesRegex(Refused, "stop unverified"): self.x.recover()
        self.assertTrue(self.x.hold.exists())
        self.assertFalse(any("start" in a for a in self.applies()[before:]))

    def test_lost_node_does_not_attempt_restart(self):
        self.x.open(); self.x.switch("u"); self.load_world()
        self.world["nodes"][3]["reachable"] = False; self.write_world()
        before = len(self.applies())
        with self.assertRaisesRegex(Refused, "Management path"): self.x.recover()
        self.assertEqual(len(self.applies()), before)
        self.assertTrue(self.x.hold.exists())

    def test_gpu_fault_does_not_attempt_restart(self):
        self.x.open(); self.x.switch("u"); self.load_world()
        self.world["nodes"][3]["gpu_fault"] = True; self.write_world()
        before = len(self.applies())
        with self.assertRaisesRegex(Refused, "GPU/driver"): self.x.recover()
        self.assertEqual(len(self.applies()), before)
        self.assertTrue(self.x.hold.exists())

    def test_retained_start_failure_preserves_hold(self):
        self.x.open(); self.x.switch("u"); self.fault(**{"retained:start:fail": True})
        with self.assertRaises(Refused): self.x.recover()
        self.assertEqual(read(self.x.state_path)["phase"], "manual")
        self.assertTrue(self.x.hold.exists())

    def test_final_api_failure_preserves_hold(self):
        self.x.open(); self.x.switch("u"); self.fault(steered_request=True)
        with self.assertRaisesRegex(Refused, "steered_request"): self.x.recover()
        self.assertTrue(self.x.hold.exists())

    def test_owned_job_stop_and_identity(self):
        self.x.open(); self.load_world()
        self.world["nodes"][0]["owned_jobs"] = [{"id": "component-1", "window_id": self.m["window_id"]}]
        self.write_world(); self.x.recover(); self.assert_closed()
        self.assertTrue(any(c["argv"] == ["stop_owned_jobs"] for c in self.calls()))

    def test_foreign_component_job_blocks_restore(self):
        self.x.open(); self.load_world()
        self.world["nodes"][0]["owned_jobs"] = [{"id": "component-1", "window_id": str(uuid.uuid4())}]
        self.write_world()
        with self.assertRaisesRegex(Refused, "Foreign component"): self.x.recover()
        self.assertTrue(self.x.hold.exists()); self.assertFalse(self.applies())

    def test_expired_open_authorization_still_allows_owned_restore(self):
        self.x.open(); self.x.auth["open_before"] = 0
        self.x.recover(); self.assert_closed()

    def test_shape_change_is_never_applied(self):
        self.x.open(); self.fault(bad_plan=True)
        with self.assertRaisesRegex(Refused, "shape change"): self.x.switch("u")
        self.assertFalse(any("start" in a for a in self.applies()))
        self.assertTrue(self.x.hold.exists())


class Watchdog(Fixture):
    def test_reboot_invalidates_old_window(self):
        self.x.open(); state = read(self.x.state_path); state["boot_id"] = "previous-boot"
        atomic_json(self.x.state_path, state)
        with self.assertRaisesRegex(Refused, "Host rebooted"): self.x.watch_once()
        self.assertTrue(self.x.hold.exists()); self.assertFalse(self.applies())

    def test_wall_clock_rollback_cannot_extend_window(self):
        self.x.open(); state = read(self.x.state_path)
        state["restore_at"] = time.time() + 10000
        state["restore_monotonic"] = time.monotonic() - 1
        atomic_json(self.x.state_path, state)
        self.assertEqual(self.x.watch_once(), "restored"); self.assert_closed()

    def test_pending_pid_reuse_preserves_hold(self):
        self.x.open(); state = read(self.x.state_path)
        state["pending"] = {"pid": os.getpid(), "birth": "wrong", "label": "old-command"}
        atomic_json(self.x.state_path, state)
        with self.assertRaisesRegex(Refused, "identity changed"): self.x.recover()
        self.assertTrue(self.x.hold.exists()); self.assertFalse(self.applies())
        state = read(self.x.state_path); state["pending"] = None
        atomic_json(self.x.state_path, state)

    def expire(self, deadline=False):
        if deadline:
            state = read(self.x.state_path); state["restore_monotonic"] = time.monotonic() - 1
            atomic_json(self.x.state_path, state)
        else:
            hold = read(self.x.hold); hold["heartbeat_monotonic"] = time.monotonic() - 100
            atomic_json(self.x.hold, hold)

    def test_fresh_window_does_nothing(self):
        self.x.open(); before = len(self.calls())
        self.assertEqual(self.x.watch_once(), "waiting")
        self.assertEqual(len(self.calls()), before)

    def test_heartbeat_loss_restores(self):
        self.x.open(); self.x.switch("u"); self.expire()
        self.assertEqual(self.x.watch_once(), "restored"); self.assert_closed()

    def test_deadline_restores_even_with_fresh_heartbeat(self):
        self.x.open(); self.x.switch("u"); self.expire(deadline=True)
        self.assertEqual(self.x.watch_once(), "restored"); self.assert_closed()

    def test_concurrent_controller_and_watchdog_share_lock(self):
        self.x.open()
        with self.x.lock():
            other = Executor(self.manifest, self.permit)
            self.assertEqual(other.watch_once(), "busy")
            with self.assertRaises(Busy): other.recover()
        self.assertTrue(self.x.hold.exists())

    def test_changed_hold_is_not_stolen(self):
        self.x.open(); hold = read(self.x.hold); hold["window_id"] = str(uuid.uuid4())
        atomic_json(self.x.hold, hold)
        with self.assertRaisesRegex(Refused, "Foreign"): self.x.watch_once()
        self.assertEqual(read(self.x.hold), hold)

    def test_command_timeout_stops_worker_and_restores(self):
        self.m["command_seconds"] = .5; self.configure()
        self.x.open(); self.fault(**{"u:start:hang": True})
        with self.assertRaisesRegex(Refused, "timeout"): self.x.switch("u")
        self.assert_closed()

    def test_independent_process_recovers_killed_controller_during_launch(self):
        self.m["heartbeat_seconds"] = .8; self.m["poll_seconds"] = .1; self.configure()
        watcher = self.start_cli("watchdog")
        self.wait_for(lambda: read(self.x.directory / "watchdog.json")["pid"] == watcher.pid)
        self.x.open(); self.fault(**{"u:start:hang": True})
        controller = self.start_cli("switch", "--arm", "u")
        def launching():
            pending = read(self.x.state_path).get("pending")
            return pending and pending["label"] == "u:start:apply" and self.load_world()["nodes"][0]["services"]["u"]["running"]
        self.wait_for(launching)
        pending = read(self.x.state_path)["pending"]
        controller.kill(); controller.wait(timeout=3)
        self.wait_for(lambda: read(self.x.state_path)["phase"] in ("closed", "manual"), seconds=15)
        self.assert_closed()
        self.assertFalse(group_members(pending["pid"]))
        self.wait_for(lambda: watcher.poll() is not None)
        self.assertEqual(watcher.returncode, 0)

    def test_pid_reuse_is_not_signalled(self):
        with self.assertRaisesRegex(Refused, "identity changed"):
            cancel_group({"pid": os.getpid(), "birth": "not-this-process"})

    def test_worker_eof_before_go_cannot_execute(self):
        job = self.root / "job.json"
        atomic_json(job, {"argv": [sys.executable, "-c", "raise SystemExit(9)"],
                          "cwd": str(self.root), "context": {}})
        result = subprocess.run([sys.executable, str(HERE / "command_worker.py"), str(job)],
                                input="", text=True, timeout=3)
        self.assertEqual(result.returncode, 125)


if __name__ == "__main__":
    unittest.main(verbosity=2)
