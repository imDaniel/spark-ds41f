#!/usr/bin/env python3
"""Validate supplied offline window evidence. Never probe or execute operations."""

import argparse
import base64
import hashlib
import json
import re
from pathlib import PurePosixPath


def validate(plan):
    """Return deficiencies; success means readiness for live preflight only."""
    errors = []

    def require(ok, where, message):
        if not ok:
            errors.append(f"{where}: {message}")
        return bool(ok)

    def text(obj, key, where):
        value = obj.get(key)
        require(isinstance(value, str) and bool(value.strip()),
                f"{where}.{key}", "required nonempty string")
        return value if isinstance(value, str) else ""

    def digest(value, where, length=64):
        require(isinstance(value, str) and
                re.fullmatch(r"[0-9a-f]{%d}" % length, value) is not None,
                where, f"required lowercase {length}-digit digest")

    def path(value, where):
        valid = isinstance(value, str) and value.startswith("/")
        valid = valid and value != "/" and "\x00" not in value
        valid = valid and str(PurePosixPath(value)) == value
        valid = valid and ".." not in PurePosixPath(value).parts
        require(valid, where, "required canonical absolute non-root POSIX path")
        return value if valid else None

    def paths(items, where):
        result = []
        if not require(isinstance(items, list) and bool(items), where,
                       "required nonempty path evidence list"):
            return result
        for i, item in enumerate(items):
            label = f"{where}[{i}]"
            if not require(isinstance(item, dict), label, "required object"):
                continue
            lexical = path(item.get("path"), label + ".path")
            resolved = path(item.get("resolved_path"), label + ".resolved_path")
            text(item, "attested_by", label)
            text(item, "observed_at", label)
            if lexical and resolved:
                result.extend((lexical, resolved))
        return result

    def overlaps(a, b):
        return a == b or a.startswith(b + "/") or b.startswith(a + "/")

    def blob(obj, where):
        if not require(isinstance(obj, dict), where, "required content evidence"):
            return
        path(obj.get("path"), where + ".path")
        digest(obj.get("sha256"), where + ".sha256")
        try:
            data = base64.b64decode(obj.get("content_base64", ""), validate=True)
            require(bool(data) and hashlib.sha256(data).hexdigest() == obj.get("sha256"),
                    where, "content bytes must be nonempty and match SHA256")
        except (ValueError, TypeError):
            errors.append(where + ": invalid content_base64")

    if not isinstance(plan, dict):
        return ["plan: required JSON object"]
    def reject_commands(value, where="plan"):
        if isinstance(value, dict):
            for key, item in value.items():
                require(key not in ("command", "commands", "shell", "argv", "script"),
                        where + "." + key, "executable command fields are not accepted")
                reject_commands(item, where + "." + key)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                reject_commands(item, f"{where}[{i}]")

    reject_commands(plan)
    require(type(plan.get("schema_version")) is int and plan["schema_version"] == 1, "schema_version", "must equal 1")
    text(plan, "inventory_id", "plan")
    text(plan, "responsible_operator", "plan")
    publication = plan.get("candidate", {})
    if not isinstance(publication, dict):
        return errors + ["candidate: required object"]
    url = text(publication, "fork_url", "candidate")
    require(url in ("https://github.com/imDaniel/spark-ds41f",
                    "https://github.com/imDaniel/spark-ds41f.git"),
            "candidate.fork_url", "must identify the selected imDaniel/spark-ds41f fork")
    ref = text(publication, "ref", "candidate")
    suffix = ref.removeprefix("refs/heads/")
    valid_ref = ref.startswith("refs/heads/") and bool(suffix)
    valid_ref = valid_ref and all(
        re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", part)
        and not part.endswith((".", ".lock")) and ".." not in part
        for part in suffix.split("/")
    )
    require(valid_ref, "candidate.ref", "required full branch ref")
    for key in ("commit", "tree", "runner_head", "fetched_tip"):
        digest(publication.get(key), "candidate." + key, 40)
    require(publication.get("commit") == publication.get("runner_head") ==
            publication.get("fetched_tip"), "candidate", "runner and freshly fetched tip must equal frozen commit")
    require(publication.get("fetch_succeeded") is True, "candidate.fetch_succeeded", "must be true")
    require(publication.get("clean") is True, "candidate.clean", "must be true")
    text(publication, "approval_evidence_id", "candidate")
    text(publication, "fetch_evidence_id", "candidate")
    text(publication, "fetched_at", "candidate")

    services = plan.get("services")
    if not require(isinstance(services, list) and len(services) == 2,
                   "services", "exactly TWO original service descriptors required"):
        return errors
    protected, names, ports, tags, service_ids = {}, {}, {}, {}, set()
    all_nodes = set()
    container_ids = set()
    for i, service in enumerate(services):
        label = f"services[{i}]"
        if not require(isinstance(service, dict), label, "required object"):
            continue
        sid = text(service, "service_id", label)
        require(sid not in service_ids, label, "service identity must be distinct")
        service_ids.add(sid)
        def not_applicable(key):
            evidence = service.get(key)
            if not require(isinstance(evidence, dict), label + "." + key,
                           "required explicit not-applicable inventory evidence"):
                return
            text(evidence, "reason", label + "." + key)
            text(evidence, "evidence_id", label + "." + key)

        source_kind = service.get("source_kind", "git")
        require(source_kind in ("git", "artifact"), label + ".source_kind",
                "must identify git or immutable artifact source")
        if source_kind == "artifact":
            digest(service.get("source_artifact_sha256"), label + ".source_artifact_sha256")
            not_applicable("git_not_applicable")
        else:
            for key in ("source_commit", "source_tree"):
                digest(service.get(key), label + "." + key, 40)
        no_steering = "steering_not_applicable" in service
        if no_steering:
            not_applicable("steering_not_applicable")
            require("steering_identity" not in service, label,
                    "steering identity conflicts with not-applicable declaration")
        else:
            text(service, "steering_identity", label)
        for key in ("config", "node_map"):
            blob(service.get(key), label + "." + key)
        authority = service.get("restore_authority")
        if require(isinstance(authority, dict), label + ".restore_authority",
                   "required registered restore policy, separate from live snapshot"):
            for key in ("policy", "config"):
                blob(authority.get(key), label + ".restore_authority." + key)
            for key in ("controller_identity", "registration_evidence_id"):
                text(authority, key, label + ".restore_authority")
            transition = authority.get("expected_transition")
            require(transition in ("none", "registered-pending-policy"), label,
                    "declare expected live-to-restore policy transition")
            live_config = service.get("config", {})
            restore_config = authority.get("config", {})
            if isinstance(live_config, dict) and isinstance(restore_config, dict):
                changed = live_config.get("sha256") != restore_config.get("sha256")
                require(not changed or transition == "registered-pending-policy",
                        label, "different restore config requires registered pending transition")
            if transition == "registered-pending-policy":
                text(authority, "expected_change", label + ".restore_authority")
                require(authority.get("exclude_from_candidate_benefit") is True,
                        label, "preexisting policy change must not count as candidate benefit")
        entrypoint = service.get("restore_entrypoint_id", "")
        require(isinstance(entrypoint, str) and re.fullmatch(r"[A-Za-z0-9_.:-]+", entrypoint),
                label + ".restore_entrypoint_id", "required registered identifier, never shell text")
        require(service.get("inventory_complete") is True, label,
                "operator must attest complete service node/persistent-path inventory")
        for key in ("model_identity", "restore_entrypoint_id",
                    "restore_evidence_id", "readiness_evidence_id"):
            text(service, key, label)
        for key in ("capacity", "guards"):
            require(isinstance(service.get(key), dict) and bool(service[key]),
                    label + "." + key, "required recorded settings")
        require(service.get("restore_preserves_original_files") is True,
                label, "restore must preserve original persistent files")
        placements = service.get("nodes", [])
        if not require(isinstance(placements, list) and bool(placements), label + ".nodes", "required complete node inventory"):
            continue
        seen = set()
        for j, node in enumerate(placements):
            loc = f"{label}.nodes[{j}]"
            if not require(isinstance(node, dict), loc, "required object"):
                continue
            name = text(node, "node_id", loc)
            require(name not in seen, loc, "duplicate service node")
            seen.add(name)
            all_nodes.add(name)
            state = node.get("original_state")
            require(state in ("running", "stopped", "absent"), loc,
                    "record running/stopped/attested absent original state")
            if state == "absent":
                require(node.get("container_id") is None, loc,
                        "absent container must not have a fabricated container ID")
                require(node.get("container_preserved") is None or
                        node.get("container_preserved") is False, loc,
                        "absent state preserves absence, not a container")
                require(node.get("exists", False) is False and
                        node.get("running", False) is False, loc,
                        "absent node contradicts present or running state")
                require(node.get("restore_state") == "absent", loc,
                        "original absence must remain absent; never start the service")
                evidence = node.get("absence_evidence")
                if require(isinstance(evidence, dict), loc + ".absence_evidence",
                           "required attested original absence"):
                    require(evidence.get("exists") is False and evidence.get("running") is False,
                            loc + ".absence_evidence", "absence must be known, not running or unknown")
                    for key in ("attested_by", "observed_at", "evidence_id"):
                        text(evidence, key, loc + ".absence_evidence")
            else:
                digest(node.get("container_id"), loc + ".container_id")
                container_id = node.get("container_id")
                if isinstance(container_id, str):
                    require((name, container_id) not in container_ids, loc,
                            "two original services cannot identify the same container ID")
                    container_ids.add((name, container_id))
                require(node.get("container_preserved") is True, loc,
                        "original container must be preserved")
            # Registered image and all persistent-path protections apply even
            # when no container currently exists on this node.
            digest(node.get("image_id"), loc + ".image_id")
            container_name = text(node, "container_name", loc)
            require(container_name not in names.setdefault(name, set()), loc,
                    "two original services cannot identify the same container")
            names[name].add(container_name)
            tag = text(node, "image_tag", loc)
            tags.setdefault(name, set()).add(tag)
            p = node.get("ports")
            require(isinstance(p, list) and bool(p) and all(type(x) is int and 1 <= x <= 65535 for x in p), loc + ".ports", "required valid reserved ports")
            ports.setdefault(name, set()).update(x for x in p if type(x) is int) if isinstance(p, list) else None
            protection = paths(node.get("protected_paths"), loc + ".protected_paths")
            protected.setdefault(name, []).extend(protection)
            entries = node.get("protected_paths", [])
            roles = {e.get("role") for e in entries if isinstance(e, dict) and isinstance(e.get("role"), str)} if isinstance(entries, list) else set()
            required_roles = {"config", "node_map", "model", "cache", "policy", "controller"}
            required_roles.add("artifact" if source_kind == "artifact" else "source")
            if not no_steering:
                required_roles.add("steering")
            require(required_roles <= roles, loc + ".protected_paths",
                    "must cover all applicable original persistent-path roles: " +
                    ",".join(sorted(required_roles)))
            evidence_paths = [service.get(k, {}) for k in ("config", "node_map")]
            if isinstance(authority, dict):
                evidence_paths.extend(authority.get(k, {}) for k in ("config", "policy"))
            for item in evidence_paths:
                value = item.get("path") if isinstance(item, dict) else None
                if isinstance(value, str):
                    require(any(value == p or value.startswith(p + "/") for p in protection),
                            loc, "live/restore evidence path not covered by protected paths")
    candidates = plan.get("candidate_nodes", [])
    if not require(isinstance(candidates, list) and bool(candidates), "candidate_nodes", "required isolated node assignments"):
        return errors
    seen = set()
    for i, node in enumerate(candidates):
        loc = f"candidate_nodes[{i}]"
        if not require(isinstance(node, dict), loc, "required object"):
            continue
        name = text(node, "node_id", loc)
        require(name in all_nodes and name not in seen, loc, "node must be inventoried and assigned once")
        seen.add(name)
        require(node.get("commit") == publication.get("commit"), loc, "candidate node commit must equal freeze")
        require(node.get("tree") == publication.get("tree"), loc, "candidate node tree must equal freeze")
        for key, reserved in (("container_name", names), ("image_tag", tags)):
            value = text(node, key, loc)
            require(value not in reserved.get(name, set()), loc + "." + key, "collides with original service")
        p = node.get("ports")
        valid = isinstance(p, list) and bool(p) and all(type(x) is int and 1 <= x <= 65535 for x in p)
        require(valid, loc + ".ports", "required valid candidate ports")
        if valid:
            require(len(set(p)) == len(p) and not set(p) & ports.get(name, set()), loc + ".ports", "duplicate or original-service collision")
        groups = [paths(node.get(key), loc + "." + key) for key in
                  ("checkout_paths", "cache_paths", "output_paths")]
        for group in groups:
            require(not any(overlaps(a, b) for a in group for b in protected.get(name, [])), loc, "candidate write path overlaps protected service path")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", help="local JSON evidence file; no probes")
    args = parser.parse_args()
    try:
        with open(args.manifest, encoding="utf-8") as stream:
            errors = validate(json.load(stream))
    except (OSError, ValueError, TypeError) as exc:
        errors = [f"manifest: {exc}"]
    print(json.dumps({
        "status": "incomplete" if errors else "ready-for-runtime-preflight",
        "errors": errors,
        "execution_authorized": False,
        "live_state_verified": False,
        "boundary": "Supplied evidence only. Executor must reverify publication, all service identities, symlink-resolved paths, live hold/idle, locks, resources and restore readiness. No operations performed.",
    }, indent=2))
    return 2 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
