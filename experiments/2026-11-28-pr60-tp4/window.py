"""Qualify the TP4 candidate under a lease and restore the promoted 1126 service."""
import argparse
import concurrent.futures
import contextlib
import datetime as dt
import fcntl
import inspect
import json
import os
from pathlib import Path
import secrets
import shlex
import subprocess
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / "2026-10-23-1022-window"))
import ingress as site
sys.path.insert(0, str(HERE.parent / "2026-10-23-1022-recovery-executor"))
from window_executor import atomic_json, stamp
import gateway

OP = Path("/srv/ai-cluster/operations/pr60-tp4-qualification")
HOLD = Path.home() / "spark-hold.json"
OWNER = "pr60-tp4-qualification"
PREFIX = "ds41f-pr60-qualification"
JOB_NAMES = ("build", "kernel", "start", "test", "sync")
PROD = Path("/srv/ai-cluster/runtimes/1126-attention/source/spark-ds41f")
REV = "63b0c0d31e4378888951295325179211d00cbde6"
IMAGE = "sha256:3e28171ec18fcb05dac837a12c680c321fa41f9a862e230d3b966668d3792157"
PROFILE = "81ca5d96cf78a1c0193ab80071efab43d666b5a57e8633ced71b227031bde1f2"
CAND = "ds41f-1126-attention-fixes"
CFG = str(HERE.relative_to(ROOT) / "cluster.json")
MEMBERS = range(1, 5)
WATCHDOG_INTERVAL = 15
LEASE_TIMEOUT = 900
WORK_BUDGET = 3 * 3600
RESTORE_RESERVE = 30 * 60
FINAL_PHASES = {"recovering", "recovery_failed", "awaiting_gateway"}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def boot():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def cmd(args, *, cwd=None, timeout=300, capture=False):
    return subprocess.run(args, cwd=cwd, check=True, text=True, timeout=timeout,
                          stdout=subprocess.PIPE if capture else None)


def remote(node, args):
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", f"admin@10.42.0.{node}", shlex.join(args)]
    return cmd(argv, capture=True, timeout=60).stdout


