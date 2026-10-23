# Recovery fixture scheduling budget

The repository CI for `7a9ad214` passed all 492 repository tests, then one of
the 44 recovery fixtures failed while opening its synthetic idle window:
`test_full_four_arm_sequence_and_pinned_restore` reached the fixture's 0.3-second
drain timeout. [Failure log](https://github.com/imDaniel/spark-ds41f/actions/runs/39939484951/job/120367122930).
The same suite had passed on macOS and the installed ARM64 host. This is separate
from the completed hardware screen and verified retained-service restoration.

`scripts/check-recovery-fixtures.py` loads the unchanged, hash-recorded historical
fixtures and gives only their synthetic drain timeout 3 seconds. It retains the
30 ms idle interval, real subprocess dispatch, busy-queue timeout/refusal test,
and all other assertions. It requires all 44 original cases, adds one delayed
observation regression (400 ms), and fails on errors, failures or skipped cases.

The production executor, runtime manifests and completed experiment evidence are
unchanged. This follow-up neither synchronizes nor starts any cluster service.

Local validation on macOS / Python 3.15: all 45 recovery cases passed in 76.187 s,
and all 19 window adapter/request/cache tests passed. The delayed-observation
regression separately reproduced the timeout with the original 0.3-second budget.
Run `PYTHONDONTWRITEBYTECODE=1 python3 scripts/check-recovery-fixtures.py` and the
window tests from the workflow to reproduce. The CI log above remains the original
failed attempt; subsequent checks belong to the new commit.
