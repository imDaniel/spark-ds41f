# PR 60 RC3 on the four-Spark TP4 ring

The RC3 Attention maintenance payload passed the four-node correctness and capacity gates. It qualifies as a release candidate for this site's TP4 ring with the small streaming and decode costs quantified below. Across four matched cells, pooled throughput was 0.27–0.46% lower than the archived 1126 release. Throughput, mean time to first visible token and end-to-end P99 showed no directional change under the qualification rule. The site resumed the accepted 1126 release after testing. [Matched comparison](runs/performance-comparison.json) · [Restored service](runs/restored-service.json)

## Candidate tested

Spark01 built the image once in 452 seconds. All four ranks ran image `sha256:5d6e6aa4747baa66fb5f691758087df9a4c482a2914d093fe3d24acea5a31394` from deployment commit `67b23d5f0abb6eeeb99b92432b17d3092e192643`. Both performance starts used the same four GB10 GPUs, kernel `7.0.0-1019-nvidia-64k` and driver `580.178.04`. [Four-node identity and continuation records](runs/operations.json)

The 757-patch vLLM series produced head `f54bee45a79182ca0de85aeddff6697e7659f626` and tree `50402286dbe6d2f5975211809be8ecada25c8f69`. The official 1017 checkpoint used the rebound Attention profile `c9c7663dade8685f5d7932b65a7476a58647981b4cfc3c59f008c6dcf080b3f6`, preserving all 778,240 direction bytes. [Component identity](../2026-11-28-1126-attention-fixes-rc3/source.json) · [Profile identity](../2026-11-28-1126-attention-fixes-rc3/profile-rebind.json)

## Correctness on hardware

The native release suite ran 195 complete test files on a GB10 in the candidate image. **16,232 passed, 357 skipped, zero failures or errors.** The audit matched historical passing cases to their JUnit identities. Supplemental checks passed for both real CUDA capture and replay variants, seven warmup cases, 87 encoder cases (37 with the actual 1017 tokenizer) and 104 parser cases. These supplemental executions are reported separately. [Native audit procedure](native.py)

| Repair | Observation |
| --- | --- |
| Tool argument types and history | Two rounds of three parallel tool calls preserved JSON-looking strings, leading zeros, numbers, booleans, nested values and literal control text. Ordered and shuffled tool results, each tested through streaming and non-streaming HTTP, produced the same 921 prompt token IDs. |
| KV prefix isolation | One 32,768-token prompt exercised nine identities combining scales 0, 1 and 4 with the caller salt omitted or set to A or B. Every first request reused zero tokens and every repeat reused 32,512. The scheduler showed four simultaneous requests with mixed identities, each producing 512 tokens. After cancellation the queue drained and subsequent requests reproduced their expected token IDs and cache hits. |
| CUDA Graph warmup | Real-device tests exercised ordinary and profiling captures, replay, per-variant warmup and release of temporary profiling pools at both tested sizes. All 39 graph profiling cases passed. Both measured TP4 engine starts completed graph initialization. |

The historical service suite passed all 46 requests, including 12 tool calls, two tool follow-ups, 20 quality fixtures, four steering dose checks, two image requests and six expected rejections. Its separate suite passed 13 cache and cancellation scenarios. Scale 0 and the default selection were functional controls. The timed cohorts used explicit scales 1 and 4. [Targeted HTTP cases](targeted.py) · [Service qualification procedure](qualify.py)

## Capacity and resource use

Single-request prefill passed at 32,768, 131,072, 262,144, 524,288 and 1,000,000 input tokens, each with 32 output tokens. The scheduler reached all requested concurrency levels in the sustained cases.

| Concurrent requests | Input tokens per request | Output tokens per request | Peak simultaneously running |
| ---: | ---: | ---: | ---: |
| 16 | 8,192 | 1,024 | 16 |
| 4 | 262,144 | 4,096 | 4 |
| 4 | 1,000,000 | 16,384 | 4 |

The stability stage completed 545 batches. The capacity run reached 79 °C GPU temperature with at least 20.03 GiB of host memory available on every rank. The two performance runs reached 63 °C, with minimum available memory of 20.69 and 18.31 GiB respectively. The three stages recorded zero swap I/O, thermal slowdown, power-cap increments or new GPU faults. During the initial cold engine start, Spark01 swapped in 2.75 MiB and out 19.125 MiB. Power-cap counters advanced during engine starts. The lowest observed startup memory headroom was 13.07 GiB.

## First maintenance window

The first window completed the native, functional, capacity and first performance runs. Its controller reserved time for restoration and declined to start the second performance run. The restore attempt stopped when Docker reported that the already-removed component-test container did not exist. Commit `05d20947b` fixed the recovery check by querying Docker's exact container inventory. A retry under the same lease restored production commit `63b0c0d31e4378888951295325179211d00cbde6`. Both gateway aliases answered and the backend success counter rose from one to three. The next window revalidated the candidate, source, four GPUs, tokenizer and prior results before starting the second performance cohort. [Recovery incident and continuation evidence](runs/operations.json)

## Matched performance against the accepted 1126 release

The two independent RC3 engine starts replayed all six timed cases apiece against the two recorded release starts. All 24 source runs use the same Sonar revision `d430736ed3af6821bf131b711add441b155a8d68`, 4,096 input and 256 output tokens, concurrency, source documents, prompt digests and client stream collector. Each measured case used an explicit Attention scale of 1 or 4, 15 seconds of warmup and 90 seconds of measurement. All 12 candidate cases finished without failed, rejected or foreign requests, or approximate token timing. The four historical cells admitted 546 measured requests and the candidate admitted 542. Individual requests determine pooled latency percentiles. Throughput uses the historical six-subwindow calculation. [Immutable reference and source locations](../../manifests/benchmarks/2026-11-28-homelab-1126-attention-tp4.json)

