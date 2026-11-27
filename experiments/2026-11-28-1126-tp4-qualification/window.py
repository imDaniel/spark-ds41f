"""Own the site qualification window and recover the accepted 1022 service on timeout."""
import argparse
import concurrent.futures
import contextlib
import datetime as dt
import fcntl
import json
import os
import shlex
from pathlib import Path
import subprocess
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / "2026-10-23-1022-window"))
import site_adapter as site
sys.path.insert(0, str(HERE.parent / "2026-10-23-1022-recovery-executor"))
from window_executor import atomic_json, stamp

OP = Path("/srv/ai-cluster/operations/1126-attention-20261128")
OLD = Path("/srv/ai-cluster/runtimes/1022-screen-20261023/source/spark-ds41f")
OLD_REV = "3e5eb7693f4b83c6232448270615e61e8e5520b2"
CFG = str(HERE.relative_to(ROOT) / "cluster.json")
HOLD = Path.home() / "spark-hold.json"
OWNER = "1126-attention-site-qualification"
PREFIX = "ds41f-1126-qualification"
UNITS = tuple(PREFIX + "-" + name for name in ("build", "kernel", "start", "test", "sync"))
WATCHDOG_INTERVAL = 15


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def boot():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def cmd(args, *, cwd=None, timeout=300, capture=False):
    return subprocess.run(args, cwd=cwd, check=True, text=True, timeout=timeout,
                          stdout=subprocess.PIPE if capture else None)


def stored():
    return json.loads(HOLD.read_text())


def owned(window):
    h = stored()
    if h.get("holder") != OWNER or h.get("window_id") != window:
        raise RuntimeError("Another holder owns the cluster")
    return h


def active(window):
    h = owned(window)
    if (h["phase"] in ("recovering", "recovery_failed") or h["boot_id"] != boot()
            or time.monotonic() - h["beat_monotonic"] >= 900):
        raise RuntimeError("Window recovery has priority")
    return h


@contextlib.contextmanager
def lock():
    with (OP / "window.lock").open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def spark(root, cfg, *args, timeout=3600):
    return cmd([str(root / "bin/spark"), "--cluster-config", cfg, *args],
               cwd=root, timeout=timeout)


def acquire(window):
    uuid.UUID(window)
    if cmd(["git", "-C", str(OLD), "rev-parse", "HEAD"], capture=True).stdout.strip() != OLD_REV:
        raise RuntimeError("Recovery checkout differs from the accepted revision")
    spark(OLD, "config/cluster.json", "doctor", "--live")
    with lock():
        for f in [HOLD, *(Path.home() / name for name in ("spark3-hold.json", "spark-request.json", "spark3-request.json"))]:
            if f.exists():
                raise RuntimeError(f"Cluster in use: {f}")
        atomic_json(HOLD, {"holder": OWNER, "window_id": window, "since": now(),
                    "since_epoch": int(time.time()), "heartbeat": now(), "expected_end": None,
                    "boot_id": boot(), "beat_monotonic": time.monotonic(),
                    "phase": "preparing", "rule": "Exclusive TP4 build, qualification and recovery"},
                    exclusive=True)


def heartbeat(window, phase=None):
    if phase in ("recovering", "recovery_failed"):
        raise ValueError("Only the recovery executor can set a recovery phase")
    with lock():
        h = active(window)
        h.update(heartbeat=now(), beat_monotonic=time.monotonic())
        if phase:
            h["phase"] = phase
        atomic_json(HOLD, h)


def four_inspect(node):
    addr = "admin@10.42.0." + str(node)
    start = stored()["since_epoch"]
    code = r"""
import json, subprocess
def out(args):
    return subprocess.check_output(args, text=True, timeout=20)
journal = out(["sudo", "-n", "journalctl", "-k", "--since", "@START", "--no-pager", "-o", "cat"])
if "NVRM: Xid" in journal or "GPU has fallen off" in journal:
    raise RuntimeError("GPU fault during this window")
out(["nvidia-smi", "-L"])
names = out(["docker", "ps", "--format", "{{.Names}}"]).splitlines()
allowed = set()
for name in ("ds41f-1022-parser-rc3", "ds41f-1126-attention"):
    if name in names:
        allowed.update(out(["docker","top",name,"-eo","pid"]).split()[1:])
active = out(["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader"]).split()
if set(active)-allowed:
    raise RuntimeError("Foreign GPU processes: " + str(set(active)-allowed))
print(json.dumps(names))
""".replace("@START", "@" + str(start))
    return json.loads(cmd(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", addr,
                           shlex.join(["python3", "-c", code])], capture=True, timeout=65).stdout)



