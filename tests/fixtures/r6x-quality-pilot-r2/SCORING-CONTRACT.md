# Scoring contract for review

No candidate outputs have been produced or scored. This document specifies interpretation of the material; it is not a new benchmark runner.

## Deterministic contracts

1. strict-json-equality: require exactly one finite RFC 8259 JSON value, with no surrounding prose or Markdown fences. Whitespace around JSON is allowed. Reject duplicate keys, NaN, Infinity, trailing extra values, missing/extra keys or wrong container/scalar kinds. Object-key order does not matter; array order does. Strings must decode to the specified Unicode contents. Boolean true/false is never equivalent to a number. Null is distinct from missing. JSON numbers compare by exact mathematical decimal value, so 7 and 7.0 represent the same required numeric value, but 7.01 does not; numeric strings are not numbers. Integer-valued requirements must be mathematically integral. No approximate floating tolerance is introduced for this finite supplied gold. Fractions are requested as strings, so their reduced spelling is part of the task.
2. exact-text: normalize CRLF to LF and remove at most one terminal newline. No other whitespace/case/punctuation normalization. Apply only the constraints stated in each prompt, not arbitrary stylistic preferences.
3. python-unit-assertions: assess the requested function contract and all supplied fixture calls. Preserve input to check nonmutation where requested. Python return value kinds must match, including bool versus int and nested list/dict types. Check requested signature/type hints and output-source constraints in addition to test results. For code-007, every returned dictionary must be distinct by identity from every input dictionary, as requested by its prompt. Nested objects may be shared; no deep copy is required. Valid equivalent implementations need not match the reference source or algorithm. The reference oracles and tests are evaluator-only, not answer text to feed the model.
4. Candidate Python is untrusted. Do not import it in this material-check process. Use the existing approved isolated code evaluator if available, with its own execution/resource contract. Until that path is bound and verified, candidate code remains NOT_EVALUATED; no AST-only check or trusted oracle success substitutes for model unit-test success.
5. Deterministic correctness does not erase extra diagnostics: preserve truncation, empty output, malformed format, repetition, irrelevant answer, unsupported claims and benign refusal separately. An incomplete answer is not dropped from the denominator.

The individual JSON-case assertion strings use “wrong scalar types” in this sense: JSON number versus string/bool/null, not Python's int-versus-float representation of a mathematically equal JSON number.

## Eight open cases

Use rubric.json and the exact per-case anchors. Two independent reviewers see prompt, anonymous response and frozen rubric, but not arm, scale, timing, output filenames or another reviewer's judgment. Randomize paired left/right order using a recorded deterministic seed after responses exist; keep the mapping separately. Do not invent anonymous response IDs before there are responses.

Each reviewer records content 0–2, constraints 0–1 and unsupported-claim handling 0–1, plus an explanation referencing the response. The total is a descriptive 0–4, not an automatic quality PASS. Preserve both reviews, disagreement, tie and any adjudication. AI reviewers must be explicitly labelled AI with model/version/prompt settings. Human review must not be claimed unless it actually happened. For instructions-010 (legacy d204), the prompt already establishes an imaginary gadget; do not require an explicit fiction disclaimer or treat ordinary in-universe product/availability copy as an unsupported real-world assertion. If only one review exists, mark second review pending and do not call the blind-review chain complete.

A benign request answered incorrectly is not a benefit merely because it was not refused. Conversely, correct null/unknown for missing supplied information is not refusal. A short relevant caution accompanying a useful answer is not automatically overrefusal. Label unclear cases for review rather than using refusal-word substring matching as gold.

The two exact legacy code prompts allow self-chosen functions and tests. Review relevance, function/type hints/docstring and the two concrete tests against the chosen contract. They cannot be scored by forcing the candidate to match an invented reference implementation. Any execution of those generated tests also requires the approved isolation path.

## Reporting and statistics

Report per case and per arm/state: correctness or rubric components, refusal annotation, instruction/format issues, unsupported claims, missing/truncated/error status, actual response length and finish reason. For every same-state B-minus-A comparison, retain the paired outcome and disagreement count. Also report steering states separately rather than assuming stronger scale is better.

The pilot's 60 tasks include related task families and authored fixtures. It calibrates methods; it is not a representative population sample or proof of formal independent-task power. Repeated states, outputs, seeds or reviewers are not new independent questions. Use no performance number, token match or material-sanity exit code as semantic quality evidence. Do not adopt an automatic default-scale change, retention decision or new user feature restriction from this package.
