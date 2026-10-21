# First finite capacity producer, source only

Base: `4d3f478e7` (PR21 normal qualification/CPU preparation). This independent
branch does not change the live helper, a retained capsule, an image, a model,
steering semantics, fan policy, memory guards or the normal configuration. No
field run, HTTP request, GPU/device access, model build or capacity PASS was
performed. Unit test data is explicitly synthetic and is not a usable corpus.

## One case, not a matrix

The new optional HTTP binding uses `schema_version: 5` and a `capacity` member.
Schemas 1 and 2 retain their previous meanings. Schema 3 is reserved for the
separate quality matrix and schema 4 for the separate performance screen; this
branch does not compose or implement them.

Only `capacity_64k_c4` is supported: exactly four distinct frozen fully templated
64,512-input-token requests, each generating exactly 1,024 output token IDs,
with concurrency four. Total input/output counts are 258,048 / 4,096. The
normal server stays TP4 ring, context 1,048,576, max sequences 16, batch 8,192
and KV 11,274,289,152 bytes (10.5 GiB) per rank. Existing 5-GiB startup and
3-GiB steady guards are unchanged. There is no 256K/1M successor, warmup,
quality/performance comparison, queue or automatic retry/continuation.

An admitted capsule selects one existing OFF/DUAL and off/loaded_zero/active
state through the existing typed selection. The request preserves temperature
0, seed 0, thinking false, no silent clipping, fresh per-request cache salts,
fixed length and the existing loaded steering profile/scale. A terminal attempt
prevents another capacity attempt within that capsule, including after failure.

## Genuine missing inputs

No genuine capacity corpus is available in this deliverable. The current
`scripts/q03_native_corpus_export.py` is the separate 16-by-2K exporter; it
cannot produce this case. Do not truncate, repeat, concatenate or synthesize
IDs from that corpus, or relabel a synthetic test fixture as a native export.
An independently reviewed real native tokenization/export of four actual
texts is required before preparation can succeed. Implementing that exporter
is a missing integration contract, not silently added here.

`scripts/q03_capacity.py --export ABSOLUTE_EXPORT --output-directory FRESH_DIR`
is offline preparation only. It accepts exactly these export keys:
`schema_version` (1), `workload` (`capacity_64k_c4`), `tokenizer_mode`
(`deepseek_v41`), `identity`, `artifacts`, `request`, and `rows`.
Identity/artifact/request meanings are the unchanged normal-qualification
contract in `q03_qualification.py`. Each row has ordered integer `id` 0–3,
actual `text`, and actual 64,512 `prompt_ids`, including BOS and all template
control tokens. Complete mounted model/tokenizer/native-source provenance is
required and verified; preparation never loads a tokenizer or derives IDs.

Preparation preserves the original export bytes in `native-export.json`, raw
`prompts.jsonl`, the exact `servebench.prompts.digest`, and a canonical corpus
with SHA256-bound references. Those protected inputs are rehashed at admission
and at the client boundary. Output locations cannot overlap their sources.
Preparation authenticates bytes and declared provenance, not the truth of the
exporter's work: the real response must subsequently echo every frozen input
ID exactly. CPU shape checks are never reported as native tokenization proof.

The HTTP `capacity` object has exactly `workload`, `corpus` (prepared path/SHA)
and `metric_labels` (`model_name: deepseek-v4.1-flash` plus the independently
frozen native `engine` index string). The index is not a boot ID. It must be
established for the exact admitted endpoint before freezing; no default engine
or engine UUID is invented. Actual stage-1/owner prerequisites and all six
bound, drained owner-matrix cells remain required. There is no field permission
or runnable live manifest in this source deliverable.

## Owned execution and evidence

The existing source-only HTTP client dispatches the capacity workload through
its four-file pinned source closure. The controller pins `q03_capacity.py`,
and the independent watchdog/recovery inventory includes it. Existing old
capsules must not be modified or resumed with these different source hashes.
The unchanged normal renderer and HTTP lifecycle retain all-rank startup,
readiness, exact container/spec bindings and steady guards. The actual client
process and descendants stay in the existing bounded remote epoch. Existing
client/job/outer deadlines and four-rank quiescence/restoration are mandatory;
a completed or timed-out HTTP call does not prove the server has drained.

The fixed frozen c6342 servebench `model` generation path sends the four
requests once with `--no-score`, concurrency four and fixed 1,024 output.
Unlike its burst path, this route returns full input and output token IDs,
which are required for the exact-capacity check. It is not a performance
measurement or proof of four simultaneously admitted native sequences.

