# Finite PR8 quality response capture

Status: source/CPU-tested capture adapter, now dispatched only by the separately
admitted schema-7 owned lifecycle in `q03-quality-owned-lifecycle.md`, not execution-frozen. No model
quality, actual tokenization, applied steering, candidate-code result, or field
readiness is established. Current schema-6 P1 behavior is unchanged.

## Reuse and fixed scope

This additive module is based on deployment PR30 local commit
`864944b8ddd98421cee1dc629f2c99bbbe5d5b8c`. `q03_quality_capture.run_client`
accepts the existing source-only PR8 `main` already loaded by
`q03_http_client.FrozenToolbox`. It verifies the exact 14-file PR8 inventory
(head `c6342a1b6c5e5a98108905a71452a51d6d3a4a87`). It does not launch a server,
create a scheduler/framework, modify PR8 sources, or expose a runnable CLI.

The single fixed command is PR8 `model`, all 60 exact reviewed pilot prompts,
concurrency 1, max generated tokens 2048, temperature 0, seed 2048, and
`--no-score`. No limit, fixed length, EOS suppression, template/thinking override,
scoring corpus, tool schema, or arbitrary caller argv is accepted. The native
thinking default remains in force; 2048 is total generation, not a final-answer
allowance. Native default/decode/parser identity still requires live binding.

The prompt JSONL hash is
`8cdcf6143d80d4776c052166a9764d36d8b5020038ad24118e352f3493cf4ca0`;
the R2 material checksum-file hash is
`c3db307e1f145026c33252c36cda819ffefda1d189400d6e06d316454f3bf580`.
Only exact `{id,text}` rows go into PR8. Gold, reference code, rubric and metadata
never enter the model prompt. Category/language and actual complete frozen input
IDs are supplied as a generation-only ordered case index, not inferred here.

The fixed selections remain `r6x188` / `native197-off` and `unloaded`, `loaded0`
through `loaded4`. Unloaded omits steering fields. Loaded0 sends top-level profile
`heretic-1017-attn-v1` and integer scale 0; it is not unloaded. Loaded1–4 use the
same top-level fields. Requested, observed-loaded and applied state stay separate.

## Capture and failure semantics

The adapter replaces only PR8's `_post` seam during this fixed call. PR8's real
generation loop, serialization, finite row order and ordinary result continue.
The semantic-body hash excludes **only cache_salt**. It does not drop any explicit
request setting. Endpoint is retained separately. Pairing must also match the
retained `decode_identity_sha256` and `tokenizer_identity_sha256`; identical body
hashes do not establish equal effective native defaults.

The configured transport must target one exact owned `http://127.0.0.1:PORT`
origin. Default transport disables proxies and redirects. Every admitted request
has its exact PR8 UTF-8 wire bytes, payload, salt, hashes, deadline-derived timeout
and prompt mapping recorded before opening transport.

Raw body bytes are written before parsing, including non-2xx, malformed and
partial responses. Strict JSON rejects duplicate keys, nonfinite constants,
nonfinite exponent conversion and invalid UTF-8. The entire decoded object,
including unexpected fields and all choices, is retained before schema checks.
Float rendering in this convenience projection is not a lossless numeric
representation: the raw bytes/hash are authoritative. Nothing strips, extracts,
normalizes, concatenates or executes answer/reasoning/tool content.

`max_response_bytes` and `max_header_bytes` are mandatory future capsule inputs,
not newly selected execution limits. Response overflow retains at most the bound
plus one detection byte, marks `overflow=true`, `complete=false`, and fails the
lane. It is not a shortened valid answer. Remaining unread bytes are explicitly
unavailable, not claimed to be preserved. Relevant headers preserve duplicates;
header overflow retains a bounded canonical prefix and observed-size/hash,
marks the overflow, and fails the lane. No overflow becomes quality evidence.

Root `prompt_token_ids` and choice `token_ids` must exist, contain real integers
(not booleans), and match full frozen input IDs and full usage counts. Output IDs
are not reconstructed from text. Missing IDs, inconsistent usage, model/choice
identity mismatch, malformed HTTP JSON, transport failure, evidence write failure
or exceeded deadline latch admission: the queued PR8 tasks cannot open another
model request. Trailing metrics fetches are suppressed after failure. PR8's
successful-subset digest is **not** the 60-slot denominator.

A valid HTTP response with invalid answer JSON, a wrong answer, refusal, missing,
null, non-string or empty final content is not retried or relabeled as transport
failure. A length finish is `TRUNCATED`; unexpected tool-call finishes are retained
and flagged, never invoked. All other captured terminal choices are `COMPLETED`,
which means only capture completion, **not** semantic correctness, eligibility or
PASS. Every row and the whole sidecar remain `semantic_scoring=NOT_EVALUATED`.
An offline evaluator consumes only `response.decoded.choices[0].message.content`
under its separately frozen parser/scoring contract, never reasoning as fallback.
A semantically correct final answer can coexist with a retained length finish;
no scorer may erase truncation or manufacture execution validity.

