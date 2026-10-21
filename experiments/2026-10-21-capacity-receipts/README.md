# Offline exact-capacity receipt contract

Source base: `3a49389f7ba0e93f05bab1c9a52b4425d4dd7fde`. This adds only a standard-library CPU receipt
validator and synthetic contract tests, alongside the separate
[paired-performance validator](../2026-10-21-paired-performance/README.md).
The intended variable is offline evidence validation, not a runtime change.

This is offline validation only, not a runnable hardware lane. It does not
collect evidence, contact an endpoint, admit a job, or start/stop a service.
No controller job, lifecycle schema, image, workload, resource default, or
existing qualification flow is changed. The existing narrow file-validation
CLI is retained; no generic benchmark CLI or runtime adapter is introduced.

Actual producer health/common-clock evidence is still missing. The current
lifecycle does not emit the complete rank-health/common-clock/swap-movement
input contract below. Daemon quiescence is not GPU drain, swap occupancy growth
is not swap traffic, and configured floors are not measured interval coverage.
A future separately reviewed collection/projection integration must retain the
actual observations and leave unavailable facts INCONCLUSIVE.

Synthetic fixture PASS values test the validator only. They are not a capacity
PASS, performance PASS, production receipt, or permission to run a workload.
There is no real capacity or performance result in this change. The paired
performance validator's overall result remains INCONCLUSIVE pending real
full-map OFF/OFF control and lifecycle/provenance integration.

## Interface and decision boundary

Python: `validate_capacity_receipt(receipt, *, expected, evidence_root)` in `scripts/q03_capacity_receipt.py` returns one JSON-safe result.

Offline CLI:

    python3 -B scripts/q03_capacity_receipt.py --expected EXPECTED.json --receipt RECEIPT.json --evidence-root RETAINED_CAPTURE_DIR

Exit codes: 0 = PASS, 1 = FAIL, 2 = INCONCLUSIVE. PASS means only that one exact cell/arm/state's supplied receipt contract validates. It is not an execution-readiness, full-matrix, quality, performance, true graph-lifetime, or full simultaneous long-context-residency verdict.

The CLI prints JSON to stdout and does not write or repair input files. The caller owns an independently frozen expected manifest; do not derive it from the receipt being tested or rewrite it after observing results. No scheduler or future typed BENCH owner is supplied here.

## Unchanged workload

- `four_64k`: exactly 4 distinct requests, each 64,512 actual fully templated input IDs plus 1,024 actual output IDs.
- `four_256k`: exactly 4 distinct requests, each 261,120 input IDs plus 1,024 output IDs.
- `single_1m`: exactly 1 request, 1,047,552 input IDs plus 1,024 output IDs.
- All cases preserve TP4 ring, maximum context 1,048,576, maxseq 16, batch 8,192, KV 11,274,289,152 bytes per rank, DSpark draft budget 5, and startup 5 GiB / steady 3 GiB guards.
- The native revision, image digest and runtime-manifest identity are frozen by the module's `FROZEN` mapping. Each actual request, capture, scrape metadata and rank observation must equal the independently frozen state/model/engine/boot/resource binding.
- All four rank memory/health and keep-stopped cleanup observations are required, even for single1m. Swap movement is reported, with no invented swap threshold.

For c4, a correctly labeled native running=4 sample must lie strictly inside every one of the four exact request intervals: max(sent_ns) < sample_ns < min(end_ns). Disjoint/partial intervals and samples on an ambiguous send/end boundary cannot establish admission. A broad earliest-send/latest-end envelope is not sufficient. The expected and every actual binding must identify the same retained common-clock source. The result separately records `observed_concurrent_admission`. This means only simultaneous admission at an observed instant. `full_concurrent_long_context_residency` always remains UNKNOWN: the input format has no request-phase/computed-token/cache-lifetime proof. Maxseq16, client overlap, or an observed running count never establishes 4×1M or 16×1M.

## Retained sources and honesty limits

A source reference is `{ "path": "relative/path", "sha256": "actual lowercase SHA256 of retained bytes" }`. Every source is read under `evidence_root`, UTF-8 decoded, and hash-checked before use. Absolute paths and symlink/path traversal escapes are rejected. Result `verified_source_sha256` contains only successfully verified file bytes. Missing hashes are not filled in and unknown files are not given invented identity.

