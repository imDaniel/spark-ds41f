# Match the single IPC lock capability returned by Docker inspect

Basecf4f9cafc377a9fa4a42ca1c5454faa06c193af0 is preserved. Four-node
image import and input admission succeeded. First owner observation created
one inert leader container but rejected its initial security metadata: Docker
returned CapAdd=[CAP_IPC_LOCK] for the requested --cap-add IPC_LOCK. RDMA
device, GPU request, security options and cgroup rule matched. No candidate
container start, startup guard or GPU/model test occurred. Exact container
removal and positive four-node drain completed; window paused with originals
still stopped and independent watchdog/heartbeat active.

Accept exactly one IPC_LOCK in either legacy or canonical CAP_IPC_LOCK form.
Do not accept missing/unknown/extra/duplicate capabilities or loosen any other
resource, RDMA, mount, image, identity, guard or cleanup invariant. Docker
arguments and runtime image/engine/fixture remain unchanged.

Private CPU replay of the actual full inspect and owner plan first reproduces
the old refusal, then verifies the fix and strict capability negatives. Public
fakes cover the complete four-rank owner state machine with canonical spelling
and reject widened capabilities before any start. Real inspect remains ignored
private evidence. Independent full integration review precedes a new window;
never edit the active capsule or its loaded source. All original assets remain.

Local full related19-module suite:333 tests passed; doctor/diff-check passed.
Actual inspect SHA256 e3d5bf0845e55aec9beb95ffa9be582d1aaf7b1e68b2ebb0f553e82d0eca5144
replays from old rejection to full initial-validator success without changing
the snapshot; config projection hash stays2aac077f94c1dcc7fc663a3c667de85f55121694cdbbf7d9782745f6b765b3a3.
Seven real-snapshot capability negative variants remain rejected. These are
CPU/control checks, not native GPU/model acceptance.
