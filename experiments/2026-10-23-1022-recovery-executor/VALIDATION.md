# Local validation record

No node operations, inference requests, GPU tests, image builds, PR changes or
service lifecycle actions were performed. All executable cluster commands in
the fault tests target temporary copies of `tests/fake_site.py`.

## Results

| Environment | Result | Raw log |
|---|---|---|
| macOS ARM64, Python 3.15.0 | 44 tests, 0 failures/errors/skips | [macOS](runs/fault-tests-macos.txt) |
| Local Docker Linux ARM64, Python 3.12 | 44 tests, 0 failures/errors/skips | [Linux](runs/fault-tests-linux-arm64-final.txt) |

These are the same 44 scenarios on two operating systems, not 88 distinct
cases. The Linux job used the existing S image, overriding its entrypoint with
Python. It mounted only this experiment read-only; used no network, Docker
socket or GPU; dropped capabilities and bounded memory to 512 MiB, CPU to one
core and processes to 128. `/tmp` was a 128 MiB temporary filesystem with
execution enabled solely for the fixture `bin/spark` scripts.

The successful set covers:

- authorization, disabled execution, implementation/adapter hashes, fixed
  source/asset identity, stale/missing rank observations and unverified assets;
- separate watchdog presence, foreign hold refusal, shared operation locking,
  queue telemetry absence and active requests without stopping the service;
- all four arms followed by the pinned restoration, healthy retained no-op,
  partial candidate/retained ranks, refused plans and unexpected shape change;
- stop errors, surviving guard/GPU processes, management loss, GPU fault,
  retained launch/doctor failure, foreign/owned component jobs;
- API verification and gateway acceptance failures, reblocking ingress and
  holding the lease for manual intervention;
- heartbeat loss, hard window deadline, wall-clock rollback, reboot refusal,
  PID reuse, worker EOF before GO, command timeout and process-group cleanup;
- killing the controller process during a simulated launch while a separate
  watchdog process terminates the pending command and restores the fixture.

## Retained unsuccessful attempts

1. [First macOS run](runs/fault-tests-first.txt): 33 tests, 6 failures and 13
   errors. `/var` versus `/private/var` aliases made the executable allowlist
   reject the same pinned file. Canonicalize both sides of that comparison.
2. [Second macOS run](runs/fault-tests-second.txt): 33 tests, 2 failures.
   The Mac process-birth fallback included the mutable process status alongside
   its start time; changing sleep/run status was misclassified as PID reuse.
   Keep start time only. Linux already uses boot ID plus `/proc` start ticks.
3. [Third macOS run](runs/fault-tests-third.txt): the original 33 cases passed.
   Subsequent review added 11 cases and the final 44-case set passed.
4. [First Linux run](runs/fault-tests-linux-arm64.txt): 44 tests, 8 failures and
   14 errors. Docker's temporary mount was non-executable, so the fixture
   `bin/spark` could not start. [Diagnosis](runs/linux-fixture-diagnosis.txt)
   records the permission error. Explicit `exec` on the isolated test `/tmp`
   fixed the fixture environment; the final Linux set passed without a code
   change or host filesystem remount.

## Other checks

- Default `bin/spark doctor`: configuration OK.
- Default 1022 and reviewed S `build check`: complete inputs OK.
- New Python and JSON sources parse, and tracked/new-file whitespace checks pass.
- All 74 files listed by the previous preparation's artifact manifest still
  match their recorded hashes. U/E/S source, images and prior run evidence were
  not changed. No upstream patch stack is affected by this controller work.
- Git HEAD remains `83a37491b3ec523d29ae967265c0f2471779e137`; the earlier local
  README hunk and preparation directory are retained. This implementation has
  not been committed or published.

The disabled template and hook contracts are review material. Local fault
coverage does not verify live SSH/privilege behavior, actual ingress blocking,
Linux service installation, GPU readiness, or hardware recovery. Those remain
separate site-binding and later authorized-window gates.
