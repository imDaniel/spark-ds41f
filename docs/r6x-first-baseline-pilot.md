# First A188 / B197-Q03-OFF baseline pilot

## Scope and prerequisites

This freeze composes the fixed two-arm identity observer, schema-6 HTTP bridge,
original warm4K case replay, tokenizer-only cloud preparation and separate site
binding, and offline A/B reader. It does not rebuild either native image or
modify their source. Both arms have Q03 OFF. This is a selected-case protocol
pilot, not a full baseline acceptance, Q03 causal comparison, quality result,
capacity qualification or promotion.

Start with `warm4k_c1`, steering `unloaded`, A188 then B197-OFF. Retain each
first result, including failures. Close and positively drain A before B. Never
start B when A's exact process/container/GPU drain is UNKNOWN. A later C4 or
loaded-dose case needs its own immutable selected-case capsule; no hidden
scheduler advances selection. The broader approved matrix and semantic-quality
pilot remain separate work, not silently removed from acceptance.

The separate executable sixty-case A/B unloaded quality entry is documented in
[`r6x-first-quality-pilot.md`](r6x-first-quality-pilot.md). Its schema-7 source
reuses this lifecycle but requires its own site-bound corpus and new capsule;
it does not turn these warm4K performance receipts into quality evidence.

Use a new clean exact freeze checkout on all four nodes. Keep every earlier
source, helper, capsule, journal and receipt unchanged. The existing safe helper
and original asset/protection/identity authority are revalidated, not rewritten.
Original registered services stay stopped on normal completion and recovery.
The same existing watchdog, heartbeat, locks, fenced epochs and positive drain
are required; this document is not permission to bypass them.

## Prepare inputs without model execution

1. Retain each arm's actual PR30 static capture and raw image inspections.
   The capture validates source/ELF/package bytes without importing a model;
   running four-rank readiness is still a separate later proof.
2. Use one tokenizer-only cloud preparation and its distinct provenance, then
   run the reviewed `bind-site` command from `q03-legacy-cpu-corpus.md` with full
   real site inputs. Do not run the document generator again at the site.
   Binding compares every frozen calibration/chat ID vector and verifies the
   complete retained weights manifest without rereading large weight bytes.
3. Use the final `corpus.json` path/hash identically in both arms. Corpus CPU
   provenance may identify native197; it is not A188 runtime evidence. Keep
   that record separate from each arm's captured static provenance and runtime
   manifest. Each actual endpoint must independently verify all four
   final-document raw ID vectors and all 64 complete chat-input ID vectors
   before measurement.
4. Keep original PR8 toolbox `c6342a1b6c5e5a98108905a71452a51d6d3a4a87`
   in an exact clean independent checkout. Current controller scripts are not
   a substitute for that toolbox inventory.

## Immutable private manifest delta

Reuse the existing registered policy, node identity, protected assets, lease,
normal adapter, safe helper and keep-stopped boundary. Do not publish site files.
The schema-6 contract is documented in `q03-baseline-service-bridge.md` and
`q03-baseline-identity.md`; all required actual path/hash facts must be supplied.

The single job has this shape:

```json
{"kind":"http_service","timeout_seconds":7200,
 "selection":{"arm":"r6x188","steering":"unloaded"}}
```

B uses `native197-off`, never relabeled A provenance. `http_service.schema_version`
is 6. `legacy_case` selects `workload: legacy_warm4k_v1`, `case_id: warm4k_c1`,
the actual shared corpus path/hash and actual native metric labels. The remaining
HTTP fields are exactly `baseline_inputs`, `baseline_identity`, `toolbox`,
`api_port`, `startup_seconds`, `client_seconds` and `legacy_case`.

Candidate fields are exactly `image`, `component_head`, `runtime_manifest_path`,
`runtime_manifest_sha256`, `pytest_base_pythonpath`, `cache_path`, `output_path`.
There is no borrowed owner/G1 fixture, owner-matrix receipt or historical build
receipt. Do not add legacy candidate fields to satisfy unrelated old schemas.
Separate code/cache/output/session inputs must pass the current canonical,
owner-controlled, no-alias and protected-root rules on all four nodes.

## Existing execution entrypoints

These are templates, not filled site commands. Use actual absolute paths and
values returned by the preceding command. `PYTHON` is the registered interpreter;
use `-B`, not `-I`, for the existing controller's sibling imports.

Keep `run-session --apply` running in its existing supervised invocation; issue
`status`, `plan-attempt` and control requests from a separate control shell.
Do not wait for the supervisor to return before planning/resuming; retain its
eventual normal exit after close.

```sh
"$PYTHON" -B "$DEPLOYMENT/scripts/q03_window.py" run-session "$MANIFEST" --session-dir "$SESSION"
"$PYTHON" -B "$DEPLOYMENT/scripts/q03_window.py" run-session "$MANIFEST" --session-dir "$SESSION" --apply
"$PYTHON" -B "$DEPLOYMENT/scripts/q03_window.py" control status --session-dir "$SESSION"
"$PYTHON" -B "$DEPLOYMENT/scripts/q03_window.py" plan-attempt --session-dir "$SESSION" --through http_service
"$PYTHON" -B "$DEPLOYMENT/scripts/q03_window.py" control resume --session-dir "$SESSION" --expected-sequence "$FRESH_SEQUENCE" --attempt "$RETURNED_DESCRIPTOR_PATH" --attempt-sha256 "$RETURNED_DESCRIPTOR_SHA256" --apply
```

Do not pass owner-mode/owner-steering options to this fixed selection. Wait for
actual owned held-ready admission and watchdog readiness before resume. The
client keeps the original 15-second warmup, six 15-second measured windows,
256-output payload, original question/seed/template defaults, raw evidence and
native counter checks. Shared documents are newly frozen original-generator
outputs, not the unavailable historical document bytes and not forced/padded
exact 4096 IDs. The <55 C, fresh all-four thermal gate and successful prefix
reset precede measurement; reset HTTP 200 without `success: true` is rejected.

After a terminal result and positive all-four drain, fresh-read status/sequence
and use the supported close request. Do not manually alter state or release hold.

```sh
"$PYTHON" -B "$DEPLOYMENT/scripts/q03_window.py" control status --session-dir "$SESSION"
"$PYTHON" -B "$DEPLOYMENT/scripts/q03_window.py" control close --session-dir "$SESSION" --expected-sequence "$FRESH_SEQUENCE" --apply
```

Require actual `closed-stopped`, original-kept-stopped, hold release, final
protection/GPU observations and normal supervisor completion before next arm.

## Read results, do not promote

Use `q03_legacy_pair_report.py` only after both closed sessions exist, as described
in `q03-legacy-pair-report.md`. It reuses original PR8 definitions and retains all
raw lineage. One boot pair remains descriptive: six time windows are not six
independent boots. It cannot conclude quality, capacity, noninferiority or an
optimization gain. A188/B197-OFF is a version-package comparison, not Q03 effect.

## Verification ledger

The assembled Linux full test run executed 1136 tests; 1132 passed and four
fixture cases correctly hit the immutable-source/output overlap guard because
CI places TMPDIR inside the checkout. The two test files now use a separate
fixture toolbox leaf: all 29 affected tests pass under that same CI layout.
Production guards and genuine corpus bytes were not changed. Earlier sparse
checkout/example-site setup failures and before/after logs remain retained.
Final repository CI is required for the published exact tree; focused tests and
mock daemon/network tests are not field execution. No A/B model-performance run
or semantic-quality acceptance is claimed by this source delivery.
