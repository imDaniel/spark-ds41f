#!/usr/bin/env python3
"""Independent, bounded recovery watcher for one immutable experiment session.

Importing this module does not run commands. The watch CLI is a site operation,
not a cloud test. Recovery is exclusively delegated to the session's fixed core.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
import re
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

from q03_remote_operation import open_private


def utc(epoch=None):
    return datetime.fromtimestamp(
        time.time() if epoch is None else epoch, timezone.utc
    ).isoformat(timespec="microseconds")


def epoch(value):
    if not isinstance(value, str):
        raise ValueError("heartbeat must be an ISO timestamp string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("heartbeat requires an explicit timezone")
    return parsed.timestamp()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def process_identity(pid):
    try:
        fields = Path(f"/proc/{int(pid)}/stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        return {"pid": int(pid), "start_ticks": fields[19]}
    except (OSError, ValueError, IndexError):
        return None


def process_alive(identity):
    return process_identity(identity["pid"]) == identity


def _same_file(path, fd):
    st = os.stat(path, follow_symlinks=False)
    opened = os.fstat(fd)
    if (st.st_dev, st.st_ino) != (opened.st_dev, opened.st_ino):
        raise RuntimeError("hold path changed; refusing foreign ownership")


@contextmanager
def _owned_fd(path, window_id, holder, writable=False, unlinking=False):
    flags = os.O_RDWR if writable else os.O_RDONLY
    fd = os.open(path, flags | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if writable else fcntl.LOCK_SH)
        _same_file(path, fd)
        data = os.pread(fd, os.fstat(fd).st_size, 0)
        record = json.loads(data)
        if record.get("window") != window_id or record.get("holder") != holder:
            raise RuntimeError("foreign hold; no mutation or recovery allowed")
        yield fd, record, data
        if not unlinking:
            _same_file(path, fd)
    finally:
        os.close(fd)


def claim_hold(path, record):
    """Create exclusively; never remove or reuse a previous/foreign hold."""
    if not record.get("window") or not record.get("holder"):
        raise ValueError("hold requires explicit UUID and holder")
    record = dict(record, heartbeat=utc())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(record, stream)
        stream.flush()
        os.fsync(stream.fileno())


def read_owned_hold(path, window_id, holder):
    with _owned_fd(path, window_id, holder) as (_, record, __):
        return record


def refresh_hold(path, window_id, holder, now_epoch=None):
    # Update only the fixed-width heartbeat bytes. Ownership bytes never change,
    # and an interrupted write cannot truncate the hold or replace a new inode.
    with _owned_fd(path, window_id, holder, writable=True) as (fd, record, data):
        old = json.dumps(record["heartbeat"]).encode()
        new = json.dumps(utc(now_epoch)).encode()
        matches = list(re.finditer(rb'"heartbeat"\s*:\s*("[^"\\]*")', data))
        if (len(old) != len(new) or len(matches) != 1
                or matches[0].group(1) != old):
            raise RuntimeError("hold heartbeat is not the fixed session format")
        offset = matches[0].start(1)
        if os.pwrite(fd, new, offset) != len(new):
            raise OSError("short heartbeat write")
        os.fsync(fd)


def release_hold(path, window_id, holder):
    # All cooperating owners create with O_EXCL; nobody may replace an extant hold.
    with _owned_fd(path, window_id, holder, writable=True, unlinking=True) as (fd, _, __):
        _same_file(path, fd)
        os.unlink(path)


def atomic_json(path, data):
    path = Path(path)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with open_private(temp, "x") as stream:
        try:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            if temp.exists():
                temp.unlink()
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def load_capsule(session_dir):
    from q03_held_window import read_owned_bytes
    root = Path(session_dir).resolve()
    path = root / "capsule.json"
    expected = read_owned_bytes(root / "capsule.sha256").decode().strip()
    raw = read_owned_bytes(path)
    if hashlib.sha256(raw).hexdigest() != expected:
        raise RuntimeError("immutable session capsule digest mismatch")
    capsule = json.loads(raw)
    if Path(capsule["session_dir"]).resolve() != root:
        raise RuntimeError("session directory mismatch")
    for key in ("deadline_epoch", "heartbeat_timeout_s", "recovery_timeout_s"):
        if not isinstance(capsule[key], (int, float)) or not math.isfinite(capsule[key]):
            raise ValueError(f"invalid bounded session field: {key}")
    if capsule["heartbeat_timeout_s"] <= 0 or capsule["recovery_timeout_s"] <= 0:
        raise ValueError("heartbeat timeout must be positive")
    core = Path(__file__).resolve().with_name("q03_window.py")
    command = [sys.executable, str(core), "recover", "--session-dir", str(root)]
    if capsule["recovery_command"] != command:
        raise RuntimeError("recovery command is not the fixed session core")
    if digest(core) != capsule["recovery_script_sha256"]:
        raise RuntimeError("recovery core source changed")
    required = {"q03_window.py", "q03_window_preflight.py", "q03_window_watchdog.py",
                "q03_original_spark_adapter.py", "q03_remote_operation.py",
                "q03_build_adapter.py", "q03_window_build.py", "q03_buildkit_plan.py",
                "q03_git_toolchain.py", "q03_held_window.py"}
    required.update({"q03_owner_model.py", "q03_rank_logs.py", "q03_image_preparation.py", "memguard-start.sh", "memguard.sh", "../bin/spark",
                     "mesh_fabric.py", "kernel_backend.py", "kernel_readiness.py", "topology.py",
                     "transport_profiles.py", "model_layout.py", "recipe.py"})
    required.update({"q03_http_service.py", "q03_http_worker.py", "q03_http_client.py", "q03_http_toolbox.json", "q03_qualification.py", "q03_quality_matrix.py", "q03_performance.py", 'q03_baseline_service.py', 'q03_baseline_identity.py', 'q03_baseline_worker.py', 'q03_legacy_case_replay.py'})
    sources = capsule["recovery_source_sha256"]
    if set(sources) != required:
        raise RuntimeError("recovery dependency source inventory mismatch")
    for name, expected_hash in sources.items():
        if digest(core.parent / name) != expected_hash:
            raise RuntimeError(f"recovery dependency changed: {name}")
    return capsule, expected


def original_stopped_complete(capsule, state):
    """A separate positive closure marker; never call stopped service restored."""
    return (capsule.get("manifest", {}).get("preflight", {}).get(
                "original_service_disposition") == "keep-stopped"
            and state.get("phase") == "closed-stopped"
            and state.get("original_kept_stopped") is True
            and state.get("restored") is False)


def reconciled(capsule, state):
    policy = capsule.get("manifest", {}).get("preflight", {}).get(
        "original_service_disposition", "restore")
    if policy not in ("restore", "keep-stopped"):
        raise RuntimeError("unknown original service disposition")
    if policy == "keep-stopped":
        if state.get("restored", False) is not False or state.get("phase") == "restored":
            raise RuntimeError("restored marker contradicts keep-stopped capsule")
        return original_stopped_complete(capsule, state)
    if state.get("original_kept_stopped", False) is not False or state.get("phase") == "closed-stopped":
        raise RuntimeError("stopped marker contradicts restore capsule")
    return bool(state.get("restored") or state.get("phase") == "aborted-before-mutation")


class Watchdog:
    """Single recovery attempt; all external effects are injectable for CPU tests."""

    def __init__(self, capsule, ops):
        self.capsule, self.ops = capsule, ops
        self.terminal = False

    def step(self):
        if self.terminal:
            raise RuntimeError("watchdog is already terminal")
        c, ops = self.capsule, self.ops
        try:
            ops.verify_capsule()
            state = ops.state()
            reconcile = reconciled(c, state)
            if reconcile and state.get("hold_released"):
                self.terminal = True
                return "stopped" if original_stopped_complete(c, state) else "restored"
            # Verified restoration may have crashed immediately before/after
            # unlink. The pinned core reconciles that without another transition
            # and never mutates a replacement foreign hold.
            hold = {} if reconcile else ops.owned_hold()
            now = ops.now()
            try:
                age = now - epoch(hold["heartbeat"])
                stale = not math.isfinite(age) or age < -5 or age > c["heartbeat_timeout_s"]
            except (ValueError, TypeError, KeyError):
                stale = not reconcile
            dead = not ops.core_alive()
            limit = c["deadline_epoch"]
            if state.get("phase") == "restoring" or state.get("restored") or original_stopped_complete(c, state):
                started = state.get("restoring_started_epoch")
                if not isinstance(started, (int, float)) or not math.isfinite(started):
                    raise ValueError("restoration start timestamp missing or invalid")
                limit = started + c["recovery_timeout_s"]
            expired = now >= limit
            if not (stale or dead or expired):
                return "waiting"
            ops.journal("recovery_trigger", stale=stale, dead=dead, expired=expired)
            # A live core must stop before its lifetime lock can be acquired by
            # recover. PID reuse never authorizes signalling the replacement.
            if not dead:
                ops.terminate_core()
            if ops.core_alive():
                raise RuntimeError("core remains alive; recovery is unsafe")
            # Termination may let the core finish its finally block, including
            # restoration and hold release. Re-read only after exact core death;
            # never use the pre-signal snapshot to inspect a replacement hold.
            ops.verify_capsule()
            state = ops.state()
            reconcile = reconciled(c, state)
            if reconcile and state.get("hold_released"):
                self.terminal = True
                ops.journal("core_completed_during_termination")
                return "stopped" if original_stopped_complete(c, state) else "restored"
            if not reconcile:
                ops.owned_hold()
            if ops.recovery_attempted():
                raise RuntimeError("recovery already attempted; manual review required")
            ops.mark_recovery_attempted()
            ops.recover()
            final = ops.state()
            if not (final.get("hold_released") and reconciled(c, final)):
                raise RuntimeError("recovery returned without verified closure")
            self.terminal = True
            result = "stopped" if original_stopped_complete(c, final) else "restored"
            ops.journal(result)
            return result
        except Exception as error:
            self.terminal = True
            ops.journal("watchdog_failed", error=str(error),
                        watchdog_hold_release_attempted=False, hold_state="unverified")
            return "failed"


class LocalOps:
    def __init__(self, root, capsule, capsule_hash):
        self.root, self.capsule, self.capsule_hash = Path(root), capsule, capsule_hash

    def now(self):
        return time.time()

    def verify_capsule(self):
        _, actual = load_capsule(self.root)
        if actual != self.capsule_hash:
            raise RuntimeError("session capsule changed after watchdog startup")
        if digest(self.capsule["policy_path"]) != self.capsule["policy_sha256"]:
            raise RuntimeError("registered restoration policy changed")

    def state(self):
        return json.loads((self.root / "state.json").read_text())

    def owned_hold(self):
        c = self.capsule
        return read_owned_hold(c["hold_path"], c["window_id"], c["holder"])

    def core_alive(self):
        return process_alive(self.capsule["core_process"])

    def terminate_core(self):
        identity = self.capsule["core_process"]
        if not self.core_alive():
            return
        # Pin the process before rechecking start time, eliminating PID-reuse
        # between a liveness check and a signal. Unsupported hosts fail closed.
        fd = os.pidfd_open(identity["pid"])
        try:
            if not self.core_alive():
                return
            for sig, wait in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
                if not self.core_alive():
                    return
                signal.pidfd_send_signal(fd, sig)
                end = time.monotonic() + wait
                while self.core_alive() and time.monotonic() < end:
                    time.sleep(0.1)
            if self.core_alive():
                raise RuntimeError("unable to stop matching core process")
        finally:
            os.close(fd)

    def journal(self, event, **fields):
        with open_private(self.root / "watchdog-journal.jsonl", "a") as stream:
            stream.write(json.dumps(dict(event=event, time=utc(), **fields)) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def recovery_attempted(self):
        return (self.root / "watchdog-recovery-attempt.json").exists()

    def mark_recovery_attempted(self):
        path = self.root / "watchdog-recovery-attempt.json"
        with open_private(path, "x") as stream:
            json.dump({"window_id": self.capsule["window_id"], "time": utc()}, stream)
            stream.flush()
            os.fsync(stream.fileno())

    def recover(self):
        with open_private(self.root / "watchdog-recovery.log", "x") as stream:
            try:
                subprocess.run(self.capsule["recovery_command"], check=True,
                               stdout=stream, stderr=subprocess.STDOUT,
                               timeout=self.capsule["recovery_timeout_s"])
            except subprocess.TimeoutExpired:
                # run() has killed and reaped the recovery core. Its journalled
                # command uses a separate group, so explicitly quiesce that too.
                self.verify_capsule()
                self.owned_hold()
                from q03_window import RuntimeOps
                RuntimeOps().terminate_owned_process(self.state().get("active_process"))
                self.journal("recovery_timeout_local_child_quiesced",
                             remote_transition_state="unverified", hold_retained=True)
                raise


def launch_watchdog(session_dir):
    root = Path(session_dir).resolve()
    load_capsule(root)
    with open_private(root / "watchdog.log", "x") as stream:
        return subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "watch",
             "--session-dir", str(root)], stdout=stream, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )


def wait_ready(session_dir, process, timeout=10):
    from q03_held_window import read_owned_bytes
    root = Path(session_dir).resolve()
    c, expected = load_capsule(root)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if process.poll() is not None:
            raise RuntimeError("watchdog exited before readiness")
        try:
            ready = json.loads(read_owned_bytes(root / "watchdog.ready.json"))
            if (ready["window_id"] == c["window_id"]
                    and ready["capsule_sha256"] == expected
                    and ready["process"]["pid"] == process.pid
                    and process_alive(ready["process"])):
                return ready
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(0.1)
    raise TimeoutError("watchdog readiness not established")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("watch",))
    parser.add_argument("--session-dir", type=Path, required=True)
    args = parser.parse_args()
    # Retain this descriptor for the entire watcher lifetime.
    with open_private(args.session_dir / "watchdog.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        c, expected = load_capsule(args.session_dir)
        ops = LocalOps(args.session_dir, c, expected)
        ops.verify_capsule()
        ops.owned_hold()
        atomic_json(args.session_dir / "watchdog.ready.json", {
            "window_id": c["window_id"], "capsule_sha256": expected,
            "process": process_identity(os.getpid()),
        })
        watch = Watchdog(c, ops)
        while True:
            result = watch.step()
            if result != "waiting":
                return 0 if result in ("restored", "stopped") else 1
            time.sleep(min(2, c["heartbeat_timeout_s"] / 4))


if __name__ == "__main__":
    sys.exit(main())
