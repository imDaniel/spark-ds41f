# Strict teacher-forced quality evidence package

## Scope and exact source

Base: `3a49389f7ba0e93f05bab1c9a52b4425d4dd7fde`.
The one intended change is to add this opt-in, standalone source/CPU artifact.
There is no runtime change, automatic test discovery integration, CLI registration,
transport implementation, dependency installation, new quality threshold, or change
to the existing PR8 work or default `servebench.behaviour.score()`.

`README.md`, `SOURCE-IDENTITY.json`, `scoring_contract.py`, `strict_producer.py`,
`test_scoring_contract.py`, `source_probe.py`, `reference/`, and `evidence/` are
copied byte-for-byte from the reviewed artifact. `MANIFEST.json` is also unchanged.
Its SHA256 is
`455fe5e85630730d060002a89b4b6e9a11f7f5d400f5e8f17fb7e8edd910e426`.
The independent final source/CPU review receipt SHA256 is
`cdd8fbe9eadfa6a27f2ce1ee397b326d74c2d965e7688a52cdb6d418ae828b76`.
That review found no remaining blocker within the source/CPU artifact scope, not
runtime quality, adoption, publication, or endpoint-integration approval.

No import adaptation or implementation edits were required. Every source-relative
path is unchanged under this experiment directory. The preserved PR8 projection is
not replaced with the older default scorer at the packaging base. Its source pin
and required license notice remain in `SOURCE-IDENTITY.json` and `reference/`.

The original README and manifest describe the artifact's original freeze, not the
later packaging state. Historical CPU/source-probe logs are copied as evidence,
not newly run results. The original source probe uses pinned author-environment
source paths; it is not a portable checkout test and is not invoked by this smoke.
Changing those paths or rerunning the source audit is outside this byte-preserving
packaging step. The standalone synthetic fixture suite remains portable.

## Explicit package/import smoke

From the repository root:

```sh
cd experiments/2026-10-21-q03-strict-teacher-forced-quality
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -v test_packaging
```

The three packaging checks validate all accepted payload hashes, standalone import
resolution (including importing but not running the source probe), and the composed
producer/parser API with two tiny in-memory synthetic receipts. The 25 original
fixture methods and five original source probes are not rerun. No endpoint, native
module, model, GPU, device, existing scorer, controller, or cluster is exercised.
`packaging-smoke.txt` records the packaging-only result. No full-suite, doctor,
build, native patch replay, runtime admission, or target quality claim is made.

## API usage, not live execution

Run from this experiment directory or explicitly add this directory to the calling
process's Python module search path. Do not import or replace the default scorer.
For independently frozen and captured JSON files, offline validation is:

```python
from pathlib import Path
from scoring_contract import load_json, validate

frozen = load_json(Path("frozen-corpus.json").read_bytes())
evidence = load_json(Path("captured-scoring-evidence.json").read_bytes())
result = validate(frozen, evidence)
```

These filenames represent caller-provided inputs, not included target results.
`frozen` contains the complete independent corpus and identity required by
`check_frozen()`. `evidence` retains independent `generation_identity` and
`scoring_identity`, `generation_extra_body`, both corpus digests, and all records.
Do not fabricate runtime observations by copying the freeze into those receipts.
Fixture metadata in tests is explicitly synthetic and is not such attestation.

For a later, separately authorized collection integration, the data-only request
and injected-transport APIs are:

```python
from scoring_contract import strict_request
from strict_producer import collect_records

request = strict_request(frozen, sequence_id, fresh_cache_salt)
records = collect_records(frozen, authorized_post, timeout=bounded_timeout)
```

`authorized_post(path, body, timeout)` is a required caller-supplied function;
there is no default endpoint or HTTP client. The existing path is
`/v1/completions`. The full request is the unchanged supported PR8 steering
projection plus fixed model/full prefix-and-output IDs, `max_tokens=1`,
`temperature=0`, top K, a fresh salt, and `return_token_ids=true`.
The caller must preserve original response wire bytes and strictly decode them
with `load_json` before returning a response object; a dict cannot reconstruct
discarded duplicate JSON members. `records` does not supply or attest identity.

The smoke's `memory_post` illustrates the interface without a network connection.
Nothing in this package authorizes sending a request or starting a model.

## Result interpretation and retained boundaries

- `COMPLETE_RECORDED_SCORING_ONLY` means complete, aligned recorded evidence only.
  `quality_pass` is still false. Declared metadata is not engine-admission,
  loaded-state, applied-state, checkpoint, or tokenizer attestation.
- Complete choice-level returned prompt IDs must equal the entire frozen prefix
  plus output. A requested prefix or root-level token list cannot substitute.
- Every independently frozen corpus ID must occur once. Output rows begin at the
  original prefix length; missing/error/shifted rows never become a successful
  intersection. Context must include the existing one scoring token; no truncation
  or changed capacity workload is introduced.
- Raw serialized `-9999` remains ambiguous native censoring and yields
  `UNRESOLVED_LOGPROB_CENSORING`, no usable score set, and no fabricated NLL.
- Top-K/given-token evidence is not full-vocabulary distribution proof. Numerical
  margins, target model quality, and same-state runtime equivalence remain unrun.

The generation-only lean token screen, capacity/performance workloads, existing
lifecycle, frozen references, and deferred Q02 are unchanged. Publication and any
future runtime integration are separate from this local candidate.
