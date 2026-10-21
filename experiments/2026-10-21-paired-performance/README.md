# Offline paired-performance receipt contract

Source base: `3a49389f7ba0e93f05bab1c9a52b4425d4dd7fde`. The intended variable is a pure offline
validator for one preregistered performance cell, using the existing unchanged
`servebench.stats.describe`/`percentiles` and `stream_chat` timing fields.
The [exact-capacity validator](../2026-10-21-capacity-receipts/README.md) is a
separate offline contract, not a hardware admission path.

This is offline validation only, not a runner, generic CLI, runtime framework,
serving capability, or runnable hardware lane. No controller job, lifecycle
schema, image, workload default, resource setting, or qualification workflow
is changed. No GPU, device, endpoint, or service is contacted.

Overall performance status remains INCONCLUSIVE pending real full-map OFF/OFF
control, lifecycle, and immutable provenance integration. Actual producer
health/common-clock evidence is still missing. Synthetic numeric NONINFERIOR
values are calculator checks, not a performance PASS or capacity PASS. No real
capacity, performance, acceleration, or scientific acceptance result is
provided by these fixtures or this source change.

## Smallest interface

    from servebench.paired_performance import evaluate
    result = evaluate(preregistration, receipts, artifacts)

All three arguments are in-memory objects. artifacts maps lowercase SHA-256 hex
to the original bytes. No paths are opened, modules dynamically loaded, subprocesses
created, or network calls made. A malformed/missing artifact returns INCONCLUSIVE.
The function never edits its inputs or chooses replacement attempts.

- statistical_status says only whether every required numeric confidence bound
  meets this cell's margins on the supplied validated record set.
- status deliberately remains INCONCLUSIVE in this standalone version. Two
  explicit prerequisites remain: CONTROL_ADMISSION_UNVERIFIED and
  EXTERNAL_PROVENANCE_REVIEW_REQUIRED. Do not remove these by rewriting a flag.
- The next reviewed integration must invoke the existing lean/full-map and
  lifecycle validators on actual underlying evidence. Its control input must
  cover complete OFF/OFF input/output-ID maps, exact state/corpus/workload and
  attempt lineage. The current lean validator's unloaded 2048-input/c1/16-output
  scope cannot be relabeled as every formal cell's control admission.
- Hashes prove supplied byte integrity, not truth of observer declarations,
  physical boot independence, successful all-rank cleanup, preregistration before
  collection, or absence of withheld attempts. The external immutable owner/daemon
  history must establish those facts. A self-written stable=true or ready=true
  receipt is not accepted scientific proof.

## Statistical rule and definitions

Four blocks only, in order OFF/DUAL, DUAL/OFF, DUAL/OFF, OFF/DUAL. Each arm has
its own fresh four-rank boot. For each metric and block, d = 100*(DUAL/OFF - 1).
Apply stats.describe to exactly four d values: sample SD, df=3, t95=3.182,
mean +/- 3.182*SD/sqrt(4). Do not pool requests across boots or use a Welch test.

- Output throughput: lower95 >= -3%.
- TTFT and TPOT request p50/p99: upper95 <= +5%.
- Cold prefill: mean of each request's actual input-token count / TTFT has
  lower95 >= -3%; mean request TTFT has upper95 <= +5%. No requested-size proxy.
- TTFT = first output-chunk time - request sent time.
- TPOT = (request end - first output-chunk time)/(actual completion tokens - 1),
  matching existing servebench.report. It includes client stream completion
  timing, is one mean per request, and is NOT individual-token ITL p99 or GPU time.
- Percentiles use existing nearest-rank stats.percentiles. Each arm/boot needs at
  least 100 timed requests for p99. Insufficient support blocks p99 decisions,
  while retaining descriptive pairs and any valid p50/throughput results.
- Sustained throughput counts exact cumulative output-event increments in
  (window_start, window_end] divided by the full registered duration. Latencies
  use requests sent in [window_start, window_end), including their complete drain.
