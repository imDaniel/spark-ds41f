# Release window attempt 2

Published source `2cf58b2a64aa50f948417a76b062cfced4cb925d`; window
`2fb58ec6-68f8-457f-94f4-bce64c95c2f6`; exact parser image `f17320f38b41`.
All 73 component files ran: **1947 passed, 20 failed,
30 teardown errors, 158 skipped**. The candidate TP4
service and request stages did not start. This is a failed qualification.

The 20 failures are incomplete hand-built model/config fixtures: missing
`steering_profile`, `steering`, or `additional_config`. The 30 teardown errors
come from a module-scoped CPU fixture leaving `vllm.triton_utils` replaced while
the global GPU cleanup imports `HAS_TRITON`. Five test files require changes;
no serving source or assertion needs to be weakened. The successor
[fixture experiment](../../../2026-10-23-1022-release-fixtures/README.md) records
the test-only patch and its later verification.

The 158 skips include the upstream's documented NaN/ReLU limitation and missing
offline resources; all original per-case reasons remain in the JUnit reports.

## Recovery review

The component suite intentionally terminates CUDA contexts in two Engram child
tests. Both passed. The kernel recorded two Xid 43 events at 12:32:32 and
12:32:40 UTC, consistent with those tests; subsequent batches of GPU tests
completed. Child host PIDs were not separately captured, so the attribution
rests on timing, the two deliberate-failure cases and continued successful GPU
execution. The fault guard correctly stopped automatic recovery for review.

`nvidia-smi` reported no required recovery action or remaining GPU processes.
A fresh CUDA sum probe passed with no new kernel event. NVIDIA documents Xid 43
as an application fault with the GPU remaining healthy:
https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html.

The operator-reviewed recovery retained the exact original fault journal hash
as a prerequisite, rejected any new event, verified all four source/image/asset
identities, and used the pinned coordinated `bin/spark` start plan. It then
passed live doctor, private and gateway checks before reopening ingress and
removing the hold. No driver reset, Docker restart, configuration promotion or
PR merge occurred. The original manifest and automatic fault guard were not
modified. `state/state.json` retains the original recovery error plus the
manual review hash and final closed state.

All four retained heads, image IDs, source trees, config/node/profile hashes,
commands and mounts match their before-window identities. Gateway configuration
is unchanged. Runner failure and successful reviewed recovery are separate
results.
