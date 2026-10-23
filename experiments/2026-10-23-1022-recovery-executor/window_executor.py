#!/usr/bin/env python3
"""Pinned TP4 window executor and independent watchdog.

No operation is performed by import, check, or plan. Execution requires an
enabled manifest, matching explicit authorization, and configured, hash-pinned
site adapters. The shipped manifest is disabled and has no live adapters.
Only the offline fixture adapters are exercised by the local fault suite.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid

HERE = Path(__file__).resolve().parent
ARMS = ("u", "e", "s-unloaded", "s-loaded")
HOOKS = ("observe", "block_ingress", "restore_ingress", "verify_retained", "stop_owned_jobs")


class Refused(RuntimeError):
    pass


class Busy(Refused):
    pass


class CommandFailed(Refused):
    pass


def require(ok, message):
    if not ok:
        raise Refused(message)


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def atomic_json(path, value, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(prefix=".window-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            os.link(temp, path)  # Atomic no-clobber publication; never steal a hold.
        else:
            os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def stamp(pid):
    """PID reuse guard. Linux uses boot ID + /proc start ticks; Mac is for tests."""
    proc = Path(f"/proc/{pid}/stat")
    if Path("/proc/sys/kernel/random/boot_id").exists():
        try:
            fields = proc.read_text().rsplit(")", 1)[1].split()
            if fields[0] == "Z":
                return None
            return Path("/proc/sys/kernel/random/boot_id").read_text().strip() + ":" + fields[19]
        except FileNotFoundError:
            return None
    result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart=,stat="],
                            capture_output=True, text=True, timeout=3)
    value = result.stdout.strip()
    return " ".join(value.split()[:-1]) if value and not value.split()[-1].startswith("Z") else None


def group_members(pgid):
    result = subprocess.run(["ps", "-eo", "pid=,pgid=,stat="],
                            capture_output=True, text=True, timeout=3, check=True)
    return [int(p) for row in result.stdout.splitlines() if len(parts := row.split()) == 3
            for p, group, status in [parts] if int(group) == pgid and not status.startswith("Z")]


def boot_id():
    path = Path("/proc/sys/kernel/random/boot_id")
    if path.exists():
        return path.read_text().strip()
    return subprocess.check_output(["sysctl", "-n", "kern.boottime"], text=True, timeout=3).strip()


def cancel_group(record):
    """Signal only a recorded, still-identical worker session; never pkill names."""
    pid = record["pid"]
    actual = stamp(pid)
    if actual is None:
        require(not group_members(pid), "Worker disappeared with surviving children; manual review")
        return
    require(actual == record["birth"] and os.getpgid(pid) == pid,
            "Worker PID identity changed; refusing to signal it")
    os.killpg(pid, signal.SIGTERM)
    deadline = time.monotonic() + 1
    while group_members(pid) and time.monotonic() < deadline:
        time.sleep(.02)
    if group_members(pid):
        # The group still contains our recorded session, or its surviving children.
        # It cannot be reused while it has members.
        os.killpg(pid, signal.SIGKILL)
    deadline = time.monotonic() + 2
    while group_members(pid) and time.monotonic() < deadline:
        time.sleep(.02)
    require(not group_members(pid), "Command group did not stop")


class Executor:
    def __init__(self, manifest, authorization=None):
        self.manifest_path = Path(manifest).resolve()
        self.m = read(self.manifest_path)
        self.fingerprint = digest(self.m)
        self.auth = read(authorization) if authorization else None
        self.directory = Path(self.m["state_dir"])
        self.state_path = self.directory / "state.json"
        self.hold = Path(self.m["hold_path"])
        self.state = None
        self._validate()

    def _validate(self):
        m = self.m
        require(m["schema_version"] == 1, "Unsupported manifest")
        uuid.UUID(m["window_id"])
        require(set(m["services"]) == {"retained", *ARMS}, "Exactly retained + four arms required")
        require(len(m["nodes"]) == 4 and len(set(m["nodes"])) == 4, "Four distinct nodes required")
        require(self.directory.is_absolute() and self.hold.is_absolute(), "Absolute state paths required")
        require(m["holder"] and m["controller_host"], "Holder and host required")
        for field in ("heartbeat_seconds", "poll_seconds", "window_seconds", "restore_reserve_seconds",
                      "command_seconds", "observe_max_age_seconds", "drain_seconds", "drain_timeout_seconds"):
            value = m[field]
            require(type(value) in (int, float) and math.isfinite(value) and value > 0,
                    f"Invalid {field}")
        require(m["restore_reserve_seconds"] < m["window_seconds"], "No experiment budget")
        require(m["poll_seconds"] < m["heartbeat_seconds"], "Watchdog poll too slow")
        for name, service in m["services"].items():
            require(Path(service["repository"]).is_absolute(), "Absolute checkout required")
            config = Path(service["config"])
            require(not config.is_absolute() and ".." not in config.parts, "Repository-relative config required")
            require(re.fullmatch(r"[a-f0-9]{40}", service["identity"]["head"]) is not None,
                    f"{name}: full source revision required")
            require(re.fullmatch(r"sha256:[a-f0-9]{64}", service["identity"]["image_id"]) is not None,
                    f"{name}: image config digest required")
        require(len({s["container"] for s in m["services"].values()}) == 5,
                "Services need distinct container names")
        require(m["services"]["retained"]["repository"] not in
                {m["services"][a]["repository"] for a in ARMS}, "Candidate must have separate checkout")

    def authorized(self, opening=False):
        require(self.m["runtime_enabled"] is True, "Runtime execution disabled")
        require(self.m["controller_host"] == socket.gethostname(), "Wrong controller host")
        self.check_files(self.m["implementation_files"])
        require(self.auth is not None, "Explicit window authorization required")
        for key, value in {"window_id": self.m["window_id"], "manifest_sha256": self.fingerprint,
                           "scope": "window_and_restore", "holder": self.m["holder"]}.items():
            require(self.auth.get(key) == value, f"Authorization {key} mismatch")
        require(self.auth.get("owner_request"), "Authorization must identify the owner's runtime request")
        require(type(self.auth.get("open_before")) in (int, float) and
                math.isfinite(self.auth["open_before"]), "Authorization needs finite expiry")
        if opening:
            require(time.time() < self.auth["open_before"], "Authorization expired before window open")
        for hook in HOOKS:
            require(self.m["hooks"].get(hook), f"Site adapter unconfigured: {hook}")

    @contextmanager
    def lock(self):
        # One lock beside the shared hold, common even to different window dirs.
        self.hold.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(str(self.hold) + ".operation.lock", "a") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise Busy("Another controller/recovery operation holds the lock") from error
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def save(self):
        atomic_json(self.state_path, self.state)

    def load_owned(self):
        self.state = read(self.state_path)
        require(self.state["manifest_sha256"] == self.fingerprint, "State/manifest drift")
        require(self.state["window_id"] == self.m["window_id"], "State/window mismatch")
        require(self.state["boot_id"] == boot_id(), "Host rebooted; require operator review")
        self.owns_hold()

    def owns_hold(self):
        hold = read(self.hold)
        require(hold.get("window_id") == self.m["window_id"] and
                hold.get("manifest_sha256") == self.fingerprint and
                hold.get("holder") == self.m["holder"], "Foreign or changed hold")
        return hold

    def heartbeat(self):
        if self.state is None or not self.hold.exists():
            return
        hold = self.owns_hold()
        hold["heartbeat"] = datetime.now(timezone.utc).isoformat()
        hold["heartbeat_epoch"] = time.time()
        hold["heartbeat_monotonic"] = time.monotonic()
        atomic_json(self.hold, hold)

    def event(self, kind, **fields):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(self.directory / "events.jsonl", "a") as stream:
            stream.write(json.dumps({"at": time.time(), "event": kind, **fields}, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def check_files(self, files):
        require(bool(files), "Executable inputs must be hash-pinned")
        for path, expected in files.items():
            require(Path(path).is_absolute() and sha(path) == expected, f"Pinned input changed: {path}")

    def run(self, argv, cwd, files, label, timeout=None, recovering=False):
        self.check_files(files)
        require(str(Path(argv[0]).resolve()) in {str(Path(p).resolve()) for p in files},
                "Command executable must be pinned")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        attempt = uuid.uuid4().hex
        job = self.directory / f"{attempt}.job.json"
        output = self.directory / f"{attempt}.log"
        context = {"window_id": self.m["window_id"], "manifest": str(self.manifest_path),
                   "state_dir": str(self.directory), "recovery_step":
                   self.state.get("recovery_step") if self.state else None, "original_ingress":
                   self.state.get("original_ingress") if self.state else None}
        atomic_json(job, {"argv": argv, "cwd": cwd, "context": context})
        timeout = min(timeout or self.m["command_seconds"], self.m["command_seconds"])
        if self.state and not recovering:
            timeout = min(timeout, self.state["restore_monotonic"] - time.monotonic())
        require(timeout > 0, "Window restoration deadline reached")
        self.event("command", label=label, argv=argv, cwd=cwd, log=output.name)
        with open(output, "w") as log:
            process = subprocess.Popen([sys.executable, str(HERE / "command_worker.py"), str(job)],
                                       stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                                       text=True, start_new_session=True, close_fds=True)
            record = {"pid": process.pid, "birth": stamp(process.pid), "label": label}
            require(record["birth"] is not None, "Worker exited before admission")
            try:
                if self.state is not None:
                    self.state["pending"] = record
                    self.save()  # Durably recorded BEFORE allowing the command to run.
                process.stdin.write("GO\n")
                process.stdin.flush()
                process.stdin.close()
                deadline = time.monotonic() + timeout
                while process.poll() is None:
                    if time.monotonic() >= deadline:
                        raise CommandFailed(f"Command timeout: {label}")
                    self.heartbeat()
                    time.sleep(min(.1, self.m["poll_seconds"]))
                require(not group_members(process.pid), "Command left background descendants")
                if process.returncode:
                    raise CommandFailed(f"Command failed ({process.returncode}): {label}; {output}")
            except BaseException:
                cancel_group(record)
                process.wait(timeout=3)
                raise
            finally:
                if process.stdin and not process.stdin.closed:
                    process.stdin.close()
                if process.poll() is not None and not group_members(process.pid) and self.state is not None:
                    self.state["pending"] = None
                    self.save()
        return output.read_text()

    def hook(self, name, recovering=False):
        spec = self.m["hooks"][name]
        return self.run(spec["argv"], spec["cwd"], spec["files"], "hook:" + name,
                        spec.get("timeout_seconds"), recovering)

    def observe(self, recovering=False):
        data = json.loads(self.hook("observe", recovering))
        age = time.time() - data["observed_at"]
        require(0 <= age <= self.m["observe_max_age_seconds"], "Stale or future observation")
        nodes = data["nodes"]
        require(len(nodes) == 4 and {n["name"] for n in nodes} == set(self.m["nodes"]),
                "Missing/duplicate rank observation")
        for node in nodes:
            require(node["reachable"] is True, "Management path lost")
            require(node["gpu_fault"] is False, "GPU/driver fault needs operator review")
            require(node["foreign_jobs"] == [], "Unknown GPU processes need operator review")
            for name, service in self.m["services"].items():
                actual = node["services"][name]
                require(actual["identity"] == service["identity"], f"{node['name']}/{name}: identity drift")
                require(all(actual.get(k) is True for k in ("clean_checkout", "assets_verified",
                            "published_revision", "startup_guards_configured")),
                        f"{node['name']}/{name}: source/assets/guards unverified")
                require(type(actual["running"]) is bool and type(actual["guard_active"]) is bool,
                        "Missing running/guard state")
                require(type(actual["owned_processes"]) is int and actual["owned_processes"] >= 0,
                        "Missing process inventory")
            require(isinstance(node["owned_jobs"], list), "Missing owned job inventory")
            require(all(j["window_id"] == self.m["window_id"] for j in node["owned_jobs"]),
                    "Foreign component job")
        require(type(data["retained_ready"]) is bool, "Missing retained readiness")
        require(type(data["ingress"]["blocked"]) is bool and data["ingress"]["aliases_sha256"],
                "Missing ingress evidence")
        if self.state and self.state.get("original_ingress"):
            require(data["ingress"]["aliases_sha256"] == self.state["original_ingress"]["aliases_sha256"],
                    "Gateway aliases changed")
        return data

    @staticmethod
    def active(data, name):
        return any(n["services"][name]["running"] or n["services"][name]["guard_active"] or
                   n["services"][name]["owned_processes"] for n in data["nodes"])

    @staticmethod
    def healthy_retained(data):
        return data["retained_ready"] and all(n["services"]["retained"]["running"] and
                                               n["services"]["retained"]["guard_active"]
                                               for n in data["nodes"])

    def watchdog_ready(self):
        ready = read(self.directory / "watchdog.json")
        require(ready["window_id"] == self.m["window_id"] and
                ready["manifest_sha256"] == self.fingerprint and
                0 <= time.time() - ready["at"] < self.m["heartbeat_seconds"] and
                ready["pid"] != os.getpid() and
                stamp(ready["pid"]) == ready["birth"], "Independent watchdog is not ready")

    def open(self):
        self.authorized(opening=True)
        with self.lock():
            require(not self.hold.exists() and not self.state_path.exists(), "Window/hold already exists")
            self.watchdog_ready()
            data = self.observe()
            require(self.healthy_retained(data), "Retained service is not healthy")
            require(not any(self.active(data, a) for a in ARMS), "Candidate already active")
            require(not any(n["owned_jobs"] for n in data["nodes"]), "Component job already active")
            require(data["ingress"]["blocked"] is False, "Ingress already blocked; ownership unknown")
            now = time.time()
            self.state = {"window_id": self.m["window_id"], "manifest_sha256": self.fingerprint,
                          "phase": "opening", "opened_at": now,
                          "boot_id": boot_id(), "restore_monotonic": time.monotonic() +
                          self.m["window_seconds"] - self.m["restore_reserve_seconds"],
                          "restore_at": now + self.m["window_seconds"] - self.m["restore_reserve_seconds"],
                          "end_at": now + self.m["window_seconds"], "pending": None,
                          "original_ingress": data["ingress"], "last_arm": None}
            # State first: crash before the exclusive hold publication changes no service.
            self.save()
            iso = datetime.fromtimestamp(now, timezone.utc).isoformat()
            atomic_json(self.hold, {"holder": self.m["holder"], "window_id": self.m["window_id"],
                                   "manifest_sha256": self.fingerprint, "since": iso,
                                   "heartbeat": iso, "heartbeat_epoch": now, "heartbeat_monotonic": time.monotonic(),
                                   "expected_end": datetime.fromtimestamp(self.state["end_at"], timezone.utc).isoformat(),
                                   "rule": "exclusive TP4 comparison; retain hold until pinned recovery verifies"},
                        exclusive=True)
            try:
                self.hook("block_ingress")
                deadline = time.monotonic() + self.m["drain_timeout_seconds"]
                idle_since = None
                while True:
                    data = self.observe()
                    require(data["ingress"]["blocked"] is True, "Ingress did not block new traffic")
                    require(self.healthy_retained(data), "Retained service failed while draining")
                    q = data["requests"]
                    require(all(type(q[k]) is int and q[k] >= 0 for k in ("running", "waiting")),
                            "Missing queue metrics is not idle")
                    if q["running"] == q["waiting"] == 0:
                        idle_since = idle_since if idle_since is not None else time.monotonic()
                        if time.monotonic() - idle_since >= self.m["drain_seconds"]:
                            break
                    else:
                        idle_since = None
                    require(time.monotonic() < deadline, "Drain timeout; do not kill user requests")
                    self.heartbeat()
                    time.sleep(self.m["poll_seconds"])
                self.state["phase"] = "ready"
                self.save()
            except Exception as error:
                self.abort(error)
                raise

    def cluster(self, name, action, recovering=False):
        service = self.m["services"][name]
        argv = [service["repository"] + "/bin/spark", "--shape", "tp4-ring", "--cluster-config",
                service["config"], "cluster", action]
        if action == "start":
            argv += ["--replace", "--unqualified-shape"]
        else:
            argv += ["--parallel"]
        output = self.run(argv, service["repository"], service["files"], f"{name}:{action}:plan",
                          recovering=recovering)
        require("--apply would refuse" not in output and "live:" in output and
                not re.search(r"cannot reach|unreachable|WARN:|ERROR:", output), "Unsafe cluster plan")
        if action == "start":
            require(re.search(r"^shape:\s+(unchanged|new engine)$", output, re.M), "Unexpected shape change")
            require("4 nodes, TP4, context 1,048,576, KV 10.5 GiB" in output, "Unexpected engine capacity")
            require(f"profile: {service['config']} on " + ", ".join(self.m["nodes"]) in output,
                    "Start plan does not cover the exact fleet")
        else:
            for node in self.m["nodes"]:
                require(f"{node}: stop exact container {service['container']} and stop its memguard" in output,
                        "Stop plan must cover every exact container and guard")
        self.owns_hold()
        return self.run(argv + ["--apply"], service["repository"], service["files"],
                        f"{name}:{action}:apply", recovering=recovering)

    def switch(self, arm):
        require(arm in ARMS, "Unknown candidate arm")
        self.authorized()
        with self.lock():
            self.load_owned()
            require(self.state["phase"] in ("ready", "testing"), "Window is not accepting tests")
            try:
                self.watchdog_ready()
                require(time.monotonic() < self.state["restore_monotonic"], "Window restoration deadline reached")
                data = self.observe()
                require(data["ingress"]["blocked"], "Ingress reopened during experiment")
                require(not any(n["owned_jobs"] for n in data["nodes"]), "Component jobs still active")
                for name in ("retained", *ARMS):
                    if self.active(data, name):
                        self.cluster(name, "stop")
                        data = self.observe()
                        require(not self.active(data, name), "Stop verification failed")
                require(not any(self.active(data, n) for n in self.m["services"]), "Overlapping engine")
                self.state.update(phase="launching", last_arm=arm)
                self.save()
                self.cluster(arm, "start")
                data = self.observe()
                require(all(n["services"][arm]["running"] and n["services"][arm]["guard_active"]
                            for n in data["nodes"]), "Partial candidate launch")
                self.state["phase"] = "testing"
                self.save()
            except Exception as error:
                self.abort(error)
                raise

    def abort(self, error):
        self.event("abort", reason=str(error))
        try:
            self._recover()
        except Exception as restore_error:
            self.state["error"] = str(error)
            self.manual(restore_error)

    def manual(self, error):
        self.state.update(phase="manual", recovery_error=str(error))
        self.save()
        if self.state.get("ingress_reopened"):
            # Gateway acceptance can fail after reopening. Re-block once, retain
            # the hold, and escalate; never silently keep an unverified route open.
            try:
                self.owns_hold()
                self.hook("block_ingress", recovering=True)
            except Exception as ingress_error:
                self.event("ingress_block_failed", reason=str(ingress_error))
        self.event("manual", reason=str(error))

    def verify(self, step):
        self.state["recovery_step"] = step
        self.save()
        checks = json.loads(self.hook("verify_retained", recovering=True))
        for key in ("health", "models", "stock_request", "steered_request", "gateway_aliases"):
            require(checks.get(key) is True, f"Recovery acceptance failed: {key}")

    def _recover(self):
        self.owns_hold()
        self.state["phase"] = "recovering"
        self.save()
        if self.state["pending"]:
            cancel_group(self.state["pending"])
            self.state["pending"] = None
            self.save()
        data = self.observe(recovering=True)
        if any(n["owned_jobs"] for n in data["nodes"]):
            self.hook("stop_owned_jobs", recovering=True)
            data = self.observe(recovering=True)
            require(not any(n["owned_jobs"] for n in data["nodes"]), "Owned jobs did not stop")
        for arm in ARMS:
            if self.active(data, arm):
                self.cluster(arm, "stop", recovering=True)
                data = self.observe(recovering=True)
                require(not self.active(data, arm), "Candidate stop unverified; refusing retained start")
        if not self.healthy_retained(data):
            if self.active(data, "retained"):
                self.cluster("retained", "stop", recovering=True)
                data = self.observe(recovering=True)
                require(not self.active(data, "retained"), "Retained stop unverified")
            require(not any(self.active(data, a) for a in ARMS), "Candidate remains active")
            self.cluster("retained", "start", recovering=True)
        data = self.observe(recovering=True)
        require(self.healthy_retained(data), "Retained service did not recover on all ranks")
        service = self.m["services"]["retained"]
        self.run([service["repository"] + "/bin/spark", "--shape", "tp4-ring", "--cluster-config",
                  service["config"], "doctor", "--live"], service["repository"], service["files"],
                 "retained:doctor", recovering=True)
        self.verify("private_api_before_ingress")
        self.state["ingress_reopened"] = True
        self.save()
        self.hook("restore_ingress", recovering=True)
        data = self.observe(recovering=True)
        require(data["ingress"] == self.state["original_ingress"], "Original ingress not restored")
        self.verify("gateway_after_ingress")
        data = self.observe(recovering=True)
        require(self.healthy_retained(data) and not any(self.active(data, a) for a in ARMS) and
                data["ingress"] == self.state["original_ingress"] and
                not any(n["owned_jobs"] for n in data["nodes"]),
                "Service changed during recovery verification")
        self.state["phase"] = "closed"
        self.save()
        self.owns_hold()
        self.hold.unlink()
        self.event("restored", retained=self.m["services"]["retained"]["identity"])

    def recover(self):
        self.authorized()
        with self.lock():
            self.load_owned()
            require(self.state["phase"] != "manual", "Manual intervention required; no automatic retry")
            try:
                self._recover()
            except Exception as error:
                self.manual(error)
                raise

    def pulse(self):
        self.authorized()
        with self.lock():
            self.load_owned()
            require(self.state["phase"] in ("ready", "testing"), "Window is not accepting heartbeats")
            require(time.monotonic() < self.state["restore_monotonic"], "Window restoration deadline reached")
            self.heartbeat()

    def watch_once(self):
        self.authorized()
        atomic_json(self.directory / "watchdog.json", {"pid": os.getpid(), "birth": stamp(os.getpid()),
                    "at": time.time(), "window_id": self.m["window_id"], "manifest_sha256": self.fingerprint})
        if not self.hold.exists():
            return "unarmed"
        try:
            with self.lock():
                self.load_owned()
                if self.state["phase"] == "manual":
                    return "manual"
                hold = self.owns_hold()
                elapsed = time.monotonic() - hold["heartbeat_monotonic"]
                require(elapsed >= 0 and math.isfinite(elapsed), "Invalid heartbeat clock")
                expired = elapsed >= self.m["heartbeat_seconds"]
                due = time.monotonic() >= self.state["restore_monotonic"]
                if not expired and not due and self.state["phase"] not in ("recovering", "closed"):
                    return "waiting"
                self.event("watchdog_recovery", cause="deadline" if due else "heartbeat_or_interruption")
                try:
                    self._recover()
                    return "restored"
                except Exception as error:
                    self.manual(error)
                    return "manual"
        except Busy:
            return "busy"  # Active commands have their own bounded deadlines.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("action", choices=("check", "open", "switch", "recover", "heartbeat", "watchdog"))
    parser.add_argument("--arm", choices=ARMS)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    try:
        executor = Executor(args.manifest, args.authorization)
        if args.action == "check" or not args.execute:
            print(json.dumps({"manifest_sha256": executor.fingerprint,
                              "runtime_enabled": executor.m["runtime_enabled"],
                              "unconfigured_adapters": [k for k in HOOKS if not executor.m["hooks"].get(k)],
                              "execution": False}, indent=2))
            return 0
        if args.action == "watchdog":
            while True:
                result = executor.watch_once()
                print(result, flush=True)
                if args.once or result in ("manual", "restored"):
                    return int(result == "manual")
                time.sleep(executor.m["poll_seconds"])
        elif args.action == "switch":
            executor.switch(args.arm)
        else:
            {"open": executor.open, "recover": executor.recover, "heartbeat": executor.pulse}[args.action]()
        return 0
    except (Refused, ValueError, KeyError, OSError) as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