@contextlib.contextmanager
def lock():
    OP.mkdir(parents=True, exist_ok=True)
    with (OP / "window.lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def stored():
    return json.loads(HOLD.read_text())


def owned(window):
    h = stored()
    if h.get("holder") != OWNER or h.get("window_id") != window:
        raise RuntimeError("Another holder owns the cluster")
    return h


def expired(h):
    mono = time.monotonic()
    return (h["boot_id"] != boot() or not 0 <= mono - h["beat_monotonic"] < LEASE_TIMEOUT
            or mono >= h["work_deadline_monotonic"])


def active(window):
    h = owned(window)
    if h["phase"] in FINAL_PHASES or expired(h):
        raise RuntimeError("Window recovery has priority")
    return h


def check_peer_reservations():
    names = ("spark-hold.json", "spark3-hold.json", "spark-request.json", "spark3-request.json")
    code = ("from pathlib import Path;import json;"
            f"print(json.dumps([n for n in {names!r} if (Path.home()/n).exists()]))")
    def check(node):
        files = json.loads(remote(node, ["python3", "-c", code]))
        if files:
            raise RuntimeError(f"Spark{node:02} has a reservation: {files}")
    with concurrent.futures.ThreadPoolExecutor(3) as pool:
        list(pool.map(check, range(2, 5)))


def rank_snapshot(prod, rev, image, profile, candidate, cfg, candidate_image, since):
    import hashlib
    import json
    from pathlib import Path
    import subprocess

    def out(*args):
        return subprocess.check_output(args, text=True, timeout=30).strip()

    profile_file = (Path("/srv/ai-cluster/profiles/deepseek-1017-attention-1126/releases")
                    / profile[:12] / "steering/heretic-1017-attn-v1.safetensors")
    if (out("git", "-C", prod, "rev-parse", "HEAD") != rev or out("git", "-C", prod, "status", "--porcelain")
            or hashlib.sha256(profile_file.read_bytes()).hexdigest() != profile
            or out("docker", "image", "inspect", "--format", "{{.Id}}", "ds41f-1126-attention:vllm-7c445f265a7b") != image):
        raise RuntimeError("The retained checkout, image or profile drifted")
    out("nvidia-smi", "-L")
    journal = out("sudo", "-n", "journalctl", "-k", "--since", "@" + since, "--no-pager", "-o", "cat")
    if "NVRM: Xid" in journal or "GPU has fallen off" in journal:
        raise RuntimeError("GPU fault since window opened")
    # docker ps must succeed even when no serving container exists.
    running = set(out("docker", "ps", "--format", "{{.Names}}").splitlines())
    owned_pids = set()
    row = {}
    for key, name, expected in (("production", "ds41f-1126-attention", image),
                                ("candidate", candidate, candidate_image)):
        row[key] = name in running
        unit = subprocess.run(["systemctl", "is-active", name + "-memguard.service"], text=True, capture_output=True, timeout=30)
        if unit.returncode not in (0, 3, 4) or unit.stderr.strip():
            raise RuntimeError("Cannot inspect " + name + " memory guard")
        row[key + "_guard"] = unit.stdout.strip() in ("active", "activating", "deactivating", "reloading")
        if row[key]:
            c = json.loads(out("docker", "inspect", name))[0]
            if not expected or c["Image"] != expected:
                raise RuntimeError("Unexpected image for running " + name)
            if key == "candidate" and c["Config"]["Labels"].get("spark.cluster-config") != cfg:
                raise RuntimeError("Unexpected profile for running " + name)
            owned_pids.update(out("docker", "top", name, "-eo", "pid").split()[1:])
    gpu_pids = set(out("nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits").split())
    if gpu_pids - owned_pids:
        raise RuntimeError("Unexpected GPU process")
    return row


def fleet(h):
    candidate_image = ""
    receipt = OP / "candidate-image.txt"
    if receipt.is_file():
        candidate_image = receipt.read_text().split()[0]
    args = [str(PROD), REV, IMAGE, PROFILE, CAND, CFG, candidate_image, str(h["since_epoch"])]
    code = inspect.getsource(rank_snapshot) + "\nimport json,sys;print(json.dumps(rank_snapshot(*sys.argv[1:])))"
    def one(node):
        return json.loads(remote(node, ["python3", "-c", code, *args]))
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        return list(pool.map(one, MEMBERS))


def production_probe():
    probe = "/usr/local/libexec/ai-cluster/services/deepseek-model-control.py"
    result = json.loads(cmd([probe, "probe"], capture=True, timeout=90).stdout)
    nodes = result["nodes"]
    if (not result["ready"] or result["failed"] or result["errors"] or set(nodes) != {"1", "2", "3", "4"}
            or any(not row["valid"] or not row["running"] or row["memguard"] != "active" for row in nodes.values())):
        raise RuntimeError("The four production ranks failed the fixed site identity and readiness probe")
    return result


def spark(root, config, *args, timeout=3600, capture=False):
    return cmd([str(root / "bin/spark"), "--shape", "tp4-ring", "--cluster-config", config, *args],
               cwd=root, timeout=timeout, capture=capture)


def transition(root, config, action, *options, pre_apply=None):
    args = ("cluster", action, *options)
    plan = spark(root, config, *args, capture=True).stdout
    print(plan, flush=True)
    if ("live:" not in plan or "--apply would refuse" in plan
            or (action == "start" and not any(p in plan for p in ("shape:   unchanged", "shape:   new engine")))):
        raise RuntimeError("The coordinated plan does not match the expected TP4 transition")
    if pre_apply:
        pre_apply()
    spark(root, config, *args, "--apply")


def acquire(window):
    uuid.UUID(window)
    with lock():
        for name in ("spark-hold.json", "spark3-hold.json", "spark-request.json", "spark3-request.json"):
            if (Path.home() / name).exists():
                raise RuntimeError("A cluster reservation already exists: " + name)
        check_peer_reservations()
        end = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=WORK_BUDGET + RESTORE_RESERVE)
        started = time.monotonic()
        h = {"holder": OWNER, "window_id": window, "since": now(), "since_epoch": int(time.time()),
             "expected_end": end.isoformat(timespec="seconds"), "heartbeat": now(), "boot_id": boot(),
             "beat_monotonic": started, "work_deadline_monotonic": started + WORK_BUDGET,
             "phase": "preparing", "rule": "Exclusive four-rank qualification and 1126 restoration"}
        rows = fleet(h)
        if not all(r["production"] and not r["candidate"] and not r["candidate_guard"] for r in rows):
            raise RuntimeError("The full retained service must be present before opening a window")
        production_probe()
        spark(PROD, "config/cluster.json", "doctor", "--live")
        atomic_json(HOLD, h, exclusive=True)


