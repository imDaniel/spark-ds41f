# Q03 finite qualification execution, first lean slice

The separate [Stage 1b schema 3 successor](q03-quality-matrix.md) now prepares the
remaining finite 16-token matrix. This document preserves schema 2 and its
original scope; it does not authorize adding work to an existing capsule.

This successor is based on controller `c6290c5ca519d68e0b3acd3ac31c58741e0e5481`
(tree `5ca21096397f6f1aef96490d515c1218af51a74d`), with the CI-only parent
`321b342667e37ae851a133d0e3dc7ea5233685a8` (private canonical test temporary
root, no runtime/helper change). It prepares the next formal
quality/capacity/performance work now; it does not replace or widen a running
owner/HTTP capsule. No field corpus, benchmark result, speedup or quality pass is
supplied by this source change. Publication and field execution are separate.

## The implemented slice

HTTP binding schema 1 remains the original fixed 16-output-token **smoke** and
its OFF/OFF/DUAL sequence. It is not a numerical quality gate. Its request argv,
serving profile and original plan interpretation are unchanged.

A **new immutable** HTTP capsule may select schema 2 and exactly one closed
qualification value:

```json
{
  "schema_version": 2,
  "qualification": {
    "workload": "lean_tokens_c1",
    "corpus": {"path": "<actual absolute corpus.json>", "sha256": "<actual SHA-256>"}
  }
}
```

This fragment is not a runnable manifest. Retain all other existing HTTP fields:
owner_inputs, the actual six-cell owner_matrix, exact PR8 toolbox, API port,
startup_seconds and client_seconds. No arbitrary argv, environment, corpus name,
concurrency or output length can be supplied. Schema 2 does **not** require a
separate schema-1 smoke result, nor any later confirmatory/performance result.
Existing HTTP owner-matrix admission is unchanged, not extended here.

The only supported workload is unloaded, 16 distinct frozen fully templated
2,048-input-token prompts, concurrency 1, fixed 16 outputs, temperature 0, seed 0,
thinking false, generation only. Output IDs are **observations**, not preknown
answers. `servebench model --no-score` from the already pinned PR8
`c6342a1b6c5e5a98108905a71452a51d6d3a4a87` records the IDs. The fixed request has
ignore_eos=true, min_tokens=max_tokens=16, return_token_ids=true and a fresh
PR8 cache_salt UUID per request. No score API or new benchmark framework is used.

- First OFF attempt: off_control1 then off_control2 in the same independently
  owned service boot, 32 requests. Two separate OFF boots are not required.
- Next DUAL attempt: the same 16 prompts, 16 requests. No caller selectors.
- Total: 48 requests, 98,304 input IDs and 768 recorded output IDs, if complete.
- OFF disagreement yields INCONCLUSIVE_UNSTABLE_CONTROL and prevents expansion
  to DUAL. Stable OFF and mismatching DUAL yields TOKEN_ID_MISMATCH; complete
  agreement yields TOKEN_ID_MATCH only for this narrow cell. The first differing
  prompt/output offset and both IDs are retained. No retries erase a failure.

The existing finite client bound remains 1–600 seconds for the **whole client**
(two sequential controls on OFF), startup at most 3,600 seconds, attempt at most
7,200 seconds, and outer lease exactly 10,800 seconds. Pick and freeze actual
bounds during admission; this change neither picks a new site duration nor
increases an existing bound. A per-socket timeout is not the process bound:
the existing supervisor fences/cancels the client and every descendant. A timed
out server request still requires candidate removal and all-four GPU drain.

## Real corpus preparation, not a synthetic ready corpus

No actual 16 × 2,048 corpus is available in this source packet. Synthetic IDs
exist only in CPU tests and must never be supplied as field input. The CPU
`prepare` command normalizes and validates an actual-tokenizer export; it does
**not** tokenize, fetch model assets, infer provenance, tune text to approximate
lengths, or establish that an unverified identity string is true.

Before admission, produce the export from the already frozen native197 runtime
and registered read-only `/models` checkpoint. Do not substitute AutoTokenizer,
a newer model, a host stdlib filler or a text-only count. Bind and review actual:

- Model repository and full revision, config SHA-256 and existing weight-manifest
  SHA-256. This does not authorize another model source or weight download.
