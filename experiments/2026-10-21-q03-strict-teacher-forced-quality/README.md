# Q03 same-state teacher-forced evidence slice

Source/CPU-only candidate for review, not published or adopted. It is separate from frozen PR8 c6342 and the generation-only lean token screen PR16. No existing repository source, CLI default, model, endpoint, native runtime, image, controller or frozen reference was changed.

## What was missing, versus already done

PR8 already preserves the supported steering controls from generation into scoring and rejects incomplete/incomparable recorded alignments. `/tmp/q03-native-steering-contract` already validates the relevant native request/admission projection. Those changes are reused, not reimplemented. The projection in `reference/pr8_projection.py` is the original PR8 function, verbatim, with source hashes and license.

The remaining producer gap is concrete: PR8 `score()` does not set `return_token_ids`; it ignores any returned prompt IDs and saves the requested prefix. Therefore those existing score files alone cannot prove actual server token-prefix identity. The old NEXT handoff's statement that generation controls are dropped describes its old deployment76dab source, not fixed PR8 c6342.

This slice supplies only:

- `scoring_contract.py`: strict offline frozen-corpus and captured-response validation. It keeps requested prefix and actual choice-level echo as different fields. No usable score set is returned when any expected record fails.
- `strict_producer.py`: a separate, opt-in source candidate with an explicitly injected transport, not a network implementation. It uses the same existing `/v1/completions` path and same PR8 scoring request, adding only `return_token_ids=true`. It preflights the whole frozen corpus, preserves every response/error and does not infer or manufacture observed model/profile identity. No existing scorer is modified and there is no auto-use or new CLI.
- Focused synthetic tests and exact-source probes. Fixtures are conspicuously synthetic and do not contain model output or target measurements.

## CPU result and reproduction

Run from this folder with Python 3:

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_scoring_contract

This is the portable, dependency-free synthetic fixture run. 25 methods passed; its log is `evidence/cpu-tests.log`.

Five source-probe methods passed. The source probe additionally requires the two already present, pinned source paths in `source_probe.py` and `SOURCE-IDENTITY.json`:

    PYTHONDONTWRITEBYTECODE=1 python3 source_probe.py

It verifies ten native file hashes, original projection bytes, actual native response/token-budget structure, and runs only original PR8 score/projection/parallel AST functions with an in-memory `_post` recorder. It never compiles the original urllib `_post`. The probe demonstrates that original PR8 accepts a fabricated wrong prefix, while this parser rejects it, and checks that the opt-in producer request adds only the ID-echo flag. No native imports, HTTP, GPU, model, devices, package installs or full suite ran.

## Evidence boundary

The caller independently freezes full ID/prompt/output records and their parsed digest, raw corpus byte hash, exact model/checkpoint/tokenizer/template/encoding identity, generation request options, declared startup profile/artifact/composite identity/FP32 scale, and context budget. Both generation and scoring metadata must match that external freeze. Expected IDs cannot be reconstructed from the successful response intersection. If the caller replaces the independent freeze with the same truncated corpus, no parser can recover the original intended corpus. Hash matching is not independent attestation.

Every expected record must occur once, without any error field. The captured request must match the selected supported steering projection plus fixed model/full token list/max_tokens1/temperature0/top-K/fresh salt/ID-echo flag. Unsupported chat-only generation options are retained in generation provenance, not blindly forwarded. Conflicting steering controls remain unchanged for the existing server validator to reject; this is not a second admission implementation.

The native response must contain exactly one successful choice at index0, the expected canonical served model name, and choice-level `prompt_token_ids` equal to the complete frozen prefix plus output. A root-level ID field cannot substitute. The single frozen model string deliberately uses the canonical served name: native `models/serving.py:144–147` returns the primary served name (or LoRA name), not necessarily an arbitrary request alias. An alias mismatch is evidence-incomparable here, not a quality event or a universal API echo rule; no aliases or model names are changed by this artifact. `prompt_logprobs` must cover the full input; its first row is None, all subsequent rows must have the given token, finite nonpositive numeric transport logprobs and valid ranks, and a complete unique rank1..K set. Every supplied logprob, including prefix and unselected entries, is checked. Output scores start at the original prefix length. Prefix/extra row shifts, malformed tokens, missing top-K ranks, errors, dropped records and duplicates reject rather than yielding an intersection summary.

