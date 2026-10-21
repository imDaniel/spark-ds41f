#!/usr/bin/env python3
"""Fixed Q03 P0/G1/G2 attempts inside one immutable bounded outer window.

No command is executed on import. The existing window owns all process/service
operations and final restoration. A control receipt acknowledges a request only,
never candidate quiescence or restoration. No controller-generation handoff.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid


class CloseWindow(RuntimeError):
    """The outer finally must restore; this is not an ordinary failed gate."""


class PauseAttempt(RuntimeError):
    """An explicit request; acknowledge a pause only after candidate drain."""


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def is_held_manifest(manifest):
    return "held_window" in manifest


def validate_held_policy(manifest):
    policy = manifest.get("held_window")
    require(isinstance(policy, dict) and set(policy) == {"schema_version"} and
            type(policy["schema_version"]) is int and policy["schema_version"] == 1,
            "unsupported held-window policy")
    require(type(manifest.get("window_seconds")) is int and
            manifest["window_seconds"] == 10800, "held lease must be exactly 10800 seconds")
    jobs = manifest.get("jobs")
    require(isinstance(jobs, list) and [j.get("kind") for j in jobs if isinstance(j, dict)]
            in (["p0"], ["p0", "g1"], ["p0", "g1", "g2"]),
            "held window supports only sequential P0/G1/G2")
    require(len(jobs) <= 3 and all(isinstance(j, dict) and
            set(j) == {"kind", "timeout_seconds"} and
            type(j["timeout_seconds"]) is int and 1 <= j["timeout_seconds"] <= 7200
            for j in jobs), "invalid bounded held job template")


def read_owned_bytes(path, *, root=None):
    """Never follow an alias; require owner-controlled ancestry and regular file."""
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path and ".." not in path.parts,
            "immutable input requires canonical absolute path")
    if root is not None:
        require(path.is_relative_to(Path(root)) and path != Path(root),
                "immutable input escaped independent root")
    for parent in (path.parent, *path.parent.parents):
        info = parent.stat()
        require(info.st_uid in (0, os.getuid()) and not info.st_mode & 0o022,
                "immutable input ancestry is not owner-controlled")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid in (0, os.getuid())
                and not info.st_mode & 0o022, "immutable input type/ownership/mode differs")
        return stream.read()


def exclusive_json(path, value):
    """An immutable evidence/control file, never replacement of an old record."""
    path = Path(path)
    raw = canonical_bytes(value)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        # A reader sees either no final name or the complete fsynced file.
        # link is exclusive, unlike replace, and cannot overwrite old evidence.
        os.link(temporary, path, follow_symlinks=False)
    finally:
        temporary.unlink()
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return sha256(raw)


def verify_outer(session, *, require_hold=True):
    if require_hold:
        require(session.owns_hold(), "held window lost its owned hold")
    raw = read_owned_bytes(session.directory / "capsule.json")
    expected = read_owned_bytes(session.directory / "capsule.sha256").decode().strip()
    require(sha256(raw) == expected and json.loads(raw) == session.capsule,
            "immutable outer capsule changed")
    require(session.capsule["recovery_source_sha256"] == session.ops.recovery_sources(),
            "pinned outer controller sources changed")
    validate_held_policy(session.manifest)
    return expected


def check_open(session):
    verify_outer(session)
    verify_history(session)
    if session.requested():
        raise CloseWindow("cluster requested; close held window")
    if time.time() >= session.capsule["deadline_epoch"]:
        raise CloseWindow("10800-second held lease expired")


def verify_history(session):
    """Rehash retained evidence, not just an in-memory predecessor string."""
    records = session.state.get("attempt_results", [])
    hashes = session.state.get("attempt_receipt_sha256", {})
    previous = None
    for record in records:
        attempt_id = record["attempt_id"]
        require(isinstance(attempt_id, str) and re.fullmatch(r"[0-9a-f]{32}", attempt_id),
                "retained attempt identity drift")
        directory = session.directory / "attempts" / attempt_id
        raw = read_owned_bytes(directory / "terminal.json")
        digest = sha256(raw)
        require(digest == hashes.get(attempt_id) and json.loads(raw) == record,
                "immutable prior terminal receipt drift")
        raw_descriptor = read_owned_bytes(directory / "descriptor.json")
        descriptor = json.loads(raw_descriptor)
        require(sha256(raw_descriptor) == record["descriptor_sha256"] and
                descriptor["previous_receipt_sha256"] == previous and
                descriptor["attempt_id"] == attempt_id and
                record["window_id"] == session.capsule["window_id"] and
                record["quiescence"].get("quiescent") is True and
                record["quiescence"].get("attempt_id") == attempt_id and
                type(record["quiescence"].get("node_count")) is int and
                record["quiescence"]["node_count"] == 4,
                "immutable prior attempt identity/quiescence chain drift")
        previous = digest
    require(previous == session.state.get("last_attempt_receipt_sha256") and
            set(hashes) == {row["attempt_id"] for row in records},
            "retained attempt history inventory drift")


def attempt_commands(manifest, window_id, attempt_id, core):
    return [list(core.job_command(manifest, job["kind"], window_id,
                                  attempt_id=attempt_id)) for job in manifest["jobs"]]


def plan_attempt(session, core, *, through=None, g1_tests_path=None, g1_tests_sha256=None):
    """Prepare only a typed descriptor; no test, container or service operation."""
    check_open(session)
    require(session.state.get("phase") in ("held-ready", "paused-quiescent"),
            "plan-attempt requires quiescent held state")
    kinds = [job["kind"] for job in session.manifest["jobs"]]
    through = kinds[-1] if through is None else through
    require(through in kinds, "requested gate is outside the fixed outer template")
    jobs = copy.deepcopy(session.manifest["jobs"][:kinds.index(through) + 1])
    updates = {}
    if g1_tests_path is not None or g1_tests_sha256 is not None:
        require(isinstance(g1_tests_path, str) and isinstance(g1_tests_sha256, str) and
                re.fullmatch(r"[0-9a-f]{64}", g1_tests_sha256),
                "new G1 binding requires an immutable input path/hash")
        raw = read_owned_bytes(g1_tests_path, root=session.manifest["independent_root"])
        require(sha256(raw) == g1_tests_sha256, "G1 binding input changed")
        updates["g1_tests"] = json.loads(raw)
    attempt_id = uuid.uuid4().hex
    descriptor = {"schema_version": 1, "window_id": session.capsule["window_id"],
                  "capsule_sha256": sha256(read_owned_bytes(session.directory / "capsule.json")),
                  "attempt_id": attempt_id,
                  "previous_receipt_sha256": session.state.get("last_attempt_receipt_sha256"),
                  "candidate_updates": updates, "jobs": jobs}
    manifest = copy.deepcopy(session.manifest)
    manifest.pop("held_window")
    manifest["jobs"] = jobs
    manifest["candidate"].update(updates)
    for key in ("cache_path", "output_path"):
        manifest["candidate"][key] = str(Path(session.manifest["candidate"][key]) / attempt_id)
    commands = attempt_commands(manifest, session.capsule["window_id"], attempt_id, core)
    descriptor["command_plan_sha256"] = sha256(canonical_bytes(commands))
    build_attempt(session, descriptor, core)
    path = session.directory / "plans" / (attempt_id + ".json")
    digest = exclusive_json(path, descriptor)
    return {"status": "planned-not-submitted", "attempt_path": str(path),
            "attempt_sha256": digest, "attempt_id": attempt_id,
            "expected_sequence": session.state["held_sequence"], "jobs": jobs,
            "deadline_epoch": session.capsule["deadline_epoch"],
            "command_plan_sha256": descriptor["command_plan_sha256"],
            "no_service_or_test_operations": True}


def build_attempt(session, descriptor, core):
    """Pure descriptor/plan binding plus existing layout validation; no writes."""
    keys = {"schema_version", "window_id", "capsule_sha256", "attempt_id",
            "previous_receipt_sha256", "candidate_updates", "jobs", "command_plan_sha256"}
    require(isinstance(descriptor, dict) and set(descriptor) == keys,
            "attempt descriptor fields differ")
    require(type(descriptor["schema_version"]) is int and descriptor["schema_version"] == 1,
            "unknown attempt schema")
    require(descriptor["window_id"] == session.capsule["window_id"], "wrong attempt window")
    require(descriptor["capsule_sha256"] == sha256(read_owned_bytes(
            session.directory / "capsule.json")), "wrong outer capsule binding")
    attempt_id = descriptor["attempt_id"]
    require(isinstance(attempt_id, str) and re.fullmatch(r"[0-9a-f]{32}", attempt_id),
            "attempt ID must be canonical UUID hex")
    require(uuid.UUID(attempt_id).hex == attempt_id, "invalid attempt UUID")
    require(descriptor["previous_receipt_sha256"] == session.state.get("last_attempt_receipt_sha256"),
            "attempt predecessor receipt differs")
    updates = descriptor["candidate_updates"]
    require(isinstance(updates, dict) and set(updates) <= {"g1_tests"},
            "attempt cannot change native image/runtime/paths or controller authority")
    if "g1_tests" in updates:
        require(isinstance(updates["g1_tests"], dict), "invalid typed G1 test binding")
        # The separately reviewed identity implementation must exist before this
        # override can be admitted; no fallback to an unchecked test mount.
        require(hasattr(core, "g1_tests_path"), "G1 identity adapter is not integrated")
    jobs = descriptor["jobs"]
    require(isinstance(jobs, list) and 1 <= len(jobs) <= len(session.manifest["jobs"]),
            "invalid attempt gate sequence")
    for job, allowed in zip(jobs, session.manifest["jobs"]):
        require(isinstance(job, dict) and set(job) == {"kind", "timeout_seconds"} and
                job["kind"] == allowed["kind"] and type(job["timeout_seconds"]) is int and
                1 <= job["timeout_seconds"] <= allowed["timeout_seconds"],
                "attempt changes gate order or exceeds reviewed job budget")
    manifest = copy.deepcopy(session.manifest)
    manifest.pop("held_window")
    manifest["jobs"] = copy.deepcopy(jobs)
    manifest["candidate"].update(copy.deepcopy(updates))
    for key in ("cache_path", "output_path"):
        manifest["candidate"][key] = str(Path(session.manifest["candidate"][key]) / attempt_id)
    commands = attempt_commands(manifest, session.capsule["window_id"], attempt_id, core)
    require(descriptor["command_plan_sha256"] == sha256(canonical_bytes(commands)),
            "typed command plan digest differs")
    directory = session.directory / "attempts" / attempt_id
    require(not directory.exists(), "attempt evidence already exists")
    core.validate_layout(manifest, session.directory)
    return {"descriptor": copy.deepcopy(descriptor), "manifest": manifest,
            "commands": commands, "directory": directory,
            "descriptor_sha256": sha256(canonical_bytes(descriptor))}


def admit_attempt(session, request, core):
    check_open(session)
    require(session.state["phase"] in ("held-ready", "paused-quiescent"),
            "resume requires proven quiescent held state")
    require(session.state.get("active_process") is None and
            session.state.get("active_attempt") is None, "prior attempt still owns work")
    session.check("stopped")
    raw = read_owned_bytes(request["attempt_path"], root=session.manifest["independent_root"])
    require(sha256(raw) == request["attempt_sha256"], "requested attempt bytes changed")
    descriptor = json.loads(raw)
    attempt = build_attempt(session, descriptor, core)
    # Canonicalize once; the receipt binds both caller bytes and the immutable
    # canonical descriptor used by the core.
    attempt["directory"].mkdir(mode=0o700)
    exclusive_json(attempt["directory"] / "descriptor.json", descriptor)
    exclusive_json(attempt["directory"] / "submission.json", {
        "input_sha256": request["attempt_sha256"],
        "descriptor_sha256": attempt["descriptor_sha256"],
        "control_sequence": session.state["held_sequence"]})
    for key in ("cache_path", "output_path"):
        Path(attempt["manifest"]["candidate"][key]).mkdir(mode=0o700, exist_ok=False)
    session.ops.validate_host(attempt["manifest"])
    check_open(session)
    session.check("stopped")
    exclusive_json(attempt["directory"] / "admitted.json", {
        "window_id": session.capsule["window_id"], "attempt_id": descriptor["attempt_id"],
        "descriptor_sha256": attempt["descriptor_sha256"],
        "command_plan_sha256": descriptor["command_plan_sha256"]})
    return attempt


def _control_dir(session):
    return session.directory / "controls"


def submit_control(session, action, expected_sequence, *, attempt_path=None, attempt_sha256=None):
    """Request only. The sole outer core owns state, candidate work and restore."""
    capsule_sha = verify_outer(session)
    require(action in ("pause", "resume", "close"), "unsupported held control action")
    require(type(expected_sequence) is int and expected_sequence >= 0 and
            expected_sequence == session.state.get("held_sequence"), "stale control sequence")
    require(session.state.get("phase") not in ("restoring", "restored", "recovery-failed"),
            "held controller is closing or terminal")
    request = {"schema_version": 1, "window_id": session.capsule["window_id"],
               "capsule_sha256": capsule_sha, "expected_sequence": expected_sequence,
               "action": action}
    if action == "resume":
        require(session.state.get("phase") in ("held-ready", "paused-quiescent"),
                "resume requires quiescent paused/ready state")
        require(isinstance(attempt_path, str) and isinstance(attempt_sha256, str) and
                re.fullmatch(r"[0-9a-f]{64}", attempt_sha256), "resume requires immutable descriptor hash")
        raw = read_owned_bytes(attempt_path, root=session.manifest["independent_root"])
        require(sha256(raw) == attempt_sha256, "attempt input hash mismatch")
        request.update(attempt_path=attempt_path, attempt_sha256=attempt_sha256)
    else:
        require(attempt_path is None and attempt_sha256 is None, "unexpected attempt on pause/close")
    path = _control_dir(session) / (f"{expected_sequence + 1:08d}.request.json")
    digest = exclusive_json(path, request)
    return {"request_path": str(path), "request_sha256": digest,
            "status": "submitted-not-yet-acknowledged", "action": action}


def take_control(session):
    sequence = session.state["held_sequence"]
    path = _control_dir(session) / (f"{sequence + 1:08d}.request.json")
    try:
        raw = read_owned_bytes(path)
    except FileNotFoundError:
        return None
    request = None
    error = None
    try:
        request = json.loads(raw)
        common = {"schema_version", "window_id", "capsule_sha256", "expected_sequence", "action"}
        require(isinstance(request, dict), "control request must be an object")
        action = request.get("action")
        require(action in ("pause", "resume", "close"), "unsupported control request")
        require(set(request) == common | ({"attempt_path", "attempt_sha256"} if action == "resume" else set()),
                "control fields differ")
        require(type(request["schema_version"]) is int and request["schema_version"] == 1
                and request["window_id"] == session.capsule["window_id"]
                and request["capsule_sha256"] == sha256(read_owned_bytes(session.directory / "capsule.json"))
                and type(request["expected_sequence"]) is int and request["expected_sequence"] == sequence,
                "control authority or sequence differs")
        if action == "resume":
            require(session.state["phase"] in ("held-ready", "paused-quiescent"),
                    "resume is not admitted while an attempt runs")
            require(isinstance(request["attempt_path"], str) and
                    isinstance(request["attempt_sha256"], str) and
                    re.fullmatch(r"[0-9a-f]{64}", request["attempt_sha256"]),
                    "resume descriptor binding is invalid")
    except (ValueError, TypeError, KeyError) as exc:
        error = str(exc)
    # Consume durably before acting; no replay even if the core dies before ack.
    session.update(held_sequence=sequence + 1)
    exclusive_json(_control_dir(session) / (f"{sequence + 1:08d}.receipt.json"), {
        "sequence": sequence + 1, "request_sha256": sha256(raw),
        "status": "rejected" if error else "accepted-request-only",
        "phase_at_acceptance": session.state["phase"], "error": error})
    return None if error else request


def poll_attempt(session):
    check_open(session)
    request = take_control(session)
    if request and request["action"] == "close":
        raise CloseWindow("operator requested close")
    if request and request["action"] == "pause":
        raise PauseAttempt("operator requested pause")


def drain_attempt(session, attempt, core, *, closing=False):
    session.update(phase="attempt-draining")
    session.ops.terminate_owned_process(session.state.get("active_process"))
    attempt_id = attempt["descriptor"]["attempt_id"] if attempt else None
    session.ops.cleanup_held_candidates(session, attempt_id=attempt_id)
    # Recovery phases intentionally tolerate a request or expired lease. They
    # still require original/GLM identity, stopped guards and all-four GPU drain.
    proof = session.check("restore_drained" if closing else "stopped")
    verify_outer(session)
    verify_history(session)
    session.update(active_process=None)
    return {"quiescent": True, "phase": "restore_drained" if closing else "stopped",
            "node_count": 4, "attempt_id": attempt_id}


def run_attempt(session, attempt, core):
    descriptor, manifest = attempt["descriptor"], attempt["manifest"]
    attempt_id = descriptor["attempt_id"]
    session.update(phase="attempt-running", active_attempt={
        "attempt_id": attempt_id, "descriptor_sha256": attempt["descriptor_sha256"],
        "directory": str(attempt["directory"])})
    passed, failure, closing = [], None, None
    try:
        for job, (name, argv) in zip(manifest["jobs"], attempt["commands"]):
            poll_attempt(session)
            require(sha256(read_owned_bytes(attempt["directory"] / "descriptor.json")) ==
                    attempt["descriptor_sha256"], "accepted attempt descriptor changed")
            session.ops.validate_host(manifest)
            session.update(phase="attempt-running", active_gate=job["kind"],
                           candidate_containers=session.state.get("candidate_containers", []) + [name])
            launch = session.ops.prepare_candidate(session, name, argv, held_attempt={
                "attempt_id": attempt_id, "kind": job["kind"],
                "image_id": manifest["candidate"]["image"],
                "plan_sha256": descriptor["command_plan_sha256"]})
            session.ops.execute(session, launch, cwd=str(attempt["directory"]),
                                timeout=job["timeout_seconds"], poll=lambda: poll_attempt(session))
            result = session.ops.validate_result(manifest, job["kind"])
            if result is not None:
                exclusive_json(attempt["directory"] / (job["kind"] + "-result.json"), result)
            passed.append(job["kind"])
            # A completed client is not daemon quiescence, even between gates.
            session.ops.cleanup_held_candidates(session, attempt_id=attempt_id)
            session.check("stopped")
    except CloseWindow as exc:
        closing, failure = exc, str(exc)
    except Exception as exc:
        failure = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, getattr(core, "SessionInterrupted", ())):
            closing = CloseWindow(failure)
    # Do not catch drain errors: UNKNOWN must reach outer bounded recovery, never
    # become paused-quiescent. SIGINT/SystemExit also bypass a normal pause.
    closing = closing or (CloseWindow("held lease/request closed during attempt")
                           if session.requested() or time.time() >= session.capsule["deadline_epoch"] else None)
    proof = drain_attempt(session, attempt, core, closing=closing is not None)
    receipt = {"window_id": session.capsule["window_id"], "attempt_id": attempt_id,
               "descriptor_sha256": attempt["descriptor_sha256"],
               "command_plan_sha256": descriptor["command_plan_sha256"],
               "passed_gates": passed, "outcome": "failed" if failure else "passed",
               "failure": failure, "quiescence": proof}
    digest = exclusive_json(attempt["directory"] / "terminal.json", receipt)
    hashes = dict(session.state.get("attempt_receipt_sha256", {}))
    hashes[attempt_id] = digest
    session.update(active_attempt=None, active_gate=None, last_attempt_receipt_sha256=digest,
                   attempt_results=session.state.get("attempt_results", []) + [receipt],
                   attempt_receipt_sha256=hashes,
                   phase="paused-quiescent" if failure else "held-ready")
    if closing:
        raise closing
    return receipt


def held_loop(session, core):
    validate_held_policy(session.manifest)
    for name in ("attempts", "controls", "plans"):
        (session.directory / name).mkdir(mode=0o700, exist_ok=False)
    session.update(phase="held-ready", held_sequence=0, active_attempt=None,
                   held_candidates=[], last_attempt_receipt_sha256=None,
                   attempt_results=[], attempt_receipt_sha256={})
    while True:
        session.beat()
        try:
            check_open(session)
            request = take_control(session)
            if request is None:
                time.sleep(0.2)
                continue
            action = request["action"]
            if action == "close":
                raise CloseWindow("operator requested close")
            if action == "pause":
                drain_attempt(session, None, core)
                session.update(phase="paused-quiescent")
                continue
            try:
                attempt = admit_attempt(session, request, core)
            except CloseWindow:
                raise
            except Exception as exc:
                if isinstance(exc, getattr(core, "SessionInterrupted", ())):
                    raise CloseWindow(str(exc)) from exc
                # Admission has not created GPU/container work. Recheck the
                # boundary before retaining a paused hold on a rejected input.
                drain_attempt(session, None, core)
                session.update(phase="paused-quiescent", rejected_attempt=str(exc))
                exclusive_json(_control_dir(session) /
                               (f"{session.state['held_sequence']:08d}.admission-rejected.json"),
                               {"error": str(exc), "attempt_path": request["attempt_path"],
                                "attempt_sha256": request["attempt_sha256"]})
                continue
            run_attempt(session, attempt, core)
        except CloseWindow as exc:
            session.update(held_close_reason=str(exc), outcome="closed")
            return


def read_status(session):
    verify_outer(session, require_hold=False)
    verify_history(session)
    return {"window_id": session.capsule["window_id"], "phase": session.state["phase"],
            "held_sequence": session.state.get("held_sequence"),
            "deadline_epoch": session.capsule["deadline_epoch"],
            "active_attempt": session.state.get("active_attempt"),
            "last_attempt_receipt_sha256": session.state.get("last_attempt_receipt_sha256"),
            "hold_status": session.hold_status(), "restored": session.state.get("restored", False),
            "hold_released": session.state.get("hold_released", False)}