- Tokenizer full revision and named asset SHA-256 inventory, native encoding
  source SHA-256, effective chat-template identity and special-token SHA-256,
  actual transformers/tokenizers versions from a retained native-runtime record.
- UTF-8 text, ordered integer IDs 0–15, all 2,048 actual fully templated input
  token IDs for each prompt, and exact generation/template controls.

The export also carries actual file pins, not hash-shaped claims alone.
Preparation and admission read/hash config.json and each tokenizer asset under
the declared model_root, the existing weights manifest, both native Python
sources and the runtime-metadata record. Admission requires model_root to equal
the registered read-only /models mount; a fixed read-only probe checks config
and tokenizer bytes on all four ranks. It neither scans all weights nor treats
a weight-manifest digest as proof that every weight file was newly rehashed.
The manifest's established provenance must be retained and reviewed. No model
file or its owner/group mode is changed to satisfy these checks.

The native CPU entrypoint is
`vllm.tokenizers.deepseek_v41.DeepseekV41Tokenizer.from_pretrained` with the
registered local model directory and `local_files_only=True`. Its wrapper uses
native `encode_messages`, not the checkpoint's generic Jinja tokenizer alone.
The following is an **unexecuted exporter recipe**, to be embedded in the
reviewed native environment with real asset identities, not a controller job:

```python
from vllm.tokenizers.deepseek_v41 import DeepseekV41Tokenizer

tokenizer = DeepseekV41Tokenizer.from_pretrained(REGISTERED_MODEL_PATH,
                                                local_files_only=True)
messages = [{"role": "user", "content": frozen_text}]
rendered = tokenizer.apply_chat_template(messages, thinking=False, tokenize=False)
# Match native HTTP generation's rendered-text backend path at fixed1M/16 output.
ids = tokenizer(rendered, add_special_tokens=False, truncation=True,
                max_length=1048561)["input_ids"]
# For this short fixed-length cell, verify rather than assume wrapper equivalence.
assert ids == tokenizer.apply_chat_template(messages, thinking=False, tokenize=True)
assert len(ids) == 2048
```

Use one original string user message, no tools, no system message, no
encode/decode round trip and no truncation of a too-long prompt. Retain rendered
text and raw source/asset/version evidence alongside the export. The 2,048 check
must pass without clipping; the comparison above catches a silently clipped
longer prompt. The exporter is preparation only and must not generate answers.

Verified against native source head
`154159dc3e38ca8b2321c01a153a1b2ccaf8663b`, tree
`ae15047b376c7792062a2607c4ef1dfbc78df18a`:

- `vllm/tokenizers/deepseek_v41.py`:
  `8450587010152846a2e3f6269c661b464615b7e46c312f4e38a397c44218a6e5`.
- `vllm/tokenizers/deepseek_v41_encoding.py`:
  `8ee7a8010c3ed257a0f1caa4dfbeb3ec68df0b03e36fcd20dad044ec7cf114fe`.
- `vllm/entrypoints/serve/tokenize/protocol.py`:
  `0bb4ddf2c0b1412e88c9d853e2ef7163f62665df0b8bbef86df5bdbdd9a76e44`.
- `vllm/entrypoints/serve/tokenize/serving.py`:
  `fb9fd00cc71df5c052f420a959e39434ce5fd3c84556275eb9dc25d43ff59750`.

The identity's encoding_source_sha256 is exactly the native encoder hash above.
chat_template_sha256 is SHA-256 of canonical JSON mapping the two exact source
filenames deepseek_v41.py and deepseek_v41_encoding.py to those hashes:
`6498f5e05580ceb03d956862a9ed5fe790062dcfc2f907d846289c7a40c10cbf`.
This binds the active Python template, not an ignored generic Jinja file.
special_tokens_sha256 names the bytes of the actual inventoried tokenizer
asset selected by artifacts.special_tokens_file, including any non-special
fields in that file; it is not a hash of an unspecified extracted subset.

The existing `/tokenize` API also accepts messages and
chat_template_kwargs={thinking:false}, calls the same native chat renderer, and
returns the complete tokens list plus count. It has no generation output reserve
or truncation, so compare IDs, not just counts. This documents a verified
source route; it is **not** permission to start a server, add requests to the
current smoke capsule, or use an unbound endpoint.

Export shape (placeholders are deliberately non-runnable):

