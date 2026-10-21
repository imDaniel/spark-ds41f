# Retain stopped original services during optimization

Owner instruction: this optimization campaign does not need the original model
services running. The explicit private capsule preflight setting
`original_service_disposition: keep-stopped` changes only lifecycle disposition.
Legacy capsules without it retain their original restore policy.

The new capsule accepts either a fully ready/idle registered original or a
positively identified, stopped and drained four-node original. It still owns a
hold, independent watchdog, pinned recovery chain and all candidate cleanup.
Normal close, request, lease expiry and recovery finish by coordinated original
STOP and positive all-four GPU/guard/input drain, then release the hold without
START. A distinct closed-stopped marker is never described as service restoration.
Unknown state retains ownership and evidence. Later capsules can start from the
verified stopped state. No original policy/config, image tags, weights, caches or
old capsules change. Native197/image90bd and Q03 owner fixtures stay unchanged.

Validation pending: local source AST and whitespace checks only while original
service is running; full CPU lifecycle fault injection is delegated to the cloud
reviewer before field execution. Preserve failed and successful results.

Added CPU cases cover explicit/default policy, ready live entry versus positive
stopped entry, mixed/missing guard/GPU proof, no START on successful or failed
jobs, retained hold on unknown drain, release-write crash reconciliation and
watchdog completion/recovery. These are control-plane tests, not GPU results.
