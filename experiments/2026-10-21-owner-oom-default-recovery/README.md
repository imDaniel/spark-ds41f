# Owner OOM-default recovery

Base: b8b6886b2a21d198b2be6f9a9fd6d8a35f8b1a55, tree
574cd6a85e0c1b1eb57c5f48c5223fa0b360eeb6.

One variable: recover a failed owner drain when Docker changes the explicit
`HostConfig.OomKillDisable=false` inert value to `null` after start. Four actual
rank inspections showed this as their only protected-projection difference.
This is a controller failure, not a model correctness or performance result.

The separately published entry point loads the unchanged legacy source and
capsule, verifies its source inventory and ownership, and uses the original
session/GPU locks and final keep-stopped recovery. Each present daemon must
match its original inert witness/hash after normalizing only this explicit
nullable default in a copy. True, absent, numeric, foreign IDs and all other
configuration changes remain refusals. All four ranks are admitted before
stopping the first. Existing remote epoch fences, exact daemon/guard cleanup,
original coordinated STOP, protection and all-four GPU-empty proof remain.
No original START or candidate launch is provided. Raw inspections and original
hashes remain evidence; the original source/capsule are not edited.

CPU verification: nine admission and full four-rank drain tests pass, including
non-alias drift on the fourth rank refusing before any stop, and a stubborn
daemon refusing a drained receipt. Separate private real-snapshot replay:
all four original validators reject, narrow recovery accepts each original
pre/post pair, 32 mutated snapshots reject, input objects remain unchanged.
`bin/spark doctor` and `git diff --check` pass. No new native build is needed;
engine, image, fixture and runtime configuration are unchanged.

Execution is bound to the retained session and independently recorded capsule
SHA using `scripts/q03_owner_oom_recovery.py --session-dir EXISTING_SESSION
--capsule-sha256 RECORDED_SHA`. This experiment does not authorize another model
attempt or establish quality, capacity, graph-use or performance acceptance.
Site paths, complete raw success/failure evidence and actual execution results
remain in the private study results directory, outside Git.

## Bounded reentry hardening candidate

Follow-on base: `7d5927adba915ccce9ad8b9a48f38734db6972d7`. The original
one-off recovery has already closed stopped; this candidate is not an instruction
to rerun that incident and does not alter the separate future-window controller.

The same two-argument entry now requires the capsule sidecar, exact old 22-source
inventory, and a present old watchdog record bound to this window and capsule.
PID/start-time observations distinguish dead, live, reused and unknown. Only
proved dead old core/watchdog identities admit recovery; malformed records,
permission errors and PID reuse never authorize signalling or takeover.

One fixed `owner-oom-recovery` journal inside the old session freezes the entry
bytes, capsule, sole owner plan, main process identity and original recovery
deadline. There is no alternate journal argument, refreshed retry budget or
arbitrary command interface. Session, watchdog, GPU and owner-journal locks are
non-following regular files and remain held through cleanup and closure; the
independent supervisor never inherits these locks. Admission and old identities
are rechecked after locking. An exact single-plan inventory replaces reliance on
the mutable active-attempt pointer, including interruption after that pointer was
cleared. An intact, contiguous immutable event tail may advance the in-memory
sequence after a crash between event publication and the mutable state write;
old event bytes are never edited.

Before any recovery mutation an independently detached supervisor must publish
an exact process/intent readiness handshake. Main and supervisor exchange bounded
identity-bound heartbeat records. Main death can launch exactly one gated retry:
the child identity and immutable intent binding are durable before execution.
The internal retry flag alone grants no authority. The retry reacquires all old
locks, drains the same durable owner/original epochs, and uses the unchanged old
restoration budget. Dead local clients are never daemon/GPU absence proof.

The existing stopped-closure ordering remains authoritative: all-four daemon and
guard cleanup, coordinated original STOP, protected-state checks and fresh
all-four GPU-empty proof precede closure and exact hold release. No original
START exists in this capsule's path. The same bound retry may finish only the
closure/hold-release tail after interruption, including after unlink but before
the final state write. A normal invocation cannot reuse a closed session.

On uncertain identity, heartbeat loss, deadline expiry, failed handshake or
exhausted retry, recovery fails closed and does not release the hold. Supervisor
or host loss is not itself recovered automatically. A crash before readiness can
leave a durable intent without an armed supervisor; it requires explicit review,
not automatic admission of another main or a fresh timeout. This design is one
bounded recovery owner, not a general service manager or host-reboot mechanism.

CPU checks for this candidate: 34 compatibility, drain, identity, heartbeat,
lock, retry-authorization and real-old-restore-ordering tests pass, including
injected interrupted-state and before/after-hold-unlink boundaries. The 42 owner
and 69 session regression tests and 11 existing independent-supervisor tests pass.
The 34 focused tests also pass using the actual unchanged b8 owner source via
`Q03_OWNER_MODEL_ROOT`. These are offline fault models, not actual SIGKILL/GPU or
remote-deployment qualification. `git diff --check` passes. `bin/spark doctor`
was attempted but the deliberately sparse cloud worktree lacks
`config/cluster.json`; complete build inputs and hardware validation are not
available in this worktree. No build/runtime/engine/image changes are included.

### Follow-on review corrections

The first hardening candidate is retained at
`49ca71ab212f3974b00f3598d0bd4d7604687926`. Its independent review identified
mutable-snapshot races and Python's ability to consume an existing bytecode
cache even with `-I -B`. The follow-on correction keeps the strict immutable
reader unchanged. State and heartbeat readers accept a complete opened inode
across atomic replacement and retry genuinely unstable content a bounded number
of times. Hold reads share the existing flock protocol for in-place heartbeat
updates. Hold is observed before state; a completed release marker triggers a
fresh hold observation before any retry decision, covering both closure/unlink
interleavings without admitting a missing unclosed or foreign hold.

The new recovery process now compiles the exact hash-verified bytes for its fixed
legacy Python import inventory through a source-only loader. Canonical module
names, filenames and sibling imports are retained; existing same-name modules
are refused, and ignored `__pycache__` files cannot select executed code in this
process. This guarantee does not extend to pre-existing explicit loaders inside
the unchanged old adapter subprocesses or protected original `bin/spark`.
Those old sources are not edited or implicitly requalified here.

The focused suite now has 42 passing tests, including an actual isolated
`-I -B` interpreter with a valid timestamp/size-matched but unpinned bytecode
cache, atomic-replacement and hold/release-order counterexamples. The actual b8
recovery modules also import successfully through the same-byte source loader.
The unchanged 42 owner, 69 session and 11 existing-supervisor regression tests
pass again; the sparse doctor and hardware qualification limits above remain.
