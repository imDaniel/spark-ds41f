# Original warm4K CPU corpus preparation

`scripts/q03_legacy_corpus_export.py` closes the endpoint/bootstrap cycle for the
finite A188 / B197-Q03-OFF comparison. It prepares one immutable shared corpus
before either measured endpoint starts. It does not contact an existing endpoint,
start a service, import a model, use a GPU, fetch assets, or install packages.

This is a new shared corpus from the original generator, **not exact historical
request replay**: the earlier run did not retain its four document texts. The
output explicitly says `UNAVAILABLE_NEW_SHARED_CORPUS`. CPU preparation is not
field qualification, performance evidence, or proof of an executing image.

## Composed source and retained inputs

Run the complete reviewed source package, including the fixed
`q03_native_corpus_export.py` support module and `q03_legacy_case_replay.py`.
The native exporter compatibility fixes are prerequisites: fresh source-only
helper imports on Python 3.15, standard negative import-cache entries for the
forbidden frameworks, and the existing rich weights-manifest parser. Do not
assemble patches in the execution environment or reuse the older lean exporter's
`encode_text` / `verify_exact` helpers: those force `thinking=False`.

The wrapper input has this shape. Values in angle brackets are documentation
placeholders, not usable identity evidence:

```json
{
  "schema_version": 1,
  "native_cpu_bindings": {
    "path": "/absolute/path/to/retained-native-cpu-bindings.json",
    "sha256": "<actual SHA256 of that file>"
  },
  "native_sources": {
    "r6x188": {
      "head": "ebb44766d6f04d3a9272b712778d7f2fa2cec1fd",
      "root": "/absolute/path/to/retained-r6x188-native-source"
    },
    "native197-off": {
      "head": "154159dc3e38ca8b2321c01a153a1b2ccaf8663b",
      "root": "/absolute/path/to/retained-native197-source"
    }
  },
  "toolbox_root": "/absolute/path/to/exact-PR8-toolbox"
}
```

The pinned `native_cpu_bindings` file retains the existing schema described in
[q03-native-cpu-exporter.md](q03-native-cpu-exporter.md): the actual registered
1017 model/tokenizer snapshot, config and complete tokenizer-asset hashes, special
token asset, existing 83-file weights manifest, retained native runtime metadata,
and native source root. Its `native_root` must equal the wrapper's `native197-off`
root. Assets and retained metadata are checked; weight files must exist, but their
contents are not opened or rehashed. Both installed tokenizer package versions
must match the retained runtime record. Their actual installed-file inventories
are hashed, not inferred from a source pin.

The `toolbox_root` must contain all files in the exact PR8 inventory in
`scripts/q03_http_toolbox.json`, pinned to
`c6342a1b6c5e5a98108905a71452a51d6d3a4a87`. A current project checkout is not an
automatic substitute: files outside the two generator modules have subsequently
changed. The producer verifies the full original toolbox inventory even though
only the generator bodies execute.

Eight selected native tokenizer, encoding, helper and request-protocol files
are pinned independently for both native heads. Their verified bytes are equal.
The producer uses the actual native197 loader projection with the real local
`transformers.TokenizersBackend`; the evidence describes selected-source-byte
verification, not verification of an entire Git tree or a running A188 image.

## Exact generator and tokenization contract

The hash-verified original `servebench/prompts.py` executable bodies and
`servebench/load.py:documents` function body run unchanged with explicit standard
library dependencies. Only the raw token-count operation is replaced with the
local actual-tokenizer call:

- Four slots, context target 4096, run seed 2048; slot seed is `2048000 + slot`.
- Each initial document uses the original 3.8 characters/token estimate and the
  same sorted Python standard-library `.py` files larger than 4096 bytes.
- Raw calibration uses `encode(text, add_special_tokens=True, truncation=False)`.
  The rescaling ratio is `len(initial_text) / actual_count`, **including** the
  `Document ...` prefix in the numerator. The original generator regenerates
  once and counts again. The corpus retains both calibration texts and complete
  ID vectors.
- All original sixteen questions `d000` through `d015` are frozen. Each of the
  64 chat inputs is the full document, two newlines, and one original question.
- Chat rendering uses `add_generation_prompt=True` and
  `continue_final_message=False`. No `thinking`, `enable_thinking`, or
  `reasoning_effort` override is supplied. The pinned native default remains
  thinking enabled / high effort.
- The complete rendered text is encoded with `add_special_tokens=False` and
  `truncation=False`. Its IDs must equal the native renderer's direct tokenized
  result. Documents and questions are never separately encoded and concatenated;
  token IDs are never padded, sliced, shortened to 4096, or decoded and reused as
  substitute input text. The original character slicing is preserved.

The corpus includes the Python implementation/version/executable hash and the
complete generator source-file inventory. Different Python installations may
produce different document bytes; generate once and use the exact canonical
corpus for both arms, both C1/C4 cases, and every selected steering state. The
existing replay preflight independently compares actual endpoint token IDs with
this corpus before measurement.

## Invocation and outputs

Use the already prepared CPU environment and its existing offline/device
isolation. The in-process audit/import guards are defense in depth, not an OS
sandbox. No new package installation or model download is part of this command:

```sh
python -B scripts/q03_legacy_corpus_export.py \
  --bindings /absolute/path/to/legacy-cpu-bindings.json \
  --output-directory /absolute/path/to/new-legacy-corpus
```

The output directory must be new and must not overlap any immutable source,
asset, metadata, or binding input. Publication occurs only after rechecking the
source and input bytes, installed packages, generator environment, all eight raw
calibration vectors, and all 64 full rendered input vectors. Outputs are written
with the existing exclusive private-file helpers:

- `corpus.json`: canonical `legacy_warm4k_v1` corpus, including artifact pins,
  four real document texts, eight calibration records, sixteen questions, and 64
  full template-ID vectors. This file's path/hash is the replay corpus binding.
- `cpu-evidence.json`: both selected native source inventories, actual package
  and input provenance, full rendered text for all 64 rows, exact tokenization
  options, and the explicit CPU-only / non-field scope.
- `bindings.json`: retained wrapper input.
- `receipt.json`: immutable file pins. The CLI also prints the receipt SHA256.

A failure produces no success receipt. Do not label synthetic unit fixtures or
real-backend tests with mock tokenizer assets as the registered model's corpus.

## Focused checks

```sh
python -B -m unittest discover -s tests -p test_q03_legacy_corpus_export.py -v
```

Tests exercise the exact original generator with explicitly synthetic tokenizers,
full-vector identity and boundary merges, raw-special-token behavior, unchanged
native chat defaults, two-head source pins, drift negatives, artifact shape,
canonical output receipts, and fresh source-only helper loading. They perform no
HTTP requests, GPU/model operations, or full-suite qualification.

## Explicit portable tokenizer-only cloud preparation

The original invocation above and its full 83-file snapshot/runtime checks are
unchanged. A separate command supports the registered three public assets
(`config.json`, `tokenizer.json`, `tokenizer_config.json`) without copying model
weights or pretending an x86 cloud process is the native ARM container.

```sh
python -B scripts/q03_legacy_portable_corpus.py prepare-tokenizer-only \
  --bindings /absolute/path/to/cloud-tokenizer-bindings.json \
  --output-directory /absolute/path/to/new-portable-preparation
```

The explicit cloud binding contains exactly:

- `schema_version: 1`, `mode: "TOKENIZER_ONLY_CLOUD"`;
- `model_repository`, `model_revision`, `tokenizer_revision`: the registered
  DeepSeek-V4.1-Flash-1017 repository and exact revision already used by the full
  exporter;
- `model_root`: the directory containing the three exact registered public
  assets. The command checks their fixed registered hashes and rejects extra
  tokenizer/template sidecars;
- `weights_manifest`: the path/SHA256 pin of the original retained 83-file
  manifest. Its raw bytes and complete inventory are validated; rich replica
  metadata stays unchanged and opaque. No weight bytes are opened, no missing
  files are synthesized, and no weight existence claim is made;
- `native_sources`: the same two fixed native heads and eight-file source
  closures required by the full exporter;
- `toolbox_root`: the complete exact PR8 toolbox inventory, not the current
  checkout's changed `servebench/__main__.py`.

Outputs are distinct from the replay corpus:

- `portable-preparation.json`: an explicitly `NOT_REPLAY_ADMISSIBLE` envelope,
  containing a canonical content hash and the exact model/tokenizer content
  identity, generator provenance, four document texts with eight calibration
  records, sixteen original questions and 64 complete chat-ID vectors. It has
  no site path/artifact fields. Source text can naturally contain path literals;
  those are document bytes, not operational artifact bindings.
- `cloud-provenance.json`: actual measured cloud package/file inventories,
  architecture, interpreter, source and asset paths, raw 83-file manifest hash,
  tokenization options, and `weights_present_verified: false`. It makes no
  native-image, historical-document, or field-performance equivalence claim.
- `stdlib-sources/`: byte-for-byte snapshots of the generator's sorted stdlib
  inputs, bound to the path-free inventory in the content. The before/after
  checks retain the actual source bytes used, not a future site's Python files.
- `receipt.json`: immutable preparation and provenance pins. Existing output
  directories cannot be reused or overwritten.

The same unchanged PR8 document generator and native tokenization calls run.
Raw calibration adds special tokens. Chat defaults omit all thinking,
`enable_thinking`, and reasoning overrides, preserving native true/high;
`add_generation_prompt=True`, `continue_final_message=False`,
`add_special_tokens=False`, and `truncation=False`. No token vectors are padded,
concatenated, sliced, or shortened. The preparation and its content alone are
rejected by the existing replay corpus, binding and client-plan validators.

## Separate site binding without regenerating documents

This command is a later site CPU preparation operation, not a service launch,
benchmark, or permission to access any machine. Run it only in an authorized
site environment with the original full registered bindings available:

```sh
python -B scripts/q03_legacy_portable_corpus.py bind-site \
  --preparation /absolute/path/to/portable-preparation.json \
  --preparation-sha256 ACTUAL_PREPARATION_SHA256 \
  --bindings /absolute/path/to/full-legacy-cpu-bindings.json \
  --output-directory /absolute/path/to/new-site-bound-corpus
```

`bind-site` first verifies the caller-pinned, canonical portable bytes and the
unchanged full exporter binding. All 83 registered manifest files must exist;
model/tokenizer assets, retained runtime metadata, package versions and both
native source selections still have to pass the original checks. Portable and
site model/tokenizer identity must match in full.

It then tokenizes only the already frozen calibration texts and each complete
frozen document/question input, comparing every ID. It does not call the PR8
random/stdlib generator, rescale any text, or consult the site's stdlib to make
replacement documents. Any drift fails before output. It writes a new ordinary
replay `corpus.json` with the validated site artifact pins, plus
`site-link-receipt.json` linking the original preparation hash, content hash,
full site binding/artifact pins, actual site package measurements, comparisons,
and final corpus hash. The original preparation is never rewritten.

The registered replay endpoint preflight remains required. A successful CPU
binding is not field qualification or evidence of A/B performance.

Focused portable tests (synthetic tokenizers/assets only):

```sh
python -B -m unittest discover -s tests -p test_q03_legacy_portable_corpus.py -v
```
