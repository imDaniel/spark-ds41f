# First A188 / B197-OFF quality pilot entry

This is the executable schema-7 successor to the schema-6 warm4K pilot, using
the same owned controller, fixed arm identities, guards, watchdog, private
controls/logs and positive four-rank terminal drain. It does not add a launcher,
model image, generic command runner, automatic rebuild or baseline promotion.
The first quality pair is **A188 unloaded, then B197-OFF unloaded**. The existing
loaded0..4 selections remain available for later approved matrix cells; their
presence in source is not actual applied-scale evidence.

The controller composition starts from the PR38 tree
`6698d7765dd27719c5a21aca7a51f593d85103f1` at
`f5d8a057de549b4050ac3f136180c9d3f62008f4`. This is also the tree of the earlier
local controller `f98e197e5c0aa5c050ee93b0caeeb8a0e9029ade`; the quality source
composition therefore does not discard PR38's control/log lifecycle fixes.
Use the exact newly reviewed publication for both controller and recovery
inventories, never files substituted into an existing capsule.

## Inputs required before an executable site capsule can be frozen

- One clean, independent published controller checkout at the reviewed source
  SHA, plus the unchanged exact PR8 toolbox checkout
  `c6342a1b6c5e5a98108905a71452a51d6d3a4a87` and its checked source inventory.
- Original 60-case R2 material archive, including all 34 checksum members, not
  only the public test-fixture subset. The generation-only prompt SHA256 is
  `8cdcf6143d80d4776c052166a9764d36d8b5020038ad24118e352f3493cf4ca0`.
- The unchanged genuine portable preparation and its complete provenance:
  preparation SHA256
  `30e0d4ff22ccd71f6f0e64d47c6fd7a63077a2e48cae1d408dffbc069457472a`,
  content SHA256
  `1bf8c8b268647d2ab8368585e85d4b5971a4f908c4067b4dc902d9412f9a0389`.
  It contains 60 complete rendered texts and 11,460 input IDs. It remains
  `NOT_REPLAY_ADMISSIBLE` until the separate full site binding succeeds.
- Actual full legacy/native CPU bindings, all original 83 manifest members,
  tokenizer/config/native-source assets, retained runtime metadata, and the
  compatible installed tokenizer packages. Existing versions must match the
  original pins; a mismatch does not authorize package installation or a rebuild.
- The actual site-bound `corpus.json` and `site-link-receipt.json` returned by
  the command below. Do not reuse a warm4K corpus or predict these hashes.
- Each arm's own static image/provenance and runtime-manifest bindings. Existing
  8ad0da... / 90bd2b... images are reusable only if the actual binding validates;
  neither a tag nor the other arm's receipt is proof of compatibility.
- The existing registered policy, physical node identities, protected roots,
  original-service keep-stopped rule, authority, safe helper, private paths and
  held-window input contracts, revalidated unchanged.
- Actual positive integer `request_seconds`, `max_response_bytes` and
  `max_header_bytes`, explicitly reviewed and frozen in this quality capsule.
  Test fixture numbers are not runtime defaults. No such numeric choice is made
  by this source delivery. The existing 106..600-second client, 7,200-second job
  and 10,800-second outer limits remain unchanged, with a five-second client
  receipt reserve. A full 60-answer fit is not established by CPU tests.

## Minimal commands and fixed selection

All uppercase names below denote actual resolved absolute paths or returned
values. `PYTHON` is the existing registered interpreter. The site binder performs
CPU/tokenizer work only; it does not contact the generation endpoint or start
services. Do not rerun preparation, document/material generators or reference
code to manufacture replacement inputs.

```sh
"$PYTHON" -B "$DEPLOYMENT/scripts/q03_quality_portable_corpus.py" bind-site \
  --preparation "$QUALITY_INPUT/portable-preparation.json" \
  --preparation-sha256 30e0d4ff22ccd71f6f0e64d47c6fd7a63077a2e48cae1d408dffbc069457472a \
  --bindings "$FULL_SITE_BINDINGS" --materials "$QUALITY_INPUT/materials" \
  --output-directory "$NEW_SITE_OUTPUT"
```

Require the returned site receipt to report 60 complete ID/rendered-text
comparisons, no regenerated prompts, and
`SITE_CPU_INPUT_BINDING_NOT_FIELD_QUALIFICATION`. Preserve the original
preparation/provenance separately. The new output must be disjoint from all
immutable inputs; do not repair asset permissions or weaken path validation.

