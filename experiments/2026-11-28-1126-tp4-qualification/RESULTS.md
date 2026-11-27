# 1126 Attention qualification on the site TP4 ring

The final RC3 image passed the native release gate, real-checkpoint encoder/parser checks, service functionality, strengthened concurrent capacity and two complete performance cohorts. Compared with the immutable 1022 reference, aggregate output throughput improves 10.24–16.67%, mean first-visible-output latency improves 7.18–9.75%, and whole-request P99 improves 6.59–17.56% on the four matched scale/concurrency cells. A single maximum stream gap regresses; it is retained below.

This result selects the candidate for the owner's preauthorized site promotion. The canonical recipe and frozen lock identify the selected runtime. Physical control adoption and its final probes are journaled under `/srv/ai-cluster/operations/1126-attention-20261128`; their receipts pin the final published deployment commit and remain operational records. No service-control success is inferred from an offline test.

## Exact inputs

- Image: `sha256:3e28171ec18fcb05dac837a12c680c321fa41f9a862e230d3b966668d3792157`, built once natively on Spark01 and distributed identically to all four ranks. Native build: 456 s, minimum MemAvailable 80.8 GiB. Build deployment commit: `8c89f1c0e5f0bbc20b48e77fa3051dc0e2b2f68b`.
- vLLM base `19f2c20ed4d64ae22bd009ceacadd85e2a81e354`, 750 patches, head `9a71ded060b015e40f721a9caf506e43bd1f3fa0`, tree `7c445f265a7bf21b4860735cd972b326968150c3`, patchset SHA-256 `4db4c0bdffb7de3a8d98287f78811ab58f00d75c688e08ccda9bea77660ab2d8`. See the [RC3 lock](../2026-11-28-1126-attention-rc3/upstreams.lock.json).
- Official 1017 checkpoint and identity `b35c43fda128481f17b7ee878df2c2f93fd6db416df451b080cc8d00e166f7d0` are unchanged. Profile `81ca5d96cf78a1c0193ab80071efab43d666b5a57e8633ced71b227031bde1f2` retains alpha 2.5, layers 2–39, operator `attn-writer-project-bf16-rank-order-v1` and request scale 0–4.
- [Payload verification](runs/native-rc3/profile-payload-review.json) proves identical tensor layout and all 778,240 direction bytes versus the accepted 1022 profile. Payload SHA-256 is `f11e3762252448f547183d4b2daaf5b58c2d9023cd5b23365bc026a402c1a8e6`. Only the chat-template binding and conversion provenance changed in the manifest.
- TP4 neighbour ring, GID 5, 64-KiB-page `7.0.0-1019-nvidia-64k` kernel and driver `580.178.04`; requested context 1,048,576, 16 sequences, 8,192 batched tokens, 10.5 GiB KV per rank. The upstream copy-draft mechanism internally reserves 11 additional rows: effective batch 8,203 and graph range 107. The requested graph range stays 96. The legacy adaptive row cost remains 0.035.
- Sampling defaults stay temperature 1.0/top-p 1.0. Image admission stays 999, processor cache 1 GiB and input limit 16,777,216 pixels. Host swappiness, kernel, Docker daemon and fan configuration were not changed in this window.

## Validation and retained unsuccessful attempts

The [final native audit](runs/native-rc3/component-run-5-audit.json) records **195 files, 16,196 passed, 357 skipped, zero failures/errors**, with every selected file reporting tests. The manifest contains 179 active canonical gate files and 16 historical additions. Three GLP-specific files are outside the owner's current scope; explicit deselections and all skip reasons are retained. Critical actual GPU gates have no skips: Attention GPU 29, consumption 32, profile identity 1 and variant-aware graph profiling 37.

The final image also passes 98 real-checkpoint encoder/recorded-output checks (including the 28 real tokenizer cases and 32 replay permutations of the original 91-token failure) plus 113 DSML/token-provenance cases. The earlier extra 25-case token-only execution is retained too; it is not counted again in the 211-case total. See [encoder/replay log](runs/native-rc3/exact-image-encoder-parser-3.log) and [both provenance files](runs/native-rc3/exact-image-parser-provenance-3.log).

The final service functional gate passes 46 requests and 13 cache/cancellation/mixed-scale scenarios, including streaming and nonstreaming tools, tool follow-ups, benign quality cases, dose/logprob changes, return-to-zero equality, two image requests and invalid-scale/profile rejection over both HTTP and SSE. All functional-phase swap-in/out page counters are zero.

Earlier attempts remain in [native RC2 receipts](runs/native-rc2/) and [final-image receipts](runs/native-rc3/):

