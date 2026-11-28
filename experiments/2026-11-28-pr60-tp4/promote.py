"""Promote the qualified RC3 image on the site TP4 ring with journaled rollback."""
import argparse
import concurrent.futures
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import uuid

import qualify
import run
import window as w

ROOT = w.ROOT
CFG = "config/cluster.json"
NEW = Path("/srv/ai-cluster/runtimes/1126-attention-fixes/source/spark-ds41f")
OPERATIONS = Path("/srv/ai-cluster/operations/pr60-tp4-promotion")
IMAGE = "sha256:5d6e6aa4747baa66fb5f691758087df9a4c482a2914d093fe3d24acea5a31394"
PROFILE = "c9c7663dade8685f5d7932b65a7476a58647981b4cfc3c59f008c6dcf080b3f6"
POLICY = Path("/etc/ai-cluster/services/deepseek-1017-attention-1022.json")


def configure(window):
    w.OWNER = "pr60-tp4-promotion"
    w.PREFIX = "ds41f-pr60-promotion"
    w.OP = OPERATIONS / window
    w.CFG = qualify.CFG = CFG
    w.WORK_BUDGET = 3600
    w.FINAL_PHASES = {*w.FINAL_PHASES, "rollback_requested", "finalizing"}


def release_inputs(revision):
    from spark_ds41f.config import configuration
    if ROOT != NEW or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise RuntimeError("Expected the fixed RC3 checkout and a full published revision")
    if run.output(["git", "-C", str(ROOT), "rev-parse", "HEAD"]) != revision or run.output(
            ["git", "-C", str(ROOT), "status", "--porcelain"]):
        raise RuntimeError("The local release checkout is not clean at the requested revision")
    cluster, site, _ = configuration(argparse.Namespace(cluster_config=CFG, shape="tp4-ring"))
    opts = cluster["serve_args"]
    profile = json.loads(opts[opts.index("--additional-config") + 1])["ds41_steering"]
    baseline = json.loads((ROOT / "manifests/baselines" / (cluster["promoted_baseline"] + ".json")).read_bytes())
    if (cluster["container"]["name"] != w.CAND or cluster["container"]["image"] != run.TAG
            or cluster["deployment"]["path"] != str(NEW) or cluster["deployment"]["branch"] != "homelab/main"
            or cluster["container"]["expected_labels"]["local.spark-ds41f.vllm.tree"] != run.TREE
            or profile["profile_sha256"] != PROFILE or baseline["image_id"] != IMAGE
            or {n["name"] for n in site["nodes"]} != {f"spark{i:02}" for i in w.MEMBERS} or len(site["nodes"]) != 4):
        raise RuntimeError("The promoted recipe does not select the qualified four-rank RC3 payload")
    return cluster, site["nodes"], profile