Build a new private manifest using the existing baseline manifest contract:
`http_service.schema_version=7`; replace only `legacy_case` with
`quality_pilot` containing exactly `workload=quality_pilot60_v1`, the actual
canonical corpus path/hash, and the three reviewed integer bounds above. The
single job remains `kind=http_service`, with selection
`{"arm":"r6x188","steering":"unloaded"}`. B's separate manifest selects
`{"arm":"native197-off","steering":"unloaded"}` and its own arm identity.
All other normal baseline fields remain mandatory; no owner/G1 receipt, build
fixture or borrowed provenance is introduced.

Run one existing supervised session per selected arm. The first command is the
non-apply plan; keep the second running in its existing supervised invocation
while issuing control requests from a separate shell:

```sh
"$PYTHON" -B "$DEPLOYMENT/scripts/q03_window.py" run-session "$MANIFEST" --session-dir "$SESSION"
"$PYTHON" -B "$DEPLOYMENT/scripts/q03_window.py" run-session "$MANIFEST" --session-dir "$SESSION" --apply
"$PYTHON" -B "$DEPLOYMENT/scripts/q03_window.py" control status --session-dir "$SESSION"
"$PYTHON" -B "$DEPLOYMENT/scripts/q03_window.py" plan-attempt --session-dir "$SESSION" --through http_service
"$PYTHON" -B "$DEPLOYMENT/scripts/q03_window.py" control resume --session-dir "$SESSION" \
  --expected-sequence "$FRESH_SEQUENCE" --attempt "$RETURNED_DESCRIPTOR_PATH" \
  --attempt-sha256 "$RETURNED_DESCRIPTOR_SHA256" --apply
```

Resume only after actual held-ready admission and watchdog readiness. Do not
pass owner-mode/owner-steering options: the arm and state are immutable manifest
selections. This fixed client first verifies all 60 live `/tokenize` responses,
then captures 60 serial natural-EOS generations with the unchanged 2,048-ID
ceiling and no thinking override. It retains five bounded metrics receipts.
No hidden segmentation, retry, budget shrink or lease extension is performed.

After terminal evidence and positive all-four drain, fresh-read status and close:

```sh
"$PYTHON" -B "$DEPLOYMENT/scripts/q03_window.py" control status --session-dir "$SESSION"
"$PYTHON" -B "$DEPLOYMENT/scripts/q03_window.py" control close --session-dir "$SESSION" \
  --expected-sequence "$FRESH_SEQUENCE" --apply
```

Require actual `closed-stopped`, original-kept-stopped, released hold, final
protection/GPU observations and normal supervisor completion before B. Unknown
drain retains the hold and blocks the next arm. A failed first attempt remains
evidence; schema 7 does not rerun it within the same capsule.

## Read the retained evidence without promoting it

The owned client creates raw response/tokenization bytes, requests, all 60 slots,
context, metrics, PR8 output and receipt privately. The standalone scorer also
creates a new 0600 report independently of inherited umask and refuses to
overwrite an existing report. Its output is outside the immutable capture and
materials, with the exact bound copied from the frozen manifest:

```sh
"$PYTHON" -B "$DEPLOYMENT/scripts/q03_quality_data_score.py" \
  --materials "$QUALITY_INPUT/materials" --capture "$RETAINED_QUALITY_CAPTURE" \
  --max-response-bytes "$FROZEN_MAX_RESPONSE_BYTES" --output "$NEW_DATA_REPORT"
```

This reports **44 literal API-content observations, 8 isolated-code evaluations
pending and 8 blind reviews pending**. Original raw responses remain authoritative.
Applied state stays `UNVERIFIED`, internal final-answer extraction stays
`UNBOUND`, overall quality stays `NOT_EVALUATED`, and comparison eligibility stays
false. A capture gate named `passed` or a scorer CLI exit 0 is not quality PASS.
No 600-question formal set, new threshold, actual scale proof or all-18-state
coverage is created by this entry.

## Source verification

`test_q03_quality_entry.py` exercises the actual `python -I -B` client CLI against
a synthetic loopback HTTP endpoint for both unloaded selections under inherited
umasks 000/002/022/077. It checks full raw bytes, source-loader dispatch, 60 live
vectors, 60 generations, 5 metric receipts, the real offline scorer CLI, private
file modes and immutable report overwrite refusal. A tokenizer mismatch retains
60 NOT_RUN slots and admits no generation request. These are CPU/transport
integration checks, not native runtime or model-quality evidence.

See `q03-quality-owned-lifecycle.md`, `q03-quality-cpu-corpus.md` and
`r6x-quality-api-content-contract.md` for the unchanged detailed contracts. The
historical composition checkpoint documents earlier source evidence, not a new
runtime execution claim.
