# 1022 parser byte-token alignment

The preceding candidate passed all 73 release component files (1,995 passed,
zero failures/errors), but its first real tool call still lost the literal
`<｜end▁of▁sentence｜>` inside a nested JSON value. The preserved generated token
IDs prove the model emitted the value as ordinary tokens. The registered parser
reproduces the loss on that exact response in a CPU-only replay.

Two parser defects participate. The earlier repair carries generated IDs into
complete-output parsing. This successor fixes byte-token alignment: individually
decoded UTF-8 fragments contain replacement characters, so the scanner's trailing
stop match fails and its fallback binds the stripped EOS to the literal spelling
inside the argument. Decode affected contiguous text spans together, preserving
their original per-token accounting. Actual stop terminals remain structural.

The new `0500` patch follows all 200 published patches without rewriting them.
Only the scanner and its tests change. No GPU kernel, native input, model weight,
attention arithmetic, sampling, launch parameter or input template changes. The
profile remains `e06b8cb6…`; another profile rebind is unnecessary.

## Validation and window

The four added byte-fallback cases produce two failures on the previous image
and all pass after repair. All 4,379 parser-engine tests and the captured response
replay pass in the exact new image, with no GPU devices or network exposed.
The build audits every source file and confirms all 32 native libraries match
the parent. Source head/tree/patchset are independently replayed from the lock.

The next window reuses the just-completed 73-file GPU component receipt. Its
scope guard rejects any source change outside the scanner and its test, changed
native inputs, an altered/failed component receipt, or an incomplete exact-image
parser gate. The candidate request plan retains every tool, quality, cache,
capacity and stability assertion. Historical U/E/r6x comparisons are reused;
there is no fresh comparative performance claim.

Every candidate generation explicitly selects profile and scale. The pinned
recovery executor and independent watchdog restore the original four-node r6x
service. PR #56 remains draft/unmerged; qualification does not promote a service.

[The preceding failed request and successful recovery](runs/attempt-3/README.md)
remain immutable evidence. Request acceptance of this successor is pending.

Subsequent qualification completed in two RC3 windows. See the
[same-image acceptance and recovery results](../2026-10-23-1022-capacity-validation/RESULTS.md)
for the actual tool/quality/cache/1M/concurrency/stability gates and their scope.