def restore(window, *, stale=False):
    # Keep one restorer in charge of cluster and ingress mutations. A killed
    # process releases flock; a reported hardware/command failure needs review.
    with lock():
        if stale and not HOLD.exists():
            return
        h = owned(window)
        if stale and (h["phase"] == "recovery_failed" or (
                h["phase"] != "recovering" and h["boot_id"] == boot()
                and time.monotonic() - h["beat_monotonic"] < 900)):
            return
        h["phase"] = "recovering"
        atomic_json(HOLD, h)
        try:
            restore_service(h)
        except Exception as error:
            h.update(phase="recovery_failed", recovery_error=str(error))
            atomic_json(HOLD, h)
            raise
        atomic_json(OP / ("recovered-" + window + ".json"), {"at": now(), "restored": OLD_REV})
        HOLD.unlink()


def restore_service(h):
    for unit in UNITS:
        active = subprocess.run(["systemctl", "--user", "is-active", unit], capture_output=True, text=True)
        if active.stdout.strip() in ("activating", "active", "deactivating"):
            cmd(["systemctl", "--user", "stop", unit], timeout=180)
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        rows = list(pool.map(four_inspect, range(1, 5)))
    if any("ds41f-1126-attention" in r for r in rows):
        spark(ROOT, CFG, "cluster", "stop")
        spark(ROOT, CFG, "cluster", "stop", "--apply")
    old_running = all("ds41f-1022-parser-rc3" in r for r in rows)
    if not old_running:
        spark(OLD, "config/cluster.json", "cluster", "start", "--replace")
        spark(OLD, "config/cluster.json", "cluster", "start", "--replace", "--apply")
    spark(OLD, "config/cluster.json", "doctor", "--live")
    site.restore_ingress(h)


def require_watchdog(window):
    receipt = OP / ("watchdog-" + window + ".json")
    try:
        ready = json.loads(receipt.read_text())
        pid = ready["pid"]
        age = time.monotonic() - ready["beat_monotonic"]
        healthy = (
            ready["window_id"] == window and ready["boot_id"] == boot()
            and 0 <= age < 3 * WATCHDOG_INTERVAL
            and type(pid) is int and pid > 0 and pid != os.getpid()
            and bool(ready["birth"]) and stamp(pid) == ready["birth"]
        )
    except (OSError, KeyError, TypeError, ValueError, subprocess.SubprocessError):
        healthy = False
    if not healthy:
        raise RuntimeError("No live independent recovery watchdog for this window")


def block_ingress(window):
    with lock():
        h = active(window)
        require_watchdog(window)
        initial_stage = site.ingress_stage(h)
        site.block(h)
        try:
            require_watchdog(window)
        except RuntimeError:
            # The current service is still running during drain.
            if initial_stage == "open":
                site.restore_ingress(h)
            raise
        if site.ingress_stage(h) != "blocked":
            raise RuntimeError("Ingress is open")


def watchdog(window):
    owned(window)
    pid = os.getpid()
    identity = {"window_id": window, "pid": pid, "birth": stamp(pid), "boot_id": boot()}
    if not identity["birth"]:
        raise RuntimeError("Cannot establish watchdog process identity")
    while True:
        try:
            h = stored()
        except FileNotFoundError:
            return
        if h.get("window_id") != window:
            return
        if h["phase"] == "recovery_failed":
            raise RuntimeError("Previous recovery failed; see the retained hold")
        atomic_json(OP / ("watchdog-" + window + ".json"),
                    {**identity, "beat_monotonic": time.monotonic()})
        if (h["phase"] == "recovering" or h["boot_id"] != boot()
                or time.monotonic() - h["beat_monotonic"] >= 900):
            restore(window, stale=True)
            # Recovery rechecks the heartbeat under the lock. If it raced with
            # a refresh, the lease is still live and still needs a watchdog.
        time.sleep(WATCHDOG_INTERVAL)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("open", "beat", "block", "reopen", "recover", "watchdog", "finish"))
    parser.add_argument("window")
    parser.add_argument("--phase")
    args = parser.parse_args()
    if args.action == "open":
        acquire(args.window)
        return
    if args.action == "watchdog":
        watchdog(args.window)
    elif args.action == "recover":
        restore(args.window)
    elif args.action == "beat":
        heartbeat(args.window, args.phase)
    elif args.action == "block":
        block_ingress(args.window)
    elif args.action == "reopen":
        with lock():
            site.restore_ingress(active(args.window))
    elif args.action == "finish":
        with lock():
            h = active(args.window)
            if site.ingress_stage(h) != "open":
                raise RuntimeError("Ingress remains blocked")
            spark(ROOT, CFG, "doctor", "--live")
            HOLD.unlink()


if __name__ == "__main__":
    main()