def heartbeat(window, phase=None):
    if phase in FINAL_PHASES:
        raise ValueError("Only recovery may enter this phase")
    with lock():
        h = active(window)
        h.update(heartbeat=now(), beat_monotonic=time.monotonic())
        if phase:
            h["phase"] = phase
        atomic_json(HOLD, h)


def require_watchdog(window):
    try:
        row = json.loads((OP / f"watchdog-{window}.json").read_text())
        pid = row["pid"]
        age = time.monotonic() - row["beat_monotonic"]
        valid = (row["window_id"] == window and row["boot_id"] == boot() and 0 <= age < 3 * WATCHDOG_INTERVAL
                 and type(pid) is int and pid > 0 and pid != os.getpid()
                 and bool(row["birth"]) and stamp(pid) == row["birth"])
    except (OSError, KeyError, TypeError, ValueError, subprocess.SubprocessError):
        valid = False
    if not valid:
        raise RuntimeError("No live independent recovery watchdog for this window")


def block_ingress(window):
    with lock():
        h = active(window)
        check_peer_reservations()
        require_watchdog(window)
        stage = site.ingress_stage(h)
        site.block(h)
        try:
            require_watchdog(window)
            if site.ingress_stage(h) != "blocked":
                raise RuntimeError("Ingress did not close")
        except RuntimeError:
            if stage == "open":
                site.restore_ingress(h)
            raise


def stop_production(window):
    with lock():
        h = active(window)
        require_watchdog(window)
        check_peer_reservations()
        if site.ingress_stage(h) != "blocked":
            raise RuntimeError("Cannot stop the serving group with open ingress")
        rows = fleet(h)
        if any(r["candidate"] or r["candidate_guard"] for r in rows):
            raise RuntimeError("Unexpected candidate before the production stop")
        production_probe()
        require_watchdog(window)
        transition(PROD, "config/cluster.json", "stop", "--parallel")
        if any(r["production"] or r["production_guard"] for r in fleet(h)):
            raise RuntimeError("The production stop did not cover every rank and guard")


def stop_jobs(h):
    window = h["window_id"]
    for suffix in JOB_NAMES:
        unit = f"{PREFIX}-{window}-{suffix}"
        status = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=30)
        if status.stdout.strip() in ("inactive", "failed", "unknown") and status.returncode in (3, 4):
            continue
        if status.stdout.strip() not in ("active", "activating", "deactivating", "reloading"):
            raise RuntimeError(f"Cannot establish job state: {unit}: {status.stderr.strip()}")
        cmd(["sudo", "-n", "systemctl", "stop", unit], timeout=180)
    name = "ds41f-pr60-component-check"
    proc = subprocess.run(["docker", "inspect", name], capture_output=True, text=True, timeout=30)
    if proc.returncode:
        if "No such object" in proc.stderr or "No such container" in proc.stderr:
            return
        raise RuntimeError("Cannot inspect component container: " + proc.stderr)
    c = json.loads(proc.stdout)[0]
    if c["State"]["Running"]:
        expected = (OP / "candidate-image.txt").read_text().split()[0]
        if c["Config"]["Labels"].get("local.spark-ds41f.qualification.window") != window or c["Image"] != expected:
            raise RuntimeError("Unowned component container")
        cmd(["docker", "stop", "--timeout", "30", c["Id"]], timeout=60)


def direct_probe():
    body = {"model": "deepseek-v4.1-flash", "messages": [{"role": "user", "content": "What is 6 times 7? Respond with the number only."}],
            "temperature": 0, "max_tokens": 32, "reasoning_effort": "none", "steering_profile": "heretic-1017-attn-v1", "steering_scale": 1}
    response = json.loads(site.api("http://127.0.0.1:8000", "/v1/chat/completions", body, timeout=120))
    choice = response["choices"][0]
    if choice["finish_reason"] != "stop" or choice["message"]["content"].strip() != "42" or not response["id"]:
        raise RuntimeError("Production inference with explicit scale failed")
    return response["id"]