- The initial test container lacked production's locked-memory/IPC settings; Engram io_uring setup failed. The test-container flags were corrected; no production memory-limit workaround was added.
- RC2 corrects fake configuration setup and assertions over undefined MXFP8 padding. The production Python/C++ arithmetic is unchanged. The initial 97-file bundle passed 7,891 cases but a coverage audit found 98 additional files; its success was not treated as the complete gate.
- The supplemental RC2 run exposed seven graph-profiling fixture failures. RC3 uses the real execution descriptor, correct graph modes and an independent 440-MiB two-variant memory oracle. RC3 differs from RC2 only in test code; the final whole-image gate is nevertheless rerun in full.
- An initial functional harness treated HTTP 200 SSE business-error envelopes as acceptance. Both transports correctly rejected invalid requests. The harness now checks the business error, and the final 46-request run passes.
- The first short capacity attempt returned all C16 responses but observed only ten running. The follow-up C16/1,024 passed; its original 262K/C4/768 batch returned four correct responses but observed only three running. These are failed overlap proofs, not hidden successful capacity results.
- The initial second performance cohort completed four cases, then the inherited freshness check timed out despite 41–44 °C sensors: Spark03's wall clock led the controller by about 80–90 ms. Five regression tests and a four-node live proof validate controller-monotonic query freshness with the unchanged 55 °C/600-second gate. The entire second six-case cohort was repeated. The incomplete attempt remains [here](runs/native-rc3/performance-boot2/), with [clock evidence](runs/native-rc3/thermal-clock-skew-review.json); it contributes no rows to the primary comparison.

## Capacity

Acceptance combines the successful cold C1 rows from the first attempt, the stronger C16 row from the second, and the two sustained C4 rows and stability loop from the third. Every accepted row checks exact input/output usage, zero cached prompt tokens, a complete length finish and the requested observed running peak.

| Input tokens | Concurrency | Output per request | Observed peak | Batch seconds |
|---:|---:|---:|---:|---:|
| 32,768 | 1 | 32 | 1 | 3.34 |
| 131,072 | 1 | 32 | 1 | 12.75 |
| 262,144 | 1 | 32 | 1 | 25.64 |
| 524,288 | 1 | 32 | 1 | 52.42 |
| 1,000,000 | 1 | 32 | 1 | 104.09 |
| 8,192 | 16 | 1,024 | 16 | 30.09 |
| 262,144 | 4 | 4,096 | 4 | 135.08 |
| 1,000,000 | 4 | 16,384 | 4 | 646.40 |

The sustained run finishes 558 mixed-scale stability batches (2,232 arithmetic requests) over the original two-minute loop. The original 1M/C4/3,072 batch was not separately executed after earlier short cases stopped their controllers; the stronger 16,384-output batch supplies the 1M/C4 resident-capacity proof. Capacity durations with changed output lengths are not performance comparisons.

## Matched performance

Reference: the accepted [1022 raw run](../2026-10-24-1022-matched-performance/runs/attempt-2/state/performance/), never rerun. Both complete candidate cohorts use Sonar `d430736ed3af6821bf131b711add441b155a8d68` through the deployment wrapper with the historical `text-chunks` collector. All 144 host source files and the 16-prompt digest match the archived workload. Each cohort retains six cases in the original order, 4K cached documents, output 256, seed 2048, warm-up 15 s, measurement 90 s in six subwindows, cold priming and observed prefix-hit rate above 0.9. All failed, rejected, foreign and approximate-timing counts are zero.

The first complete cohort runs on engine start `boot2` (95.9 s startup); the second complete cohort is `performance-boot2-retry` on independent engine start `boot3` (93.5 s startup). All four container IDs and start timestamps differ. Both starts retain JIT caches; these are not physical host reboots. The temperature freshness correction is outside the timed workload and retains the original physical temperature threshold and query timeout.

| Scale / concurrency | Output tok/s, 1022 → candidate | Change | Mean TTFT ms, old → new | Request P99 s, old → new |
|---|---:|---:|---:|---:|
| 1 / C1 | 70.53 → 81.73 | +15.87% | 305.4 → 277.9 | 4.287 → 3.614 |
| 1 / C4 | 139.53 → 153.82 | +10.24% | 371.8 → 345.1 | 9.025 → 8.430 |
| 4 / C1 | 70.43 → 82.18 | +16.67% | 301.9 → 272.5 | 4.230 → 3.487 |
| 4 / C4 | 137.97 → 152.59 | +10.60% | 385.1 → 347.7 | 9.153 → 8.428 |

