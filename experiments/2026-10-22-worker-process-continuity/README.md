# Baseline worker epoch observation

Base: `98b05d2a75fd8d04085f416b145ad593c811e07d` (PR32 source).
One intended variable: add read-only actual-worker PID/starttime observations
and require their valid shape in the baseline readiness report. No measured
performance change or performance claim is made.

Focused CPU commands, all passed:

```sh
python -B -m unittest discover -s tests -p 'test_q03_baseline*.py'
# 54 tests, including actual Linux reader, truncated records and dynamic worker-source pins.
python -B -m unittest discover -s tests -p test_q03_http_service.py
# 11 tests, unchanged original HTTP contracts.
PYTHONPATH=tests python -B -m unittest test_q03_http_independent_faults.ActualHttpLifecycleFaults
# 9 tests, existing owned lifecycle with synthetic external boundaries.
git diff --check
```

The sparse cloud worktree does not include a configured site node map or full
build inputs. `python -B bin/spark doctor` was attempted without `--live` and
could not complete because the configured node map is absent. It is not a
doctor pass. No full suite, build, GPU/model/device operation, site access,
publication or field receipt was performed; no upstream series changed.

Retain as a source prerequisite only. The current P1 source freeze remains
unchanged. Future same-boot field execution requires a new reviewed source and
capsule, exact rank/container/host/source binding, retained boundary snapshots,
and the existing cleanup and positive all-four drain. It must not infer applied
GPU scale, unchanged weights or request idleness from process identity. See
`docs/q03-worker-process-continuity.md` for explicit observation boundaries.
