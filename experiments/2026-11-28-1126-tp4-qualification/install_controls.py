"""Install the fixed site's rendered controls with a lease-bound rollback journal."""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
OP = Path("/srv/ai-cluster/operations/1126-attention-20261128")
HOLD = Path("/home/admin/spark-hold.json")
OWNER = "1126-attention-site-qualification"
BACKEND = "/usr/local/libexec/ai-cluster/services/deepseek-model-control.py"
TARGETS = frozenset([
    BACKEND, "/etc/ai-cluster/services/deepseek-1017-attention-1022.json",
    "/usr/local/libexec/ai-cluster/cluster-shutdown.py", "/etc/ai-cluster/cluster-power.json",
    "/etc/ai-cluster/engine-control.json", "/etc/sudoers.d/93-sparkdash-deepseek",
    "/var/lib/spark-monitor/sparkdash-launchers/deepseek-1017-attention-1022/start.sh",
    "/var/lib/spark-monitor/sparkdash-launchers/deepseek-1017-attention-1022/stop.sh",
    "/srv/ai-cluster/config/sparkdash/state/llm-launchers.json",
])
INPUTS = TARGETS | {"/usr/local/libexec/ai-cluster/" + n for n in
                    ("engine-control.py", "engine-power.py", "model-control.py")}
LOCKS = [Path("/var/lib/ai-cluster/engine-control") / f"lock-spark{n:02}-gpu0" for n in range(1, 5)]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def file_state(path):
    path = Path(path)
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode):
        raise RuntimeError("A control input is not a regular file: " + str(path))
    return dict(sha256=sha(path), mode=stat.S_IMODE(st.st_mode), uid=st.st_uid, gid=st.st_gid)


def atomic(path, data, mode, uid, gid):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data); out.flush(); os.fsync(out.fileno())
        os.chown(temporary, uid, gid)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def journal(record):
    record["updated_at"] = time.time()
    atomic(OP / "control-adoption.json", (json.dumps(record, indent=2) + "\n").encode(),
           0o644, os.geteuid(), os.getegid())


def lease(window, recovering=False):
    hold = json.loads(HOLD.read_text())
    if hold.get("holder") != OWNER or hold.get("window_id") != window:
        raise RuntimeError("Another window owns the site")
    if not recovering and (hold.get("boot_id") != Path("/proc/sys/kernel/random/boot_id").read_text().strip() or
                           hold["phase"] in ("recovering", "recovery_failed") or
                           time.monotonic() - hold["beat_monotonic"] >= 900):
        raise RuntimeError("Recovery has priority")


@contextlib.contextmanager
def locks():
    with contextlib.ExitStack() as stack:
        for path in LOCKS:
            file = stack.enter_context(path.open("a"))
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def prepare(window):
    lease(window)
    if (OP / "control-adoption.json").exists():
        raise RuntimeError("A control adoption journal already exists")
    before = {}
    for raw in sorted(INPUTS):
        path = Path(raw)
        before[raw] = file_state(path)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != before[raw]["sha256"]:
            raise RuntimeError("A control input changed while being captured: " + raw)
        atomic(OP / "controls-input" / raw.lstrip("/"), data, 0o644, os.geteuid(), os.getegid())
        if raw in TARGETS:
            atomic(OP / "controls-backup" / raw.lstrip("/"), data, 0o600, os.geteuid(), os.getegid())
    record = dict(window_id=window, status="prepared", installer_sha256=sha(__file__), before=before)
    journal(record)
    return record


def record_for(window):
    lease(window, recovering=True)
    record = json.loads((OP / "control-adoption.json").read_text())
    if record.get("window_id") != window or record.get("installer_sha256") != sha(__file__):
        raise RuntimeError("Control rollback identity changed")
    if set(record["before"]) != set(INPUTS):
        raise RuntimeError("Unexpected control backup scope")
    if record.get("status") not in {"prepared", "installing", "installed", "rolling_back", "rolled_back"}:
        raise RuntimeError("Unexpected control adoption state")
    return record


