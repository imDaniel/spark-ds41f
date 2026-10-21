# Validation runbook

Source freeze: `154159dc3e38ca8b2321c01a153a1b2ccaf8663b`, tree
`ae15047b376c7792062a2607c4ef1dfbc78df18a`. Runtime `vllm/` bytes are unchanged
from dd0. P0 execution and G1/G2 GPU execution have NOT run in cloud.

## Source-tree and installed-runtime contract

The image may not contain test files. Use the exact prepared candidate source
checkout's tests in the authorized isolated environment, and independently
verify the installed runtime identity. Do not silently overlay baseline runtime
with PYTHONPATH. Verified wheel, editable and image-source candidate installs are allowed; an unknown
Python overlay is not a candidate installation. Native extension origins/hashes
and package versions come from reviewed build/install receipts.

Commands below run from the prepared vLLM source-tree root. `$PYTHON` is the
absolute path to its approved installed-runtime Python; `$DEPLOYMENT` is the
absolute path to the separately frozen deployment checkout; `$RESULTS` is a new
independent results directory, never either original service's directory/cache.
Set those paths from the independently reviewed private execution plan. Verify:

```sh
test "$(git rev-parse HEAD)" = 154159dc3e38ca8b2321c01a153a1b2ccaf8663b
test "$(git rev-parse HEAD^{tree})" = ae15047b376c7792062a2607c4ef1dfbc78df18a
test -z "$(git status --porcelain)"
EXPERIMENT="$DEPLOYMENT/experiments/2026-10-21-mhc-input-validation"
```

Record source tree, installed runtime/native hashes, actual architecture,
versions, command, timestamps, exit codes, stdout/stderr and JUnit. Keep every
failed attempt. Test availability and runtime identity are separate checks.

## P0: full imports and real registration, FakeTensor dispatch

The standard-library launcher uses two isolated child processes, default opaque
LayerName and explicit string mode. Safe listing only (not a P0 pass):

```sh
"$PYTHON" tests/standalone_tests/deepseek_v41_q03_registration.py --list
```

After reviewed installation evidence exists, set `$RUNTIME_MANIFEST` to its
independently reviewed manifest. Do not manufacture this from an unchecked image.
The manifest schema is in the source's
`tests/standalone_tests/deepseek_v41_q03_registration.md`. Exact invocation:

```sh
"$PYTHON" tests/standalone_tests/deepseek_v41_q03_registration.py \
  --run --allow-platform-initialization --expected-platform cuda \
  --expected-head 154159dc3e38ca8b2321c01a153a1b2ccaf8663b \
  --runtime-manifest "$RUNTIME_MANIFEST" \
  --output-dir "$RESULTS/p0-registration-run-01"
```

Full module imports may initialize native/device dependencies even though calls
use FakeTensors: this is NOT CPU-only execution. Both processes must pass with
complete reports. It does not run full opcheck, AOT functionalization, actual
MoE arithmetic or a model. Stop on any nonzero status before G1.

## G1: pinned original188 / candidate-off / dual

Exact file: `tests/kernels/test_deepseek_v41_mhc_three_way.py`.
`expected/g1.json` pins 20 full pytest node IDs independently derived from source
decorators before any result: seven row boundaries, finite edges, three nonfinite
cases, nine steering/row projection cases. The offline original fixture is the
exact decorated finalize/constants from ebb44766 / tree ac795579, source blob
`d238757bb886e5df208dcffbea6abda9332f4749`, SHA256
`19e3f85d32ec917dc62839e564f494cfb44d1ebbb6d42008e2d213a1b789f23b`.
It is not a full baseline module/model import.

```sh
set +e
VLLM_Q03_RUN_G1_GPU=1 "$PYTHON" -m pytest -q \
  tests/kernels/test_deepseek_v41_mhc_three_way.py \
  --junitxml="$RESULTS/g1.xml" >"$RESULTS/g1.log" 2>&1
G1_STATUS=$?
set -e
"$PYTHON" tests/kernels/q03_result_receipt.py --stage G1 \
  --expected "$EXPERIMENT/expected/g1.json" \
  --expected-sha256 0abf29b59d76abeb84e10ddc5341329fa75c79e2659badc0353b6d26e4212ce5 \
  --source-tree ae15047b376c7792062a2607c4ef1dfbc78df18a \
  --junit "$RESULTS/g1.xml" --exit-code "$G1_STATUS" \
  --output "$RESULTS/g1-receipt.json"
```

## G2: bounded real producer-to-plan and arena lifetime

Only after P0/G1 pass, exact file `tests/kernels/test_deepseek_v41_q03_owner.py`
uses real legal post_pre producer and MoeExpertsV41Plan, not full MoERunner or
TP4/full model. `expected/g2.json` pins 12 IDs: nine steering/row combinations,
one shared-addend/event case, one alternating graph/eager case and one retained
allocation case. Its comparator is candidate BF16, with original188 proof in G1.

H=5120, per-rank I=576, 8 experts, top-k2, active rows at most96. The 8192-row
arena payload is 41.25 MiB; conservative fixture payload estimate 275,775,488
bytes is below 512 MiB, with a separate 2 GiB free-memory precondition. These are
not measured total allocator/driver/compiler peaks. Capacity at8192, production
shared-expert scheduling, actual graph wrapper retirement, fullgraph compiler,
full MoERunner and distributed TP4 remain separate unexecuted gates.

```sh
set +e
VLLM_Q03_RUN_G2_GPU=1 "$PYTHON" -m pytest -q \
  tests/kernels/test_deepseek_v41_q03_owner.py \
  --junitxml="$RESULTS/g2.xml" >"$RESULTS/g2.log" 2>&1
G2_STATUS=$?
set -e
"$PYTHON" tests/kernels/q03_result_receipt.py --stage G2 \
  --expected "$EXPERIMENT/expected/g2.json" \
  --expected-sha256 c8a33ae139760934448da9b4fbc7ec162a9fa9ecacba4dadc4fda6556d011374 \
  --source-tree ae15047b376c7792062a2607c4ef1dfbc78df18a \
  --junit "$RESULTS/g2.xml" --exit-code "$G2_STATUS" \
  --output "$RESULTS/g2-receipt.json"
```

Use a new results directory for every attempt; checker outputs cannot overwrite
an existing receipt. Missing/zero/skipped/failed/extra/duplicate IDs or nonzero
pytest exit fail the gate. Exact inventory is independent of finished JUnit.
Receipt success proves result accounting, not runtime provenance. Default module
skips, collection only or AST checks are not GPU success. No test repairs service
or hardware as a side effect. Stop at first failure; preserve evidence.

## Operational boundary

This public package contains tests and reproducible source identities, not a site
configuration or execution authorization. Set paths from an independently reviewed
private plan. Preserve existing services and their files; use isolated candidate
checkouts, caches, output directories, containers and image identities. No image,
GPU, capacity or performance qualification is claimed by these CPU records.

No operational cluster template is included. If needed, derive the ignored local
profile from an owner-supplied private independent baseline with
`python experiments/2026-10-21-mhc-input-validation/create_local_profile.py --baseline "$PRIVATE_BASELINE"`.
This writes only a new cluster.local.json beside the generator, preserves baseline
capacity arguments, selects this candidate lock/tree, and keeps launch/optimization
disabled. It does not establish path isolation or authorize a build/deployment.
Existing local output is never overwritten.

For the official built-image layout use installation.mode=image-source with
reviewed package_root/native hashes; preserve the baked-in image PYTHONPATH.
This does not permit a host-source overlay or a fabricated build manifest.
