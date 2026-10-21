# mHC routed-input validation

Public test-only package. P0 full imports and G1/G2 GPU execution have NOT run.
No image-build, capacity, performance or deployment qualification is claimed.
No operational cluster profile, node map, infrastructure inventory or raw log
is included. Private evidence is retained separately; published summaries contain
only test counts, scoped outcomes and public source identities.

## Frozen source

Deployment base: `610cbde6cd65284dceb9202136a6892a1fb7211c`.
Runtime deployment provenance: `3ff57762036fc72c2221c3845a39e70850fd2d7e`.
Component base: `19f2c20ed4d64ae22bd009ceacadd85e2a81e354`.
The series composes the unchanged 192 inherited mailboxes exactly once, followed
by five test-only mailboxes numbered0500–0504. Published historical evidence and
mailbox bytes are not rewritten.

Final HEAD: `154159dc3e38ca8b2321c01a153a1b2ccaf8663b`.
Final tree: `ae15047b376c7792062a2607c4ef1dfbc78df18a`.
Patchset SHA256: `98741a02fde9452b0a7dc906861ec381c2bff20a4f0067ba61c3fe543d9f92e5`.
Fresh full197 replay reproduced188/192/196/final checkpoints. Runtime `vllm/`
bytes remain unchanged from the192 checkpoint. See base.json and patch-map.json.
The fingerprint uses base revision/NUL, raw series bytes, then each literal
entry/NUL/patch bytes. Replay uses `git am --committer-date-is-author-date` with
historical committer Christopher Owen <3221756+christopherowen@users.noreply.github.com>.

## Evidence scope

The private integrated deployment suite passed512 tests, zero failures/errors/skips;
an earlier interrupted attempt (exit130) is not a pass. Separate component stdlib
checks passed15 and inherited host checks passed30. Prior packaging checks passed5.
Release classification selected128, gated71, excluded57, with0 problems.
These precede public packaging minimization; they do not qualify a new public
operational profile. Sanitized counts and scoped outcomes are in
cpu-checks.json. Other component source descriptions and cpu-cases.json retain
historical provenance, not a new dependency build result.

Complete build check failed because candidate inputs were not prepared. Image
compilation, P0 and GPU execution remain unexecuted. CPU AST/discovery and receipt
accounting are not full runtime imports or numerical execution.

## Reproduction

From the deployment repository root, with VLLM_REPO set to a clean prepared
candidate component checkout:

```sh
python -m unittest discover -s tests -p test_mhc_validation_packaging.py -v
python scripts/release_tests.py check "$VLLM_REPO" 19f2c20ed4d64ae22bd009ceacadd85e2a81e354 154159dc3e38ca8b2321c01a153a1b2ccaf8663b experiments/2026-10-21-mhc-input-validation/release-tests.json
python "$VLLM_REPO/tests/models/test_deepseek_v41_q03_validation.py"
python "$VLLM_REPO/tests/models/test_deepseek_v41_q03_host.py"
```

The [validation runbook](validation-runbook.md) pins exact P0,20-case G1 and12-case
G2 commands and expected hashes. G2 is bounded producer-to-plan, not full model,
MoERunner or distributed TP4. Opcheck, functionalization, fullgraph and production
lifetime/capacity/performance remain separate gates.

An optional offline generator accepts an owner-supplied private independent
baseline and writes only ignored cluster.local.json. No site defaults are shipped.
Existing private profiles and raw results must never be added to Git.
