# Steering hardware qualification

<!-- markdownlint-disable MD013 -->

Status: hardware comparisons completed; PR #4 remains Draft and the candidate is not promoted. These are isolated test recipes. Existing service recipes, model weights and profile artifacts remain unchanged. See [results and limits](RESULTS.md) and [normalized receipts](results.json).

The intended variable is the steering execution implementation. The four arms use the same official 1017 weight mount, TP4 ring, precision, speculative policy, 1,048,576-token limit, 16 sequence limit, 8192-token batch budget and explicit 10.5 GiB KV budget. All arms expose only a loopback test port, use the same profiler configuration and enable the internal fixed-workload benchmark endpoint. Cost curves and caches are separated from the retained service.

| Recipe | Implementation | Image ID | vLLM tree |
| --- | --- | --- | --- |
| [official.json](official.json) | original 172-patch model, no steering | `sha256:70277be306b752c829feda50f25f36f502bb32809a04b96dd6e1164f29977506` | `d11e9836e61bc2b763d6805d3324ec8fb5376360` |
| [parent.json](parent.json) | independent 180-patch parent, bundled profile loaded | `sha256:a63259a196d82dae51016d487ed43093f2045261adcb41045cfe9c1be29f70d2` | `5de1eabc3f19b3f646796ef802cbf26a181661a6` |
| [candidate-unloaded.json](candidate-unloaded.json) | 184-patch candidate, no loaded profile | `sha256:6bfca1ba83e22bd6ed3933fa8837f25131bbbb791ad6e6d7c4c0587977ad597f` | `efcbb1ef861a59d22bace43e998067de9c1708de` |
| [candidate-loaded.json](candidate-loaded.json) | same candidate, bundled profile loaded | same candidate image | same candidate tree |

The loaded arm uses `heretic-1017-attn-v1`, alpha 2.5, attention layers 2–39 and request scale range [0,4]. Loading via `hf:default` resolves the bundled artifact; requests still name the actual profile ID. Alpha and request scale are recorded separately.

The image was built once on the pinned native parent. All 7699 tracked vLLM source files were SHA-verified in the image and the native binary hashes were unchanged. Its source labels identify the candidate head `43608a373a5d1a062dbd754d37031fb5314deabc`; the inherited deployment revision label identifies the build controller's pre-publication commit, not a later deployment commit. Distribution verifies the cached parent layer prefix, the image delta SHA and identical final image IDs on all ranks.

## Evidence completed before whole-model testing

- Initial candidate tree `c035b638c729a0beb24e7deccc29e7c4e0fa9eec`: 25 GPU cases passed and 4 fused graph cases failed TileLang compilation. The failure and logs are retained. Patch 0192 writes each BF16 fragment inside its defining conditional region.
- Current tree `efcbb1ef861a59d22bace43e998067de9c1708de`: 29 real SM121 GPU cases passed, including 101 scale changes in a captured active graph. ARM image CPU gate: 108 passed, zero skipped; actual OpenAI request-class wire checks also passed on CPU.
- Independent parent/candidate images compiled their own stock mHC code with separate caches. Twelve cases covering pre/broadcast/post-pre, world sizes 1/4 and rows 1/96/8192 have identical input and output bits, stable pointers and successful graph replay. Timings are diagnostic only.
- Fresh replay of all 184 patches reproduces the declared head/tree/patchset; final diff check passes. The current macOS CPU gate executes all 108 pinned cases without skips.

## Completed hardware scope

The real four-rank runs cover loaded/unloaded startup with the explicit KV budget, target/draft graph replay, stock/active transitions, mixed requests, cancellation and slot reuse, 8K/32K prefill, independent stock token/logprob comparisons, all-rank prefill/decode traces and one exact 1M request. The fixed grid uses 60 alternating rounds for eleven shapes; a separate 100-round current-token-only control removes historical KV. Real C1/C4 load windows retain TTFT/TPOT/TPS, draft acceptance, preemptions and failures.

