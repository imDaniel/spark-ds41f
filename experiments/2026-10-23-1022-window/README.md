# Bounded 1022 GPU / TP4 window

The owner authorized GPU/TP4 execution on 2026-10-23 after finishing use of the
Spark service. This experiment temporarily uses the four nodes and restores
r6x revision `223a3bb3498d12f470e876db3b9e1f5bdc8772b6` afterwards. PR #56 remains
unmerged. These enabled recipes are separate from the immutable disabled
preparation recipes.

The three built images and four arms are U, E, S-unloaded and S-loaded, in that
order. All use the same 1017 weights, TP4 topology, 1M capacity declaration,
16 sequences, 8192 batch and 10.5 GiB KV/rank. The loaded arm uses the rebound
`e06b8cb6` profile. The retained r6x service keeps its original `d7f0b997` profile.
Image/config/source identities are frozen into a node-local manifest after
this preparation commit is published. No credential is stored in Git.

`site_adapter.py` binds the previously fault-tested recovery executor to actual
SSH, Docker, memguard, image/source/profile checks and API telemetry. A dedicated
UUID-tagged INPUT rule gates only external port 8000. It first blocks new TCP
connections, waits for existing streams to close and zero running/waiting
requests, then blocks external requests completely. Loopback stays available
for private recovery checks. The original CPA provider configuration is not
modified; the public model list and stock/steered aliases are verified before
release. A private API key is stored only in the protected operations folder.

`run_window.py` runs the 29 synthetic steering GPU contracts after coordinated
retained shutdown, then the four TP4 arms. `request_screen.py` records functional
requests, stock return after scale 0/1/4/0, invalid admission, cancellation,
interleaved C4, and 1024/32768-token C1/C4 bounded throughput checks. Graph/eager
numerical contracts belong to the component suite. Identical performance
prefixes intentionally include cache reuse; these numbers are not a full
cold-prefix release benchmark. No full 1M request is scheduled.

The independent controller watchdog runs on 01 and owns the same operation
lock. Restoration starts by minute 75 of a 90-minute reservation, or earlier on
failure. No automatic retry is allowed after a GPU/driver or management fault;
a manual state retains the hold. A failed 01 requires local console recovery.
Every runtime start/stop uses the named `bin/spark` configuration and all four
ranks. Passing local fixture tests is not hardware acceptance; actual results
are recorded separately after the window.

## Long-prefix cache follow-up

Short functional prompts can remain below a retained prefix boundary, so their
scale-alternation success alone cannot establish KV namespace separation.
`cache_screen.py` adds a 32768-token nonce-prefixed workload with explicit
`cached_tokens` assertions: cold 0/1/4 namespaces, warm reuse within each,
stock return, mixed C4 and stock after cancellation. Four injected scenarios
verify that cross-scale reuse, absent hits and mixed-row drift fail the check.

After the initial four-arm run restores r6x, `run_cache_window.py` permits one
additional S-loaded boot. Its separately frozen manifest must retain the
original reservation end and restoration reserve; it must not reset the
90-minute budget. The initial runtime revision and all results remain intact.
This follow-up is skipped after a hardware fault or another hard failure, and
is recorded as incomplete if there is insufficient remaining time.

## Reviewed continuation after the rejection harness error

The first hardware run completed U and E, then stopped after the S-unloaded
server correctly returned an SSE `BadRequestError` with code 400 for a negative
request. The original harness expected only an HTTP 400 and misclassified that
stream response as a failure. Recovery ran immediately. The corrected harness
checks both non-streaming HTTP 400 and streaming HTTP/SSE 400, rejects any content
before a stream error, and still fails server errors. It also covers nonfinite
doses and a valid named profile on an unloaded server.

`run_remaining.py` reuses the hash-pinned completed E reference, runs only
S-unloaded and S-loaded, then performs the long-prefix cache checks in that same
loaded engine. Its authorization and absolute end guard preserve the original
reservation. U/E, the initial 29 passing GPU cases and the interrupted S receipt
remain evidence; this continuation does not rewrite or discard the first run.
