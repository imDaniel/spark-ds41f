# Frozen HTTP log-permission recovery

This is an independent recovery branch from
`3a49389f7ba0e93f05bab1c9a52b4425d4dd7fde`, not a hotfix to the failed
session. It supports exactly window `4f58632f54964f109c3457fa17c57872`, capsule
`738b66237ae65f4148b423af002b2b1f60c82b7de6bf986ffcd4408d598f844e`, and
attempt `beeba65b41c44f95ae23b049f70f2259`.

The old HTTP result remains failed. A successful new result means only that
its diagnostics were preserved, its candidates and guards were drained, the
registered original services remained stopped, protected inputs and all four
GPU process lists were verified, and the exact owned hold was released.
It grants no START, new test, original-service mutation, or promotion authority.

## Separate helper release

The new helper is the byte-exact 0600 creation repair from
[PR 17](https://github.com/imDaniel/spark-ds41f/pull/17), frozen at
`40c4f68ccb1a13b251ae31d67ebc8bab5c944d11` on
`fix/q03-private-log-creation-20261021`:

- New helper SHA256: `7271c816f06c6f1bb600649b31c7e74941a075a502d958d0bf945e2b38ebd36e`.
- Old helper SHA256: `1ddb0c79d6e50fd71245cff1c53c32fe9bf862e1a57f4078e6fed1ecafd7484a`.

The recovery branch deliberately retains the old helper source; do not copy its
`scripts/q03_remote_operation.py` as the repair. Provision the helper bytes from
PR 17 separately on all four hosts as the original session user, under the independent
root's `helpers/q03_remote_operation-<new-sha>.py` path. Never overwrite the
old hash-named helper or checkout. Only new recovery epochs use the new helper.
The old helper remains the interpreter for old terminal semantics. The strict
log reader is unchanged: historic 0664 peer streams still fail completeness.

## Preparation and explicit apply

Run preparation and apply on the registered physical head **spark01**, as the
original admin/session-lock owner. The `/proc` identities and shared GPU lock are
local to that host; another rank, Mac or cloud computer is not an execution
substitute. Use the newly published, clean recovery checkout, not the old checkout.
Preparation requires the existing private evidence-binding file whose SHA256 is
`ee93056f270cdc5286871eef02286919650773c3842df30808f6eb6bedf99e75`.
That file contains private site evidence and must remain private; it is not in
this repository. Original protected inventories and remote-authority bytes are
also required on site, and must still match the capsule's hashes.

Preparation is part of the already-authorized recovery workflow, not another
permission request. It creates one private immutable intent in a **new** journal
outside the old session, old checkout, candidate resources and protected roots:

```sh
python3 -I -B /path/to/new-checkout/scripts/q03_recover_http_logs.py \
  --prepare \
  --old-session /path/to/old-session \
  --evidence-file /path/to/private-evidence-bindings.json \
  --journal-dir /independent-root/recoveries/new-http-log-recovery \
  --new-helper /independent-root/helpers/q03_remote_operation-7271c816f06c6f1bb600649b31c7e74941a075a502d958d0bf945e2b38ebd36e.py \
  --new-helper-sha256 7271c816f06c6f1bb600649b31c7e74941a075a502d958d0bf945e2b38ebd36e \
  --authorization-reference 'reference to the approved exact recovery request' \
  --budget-seconds 7200
```

The journal's parent must already exist. Every ancestor, not just that parent,
must be owned by root or the invoking session user and not group/world writable.
Deliver the private binding file to a controlled on-site path before preparation;
a cloud-local path or attachment alone is not an on-site file.
Omit `--prepare` for read-only file verification and a plan without any journal
or device action. Preparation prints the exact pinned apply command:

```sh
python3 -I -B /path/to/new-checkout/scripts/q03_recover_http_logs.py \
  --journal-dir /independent-root/recoveries/new-http-log-recovery --apply
```

The new absolute deadline starts when preparation writes the intent. It is at
most 7,200 seconds after issuance, and apply and supervisor retry use that same
deadline. The original recovery deadline, `2026-10-21T20:59:43.443826Z`, is
recorded separately; its state and `restoring_started_epoch` are not reset.
The shared hold's ownership and original expected-end fields are not changed.
Only its existing fixed-width heartbeat is refreshed while recovery is active.

## Positive barriers

1. Verify the exact capsule, 28 old source pins, failed state, admitted plan,
   complete rank0 generation, four container/config/guard identities, and private
   archival binding. Reject changed or extra operation/epoch identities.
2. Acquire the actual old session, watchdog and HTTP locks, the existing shared
   GPU lock, and a new recovery lock. Recheck path/inode identities at authority
   boundaries. Prove the recorded old core and watchdog are terminal.
3. Start an independent source-pinned supervisor before any new remote action.
   Both sides have PID/start-ticks and intent-bound durable heartbeats. A dead
   main can get one gated retry under the same intent, locks and deadline;
   explicit UNKNOWN does not trigger a blind retry. A stuck supervisor prevents
   further mutation when its heartbeat goes stale.
4. Verify all four new helper installations and old resource/authority
   boundaries. Freshly audit all 39 old sealed epochs per host and all 46 old
   terminal operations using unchanged old helper semantics, exact archived
   hashes/modes and current process/group observations. Old failed log reads
   remain failed diagnostics, not successful captures.
5. Verify protected inputs, GLM and registered original identities. Originals
   must already be stopped; this entry does not acquire authority to stop them.
6. Copy only the previously complete rank0 log generation byte-for-byte into the
   new journal. Capture new bounded timestamped stdout/stderr generations for
   the three incomplete peers. Keep every historic rejected generation intact.
7. The unchanged all-four log barrier validates all four complete generations
   before exact-ID removal without force. All new operations are durable,
   remotely supervised and sealed. Only the helper's exact already-unloaded
   memguard exit5 may receive a separate, bound lifecycle acknowledgement with
   fresh inactive/absent guard evidence; the original failed receipt remains
   unchanged. Other failed mutations never authorize overlap. Missing, truncated, failed or changed logs
   retain the stopped candidates and hold.
8. Recheck old evidence, all new epoch barriers, all four retained log proofs,
   candidate absence and guards. Re-run full protected/original/GLM/GPU drain
   checks within the new deadline. Write a separate closure proof, then a durable
   release-ready record before releasing only the exact owned hold.

A crash immediately after hold unlink is reconciled by the independent
supervisor using absent hold plus the exact pre-existing closure proof. It is
not converted into a fabricated old terminal receipt. Any unknown ownership,
source change, process identity, daemon result, protection check or GPU drain
fails closed. Preserve the new journal and report the exact blocker; do not
chmod historic files, edit old receipts, repeat old `recover`, or make another
intent to silently extend the same attempt.

## CPU validation

```sh
python3 -m unittest discover -s tests -p test_q03_http_log_recovery.py -v
```

Coverage includes real Linux old-helper observation with 0664 rejection,
immutable byte/mode drift, live old process, unknown epoch, failed mutating
receipt, exact client-group exception, actual lock exclusion/replacement,
private exclusive files, rank/config/guard drift, all-four cached seal proofs,
new peer-only generations, incomplete log retention/resumption, source-inert
import, new fixed budgets, supervisor identity/readiness, real gated retry
launch, and real process death plus write failures around hold unlink.

These tests do not claim current site safety or a successful physical recovery.
Site execution must use the frozen published ref and independently verify its
current receipts. No cloud test invokes Docker, SSH, Spark, a GPU, or a device.
