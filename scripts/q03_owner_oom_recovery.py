#!/usr/bin/env python3
"""Narrow, separately frozen recovery for a b8 owner false->null OOM default.

Loads the unchanged legacy capsule's own verified recovery implementation.
Does not rewrite its source, capsule, plan, recorded hashes, or raw inspect.
No launch/start/resume interface is provided. The legacy final all-four drain
and keep-stopped policy remain authoritative.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

LEGACY_HEAD = "b8b6886b2a21d198b2be6f9a9fd6d8a35f8b1a55"
LEGACY_TREE = "574cd6a85e0c1b1eb57c5f48c5223fa0b360eeb6"


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def compatible_validate(owner, core, current, plan, row, witness):
    """Only an actual retained false witness authorizes the nullable alias."""
    require(witness["HostConfig"].get("OomKillDisable") is False,
            "recovery lacks explicit false OOM witness")
    require(owner.validate_container(witness, plan, row, initial=True) == row["config_sha256"],
            "original inert witness/hash differs")
    host = current["HostConfig"]
    require("OomKillDisable" in host and
            (host["OomKillDisable"] is None or host["OomKillDisable"] is False),
            "unexpected OOM setting; no recovery alias")
    normalized = copy.deepcopy(current)
    normalized["HostConfig"]["OomKillDisable"] = False
    # This still checks exact ID/image/labels and every original config field.
    owner.validate_container(normalized, plan, row)
    require(core.held_container_sha256(normalized) == row["config_sha256"],
            "non-OOM config drift; recovery refuses")


def quiesce(session, attempt_id, owner, core, ops=None):
    path = session.directory / "attempts" / attempt_id / "owner" / "plan.json"
    plan = owner.json_bytes(owner.read_owned_bytes(path))
    expected = dict(plan)
    wanted = expected.pop("plan_sha256")
    require(owner.digest(owner.canonical(expected)) == wanted, "retained plan digest differs")
    require(plan["attempt_id"] == attempt_id and plan["window_id"] == session.capsule["window_id"],
            "foreign owner plan")
    journal = owner.Journal(session, plan)
    try:
        ops = owner.OwnerOps(session, plan, journal, recovering=True) if ops is None else ops
        ops.journal = journal
        ops.check_authority()
        ops.seal_all()
        rows = journal.state["ranks"]
        require(len(rows) == 4 and [r["rank"] for r in rows] == list(range(4)), "four durable ranks required")
        events = [owner.json_bytes(owner.read_owned_bytes(p))
                  for p in sorted(journal.directory.glob("[0-9]*.json"))]
        witnesses = {}
        # Admit every present rank before the first stop; retain original raw data.
        for row in rows:
            rank = row["rank"]
            candidates = [e["container"] for e in events if e["kind"] == "daemon-inspect"
                          and e.get("rank") == rank and e.get("container") is not None
                          and e["container"].get("State", {}).get("Running") is False
                          and core.held_container_sha256(e["container"]) == row["config_sha256"]]
            require(candidates, "missing original pre-start config witness")
            witnesses[rank] = candidates[0]
            item = ops.inspect(rank, row)
            if item is not None:
                compatible_validate(owner, core, item, plan, row, witnesses[rank])
        for row in rows:
            rank = row["rank"]
            item = ops.inspect(rank, row)
            if item is not None:
                compatible_validate(owner, core, item, plan, row, witnesses[rank])
                if item["State"].get("Running") is True:
                    ops.action(rank, "stop", row)
                    item = ops.inspect(rank, row)
                    if item is not None:
                        compatible_validate(owner, core, item, plan, row, witnesses[rank])
                        require(item["State"].get("Running") is False, "owned daemon still running")
            if row["guard_intent"]:
                state = ops.guard_state(rank, row)
                if state.get("absent") is not True:
                    require(state.get("owned") is True, "foreign/unknown guard")
                    if state.get("active") is True:
                        ops.action(rank, "stop_guard", row)
                state = ops.guard_state(rank, row)
                require(state.get("absent") is True or
                        (state.get("owned") is True and state.get("active") is False),
                        "guard not proven inactive")
            if item is not None:
                ops.action(rank, "remove", row)
            require(ops.inspect(rank, row) is None, "exact owned ID still present")
            row["removed"] = True
            journal.save()
        ops.seal_all()
        journal.phase("drained")
        proof = dict(window_id=plan["window_id"], attempt_id=attempt_id, node_count=4,
                     daemon_quiescent=True, gpu_drain_required_by_caller=True,
                     plan_sha256=plan["plan_sha256"], compatibility="explicit-false-to-null-only")
        if not (journal.directory / "drained.json").exists():
            owner.exclusive_json(journal.directory / "drained.json", proof)
        return proof
    finally:
        journal.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-dir", required=True)
    parser.add_argument("--capsule-sha256", required=True)
    args = parser.parse_args()
    directory = Path(args.session_dir)
    require(directory.is_absolute() and directory.resolve() == directory, "session alias")
    raw = (directory / "capsule.json").read_bytes()
    require(hashlib.sha256(raw).hexdigest() == args.capsule_sha256, "capsule identity differs")
    capsule = json.loads(raw)
    manifest = capsule["manifest"]
    require(manifest["preflight"].get("original_service_disposition") == "keep-stopped",
            "recovery only permits keep-stopped")
    legacy = Path(manifest["preflight"]["independent_checkout"])
    require(legacy.is_absolute() and legacy.resolve() == legacy, "legacy checkout alias")
    def git(*arguments):
        return subprocess.check_output(["git", "--no-replace-objects", "-c", "core.fsmonitor=false",
            "-c", "core.hooksPath=/dev/null", "-C", str(legacy), *arguments], text=True).strip()
    require(git("rev-parse", "HEAD") == LEGACY_HEAD and git("rev-parse", "HEAD^{tree}") == LEGACY_TREE
            and not git("status", "--porcelain"), "legacy b8 source not exact and clean")
    sys.path.insert(0, str(legacy / "scripts"))
    import q03_window as core
    import q03_owner_model as owner
    from q03_window_watchdog import process_alive

    class RecoveryOps(core.RuntimeOps):
        def cleanup_owner(self, session, attempt_id=None):
            active = session.state.get("active_attempt")
            selected = attempt_id or (active or {}).get("attempt_id")
            require(selected is not None, "missing failed owner attempt")
            return quiesce(session, selected, owner, core)

    ops = RecoveryOps()
    session = core.Session(directory, ops)  # Legacy exact 22-file inventory.
    require(session.owns_hold(), "foreign or missing hold")
    require(not process_alive(capsule["core_process"]), "legacy core still alive")
    ready = directory / "watchdog.ready.json"
    require(not ready.exists() or not process_alive(json.loads(ready.read_text())["process"]),
            "legacy watchdog still alive")
    require(session.state.get("phase") == "recovery-failed", "only failed recovery admission")
    print(json.dumps(core.recover(directory, ops=ops)), flush=True)


if __name__ == "__main__":
    main()
