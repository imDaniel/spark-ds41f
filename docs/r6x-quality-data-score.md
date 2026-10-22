# Finite offline R2 quality data evaluator

This independent addition does not change P1, launch generation, execute Python
answers, run a model, or contact a network/device. It reads the unchanged R2
material snapshot and one immutable `r6x-quality-capture/1` bundle. It is not a
full quality freeze or a general evaluator/plugin framework.

## Status and field-unproved boundary

- Implemented: strict lossless JSON and exact-text comparison kernels for the
  existing 36 JSON + 8 text cases, fixed material/hash admission, immutable
  request/case/raw-response checks and a complete 60-slot report.
- Report schema `r6x-quality-data-score/2` predeclares the fixed score target
  `kind=literal_api_field`, JSON pointer `/choices/0/message/content`. The entire
  delivered field enters the existing data rubrics after evidence admission.
  This is an API-visible observation, not verified internal model-final extraction.
  Missing/null/empty means `NO_API_VISIBLE_ANSWER`; non-string is a schema issue.
- **Native internal reasoning/final parser binding remains UNBOUND.** A supplied
  `VERIFIED` label or hash never proves the live parser/configuration. It does not,
  however, make the literal returned API field unknowable. The versioned, separately
  hash-pinned amendment is [the API-content contract](r6x-quality-api-content-contract.md).
  The report retains both its hash and the original base evaluator-contract hash.
- Eight Python cases are `not_evaluated` / `NOT_EVALUATED`; eight open cases are
  `not_evaluated`, with a separate `pending_review` annotation for admitted API responses. There is no Python backend, AST substitute, code excerpt
  extraction, reviewer assignment, automatic refusal label, or fake judgment.
- Every result has `comparison_eligible=false`; runtime, actual source/OCI/model,
  tokenizer/template/effective defaults, applied state/scale, lifecycle/drain,
  and paired comparability remain independently unproved. No hash-only context
  label is promoted into evidence that these operations happened.
- Overall quality is always `NOT_EVALUATED`. A successful CLI exit means a report
  was written, not quality PASS. No keep/drop, default-scale, user workflow,
  representative population, noninferiority or formal-600 conclusion follows.

## API and CLI

`evaluate(material_directory, capture_directory, max_response_bytes=...)` reads
only pinned data files from the reviewed R2 directory. The exact snapshot SHA is
`c3db307e1f145026c33252c36cda819ffefda1d189400d6e06d316454f3bf580`.
No reference implementation or material-verification program is imported/run.

The CLI takes `--materials`, `--capture`, `--max-response-bytes` and `--output`.
The explicit byte bound must come from an approved execution capsule and match
both the capture summary and identity. There is deliberately no guessed default.
An example with a placeholder, not a newly approved numeric ceiling:

```sh
python scripts/q03_quality_data_score.py \
  --materials /path/to/frozen-r2 \
  --capture /path/to/owned-attempt/quality-capture \
  --max-response-bytes "$APPROVED_CAPSULE_RESPONSE_BYTES" \
  --output /path/to/new-quality-data-report.json
```

The output is exclusive-create and must be outside both input directories. The
scorer never rewrites capture, source material, existing output, or capture's
`semantic_scoring=NOT_EVALUATED`. An invalid whole-bundle identity fails admission
with an explicit error rather than inventing a partially successful report.
Duplicate/missing/reordered primary slots produce all 60 invalid planned slots,
not a successful-subset intersection. In that case verified transport counts
are null; separately labelled observed capture counts remain available.

## Numeric/text semantics and input resources

JSON must contain exactly one value with JSON whitespace only. Duplicate decoded
keys, nonfinite constants, fences/prose/trailing values, wrong kinds, extra or
missing keys, and wrong array order fail the content contract. Objects ignore key
order; strings retain exact decoded contents; bool is never a number.

Both gold and answer use `Number(sign, coefficient, exponent, lexeme)`. Coefficient
zero/trailing-zero normalization and signed decimal-string exponent addition
compare mathematical decimal values without binary float, decimal-context
rounding, integer exponent expansion, Python bool/int aliasing, or huge-integer
conversion. Thus 7, 7.0 and 7e0 agree, while arbitrarily precise differing
fractions remain distinct. Envelope/token integer fields retain their literal
integer-kind requirement. The capture's decoded mirror is checked against its
original JSON numeric representation, but raw bytes are authoritative and answer
text is parsed independently with exact numbers. Unknown fields remain available
in the full raw object; nothing is clipped from the retained response.

Text normalizes CRLF to LF and removes at most one trailing LF from each value.
No stripping, case folding, Unicode normalization, repair, answer extraction,
reasoning concatenation or fence removal occurs.

