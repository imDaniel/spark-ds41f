# Sixty-case quality CPU corpus

This independent producer handles `quality_pilot60_v1`. It does not alter the
legacy warm4K/P1 producer or its execution path. Preparation is CPU/tokenizer
work only: no generation endpoint, model, reference/candidate execution, GPU,
network, weight transfer or weight rehash. The source-only helper closure is
selected before its first import; invoke it in a fresh interpreter.

## Fixed input and output contract

The approved R2 `SHA256SUMS` is
`c3db307e1f145026c33252c36cda819ffefda1d189400d6e06d316454f3bf580`.
The exact generation-only `prompts.jsonl` is
`8cdcf6143d80d4776c052166a9764d36d8b5020038ad24118e352f3493cf4ca0`.
All 34 original manifest members are verified and retained, without executing
material scripts. Only the exact 60 ordered single-user prompts reach the
native tokenizer. Gold is read as data only to verify the separate case metadata;
it is never part of a tokenization request.

Rendering uses the unchanged verified native default thinking/high semantics:
no thinking/effort overrides, `add_generation_prompt=True`,
`continue_final_message=False`, complete rendered-text encoding with
`add_special_tokens=False`, `truncation=False`. Every rendered vector must equal
the native direct chat-template vector. All input IDs are actual backend output,
not concatenated, padded, truncated or reconstructed from counts.

The path-free portable content retains original prompt JSONL, each prompt's
UTF-8 identity, complete rendered text, text/ID hashes and lengths, all 60 input
vectors, the exact case map, tokenizer identity and rendering options. The outer
artifact is explicitly `NOT_REPLAY_ADMISSIBLE`. Its provenance separately keeps
actual Python/platform/package identities, selected source bytes, original raw
asset manifest and original material files. Cloud x86 is not native ARM or
executing-container proof.

## Prepare once, tokenizer-only cloud

Use the same reviewed cloud-binding schema as the legacy portable producer:
registered minimal public assets, both exact A188/B197 source selections,
original retained 83-file manifest, and exact PR8 toolbox source. The toolbox is
verified, not executed as a prompt/document generator here.

```text
PYTHON -B DEPLOYMENT/scripts/q03_quality_portable_corpus.py prepare-tokenizer-only \
  --bindings CLOUD_BINDINGS \
  --materials ORIGINAL_R2_MATERIAL_DIRECTORY \
  --output-directory NEW_CANONICAL_OUTPUT_DIRECTORY
```

Substitute actual canonical absolute input/output paths and the independently
verified tokenizer interpreter. Output must not overlap any immutable input.
Retain `portable-preparation.json`, `cloud-provenance.json`, `receipt.json` and
all retained source/material/raw-input directories together. The corpus identity
is the returned preparation hash plus content hash; do not rewrite either.

## Separate unchanged full site binding

```text
PYTHON -B DEPLOYMENT/scripts/q03_quality_portable_corpus.py bind-site \
  --preparation UNCHANGED_PORTABLE_PREPARATION \
  --preparation-sha256 ACTUAL_PREPARATION_SHA256 \
  --bindings FULL_ORIGINAL_SITE_BINDINGS \
  --materials UNCHANGED_R2_MATERIAL_DIRECTORY_AT_SITE \
  --output-directory NEW_CANONICAL_SITE_OUTPUT_DIRECTORY
```

This calls the unchanged full legacy/native site validator, not the cloud
validator: all 83 original manifest files must exist, model/config/tokenizer
assets and both selected native source closures must match, and actual installed
package versions must match the retained native runtime pins. It does not rehash
weight shards. It verifies the original materials and compares all 60 complete
rendered strings and all 60 complete ID vectors against the portable artifact.
No prompt regeneration, source substitution or fallback is permitted.

Only then does it emit `corpus.json` using the lifecycle client's exact schema:
`schema_version`, `workload`, `model`, `material_sha256`, `prompts`,
`tokenizer_identity`, `artifacts`, `cases`, `default_rendering`. Each final case
contains only `id`, `category`, `language`, `prompt_sha256`, `input_ids`.
`site-link-receipt.json` retains the preparation/site link and full comparison.
Preserve original preparation/provenance separately and unchanged.

This is CPU input binding, not runtime qualification or a semantic PASS. Live
`/tokenize` checks, generation prompt-ID equality, actual parser/default/state
bindings, lifecycle evidence, safe candidate evaluation and review remain
independent requirements. No runtime/parser/engine evidence is fabricated by
this producer. It neither changes the existing deadline nor segments/retries a
quality lane.

## Focused tests

```text
python -B -m unittest discover -s tests -p test_q03_quality_portable_corpus.py -v
```

These are explicitly synthetic protocol/source-loader tests, not model or site
evidence. They cover exact metadata/prompts/defaults, full vectors, source-cache
and package/extension shadows, mandatory full site validation, immutable
preparation and the final corpus schema. The genuine CPU preparation and its
raw provenance are separate artifacts, not checked-in fabricated fixtures.