## Artifact contract: r6x-quality-capture/1

One new exclusive `quality-capture/` directory beside the ordinary PR8 output:

- `identity.json`: material/prompt hashes, lane/window/attempt/plan identity,
  requested arm/state, runtime/tokenizer/decode references, observed-loaded state,
  and separate applied-state evidence. These references are retained claims,
  not independently validated by this capture-only module.
- `case-index.json`: immutable full 0..59 index, stable ID/category/language,
  prompt UTF-8 hash, expected complete input IDs/count/hash, initial NOT_RUN state.
- `prompts.jsonl`: byte-identical generation-only material snapshot.
- `request-NNN.json`: durable pre-transport record, including exact request bytes
  in base64 and SHA-256, body, endpoint, semantic-body hash, salt and timeout.
- `response-NNN.bin`: exact retained HTTP body bytes. Partial/overflow flags and
  completeness are in its record; no missing suffix is invented.
- `record-NNN.json`: one terminal primary row. `response` contains status,
  relevant headers, raw path/count/hash, completeness, overflow, decoded full
  object and parse error. `diagnostics` contains transport/input/output/usage,
  final-content presence/type/empty, truncation and tool-finish observations.
- `quality-capture.json`: all 60 rows, outcome counts (`COMPLETED`, `TRUNCATED`,
  `ERROR`, `NOT_RUN`), first capture error, ordinary result hash and transport
  bounds. Execution status is always `CAPTURE_ONLY_NOT_EXECUTION_VALIDATED`.

No output is overwritten, retried, silently resumed or better-answer selected.
An abrupt process death may leave only the immutable identity/case index and
pre-transport/raw artifacts. They are incomplete evidence, never a successful
summary; the future supervisor reconciliation must classify interrupted and
unstarted slots. An I/O failure is latched, but cannot guarantee a new record
on a full or unavailable filesystem. Existing artifacts must be retained.

## Owned lifecycle integration and remaining evidence

The schema-7 integration is documented in `q03-quality-owned-lifecycle.md`. It
adds the source-only dispatch, exact sixty-case corpus/index binding, complete
live-token preflight, controller-owned runtime context and raw/ordinary receipt
revalidation. Existing hard supervision and positive drain are reused, not
reimplemented. No field execution or semantic eligibility follows from that code.

Actual corpus site binding, numeric capsule bounds/admission, four-rank runtime
and terminal drain, native parser/default configuration and per-request applied
state remain separate field/review dependencies. The current native final-answer
extraction remains explicitly UNBOUND. A data evaluator and approved isolated
candidate evaluator remain separate from capture; no code candidates run here.

All metrics now use the same proxy/redirect-disabled bounded transport as model
and live-token requests. Missing/nonfinite/negative idle gauges or transport errors
fail closed, and raw metrics are retained. Identity and summary carry identical
transport bounds including the per-request seconds supplied by the owned lane.
The actual HTTPResponse `read1` path preserves truncated chunk bytes that the
older aggregate `read` path could lose inside nested IncompleteRead exceptions.

## Focused validation only

Run only this file with stdlib unittest (no install needed):

`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_q03_quality_capture.py -v`

Twenty-three self-contained fake-transport tests cover full/raw/wire retention,
reasoning/tools/usage, natural stop/length, missing/null/type preservation,
unloaded versus loaded0 and all six state fields, malformed/duplicate/nonfinite
JSON, non-2xx, partial/IncompleteRead, overflow, missing token evidence, identity,
usage/Content-Length mismatch, deadlines, duplicates, persistence failure and
immutable artifacts. These are synthetic CPU protocol checks, not model evidence.

The twenty-fourth test runs by default from the committed R2 data subset and
14 exact PR8 source blobs extracted from the locally available pinned Git object
that repository CI already fetches. `Q03_TEST_TOOLBOX` and
`Q03_TEST_QUALITY_MATERIAL` are optional explicit reviewer overrides, not CI
requirements. It verifies all pinned source bytes, then runs unchanged PR8
`model --no-score` using exclusively synthetic transport and token bindings.
One case captures all 60 slots; another injects HTTP JSON failure at slot 1 and
proves exactly two transport calls, then 58 NOT_RUN slots. Metrics use bounded synthetic responses;
scoring is replaced by an assertion that it must never be called. No network,
model, GPU, device, candidate or trusted-reference execution occurs.

This is not the repository full suite, `bin/spark doctor`, a build check, a
runtime release gate, a field run or publication approval.
