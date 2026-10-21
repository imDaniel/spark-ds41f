# Native CPU corpus exporter for the lean schema-2 lane

This is **preparation only**, on a new branch based on deployment commit
`3a49389f7ba0e93f05bab1c9a52b4425d4dd7fde`. It adds an executable producer for the
existing [qualification export](q03-qualification-execution.md) without changing
that frozen validator, HTTP capsule, native image, controller, or serving policy.
The HTTP binding is schema 2; its existing tokenizer export remains schema 1.

No model service is started or contacted. There is no generation, device action,
SSH, package installation, model download, automatic fallback, or authorization
to run this on the serving hosts. The actual registered snapshot and measured
native runtime record are supplied for a separately authorized local CPU run.
Cloud unit tests are synthetic control-flow tests, **not a real corpus**, and
must never be attached as field evidence.

## Required existing inputs

Use an owner-controlled, non-shared-writable location for the bindings, native
source tree, retained provenance files, and new output directory. Do not change
model data permissions to satisfy the code/evidence boundary. The registered
model snapshot itself may contain its existing Hugging Face blob symlinks.

- The registered local snapshot for
  `deepseek-ai/DeepSeek-V4.1-Flash-1017`, revision
  `1bae0e1088993b9b82de08f59de5be5a34e42170`. `model_root` is the canonical actual
  snapshot mounted read-only as `/models` by the existing lane, not the hub
  directory or a download location. The later admission still checks the mount.
- Previously retained expected SHA-256 pins for `config.json`, `tokenizer.json`,
  `tokenizer_config.json`, and every relevant tokenizer sidecar. The exporter
  reads and checks the actual bytes before and after constructing the corpus.
  It rejects unlisted sidecars, alternate tokenizer-file redirects, remote-code
  mappings, or alternate vocabulary/model files outside this single-file native
  backend closure. It does not silently substitute another tokenizer.
- The existing **83-file** weights manifest and its retained SHA-256. Supported
  retained formats are ordinary `sha256sum` lines (`HASH  relative/path`) or a
  JSON object `{"files": {"relative/path": "sha256", ...}}`. The manifest is
  read and hash-checked, its 83 distinct relative entries are checked for local
  existence, and its raw provenance is retained. Weight file contents are
  **not opened or rehashed**. An unsupported existing manifest format fails
  explicitly; do not fabricate a new manifest and call it the old evidence.
- A complete native197 source root, with five files matching the exact pins in
  `SOURCE_FILES` in the exporter. These are the native wrapper, encoder,
  `get_cached_tokenizer` source, tokenizer protocol, and tool-name helper.
- The existing native runtime metadata file with exactly the fields required by
  `q03_qualification.verify_artifacts`: `image`, `source_head`,
  `runtime_manifest_sha256`, `model_root`, `model_repository`, `model_revision`,
  `tokenizer_revision`, `transformers_version`, and `tokenizers_version`.
  The version values must be observed in the native environment, not inferred
  from an image name, a lock file, or this exporter. The existing runtime
  manifest hash alone does not provide these package version values.
- An existing CPU interpreter environment with the real installed `transformers`
  exposing `TokenizersBackend` and `tokenizers`. It must match the measured
  native record's actual versions. The exporter neither chooses package versions
  nor installs them. Missing packages or a mismatch are a blocker, not a reason
  to invent versions or install another backend.

## Bindings and command

The following shows the closed input shape. Placeholders are deliberately
non-runnable. Obtain the actual values from retained local evidence; the
exporter does not create the native runtime record on the operator's behalf.

```json
{
  "schema_version": 1,
  "model_repository": "deepseek-ai/DeepSeek-V4.1-Flash-1017",
  "model_revision": "1bae0e1088993b9b82de08f59de5be5a34e42170",
  "tokenizer_revision": "1bae0e1088993b9b82de08f59de5be5a34e42170",
  "model_root": "<canonical registered local snapshot>",
  "model_config_sha256": "<retained config.json hash>",
  "tokenizer_assets_sha256": {
    "tokenizer.json": "<retained hash>",
    "tokenizer_config.json": "<retained hash>"
  },
  "special_tokens_file": "tokenizer_config.json",
  "weights_manifest": {"path": "<existing manifest>", "sha256": "<retained hash>"},
  "runtime_metadata": {"path": "<measured native runtime record>", "sha256": "<retained hash>"},
  "native_root": "<canonical native197 source root containing vllm/>"
}
```

If the snapshot has additional relevant tokenizer sidecars, include their actual
retained pins. `special_tokens_file` must identify the actual inventoried file
containing the special-token configuration; its identity is the whole file's
bytes, not an invented extracted subset.

Run once, in a fresh interpreter, with no preimported GPU/model modules:

```sh
python3 -B scripts/q03_native_corpus_export.py \
  --bindings "$ACTUAL_NATIVE_CPU_BINDINGS" \
  --output-directory "$NEW_OWNER_CONTROLLED_CORPUS_DIRECTORY"
```