Before reads, regular-file and no-symlink checks and bounded `read(bound+1)` apply.
The raw body bound is the capsule's bound; one extra overflow byte is preserved
but never scored. Finite metadata storage allowances derive from that bound
(identity: one bound, per-record/request: eight bounds, lane summary: 60 × eight
bounds, initial index: 60 bounds). These are evaluator capacity guards, never
new answer gold: resource-limit failures are `evaluator_error`, not false.
CPython's recursion guard is retained; pathological nesting becomes a labelled
parser resource error instead of an arbitrary answer-depth restriction. The
number algorithm and iterative value comparison do not expand exponents or
recurse over answer trees. There is no arbitrary evaluator plugin interface.

A numeric capture limit has not yet been adopted for the actual field capsule.
The chosen cap's CPU/memory fit and production parser/resource receipts therefore
remain unproved. The focused tests cover long coefficient/exponent lexemes and
recursion exhaustion, not a general hostile-input isolation certification.

## Evidence and reporting

Each slot binds the case-record/prompt hashes, original immutable input-ID vector,
request wire and semantic hash, persisted request/record sidecars, raw response
hash/bytes, model/choice/root input IDs/output IDs/usage, and observed finish
reason. Full raw response and decoded-object references are retained separately
from the final-field JSON pointer. No response-provided path is followed.

Transport failures, bad HTTP envelopes, missing/inconsistent evidence, altered
raw bytes or request identity yield null correctness. For the fixed literal API field, invalid answer JSON and wrong/empty/missing/null
answers are quality failures; reasoning cannot rescue them. A correct final answer at `length` may be
correct while remaining truncated. A stop at the token cap is not automatically
truncated; no absent reasoning count is invented as zero.

Counts distinguish planned, attempted, completed, truncated, not-run, evidence
error, incomplete, evaluated, correct, incorrect, evaluator error, pending blind,
pending isolated-code and comparison-eligible. A content fraction carries its
explicit provisional evaluated-case denominator. The report identifies 720
planned pilot response slots, 60 questions, and exactly 60 slots in this capture;
it neither fabricates missing arms/states nor treats them as independent tasks.

## Focused verification

```sh
python -m unittest discover -s tests -p test_q03_quality_data_score.py -v
```

The synthetic fixture JSON is independently SHA-pinned in the source/report.
Tests cover positive/negative JSON/text semantics, huge decimal exponents without
expansion, bool/number and null/missing, raw/mirror tampering, exact request/input
identity, reasoning/final separation, missing/null/empty/truncated responses,
HTTP errors versus quality failures, code nonexecution, native extraction UNBOUND with literal-field scoring,
parser-resource failures, path/symlink rejection and no subset/overall PASS.
They do not repeat the original 60 gold/reference checks, run models, execute
candidate code, certify isolation, or run the repository full suite.

### Capture interoperability and negative admission checks

The capture identity and summary must retain the same transport-bounds object:
`response_bytes`, `header_bytes`, and (for the owned lane) `request_seconds`.
Request seconds is retained as metadata, not treated as an answer-size ceiling.
The scorer also validates the retained header-pair schema and capsule header-byte
bound. A present Content-Length must be one ASCII decimal value exactly matching
the retained body length; duplicate, malformed or mismatched values are evidence
errors, never quality failures. Non-string/empty request salts invalidate the
primary inventory before uniqueness testing, including unhashable arrays/maps.

The integration fixtures run by default using the committed R2 data subset and
the exact PR8 Git object already fetched by repository CI. The test helper
extracts and verifies its 14 source files locally; it never fetches or substitutes
the current toolbox tree. `Q03_TEST_TOOLBOX` and `Q03_TEST_QUALITY_MATERIAL` are
optional explicit reviewer overrides, not ordinary CI requirements. All 30
focused tests pass with no overrides. The actual pinned source-only PR8 loop and
capture writer use exclusively fake HTTP, then pass the emitted bundle to the
public scorer. The test
uses original prompt bytes only, synthetic token vectors and synthetic responses.
It does not rerun gold/reference assertions or establish live tokenizer/model,
parser, lifecycle or quality evidence. The complete synthetic capture retains 60 valid slots and scores exactly the 44
data cases against their unchanged rubrics. Its deliberately incorrect synthetic
content yields 44 observed failures, not a model result. The eight code/eight blind
cases remain pending, comparison eligibility is zero, the internal final parser
remains UNBOUND, and overall quality remains NOT_EVALUATED.

The partial-capture integration fixture also preserves one truncated wrong data
answer in its denominator, one infrastructure error with null correctness, and
48 NOT_RUN slots. None disappears into a successful-subset result.
