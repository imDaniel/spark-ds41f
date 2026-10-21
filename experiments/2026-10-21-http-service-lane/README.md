# Bounded HTTP service/client lane

This is a separate typed `http_service` job for a **new** held capsule. It does
not reinterpret an existing owner attempt, modify an active capsule, replace
native image source, or provide an arbitrary command runner. Original service
policy is always `keep-stopped`; neither normal completion nor recovery starts
originals. Site inputs, inventories and receipts remain private and outside Git.

Base controller: public native-RPC repair `e650318ef7fa3a43ab03338967b20efa3a61c5d0`
(tree `1c9b3dfb71e133f1a8e08a8296d8a42af2aa3e2e`), descended from
`7403bc6014e959af6e4087b48e05f43e3ccb59e6`.
Native runtime/source/image remain the owner validator's exact native197,
runtime24315 and image90bd identities. The observation fixture is the native-safe
named-RPC test component `141f8647e885812095060ff9e7f8b991316ae538`, tree
`0990e860d8c3c621f2889176cd38ff1600600646`, fixture SHA256
`6b33c8026bfc2b5e18274235473cf1c5c6620981ddf2f433817c40408c05f5ad`.
The distinct client toolbox is pinned to
`c6342a1b6c5e5a98108905a71452a51d6d3a4a87`, tree
`dbbd5d0e8976861f15afa2c1bd9c6daae1f2f660`; its imported Python closure is in
`scripts/q03_http_toolbox.json`. No toolbox branch is advanced implicitly.

## Scope and normal serving compatibility

The normal profile is the exact hash-bound owner profile. `http_plan` invokes
`bin/spark.expected_environment`, `expected_command` and `rendered_docker_command`
on it. It preserves the 1,048,576-token context, 16 sequences, batch 8192,
11,274,289,152-byte KV pool (10.5 GiB), four-node ring and normal parsers. The
only declared deltas are the native OFF/DUAL environment selection, chosen
existing steering state, independent container/cache/result names, explicit
candidate ports, localhost HTTP binding, and a frozen read-only observation
worker extension. The image's installed source and baked PYTHONPATH are retained.
The worker bootstrap supplies only the mounted extension's import directory via
normal multiprocessing's inherited `sys.path`.

The detached, name-based `bin/spark cluster start` wrapper is intentionally not
called: it would launch before an exact container ID was durable and performs
name-based partial rollback. Its normal renderer is transformed to inert create;
all four IDs/config hashes are committed before startup guards, then all four
exact-ID guards precede any start. Existing `memguard-start.sh`, `memguard.sh`,
`q03_remote_operation` and exact daemon inspection remain the mutation machinery.
No SSH, Docker, GPU or model operation occurs during import/planning or CPU tests.

## Typed inputs and execution

Use the existing `q03_window.py run-session`, `plan-attempt`, and
`control resume/pause/close` commands. The usual publication/preflight/hold,
independent watchdog and heartbeat requirements are unchanged. This lane needs
no new top-level launcher and accepts no caller-supplied argv, URL or engine
arguments.

A private manifest supplies exactly one job:

    {"kind":"http_service","timeout_seconds":7200,
     "selection":{"mode":"off","steering":"off"}}

`window_seconds` is 10800 and `held_window` is `{"schema_version":1}`. The private
`http_service` binding has these exact keys:

- `schema_version`: 1.
- `owner_inputs`: the existing exact owner binding (tests, profile, nodes,
  independently reviewed effective configs, prior P0/G1/G2 prerequisites,
  rendezvous port and handoff budget).
- `owner_matrix`: `capsule` path/hash binding and six `terminals`, each containing
  `terminal` and `descriptor` path/hash bindings. All six mode/steering cells must
  already have passed with identity-bound, positive four-node drain evidence.
  Its fixture tree/hash must match the fixed current owner fixture. The prior
  profile, effective-config and physical node-map hashes must match
  `owner_inputs`; private paths may relocate without changing these identities.
- `toolbox`: a separate clean exact c6342 checkout with `path`, `head`, `tree`,
  and `git_config_sha256`. Only the physical head needs that checkout.