The independent 180-patch parent and 172-patch official references are complete. The final candidate restart repeats smoke, scale=0 fixed grids, the zero-context control and C1/C4 load before any profiler use in that process. All runs, including earlier candidate runs and instrumentation failures, are retained. Short-run agreement does not establish every workload's equivalence; default-budget startup and four concurrent 1M contexts are outside this result. The candidate remains unpromoted pending owner acceptance.

Site-local nodes live in ignored `config/nodes-steering-gpu.local.json`. Deploy only a clean published fork commit via coordinated `bin/spark cluster sync/start/stop`; the unchanged host policy is validation input only. Local raw receipts are under HomeLab `ops/steering-gpu-acceptance-20261019/evidence/`; the head node's isolated runtime retains image builds, GPU logs, traces and per-arm model receipts. Private pre-window configuration snapshots are excluded from Git.

The explicit `QualificationWorker` extension is mounted read-only in all four test arms. Its state RPC reads graph/control/memory metadata. Its fixed-grid RPC temporarily substitutes dummy-only state preparation, preloads a fixed scale, runs the existing engine grid and restores the original state in a `finally` block. The candidate active grid preloads control rows; the legacy parent retains its original dummy zero-fill, identified in the result. The adapter rejects real batches; normal request handling is not wrapped. Real-request traces separately verify row preparation and request routing. The extension and profiler are absent from retained service recipes.

Initial coordinated start attempts retained two setup failures: missing new cache directories (preflight, no launch) and a qualification-only `PYTHONPATH` override that selected the base wheel instead of the pinned source (argument parsing, no model load). The recipes now preserve the native image source paths and prepend only the diagnostic module. All three independent images must pass source/config import checks before another start. These were test harness failures, not GPU or model results.

The parent model arm loads the retained profile so scale=0 measures the former disabled path as actually served. The official arm supplies a true original-model reference. Parent scale=0 uses the legacy graph ABI and zero controls; active fixed-grid timing is limited to the candidate. Real requests compare both implementations with the same profile and strength.


## Repeating a probe

The [client](probe_model.py) runs on the head or another host with access to the loopback test API. It reserves a unique `--output-dir/runs/<run-id>/evidence` directory for raw HTTP responses, fixed-grid samples and servebench receipts. `--run-id` may name a new directory; an existing ID fails before network requests. It never starts or stops a model. Serialize phases; reset-prefix operations require an idle test server. The `parent` arm loads the retained profile, but its fixed grid is scale=0 only; use `active-engine` only with the candidate.

```sh
python3 experiments/2026-10-19-steering-gpu/probe_model.py candidate-loaded smoke --url http://127.0.0.1:8004
python3 experiments/2026-10-19-steering-gpu/probe_model.py candidate-loaded active-engine --telemetry /path/to/host-telemetry.jsonl
python3 experiments/2026-10-19-steering-gpu/probe_model.py candidate-loaded prefill --telemetry /path/to/host-telemetry.jsonl
python3 experiments/2026-10-19-steering-gpu/probe_model.py candidate-loaded bench --telemetry /path/to/host-telemetry.jsonl
python3 experiments/2026-10-19-steering-gpu/probe_model.py candidate-loaded decode-trace
python3 experiments/2026-10-19-steering-gpu/probe_model.py candidate-loaded capacity
```

Measured phases require a fresh JSONL telemetry record containing `samples` for all four nodes, each with Unix `time` and `hottest_c`. Every measurement begins below 55 °C; the fan policy remains the retained firmware policy because its optional host module is absent. This differs from an upstream promotion run with the external fan curve. The client refuses stale/missing thermal evidence. The benchmark uses the first 16 deterministic default servebench prompts, seed 2048, temperature 0, 4096-token document context, fixed 256-token outputs, a 15-second warmup and six windows over 90 seconds at C1/C4. Fixed grids run 60 alternating rounds with one settle step and three warmups per shape. Real prefill uses 8192/32768 targets and three repeats. Capacity sends exactly 1,048,568 input token IDs and requests eight output tokens, checking both counts.

