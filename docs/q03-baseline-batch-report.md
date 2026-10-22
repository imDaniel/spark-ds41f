# Offline intake for one fixed warm4K batch

This reader adds no execution or testing lane. It consumes only an already
closed, keep-stopped historical A188/B197-OFF pair or revised clean-U/steer-S
pair produced by the finite same-state
`warm4k_c1` then `warm4k_c4` batch. Both arms must contain the whole batch. A
single case, a partial batch, mixed single/batch input, retry selection, reordered
case, arbitrary job or changed arm/steering state is rejected.

Run from the exact source checkout matching the retained recovery capsule:

```sh
python3 -I -B scripts/q03_legacy_pair_report.py \
  /original/a/session/attempts/ATTEMPT_A \
  /original/b/session/attempts/ATTEMPT_B
```

The two arguments identify retained attempts, not client subdirectories. Do not
move, rewrite, relabel or normalize hash-bound artifacts to make intake pass.
The command reads only, emits JSON on stdout, and returns nonzero on rejection.
There are no case-selection, job-selection, source-bypass or acceptance flags.

## Complete retained contract

The reader checks the closed outer capsule and its sole passed typed attempt,
including the derived child candidate paths and unchanged jobs/selection. It
reproduces the parent plan from hash-verified original resource JSON, preserving
nested original ordering when the renderer embeds JSON in argv/environment
strings. Canonical serialized plans are not used as replacement source inputs.

For both cases it independently derives the full client plan, separate output
and controller paths, parent hash and index. It validates the real original PR8
client evidence and summary, exact supervised receipt, controller-owned context,
per-case result, and the complete parent result. The exact ordered case journal
also binds each context/result, predecessor result, fixed supervised command,
operation ID, successful terminal receipt and all-four positive epoch seal.
Retained rank-zero remote-operation manifests and receipts must match their
recorded hashes, original command, helper source and client chronology.
Each case also requires exactly one controller prefix-reset event between its
intent and context pin, with the exact context receipt. All four final thermal
samples and the reset must follow the previous client's supervised completion
(for C4) and precede that case's operation creation. These comparisons use Unix
wall-clock timestamps only, never the client's monotonic timing domain. Rank-zero
seal operation receipts must preserve every immutable terminal receipt field.

Both contexts bind the same four containers/configurations and initial readiness.
The parent must have the complete successful lifecycle, all-four retained exact
container logs, daemon retirement and final GPU drain after C4 finishes. Failed,
partial, cancelled, timed-out, unknown, duplicated and aliased evidence cannot
produce a complete batch report. The physical observations remain supplied
retained evidence, not independent attestation.

## Source compatibility and interpretation

The reader names the batch recovery-source contract `owned-http-finite-batch/2`.
It still requires the exact whole recovery inventory **and every source hash**;
this is not a list of optional dependencies. The pre-batch inventory is named
`owned-http-single/1` for diagnostics and must be consumed by its matching
archived single-case reader/source checkout. Removing `q03_baseline_batch.py`,
editing a capsule or relaxing source checks is not a compatibility path.
Matching-source single-case attempts retain the existing single-case report
schema and behavior. The batch report uses `legacy-baseline-batch-pair-report/1`.

A complete two-arm batch is **one boot pair with two different cases**, never two
independent paired repetitions. Each per-case comparison is descriptive and
shares that boot pair. Do not pool C1/C4 subwindows as independent observations.
Performance remains INCONCLUSIVE, quality/capacity NOT_EVALUATED, and no
between-boot confidence interval or acceptance decision is produced. This is
one steering state, not the complete matrix or an implementation of cold cases.

CPU regressions use real original C1/C4 clients over localhost fake HTTP,
canonical retained plans and deliberately non-sorted original peer maps. The
registered fixed-profile hash, runtime provenance and physical daemon
observations are synthetic test seams. They are not model, GPU, native-image,
site, thermal, capacity or performance validation.

## Revised clean U/S pair

The new `r6x-unpatched/absent` and `r6x-steer` selections are separately bound
by the [clean baseline admission](r6x-unpatched-baseline-admission.md), not aliases
for the historical A188/B197-OFF pair. The reader uses the exact selected
profile/effective-config pins, shared recipe and shared runtime. It removes only
the declared `additional_config.ds41_steering` value when comparing effective
configuration across U and the fixed selected S state. U has no loaded profile;
S retains its own four-rank profile/capability proof. Output uses explicit
`R6X_UNPATCHED`/`R6X_STEER` arm labels and actual states. Mixed historical/revised
pairs and any unbound revised admission are rejected.

This composition also preserves the schema-7 quality dependencies. Contract
version 2 requires their source inventory as well as batch dependencies; every
source hash must match, including for single-case attempts read here.

Revised single-case and batch reports use `r6x-baseline-pair-report/1` and
`r6x-baseline-batch-pair-report/1`, respectively. Their metric columns are
`r6x_unpatched` and `r6x_steer`; they are not the historical `a188`/`b197_off`
columns under a reused schema. Historical schemas and columns remain unchanged.