Source SHA verification proves only local integrity, not truth or producer authenticity. Capture documents must be retained actual observations from an independently admitted executor and trusted collection chain. This verifier cannot distinguish a deliberately fabricated full source bundle from real observations. Synthetic fixtures are never production evidence. An unbound expected manifest, a summary success boolean, absent capture, missing actual IDs, missing labels, missing rank, incomplete guard interval or counter reset is INCONCLUSIVE, never a pass. Contradictory actual evidence, including short/changed IDs, duplicates, observed errors, preemption, changed binding/resources or nonquiescent drain, is FAIL. Original files and unfavorable observations are retained by the caller.

The rank and scrape JSON documents below are an offline input schema, not an implemented collector or a claim that existing CF4 or servebench already exports these facts. A future reviewed adapter may project supported actual captures into this schema while preserving their provenance, but must not fill absent observations with asserted zero/true values. Until it has the facts and review, production validation remains unavailable/inconclusive.

## Input schema

EXPECTED.json:

- `schema`: `q03.capacity.expected.v1`.
- `case`: one exact name above.
- `clock_source`: source reference to retained actual capture-origin JSON with nonempty `clock_id`, nonempty `source_id`, `domain` monotonic and `unit` ns. This identifies an independently verified common capture clock, not a guessed clock mapping.
- The required `binding.clock` object must exactly equal that retained clock source, including any additional provenance fields. Actual request, scrape and rank bindings must preserve it.
- `binding`: one complete object, copied unchanged into actual source captures only after it has been independently verified there. Required members:
  - `native_commit`, `image_digest`, `runtime_manifest_sha256`: the module's fixed `FROZEN` identities.
  - `engine_id`, `boot_id`, `model_name`, `endpoint`: nonempty actual identities, no placeholders.
  - `model_assets_sha256`, `tokenizer_assets_sha256`, `request_semantics_sha256`: actual frozen manifest/semantic identities. These identity digests are comparison keys, not a claim that this verifier has read the underlying model files.
  - `resources`: exactly the `RESOURCES` mapping exported by the module.
  - `state`: `mode` is off or dual; `steering` is unloaded, loaded_zero or active_1. Unloaded has `profile`, `profile_sha256`, `scale` all null. Loaded states use profile `heretic-1017-attn-v1`, its frozen actual artifact hash, and scale 0 or 1 respectively. A numeric 0.0/1.0 freeze is also permitted; booleans are not. Actual bindings must preserve the freeze's exact JSON structure and number types. Extra binding members are compared too, not discarded.
- `metric_labels`: exactly `{model_name: actual served name, engine: actual native engine label}`. The native engine label is not assumed to be the unique engine/boot ID; both identities are bound separately.
- `corpus`: source reference to frozen JSONL, one `{request_id, prompt_token_ids}` object per distinct prompt. These are the complete model-ready input IDs, not text-only counts or nominal prompt sizes.

RECEIPT.json:

- `schema`: `q03.capacity.receipt.v1`.
- `case`: same exact case.
- `requests`: source reference to actual JSONL responses. Exactly one record per frozen ID, including failures, no filtering. Each record has `request_id`, complete `binding`, `prompt_token_ids`, `output_token_ids`, integer `prompt_tokens`/`completion_tokens`, integer `status_code`, `finish_reason`, `error`, boolean `truncated`, and integer `sent_ns`/`end_ns` from a shared monotonic time domain. Successful records have status 200, finish_reason length, error null and truncated false. Integer fields and token IDs reject booleans and floating-point values.
- `counters`: source reference to a JSON capture with `binding` and `samples`. Every sample has its own verified `binding`, integer `observed_ns`, and `source` reference to unmodified Prometheus text. Preserve before, intermediate and after samples in order, without deleting resets or unfavorable samples. All request intervals must fit within the before/after fence; boundary running/waiting are both zero. Sample times and request/rank times must already be in the same verified monotonic domain, not guessed host-clock alignment.
- `ranks`: exactly four source references, one actual capture for each unique rank 0–3 and unique node. Each capture has `rank`, `node_id`, `binding`, and the sections below.