def restore(record):
    # Validate the entire transaction before replacing any file. An unknown
    # subsequent edit belongs to its author and must not be overwritten.
    after = record.get("after", {})
    for raw, before in record["before"].items():
        current = file_state(raw)
        allowed = [before]
        if raw in after:
            allowed.append({**before, **after[raw]})
        if current not in allowed:
            raise RuntimeError("Control input changed outside this transaction: " + raw)
        if raw in TARGETS and sha(OP / "controls-backup" / raw.lstrip("/")) != before["sha256"]:
            raise RuntimeError("Control backup changed: " + raw)
    record["status"] = "rolling_back"; journal(record)
    for raw in sorted(TARGETS):
        old = record["before"][raw]
        if file_state(raw) != old:
            atomic(Path(raw), (OP / "controls-backup" / raw.lstrip("/")).read_bytes(),
                   old["mode"], old["uid"], old["gid"])
    if any(file_state(raw) != record["before"][raw] for raw in INPUTS):
        raise RuntimeError("Control rollback did not restore every input")
    record["status"] = "rolled_back"; journal(record)
    return record


def install(window):
    lease(window)
    record = record_for(window)
    if record["status"] != "prepared":
        raise RuntimeError("Control adoption is not prepared")
    stage = OP / "controls-rendered"
    manifest = json.loads((stage / "install-manifest.json").read_text())
    if set(manifest["files"]) != set(TARGETS) or manifest["retire_after_verification"]:
        raise RuntimeError("Unexpected control installation scope")
    revision = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    if revision != manifest["revision"] or subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain"]):
        raise RuntimeError("Publish and synchronize the exact control source first")
    for raw, before in record["before"].items():
        if file_state(raw) != before:
            raise RuntimeError("A captured host input changed: " + raw)
    for raw, entry in manifest["files"].items():
        if set(entry) != {"sha256", "mode"} or entry["mode"] not in (0o644, 0o755, 0o440):
            raise RuntimeError("Unexpected rendered control metadata: " + raw)
        if sha(stage / raw.lstrip("/")) != entry["sha256"]:
            raise RuntimeError("A rendered control file changed: " + raw)
    subprocess.run(["/usr/sbin/visudo", "-cf", str(stage / "etc/sudoers.d/93-sparkdash-deepseek")], check=True)
    lease(window)
    record.update(status="installing", revision=revision, baseline=manifest["baseline"], after=manifest["files"])
    journal(record)
    try:
        for raw, entry in sorted(manifest["files"].items()):
            old = record["before"][raw]
            atomic(Path(raw), (stage / raw.lstrip("/")).read_bytes(), entry["mode"], old["uid"], old["gid"])
        if any(file_state(raw) != {**record["before"][raw], **entry}
               for raw, entry in manifest["files"].items()):
            raise RuntimeError("Installed control identity differs")
        subprocess.run(["/usr/sbin/visudo", "-c"], check=True)
        # Installation needs root; the fixed service coordinator and its SSH
        # identity deliberately require admin, just as normal registry calls do.
        probe = json.loads(subprocess.check_output(
            ["/usr/bin/sudo", "-n", "-H", "-u", "admin", "--", BACKEND, "probe"],
            text=True, timeout=90))
        if not probe.get("ready") or probe.get("failed"):
            raise RuntimeError("The installed fixed service is not ready")
        record.update(status="installed", probe=probe); journal(record)
    except BaseException:
        restore(record)
        raise
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "install", "rollback"])
    parser.add_argument("window")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("The fixed control installation requires root")
    with locks():
        result = (prepare(args.window) if args.action == "prepare" else install(args.window)
                  if args.action == "install" else restore(record_for(args.window)))
    print(json.dumps({"status": result["status"], "window_id": args.window}))


if __name__ == "__main__":
    main()
