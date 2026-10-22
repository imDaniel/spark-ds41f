# Schema 6: first selected r6x188 / native197-OFF case

This adapter reuses the current HTTP controller, `bin/spark` normal renderer,
physical rank mapping, remote boundary, exact-container guards, fenced operation
epochs, log retention, watchdog, and positive four-rank drain. It is not another
controller or benchmark framework. Schemas 1–4 retain their original meaning.

## Closed contract

The HTTP binding has exactly `schema_version: 6`, `baseline_inputs`,
`baseline_identity`, `toolbox`, `api_port`, `startup_seconds`, `client_seconds`,
and `legacy_case`. `baseline_inputs` contains schema version 1, pinned `profile`,
`nodes`, `effective_config`, and bounded `master_port` / `handoff_seconds`.

A job selects exactly one immutable `arm` (`r6x188` or `native197-off`) and one
`steering` state (`unloaded`, `loaded0` through `loaded4`). That capsule runs one
selected `warm4k_c1` or `warm4k_c4` case. A passing case completes its selection;
no hidden scheduler advances to another arm or dose. A failed attempt does not
advance. This is a selected-case pilot, **not** a full twelve- or eighteen-state
matrix or the old same-boot `0 → 1 → 4 → 0` sequence.

The candidate contains only image, component head, runtime-manifest path/hash,
baked Python path, and separate cache/output roots. It has no borrowed native197
test checkout, fixture, owner matrix, prerequisite receipts, or historical build
reproduction requirement. The independent baseline identity module validates
actual fixed-image inspect, captured static provenance, and actual running
workers separately for each arm. No arbitrary image, argv, environment, mount,
worker extension or concurrency is accepted.

The source profile is pinned to `e935b492…`. Its renderer delta is limited to the
selected exact image and its actual raw labels, isolated owned resources,
loopback HTTP / selected rendezvous ports, loaded versus unloaded steering, and
the read-only baseline observer. Model assets, TP4 ring, 1M capacity, maxseq16,
batch8192, KV10.5GiB per rank, precision, graph and DSpark remain unchanged. The
expected resolved configuration is the independently frozen `a144ea7b…` input;
it is never learned from a first worker. All loaded doses have identical startup
configuration because dose is carried by the original per-request scalar.

## Input and ownership checks

The original PR8 toolbox remains an exact clean standalone checkout. Controller
Git metadata is independently validated and its configuration bytes are bound
into the all-four input closure. Every rank verifies the same deployment,
actual image configuration and RootFS, static provenance, profile and node map.
Existing remote boundary rules continue to distinguish registered read-only
model data from new owner-controlled code and evidence directories.

Corpus production provenance pins (weights manifest, runtime metadata and exact
shared native tokenizer sources) are retained separately from each arm's live
runtime proof. Every rank hashes the registered mounted config/tokenizer assets
using the existing generic read-only asset probe. The corpus model root must be
the actual profile's read-only `/models` mount. Original assets are never chmoded
or modified to satisfy a new guard.

The worker bootstrap verifies its own source and the identity module before
normal vLLM startup. There is no vLLM source overlay and no owner-test fixture
mount. Both arms explicitly set Q03 OFF. All four actual worker/runner resolved
configurations and steering profile identity must match the frozen expectation.

Client ≤600 seconds, startup ≤3600 seconds, job ≤7200 seconds, and outer lease
10800 seconds stay unchanged. Image preparation is explicitly unsupported by
this new path. Thermal/reset, original priming and measurement, first-failure
behavior and result validation belong to the finite legacy case adapter and the
existing supervised HTTP client. A finished client is never positive service
quiescence; the original all-four drain remains mandatory.

## CPU checks and limits

Run `python -B -m unittest discover -s tests -p test_q03_baseline_service.py`.
These focused tests exercise the actual normal renderer for both finite arms
and six states, immutable selection, capacity/read-only guards, independent
input closure, source-pin faults, and reuse of existing generic preparation.
External identities and files are synthetic fixtures in these tests.

CPU tests do not establish actual image availability, site admission, thermal
comparability, model/capability quality, capacity, performance or the complete
comparison matrix. No Mac, Spark, SSH, service launch, publication or field
receipt is produced by these tests. New field execution still requires complete
new provenance/corpus artifacts, an independently reviewed source freeze, and a
new immutable capsule. Old references and capsules are not advanced in place.
