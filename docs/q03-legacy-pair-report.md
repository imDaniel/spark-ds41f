# Selected legacy A188/B197-OFF comparison

`scripts/q03_legacy_pair_report.py` is an offline reader, not a runner or a new
benchmark framework. It consumes one actually completed `warm4k_c1` or
`warm4k_c4` selected-case session per arm, at the same steering state. It performs
no network requests, subprocesses, Docker operations, model imports, service
changes, scheduling, retry selection or publication.

## API and retained inputs

```python
compare_pair(a188_attempt_directory, b197_off_attempt_directory)
```

```sh
python -B scripts/q03_legacy_pair_report.py \
  /retained/a188-session/attempts/ACTUAL_A_ATTEMPT_ID \
  /retained/b197-session/attempts/ACTUAL_B_ATTEMPT_ID > comparison.json
```

Use canonical absolute paths from the actual retained sessions. The original
capsules/plans bind their paths, original PR8 toolbox, source inventory, frozen
corpus, provenance and client result locations. This reader does not relocate,
rewrite or rehash them to fit a different host. Missing retained paths are a
visible intake failure. It neither fetches missing artifacts nor accepts a
caller-supplied `passed` declaration as a replacement.

Read with the source revision that created the sessions. The normal existing
recovery-source equality check applies even though no recovery action is run.
This file is an independent analysis tool and is not recovery/execution authority.

Use a fresh interpreter for the CLI/API, without already imported task helpers.
The reporter scopes initial and late helper imports to their sibling `.py` source,
uses an absent bytecode-cache prefix, and rejects preloaded/replaced helper modules.
`-B` alone is not a cache-read defense. Import-path/extension shadows and existing
`.pyc` files cannot substitute for the protected helper sources. Library calls
restore the caller's bytecode settings, import hooks and import path on success or
failure; there is no production bypass for the fresh-process checks.

Each arm must contain exactly one passed selected-case attempt, its unchanged
capsule and descriptor/admission chain, supervised-client receipt and raw legacy
output/evidence, original response bytes and token IDs, four-rank ready identity,
controller-pinned admission context, complete HTTP journal, exact-container rank
logs, positive daemon/log drain, four-rank terminal quiescence, and final
`closed-stopped` / hold-released closure with a retained successful four-node
GPU-drain preflight observation after client completion. An open session, failed
attempt, missing/partial log, unknown drain, foreign window/attempt/container or
selected retry is rejected. The report does not cause a session to close.

Existing validators are reused: Session, outer/history and admitted-plan checks;
the schema6 plan generator and baseline readiness validator; the original-case
`validate_output`; existing exact-container and retained-log validators; and the
original final-drain preflight validator. Fixed profile/node/effective-config
bytes and actual per-rank Docker resources are compared. Expected arm-specific
runtime/source identities and per-boot bind paths are not forced equal.

A188 and B197-OFF must have different owned boot/container identities, no overlap
in their client intervals, positive A final drain before B client start, the same case and steering state, complete frozen
corpus and shared semantic assets, and matched effective/resource configuration.
The reader preserves thermal admission observations. Passing the original
fresh/<55°C entry gate does not establish matching thermal trajectories.

## Statistics and units

The original exact PR8 `c6342a1b6c5e5a98108905a71452a51d6d3a4a87`
`servebench.report`, `prom`, and `stats` definitions remain authoritative. The
legacy producer re-derives its saved summary from unrounded raw evidence before
this tool reads it. The report preserves that summary unchanged, including its
original six-window samples, rounding and within-run intervals. It does not call
`report.compare`, Welch, or use those intervals as between-boot confidence.

- Output TPS: original mean over six 15-second measured subwindows after the
  original 15-second warmup. Units: output tokens/s.
- TTFT: first minus sent, seconds. TPOT: `1000*(end-first)/(tokens-1)`, ms/token.
  E2E: end minus sent, seconds. Original means/p50/p90/p99 and per-user TPS remain.
- Latencies use the original request-sent-in-`[w0,w1)` subset, not all completed
  requests or just responses completed within the window.
- Additional p95 is explicitly labeled and uses the same frozen nearest-rank
  `stats.percentiles` on those unrounded per-request observations. It does not
  estimate p95 from the rounded saved rows or pretend p95 was in the old output.
- Errors/rejections/foreign/approximate-time counts keep original whole-run scope.
- Cache hit rate remains a fraction, not a percentage; server subwindow means,
  step/token/draft statistics and original sample counts are retained.
- Paired changes are `B-A` and `100*(B/A-1)`. A zero baseline has an undefined
  relative change, not zero improvement. Absent ratio denominators remain
  explicit `UNAVAILABLE`; required missing/reset/foreign native metrics reject
  the original-case input rather than becoming zero.

One pair is descriptive only. The six subwindows are not six independent boots.
The output has `independent_boot_pairs: 1`, no paired-boot CI, performance
`INCONCLUSIVE`, statistical power `UNKNOWN`, and quality/capacity
`NOT_EVALUATED`. Proposed 3% throughput and 5% p95-latency flags only say whether
an observed point estimate merits investigation. No flag is a noninferiority,
quality, performance-gain, promotion or keep/discard decision. Tail sample counts
are visible; no minimum count is misrepresented as sufficient power.

A188 versus B197-OFF measures the version-package difference. It cannot isolate
Q03, because both runs are OFF and their runtime identities differ. The fresh
shared warm4K corpus is not the unavailable original188 document bytes. One case
and scale is not a full matrix, release, long-prefill, capacity or quality test.
A separate real semantic-quality evaluation remains required; the reporter does
not accept arbitrary quality summaries or treat exit 0 as quality PASS.

## Why not use the paired Q03 acceptance validator?

The existing PR19-era offline `servebench.paired_performance.evaluate` accepts
its own preregistered OFF/DUAL eight-boot/four-pair contract and external control
prerequisites. Relabeling A188/B197 or six subwindows to satisfy it would change
the experiment. This small reader reuses the original measurement definitions
without duplicating its statistical acceptance framework or claiming its gates.

Hashes and coherent receipts establish retained byte lineage, not independent
attestation of physical observations or exhaustive evidence of every external
attempt. Field review still establishes provenance and physical execution.

## Focused CPU verification

```sh
python -B -m unittest discover -s tests -p test_q03_legacy_pair_report.py -v
```

Tests use the unchanged PR8 reporter/stats, original producer with a localhost
synthetic HTTP service and accelerated clocks, and existing actual HTTP
lifecycle/log validators over fake daemon observations, plus retained session/history
and fixed-plan regeneration checks. Synthetic rows do not
become field performance, GPU drain, model quality, or capacity evidence.
Fresh subprocess regressions use genuine timestamp-valid poisoned caches,
initial/late helper and extension/import-path shadows, preloaded/replaced/negative
module-cache entries and caller-state restoration. Shared synthetic unit fixtures
explicitly mock only the fresh-process guard; subprocess regressions do not.
No GPU, remote machine, full benchmark suite or publication is involved.