```json
{
  "schema_version": 1,
  "tokenizer_mode": "deepseek_v41",
  "identity": {
    "model_repository": "<actual>", "model_revision": "<full40hex>",
    "model_config_sha256": "<full64hex>", "weights_manifest_sha256": "<full64hex>",
    "tokenizer_revision": "<full40hex>",
    "tokenizer_assets_sha256": {"<actual relative asset>": "<full64hex>"},
    "encoding_source_sha256": "<full64hex>", "chat_template_sha256": "<full64hex>",
    "special_tokens_sha256": "<full64hex>",
    "transformers_version": "<actual>", "tokenizers_version": "<actual>"
  },
  "artifacts": {
    "model_root": "<actual registered read-only model mount>",
    "weights_manifest": {"path": "<actual retained manifest>", "sha256": "<full64hex>"},
    "runtime_metadata": {"path": "<actual native runtime record>", "sha256": "<full64hex>"},
    "native_sources": {
      "deepseek_v41.py": {"path": "<actual frozen source>", "sha256": "<exact wrapper hash above>"},
      "deepseek_v41_encoding.py": {"path": "<actual frozen source>", "sha256": "<exact encoder hash above>"}
    },
    "special_tokens_file": "<relative tokenizer asset containing special tokens>"
  },
  "request": {"temperature": 0, "seed": 0, "chat_template_kwargs": {"thinking": false}},
  "rows": [{"id": 0, "text": "<actual UTF-8 text>", "prompt_ids": ["<2048 actual integer IDs>"]}]
}
```

The pinned runtime_metadata JSON has exactly image, source_head,
runtime_manifest_sha256, model_root, model_repository, model_revision,
tokenizer_revision, transformers_version and tokenizers_version. Its native
image/head/runtime must equal the existing admitted native197 pins; the other
values must equal the export. This is retained measured provenance from the
actual producer, not permission to fabricate values. Preparation does not start
a runtime to discover them. Absent or mismatching file evidence blocks admission.

On an owner-controlled, non-shared-writable path, the existing Python interpreter
may run this source-only command after the real export is frozen:

```sh
python3 -B scripts/q03_qualification.py prepare \
  --tokenizer-export "$ACTUAL_EXPORT" --output-directory "$NEW_CORPUS_DIRECTORY"
```

The new directory must not exist. The command emits the corpus path/hash pin;
retains raw export SHA-256, parsed exact-ID rows, raw JSONL SHA-256 and exact PR8
`servebench.prompts.digest`; and exclusively writes corpus.json/prompts.jsonl.
Rows and token vectors must each be distinct and ordered. Invalid, missing,
boolean or inexact IDs fail closed. The raw input export remains an external
retained source artifact; preparation cannot replace asset/provenance review.

## Admission, lifecycle and receipts

Use the existing run-session / plan-attempt / control-resume forms, with no new
CLI switches. The manifest's only job remains http_service. Only the new capsule
uses schema 2; all older controller/helper paths and immutable plans remain
available for their own recovery. The new source inventory includes
q03_qualification.py, and the existing all-four code staging/admission rule still
applies. Do not replace any active capsule, helper or authority file.

Serving stays TP4 ring / context1,048,576 / seq16 / batch8192 /
KV11,274,289,152 bytes per rank / DSpark5. Existing native197 image/runtime,
steering absence, exact-ID all4 inert create, startup5GiB/steady3GiB guards,
readiness comparisons, client epoch seal, all-rank raw-log-before-remove and
positive daemon/GPU drain are unchanged. Original completion remains
keep-stopped, never START. No rebuild, extra fan policy or new telemetry
framework is introduced by this generation-only slice.

The capsule/descriptor/plan/spec bind the native state and corpus. Corpus/JSONL
are immutable controller/head inputs, disjoint from every writable result/cache
root. The client uses the same frozen PR8 closure and a source-only, hash-checked
three-module qualification import closure, including under Python -I. The controller also compiles the new qualification
module only from its exact literal-pinned source bytes, never ignored .pyc. Raw
servebench outputs are fsynced and retained as off_control1.json,
off_control2.json or dual.json. lean.json binds their hashes, actual CLI exits,
complete results, state identity, corpus, toolbox and plan. client-receipt.json
binds lean.json and actual client completion. A saved JSON with failed CLI exit
cannot pass.