- `api_port`: an explicit unprivileged candidate port, different from rendezvous.
- `startup_seconds`: 1..3600; `client_seconds`: 1..600. Both are subordinate to the
  immutable job and outer deadlines, never fresh authority to extend a lease.

`plan-attempt` derives the next successful mode: OFF, OFF, then DUAL. Steering
remains the outer selection. A failed arm does not advance the sequence. After
three passed arms a new plan is refused; broader measurements require a separate
reviewed job, not editing this frozen smoke contract. Each arm runs one fixed
servebench burst: concurrency 1, batch 1, 16 output tokens, seed0, normal default
prompts, request timeline retained. The client uses a closed source-only loader
for the pinned toolbox, rejecting preloaded or undeclared toolbox modules. This
is HTTP lifecycle smoke only, not a performance, numerical acceptance or
owner-consumption result. Loaded-zero requests explicitly carry the existing
profile name and numeric `0`; active requests carry that name and numeric `1`.
Off requests omit these controls. Omitted or null scales are not zero-dose
substitutes. The receipt binds request identity and lifecycle, not server-applied
per-request steering state, which the native response/metrics do not expose.

## Readiness, client ownership and recovery

Phases are admitted → prepared4 → created4 → startup_guards4 → started4 →
config_ready → steady_guards4 → client_running → client_terminal → drained.
The HTTP endpoint calls the registered string method `q03_http_observe` with
JSON-only args. It never serializes a Python callable or enables insecure
serialization. The extension and fixture are loaded from the exact verified
source bytes, not ignored bytecode. Each actual worker hash-checks the runtime
and fixture, invokes the existing native/config observation locally, and returns
its actual rank,
resolved engine/runner config, owner state, plus mounted attempt/window/spec
identity. `/health`, Docker launch arguments and controller-synthesized receipt
fields are not readiness evidence. All four reports must match before the
client starts, and the same reports are rechecked after it finishes.

The client is fixed host-side Python, not `docker exec`. The existing independent
remote supervisor owns its process group and descendants. Five-second all-four
container/config/steady-guard checks continue while the client runs. Client
success needs a native result and hash-bound receipt; process exit alone is not
measurement success. An existing output **or** receipt blocks replay before a
request is sent. Terminal client data and receipts are fsynced.

Every remote intent is journalled before dispatch. Recovery seals every epoch
on every physical node, cancels a remaining fixed client and requires positive
client descendant termination before stopping/removing exact service IDs and
owned guards. A timed-out client's lifecycle receipt does not prove the server
request ended: all-four service removal and the core's existing GPU/process
preflight drain are still mandatory. Uncertain Docker/helper/guard outcomes,
late creates without a durable ID, changed sources, foreign identities or an
unproven drain retain the hold and cannot authorize the next arm or release.
Recovery enumerates admitted HTTP journals rather than trusting active PID state.

## Verification and limitations

CPU tests cover the actual adapter loop with injected daemon/transport boundaries
and real filesystem journals; they do not qualify hardware or simulate vLLM.
The exact historical e935 profile, runtime24315 and build-receipt bytes were
read-only-rendered across all six mode/steering combinations with the actual
owner-plan function and published normal renderer, without mocks. This is pure
plan derivation, not full current-site admission. Run:

    PYTHONPATH=scripts python -B -m unittest discover -s tests -p 'test_q03_*'
    git diff --check

Actual cloud-side work does not run SSH/GPU/Docker, start/stop any Spark, write
original assets or use a Mac. Hardware execution remains subsequent, explicit
local deployment/testing after the real owner matrix and new-capsule admission.

Cloud verification: the 379-test Q03 regression passed before the final
owner-matrix identity closure, including 31 independent HTTP fault tests and the
native-safe owner RPC tests. After that narrow closure, all 42 affected HTTP
tests pass, including wrong fixture/tree/profile/effective-config/node-map and
relocated-path controls; syntax and whitespace checks pass. The HTTP string request and four representative primitive response
reports also pass the exact native encoder/utility-result branch probes with
real msgspec. Those probes use unchanged native AST-extracted classes/branches,
not a full vLLM/Torch import or actual four-worker execution. `bin/spark doctor`
and `bin/spark build check` were attempted and both stop at the missing
private `config/nodes-ring4.local.json`, before any target operation. Full native
build and actual service qualification are not claimed.
