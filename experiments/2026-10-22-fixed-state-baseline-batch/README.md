# Fixed-state C1/C4 baseline batching

Base: `f5d8a057de549b4050ac3f136180c9d3f62008f4`.
One intended variable: reuse one owned server boot for the already-defined
`warm4k_c1` then `warm4k_c4` at the same arm and steering state. All original
per-case proofs and final all-four cleanup remain required. The native replay
client, worker, model, images and thresholds are unchanged.

The existing `c5b1c51519b1474832b41d9727415e1d702ec266` source-only historical
sequence planner was inspected, not cherry-picked or activated. It reserves a
different cross-state schema-8 protocol and supplies no runtime authority.

CPU checks (run with `PYTHONDONTWRITEBYTECODE=1` and `python -B`):

```
python -B -m unittest discover -s tests -p 'test_q03_baseline_batch.py' -v
python -B -m unittest discover -s tests -p 'test_q03_*.py'
GIT_NO_LAZY_FETCH=1 GIT_OPTIONAL_LOCKS=0 git diff --check
```

The new tests cover first-case failure, request/cancel after C1, C4 unknown
descendants, immutable metadata isolation, bounded/no-overwrite dispatch,
all-four exact-command sealing, one all-four start and unchanged final drain.
Filesystem journals are real; external actions and observations are synthetic.
Test output is not model or performance evidence. Final CPU results: all 13 new
batch tests pass; the materialized Q03 regression suite passes 215 tests.
Additionally, all 12 old arm/steering single-case plans are byte-identical to
the base revision under the unchanged renderer fixtures. `git diff --check`
passes. These are source-contract checks, not a real same-boot experiment.

Preparation initially failed because shared `/tmp` was full. Only this task's
partial view was moved to a registered independent sparse worktree on the cloud
workspace; its own Git index was compacted to v4. No other author's files were
removed. An initial regression discovery exposed absent sparse fixture modules
and a fixture-compatibility issue in the new result-scope selection; those were
corrected before the final run. The explicit controller/watchdog recovery
inventories were updated to include the new source module.

No installation, full clone, image build, CI, publication, Mac operation, device
launch, service mutation, memory measurement or GPU run was performed.
Remaining integration: independent review and approved successor composition;
freeze the new source/recovery inventories in a new capsule; explicitly select
the fixed two-case field; adapt the still-single-case `read_drained` report
reader to verify parent closure plus each derived client/result path;
then separately conduct any authorized finite hardware validation. Current
single-case execution is unaffected. No broad matrix completion is claimed.

## Clean baseline source composition

Source-only composition base: `eb775f13d4a61ece739003c5fca860864d91ea63`.
Producer: `7ce49d22d5fd0d22cf468eb8cc6a60bff0c5fb8f`; retained-reader delta
`ef27d95b176d1dfd1591fc94af14997f336301c9` and chronology correction
`9da4ff9db4d211010d4e9ecb972b3b6b5714b9ae`. The intended variable remains
only boot reuse for the fixed original warm C1/C4 sequence, never state or image
reuse. Merge resolution preserves quality source pins, schema-7 entry points and
the union recovery inventory. The offline reader separately admits the truthful
new U/S pair while retaining historical semantics and all corrected chronology
checks. It is not a generic batch or benchmark framework.

All five actual clean-baseline hashes remain UNBOUND. There is no publication,
CI, image build, device access, model load, GPU execution or field result. CPU
fixtures and localhost fake HTTP are not thermal, quality, capacity or native
runtime qualification. Tests are run from the independent composed checkout
with `PYTHONDONTWRITEBYTECODE=1 python -B -m unittest discover -s tests -p PATTERN`.
Relevant patterns are `test_q03_baseline*.py`, `test_q03_unpatched_baseline.py`,
`test_q03_clean_baseline_batch.py`, `test_q03_quality*.py`,
`test_q03_legacy*.py`, `test_q03_http*.py`, `test_q03_window*.py`, and
`test_q03_held_window.py`. Source pins are recomputed after composition.

Local `bin/spark doctor` cannot run its site checks in this source-only view:
`config/nodes-ring4.local.json` is absent. No synthetic site configuration is
substituted. No upstream patch series is changed; there is no affected upstream
patch-application test. Build checks require unavailable build/site inputs and
are not run. These limits are not successful native/runtime validation.

CPU validation on the composed source: baseline 54, revised admission 12,
composition 18, quality 105 (one existing skip), complete legacy 91, HTTP 42,
window 81, and held-window 30 tests. All non-skipped tests passed. The final
composition plus historical pair-reader rerun passed 40 tests. These counts
include inherited test methods; they do not imply unique independent runs or
hardware evidence. `git diff --check` passed.

The initial sparse-view runs had missing `servebench` fixture errors (29) and
standalone window imports without `PYTHONPATH=scripts:tests` (49). Materializing
only the required source directory and using that explicit path resolved them;
the complete suites were rerun, not skipped. Review also found an incorrect
proposed direct/None effective steering projection. It was corrected to the
actual `{present, value}` representation and each full frozen state, with an
exact-byte `a144ea7b...` fixture covering every S state and rejecting the wrong
shape. No intermediate source was published.
