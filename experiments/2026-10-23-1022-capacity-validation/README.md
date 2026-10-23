# Same-image capacity continuation

Base repository commit: `8c79b93e4afa14b054031d492f3b08f2013f4ac1`.
The candidate remains RC3 `14d5bb2c…`, vLLM tree `dc13e8dd…`, rebound
profile `e06b8cb6…` and the preceding parser-byte-alignment cluster configuration.
No runtime source, native library, launch setting, weight or profile changes.

The preceding window passed 12 tool calls, two tool followups, 20 quality smoke
cases, 13 cache checks, and cold single requests through 1,000,000 input tokens.
All 16 requests in the 8K concurrency batch succeeded, but only five were observed
running together. With just 32 output tokens, early requests finished while later
prefills were still being admitted. That batch did not prove the requested C16
capacity. The runner restored r6x and withheld subsequent stages. All evidence,
including the failed assertion, is preserved in [attempt 4](runs/attempt-4/README.md).

The intended variable is generated length: C16/8K uses 256 output tokens,
C4/262K uses 768, and C4/1M uses 3072. The margins cover later chunked prefills
while earlier requests decode (at most six tokens per speculative decode step).
Cold per-request cache salts, exact input/output usage, and the original admitted
concurrency assertion remain required. Running/waiting/KV samples and request
start/end/first-visible timestamps are saved. A peak running count proves admission;
it alone does not establish that all contexts were fully prefetched simultaneously.
First-visible timing is not an engine first-token performance benchmark.

`request-reuse.json` pins the preceding raw request/cache/manifest receipts.
The guard checks their hashes, runtime identity and every reused gate before
opening the window. The existing component receipt and exact-image CPU parser
guards also remain mandatory. Only one tool smoke request, the three unpassed
capacity cases, and two minutes of mixed-scale stability run again.

Commands from the repository root:

```sh
python3 -m unittest discover -s experiments/2026-10-23-1022-capacity-validation -p 'test_*.py'
python3 experiments/2026-10-23-1022-capacity-validation/run_window.py
# On Spark01, with a freshly pinned manifest, authorization and independent watchdog:
python3 experiments/2026-10-23-1022-capacity-validation/run_window.py --manifest /srv/ai-cluster/operations/1022-release-20261023/window.json --authorization /srv/ai-cluster/operations/1022-release-20261023/authorization.json --execute
```

The planned maintenance window is 20–40 minutes, bounded at 75 minutes with
15 minutes reserved for recovery. Failure still triggers coordinated restoration
of the exact retained four-node r6x service. PR #56 stays draft and unmerged.
This bounded release screen makes no comparative throughput or long-soak claim.

The continuation is complete: [final qualification results](RESULTS.md) record
the passing same-image capacity/stability gates and verified r6x recovery.