- Burst throughput is total actual outputs/(latest request end - earliest send),
  not the first-output-to-last-output denominator. A frozen finite burst-repeat
  inventory is collapsed into one boot observation; the whole collection wall
  interval includes any intervening gaps. Freeze that collection policy first.
- Subwindows and burst repeats never increase boot n. No trimming, retry-to-green,
  dropping unfavorable pairs, or "same"/equivalence verdict. A confidence bound
  not meeting its margin is INCONCLUSIVE, including a clearly unfavorable result.
- No acceleration, whole-matrix, quality, capacity, original188, or routed-Q03-use
  conclusion follows from an isolated cell or this CPU calculator.

## Required input contract

All JSON objects are ordinary JSON without duplicate keys or NaN/Infinity.
canonical_sha256 hashes UTF-8 json.dumps(sort_keys=True, separators=(',', ':'),
ensure_ascii=False, allow_nan=False). Original byte hashes are SHA-256(raw bytes),
not canonical hashes. Explicit booleans do not substitute for numeric counts.

### 1. Frozen preregistration

schema = q03-paired-performance/1; nonempty cell_id; kind = sustained, burst,
or cold_prefill; blocks = four unique ordered IDs. These are metadata formats,
not new live-run selections. Preserve the independently approved final matrix.

identity is the full common object copied exactly into each run and admission:
- model, exact service endpoint and metrics_endpoint strings.
- metric_labels={model_name, engine}, preserving native labels. Native engine is
  an engine index such as "0", not an instance ID; it may repeat across boots.
- checkpoint_sha256 binding the actual model/config/weight manifest, not a tag.
- tokenizer_sha256 binding assets, revisions, encoding and package versions.
- template_sha256 binding the actual fully applied chat template/special tokens.
- runtime_sha256 and image_sha256 binding the admitted source/runtime/image.
- resources_sha256 binding topology, model limit, maxseq, batch, KV allocation,
  DSpark, guard/fan/cooling settings and every fixed resource.
- Additional frozen fields are retained and must match exactly. These hashes
  are commitments to independently verified identity evidence, not evidence
  that this offline module inspected mounted model files or an OCI image.

state contains name (unloaded/loaded_zero/active), profile_name, profile_sha256,
vector_sha256, scale_fp32_hex, and complete extra_body. FP32 is eight hexadecimal
digits in network-byte order, e.g. 00000000 or 3f800000. Unloaded has null profile
and vector and zero scale, with no steering request fields. Loaded states require
profile/vector identity; loaded_zero is zero, active nonzero, all finite. The
explicit steering_profile/steering_scale in extra_body must match name/FP32 bits.
This narrow receipt form retains the existing direct extra_body fields, not a new CLI.
Any additional steering fields at top level or inside vllm_xargs must be frozen
exactly by location and match the same profile/FP32 state. Contradictory duplicate
locations are rejected rather than choosing precedence. Unrelated nested fields
are not blanket-rejected. Boolean scales do not equal numeric scales.

arm_configs maps OFF and DUAL to four ordered entries, each with rank (0..3),
worker_config_sha256 and runner_config_sha256. Supply the original JSON bytes of
both independently frozen complete 58-field config snapshots in artifacts.
All actual rank config hashes are checked against these snapshots. A digest of
an incomplete dictionary is rejected. No approximation by a few chosen fields.

corpus contains raw_sha256 and parsed_sha256. The raw bytes are frozen JSONL;
each row has a unique string id and a complete prompt_token_ids integer vector.
The parsed digest hashes the entire ordered row list, including additional
fields, not an intersection or seed. Every vector has exactly input_tokens.
The final collector must bind the actual server-echoed token IDs to each record's
input_ids_sha256; copying a requested digest is not a server echo.