def open_window(window, revision):
    release_inputs(revision)
    w.cmd(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", revision, "refs/remotes/origin/homelab/main"])
    w.OP.mkdir(parents=True, exist_ok=True)
    # A refused preflight can be retried with the same UUID. An operation
    # with any admitted lease or recovery artifacts can never be reused.
    marker = w.OP / "release-plan.json"
    with w.lock():
        record = {"window_id": window, "revision": revision}
        allowed = {"window.lock", "release-plan.json", "candidate-image.txt"}
        if w.HOLD.exists() or any(p.name not in allowed for p in w.OP.iterdir()):
            raise RuntimeError("This UUID has been used or a cluster hold exists")
        if marker.exists() and json.loads(marker.read_bytes()) != record:
            raise RuntimeError("The existing promotion plan identifies a different commit")
        w.atomic_json(marker, record)
        (w.OP / "candidate-image.txt").write_text(IMAGE + " " + run.TAG + "\n")
    w.acquire(window)
    with w.lock():
        h = w.owned(window)
        h.update(release_revision=revision, rule="Exclusive four-Spark RC3 production promotion with rollback")
        w.atomic_json(w.HOLD, h)


def permit(window, *, isolated=False):
    h = w.active(window)
    w.require_watchdog(window)
    if isolated and w.site.ingress_stage(h) != "blocked":
        raise RuntimeError("This transition requires drained ingress")
    return h


def controls(window, action):
    w.cmd(["sudo", "-n", "python3", "-B", str(ROOT / "host/site/install_controls.py"), action, window])


def verify_release(h):
    cluster, nodes, profile = release_inputs(h["release_revision"])
    rows = w.fleet(h)
    if any(r["production"] or r["production_guard"] or not (r["candidate"] and r["candidate_guard"]) for r in rows):
        raise RuntimeError("Expected exactly the four new release ranks and guards")
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        return dict(pool.map(lambda n: qualify.rank_identity(n, cluster, IMAGE, h["release_revision"], profile), nodes))


def probe_controls(revision, image):
    policy = json.loads(POLICY.read_bytes())
    if policy["revision"] != revision or policy["image_id"] != image:
        raise RuntimeError("Installed control policy selects a different release")
    return w.production_probe()


def offer_gateway(window, target, direct, ranks=None):
    with w.lock():
        h = w.owned(window) if target == "restored" else permit(window, isolated=True)
        before = w.success_count()
        # Publish a new challenge only after ingress is verified open.
        w.site.restore_ingress(h)
        if w.site.ingress_stage(h) != "open":
            raise RuntimeError("Ingress did not reopen")
        h.update(phase="awaiting_gateway", target=target, challenge=secrets.token_hex(16),
                 reopened_at=time.time(), direct_request_id=direct, success_before_gateway=before,
                 ranks=ranks, beat_monotonic=time.monotonic(), heartbeat=w.now())
        h.pop("recovery_error", None)
        w.atomic_json(w.HOLD, h)


def require_unit(window, suffix):
    unit = f"{w.PREFIX}-{window}-{suffix}.service"
    main_pid = run.output(["systemctl", "show", unit, "--property=MainPID", "--value"])
    if main_pid != str(os.getpid()):
        raise RuntimeError("Expected this executable to be the main process of " + unit)


def stop_previous(window):
    def verify():
        permit(window, isolated=True)
        w.check_peer_reservations()
    verify()
    h = w.owned(window)
    if any(r["candidate"] or r["candidate_guard"] or not (r["production"] and r["production_guard"])
           for r in w.fleet(h)):
        raise RuntimeError("The old production group changed before the coordinated stop")
    probe_controls(w.REV, w.IMAGE)
    # The watchdog can fence the controller even if a node stalls during stop.
    w.transition(w.PROD, CFG, "stop", "--parallel", pre_apply=verify)
    if any(any(r.values()) for r in w.fleet(h)):
        raise RuntimeError("The complete serving group did not stop")


def execute(window):
    require_unit(window, "controller")
    permit(window)
    job = run.Run(window)
    try:
        w.heartbeat(window, "preparing-controls")
        controls(window, "prepare")
        w.cmd(["python3", "-B", str(ROOT / "host/site/render_controls.py"), "--existing-root",
               str(w.OP / "controls-input"), "--output", str(w.OP / "controls-rendered")])
        h = permit(window)
        revision = h["release_revision"]
        plan = w.spark(ROOT, CFG, "cluster", "sync", capture=True).stdout
        print(plan, flush=True)
        if "plan only" not in plan or any(f"spark{i:02}: fetch origin and detach {NEW} at {revision}" not in plan
                                          for i in w.MEMBERS):
            raise RuntimeError("Incorrect four-rank sync plan")
        w.heartbeat(window, "synchronizing")
        permit(window)
        w.check_peer_reservations()
        w.spark(ROOT, CFG, "cluster", "sync", "--apply")
        for n in w.MEMBERS:
            if (w.remote(n, ["git", "-C", str(NEW), "rev-parse", "HEAD"]).strip() != revision or
                    w.remote(n, ["git", "-C", str(NEW), "status", "--porcelain"]).strip() or
                    w.remote(n, ["docker", "image", "inspect", "--format", "{{.Id}}", run.TAG]).strip() != IMAGE or
                    w.remote(n, ["sha256sum", str(Path("/srv/ai-cluster/profiles/deepseek-1017-attention-1126/releases")
                             / PROFILE[:12] / "steering/heretic-1017-attn-v1.safetensors")]).split()[0] != PROFILE):
                raise RuntimeError(f"spark{n:02} has unexpected synchronized release inputs")
        w.heartbeat(window, "draining")
        w.block_ingress(window)
        stop_previous(window)
        job.stage("start", "start", [sys.executable, "-B", __file__, "start", window], minutes=20)
        h = permit(window, isolated=True)
        ranks = verify_release(h)
        w.spark(ROOT, CFG, "doctor", "--live")
        w.heartbeat(window, "installing-controls")
        permit(window, isolated=True)
        controls(window, "install")
        probe_controls(revision, IMAGE)
        w.atomic_json(w.OP / "verified-ranks.json", ranks)
        offer_gateway(window, "promoted", w.direct_probe(), ranks)
    except BaseException as exc:
        with w.lock():
            h = w.owned(window)
            if h["phase"] not in ("recovering", "recovery_failed"):
                h.update(phase="rollback_requested", requested_reason=str(exc))
                w.atomic_json(w.HOLD, h)
        raise


@contextlib.contextmanager
def recovery_lock():
    with (w.OP / "recovery.lock").open("a") as fd:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield


def recover(window, *, stale=False):
    with recovery_lock():
        with w.lock():
            if stale and not w.HOLD.exists():
                return
            h = w.owned(window)
            if h["phase"] == "finalizing":
                return complete_decision(h)
            if stale and (h["phase"] == "recovery_failed" or h["phase"] == "awaiting_gateway" and h.get("target") == "restored"
                          or h["phase"] not in ("recovering", "rollback_requested") and not w.expired(h)):
                return
            h.update(phase="recovering")
            w.atomic_json(w.HOLD, h)
        try:
            # Fence the controller before undoing any host changes. The startup
            # child has its own unit and is stopped by restore_service as well.
            unit = f"{w.PREFIX}-{window}-controller"
            state = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=30)
            if state.stdout.strip() in ("active", "activating", "deactivating", "reloading"):
                w.cmd(["sudo", "-n", "systemctl", "stop", unit], timeout=180)
            elif state.returncode not in (3, 4) or state.stdout.strip() not in ("inactive", "unknown", "failed"):
                raise RuntimeError("Could not fence the promotion controller: " + state.stderr.strip())
            if (w.OP / "control-adoption.json").is_file():
                controls(window, "rollback")
            direct = w.restore_service(h)
            probe_controls(w.REV, w.IMAGE)
            offer_gateway(window, "restored", direct)
        except BaseException as exc:
            with w.lock():
                h = w.owned(window)
                h.update(phase="recovery_failed", recovery_error=str(exc))
                w.atomic_json(w.HOLD, h)
            raise