Whole-step grids fix shapes, context, dummy tokens and draft work; changed activations can still change MoE routing. They separate output acceptance from step cost, not every data-dependent effect. Real TPS additionally depends on generated tokens and DSpark acceptance. Short samples and within-boot intervals cannot establish long-term equivalence or eliminate boot variance. Raw receipts and unsuccessful runs remain part of the evidence.

## Earlier candidate receipts

[candidate-results.json](candidate-results.json) records the candidate measurements, per-rank trace hashes/counters and raw fixed-grid samples. The real request with 1,048,568 input tokens plus eight generated tokens passed at scale=1; a subsequent stock request retained identical token/logprob bits. Both prefill and decode traces on all four ranks contain no steering operations or kernels at scale=0. The first and last scale=0 load windows differed by less than 0.1% in aggregate TPS. This file preserves the first candidate window. The completed independent references and the final restart are recorded separately in results.json, with source receipt hashes and raw samples in reference-engine-samples.jsonl. These historical API fingerprints compare selected API JSON, not internal hidden-state tensor bytes.

The original 172-patch InputBatch has no `is_dummy` marker. The diagnostic adapter initially rejected its first grid call before model forward; that failure is retained. The adapter now recognizes its actual unregistered `req_<index>_<uuid>` contract, while rejecting registered or real batches. Two deployment regressions cover marker authority and legacy rejection; actual InputBatch makers from all three pinned images passed CPU checks. This is test instrumentation compatibility; the 184 vLLM patches and serving image are unchanged. The official arm is restarted before measurements to clear partial dummy-run state.

## Current-token-only control

On an idle test server, the same extension RPC accepts the following JSON. Apply the same thermal baseline and prefix-cache reset as the client, and serialize the call. This supplements the existing eleven-shape grid; it is not a replacement for real requests.

```json
{"method":"qualification_fixed_grid","args":["{\"shapes\":[{\"num_reqs\":1,\"num_tokens\":1,\"context_len\":0},{\"num_reqs\":4,\"num_tokens\":4,\"context_len\":0},{\"num_reqs\":16,\"num_tokens\":16,\"context_len\":0}],\"replays\":100,\"warmup\":5,\"settle\":1,\"qualification_scale\":0}"],"timeout":600}
```

## Matching nonzero parent load

The retained parent was restarted before its additional scale=1/4 load, with no Torch profiler used in that process. Its four runs use the same first sixteen `servebench.prompts.default_prompts()` entries (digest `ce2d8ed479d6cbf2f01e01de28faae01e311495472b2260b7c8bbf3c901a0961`). On the idle qualification server, prepare those entries as JSONL and use the normal CLI below for C1/C4 and scale 1/4, with the same thermal gate and prefix-cache reset between runs. Record each run separately.

```sh
python3 -m servebench run http://127.0.0.1:8004 --mode closed --concurrency 1 --warmup 15 --duration 90 --batches 6 --max-tokens 256 --context 4096 --seed 2048 --prompts /path/to/first-16-prompts.jsonl --extra-body '{"temperature":0,"seed":2048,"steering_profile":"heretic-1017-attn-v1","steering_scale":1}' --save-requests --output /path/to/new-parent-s1-c1.json
```

## Evidence gate scope

The [retained evidence package](evidence/README.md) and CPU-only checkers expose the existing stock API observations, independent mHC byte hashes, GPU JUnit/logs and sixteen sanitized trace files. Collector HTTP success is now `capture_succeeded`, with `trace_acceptance=not_evaluated`; only the independent parser can report its narrow named-event gate. It does not establish generic copy/launch/SP materialization absence, and a matched official/unloaded trace is unavailable. Thermal admission additionally checks exactly four distinct expected nodes and finite, non-future records aged less than 20 seconds.

Throughput agreement does not establish latency equivalence: C4 restart TTFT mean/p90 differ by +6.30%/+36.15% in 48/49 requests. The high prefix-cache hit rate (~94%) makes this a warm-document load. No claim of all-metric negligible overhead or ±1% boot-level equivalence is accepted. See RESULTS.md for the unresolved boundary; the runtime patch series is unchanged.