Every returned prompt-ID vector must equal its independently frozen 2,048 IDs;
every completion must have 16 non-boolean integer token IDs and finish=length.
No intersections, ignored failures, duplicate IDs or truncated token maps count.
http/result.json separates execution completion from qualification_status.
`result=passed` and passed_gates=[http_service] alone are **not** a token-match
claim; unstable complete controls remain an inconclusive scientific result.

After both attempts have identity-bound terminal.json, outcome=passed and
positive all-four quiescence, the offline comparison is:

```sh
python3 -B scripts/q03_qualification.py compare \
  --off-attempt "$ACTUAL_OFF_ATTEMPT_DIRECTORY" \
  --dual-attempt "$ACTUAL_DUAL_ATTEMPT_DIRECTORY"
```

It reuses the production Session reader, verify_outer (without taking a hold),
verify_history and HTTP verify_plan_identity. It rechecks actual
capsule.sha256, capsule/descriptor/admitted/plan/client hashes, terminal
command-plan and quiescence attempt/phase identities, retained history hashes, raw result files, arm
semantics, complete IDs and all4 drain. Different corpus/native/state/toolbox or
unproven drain is rejected, not described as equal. Keep all retained inputs
available; do not copy just summaries or delete preparation files.

Operational infrastructure retries retain the existing held-window semantics.
They do not make the scientific record green: any retained failed attempt makes
comparison status INCONCLUSIVE_PRIOR_FAILED_ATTEMPTS, with explicit failure
lineage (attempt ID, failure reason, passed gates and terminal hash). The selected
complete pair's token status is reported separately. A later matching pair cannot
erase an earlier unstable control, mismatch or incomparable failure. No
infrastructure-only classification is inferred by this first finite comparator.

## Subsequent prepared protocol slices, not implemented launch options

These remain separate future slices, not serial prerequisites to this first
lean contract and not claimed supported by schema 2:

1. Expand generation to unloaded / loaded_zero / active1 × c1/c8/c16, OFF c1
   repeated per state. Total21 calls/336 requests/5,376 output IDs including
   this first3. No-op compares unloaded↔loaded_zero; active only to same active
   state. Startup profile and request scale0/1 must both be bound.
2. Descriptive active1 performance: 2K input/64 outputs, c1/c8/c16, OFF/DUAL pair,
   10s warmup+30s measured, three subwindows, six calls. Then four cold-prefill
   cells: OFF/DUAL × 32K/256K, one output. Retain exact inputs, full-wall timing,
   labeled native counters, no-reset/foreign-work evidence, thermal/guard state,
   and all4 drain. The PR8 scalar-metric burst defect is already repaired in the
   pinned toolbox; do not carry the old handoff's stale “unrepaired” status.
3. Confirmatory quality: 2K/8K × c1/c8/c16 across all3 states, 256 fixed outputs,
   scale4 quality sentinels at c16, complete input/output IDs and same-state
   teacher-forced scoring. PR8 forwards supported steering controls to scoring,
   but verified choice-level prompt-ID echo and complete finite aligned scoring
   remain separate. No top-K summary implies full-vocabulary equivalence.
4. Exact capacity, separately per arm/state: four × (64,512 input+1,024 output),
   four × (261,120+1,024), one × (1,047,552+1,024). No nominal clipping, errors,
   preemption/OOM, partial output or changed resource bounds. Labeled running=4
   alone does not prove full long-context residency; retain phase/token/cache
   lifetimes and all-node memory evidence. maxseq16 does not mean 16×1M capacity.
5. Confirmatory performance: four independent paired boots per cell in order
   OFF/DUAL, DUAL/OFF, DUAL/OFF, OFF/DUAL; fixed bursts and cold-prefill
   2K/8K/32K/256K. Use existing paired d=100×(DUAL/OFF−1), df3/t=3.182:
   throughput lower95≥−3%, TTFT/TPOT p50/p99 upper95≤+5%; cold-prefill actual
   input/TTFT lower95≥−3% and TTFT upper95≤+5%. Fewer than100 timed requests
   leaves p99 support insufficient. Subwindows do not increase boot n. Missing
   metrics, unstable control or crossing a margin is inconclusive, not “same.”

Per-workload Q03 use/fallback attribution, real graph replay/individual retirement,
stream completion ordering and original188 full runtime identity remain separate
claims. None can be inferred from this token screen or a favorable speed cell.
