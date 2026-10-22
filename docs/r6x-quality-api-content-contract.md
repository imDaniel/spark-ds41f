# Literal API-content interpretation, version 1

This amendment is declared before inspecting any real model response. It applies
to report schema `r6x-quality-data-score/2`, with the unchanged R2 case inventory,
gold, rubrics, generation settings and evidence-admission checks.

The fixed score target is the complete literal API value at
`/choices/0/message/content`, selected by `kind=literal_api_field`. This is an
observable endpoint response, not a claim of verified internal model-final-answer
extraction. No configuration option may select another field or extraction rule.

Already-admitted responses enter the existing 36 strict-JSON and eight exact-text
kernels. Never concatenate reasoning, repair JSON, strip prose/fences, extract a
trailing answer or recover a different answer from another field. If reasoning is
mixed into the content field, the entire delivered field is assessed against the
existing rubric. Missing/null/empty content is `NO_API_VISIBLE_ANSWER`, not proof
that an internal final answer did not exist. Non-string content remains a schema
issue with null correctness. Correctness and reported truncation remain separate.

This clarifies the stronger native-extraction condition in the original evaluator
contract (SHA-256
`366045261663fd4729e9d10fe3b927dbadd23351fc66a41a8d38e17b8fc88e76`):
internal native reasoning/final separation stays UNBOUND, but does not gate this
predeclared literal-field observation. It does not weaken the frozen JSON/text
assertions or the separate transport/material/request/input-ID checks.

The source rationale is native serving.py SHA-256
`ea1f76074a9587c8054f54d30a6ba748b6a5fc4d90dd82c801504505c1da1f92`:
lines 969–985 assign content from parser output or output.text; lines 1002–1004
place it in the assistant message; lines 1083–1085 attach the message to a choice;
lines 1163–1173 construct the returned choices/model/token evidence. This read-only
source observation does not prove which branch/configuration ran in the field.

All 60 slots and the 44 planned data-case denominator remain explicit. Eight
Python cases require verified isolation; eight open cases require independent
blind reviews. Infrastructure errors and missing responses are never wrong-answer
scores. Runtime/template/defaults, native parser, applied scale, lifecycle and
paired-comparison proof stay separate. Overall quality remains NOT_EVALUATED and
comparison eligibility remains false in this scorer. No underlying model-capacity,
causal-dose, formal noninferiority or deployment/default acceptance follows.

Raw capture files, including `semantic_scoring=NOT_EVALUATED`, are never mutated.