Cells show production → candidate. Percentages describe the change from the recorded production result.

| Scale | Concurrency | Aggregate output tok/s | Mean TTFT, ms | End-to-end P99, s | Admitted requests |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 1 | 81.73 → 81.36 (-0.45%) | 277.9 → 278.8 (+0.30%) | 3.614 → 3.627 (+0.37%) | 57 → 57 |
| 1 | 4 | 153.82 → 153.11 (-0.46%) | 345.1 → 346.4 (+0.39%) | 8.430 → 8.535 (+1.24%) | 326 → 324 |
| 4 | 1 | 82.18 → 81.89 (-0.34%) | 272.5 → 274.2 (+0.64%) | 3.487 → 3.481 (-0.18%) | 56 → 56 |
| 4 | 4 | 152.59 → 152.18 (-0.27%) | 347.7 → 355.2 (+2.14%) | 8.428 → 8.631 (+2.41%) | 107 → 105 |

| Scale | Concurrency | TTFT P99, ms | TPOT median, ms/token | TPOT P99, ms/token | Stream gap P99, ms | Longest stream gap, ms |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 1 | 308.3 → 301.1 | 11.094 → 11.187 | 13.123 → 13.176 | 36.52 → 37.61 | 63.827 → 64.278 |
| 1 | 4 | 517.6 → 520.2 | 24.544 → 24.773 | 31.775 → 31.984 | 230.66 → 231.63 | 457.168 → 302.808 |
| 4 | 1 | 300.6 → 302.0 | 11.161 → 11.186 | 12.578 → 12.547 | 41.02 → 41.87 | 69.376 → 63.455 |
| 4 | 4 | 555.5 → 575.2 | 24.089 → 24.530 | 31.772 → 32.540 | 230.69 → 232.16 | 312.786 → 329.258 |

Two secondary metrics met the comparator's criterion for a regression. At scale 4 and concurrency 4, median time per output token rose from 24.089 to 24.530 ms (+1.83%). Each candidate start returned a higher median than either baseline start. At scale 1 and concurrency 1, the longest visible stream interval rose from 63.827 to 64.278 ms (+0.71%). Stream gaps measure client-visible SSE chunks. A chunk can contain several tokens.

Scale 4 at concurrency 4 also had the largest increase in pooled mean TTFT (+7.45 ms) and end-to-end P99 (+0.203 s). Pooled TTFT P90 rose from 357.7 to 428.7 ms. The individual P90s were 354.3 and 508.5 ms on 1126, and 500.0 and 359.8 ms on RC3. A fixed direction would require all four cross-start contrasts to exceed each version's repeat variation and the 95% Welch interval for the two-start mean difference to exclude zero. The archived cohorts and two independent candidate starts support a site-specific release judgment with limited precision on tail changes. The comparison JSON retains every startup, all four contrasts, intervals and individual run identifiers. [Comparison and uncertainty](runs/performance-comparison.json)

## Recovery, evidence and reproduction

Ingress drain began at 13:58:25 JST on November 28. Both production gateway probes completed in the first window by 16:57:38 JST. The second drain began at 16:58:38 JST, ingress reopened by 17:16:54 JST and the second window finalized at 17:17:08 JST. The combined upper bound from both drain requests to confirmed reopened ingress was 3 h 17 min 30 s, including time spent draining. Both gateway aliases returned HTTP 200 with fresh challenges in each window and the backend success count rose from 1 to 3 after each pair. At 17:17:42 JST an independent probe verified four production ranks, active memory guards, the original checkout, image and profile, open ingress for both window IDs, a released hold, and zero candidate ranks. The later recovery and audit commits changed qualification tooling only. The RC3 runtime and profile used by both timed starts were identical. [Operations](runs/operations.json) · [Four-rank restoration](runs/restored-service.json)

The [qualification archive](runs/qualification.tar.gz) has SHA-256 `f39534af841fc9a6a7cc9516fbbee8226208c51fc104481c180aa01b0abdf99c`. It contains raw Sonar measurements, prompt identities, functional and capacity receipts, stage telemetry, per-file native results and recovery receipts from both windows. Its per-window inventories record the original digests. Native JUnit shard 010 has a separate [sanitized case ledger](runs/native-chunk010-cases.json.gz) covering 678 passing and 118 skipped cases. The original shard is retained on Spark01 and identified by SHA-256 in the archive. Cleanup of the first window's disposable pytest/compiler scratch and an unused verification checkout returned 1,854,369,792 bytes to the filesystem after the archive was verified.

To reproduce the comparison in a checkout with the pinned Sonar client, run the following from the repository root. The scratch directory holds the extracted receipts and the recalculated JSON.

```sh
experiment=experiments/2026-11-28-pr60-tp4
receipts=$(mktemp -d)
tar -xzf "$experiment/runs/qualification.tar.gz" -C "$receipts"
python3 "$experiment/compare.py" \
  --candidate "$receipts/1d6d2564-08f8-470e-a9b5-a76d82b58ab7-performance-1" \
  --candidate "$receipts/9025c649-3f78-4949-a3b0-62c44787337e-performance-2" \
  --output "$receipts/recalculated.json"
```
