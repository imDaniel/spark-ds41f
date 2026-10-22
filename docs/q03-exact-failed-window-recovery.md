# Exact pre-attempt failure closure

This independent entry admits only window `bd3fceb06763454fa67d5712fdea4bbf`,
old source `652d5fc9b3957158348c598dedc9948c47c14221`, and capsule SHA-256
`2a01a8dc02ea1673a0bf7b460c8d90761ba5a6a6c86ac9805720b069ab1d7586`.
It is not a new execution window and cannot start any model or original service.

The 23 historical file fingerprints come from the original raw node witness,
not the normalized archive metadata. A separate private 16-record lstat witness
must have SHA-256
`837504ffffe5ec94f08dd8e76870e25802c2f87186270efdd47b16aabfd854d2`.
Private paths, raw hold bytes and live inode metadata are not distributed here.

## Invocation

Use the newly published independent checkout, never edit or replace the old
checkout or session. The journal must be the single fixed path
`$INDEPENDENT_ROOT/sessions/recovery-bd3fceb06763454fa67d5712fdea4bbf-neverstart`.
All input paths must be canonical, on the authorized head, and owner-controlled.

```sh
python3 -I -B "$NEW_CHECKOUT/scripts/q03_failed_window_recovery.py" \
  --old-session "$OLD_SESSION" --journal-dir "$RECOVERY_JOURNAL" \
  --live-witness "$PRIVATE_LSTAT_WITNESS"
```

This default is read-only file/process/hold admission, not a proof of live drain.
Only after source review and the authorized local handoff, append `--apply`.
No arbitrary command, START, restart, resume or watchdog retry is exposed.

## Closure contract

- Verify the exact immutable capsule, 23 old artifacts, 34 pinned source files,
  private metadata witness, hold invariants and fresh core/watchdog death.
- The only writable-ancestry exception is the witnessed original
  `restore-evidence` directory at mode 0775, behind its exact private 0700
  enclosing session. Its live inode, ownership, type and metadata must match.
  Every permitted 0664 historical file also requires its exact hash and mode.
- Acquire the existing session, watchdog and shared GPU locks and the new
  journal lock. Refuse a preexisting journal, any source drift or UNKNOWN state.
- Refresh heartbeat only while this bounded, live recovery owns those locks.
  Use the capsule's existing 7200-second recovery policy, not a new time limit.
- Freshly seal the old STOP epoch on the exact four configured targets and
  require all eight historical operation IDs. Durably register a new epoch,
  run only the registered coordinated STOP, and seal it even if STOP fails.
- Require fresh protected-file, GLM identity, all-four original STOP/memguard,
  GPU-drain and window-container-absence evidence. A failed STOP or incomplete
  proof never becomes success merely because a later seal succeeds.
- Write a separate `closed-stopped` proof and release intent before releasing
  the exact owned hold inode. `restored` remains false. Historical session files,
  source bytes and permissions are never rewritten or chmodded.

Failures before release retain the new journal and do not release the hold.
A failure after unlink reports release uncertainty and fresh absence separately;
it never claims the hold is still retained, recreates it, or retries blindly.
An existing incomplete journal requires evidence-led review, not a new path.

## CPU verification

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests \
  -p test_q03_failed_window_recovery.py -v
```

The focused matrix uses only temporary files and fake operation effects. It does
not contact the cluster, load a model, or establish actual release eligibility.
