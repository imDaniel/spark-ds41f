# Candidate release qualification

The owner authorized repairing the failed tool call and bringing the 1022
attention candidate to release readiness. PR #56 stays draft and unmerged.
The retained r6x identity is restored after this bounded window. All generation
requests explicitly name `heretic-1017-attn-v1` and scale 0, 1 or 4; earlier
U/E/r6x comparisons remain historical evidence, not new comparison runs.

The [plan](plan.json) allows 75 minutes at most, with the final 15 reserved for
recovery. The expected window is 35–55 minutes. A deadline or failed assertion
ends qualification and invokes the independently watched recovery executor.
Missing stages are not passing stages.

## Acceptance

1. Run all 73 classified serving test files in the exact new image, using one
   GPU while all four serving ranks are stopped. Record every skip; steering,
   encoder and the new parser cases must execute without skips.
2. Reproduce the failed named-tool request first, preserving its prompt, scale,
   schema and budget, with generated token IDs added to the receipt. Check exact
   literal arguments for named/required/auto choices, complete/streaming responses
   and scales 1/4, then the assistant/tool continuation.
3. Require complete arithmetic, sorting, JSON, code and Chinese outputs at
   0→1→4→0, with deterministic return to zero. Preserve prose for human review;
   automated string checks alone are not a broad model-quality score.
4. Demonstrate real 32K prefix reuse within a scale, cold isolation between
   scales, mixed requests and cancellation recovery.
5. Exercise cold requests through 1,000,000 input tokens, sixteen simultaneous
   8K requests and four simultaneous 256K/1M requests. Record actual admission,
   prompt counts, cached tokens and resource telemetry. Requests completing
   serially do not establish concurrent capacity.
6. Run two minutes of mixed-scale requests after the large-context pressure.
7. Restore all four pinned r6x identities, verify private and gateway calls,
   remove only this window's ingress rules and hold, and stop its watchdog.

This is the site's explicit-scale qualification. It does not rerun upstream's
multi-arm release comparison, claim statistically unchanged performance from
historical measurements, or authorize production promotion. Kernel/startup code
is unchanged by the parser repair; earlier cold-boot evidence remains linked
through the preserved 1022 experiment rather than being rewritten.

`run_window.py` delegates ownership, heartbeats, process cancellation, coordinated
cluster operations and recovery to the previously fault-tested executor. The
component container is labeled with the window UUID so recovery can stop it
before starting any serving rank. Its image digest equals the TP4 candidate's.
The runtime manifest freshly pins every imported script, bundle file, original
recovery config and selected source commit; stale authorization files cannot be
reused. `test_requests.py` checks the new SSE/tool-only client and rejects
truncation, missing completion markers, missing usage and implicit scales.

Preparation and execution receipts will be added after each gate completes.
