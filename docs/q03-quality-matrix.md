# Q03 Stage 1b: finite 16-token matrix

Source preparation only. Base is PR21 `4d3f478e7ef8221cb45a3c19fd81bf17aaa14c02`
(tree `b4b6d918f8b0c6d40f808f1a330c5a72aa584749`). This is a new immutable
HTTP capsule, not an update to a running capsule or its recovery authority.
No actual corpus, service/GPU/tokenizer execution, result, quality acceptance,
capacity acceptance or performance claim is provided here.

Schema 1 smoke and schema 2 `lean_tokens_c1` retain their meanings. In particular,
`q03_qualification.py` is byte-identical to PR21. The new opt-in schema 3 workload
is exactly `lean_tokens_stage1b_v1`. It reuses the same actual 16 distinct fully
templated 2,048-input-token prompts and pinned PR8 `servebench model --no-score`.
Every call generates 16 outputs per prompt, with temperature 0, seed 0 and
thinking false. The existing fixed-length, return-token-IDs and fresh cache-salt
behavior is unchanged. There is no caller-supplied concurrency, argv, endpoint,
output length, new model, scoring operation, benchmark framework or threshold.

## Exact inventory and prerequisites

The first three Stage 1a calls must already be complete: unloaded OFF c1 twice,
then unloaded DUAL c1, with stable controls and exact output-ID equality. Reuse
is pending until the actual passing receipts exist. They are not fabricated,
rerun automatically, or replaced by an offline validator's success message.

The new capsule executes this fixed order, one owned arm at a time:

1. Unloaded OFF: c8, c16.
2. Unloaded DUAL: c8, c16.
3. Loaded-zero OFF: c1 control1, c1 control2, c8, c16.
4. Loaded-zero DUAL: c1, c8, c16.
5. Active-scale1 OFF: c1 control1, c1 control2, c8, c16.
6. Active-scale1 DUAL: c1, c8, c16.

These are 18 new calls, 288 requests and 4,608 output IDs. Including the reused
three calls, the complete screen is exactly 21 calls, 336 requests and 5,376
output IDs. Every call executes all 16 distinct prompts exactly once. Setup,
startup, readiness, draining, removal and restoration work are additional, not
included in these counts. Same-arm calls share one owned boot. Arms never run
concurrently; operator admission still controls attempts. There is no automatic
queue, retry, lease extension or assumption that six boots fit one lease.

An illustrative qualification fragment (not an executable manifest):

```json
{
  "schema_version": 3,
  "qualification": {
    "workload": "lean_tokens_stage1b_v1",
    "corpus": {"path": "<actual canonical corpus.json>", "sha256": "<actual digest>"},
    "stage1a": {
      "source_head": "<exact frozen PR21 head above>",
      "source_tree": "b4b6d918f8b0c6d40f808f1a330c5a72aa584749",
      "capsule": {"path": "<retained Stage1a capsule.json>", "sha256": "<actual digest>"},
      "off_attempt": "<retained OFF attempt directory>",
      "dual_attempt": "<retained DUAL attempt directory>"
    }
  }
}
```

