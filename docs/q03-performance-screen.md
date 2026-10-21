# Bounded Stage 2 performance screen

This source prepares an executable producer for a future, separately admitted
HTTP window. It does not run a field cell, mutate the current capsule, establish
Stage 1 completion, or approve a performance/capacity result.

## Minimal source composition and admission

Compose the independently reviewed Stage 1b PR26 source
`d8cbf37978b77a621ea26b9c700a97922a603e27` (tree
`77ef9d7e5e7dd19f2e0f8bea71bf41e60cf2fea2`) with this delta, then freeze
one source revision **before** executing the future Stage 1b window. The Stage 1b
(schema 3) and Stage 2 (schema 4) windows must use that identical composed source
closure. The unchanged quality comparator checks its live source inventory; a
standalone earlier schema-3 execution is deliberately rejected. Supporting such
historical evidence would need a separately reviewed finite source binding, not
an import workaround or weaker recovery check. Existing fixed PR21 Stage 1a
history remains governed by the Stage 1b validator's own exact allowlist.

Schema 4 adds exactly `performance_screen` to the ordinary HTTP binding instead
of `qualification`. Its fields are:

- `workload`: `lean_active1_performance_screen_v1`.
- `corpus`: the exact prepared native 16-prompt, 2,048-input-token corpus pin.
- `stage1`: `session` (canonical absolute directory) and its pinned `capsule`.
  Admission rereads the complete six-attempt Stage 1b session, including the
  nested Stage 1a chain, complete actual token IDs, stable controls, equality,
  all-four drain, source/toolbox/model/profile/config identities and no failed
  lineage. A claimed pass summary is not accepted.
- `cell_seconds`: explicitly reviewed integer external execution bound, 41..180
  seconds. The 40-second nominal cell is not a lease estimate. The existing
  `client_seconds` must cover three such bounds plus 15 seconds for durable
  finalization and stay at most 600 seconds. Per-cell execution is interrupted
  at its deadline; evidence serialization/fsync remains under the existing
  whole-client supervisor. Startup, cooling, owned drain and teardown consume
  the separate existing job/outer bounds, not the 240 nominal seconds.
- `metric_labels`: the verified native `model_name` and `engine` selectors.
  Model is `deepseek-v4.1-flash`; engine is an observed numeric label string,
  not an invented boot ID or a silently assumed default.

All original six owner gates, the fixed 10800-second outer lifecycle, stopped
original-service policy, source-only imports, exact-container guards, client
process/descendant supervision, epoch sealing and all-four service/GPU drain
remain required. New source means a new capsule, never migration of the current
field capsule. Existing schema 1, schema 2, scoring defaults and owner behavior
are unchanged.

## Exact executable workload

The controller invokes its existing fixed HTTP client. That client loads the
pinned c6342 PR8 toolbox, then invokes the existing `servebench run` CLI three
times in order c1, c8, c16. Each is closed load, 10 seconds warmup, 30 seconds
measurement in three 10-second subwindows, exactly 64 output tokens, seed 0,
temperature 0, thinking false, context 0 and the frozen native prompt JSONL.
Fresh per-request cache salts and PR8 request/chunk/timing semantics are kept.
The active profile is `heretic-1017-attn-v1`, scale 1, in both OFF and DUAL arms.
The next arm needs an explicit ordinary held-window resume, positive prior drain
and three successful prior cells. A failed attempt stops this finite screen;
no retry-to-green, duration extension or automatic next window is provided.

The three pre-existing `active_1-screen-c{1,8,16}` suite templates use top-level
steering fields. Exact native197 source 154159dc3e38ca8b2321c01a153a1b2ccaf8663b
supports this: `ChatCompletionRequest(SteeringRequest)` inherits
`SteeringRequest._forward_steering_fields`, which copies those values into
`vllm_xargs` and rejects conflicting duplicate values. No payload normalization
or new steering behavior is introduced.

## Evidence and stopping

Fixed instrumentation around the unchanged PR8 functions preserves:

- Original CLI JSON and every unrounded request/chunk record, PR8 window/fence
  clocks, parsed subwindow scrapes and full native Prometheus text for all six
  scrapes per cell, with actual head monotonic start/end brackets.
- Exact endpoint/attempt/plan/corpus/source bindings and a runtime context tied
  to the actual journal's four container IDs/config hashes and readiness bytes.
  Host boot UUID binds the head monotonic clock; native engine labels are only
  selectors. OFF and DUAL may not reuse a service container ID.
- Missing, nonfinite, fractional, reset, negative or foreign native counters are
  failures, never zeros. Required count deltas must exactly match all actual
  completed requests, 2,048 inputs and 64 outputs. Preemption or foreign load
  stops the screen. Decimal parsing retains counter precision beyond 2^53;
  its inline rules derive from the already-reviewed paired receipt parser, not
  a new runtime dependency on an offline validator.
- A first stream/scrape failure latches out new requests immediately and aborts
  the fixed client's current cell. Partial evidence is retained. Remaining
  in-flight work is not called quiescent: the existing process supervisor and
  service teardown must still prove it. No subsequent cell executes.

The original PR8 report is recomputed from retained raw records and native
scrapes under the pinned report source closure; an edited summary cannot change
comparison flags. All three cells must have a single head boot UUID and
nonoverlapping monotonic brackets. Missing thermal, fan and cooling observations
are explicitly UNKNOWN. Existing fan policy stays unchanged; no threshold,
fan control, cooling policy or server setting is added.

## Result boundary

`q03_performance.compare_session(directory)` reads the complete drained pair.
It returns raw point-estimate changes and flags throughput below -3% or latency
above +5% for investigation only. Missing timed statistics are UNKNOWN, not a
positive result. Three within-run windows are not independent boot samples.

Capacity and performance stay INCONCLUSIVE even for a complete short screen.
There is no noninferiority, acceleration, long-prefill, full-capacity, 256-output,
original188 or distribution-quality claim. No 360-minute matrix, new workload,
next-window scheduler or automatic advancement is part of this change.
