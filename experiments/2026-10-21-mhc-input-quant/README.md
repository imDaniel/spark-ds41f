# mHC routed-input quantization

Status: source replay and CPU/static checks complete. The candidate is off by
default and is not image-, GPU-, capacity- or performance-qualified.

## Intended variable and scope

For the eligible TP4 SM121 target decoder's non-sequence-parallel FFN entry,
preserve the existing BF16 mHC output and also emit the routed experts' MXFP8
input and packed UE8M0 scales. The routed consumer reuses that pair instead of
casting BF16 again. Router and shared experts continue to consume BF16.
Attention entry, drafter, sequence-parallel and unsupported modes retain the
complete existing path. These are optimization eligibility checks, not new
restrictions on model functionality.

Steering off, loaded with zero request scale, and active retain their existing
semantics. The candidate preserves both BF16 rounding boundaries and uses the
pinned cast configuration. The sample profile explicitly keeps
`VLLM_DS41_Q03_MHC_MXFP8=0`. Changing it to 1 selects the experimental path only
for eligible configurations; doing so is not evidence of qualification.

## Frozen source and mailbox composition

The verified deployment base is `3ff57762036fc72c2221c3845a39e70850fd2d7e`.
Starting from vLLM `19f2c20ed4d64ae22bd009ceacadd85e2a81e354`, the
[series](vllm/series) composes the original 176 upstream and 12 steering
mailboxes exactly once, followed by four new mailboxes in this independent
namespace:

- 0500: optional rounded mHC quantization and GPU differential tests;
- 0501: explicit routed-input consumption across the opaque MoE boundary;
- 0502: target-owned buffers allocated before profiling;
- 0503: standalone CPU host/static contracts.

Existing files, order, source manifests, patch maps and historical results remain
unchanged. References to old mailboxes are relative paths, not copied or
renumbered patches. [patch-map.json](patch-map.json) records all 188 inherited
file hashes, four new mailbox hashes/commits and changed-file blob identities.

A fresh local-only replay of all 192 mailboxes independently reached the inherited
checkpoint `ebb44766d6f04d3a9272b712778d7f2fa2cec1fd` / tree
`ac79557915e3ee2778f8f6353a3d35f848f564e4`, then the candidate head
`dd0d8326d31456a6038ac2f244f765e8d783ce59` / tree
`9cee307fa4fcfca0b907bafc192a7866ea77ec83`. Replay used the project's fixed
committer with `git am --committer-date-is-author-date`; its complete log,
including the inherited trailing-blank warning, is retained under
[runs/source-replay](runs/source-replay). The final base-to-head diff check passed.

The candidate patchset fingerprint is
`5a918aefd8a12c5c36aae9117ba7059e976a4503efae3362fd41da1df3dddd7e`.
It uses `bin/spark`'s algorithm: each applied upstream commit revision plus NUL,
then raw series bytes, then each literal entry name plus NUL and patch bytes in
series order. It is not a checksum of concatenated patch files.
[base.json](base.json), [source.json](source.json) and
[upstreams.lock.json](upstreams.lock.json) bind those actual identities. Non-vLLM source records and their verification
descriptions are inherited historical evidence, not freshly validated components.

## Recorded checks and limits

- Frozen-source standalone host/static suite: 30 passed, no failures, errors or
  skips. [Native receipts](runs/host-final) include exact test IDs and source
  hashes. The harness extracts actual host function bodies with explicit metadata
  adapters; it does not execute Torch dispatcher registration, device arithmetic,
  CUDA graphs or model inference.
- Syntax parsing: all ten changed Python files passed. This is not kernel
  compilation.
- Release classification: 125 selected files, 68 gate, 57 excluded, zero
  problems. [release-tests.json](release-tests.json) classifies the new host file
  and existing mHC GPU file. Classification is not execution of those GPU tests.
- Packaging unit checks: five passed, covering immutable inherited mailboxes,
  exact order/no duplication, source/lock/profile consistency and preserved
  steering/capacity settings.
- Candidate local doctor: configuration OK using an ignored example node map.
  The initial baseline doctor failure for a missing map remains recorded.
- Complete candidate build-input check: blocked because its content-addressed
  build directory is not prepared. No full image inputs, image build, GPU test,
  cluster operation or performance result is claimed.

[Host development receipts](runs/host-development) retain the initial two fixture
failures and one adapter error and all subsequent runs. An early pipeline masked
the failed shell status; its raw FAILED report remains authoritative. The
intermediate host-02 run is historical, not the final-source result.
The original steering [cpu-cases.json](cpu-cases.json) is unchanged; this
packaging run did not re-execute that full inherited gate. The new
[q03-cpu-cases.json](q03-cpu-cases.json) records the distinct frozen-source suite.

## Resource and qualification boundary

One target-model owner shares two independent persistent q/scale allocations
across eligible layers. At 8192 rows their combined payload is 43,253,760 bytes
(41.25 MiB per rank); the previous routed workspace remains reserved. This is
not a zero-memory-cost change. Allocate before profiling/capture, retain stable
pointers for graph lifetime, and preserve 1,048,576 context tokens, 16 sequences,
8192 batched tokens and 10.5 GiB KV per rank without lowering memory guards.

The [isolated profile](cluster.json) preserves the existing steering artifact
mount/arguments, TP4 sizing and inherited `launch_enabled=false`; it changes only
candidate source/image identity, checkout/cache namespace and example node-map
path. The image tag names an intended build, not an existing image.

Subsequent separately authorized SM121 validation must establish BF16/FP8/scale
correctness, nonfinite behavior, generated-code resources, actual cast
elimination, frozen preparation, graph/stream safety and capacity. Whole-model
or performance claims require matched measurements and native receipts. None of
those later gates is satisfied by CPU/static success.

## Reproduction entries

After obtaining the pinned build inputs in an authorized environment:

```sh
cp config/examples/nodes-ring4.json config/nodes-mhc-input-quant-example.local.json
bin/spark --shape tp4-ring --cluster-config experiments/2026-10-21-mhc-input-quant/cluster.json doctor
bin/spark --shape tp4-ring --cluster-config experiments/2026-10-21-mhc-input-quant/cluster.json build prepare --only vllm
python scripts/release_tests.py check VLLM_REPO 19f2c20ed4d64ae22bd009ceacadd85e2a81e354 dd0d8326d31456a6038ac2f244f765e8d783ce59 experiments/2026-10-21-mhc-input-quant/release-tests.json
python VLLM_REPO/tests/models/test_deepseek_v41_q03_host.py
python -m unittest discover -s tests -p test_mhc_input_packaging.py -v
```

`VLLM_REPO` denotes the clean replayed candidate checkout. Partial source
preparation is not a full build check. No image apply, GPU or deployment command
is authorized by this record.

## Final deployment CPU validation

Full repository unittest discovery: 485 tests passed in 180.064 seconds, without failures/errors/skips. The first two attempts and their local test-map setup failures remain in `runs/deployment-suite/`; only ignored fixture maps changed between runs. Synthetic benchmark output is loopback fake-service testing, not model/GPU performance. No production profile or historical evidence was modified.