Rank capture sections:

- `startup`: integer `observed_ns`, integer `guard_floor_bytes` = 5 GiB, integer `free_bytes` >= 5 GiB, boolean `passed` true. This observation precedes all requests.
- `steady`: integer `start_ns`/`end_ns` enclosing all requests, boolean `complete` true, integer `guard_floor_bytes` = 3 GiB, integer `min_free_bytes` >= 3 GiB. Whole-interval integer `guard_halts`, `oom_events`, `allocation_retries`, `driver_errors`, `errors`, `engine_restarts` must be zero. Integer nonnegative `swap_in_bytes`/`swap_out_bytes` are retained observations. A configured memory floor without measured interval coverage is not enough.
- `drain`: integer `observed_ns` after requests, final counter scrape and steady capture end, integer `daemon_active`, `engine_processes`, `gpu_processes`, `pending_epochs`, `guard_processes` all zero; boolean `stopped`, `quiescent`, `protected_inputs_unchanged` all true; `policy` keep-stopped; `owned_ids` empty. A daemon-only receipt is not all-four GPU/stopped proof.

### Counter semantics

The parser preserves native labels and uses these existing metrics:

- `vllm:request_success_total`, including `finished_reason`.
- `vllm:num_preemptions_total`.
- `vllm:num_requests_running`.
- `vllm:num_requests_waiting`.

All relevant values must use ASCII Prometheus decimal/scientific syntax and be finite nonnegative integers. Underscores, Unicode digits and hexadecimal/P-exponent spellings are rejected before numeric conversion; integer-valued scientific notation remains supported. Native endpoint exposition without explicit per-series timestamps is supported; explicit metric timestamps require an independently bound clock conversion that this schema does not provide, so they remain INCONCLUSIVE rather than masquerading as a fresh observation. Every sample requires model_name/engine labels matching the frozen native series. Foreign/unlabeled engine captures, ambiguous gauge/preemption series, absent metrics, new/disappeared series and decreases in monotone counters do not become zero. Deltas are evaluated between every retained adjacent sample, not only the endpoints. A stable nonzero historical error counter is not a new failure; a positive non-length/error interval increment is a failure. New/disappeared error series with no usable baseline stay unknown, never a false clean interval. The length-finish delta must equal exactly 4 or 1, and preemption deltas must be zero. JSON duplicate keys, nonfinite values and Prometheus duplicate label/series ambiguity are rejected.

This cannot recover an omitted scrape or prove no unobserved reset merely from two endpoints. The same-engine/boot capture and whole-interval no-restart/guard observation are necessary parts of its evidence contract. It does not synthesize an error counter that native vLLM lacks: OOM/allocation/driver errors require the actual all-rank health evidence.

## Focused verification

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_q03_capacity_receipt.py -v

Tests create deterministic synthetic source bytes in temporary directories, hash those bytes, and use exact full-size token arrays for all three cases. Negative fixtures cover missing/duplicate/unexpected IDs; token mismatch and truncation; strict integer/boolean handling; state/image/engine/resource mismatch; missing/unlabeled/reset/disappearing/error counters; missing occupancy; all-four rank/guard/drain evidence; unknown versus contradictory observations; source integrity; path boundaries; and CLI exit decisions. No GPU, live endpoint, device or full project suite is involved.

## Intake identity and verification scope

The module and test bytes are unchanged from the reviewed CPU contract:

- `scripts/q03_capacity_receipt.py` SHA-256:
  `2c3266bfba948e33fff69e381e15b7938f87cd3a8c42b4317b98df3f74252b28`.
- `tests/test_q03_capacity_receipt.py` SHA-256:
  `41b6eb70d0e4911361f36025885d86980b3626b68a6361b8832ec49fda573985`.

The focused suite has 79 tests. All receipts and captures generated by it are
synthetic, temporary CPU inputs; they do not establish producer authenticity.
No private site captures, raw runtime receipts, or source-comparison reports
are included. Full repository, native patch/build and hardware qualification
are separate checks, not implied by these focused tests.
