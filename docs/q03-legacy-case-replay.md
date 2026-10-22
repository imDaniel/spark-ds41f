# Finite original warm4K case replay

Status: source/CPU preparation, not a benchmark result. The first-round arms are
`r6x188` and `native197-off`, never DUAL. This module does not launch a service,
modify existing schema 1–4, or weaken owned HTTP/watchdog/drain admission.

## Fixed scope

`scripts/q03_legacy_case_replay.py` admits exactly `warm4k_c1` or `warm4k_c4`, one
case per existing supervised client. Both reproduce the original
`experiments/2026-10-19-steering-gpu/probe_model.py:bench` request/load settings:

- The first 16 unchanged `servebench.prompts.default_prompts()` questions.
- Closed load, concurrency 1 or 4, approximately 4096-token real source documents.
- Fixed 256 outputs; temperature 0 and request/load seed 2048.
- No thinking or chat-template override. In the actual native tokenizer, absent
  thinking flags select its default; the old PR25 thinking-false recipe must not
  be substituted.
- Serial one-output document primers, then the original 15-second warm-up and
 90-second measured window split into 6 subwindows.
- A fresh salt per document slot, reused throughout that slot's run. C1 uses
  frozen slot 0 from the same four-document corpus used by C4.

The exact PR8 toolbox is `c6342a1b6c5e5a98108905a71452a51d6d3a4a87`, with the
complete unchanged inventory from `q03_http_toolbox.json`. The real
`servebench.__main__.main`, `load.run`, `client.stream_chat`, `prom`, and `report`
remain the scheduler, transport and formulas. There is no caller argv or free
concurrency, duration, output length, sampling, corpus-text or import override.

Unloaded and loaded0 are different explicit states even if both native dispatch
paths report STOCK. Loaded1/2/3/4 are separate typed states. Arm and case remain
fixed by the surrounding capsule. Same-boot scale-return sequences are not
implemented by this single-case module and are not claimed by independent boots.

## Corpus before service admission

The historical warm document bytes/IDs were not archived. Seed2048 does not fix
stdlib source bytes across Python installations. Therefore a new comparison must
freeze one real corpus before either arm runs. It is a newly matched A/B replay
of the old workload, not byte-identical historical188 replay.

The separate offline CPU producer uses the existing source-verified native
CPU tokenizer loader and exact PR8 document-generation code. It must retain:

1. All four complete document texts, source/environment identities and SHA256s.
2. Both original raw-tokenizer rescaling inputs and complete returned IDs.
3. All 64 full chat-template input-ID vectors for four documents ×16 questions.
4. Actual model/tokenizer asset pins, existing weights manifest and provenance.
5. The exact sixteen question texts and PR8 digest.

Raw prompt tokenization uses the native `/tokenize` default
`add_special_tokens=True`. Chat tokenization uses its full native default
renderer with no thinking override, `add_generation_prompt=True`,
`continue_final_message=False`, and `add_special_tokens=False` for rendered text.
No ID truncation, stitching, seed-only regeneration, fake length claim, model
import or new model service is a corpus producer.

The replay checks all four raw-ID vectors and all 64 chat-template vectors against
its already owned arm's `/tokenize` endpoint before any primer. It then substitutes
only `load.documents` to return those frozen texts, actual lengths, slot indices
and newly minted per-slot salts. The original loop still performs every primer.
Missing or mismatching IDs stop the case; no approximate-count fallback exists.

`validate_corpus` validates the self-contained immutable corpus. `artifact_files`
returns corpus-production provenance pins; `mounted_asset_files` returns actual
config/tokenizer pins for the existing all-four mounted-file verification. The
production runtime metadata does not impersonate the current arm's identity.

## Owned admission and bounds

The existing root controller owns immutable plan/source admission, actual
four-container identity/readiness, supervisor and descendant ownership,
independent watchdog, epoch sealing, original-service keep-stopped policy,
first-failure teardown and positive all-four service/GPU drain. A timeout is not
quiescence. This client never starts/stops a service or changes fan settings.

The runtime context retains window/attempt/plan/URL, four distinct actual container
IDs/config hashes, the ready-observation hash, and typed admission evidence:

