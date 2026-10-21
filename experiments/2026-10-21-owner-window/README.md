# Q03 fixed owner-model window

Base: `76dabf376bd04aef768cea71f3c0d4b360cb633b`. Candidate, not promoted.
This is a new immutable controller generation. It cannot replace a live76dab
controller or assume its hold. No benchmark or serving endpoint is implemented.

The one new typed job is `owner_model`, with a bounded timeout and a selection
of `mode: off|dual` and `steering: off|loaded_zero|active`. Each explicit held
attempt creates one fresh MP pytest leader and three headless followers.
Fixed capacity, ring transport and observation batches are unchanged. Passing
qualifies only owner/configuration/collector retention, not graph retirement,
quality, capacity, old188 equivalence or performance.

The initial manifest additionally contains `owner_model`, schema1: exact owner
tests `{path,head,tree,fixture_sha256,git_config_sha256}`; path/hash bindings for
`profile`, `nodes`, `effective_config`; `prerequisites` path/hash bindings for
prior passed P0/G1/G2 `terminal`, `descriptor`, `capsule`; explicit candidate
`master_port` and bounded `handoff_seconds` (1..600). All site values remain
private. Effective config maps all58 reviewed fields separately for the three
steering states; values are not inferred from the first observed worker.

The initial owner manifest must have held_window schema1 and window_seconds10800.
Existing plan-attempt may select `--owner-mode` and `--owner-steering`; it generates
a fresh UUID and exact plan digest. Existing explicit resume/pause/status/close
remain the only controls. No automatic scheduling/retry, source replacement,
G1 test override, arbitrary command/env/mount interface, or lease extension.

All-four immutable input/image checks precede initial STOP. Each attempt derives
its unique resources, journals exact container IDs/config before start, arms
startup guards, starts all4 before readiness, validates four worker/runner
reports, switches all4 guards, then atomically acknowledges the exact ready
receipt. Core process liveness, source/hold/control/deadline remain mandatory.

The existing gated PID/PGID command supervisor owns both run and quiesce adapter
processes. The fixed remote helper owns bounded Docker/systemd operations.
Every remote intent is durable before launch; epochs are sealed before overlap.
The daemon journal is shared by normal completion, failed attempts and watchdog
recovery. Main/watchdog final restore enumerates every journal, not just active
attempt. Missing ID after create intent or failed/uncertain remote epoch remains
UNKNOWN and retains the hold; it does not promise automatic recovery. The
existing original policy alone performs final restoration and release.

Quiesce proves exact-ID container/guard/epoch drain; unchanged all-four preflight
additionally proves GPU drain before paused/ready. Final restoration checks GPU
drain after registered STOP, allowing recovery of a partially started original
restore without falsely attributing its known GPUs to candidate work.

Review/CPU faults and fresh replay are required before publication/field use.
No field operations were used to develop this candidate.

## Deployment and review boundary

Use a new clean checkout at the published deployment commit on every node.
Retain the existing active controller checkout, capsule and helper paths. The
ordinary `bin/spark --shape tp4-ring --cluster-config <private-profile> cluster
sync --revision <published-commit>` plan and its explicit `--apply` perform
coordinated Git synchronization; never synchronize a dirty source tree. New
checkout initialization uses the same owner-controlled origin, not a copy of
the original service checkout. Keep all site files ignored.

The owner stage is a four-rank test fixture, not a serving cluster. Its leader
must own the actual LLM lifetime, so a normal API-server launch cannot replace
it. It reuses the existing profile renderer, per-rank network settings, memory
guards and remote-operation supervisor. Serving and performance stages continue
to use `bin/spark cluster start/stop` with the explicit candidate profile. Do
not treat this bounded observation as their acceptance.

The single net patch in `patches/vllm/series` applies after native197 to a new
test-only checkout. It does not modify the native image or overlay vLLM sources.
Earlier mailboxes and the G1-cache test chain remain separate. Record fresh
replay HEAD/tree and exact fixture hash before using it.

CPU checks (no remote services or GPU):

```sh
PYTHONPATH=scripts PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_q03_owner_model.py' -v
PYTHONPATH=scripts PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_q03_held_window.py' -v
PYTHONPATH=scripts PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_q03_window.py' -v
bin/spark doctor
git diff --check
```

All four input probes require the exact published clean deployment HEAD and
owner-controlled origin in addition to the same immutable native image/test
identity. Positive all-four epoch seals are retained only after all receipts
are durable; unfinished seals are retried during drain. Previously sealed
epochs permanently reject late launch and need not be polled quadratically.
