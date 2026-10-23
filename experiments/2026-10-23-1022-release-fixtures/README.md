# 1022 release fixture correction

The [complete component run](../2026-10-23-1022-release-readiness/runs/attempt-2/README.md)
exposed 20 test failures and 30 teardown errors. This successor preserves all
199 published patches and appends one test-only patch. Hand-built model and
configuration fixtures now carry the same disabled-steering defaults as their
real constructors. CPU import stubs are function-scoped so they are restored
before the real per-test GPU cleanup runs.

Only five test files change. The source inventory proves every runtime and
native input byte is identical to the preceding parser candidate. The parser
repair, attention arithmetic, weights, profile, launch parameters and native
libraries are unchanged. The corrected files pass 133 CPU tests; 27 GPU-only
cases remain for the protected hardware window. The full 200-patch replay is
independent of the editable source branch.

The image is built once and distributed by exact config digest. Its full source
archive and 32 retained native libraries are audited during the build. The
unchanged profile remains `e06b8cb6…`; there is no new profile rebind.

## Qualification window

The request plan remains candidate-only with explicit scale, complete tool
arguments, quality, 32K cache isolation, 1M inputs, concurrent capacity and a
bounded stability pass. Existing U/E/r6x comparison receipts are reused, not
rerun. `run_window.py` retains the pinned recovery executor and independent
watchdog and restores r6x after the window. PR #56 remains draft and unmerged.

All 73 component files run again. Two deliberate CUDA-context failure cases in
`test_deepseek_v41_engram_rows.py` are deselected with explicit receipt references:
both passed in the previous exact-runtime run. Their unchanged source and native
inputs permit that evidence to be reused, avoiding intentional Xid 43 injection
in an automatically recovered window. This does not suppress unexpected driver
faults. Every previous failure and teardown error must execute and pass. The
upstream NaN and offline-resource skips keep their original explanations.

The previous task's TileLang cache was copied to a new task-specific cache path;
serving caches and immutable result artifacts were not altered. Runtime/native
inputs are identical, so cached compilation does not change what is tested.
Missing cache paths are rejected before the window opens. The component
container and candidate TP4 service must use the same image digest.

Publication and GPU/request acceptance are separate gates. This preparation
record alone does not establish release readiness or authorize promotion.
