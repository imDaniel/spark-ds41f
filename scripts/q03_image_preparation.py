#!/usr/bin/env python3
"""Fixed same-image preparation inside a NEW recovery-owned Q03 capsule.

No import-time actions, standalone hold, arbitrary argv or service operation.
The core owns the child and watchdog; every mutating helper epoch is durable
before dispatch. Recovery fences and waits for positive terminal receipts,
including Docker load, before it may restore the registered original service.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import sys
import time
import uuid

from q03_held_window import exclusive_json, read_owned_bytes
from q03_original_spark_adapter import evidence_path, load_remote_helper
from q03_owner_model import IMAGE, canonical, digest, remote_nodes, validate_owner_binding

DOCKER = ["sudo", "-n", "docker", "--host", "unix:///var/run/docker.sock"]


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def validate_binding(manifest):
    value = manifest.get("image_preparation")
    require(isinstance(value, dict) and set(value) == {
        "schema_version", "archive_path", "archive_sha256", "archive_size",
        "export_receipt_path", "export_receipt_sha256"}, "fixed image preparation binding required")
    require(type(value["schema_version"]) is int and value["schema_version"] == 1,
            "unsupported image preparation schema")
    require(type(value["archive_size"]) is int and value["archive_size"] > 0,
            "positive archive byte count required")
    import re
    for key in ("archive_sha256", "export_receipt_sha256"):
        require(isinstance(value[key], str) and re.fullmatch(r"[0-9a-f]{64}", value[key]),
                "pinned image preparation hash required")
    archive = evidence_path(value["archive_path"], manifest)
    receipt = evidence_path(value["export_receipt_path"], manifest)
    require(str(archive) == value["archive_path"] and str(receipt) == value["export_receipt_path"]
            and archive != receipt, "canonical distinct preparation inputs required")
    for path in (archive, receipt):
        for parent in path.parents:
            require(not parent.is_symlink(), "image input ancestry alias")
            info = parent.stat()
            require(info.st_uid in (0, os.getuid()) and not info.st_mode & 0o022,
                    "image input ancestry is not owner-controlled")
    info = archive.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            and not info.st_mode & 0o022 and info.st_size == value["archive_size"],
            "archive identity/type/owner/size differs")
    raw = read_owned_bytes(receipt)
    require(digest(raw) == value["export_receipt_sha256"], "export receipt changed")
    proof = json.loads(raw)
    require(proof.get("result") == "passed" and proof.get("path") == str(archive)
            and proof.get("archive_sha256") == value["archive_sha256"]
            and proof.get("size") == value["archive_size"]
            and proof.get("config_sha256") == IMAGE.removeprefix("sha256:")
            and proof.get("original_image_tag_bindings_unchanged") is True,
            "successful source-export identity required")
    entries = proof.get("manifest")
    require(isinstance(entries, list) and len(entries) == 1 and not entries[0].get("RepoTags"),
            "this preparation accepts only the measured tagless single image")
    h = hashlib.sha256()
    with archive.open("rb") as stream:
        for block in iter(lambda: stream.read(16 * 1024 * 1024), b""):
            h.update(block)
    require(h.hexdigest() == value["archive_sha256"], "source archive SHA differs")
    return dict(value)


PROBE = r'''
import hashlib,json,os,pathlib,platform,stat,subprocess,sys
v=json.loads(sys.argv[1]); root=pathlib.Path(v['root']);target=pathlib.Path(v['target'])
assert root.resolve()==root and target.parent==root and not target.is_symlink()
assert platform.node()==v['hostname']
for p in (root,*root.parents):
 s=p.lstat();assert stat.S_ISDIR(s.st_mode) and s.st_uid in (0,os.getuid()) and not s.st_mode&0o022
d=['sudo','-n','docker','--host','unix:///var/run/docker.sock']
def run(*a):return subprocess.check_output(d+list(a),text=True,timeout=90)
version=json.loads(run('version','--format','{{json .}}'));assert tuple(map(int,version['Server']['ApiVersion'].split('.')))>=(1,48)
info=json.loads(run('info','--format','{{json .}}'))
ids=sorted(set(run('image','ls','--all','--no-trunc','--quiet').split()))
images=json.loads(run('image','inspect',*ids));assert sorted(i['Id'] for i in images)==ids
assert sorted(set(run('image','ls','--all','--no-trunc','--quiet').split()))==ids
containers=sorted(set(run('container','ls','--all','--no-trunc','--quiet').split()))
objects=json.loads(run('container','inspect',*containers)) if containers else []
free=os.statvfs(root);available=free.f_bavail*free.f_frsize
blocks,size=map(int,subprocess.check_output(['sudo','-n','stat','-f','--format=%a:%S',info['DockerRootDir']],text=True,timeout=30).strip().split(':'))
assert available>4*v['size'] and blocks*size>4*v['size']
file=None
if v['file']:
 p=target/'candidate.tar.partial';s=p.lstat();assert stat.S_ISREG(s.st_mode) and s.st_uid==os.getuid() and stat.S_IMODE(s.st_mode)==0o600 and s.st_size==v['size']
 h=hashlib.sha256()
 with p.open('rb') as stream:
  for block in iter(lambda:stream.read(16*1024*1024),b''):h.update(block)
 assert h.hexdigest()==v['sha256'];file={'sha256':h.hexdigest(),'size':s.st_size}
print(json.dumps({'images':images,'containers':objects,'archive':file,'docker_api':version['Server']['ApiVersion']}))
'''

CREATE_DIRECTORY = r'''
import os,pathlib,stat,sys
root=pathlib.Path(sys.argv[1]);target=pathlib.Path(sys.argv[2]);assert target.parent==root and target.resolve()==target
for p in (root,*root.parents):
 s=p.lstat();assert stat.S_ISDIR(s.st_mode) and s.st_uid in (0,os.getuid()) and not s.st_mode&0o022 and not p.is_symlink()
os.umask(0o077);target.mkdir(mode=0o700,exist_ok=False)
'''


def preserve_images(before, after):
    old = {item["Id"]: item for item in before}
    new = {item["Id"]: item for item in after}
    require(set(old) <= set(new) and set(new) - set(old) <= {IMAGE},
            "unrelated image-store writer or lost original image")
    require(all(new[key] == item for key, item in old.items()), "original image/tag identity changed")


def same_containers(before, after):
    """Compare full normalized inspect bytes, preserving JSON value types."""
    from q03_window import held_container_projection
    def normalize(values):
        return sorted((dict(item, **held_container_projection(item)) for item in values),
                      key=lambda item: canonical(item))
    return canonical(normalize(before)) == canonical(normalize(after))


class Preparation:
    def __init__(self, session, *, recovery=False):
        self.session, self.recovery = session, recovery
        self.root = session.directory / "image-preparation"
        self.pre = session.manifest["preflight"]
        self.helper = load_remote_helper(self.pre["remote_helper_sha256"])
        require(self.pre["remote_helper_sha256"] == session.capsule["recovery_source_sha256"]["q03_remote_operation.py"],
                "preparation helper differs from recovery capsule")
        self.nodes = remote_nodes(validate_owner_binding(session.manifest, session.ops))
        started = session.state.get("restoring_started_epoch", time.time())
        self.end = (started + session.capsule["recovery_timeout_s"] if recovery
                    else session.capsule["deadline_epoch"])

    def check(self):
        require(self.session.owns_hold(), "preparation lost hold")
        require(self.session.ops.recovery_sources() == self.session.capsule["recovery_source_sha256"],
                "preparation/recovery source changed")
        require(time.time() < self.end, "bounded preparation/drain deadline reached")
        if not self.recovery:
            from q03_window_watchdog import process_alive
            require(process_alive(self.session.capsule["core_process"]), "preparation core died/reused")
            require(not self.session.requested(), "cluster requested; no new preparation action")
            state = self.session.ops.read_json(self.session.directory / "state.json")
            require(state.get("phase") == "image-preparation" and not state.get("active_attempt"),
                    "preparation is outside its sole core-owned stage")

    def transport(self, rank, argv, timeout=90):
        self.check()
        from q03_window_preflight import _node_argv
        result = self.session.ops.run(_node_argv(self.pre, self.nodes[rank], argv),
                                      timeout=min(timeout, max(1, self.end - time.time())), check=False)
        exclusive_json(self.root / ("rpc-" + uuid.uuid4().hex + ".json"), {
            "rank": rank, "argv": argv, "exit_code": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr})
        require(result.returncode == 0, "preparation remote outcome UNKNOWN")
        return result.stdout

    def rpc(self, record, action, extra=()):
        args = [action, "--root", str(Path(self.session.manifest["independent_root"]) / "remote-operations"),
                "--window", self.session.capsule["window_id"], "--epoch", record["epoch"],
                "--authority", self.pre["remote_authority_path"],
                "--authority-sha256", self.pre["remote_authority_sha256"], *extra]
        wire = self.helper.verified_helper_command(self.pre["remote_helper_path"], self.pre["remote_helper_sha256"], args)
        wire[0] = "python3"
        value = json.loads(self.transport(record["rank"], wire))
        require(value.get("window") == self.session.capsule["window_id"], "foreign preparation receipt")
        require(value.get("epoch") == record["epoch"] if action == "seal" else
                value.get("operation") == record["operation"], "foreign preparation epoch/operation")
        return value

    def drain(self, record):
        # Seal before polling: a delayed launch cannot arrive after this fence.
        # Do not cancel an accepted Docker load and mistake CLI death for daemon
        # quiescence. Existing bounded supervisor must finish positively.
        value = self.rpc(record, "seal")
        while value.get("safe_to_overlap") is not True:
            pending = value.get("operations", [])
            require(value.get("sealed") is True and pending and all(
                row.get("safe_to_overlap") is True or row.get("status") == "running" for row in pending),
                "preparation daemon/epoch UNKNOWN; retain hold and refuse restoration")
            time.sleep(.2)
            value = self.rpc(record, "seal")
        exclusive_json(self.root / ("sealed-" + record["epoch"] + "-" + uuid.uuid4().hex + ".json"), value)

    def mutate(self, rank, kind, argv, timeout):
        self.check()
        record = dict(rank=rank, kind=kind, epoch=uuid.uuid4().hex, operation=uuid.uuid4().hex,
                      argv_sha256=digest(canonical(argv)))
        # Fsync intent before any remote mkdir/transfer/load can be dispatched.
        exclusive_json(self.root / ("intent-" + record["operation"] + ".json"), record)
        value = self.rpc(record, "launch", ("--operation", record["operation"],
            "--argv-json", json.dumps(self.helper.bound_command(argv)),
            "--cwd", self.session.manifest["independent_root"], "--timeout-seconds", str(timeout)))
        while value.get("status") != "terminal":
            self.check()
            require(not (value.get("status") == "uncertain" and value.get("state", {}).get("supervisor")),
                    "preparation supervisor lost; outcome UNKNOWN")
            time.sleep(.2)
            value = self.rpc(record, "status", ("--operation", record["operation"]))
        exclusive_json(self.root / ("terminal-" + record["operation"] + ".json"), value)
        require(value.get("action_succeeded") is True, "preparation action failed/UNKNOWN")
        self.drain(record)

    def probe(self, rank, target, binding, *, file=False):
        payload = dict(root=self.session.manifest["independent_root"], target=str(target),
                       hostname=self.nodes[rank]["expected_hostname"], size=binding["archive_size"],
                       sha256=binding["archive_sha256"], file=file)
        return json.loads(self.transport(rank, self.helper.bound_command(
            ["python3", "-I", "-B", "-c", PROBE, json.dumps(payload)]), 180))

    def run(self):
        binding = validate_binding(self.session.manifest)
        self.root.mkdir(mode=0o700, exist_ok=False)
        exclusive_json(self.root / "binding.json", binding)
        source = json.loads(self.transport(0, self.helper.bound_command(DOCKER + ["image", "inspect", IMAGE])))[0]
        # The immutable source may retain its candidate build tag. Only the
        # hash-bound export archive and newly imported peers must be tagless;
        # saving by ID does not remove a source tag, and we never retag it.
        require(source["Id"] == IMAGE, "source image identity changed")
        for rank in (1, 2, 3):
            self.check()
            target = Path(self.session.manifest["independent_root"]) / (
                "image-receive-" + self.session.capsule["window_id"] + "-" + str(rank))
            before = self.probe(rank, target, binding)
            require(all(item["State"].get("Running") is False for item in before["containers"]),
                    "preparation requires all original/candidate containers stopped")
            exclusive_json(self.root / ("before-" + str(rank) + ".json"), before)
            existing = next((item for item in before["images"] if item["Id"] == IMAGE), None)
            if existing is None:
                self.mutate(rank, "prepare", ["python3", "-I", "-B", "-c", CREATE_DIRECTORY,
                                             self.session.manifest["independent_root"], str(target)], 60)
                node = self.nodes[rank]
                peer = ["ssh", "-T", "-i", self.pre["ssh_identity"], "-o", "BatchMode=yes",
                        "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10", node["ssh_host"]]
                receiver = "umask 077; set -C; cat > " + shlex.quote(str(target / "candidate.tar.partial"))
                transfer = shlex.join([*peer, receiver]) + " < " + shlex.quote(binding["archive_path"])
                self.mutate(0, "transfer-" + str(rank), ["sh", "-c", transfer], 1200)
                received = self.probe(rank, target, binding, file=True)
                preserve_images(before["images"], received["images"])
                require(same_containers(before["containers"], received["containers"]), "containers changed during transfer")
                self.mutate(rank, "load", DOCKER + ["image", "load", "--platform", "linux/arm64",
                                                   "--input", str(target / "candidate.tar.partial")], 600)
            after = self.probe(rank, target, binding, file=existing is None)
            preserve_images(before["images"], after["images"])
            require(same_containers(before["containers"], after["containers"]), "containers changed during image preparation")
            item = next(item for item in after["images"] if item["Id"] == IMAGE)
            require(item["Config"] == source["Config"] and item["RootFS"] == source["RootFS"]
                    and item["Os"] == "linux" and item["Architecture"] == "arm64"
                    and (existing is not None or not item.get("RepoTags")),
                    "prepared peer image identity differs")
            exclusive_json(self.root / ("after-" + str(rank) + ".json"), after)
        exclusive_json(self.root / "result.json", dict(result="passed", image=IMAGE, nodes=4,
                       scope="same-image preparation only; not GPU/model readiness"))

    def quiesce(self):
        if not self.root.exists():
            return {"quiescent": True, "no_preparation_intent": True}
        binding = json.loads(read_owned_bytes(self.root / "binding.json"))
        require(binding == self.session.manifest["image_preparation"], "retained preparation binding changed")
        for path in sorted(self.root.glob("intent-*.json")):
            record = json.loads(read_owned_bytes(path))
            require(type(record["rank"]) is int and record["rank"] in range(4), "foreign preparation rank")
            self.drain(record)
        # Registered original/owner transitions may have legitimately changed
        # containers since preparation. Their recovery paths prove daemon/GPU
        # drain; this path separately preserves every pre-preparation image/tag.
        for rank in (1, 2, 3):
            before = self.root / ("before-" + str(rank) + ".json")
            if before.exists():
                ids = self.transport(rank, self.helper.bound_command(DOCKER + ["image", "ls", "--all", "--no-trunc", "--quiet"])).split()
                images = json.loads(self.transport(rank, self.helper.bound_command(DOCKER + ["image", "inspect", *sorted(set(ids))])))
                preserve_images(json.loads(read_owned_bytes(before))["images"], images)
        return {"quiescent": True, "positive_remote_epoch_drain": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "quiesce"))
    parser.add_argument("--session-dir", required=True)
    args = parser.parse_args()
    from q03_window import RuntimeOps, Session
    session = Session(args.session_dir, RuntimeOps())
    preparation = Preparation(session, recovery=args.action == "quiesce")
    result = preparation.run() if args.action == "run" else preparation.quiesce()
    print(json.dumps({"window_id": session.capsule["window_id"], **(result or {"result": "passed"})}))


if __name__ == "__main__":
    main()
