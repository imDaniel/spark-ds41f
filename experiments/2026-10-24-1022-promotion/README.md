# Site promotion: 1022 with attention steering

Owner decision, 2026-10-24: "确定按你的建议执行，合并56，正式晋级".
The owner also confirmed the currently running PR #56 steering works and cancelled
repeat performance measurement after the prior ingress isolation was verified.
This promotes the existing candidate, with no new upstream commits or compute
settings. It is a release/adoption record, not another performance experiment.

## Accepted identities and scope

- [Baseline](../../manifests/baselines/2026-10-24-homelab-1022-attention.json),
  [source manifest](../../manifests/sources/2026-10-24-homelab-1022-attention-source.json),
  root `config/cluster.json` and `upstreams.lock.json` are the production authority.
- Tested deployment `4ec4bab7e29d4617a7d728bd589e67e58deb1331`; RC3 image
  `14d5bb2c49aa…`, vLLM tree `dc13e8dd17ff…`, profile `e06b8cb60f3f…`.
- TP4 ring only: 1,048,576 tokens, 16 sequences, 8192-token batch, 10.5 GiB KV/rank,
  DSpark 5 / row cost 0.035. Unchanged 1017 model weights, attention writer,
  layers 2–39, alpha 2.5, request profile `heretic-1017-attn-v1`, scale 0–4.
- Root recipe resolves to the accepted concrete recipe's exact command,
  environment, mounts, host policies and fabric. Materialized checkpoints can
  name a snapshot without a separate blob mount. Existing hub profiles remain
  selectable. TP3/TP3-4K sizing templates remain, without benchmark acceptance.
- Gateway and aliases stay unchanged. Existing image limit 999 and processor
  cache 1 GiB are preserved configuration, not new multimodal test evidence.

## Evidence and limits

[Capacity/function record](../2026-10-23-1022-capacity-validation/RESULTS.md):
parser/encoder/steering checks, real tool calls, cache isolation, cold contexts to
1M, 16×8K and four concurrent 262K/1M requests, bounded stability and recovery.
[Performance](../2026-10-24-1022-matched-performance/RESULTS.md): historical r6x
was not rerun; candidate only, explicit scale 1/4, six matched groups, zero foreign
or failed requests. All unfavorable outcomes are retained. The accepted reference
in `manifests/benchmarks/2026-10-24-homelab-1022-attention-tp4.json` wraps those
existing receipts and their actual shape; it does not pretend a new full
`bin/spark bench` suite was run. Recompute with the performance experiment's
`compare.py`.

TPS changed +0.09 to +1.65%; common C4/scale1 +0.86%, TTFT mean essentially level,
P90 better and P99 slightly worse. Single-stream TTFT mean is 33–34 ms slower.
Scale4 C4 first-token tails regress while complete-answer P99 improves. No
cross-day statistical equivalence, broad quality scoring, long soak or fresh
image-request gate is claimed. Owner acceptance includes these recorded limits.

## Activation without a serving restart

1. Capture the live four-node image/profile/command/mount/environment identities,
   container start times, active guards, existing owner-trial hold and gateway hash.
2. Run offline checks and a fresh source replay; publish the promotion commit on
   PR #56, require passing CI, then merge with the expected head SHA.
3. Verify ownership of window `d8edd2b0-d183-42c4-9da4-e5a6ab332f42`. Stop only
   its independent owner-trial watchdog after recording the owner's promotion
   decision. Keep the hold until adoption finishes; memory guards keep running.
4. From a clean published checkout, plan and apply `bin/spark --shape tp4-ring
   --cluster-config config/cluster.json cluster sync --revision <merged SHA>`.
   Every node detaches at the same merged commit; origin remains this fork and
   upstream push stays disabled. The serving containers are not replaced.
5. Run the promoted profile's `doctor --live`, compare exact runtime identities
   and unchanged start times, and probe existing scale1/4 gateway aliases. Save
   receipts in `/srv/ai-cluster/operations/1022-promotion-20261024`.
6. Only after all nodes and routes verify, mark the old owner-trial window
   promoted, archive its manifest/authentication metadata, remove its own hold,
   and delete the temporary recovery API-key copy. Do not remove rollback assets.

If source adoption fails, retain the hold, report it and return changed checkouts
to their previous commit with the coordinated sync rollback. A source/config-only
failure does not require stopping a healthy candidate. Service failure uses the
retained r6x recovery below. This record does not claim activation succeeded
before those operational receipts exist.

## Retained r6x recovery

Branch `archive/r6x-attention-steering-20261023` remains at
`223a3bb3498d12f470e876db3b9e1f5bdc8772b6`. Four-node retained checkout:
`/srv/ai-cluster/runtimes/deepseek-1017-heretic-r6x/source/spark-ds41f`;
node-local config `config/cluster-steering-r6x-cache-details-tp4.local.json`,
SHA `c26d9fcb996da19b26b243dcc024664a9e004bfa1d91377274607c04dd8ff707`;
image `8ad0da0366af…`, profile `d7f0b997ea27…`. Preserve the stopped containers,
image, sidecar, node maps and model weights. The tested recovery executor inputs remain recoverable from the published
`4ec4bab7` commit and archived window manifest; the original r6x checkout is
unchanged. The former watchdog must not be restarted against a candidate source
checkout whose pinned files moved.

For a later authorized rollback: take a new exclusive hold; isolate external
port 8000 and drain all requests; plan/apply a coordinated stop using the promoted
root config; from the retained clean checkout plan/apply the original four-node
start using its named local config (`--replace --unqualified-shape`, as qualified
in the preserved recovery runs). Read the plans' live/shape lines; never use
`--change-shape`. Verify exact old identities and private API, reopen ingress,
verify original gateway aliases, then release the hold. No Docker restart,
weight patching, driver changes or manual one-rank launches are involved.

`previous/` freezes the earlier upstream root recipe/lock for historical tests
(the recipe adds only an explicit pointer to its frozen lock). Thirty-three
historical arm/lock references are pinned to these copies instead of floating
with the promoted root; [the mapping](historical-reference-map.json) records
old/new hashes, and a test reverses only those pointer edits and verifies the
original byte hashes. Patch series, published baselines and raw receipts are
unchanged;
the site's actual rollback is the separately preserved r6x identity above.
