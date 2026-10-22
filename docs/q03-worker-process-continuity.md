# Read-only baseline worker process epochs

This is a narrow prerequisite for a future same-boot baseline capsule, not a
new controller or an execution plan. It branches from PR32 source
`98b05d2a75fd8d04085f416b145ad593c811e07d`. The existing P1 freeze, immutable
capsules, historical references and schemas 1–4 are not advanced in place.

## Observation and validation

Each actual baseline worker's existing `q03_http_observe` RPC adds:

- `worker_pid`: `os.getpid()`, a positive exact integer below `2**31` in that
  worker's PID namespace. It is not the API server PID or container init PID.
- `worker_starttime_ticks`: Linux `/proc/self/stat` field 22, a positive exact
  integer below `2**64`. It is a kernel start-time tick count, not wall time.

The reader is source-only and standard-library-only. It reads the fixed self
path with a 64 KiB bound, checks the reported PID, and separates comm at its
final closing parenthesis so spaces, embedded parentheses and newlines do not
move field 22. It requires the complete supported Linux record through field
52 and a terminal newline; a read cut inside field 22 cannot become shorter
accepted start ticks. Non-Linux, unavailable, oversized, malformed or dead-process
observations fail closed. Readiness rejects absent values, strings (including
`unknown`), floats, booleans, zero, negatives and out-of-width integers.

The two values belong to the same report as its rank, exact arm/source,
runtime, attempt and spec identity. Existing owned lifecycle evidence binds
the report to its rank's exact container/config and physical host mapping.
The pair is **not** a global key: different rank containers may legitimately
report the same PID and ticks. Never substitute it for the existing bindings.

The existing HTTP lifecycle already requests fresh reports before and after
the client, validates all four, then compares complete reports before collecting
the result. The new fields make a changed PID, or a reused PID with a changed
start time, fail that comparison. No controller sequencing, RPC method,
request scheduling, engine, kernel, model, capacity or cleanup behavior changes.
The source-only identity/bridge pins are updated; worker source continues to
be bound by the existing plan's hash and read-only mount.

## Evidence limits

This is sampled process-epoch evidence within the same host boot and PID
namespace, not continuous event tracing or an independent host boot-ID proof.
PID and kernel start ticks are not cryptographic identities. They do not rule
out an exec in the same process, deliberate forged observations, or theoretical
PID reuse with a same-tick start-time collision. Exact container/host/source
and owned-attempt evidence remain necessary.

An unchanged epoch does **not** prove unchanged in-memory weights, no in-process
model reload, unchanged CUDA graph state, completed requests, client/service
quiescence, or an applied GPU steering scale. Request body/profile identity
still does not establish applied-scale telemetry. No invasive profiler,
weight hashing, new signaling/kill capability or cleanup authority is added.

A future same-boot implementation must use a newly reviewed source freeze and
immutable capsule, retain raw validated observations at its required sequence
boundaries, and compare within the exact owned bindings. This addition alone
does not wire that sequence, manufacture past observations, qualify a same-boot
run, or change the current single-case P1 gate.

## Focused checks

`tests/test_q03_baseline_process.py` covers the bounded parser and actual Linux
self-process reader, strict four-rank receipt validation, source-pin closure,
and the existing HTTP loop with synthetic external observations. PID changes
and same-PID/new-starttime changes on every rank are rejected before result
collection. These are CPU checks, not actual model/worker restart experiments.

Validation commands and limits are retained in
`experiments/2026-10-22-worker-process-continuity/README.md`.
