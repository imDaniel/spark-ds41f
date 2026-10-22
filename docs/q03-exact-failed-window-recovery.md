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
  --live-witness "$PRIVATE_LSTAT_WITNESS" \
  --continuation-witness "$PRIVATE_CONTINUATION_WITNESS"
```

This default is read-only file/process/hold admission, not a proof of live drain.
Only after source review and the authorized local handoff, append `--apply`.
No arbitrary command, START, restart, generic resume or watchdog retry is exposed.

## Exact failed-predecessor continuation

The first independent entry failed before its old-epoch transport call because
it rejected unchanged registered source/configuration ancestry. This successor
does not edit that entry, its failure journal, the original window, or modes.
It additionally requires the private continuation authority SHA-256
`a98be5cacaf7e7a8e66f6cfcd5aff65e05d439696211fa96946975bd7f78374a`.

The authority identifies exactly 13 original policy/source/configuration/inventory
files and their witnessed ancestors. Group-writable original paths are accepted
only by exact path, inode, ownership, mode and hash. Content authority still comes
from the old capsule and its protected inventory, not merely the new observation.
The profile, physical node map and upstream configuration are parsed from the
same verified bytes. Default read-only admission exercises this exact local
loader before creating a claim or updating the hold; it never contacts SSH.

Pass the existing fixed journal as `--journal-dir`, not a new sibling. The
predecessor intent/state/log/lock remain byte-for-byte unchanged. Its exact failed
command, recorded recovery identity, known original core/watchdog identities,
and absence of a matching old command are rechecked before and after locks.
Unrecorded historical supervisor/launcher identities remain explicit gaps; they
are not invented or treated as a current all-four drain proof. The pinned
supervisor contract and exact terminal command record bound this pre-STOP
failure. Fresh old-epoch sealing is still mandatory.

One exclusive nested claim named `continuation-79633da35fcbe1dc` is permitted
inside that existing journal. Any preexisting claim refuses another attempt.
The original absolute recovery deadline is retained exactly, rather than
starting a new 7200-second budget. An expired deadline refuses continuation.


## Closure contract

- Verify the exact immutable capsule, 23 old artifacts, 34 pinned source files,
  private metadata witness, hold invariants and fresh core/watchdog death.
- The historical-session writable-ancestry exception is the witnessed original
  `restore-evidence` directory at mode 0775, behind its exact private 0700
  enclosing session. Its live inode, ownership, type and metadata must match.
  Every permitted 0664 historical file also requires its exact hash and mode.
  The separately hash-bound continuation authority scopes the original
  registered source/configuration exceptions described above.
- Acquire the existing session, watchdog, predecessor-journal and shared GPU
  locks, then the exclusive nested claim lock. Refuse any predecessor drift,
  existing continuation claim, source drift or UNKNOWN state.
- Refresh heartbeat only while this bounded, live recovery owns those locks.
  Preserve the first recovery's absolute deadline under the capsule's existing
  7200-second policy; continuation never resets or extends it.
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
  -p 'test_q03*recovery*.py' -v
```

The focused matrix includes temporary-file admission faults and fake whole-entry
effects, plus the real isolated child bootstrap, supervisor, original parser,
configuration loader and STOP implementation against a local fake SSH transport.
It does not contact the cluster, load a model, or establish release eligibility.
