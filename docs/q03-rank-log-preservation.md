# Q03 candidate rank-log preservation

This follow-up is based on HTTP service commit
`8b463045ceb7d7395b0dce8ea7a9d0de2b1554df`. It changes only candidate retirement
and its evidence contract. Owner execution/readiness, HTTP workload, original
assets, caches and START policy are unchanged. Use a **new** immutable capsule
whose source inventory includes `q03_rank_logs.py`; do not update an active
capsule or reinterpret evidence from an older controller.

## New-capsule deployment handoff

This publication is not a deployment and does not authorize original START or a
native image rebuild. Before admitting a **new** owner or HTTP capsule, stage
the published `scripts/q03_remote_operation.py` identically on **all four**
registered nodes at this new versioned path under the approved independent root:

`<independent_root>/helpers/q03_remote_operation-1ddb0c79d6e50fd71245cff1c53c32fe9bf862e1a57f4078e6fed1ecafd7484a.py`

Its exact SHA-256 is
`1ddb0c79d6e50fd71245cff1c53c32fe9bf862e1a57f4078e6fed1ecafd7484a`.
Use exclusive creation in an owned, non-shared-writable namespace; if that path
already exists, verify identical bytes rather than overwrite it. Never replace
the old `6eca...` helper or change any existing capsule, helper pin or authority
file. Retain old helper paths for their original capsules and recovery.

For the new capsule only, bind `preflight.remote_helper_path` to that resolved
absolute versioned path and `preflight.remote_helper_sha256` to the full hash
above. Require the same hash in `recovery_source_sha256.q03_remote_operation.py`,
plus the exact published controller source inventory including
`q03_rank_logs.py`. Verify path, owner/mode, bytes and helper/authority identity
on all four registered nodes before admission; a missing or different rank is
a preparation blocker, not permission to reuse the old helper. Preserve the
existing keep-stopped original-service disposition and native image pins.

## Ordering and evidence

Owner and HTTP normal, failure and watchdog cleanup call the same fixed routine:

1. Fence pending operations. Inspect and STOP every safely identified candidate
   and stop its owned guard. Continue other ranks' STOP checks if one rank fails.
   Require all four daemon/guard checks before any log read or removal.
2. For each created exact ID, retain a pre-read inspect, run only
   `docker logs --timestamps -- <exact-64-hex-ID>`, and preserve separate raw
   stdout/stderr bytes. Retain actual reader exit/cancel/timeout status and the
   post-read inspect, including the container's own exit state. A container's
   nonzero exit does not imply that reading its logs failed, or vice versa.
3. Fsync the private session files and hash-index every rank's binding, raw
   streams, pre/post inspect, and read receipt. Require the stable stopped
   identity before and after reading. Write the all-four index before removal.
4. Reverify those hashes and exact stopped identity at each destructive boundary;
   remove by ID without `--force`, then positively verify absence. GPU drain is
   still a separate existing caller obligation; successful logging is not GPU
   drain, daemon quiescence or qualification success.

Artifacts are private directories (0700) and files (0600) under
`attempts/<attempt>/<owner-or-http>/rank-logs/<rank>/<capture-id>/`:
`intent.json`, `pre-inspect.json`, `stdout.log`, `stderr.log`, `receipt.json`,
`post-inspect.json`, and `index.json`. Failures retain `failure.json` and any
partial files; an unavailable post-inspect is explicit in
`post-inspect-error.json`. The lane-level `rank-logs-index.json` binds all four
ranks and the final `drained.json` includes its SHA-256. A never-created rank is
explicitly marked `not_created`; a create intent without an ID is UNKNOWN, not
an empty successful log. Remote operation output/receipt files remain retained.

## Bounds, failures and retries

A log reader has a 20-second supervisor timeout, further shortened by the
existing recovery deadline, and kernel `RLIMIT_FSIZE` caps each raw stream at
4 MiB. Reaching either cap is conservatively **truncated**, even if the reader
exits zero. Reads and rereads reject non-regular, aliased, shared-writable,
oversized or changing files; FIFO opens cannot block cleanup.

There is at most one new capture generation per incomplete rank per cleanup
invocation. Failure, truncation, corrupt transport or missing artifacts does
not authorize removal of any rank. All containers remain stopped and their
existing evidence remains immutable. A later recovery invocation reuses verified
complete generations and starts a new generation only for incomplete ranks.
No partial attempt is overwritten or called complete.

Only the exact closed log command is classified as read-only by the existing
remote helper. Its sealed epoch may be safe for later operations after a
positive process-group termination receipt, including nonzero exit, timeout or
cancellation. That fact is separate from `action_succeeded` and log completeness.
Missing or corrupt output cannot turn known group quiescence into a permanently
failed-epoch deadlock. This exception does **not** apply to arbitrary commands,
STOP, remove, guard changes, or any mutation. Missing terminal receipts, lost
supervisors, unproven groups and unknown mutation epochs still block recovery,
retain the hold, and do not permit a replacement reader.

Completed-generation retries verify every artifact again, even after a process
was lost between removal and recording absence. An absent container without a
complete generation cannot be recovered by inventing empty logs. Loss of logs
or persistent cap failures require investigation while retaining the container;
this implementation does not silently waive evidence requirements or enlarge
limits.

## CPU verification

Tests cover both actual lane state machines plus independent shared retirement
and remote-epoch fault probes. Real temporary filesystem faults include partial
writes, process death before index publication, duplicate cleanup, corruption,
FIFO/symlink replacement, failed reads and all-four identity bindings. Additional
local-child tests use a temporary executable stub named `docker` to verify the
actual kernel output cap and timeout/group kill. They never contact Docker,
GPUs, SSH, a model service or the original assets. No field qualification or
performance claim follows from these tests.