workload is matched in full, including extra fields, and minimally contains:
mode, concurrency, input_tokens, output_tokens, warmup_s, duration_s, subwindows,
temperature, seed, request_template_sha256, cache_policy. Freeze arrivals/order,
cache salting, all sampling settings and workload-specific settings here too.
Sustained mode is closed/poisson, positive duration, outputs >1. Finite burst/cold
mode is burst and also requires bursts_per_boot and requests_per_boot, with
requests_per_boot = concurrency * bursts_per_boot. Cold outputs exactly one.
Supply request-template JSON bytes; model, temperature, seed, max_tokens,
min_tokens, ignore_eos=true and exact steering fields are checked. State fields
cannot override frozen workload fields. Neither this template nor its hash is
proof that the server applied the requested state.

client_source_sha256 identifies supplied original timing-collector source bytes.
Those bytes are hashed, never imported or executed. PR8 stream_chat source is
SHA-256 e6b649459307cf0fb79031b53b4ab48e6c1c37aa2f40343f4fe919768e00133a.

### 2. Receipts and declared-attempt ledger

receipts contains run_sha256 (eight distinct raw run hashes in execution order),
ledger_sha256, and control_sha256. The ledger contains preregistration_sha256
and attempts, exactly [{slot: 0..7, run_sha256: corresponding hash,
outcome: 'completed'}]. Extra, failed, missing, duplicated or reordered declared
attempts block evaluation; retain the original ledger rather than edit it.
A fresh reviewed registration is needed after contamination, not cherry-picking
replacement pairs. This consistency check cannot prove omitted-attempt absence.

control_sha256 points to an external provenance object with the registration
hash, status='stable', and a nonempty evidence_sha256 list of available original
bytes. Unstable/missing control blocks even numeric evaluation. A stable string
and available bytes still do NOT clear CONTROL_ADMISSION_UNVERIFIED. A later
adapter must validate the actual full maps and lineage rather than trust it.

### 3. One raw run per boot

schema=q03-performance-run/1; slot=0..7; block_id; arm; preregistration_sha256;
exact identity/state/workload/corpus; boot; timing; records; start; window;
boundaries; counter_samples; rejected=0.

boot has id, engine_id, window_id, attempt_id, started_at_ns (positive UTC Unix-nanosecond integer),
and admission_sha256. All eight boot and engine IDs and 32 rank-container IDs
must be distinct; window/attempt pairs are unique and start times follow the frozen arm order.
Here engine_id identifies the engine instance through existing admitted boot/container
lineage, NOT a native metric label or a new runtime identity API. Repeated client
clock domains are allowed and do not count as additional boots.

The admission JSON schema is q03-performance-admission/1 with boot_id,
engine_id, window_id, attempt_id, started_at_ns, arm, exact identity/state, and four ordered ranks.
Each rank carries container_id and the exact frozen worker/runner config hashes.
ready, steady_guards and cleanup must explicitly be true, but remain external
admission declarations until the real lifecycle receipt validator verifies the
underlying all-rank ready/guard/drain/keep-stopped chain. They are not a substitute
for the actual cf4/native/runtime/terminal evidence.

timing records clock='time.monotonic', nonempty clock_id,
source='servebench.client.stream_chat', source_sha256 and units='seconds'.
All run/request/counter times must use that same clock domain. No GPU-clock claim.

records retain original stream_chat fields: sent, first, end, events (time,
cumulative output count), prompt_tokens, completion_tokens, finish_reason,
token_times and error. They also retain original index/slot/prompt_id and append
input_ids_sha256. Finite collections additionally record burst=0..N-1. Indexes
are unique and contiguous from zero; every preregistered client/burst slot and
every frozen prompt must appear, including measured-window corpus coverage.
Errors, stopped requests, incomplete lengths, non-length finishes, interpolated
counts, nonfinite/missing times and nonmonotonic/missing events block the run.
Only exact actual counts and complete successful records enter ratios, without
replacing a failed run. Input digests must equal the actual frozen ID vectors.