| Scale / concurrency | TTFT P99 ms, old → new | TPOT P99 ms, old → new | SSE gap P99 ms, old → new | Maximum SSE gap ms, old → new |
|---|---:|---:|---:|---:|
| 1 / C1 | 331.3 → 308.3 | 15.66 → 13.12 | 41.1 → 36.5 | 66.8 → 63.8 |
| 1 / C4 | 558.8 → 517.6 | 33.99 → 31.78 | 248.5 → 230.7 | 341.6 → 457.2 |
| 4 / C1 | 331.1 → 300.6 | 15.42 → 12.58 | 45.0 → 41.0 | 69.1 → 69.4 |
| 4 / C4 | 574.2 → 555.5 | 34.21 → 31.77 | 246.7 → 230.7 | 341.2 → 312.8 |

SSE gaps are observed stream-chunk intervals, not within-chunk token timestamps. The scale1/C4 maximum is one interval above 400 ms among 35,342 candidate intervals; the reference contains 15,788 intervals and none above 400 ms. Candidate start A's maximum is 300.7 ms, start B's maximum is 457.2 ms. Its P99 improves to 230.7 ms from 248.5 ms. The isolated maximum is retained as a limitation; no cause is inferred from it and no claim of improvement in every metric is made.

| Scale / concurrency | TPS on starts A / B | Mean ± 95% boot-mean CI half-width | Reference / candidate admitted requests |
|---|---:|---:|---:|
| 1 / C1 | 80.89 / 82.57 | 81.73 ± 10.66 | 24 / 57 |
| 1 / C4 | 153.59 / 154.04 | 153.82 ± 2.89 | 144 / 326 |
| 4 / C1 | 82.27 / 82.09 | 82.18 ± 1.13 | 24 / 56 |
| 4 / C4 | 152.60 / 152.58 | 152.59 ± 0.14 | 49 / 107 |

The separate boot-mean interval has only two observations (Student-t, one degree of freedom), so the scale1/C1 interval is wide. Per-run six-subwindow intervals and all per-start metrics remain in the [comparison JSON](runs/native-rc3/performance-comparison.json). Latency percentiles pool actual requests admitted during measured windows; they are not averages of run percentiles. The archived reference is not a contemporaneous paired experiment, and the frozen legacy client versus pinned Sonar remains a comparison limit.

Reproduce the calculation on a DGX checkout with the pinned Sonar client:

```sh
python3 experiments/2026-11-28-1126-tp4-qualification/compare.py \
  --candidate experiments/2026-11-28-1126-tp4-qualification/runs/native-rc3/performance-boot1 \
  --candidate experiments/2026-11-28-1126-tp4-qualification/runs/native-rc3/performance-boot2-retry \
  --output /tmp/1126-attention-comparison.json
```

## Memory, operations and scope

The final RC3 starts have minimum MemAvailable 13.47 GiB and 17.34 GiB across the four nodes. The first records 12 head swap-out pages (0.75 MiB at 64 KiB/page), despite the rounded monitor showing 0.0 GiB growth. The second records zero swap pages in/out. Both complete performance cohorts and the sustained capacity/stability phase have zero swap pages in/out. Minimum MemAvailable during the sustained phase is 20.25 GiB; thermal slowdown and power-cap counters are zero. An earlier RC2 cold preparation start had 0.13 GiB swap growth; this window does not qualify physical cold reboots.

The fixed shutdown coordinator now targets the selected 1126 repository, container and guards. Service-control rendering preserves the existing launcher/service IDs and unrelated registrations. Journaled installation, partial-write/failed-probe rollback, lease/watchdog recovery and fixed power sequencing have offline fault coverage. The final deployment still requires clean published Git on all four nodes, unchanged runtime command/environment/mount verification, reviewed control installation, backend/registry probes and power dry-run. No actual poweroff is part of this acceptance.

The upstream reference recipes remain frozen independently. Twenty-seven imported arms that still named the moving root are bound to the identical frozen upstream 1126 recipe, preserving their files unchanged while the site root advances. The site's release selection explicitly uses its own `site_release` namespace. The prior accepted 1022 commit is preserved on `archive/1022-attention-20261128` at `3e5eb7693f4b83c6232448270615e61e8e5520b2`; its models, profile and recovery image remain available during adoption.

This promotion qualifies the site's TP4 Attention mode. It does not qualify GLP, TP3, a broad model-quality score, a full vision evaluation, a multi-day soak or physical reboot recovery. The two solid-colour API image requests demonstrate the exercised image path only.
