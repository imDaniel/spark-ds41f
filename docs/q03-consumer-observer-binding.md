# Separate Q03 consumer-observer binding

This is an optional tests-only controller lane, not a replacement for the
owner/HTTP capsule and not a native consumer-use result. It reuses the owner
admission, bounded attempt, four-rank barriers, ready/steady acknowledgement,
journalled cleanup and collector-retention workflow. It does not change the
native image, model, capacity, requests, steering, or serving workflow.

## Immutable inputs

- Controller starting point: `3a49389f7` (existing owner/HTTP behavior).
- Tests-only composition: `8dd783aad9d52252816102511f36f4e6ee1f912a`.
- Composition tree: `461e76abd09203824588e8d809d42bf5a050b1a9`.
- Mounted tests subtree: `6d6088b3d791c37a49c85094360c251208d16944`.
- Original owner source: `141f8647e885812095060ff9e7f8b991316ae538`.
- Independent source/CPU review receipt SHA-256:
  `508707240bde121b25c5a4f06e41a3e49bf370895e4f6cd6ca1bcecb95b1d964`.

The existing component197 runtime/image/manifest pins remain unchanged. This
composition uses the normal-qualification PR21 base
`4d3f478e7ef8221cb45a3c19fd81bf17aaa14c02`, including its fixed helper-0600
and native CPU exporter. The eight non-document consumer files are unchanged
from the independently reviewed `36ce358bd2baa79e3665b142f3ce516022796624`;
this composition note is the only intentional documentation deviation. That
original candidate and its review remain preserved separately. No existing
capsule's source inventory or frozen references may be updated in place to use
this lane. This composition requires its own reviewed controller and NEW capsule
identity; it does not establish native consumer-use proof.

## Selection and boundaries

Only a separately prepared, reviewed NEW owner held-window manifest can select
`owner_model.test_lane = "consumer_observer"`. The job remains `owner_model`,
with `selection.mode = "dual"` and one of the existing steering states. Omit
`test_lane` for the unchanged owner lane. Unknown values and `off` in the new
lane are rejected. There is no attempt-time switch between old and new lanes.

Its `owner_model.tests` binding must use the exact composition head/tree above,
with `fixture_sha256` equal to
`c0ef7a2ff7eb57e7b6a1b5c19212ce3c49f27aed78f8a818202d5c35592661a9`.
The existing canonical path, clean standalone Git checkout, private Git config,
all-four input admission and prior P0/G1/G2/drain requirements still apply.
The checkout needs the complete unchanged tests subtree, not a sparse export.
No runtime overlay is mounted, and no runtime patch is added by this binding.

The closed plan selects only:

- `q03_use_owner_worker.Q03UseOwnerWorkerExtension`;
- `tests/compile/fullgraph/q03_use_owner_fixture.py::test_real_model_owner_and_observed_use`;
- the four integration source pins, test package initializer pins, original
  owner identity, complete tests subtree, runtime support and package sources;
- the existing three request batches repeated twice and existing sampling,
  effective-config, runtime/native identity and retention checks.

The original owner fixture receives its original digest. Ready/steady exchanges
receive the new complete spec digest. Independent per-attempt directories and
`q03-consumer-…` container names prevent silent reuse of an existing owner run.
The immutable controller source inventory includes both new binding modules.

## Interpreter closure

Only this lane mounts `scripts/q03_consumer_site.py` read-only as
`/binding/sitecustomize.py`. It explicitly prefixes `/binding` to the pinned
baked PYTHONPATH, without mounting a second vLLM tree. The old owner bootstrap
and old owner/HTTP environment defaults are unchanged.

The first `-I -B -S` interpreter checks the guard bytes, its resolved import
origin, and the absent isolated bytecode prefix before a normal site import can
read any stale cached guard. It then execs a `-B -P` interpreter. The lane sets
`PYTHONSAFEPATH=1`, `PYTHONDONTWRITEBYTECODE=1` and
`PYTHONPYCACHEPREFIX=/cache/consumer-bytecode`; this prefix must remain absent.
Native safe serialization remains required and the existing native
`VLLM_WORKER_MULTIPROC_METHOD=spawn` selector is fixed to fresh workers.

Each new interpreter, including actual Python spawn children, checks effective
bytecode state, the complete spec digest, source subtree/modes, package origins,
runtime manifest, pinned runtime sources, and native library bytes before
worker imports. Test sources and pinned runtime Python sources are compiled
directly from checked bytes, never loaded from pyc. Pinned module names must
resolve to their exact files, not a same-root shadow package; native imports
must resolve to the exact manifest paths. Guard errors terminate the interpreter
instead of Python's usual log-and-ignore sitecustomize behavior.

This closes the controller's source/import contract in CPU subprocess fixtures.
It does not establish actual native bootstrap ordering, TP4 IPC, registered
producer interception, CUDA capture/replay, or device completion. Those remain
separate authorized native validation requirements.

## Results and evidence limits

The unchanged owner safety checks and selected pytest case must succeed before
result intake. Intake additionally requires all six request-local four-rank
observations, exact worker/owner identities, fresh initial observers, successful
hook restoration, and the original four-rank collector-retention evidence.
Counts must agree with request-local records. Missing categories remain JSON
null and an event-free request remains UNKNOWN, never zero or a synthetic pass.

A returned controller `result: passed` is the bounded owner/retention/observation
workflow result only. The new lane explicitly reports
`qualification: owner-retention-and-observation-only`,
`consumer_use: UNQUALIFIED`, and `missing_events: UNKNOWN` even when records
contain observed use. No automatic numerical, full-model, FULL, retirement,
quality, capacity, performance, promotion, merge, or native admission follows.

## CPU validation

`tests/test_q03_consumer_observer.py` exercises real controller admission/plans
and real Python exec/spawn with synthetic source/native/model protocol doubles.
It covers both launcher roles, child-only drift, out-of-root and same-root
shadowing, native-origin shadowing, source/spec/bytecode identity, valid-header
stale pyc, independent initial-container intake, and UNKNOWN receipt semantics.
Its temporary mount-path relocation and synthetic wheel/runtime bytes are
explicit fixture boundaries, not native execution evidence.

Run together with the existing owner, HTTP, held-window, rank-log and
qualification CPU suites. Native GPU/model tests are deliberately not invoked
by this controller implementation task. Publication and deployment require the
separate source review and authorization boundaries; no deployment was performed.
