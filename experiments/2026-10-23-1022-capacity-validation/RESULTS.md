# 1022 attention steering release qualification

**All gates in the agreed bounded qualification are satisfied.** The candidate
is ready for an owner release decision. PR #56 remains draft/unmerged and the
original four-node r6x service has been restored. Production promotion still
requires the owner's separate decision and a new immutable baseline.

## Qualified identity

| Input | Identity |
| --- | --- |
| Final runtime-test repository commit | `997307faa59471478f32ba67b2c2bf2f02db2900` |
| Image | `sha256:14d5bb2c49aad6ad5b09c93beb70379c4088c128162e24bb418d593e5e7853f5` |
| vLLM head | `b70fe11a88b34e0987e0e8c1b6796869be7e9f71` |
| vLLM tree | `dc13e8dd17ff4415f0330084c02bb684ad021420` |
| Profile SHA256 | `e06b8cb60f3f382157ef6632a93efdd90185beafa2251a8c29575f09c45cd45b` |
| Model identity | `b35c43fda128481f17b7ee878df2c2f93fd6db416df451b080cc8d00e166f7d0` |
| Configuration | `../2026-10-23-1022-parser-byte-alignment/cluster.json` |

All four ranks used the same image. Source, patchset and native-library audits,
fresh patch replay, doctor and build check passed. The unchanged configuration
is TP4, maximum context 1,048,576, maximum 16 sequences, 10.5 GiB KV per rank,
with the existing attention directions, layers 2–39 and alpha 2.5.

## Functional and regression evidence

- RC2 GPU component receipt: 73 files, 1,995 passed, zero failures/errors and
  158 documented skips. Critical steering/encoder/parser cases did not skip.
  Reuse is guarded by the scanner-only source delta and unchanged native inputs.
  Two deliberate CUDA fault cases retain their earlier same-runtime passing
  receipts; the fault injections were not repeated.
- Exact RC3 image: 4,379 parser-engine tests passed without skips, plus replay
  of the actual failed API token sequence. The 201-patch source stack replays
  to the recorded head/tree. All 131 relevant test files are classified:
  74 gate files and 57 reasoned exclusions, with no unclassified files.
- Original control-spelling regression: 25 parameterized cases, with 19 failures
  on both r6x and the original 1022 image before repair. Four additional UTF-8
  byte-token cases expose the second scanner defect (two fail before repair).
  These tests expose pre-existing parser defects; attention arithmetic is unchanged.
- RC3 actual API: 12 complete/stream tool calls at scales 1 and 4 across named,
  required and auto choices; two tool followups; 20 quality smoke responses;
  13 cache checks. All passed. Returning to scale zero reproduces the initial
  output hashes. The original failing RC2 request and repaired RC3 request have
  exactly identical generated token IDs; only parsing changes the nested value.
- Cold single-request input lengths 32,768 / 131,072 / 262,144 / 524,288 /
  1,000,000 passed with exact prompt/output usage and zero cache hits.
- Existing real-tokenizer coverage (all 28 dependent encoder cases), profile
  payload/binding checks and earlier U/E/r6x hardware comparisons remain preserved.
  Their inputs did not change. No new comparative throughput claim is made.

The functional and C1 results are pinned in [attempt 4](runs/attempt-4/README.md).
The same-image continuation revalidated their hashes, image/profile/configuration
identity and assertions before opening its window.

## Concurrency and short stability

Every candidate generation explicitly supplied the profile and scale. Capacity
requests used independent cold cache salts and required exact output lengths.

| Input tokens per request | Concurrency | Output tokens per request | Observed running | Batch seconds | All streams producing before first completion |
| --- | --- | --- | --- | --- | --- |
| 8,192 | 16 | 256 | 16 | 23.876 | 1.284 seconds |
| 262,144 | 4 | 768 | 4 | 173.702 | 1.714 seconds |
| 1,000,000 | 4 | 3,072 | 4 | 881.492 | 11.717 seconds |

All 24 capacity requests completed successfully, with zero cached tokens. The
overlap is `earliest completion - latest first-visible output`, so these batches
demonstrated overlapping output as well as scheduler admission. All 2,131 metric
samples were obtained without collection errors. First-visible timing and these
batch durations are operational evidence, not a comparative performance benchmark.

The final mixed scale 0/1/4/0 C4 arithmetic loop completed 472 batches / 1,888
requests in 120.020 seconds. Every response finished normally with `42`.
The original literal tool request also passed after the new window's startup.

The preceding 32-output-token C16 screen is retained as a failed capacity proof:
all requests succeeded but only five ran together. Increasing output duration
allowed the remaining admissions while preserving the original concurrency gate.
No runtime change was made between these two RC3 windows.

## Recovery and operating observations

The final window, `3ead01df-5424-4f2b-9560-c68abc34a7f8`, took **25.144 minutes**
from opening through verified restoration. Its [raw results and recovery
receipts](runs/attempt-5/README.md) include full responses, metric samples,
telemetry, executor events and per-command logs. All unfavorable earlier attempts
remain preserved.

Minimum observed available RAM was 20.77 GiB on Spark01 and 22.42–22.62 GiB on
Spark02–04. Peak observed GPU temperatures were 78 / 80 / 79 / 82°C respectively.
No swap growth or new GPU fault was observed. No Docker restart or driver reset
was performed. These are sampled observations.

The executor restored r6x commit `223a3bb3…`, image `8ad0da03…` and profile
`d7f0b997…` on all four nodes. Configuration, node map, image, source, mounts,
command and profile identities match the pre-window inventory. Private and
gateway probes passed, gateway configuration is unchanged, ingress is open and
the hold is absent. The independent watchdog was stopped after verified recovery;
both temporary gateway-key copies were removed.

The tested commit passed all 16 GitHub CI jobs in
[run 39970909461](https://github.com/imDaniel/spark-ds41f/actions/runs/39970909461).
The new continuation's nine offline guards passed, including rejection of altered
receipts, changed runtime identity, missing responses and insufficient admitted
concurrency. Subsequent publication adds evidence and documentation only.

## Scope of the release decision

This is the shortened qualification requested by the owner: functional/parser,
steering/cache, capacity and two-minute stability gates, reusing accepted earlier
comparisons and unchanged component evidence. Broad model-quality evaluation,
long-duration soak and a fresh statistical performance comparison were outside
this window. The five quality prompts are smoke coverage. Promotion should use
the exact candidate identities above; none of these receipts promotes it by itself.