def restore_service(h):
    stop_jobs(h)
    rows = fleet(h)
    # An interrupted transition can leave just worker ranks or guards. Restore
    # this window's full firewall rule before making changes to such a group.
    if any(r["candidate"] or r["candidate_guard"] or not (r["production"] and r["production_guard"]) for r in rows):
        if site.ingress_stage(h) != "blocked":
            site.run(site.firewall(h, "-I"))
            if site.ingress_stage(h) != "blocked":
                raise RuntimeError("Cannot isolate the group for recovery")
    if any(r["candidate"] or r["candidate_guard"] for r in rows):
        transition(ROOT, CFG, "stop", "--parallel")
        rows = fleet(h)
        if any(r["candidate"] or r["candidate_guard"] for r in rows):
            raise RuntimeError("An owned candidate rank or guard is still active")
    if not all(r["production"] and r["production_guard"] for r in rows):
        if any(r["production"] or r["production_guard"] for r in rows):
            transition(PROD, "config/cluster.json", "stop", "--parallel")
            if any(r["production"] or r["production_guard"] for r in fleet(h)):
                raise RuntimeError("Partial production group did not stop")
        transition(PROD, "config/cluster.json", "start", "--replace")
    spark(PROD, "config/cluster.json", "doctor", "--live")
    production_probe()
    return direct_probe()


def success_count():
    import re
    metrics = site.api("http://127.0.0.1:8000", "/metrics", timeout=10).decode()
    values = re.findall(r"^vllm:request_success_total(?:\{[^\n]*\})?\s+([0-9.eE+-]+)$", metrics, re.M)
    if not values or any(not float(value).is_integer() or float(value) < 0 for value in values):
        raise RuntimeError("Cannot measure successful production requests")
    return sum(int(float(value)) for value in values)


def restore(window, *, stale=False):
    with lock():
        if stale and not HOLD.exists():
            return
        h = owned(window)
        if h["phase"] == "awaiting_gateway":
            if stale:
                return h
            # A repeat explicit call may follow a new production failure.
            try:
                production_probe()
            except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
                pass
            else:
                if site.ingress_stage(h) == "open":
                    return h
        if stale and (h["phase"] == "recovery_failed" or (h["phase"] != "recovering" and not expired(h))):
            return
        h["phase"] = "recovering"
        atomic_json(HOLD, h)
        try:
            direct_id = restore_service(h)
            before = success_count()
            site.restore_ingress(h)
            if site.ingress_stage(h) != "open":
                raise RuntimeError("Production ingress did not reopen")
            h.update(phase="awaiting_gateway", challenge=secrets.token_hex(16), reopened_at=time.time(),
                     direct_request_id=direct_id, success_before_gateway=before)
            h.pop("recovery_error", None)
            atomic_json(HOLD, h)
            return h
        except Exception as error:
            h.update(phase="recovery_failed", recovery_error=str(error))
            atomic_json(HOLD, h)
            raise


def finalize(window):
    with lock():
        h = owned(window)
        if h["phase"] != "awaiting_gateway" or site.ingress_stage(h) != "open":
            raise RuntimeError("The verified production service has not reopened")
        receipt = json.loads((OP / f"gateway-{window}.json").read_text())
        gateway.validate_receipt(receipt, window, h["challenge"], h["reopened_at"])
        production_probe()
        after = success_count()
        if after < h["success_before_gateway"] + 2:
            raise RuntimeError("The local production request counter did not confirm both gateway requests")
        atomic_json(OP / f"recovered-{window}.json", {"window_id": window, "at": now(), "revision": REV,
                    "image": IMAGE, "profile": PROFILE, "direct_request_id": h["direct_request_id"],
                    "success_before_gateway": h["success_before_gateway"], "success_after_gateway": after,
                    "gateway": receipt})
        # The gateway probe runs outside this process. Recheck the lease just
        # before releasing it in case another coordinator superseded the hold.
        owned(window)
        HOLD.unlink()


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
        if h.get("holder") != OWNER or h.get("window_id") != window:
            return
        atomic_json(OP / f"watchdog-{window}.json", {**identity, "beat_monotonic": time.monotonic()})
        if h["phase"] not in ("awaiting_gateway", "recovery_failed") and (h["phase"] == "recovering" or expired(h)):
            restore(window, stale=True)
        time.sleep(WATCHDOG_INTERVAL)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("open", "beat", "block", "stop-production", "recover", "finalize", "watchdog", "status"))
    parser.add_argument("window", type=lambda value: str(uuid.UUID(value)))
    parser.add_argument("--phase")
    args = parser.parse_args()
    actions = {"open": acquire, "block": block_ingress, "stop-production": stop_production,
               "watchdog": watchdog, "recover": restore, "finalize": finalize, "status": owned}
    if args.action == "beat":
        heartbeat(args.window, args.phase)
    else:
        result = actions[args.action](args.window)
        if result is not None:
            print(json.dumps(result))


if __name__ == "__main__":
    main()
