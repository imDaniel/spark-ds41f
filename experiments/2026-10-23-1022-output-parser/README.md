# 1022 output parser release candidate

Status: preparing and validating a downstream release candidate. PR #56 remains
draft and unmerged; the retained r6x service is the recovery target. This record
does not claim GPU acceptance or promotion.

The failed literal-argument request is preserved in
[the preceding window](../2026-10-23-1022-scale-results/README.md). The registered
DeepSeek V4.1 non-streaming parser accepted generated token IDs but discarded
them while splitting reasoning from tool extraction. Its text-only fallback
then removed ordinary characters spelling `<｜end▁of▁sentence｜>`. The five
relevant parser files are byte-identical in the retained r6x and original 1022
images. The new regression collection fails 19 cases in each image; six streaming
cases already pass. This is an existing upstream parsing defect, not a newly
introduced steering calculation or 1022 input-encoder regression.

The single new patch selects a token-aware complete-output pass only when both
registered adapters are DeepSeek V4.1. Existing streaming adapters and mixed
parser combinations remain intact. The pass preserves thinking controls,
reasoning token accounting, schema handling, JSON forced-tool conversion and
removal of actual special tokens. It does not change model weights, sampling,
GPU kernels, request scale, the input encoder or the attention operator.

The 198 preceding patches are immutable references. [patch-map.json](patch-map.json)
records their hashes and the appended `0500` patch. Source head is
`9dd408b8ee85eec4491ce7176dc50dd7c87afe22`, tree
`2ac30bad166054360075fc2def3143e76f3757c2`.

## Evidence and remaining gates

- CPU-only execution in isolated containers from the real serving images:
  [r6x](runs/r6x-before.txt) and [1022](runs/before.txt) each fail the same 19
  regression cases. No GPU devices or network were exposed to these containers.
- With the repair, the entire parser-engine collection passes:
  [4,375 passed](runs/after-full.txt), including the 25 new cases. This exercises
  real request protocol objects and the registered parser manager.
- CI replays this 199-patch stack independently, executes the pinned 126 steering
  CPU contracts and checks complete release-test classification. Parser-engine
  execution is a local image gate, not a claim about the minimal CI environment.
- The exact distributed image passes 4,375 parser, 126 steering CPU and 66
  encoder tests (including all 28 real-checkpoint cases), with no skips.
  [Receipts](runs/exact-image-cpu/) also cover 16 real-checkpoint parser
  combinations and unchanged profile binding/payload. The first checkpoint
  helper omitted the special-token configuration; its failed receipt is retained.
  Loading the complete checkpoint tokenizer fixes that fixture without changing
  runtime code or the image.
- Build input/source checks and distribution identity checks pass. The release
  component gate, explicit-scale GPU requests and capacity gates remain pending.
  [The first qualification attempt](../2026-10-23-1022-release-readiness/runs/attempt-1/README.md)
  stopped before component execution because the harness omitted its Docker
  executable pin; automatic recovery restored the original four-rank service.

The profile remains `e06b8cb60f3f382157ef6632a93efdd90185beafa2251a8c29575f09c45cd45b`:
its model and input-template binding did not change. The earlier rebind receipt
remains historical evidence; a new output-parser source tree alone does not
require changing the artifact or its direction payload.

All generation tests will explicitly carry a profile and scale. Existing U/E/r6x
comparison data is retained as historical reference, without a fresh comparative
performance or no-regression claim. Successful qualification does not authorize
merging PR #56 or promoting the service; owner acceptance remains separate.
