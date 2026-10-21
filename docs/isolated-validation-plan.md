# Offline isolated-window evidence validator

`scripts/isolated_validation_plan.py` is deliberately not a controller. It reads
one local JSON evidence file and prints deficiencies, or
`ready-for-runtime-preflight`. It performs no SSH, Docker, subprocess, network,
Git fetch, filesystem resolution, GPU probe, hold operation, or service action.
Exit 0 means only internally consistent supplied evidence. Exit 2 means missing
or conflicting evidence. Neither is execution authorization or live idle proof.

Run with the approved Python environment:

```
.venv/bin/python scripts/isolated_validation_plan.py /independent/path/plan.json
.venv/bin/python -m unittest discover -s tests -p test_isolated_validation_plan.py
```

## Descriptor fields

The synthetic fixture in `tests/test_isolated_validation_plan.py` is an executable
schema example, NOT a site map or a deployable example. Never substitute its
node IDs or paths for inventory. Top-level fields:

- `schema_version`: 1; `inventory_id`; `responsible_operator`.
- `candidate`: exact imDaniel/spark-ds41f HTTPS fork URL (optional .git suffix), full branch `ref`, exact 40-digit
  `commit`/`tree`, `runner_head`, `fetched_tip`, `clean`, `fetch_succeeded`,
  `approval_evidence_id`, `fetch_evidence_id`, `fetched_at`. This bounded first
  version requires the approved commit to equal the freshly fetched tip and
  runner HEAD. It does not fetch or independently establish publication.
- `services`: exactly two distinct original services. Each records
  `source_commit`, `source_tree`, observed live `config`, `node_map`,
  `model_identity`, `steering_identity`, `capacity`, `guards`,
  `restore_entrypoint_id`, `restore_evidence_id`, `readiness_evidence_id`,
  `restore_preserves_original_files: true`, `inventory_complete: true`, and
  the complete `nodes` inventory. Restore entrypoint is a registered identifier,
  never shell text. Executable command/script/argv fields are rejected.
- `restore_authority` inside each service: registered `policy` and restore
  `config`, `controller_identity`, `registration_evidence_id`, and
  `expected_transition` (`none` or `registered-pending-policy`). A pending
  transition requires `expected_change` and `exclude_from_candidate_benefit:
  true`. Observed live config is NOT required to equal the registered restore
  config: a pinned preexisting policy transition must not be silently reverted.
- Each original node: `node_id`, 64-digit `container_id`/`image_id`,
  `container_name`, `image_tag`, `original_state` (`running`/`stopped`),
  `container_preserved: true`, reserved `ports`, and `protected_paths` covering
  source/config/node_map/model/steering/cache/policy/controller roles. Live and
  restore config/map/policy paths must fall within these protected paths.
  An attested absent container instead uses `original_state: absent`, no
  `container_id`, no `container_preserved: true`, and `restore_state: absent`.
  Its `absence_evidence` records `exists: false`, `running: false`, nonempty
  `attested_by`, `observed_at`, and `evidence_id`. Never invent a container ID.
  Registered image identity, reserved name/tag/ports, and every applicable
  persistent-path protection remain mandatory. This preflight descriptor does
  not claim post-run evidence: the live window must verify the same absent
  state both before mutation and after restoration, and must never start it.
- `candidate_nodes`: each assigned inventoried node once, frozen `commit`/`tree`,
  isolated `container_name`, `image_tag`, `ports`, and nonempty
  `checkout_paths`, `cache_paths`, `output_paths`. These writable groups
  may be nested within the independent candidate checkout (for example .work),
  but must not overlap any original protected path on that host. Production
  names/tags/ports may not be reused even if a service is stopped.

Each content record (`config`, `node_map`, `policy`) has `path`, `sha256`, and
`content_base64`; decoded nonempty bytes must match the hash. Keep site evidence
outside Git. Do not put credentials or secrets into the descriptor or output.
Each path record has `path`, `resolved_path`, `attested_by`, `observed_at`, and
an original protection record also has `role`. Paths are canonical absolute
non-root POSIX paths. Both lexical and supplied resolved paths participate in
parent/child collision checks.

## Trust and runtime boundary

This script cannot prove that an operator's statements are true, current, or
complete. It does not authenticate an approval/evidence ID, infer unknown second
services, parse a real node map into operations, or verify artifact availability.
No local cloud `resolve()` call can attest the remote executor's mount/symlink
namespace. The actual executor must resolve and reverify every path immediately
before writes (including nonexistent descendants and parent symlinks), protect
against resolution races, and verify complete mount/write boundaries. Merely
setting `resolved_path` equal to `path` is not proof of non-aliasing.

Before a runtime window, independently revalidate successful fresh publication,
approved frozen runner and node identity, both live snapshots and registered
restore policies, immutable configs/maps/artifacts/containers, live hold owner,
valid idle readings, locks, heartbeat/watchdog, original resource thresholds,
coordinated stop scope, and readiness/failure-restore evidence. Original service
files, source, tags and caches remain immutable. Existing lab guards are neither
modified nor bypassed. Failure restoration must follow registered authority;
a previously pending policy effect is logged separately, not a Q03 benefit.

Unknown service/controller/node-map evidence remains a real blocker. This tool
only makes deficiencies actionable; it cannot manufacture the missing inventory
or declare a safe execution window. No execution controller is included.

## Optional original-service capabilities

An original service without a Git checkout may declare `source_kind: artifact`,
`source_artifact_sha256`, and `git_not_applicable` containing nonempty `reason`
and `evidence_id`, instead of source commit/tree. Its protected source role is
`artifact`. A service without steering may omit steering identity and its
protected steering role only when `steering_not_applicable` contains nonempty
`reason` and `evidence_id`. Absence alone never means not applicable. All actual
original persistent files remain protected; these declarations must be grounded
in complete inventory, not used to fabricate an unknown second service. Other
capabilities outside this bounded schema require review once inventory arrives;
do not manufacture config/map/model/cache/policy fields to pass validation.