The output directory must not exist and must not overlap the model, native
sources, or input provenance. Use the operator's existing network/device
isolation where available. The script also hides GPU devices, disables the
ML frameworks before backend imports, enables Hugging Face offline mode, rejects
GPU/model imports, and installs a Python audit guard against socket operations,
child processes, and direct GPU device opens. These are defense in depth, not a
claim that Python audit hooks constitute an operating-system security sandbox.
It never creates a namespace, container, service, or environment itself.

The source-only bytecode prefix is set **before importing task-owned helpers**,
with bytecode writes disabled, so old timestamp-valid `.pyc` files cannot replace
recorded helper or tokenizer package source. A process that imported helpers
before isolation must be replaced by a fresh interpreter, not silently reused.

## Exact native semantics and bounded construction

The loader verifies all five complete native source files, then compiles their
unchanged callable bodies in an explicit CPU-only namespace. It executes the
whole stdlib-only protocol, the complete tool-name helper and encoder bodies,
the exact `get_cached_tokenizer` function, and the complete native wrapper
bodies. Top-level imports in projected files are omitted and replaced by explicit
real stdlib, backend, and native dependency bindings. The backend is the real
installed `transformers.TokenizersBackend`; `ChatCompletionMessageParam` is an
explicit annotation-only `dict` binding. No behavioral tokenizer/cache stub,
`AutoTokenizer`, generic Jinja replacement, or synthetic token producer exists
in the CLI. This is honestly a **native source CPU projection**, not an assertion
that the entire vLLM runtime package was imported.

The unchanged native constructor is called as:

```python
DeepseekV41Tokenizer.from_pretrained(local_snapshot, local_files_only=True)
```

Sixteen distinct written planning tasks seed the corpus. Complete natural-language
notes and then reference keywords extend each original string user message.
Every candidate's **entire native-rendered template** is tokenized. The search
accepts only measured positive length increases that do not exceed 2,048. It has
a fixed 4,096-candidate limit per row and fails closed if no exact extension is
found. It makes no BPE additivity or monotonicity assumption, does not use a
binary search, does not slice IDs, does not decode token seeds, and does not
truncate a longer prompt to achieve the target.

Each final text is independently checked through all three paths:

1. `apply_chat_template(messages, thinking=False, tokenize=False)` followed by
   backend `__call__(add_special_tokens=False, truncation=False)`.
2. The actual HTTP rendered-text semantics:
   `__call__(add_special_tokens=False, truncation=True, max_length=1048561)`.
3. Native wrapper `apply_chat_template(messages, thinking=False, tokenize=True)`
   without any truncation parameter.

All full ID vectors must be identical and exactly 2,048 integers. All 16 texts
and full input-ID vectors must be distinct. The completed rows are checked again
before output, detecting drift after construction. The HTTP truncation setting
is faithfully reproduced, but equality to the untruncated path proves it did
not clip this corpus.

## Retained outputs and what they prove

A successful run writes new immutable files and prints their pins:

- `tokenizer-export.json`: all 16 original texts and all 32,768 actual input IDs,
  plus the existing schema-1 export identity and native artifact pins.
- `cpu-evidence.json`: all 16 rendered template strings, candidate counts, exact
  lengths, five native source pins, actual preparation/helper source pins and
  recipe revision, actual installed package versions and byte inventories of
  the package roots and matching dist-info files (excluding bytecode and external
  wheel scripts), import origins, Python version, and effective isolation settings.
- `bindings.json`: the actual parsed input bindings; the original raw input hash
  is also retained in the CPU evidence.
- `prepared/prompts.jsonl` and `prepared/corpus.json`: produced by the unchanged
  `q03_qualification.prepare`, including its exact servebench prompt digest.
- `receipt.json`: the final output pins. An interrupted/failed run without this
  completed receipt is not a successful preparation.

Package versions come from installed distribution metadata and are checked
against the loaded modules and supplied native record. Package byte inventories
cover the package roots and matching dist-info files, excluding bytecode and
external wheel scripts. They are measured in this CPU process and rechecked after
construction; they are not an OS or transitive-dependency inventory. They are not
invented wheel hashes or a claim of native-image byte equality. The supplied
native image/source/runtime record and measured CPU environment remain separate
provenance. Even a successful export is **not** a native HTTP token-match result,
model generation, OFF/DUAL qualification, performance claim, or permission to
change the frozen capsule. Those existing gates still apply to a newly admitted
schema-2 capsule.

## Source validation

```sh
python3 -B -m unittest discover -s tests -p 'test_q03_native_corpus_export.py' -v
python3 -B -m unittest discover -s tests -p 'test_q03_qualification.py' -v
git diff --check
```

The focused suite uses explicitly synthetic test doubles to exercise exact-length
construction, nonmonotonic candidate lengths, HTTP/native drift, truncation,
bounded failure, source/asset/version drift, missing/redirected assets,
byte identity and import origins, CPU/no-network guards, immutable output, and
complete export-to-preparation wiring. It does not provide or claim real
registered tokenizer assets, real model IDs, or a field execution result.