All existing owner_inputs, actual six-cell owner_matrix, toolbox, api_port,
startup_seconds and client_seconds fields remain required. Real corpus/artifact
identity requirements and all-four model/tokenizer byte checks are unchanged from
[q03-qualification-execution.md](q03-qualification-execution.md). For standalone
CPU corpus preparation, use the separately reviewed
[PR25 compatibility successor](https://github.com/imDaniel/spark-ds41f/pull/25) at
`fb4d6e8e8e6ec81a767c1163e4bab4385fbd1266` (exporter SHA-256
`d09d4bacf50efd9ccddf2493c31995909e3eef22aaa0bb2fc5b4ce0b09a2e457`).
This Stage 1b branch deliberately does not compose that exporter fix: its
inherited PR21 exporter is not claimed ready for those compatibility cases.
Actual registered asset/runtime/manifest evidence and real corpus generation
remain required; a CPU fixture or source CI does not provide them.

## Strict historical reuse, not historical execution authority

Only the exact PR21 publication and its literal 28-file recovery-source hash
inventory are recognized. The historical source files are read and hashed,
never imported, executed or used to recover the current window. A different
actual Stage 1a source freeze is unsupported and must be reviewed explicitly;
it is not silently treated as PR21. No generic historical-code loader exists.

Reuse rechecks the capsule pin and sidecar, canonical archived source location,
source bytes, retained history and order, descriptor/admission/plan hashes,
terminal command-plan identity, passed HTTP outcome, client completion and
raw output hashes, complete exact input/output IDs and positive all-four drain.
The historical executed qualification-module pins and complete PR8 toolbox
inventory must match the frozen source. Both old controls and the candidate must
match. Any failed old attempt remains in its history and blocks reuse; no later
green pair erases it. New native image/head/runtime, corpus, profile, effective
unloaded configuration and toolbox identities must match the old evidence.

The ordinary Session, verify_outer, verify_history and recovery checks remain
strict. The old capsule cannot be opened with current live RuntimeOps. The
isolated historical reader accepts only the one literal old inventory for
read-only evidence, not live operations. Retain both old and new source/evidence
roots. Historical roots cannot overlap writable current resource/result/cache
roots. The active capsule and old recovery executables are never replaced.

## Comparison and immediate short-circuit

Every raw call is fsynced and validated before the next call. Require all 16
unique IDs, the exact 2,048 frozen input IDs, 16 non-boolean integer output IDs,
finish=length, the exact request controls/concurrency/model/URL and a zero CLI
exit. Raw incomplete/errored/mismatching outputs are retained; later calls do
not run. A mismatching second OFF c1 control records
INCONCLUSIVE_UNSTABLE_CONTROL. Other token divergence records TOKEN_ID_MISMATCH
with the first prompt/output position and both IDs. Failed attempts stop this
finite matrix; no retry-to-green advances its inventory.

Unloaded c8/c16 and all loaded-zero calls compare to the proven unloaded c1
reference. This enforces no-op equality across state and concurrency. Active OFF
establishes a new active-only c1 reference; its repeat and c8/c16 must match.
Active DUAL uses the drained active OFF reference and gates c8/c16 on its first
c1 result. Active tokens are never compared to unloaded tokens.

The normal HTTP startup profile binds the loaded steering artifact. Request
controls use the existing vllm_xargs profile and scale 0/1. Native image/runtime,
TP4 ring, fixed1M/maxseq16/batch8192/KV10.5GiB, DSpark5, all-four create/start,
startup5GiB/steady3GiB guards, readiness identity, descendant supervision, epoch
sealing, raw-log retention, positive daemon/GPU drain and keep-stopped terminal
behavior are unchanged. Client remains bounded at 600 seconds maximum per arm,
startup at 3,600, attempt at 7,200 and outer lease at 10,800. Admission must freeze
adequate actual bounds; this packet does not increase them or invent timings.

After all six new arms are passed and drained, the offline whole-screen reader is:

```sh
python3 -B scripts/q03_quality_matrix.py --session-dir "$ACTUAL_STAGE1B_SESSION"
```

It rechecks the complete old and new chains, all six selections in order, current
four-module client source closure, all per-state references/identities and exact
18-new/21-total counts. A summary alone is not acceptable prerequisite evidence.
The source-pinned controller entrypoint quality_module().compare_session exposes
this same read-only validation to separately reviewed successors.

## Explicitly later

This remains a small 16-token identity/equality screen, not full quality.
Teacher-forced distribution checks, scale4 sentinels, 256-output confirmation,
8K inputs, full capacity and original188 noninferiority remain later gates.
Stage 2 closed performance (64 outputs, 10s/30s windows) is not executable through
this workload. There is no performance, quality, deployment or runtime promotion.
