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
from window_executor import atomic_json

OP = Path("/srv/ai-cluster/operations/1126-attention-20261128")
OLD = Path("/srv/ai-cluster/runtimes/1022-screen-20261023/source/spark-ds41f")
OLD_REV = "3e5eb7693f4b83c6232448270615e61e8e5520b2"
CFG = str(HERE.relative_to(ROOT) / "cluster.json")
HOLD = Path.home() / "spark-hold.json"
OWNER = "1126-attention-site-qualification"
PREFIX = "ds41f-1126-qualification"
UNITS = tuple(PREFIX + "-" + name for name in ("build", "kernel", "start", "test", "sync"))


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
    with lock():
        h = owned(window)
        if h["boot_id"] != boot() or h["phase"] == "recovering":
            raise RuntimeError("Window recovery has priority")
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
    with lock():
        h = owned(window)
        if stale and h["boot_id"] == boot() and time.monotonic()-h["beat_monotonic"] < 900:
            return
        if h["phase"] == "recovering" and stale:
            return
        h["phase"] = "recovering"
        atomic_json(HOLD, h)
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
    with lock():
        owned(window)
        atomic_json(OP / ("recovered-" + window + ".json"), {"at": now(), "restored": OLD_REV})
        HOLD.unlink()


def watchdog(window):
    owned(window)
    atomic_json(OP / ("watchdog-" + window + ".json"), {"ready": now(), "pid": os.getpid()})
    while HOLD.exists():
        h = stored()
        if h.get("window_id") != window:
            return
        if h.get("phase") != "recovering" and (h["boot_id"] != boot() or time.monotonic()-h["beat_monotonic"] >= 900):
            restore(window, stale=True)
            return
        time.sleep(15)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("open", "beat", "block", "reopen", "recover", "watchdog", "finish"))
    parser.add_argument("window")
    parser.add_argument("--phase")
    args = parser.parse_args()
    if args.action == "open":
        acquire(args.window)
        return
    h = owned(args.window)
    if args.action == "watchdog":
        watchdog(args.window)
    elif args.action == "recover":
        restore(args.window)
    elif args.action == "beat":
        heartbeat(args.window, args.phase)
    elif args.action == "block":
        if not (OP / ("watchdog-" + args.window + ".json")).is_file():
            raise RuntimeError("Recovery watchdog has not reported ready")
        site.block(h)
        if site.ingress_stage(h) != "blocked":
            raise RuntimeError("Ingress is open")
    elif args.action == "reopen":
        site.restore_ingress(h)
    elif args.action == "finish":
        with lock():
            owned(args.window)
            if site.ingress_stage(h) != "open":
                raise RuntimeError("Ingress remains blocked")
            spark(ROOT, CFG, "doctor", "--live")
            HOLD.unlink()


if __name__ == "__main__":
    main()