- Four container/rank-bound UTC thermal samples with `0 <= hottest_c < 55`, each
  less than 20 seconds old when priming is admitted.
- A separate original cooling wait between 0 and 600 seconds with unchanged fan
  policy. Cooling is outside the client and not hidden inside the measurement.
- The same-attempt POST `/reset_prefix_cache` receipt after those samples, HTTP 200,
  with its exact response text/hash and completion UTC time. Its parsed body must
  be exactly `{"success":true}` with a boolean true: native reset can return
  HTTP 200 even when held blocks or in-flight offload prevent a reset. The request
  retains the original false defaults for resetting running/external requests.

The client validates thermal freshness both before and after live tokenizer
preflight. Slow preflight fails closed rather than accepting stale temperatures.
This is deliberately a visible readiness risk to evaluate in the first local run;
it must not silently extend freshness or the client deadline.

The client bound is 106–600 seconds. Its internal signal deadline leaves 5 seconds
inside that existing budget for durable failure evidence; the outer fixed
supervisor remains authoritative. Prefix/corpus preparation and request draining
may still exhaust the bound. Such a case is FAILED/INCOMPLETE, not permission to
shorten 15+90, reduce 256 outputs, or extend a lease.

## Evidence and counter accounting

Each run retains original saved `legacy-run.json`, unrounded
`legacy-evidence.json`, fixed `legacy-prompts.jsonl`, and a small result receipt.
The existing client adds its own `client-receipt.json` outside this module.

Every priming and timed request retains the actual input IDs, original payload,
actual wire payload, response SSE bytes, parsed response/reasoning text, observed
output IDs if returned by the original request, and original timing/usage/error.
The module does not change the request to demand output IDs. If native streaming
does not return them, the field explicitly says
`NOT_RETURNED_BY_ORIGINAL_REQUEST`. Complete input IDs and actual response bytes
remain available for later review; absent output IDs are never reconstructed.

The original 9 metric scrapes are retained: a post-prime initial fence, 7 measured
boundaries and one final-drain fence. A separate pre-prime audit scrape records
primer work without including it in original measurement formulas. Required
native model/engine-labelled counters include cache/steps/acceptance metrics;
missing, foreign, duplicate, reset or nonfinite series fail closed. Accounting
checks primer counts separately, then timed counts, then their combined totals.
Variable actual prompt counts are summed; no request is assumed 2048 tokens.

Saved summaries are re-derived from exact pinned PR8 reporting source and
unrounded raw records. The original rounded saved request shape is checked too.
The original TTFT/TPOT, six-window throughput, acceptance, prefix-hit and other
available formula definitions are unchanged. Within-run subwindows are not six
independent service boots or evidence of statistical noninferiority.

The first failed primer, timed request, live-ID verification, counter check or
hard deadline latches admission closed. Partial evidence is retained and the
existing supervisor must finish teardown/drain before any next case.

## Result scope and remaining dimensions

A validated result says `COMPLETE_ORIGINAL_CASE_CLIENT_ONLY`. Performance,
quality and capacity remain `NOT_EVALUATED`; lifecycle still requires the owned
four-rank terminal/drain chain. Exit 0 is not old quality PASS and is not permission
to merge/promote a candidate.

`coverage()` explicitly retains gaps for original same-boot return sequences,
release 256, sampled/Poisson, cold/long prefill, near 1M capacity, model 256 quality
and new semantic quality. DUAL is outside this first round. A 2K/16 or 2K/64 screen
cannot fill these rows or substitute for this 4K/256 case.

## Focused CPU verification

Run only the changed focused tests:

`python -B -m unittest discover -s tests -p test_q03_legacy_case_replay.py -v`

Tests extract the exact toolbox from locally present immutable Git objects, check
its inventory, and execute its real main/load/client/report against a localhost
HTTP fake. Character IDs and accelerated clocks are explicitly synthetic. Tests
cover C1/C4, primers/cache salts/fences, all typed states/argv, corpus/token drift,
thermal/reset mutations, response closure, native-counter failures, failed-primer
and failed-timed-request admission stops, and actual signal timeout handling.
They are not model, performance, capacity, quality or remote-machine evidence.
No full benchmark suite, deployment or PR publication is performed by these tests.
