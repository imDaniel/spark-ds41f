# First finite capacity corpus: offline native CPU preparation

This opt-in exporter prepares only the real-text inputs for the reviewed
`q03_capacity.py` producer: workload `capacity_64k_c4`, four distinct requests,
each with exactly **64,512 fully templated input IDs** and a later 1,024-output
budget. It is not a 1M, general target, generation, endpoint, or field runner.
No service or device is contacted. Capacity, residency, admission and performance
remain unproven by preparation.

## Source composition and invariant boundaries

The source composes PR25 commit `fb4d6e8e8e6ec81a767c1163e4bab4385fbd1266`
(tree `2fb1ecd9` prefix) with reviewed PR27 producer commit
`3971a0664e6f77e9d9f3fe6814e4383858950ced`
(tree `f785c9082a8c70b81204a37e9baef042aea59b52`). The native CPU helper's script
SHA-256 is `d09d4bacf50efd9ccddf2493c31995909e3eef22aaa0bb2fc5b4ce0b09a2e457`.
That is **not** the native component source head. The existing checks still bind
native197 source `154159dc3e38ca8b2321c01a153a1b2ccaf8663b`, image
`sha256:90bd2bbc4709b14a2210a6752c707d9d16661180248949e183fa0c25b6691ff6`,
and runtime manifest
`24315da1b6138aef8acccb7244000feceef71b74486eeddce31bec25264919b9`.
No native source, image, runtime policy, capacity producer, or original
`q03_native_corpus_export.py` behavior changes. The original 16 × 2,048 exporter
and its defaults remain unchanged. This is an independent preparation entrypoint.

## Required retained inputs

Use exactly the input shape and existing evidence described in
[q03-native-cpu-exporter.md](q03-native-cpu-exporter.md). Reuse actual retained
bindings, not synthetic unit-test records or a rewritten weights inventory:

- Registered local `deepseek-ai/DeepSeek-V4.1-Flash-1017` revision
  `1bae0e1088993b9b82de08f59de5be5a34e42170`, actual tokenizer/config assets and
  complete sidecar closure with retained expected hashes.
- Original 83-file manifest at its original path and raw-byte SHA-256. Existing
  rich-envelope generator/root/host/time/file-identity metadata stays opaque and
  intact. No normalization, replacement inventory, or weight-content reads.
- Complete five-file pinned native tokenizer source closure; original measured
  native image/source/runtime/package metadata. Missing provenance is a blocker.
- An already installed CPU interpreter exposing real Transformers
  `TokenizersBackend` and `tokenizers`, with versions matching that native record.
  There is no package installation, backend substitution, download or fallback.

The exporter reuses the native CPU projection and all asset, source, manifest,
package-version and byte-inventory checks. It records and rechecks its own script
and `q03_capacity.py` too. Every helper is loaded under the source-only bytecode
prefix before imports. Run in a fresh interpreter, never a pre-imported notebook.
The no-network/child-process/GPU-import/device-open guards are inherited unchanged;
use existing operator OS isolation where available. Python guards are defense in
depth, not an OS sandbox.

## Opt-in operator command

Only a separately authorized CPU environment that already has these inputs may
run the following. This document is not authority to use a Mac, serving host,
SSH, container, model endpoint, or any device. No cloud test result supplies the
missing registered capacity corpus.

```sh
python3 -B scripts/q03_capacity_corpus_export.py \
  --bindings "$ACTUAL_NATIVE_CPU_BINDINGS" \
  --output-directory "$NEW_OWNER_CONTROLLED_CAPACITY_CORPUS_DIRECTORY"
```

The output directory must be new, canonical, absolute, owner controlled and
non-overlapping with immutable model/source/provenance inputs. There is no target,
request-count, output-budget or unbounded-search option. Do not edit the existing
16 × 2,048 corpus or reuse its IDs as a capacity corpus.

The four distinct written planning tasks use complete natural-language notes.
A fixed 128/32/8/1-note block schedule followed by reference keywords proposes
full text strings. Every candidate is rendered and tokenized from scratch.
Only measured positive increases no greater than 64,512 are accepted. Overshoot
or nonmonotonic candidates are discarded; no binary search, BPE additivity,
synthetic IDs, decoded token seeds, token slicing or truncation repair exists.
Construction is bounded at 512 candidate measurements and 2 MiB UTF-8 source
text per row. These finite preparation bounds do not restrict service usage.
Failure to find an exact text is explicit, not grounds to widen the target.

Each finished row is checked through three independent call paths: native
rendered text without truncation; the HTTP tokenizer settings with
`truncation=True, max_length=1048561`; and native wrapper
`apply_chat_template(..., thinking=False, tokenize=True)`. Full ID vectors must
be equal and exactly 64,512 integers. Equality proves this HTTP setting did not
clip the text. All four texts and full ID vectors must differ. The same three
paths are checked again before output, beyond the candidate-measurement budget.

## Output and handoff to the capacity producer

A completed `receipt.json` and printed pins identify:

- `tokenizer-export.json`: producer schema 1, exactly `schema_version`, `workload`,
  `tokenizer_mode`, `identity`, `artifacts`, `request`, `rows`. Workload is
  `capacity_64k_c4`; tokenizer mode is `deepseek_v41`; identity/artifacts and
  request are the unchanged qualification contracts. Ordered rows 0–3 contain
  only `id`, original `text`, and the complete actual `prompt_ids`.
- `cpu-evidence.json`: preparation-only scope, original binding raw hash,
  retained runtime and manifest pins, all rendered texts, candidate counts,
  construction bounds, actual source/package measurements and isolation settings.
- `bindings.json`: parsed copy of the supplied bindings, not a new runtime record.
- `prepared/native-export.json`, `prepared/prompts.jsonl` and
  `prepared/corpus.json`: made by the **actual reviewed `q03_capacity.prepare`**,
  not the 16-row qualifier or an offline-validator reconstruction. The native
  export bytes are preserved; corpus carries `source_export`, `prompts` and the
  exact servebench `prompts_sha256`. `q03_capacity.validate_corpus` checks it again.

A separately prepared schema-5 HTTP capsule manifest uses the returned corpus pin
under `http_service.capacity = {workload: "capacity_64k_c4", corpus: {path, sha256},
metric_labels: {model_name: "deepseek-v4.1-flash", engine: "<native index>"}}`.
The derived runtime binding carries this same object under `http.capacity`.
The engine label must be independently observed/frozen, never guessed by the
exporter. The export remains schema 1; schema 5 describes the HTTP capsule.
No capsule is created or changed here. An interrupted run without the completed
receipt is not usable preparation evidence; preserve the failure and choose a
new output directory for an independently authorized retry.

## Source checks, not field claims

```sh
python3 -B -m unittest discover -s tests -p 'test_q03_capacity_corpus_export.py' -v
python3 -B -m unittest discover -s tests -p 'test_q03_native_corpus_export.py' -v
python3 -B -m unittest discover -s tests -p 'test_q03_capacity.py' -v
git diff --check
```

These are synthetic CPU tests of whole-text construction, overshoot,
nonmonotonic boundary behavior, bounded failure, no-truncation equality,
missing provenance, source-only imports, isolation and actual consumer wiring.
They do not establish genuine 64K IDs, native ARM package identity, capacity,
residency, health, swap, a common clock or performance. A real-package import
check can prove only that the actual installed CPU backend imports under these
guards; it cannot replace an actual registered-asset export.