For serialized input use `load_json`, which rejects duplicate JSON members and NaN/Infinity. The injected producer transport returns a decoded native response object; a future network integration must retain the original wire bytes and use this strict decoder. A dictionary cannot reveal duplicate keys already discarded by a permissive decoder. These fixtures only prove parser behavior, not an HTTP library's decoding or capture implementation.

The source-derived context check is `len(prompt_ids) + len(output_ids) + 1 <= context_limit`. A complete 1,048,576-token capacity sequence cannot be rescored inside a 1,048,576-token context under the existing one-generated-token scorer. It is rejected, never truncated or silently given a different token budget. Capacity and short quality scoring remain separate workloads.

Native197 has a further representation ambiguity: `completion/serving.py:523` invokes `generate/base/serving.py:376–388`, which mutates normal list-based `-inf` logprobs to `-9999.0` in place. `SamplingParams.flat_logprobs` defaults to false and the completion request does not expose it. Therefore the choice field can already be censored even though it reads from `final_res.prompt_logprobs`. A returned `-9999` cannot distinguish a clamped zero-probability value from a genuine finite value. The parser preserves the original evidence and returns `UNRESOLVED_LOGPROB_CENSORING`, with affected positions, no usable score collection and no NLL/distribution acceptance. It never converts the sentinel back to `-inf`, retries, or labels it a quality regression. Genuinely authenticated pre-serialization `-inf` would instead be a zero-probability/infinite-NLL quality event, distinct from null/missing/malformed transport evidence; this HTTP-record artifact cannot recover that fact from `-9999` alone. All records are still validated first, so censoring cannot hide a later missing/error record. The focused source probe executes the exact clamp function on simple CPU objects to reproduce the mutation.

Otherwise, success is explicitly `COMPLETE_RECORDED_SCORING_ONLY`, with `quality_pass=false`. Matching declared identity is not proof of loaded/admitted/applied state. An ordinary native completion response does not contain that proof. Actual runtime/model/tokenizer/profile identity, same-state teacher-forced prefill versus decode/verification, cache and graph row behavior, independent controls and numerical margins remain unrun/unproven. Equality of recorded top-K/given-token probabilities, or truncated top-K KL, is not full-vocabulary distribution equality. No new tolerance, acceptance margin, quality claim, scheduling gate or application usage restriction is introduced.

## Exact native197 source checked

Native commit `154159dc3e38ca8b2321c01a153a1b2ccaf8663b`, tree `ae15047b376c7792062a2607c4ef1dfbc78df18a`; all inspected files matched `git show` and their retained hashes.

- `completion/api_router.py:47–55`: existing typed completion route.
- `completion/protocol.py:50,269–279,369–406`: SteeringRequest inheritance, max_tokens budget, extra_args forwarding. This does not make arbitrary chat options equivalent.
- `completion/serving.py:168–207,519–523,579–591`: sampling conversion and ordinary generation route; actual prompt IDs attached on each choice only when requested. The dedicated choice field uses `final_res.prompt_logprobs`, which has already been mutated in place by the normal list clamp helper. The source probe explicitly confirms that aliasing; it is not an uncensored raw-score channel.
- `serve/engine/protocol.py:95–133`: existing strict steering merge/conflict/beam contract, already covered by the earlier native contract.
- `renderers/base.py:666–690`, `renderers/params.py:440–551`: supplied IDs bypass text tokenization but still pass truncation/padding/length validation. Response echo checking is necessary rather than assuming no transformation.
- `generate/base/serving.py:376–388`, `sampling_params.py:302`: actual clamp mutation and normal non-flat default.
- `vllm/logprobs.py:167–172,180–210`: first prompt row is None; per-position output includes given token and top-K ranks. `v1/engine/logprobs.py:121–186` appends prompt-position data. All are source proof only.

## Remaining integration step, not hidden readiness

After review, a separately authorized Q03 scoring integration can explicitly call the producer with its existing bounded HTTP transport and independently sourced runtime/generation/scoring receipts, preserve raw wire bytes using strict JSON decoding, and pass complete receipts to the parser. This artifact does not wire itself into the current controller or generic PR8 score default. No new model/endpoint/framework or runtime instrumentation is proposed. The existing no-score lean token screen remains unchanged. Q02 remains deferred.