Before and after generation the producer retains actual raw `/metrics` bytes,
HTTP status/error, exact URL, model/engine labels, window/attempt/spec/plan
identity and local monotonic-nanosecond brackets. No redirects or proxy are
used for these raw scrapes. Response size is bounded; missing/errored data is
not substituted with zeros. A before-fence without all required counters or
known idle running/waiting gauges prevents generation. There is no model or
engine lookup from the first observed scrape.

Required traffic/preemption samples use the strict native numeric/label parser
derived from PR19 `523d10d40c6752c7b2c20145493b8785c4074183`,
`servebench/paired_performance.py`. This is a small source derivation, not an
import of or execution authority from the offline validator. The only parser derivation normalizes allowed outer whitespace before metric
detection while preserving raw bytes, so it cannot hide a foreign series.
Label sets stay intact; foreign engine/model series, duplicates, invalid numeric spelling,
nonfinite/fractional counts, invalid timestamps, missing/changed series and
counter resets fail. The exact native197 metrics logger,
`vllm/v1/metrics/loggers.py` at source
`154159dc3e38ca8b2321c01a153a1b2ccaf8663b` (SHA256
`cebd5c439625db19cdc47634395719421a7fc10407f73926789d583616920d61`),
uses model/engine labels and initializes the required gauges/counters and all
request finish-reason series before traffic; this is source evidence, not
proof of the future live endpoint. Decimal arithmetic retains integral precision above 2^53
and large baselines. Native metric timestamps are syntax-checked only, never
used as a fabricated common clock. Exact total deltas must be four requests,
258,048 input tokens, 4,096 output tokens and zero preemptions, with idle final
fences. Failure preserves available generation and raw before/after files,
records the error and does not retry or advance.

Retained files are `capacity-before.prom`, `capacity-after.prom`,
`capacity-generation.json`, `capacity.json` and the existing
`client-receipt.json`. Controller collection revalidates hashes and all four
request IDs/full input and output vectors, and the existing terminal receipt
must still prove lifecycle drain. The controller's internal `result: passed`
means that the finite producer completed under the HTTP protocol only. The
capacity receipt always says `capacity_status: INCONCLUSIVE` and
`overall_status: INCONCLUSIVE`, even when `producer_status` is
`FOUR_REQUESTS_COMPLETED`. An offline receipt parser cannot establish that the
actual client, endpoint, boot or cleanup ran.

## Evidence not produced here

Native c4 admission, full residency, allocator retries/OOM health, driver
errors, all-rank memory minima, swap movement and a common clock remain
`UNKNOWN`. Four client threads and two idle fences prove none of these.
The separate PR22 allocator snapshot RPC is not installed, invoked or assumed
present; using it later requires explicit source/composition pins and an owned
transport. A snapshot alone still would not prove full health or residency.
Do not turn a missing field into zero, an unknown into PASS, or this one case
into 16-by-1M capacity or full-capacity acceptance. No new thresholds, fan
policy, observer framework, arbitrary argv or unrequested features are added.

## Source verification

Focused CPU tests cover exact corpus shape/provenance, all request states,
full counts, raw retention, missing/foreign/duplicate/reset/nonfinite metrics,
source inventory and isolated `-I -B` fixed-client dispatch. Existing HTTP
fault tests retain partial-start, lost authority, deadline, descendant seal
and all-four drain coverage. These fixtures are not field evidence.

Commands (run sequentially, not a full suite):

```
python -m unittest discover -s tests -p 'test_q03_capacity.py' -v
python -m unittest discover -s tests -p 'test_q03_qualification.py' -v
python -m unittest discover -s tests -p 'test_q03_http*.py' -v
python -m unittest discover -s tests -p 'test_q03_native_corpus_export.py' -v
PYTHONPATH=scripts python -m unittest discover -s tests -p 'test_q03_git_toolchain.py' -v
git diff --check
```

Results: 25 capacity tests, 16 qualification tests, 42 HTTP tests, 23 native
exporter tests and 18 Git/source-inventory tests passed (124 focused tests).
The Git group initially needed its tracked validation-selection fixture
materialized in this sparse checkout and the scripts import path supplied for
its standalone run, then passed without changing the tested implementation.

`bin/spark doctor` was attempted but is blocked by absent site-private
`config/nodes-ring4.local.json`. No site config is fabricated to get a green
check. No upstream patch series, build input, runtime image or workload assets
were changed; no component rebuild is claimed.