def complete_decision(h):
    # The accepted decision is journaled in the lease before publishing its
    # standalone receipt. Replaying this tail cannot change a chosen release.
    window = h["window_id"]
    row = h["decision"]
    if h["phase"] != "finalizing" or row["window_id"] != window or row["target"] not in ("promoted", "restored"):
        raise RuntimeError("Inconsistent finalization journal")
    w.atomic_json(w.OP / (row["target"] + ".json"), row)
    w.owned(window)
    w.HOLD.unlink()
    return row


def finalize(window):
    with w.lock():
        h = w.owned(window)
        if h["phase"] == "finalizing":
            return complete_decision(h)
        if h["phase"] != "awaiting_gateway" or w.site.ingress_stage(h) != "open":
            raise RuntimeError("No release is ready for external gateway acceptance")
        target = h["target"]
        if target not in ("promoted", "restored"):
            raise RuntimeError("Unknown release target")
        receipt = json.loads((w.OP / ("gateway-" + h["challenge"] + ".json")).read_bytes())
        w.gateway.validate_receipt(receipt, window, h["challenge"], h["reopened_at"])
        if target == "promoted":
            if w.expired(h) or verify_release(h) != h["ranks"]:
                raise RuntimeError("The promotion expired or a verified rank restarted")
            revision, image, profile = h["release_revision"], IMAGE, PROFILE
        else:
            revision, image, profile = w.REV, w.IMAGE, w.PROFILE
            if any(not (r["production"] and r["production_guard"]) or r["candidate"] or r["candidate_guard"]
                   for r in w.fleet(h)):
                raise RuntimeError("Incomplete rollback")
        probe = probe_controls(revision, image)
        after = w.success_count()
        if after < h["success_before_gateway"] + 2:
            raise RuntimeError("The chosen backend did not confirm both gateway calls")
        row = dict(window_id=window, at=w.now(), target=target, revision=revision, image=image, profile=profile,
                   direct_request_id=h["direct_request_id"], success_before_gateway=h["success_before_gateway"],
                   success_after_gateway=after, gateway=receipt, ranks=h.get("ranks"),
                   readiness={k: {f: v[f] for f in ("valid", "running", "memguard")} for k,v in probe["nodes"].items()})
        h.update(phase="finalizing", decision=row)
        w.atomic_json(w.HOLD, h)
        return complete_decision(h)


def watchdog(window):
    w.owned(window)
    pid = os.getpid()
    ident = dict(window_id=window, pid=pid, birth=w.stamp(pid), boot_id=w.boot())
    if not ident["birth"]:
        raise RuntimeError("Missing independent watchdog process identity")
    while w.HOLD.exists():
        try:
            h = w.stored()
        except FileNotFoundError:
            return
        if h.get("holder") != w.OWNER or h.get("window_id") != window:
            return
        w.atomic_json(w.OP / f"watchdog-{window}.json", {**ident, "beat_monotonic": time.monotonic()})
        if h["phase"] in ("finalizing", "recovering", "rollback_requested") or w.expired(h):
            recover(window, stale=True)
        time.sleep(w.WATCHDOG_INTERVAL)


def main():
    sys.path.insert(0, str(ROOT))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("open", "execute", "start", "watchdog", "recover", "finalize", "status"))
    parser.add_argument("window", type=lambda s: str(uuid.UUID(s)))
    parser.add_argument("--revision")
    a = parser.parse_args()
    configure(a.window)
    if a.action == "open":
        open_window(a.window, a.revision or "")
    elif a.action == "start":
        require_unit(a.window, "start")
        def check():
            permit(a.window, isolated=True)
            w.check_peer_reservations()
        check()
        w.transition(ROOT, CFG, "start", "--replace", pre_apply=check)
    else:
        fn = {"execute": execute, "watchdog": watchdog, "recover": recover, "finalize": finalize, "status": w.owned}
        result = fn[a.action](a.window)
        if result is not None:
            print(json.dumps(result))


if __name__ == "__main__":
    main()
