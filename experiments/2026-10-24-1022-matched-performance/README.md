# Final-candidate matched historical performance

Base repository: `b8553de852becc7649e5914919ecfe33f8425cab`. This experiment
measures only the previously qualified RC3 image `14d5bb2c…`, tree `dc13e8dd…`,
profile `e06b8cb6…`. Build inputs, compute settings, model weights and steering
arithmetic remain unchanged. The temporary configuration binds the existing
port 8000 on the original interface so the existing gateway can reach the owner
trial; only the host/port/readiness URL differ from the preceding RC3 recipe.
The intended variable is the serving version,
compared with preserved r6x observations; the old service is not benchmarked again.

The original measurement modules are frozen byte-for-byte from historical
deployment `8181e0cc…` under `measurement/`, with their license and hashes.
The same 16 questions, seed 2048, per-stream 4K standard-library document,
256 fixed output tokens, 15-second warmup, 90-second measurement and six batch
windows are used. No reasoning/chat-template override is introduced. Host Python
standard-library inputs are pinned; generated documents are saved and their exact
token counts must match the old receipts. Prefix cache resets before each run,
then each stream's document is primed using the same historical algorithm.

All generated requests explicitly carry scale 1 or 4 and the pinned profile.
The matrix is scale 1/C1, 1/C4, 4/C1, 1/C4, 4/C4, 1/C4. The additional C4
scale-1 runs match the three preserved daily-followup observations. Full timing
events, usage, server counters, cache hit share and latency distributions are saved.
Each case starts with every node's hottest thermal zone below 55°C, by natural
cooldown under the retained fan policy. No fan/driver/Docker changes are made.

Before and after measurement, arithmetic and the repaired literal-tool request
run at both scales. Existing full functional, parser, GPU, profile/cache and
capacity evidence is reused for the unchanged candidate. Those quality gates and
this performance comparison remain distinct requirements for the release decision.

The report must give old/new aggregate and per-stream TPS, TTFT mean/p50/p90/p99,
TPOT and total-latency tails, percentage changes, every individual run and all
failures. C4 scale 1 is compared both with the initial historical run and the
three-run daily followup. Historical references are not contemporaneous paired
boots; even matched workloads do not establish causality or statistical equivalence
across days. Six within-run windows are not six independent service startups.

The owner subsequently requested that a passing candidate remain available for
personal scale testing through the existing gateway and aliases. On success,
the executor checks private and scale-1/4 gateway requests, reopens ingress and
enters an explicit `owner_trial` state. The hold has no automatic end time; its
watchdog keeps the exclusive hold alive and leaves the candidate running until
the owner ends the trial. Per-node memory guards remain active. Gateway
configuration, aliases and formal production recipe are unchanged.

Before that accepted handoff, failure or controller loss retains the bounded
recovery policy. A failed gateway probe cannot enter `owner_trial`. Recovery
drains reopened ingress before stopping any rank and restores the original four-
node r6x image/config/profile with private/gateway checks. Estimated benchmark
maintenance is 20–35 minutes, bounded at 60 with 15 minutes reserved for recovery;
the accepted personal trial is owner-controlled. PR #56 stays draft/unmerged.

```sh
python3 -m unittest discover -s experiments/2026-10-24-1022-matched-performance -p 'test_*.py'
python3 experiments/2026-10-24-1022-matched-performance/run_window.py
```