Sustained window=[start+warmup_s, start+warmup_s+duration_s]; boundaries contain
subwindows+1 evenly spaced measured boundaries. Observed spans and all subwindow
widths must be positive; exact recorded endpoint arithmetic is checked without an
absolute tolerance that could admit collapsed/tiny invalid windows. No request may begin beyond
window end; complete drain is retained. Finite collections use null window and
empty boundaries and retain every fixed burst repetition.

counter_samples has the complete chronological before/after fences and, for
sustained cells, one raw scrape at every registered boundary. Each entry has
at, engine_id, raw_sha256 and binding; inner samples also identify boundary.
The binding contains the exact metrics endpoint, window_id, attempt_id, boot_id,
admission_sha256, clock_id, preregistration_sha256, slot and measured window.
The admission digest binds all four container/config identities. Each binding
is checked against the run and must come from the actual capture envelope.
A stale endpoint, copied old boot/attempt, or wrong clock/window is inconclusive.
These fields bind declared provenance; they do not authenticate a collector or
prove its underlying lifecycle receipts. Fences cover
every request from before first send through after last drain. Raw Prometheus
bytes retain model_name/engine and every other series label; labels/series must
stay identical and counters nondecreasing across ALL samples, not just endpoints.
Required counters are request_success_total, generation_tokens_total and
num_preemptions_total, under the vllm: prefix. Cold prefill additionally requires
prefix_cache_hits_total with zero whole-run delta. Whole-fence request/output
deltas must equal all actual client records and preemptions must remain zero.
Native labels retain only Prometheus string escapes (backslash, quote, newline),
not JSON-only escapes. Counter numbers require a native finite decimal/scientific
lexeme, nonnegative integral value, and finite native float64 range. A supplied
optional timestamp must be a signed 64-bit decimal integer. Counts are parsed
as exact Decimal values, normalized without float conversion, and differenced/
summed with enough decimal precision for all endpoint digits and carry. Thus
underscores, fractional values, zero-underflow and >2^53 rounding cannot pass.
Missing, reset, unlabeled, foreign-engine, duplicate or nonfinite metrics never
become zero. These tests do not prove absence of a reset between unsampled
scrapes; actual engine/daemon lifecycle provenance remains required.

## Focused CPU verification

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_q03_paired_performance.py -v

The 42-test synthetic suite covers incomplete identity, state/FP32 fields,
raw hash tampering, complete config snapshots, exact input/output counts,
negative pairs, df/t calculation, full-wall burst denominator, 100-request
tail support, cold-input/TTFT, cache hits, missing/reset/nonfinite metrics,
reused boots, declared retries, fixed burst inventories, subwindow
pseudoreplication, and unverified control admission.

The `fixture()` function in `tests/test_q03_paired_performance.py` is the
complete synthetic input example. Its tiny input lengths, dummy identities,
fake source bytes, and fake configuration fields are intentionally not field
evidence. No standalone synthetic result is included as an acceptance receipt.
Full repository, native patch/build, and hardware qualification are separate
checks, not implied by these focused tests.

## Intake identity

The module and test bytes are unchanged from the reviewed CPU contract:

- `servebench/paired_performance.py` SHA-256:
  `b867f9a4c464324bfff01eddf30fa154cdb5eb3e32ff9718318df808520b0663`.
- `tests/test_q03_paired_performance.py` SHA-256:
  `8ce18607d6f915f97a66e140be5219f0cf3bf92191bce58a119f7cc40988b3e4`.

The existing `servebench/stats.py` is reused without copying or changing it:
SHA-256 `62e42ae9b37eb009b8787aaa69a91412de74a20dea38bb43893088af7639df7c`.
`servebench/__init__.py` is unchanged too. Only the new module, focused test,
and neutral contract documentation are included; there are no private site
captures, raw runtime receipts, or source-comparison reports.
