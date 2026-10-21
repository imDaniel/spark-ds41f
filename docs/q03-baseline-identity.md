# First-round baseline identity adapter

This is a new schema-6 admission/observation dependency, not a change to an
existing Q03 schema, frozen capsule, deployed service, or historical result.
The only arms are `r6x188` (8ad, ebb44766/ac795) and `native197-off`
(90bd, 154159dc/ae150). The only steering states are `unloaded` and
`loaded0` through `loaded4`. Both arms force Q03 OFF. Startup-loaded zero is
not unloaded. No arbitrary image, argv, import module, source SHA, or dose
can be supplied through selection.

## Explicit actual provenance, not a reconstructed historical build

`http_service.baseline_identity` has exactly:

```json
{"schema_version":1,"arm":"r6x188","provenance":{"path":"/new-owned/runtime.json","sha256":"<actual bytes SHA256>"}}
```

`candidate` uses image, component_head, runtime_manifest_path,
runtime_manifest_sha256, pytest_base_pythonpath, cache_path and output_path.
The runtime path/hash must be the same explicit provenance above. A188 cannot
use native197's old 24315da runtime file. The historical official build receipt
and inputs are useful provenance if recovered, but are not prerequisites for
this new actual-image comparison or silently fabricated inputs.

The producer records exact arm/image/source head/tree, actual installed vLLM
root, baked PYTHONPATH, platform/Python, distribution metadata versions, package
origins and init-source hashes, Torch's literal version.py value/hash, the two
actual native ELF paths/hashes, and twenty complete source-module hashes.
Those twenty modules are pinned independently from each arm's exact Git
revision and include model, DSpark, mHC, runner/worker, and steering source.
They are not the old G1 selected-function fixture and are not a claim that
all bytes of every Python dependency were audited.

Current raw image inspect separately binds ID, architecture, OS, complete image
config hash, RootFS layer hash, baked Python path, and vLLM/component labels.
All four ranks must agree on the checked image/config/RootFS. Captured native
hashes come from the exact fixed image, not from caller-selected native files.
A fresh capture does not establish an old benchmark's exact historical input.

## Inert producer handoff

On the authorized local lane, run the planner (not Docker) from the new frozen
source checkout:

```sh
python3 -I -B scripts/q03_baseline_identity.py plan-static r6x188 --capture-id <new-32-hex-UUID>
python3 -I -B scripts/q03_baseline_identity.py plan-static native197-off --capture-id <different-new-32-hex-UUID>
```

The returned argv is the complete, fixed producer. Do not replace its image,
program or flags. It uses runc, network none, no devices/GPU, read-only rootfs,
no mounts, all capabilities dropped, no-new-privileges, one CPU, 1 GiB memory,
64 PIDs, 120 seconds, and Python `-I -S -B`. It explicitly disables NVIDIA
visibility. It reads baked PYTHONPATH as data and combines it with sysconfig's
purelib/platlib paths. PathFinder reads the top-level package spec without
importing it; importlib.metadata reads installed metadata. No site/.pth, Torch,
vLLM, CUDA, ELF, model, or network import occurs.

The existing authorized finite supervisor must first prove the unique
`q03-baseline-static-<UUID>` name absent, preserve actual command/image inspect,
node identity, raw stdout/stderr/exit/time, and handle uncertain outcomes by
exact owned identity. Do not infer successful cleanup from client timeout or
launch another capture after unknown daemon state. This planner itself executes
nothing and grants no authority to alter the protected original containers.

The one marked output is `Q03_BASELINE_STATIC=<JSON>`. The pure output validator
rejects missing/duplicate records, wrong arm/image/source, wrong source hashes,
and incomplete runtime evidence. Materialize its exact canonical JSON as a new
owned immutable runtime file and hash those actual bytes. Keep original logs.
Repeat/read equivalent fixed-image facts on all registered nodes through the
existing transport; do not guess endpoints. Current all-four image admission
and actual worker observation are still required before measurements.

## Worker and lifecycle bridge

The ordinary normal-profile CLI uses the source-only extension
`q03_baseline_worker.HttpObservation` and safe string RPC `q03_http_observe`.
Mount only identity+worker sources at `/q03-http`, the explicit runtime file,
and the new attempt spec. Pin both source files with
`Q03_BASELINE_IDENTITY_SHA256` and `Q03_HTTP_WORKER_SHA256`. Retain existing
`Q03_HTTP_WINDOW`, `Q03_HTTP_ATTEMPT`, `Q03_HTTP_SPEC_SHA256`, and safe
serialization=0. Set PYTHONPYCACHEPREFIX=/cache/baseline-bytecode; the worker requires that prefix to be absent, sys.pycache_prefix to match, and bytecode writes disabled at bootstrap and each observation. This prevents baked timestamp/hash .pyc from substituting code while leaving PYTHONPATH untouched. No native197 owner fixture is mounted or required by A188.

`plan.spec.baseline_identity` is the checked identity/runtime/provenance object.
Readiness binds its hash and window/attempt/spec, exact worker/config/TP ranks,
actual imported package and native paths/hashes, independently frozen effective
config for both worker and runner, SM121, SP off, Q03 off/owner absent, and exact
loaded-profile identity. Actual imported Python/Torch versions are recorded
separately from distribution metadata; actual Torch must equal the statically
captured version.py literal, not a guessed metadata normalization. Loaded
profile fields must agree across all four ranks. The bridge compares unchanged
readiness before/after the client, outside the timed workload.

The bridge must retain independent root/source/asset protection, original
keep-stopped, fresh owned resources, guards, logs, positive remote epoch sealing,
watchdog, and all-four drain. Neither this adapter nor a CPU test is a model,
quality, performance, capacity, owner-retention, or freeze acceptance result.

## Focused verification

`python3.12 -B -m unittest discover -s tests -p test_q03_baseline_identity.py -v`
contains fourteen focused tests. They exercise real identity/image/ready
validators; two-arm/six-state acceptance; cross-arm, malformed, source/native,
config/rank/version/profile mutation rejection; actual strict owned-file reads;
actual static producer body in a real isolated `-I -S` subprocess against test
packages that throw if imported; and a real two-source isolated worker bootstrap
with mismatched dependency hashes rejected. A real poisoned timestamp-pyc regression demonstrates that -B alone executes the stale EVIL payload, while the fresh checked prefix imports the GOOD source; a missing prefix configuration is rejected. The CPU fixtures are synthetic and
not field or model evidence. No model/full suite/device action was run.
