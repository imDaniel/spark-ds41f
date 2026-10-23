# Recovery review for the 1022 comparison

This review targets the service actually accepted at this site. It does not
adopt `config/cluster.json` as the recovery recipe. No lifecycle action was run.

## Frozen recovery identity

`recovery-policy.json` pins repository revision `223a3bb3498d12f470e876db3b9e1f5bdc8772b6`,
image `sha256:8ad0da0366af0560678e09dbccefedc6688a58d79758b4f11fc63ef69ede4a0a`,
the retained r6x source directory, exact configuration and node-map hashes,
the old `d7f0b997…` profile, model path, cache path and read-only model/profile mounts.
All four ranks matched these identities during the read-only inspection.

The retained start **plan** was evaluated using the retained checkout and its
named site configuration. It reports four nodes, TP4, 1,048,576 context,
10.5 GiB KV per rank, the expected image, and `shape: unchanged`.
See `runs/retained-start-plan.txt`. No `--apply` was used.

A complete Git bundle of the archive branch, exact ignored site configuration
files and a copy of the retained image are kept in the local offline artifact
directory. The original model, cache and profile remain on every node. The
inspection counted 48 model shards; it did not reread hundreds of GB of weights.

## Findings and disposition

| Finding | Disposition for this experiment |
|---|---|
| `scripts/lab.py` requires and later checks out `origin/main`; the default recipe is upstream 1022 | Do not use its window/sync/watchdog entry points for this comparison. Do not merge PR #56 to satisfy this guard. Candidate preparation must use an explicitly published, frozen revision in its separate directory. |
| The old r6x acceptance watchdog restores `2e99502f` / `a63259…` | Obsolete for this window. Recovery uses `recovery-policy.json`, not that historical script or the older rollback field in the 2026-10-19 baseline. |
| A healthy retained service could be restarted by a generic restore action | The reviewed decision is a no-op plus verification when all four retained ranks are healthy. |
| An interrupted launch can leave a partial set of ranks | Stop the candidate on **all four** configured nodes with its own recipe, verify it stopped, then reobserve. A partial/unhealthy retained set also requires a coordinated stop before its coordinated start. |
| Missing queue telemetry can be mistaken for idle | Both running and waiting metrics must be present and zero for at least 30 seconds. Block new requests at the agreed client/gateway ingress before draining. Do not terminate an active user request to meet the time budget. |
| A timeout or failed stop can allow old and new engines to overlap | Do not start the retained service until candidate containers, their memory guards and owned component jobs are confirmed stopped on every rank; any unknown GPU process blocks restoration for operator review. |
| 02–04 have no current LAN address | The Mac connects to `192.168.88.125`; 01 reaches the other nodes through their existing internal management addresses. The independent watchdog must run on 01. A failure of 01 itself requires console/out-of-band access; software rollback is not a guarantee for that case. |
| A lease may be released before service recovery succeeds | Retain the hold on any failed identity/readiness/doctor check. Release only this window's hold after full recovery and gateway verification. |

`tools/recovery_contract.py` expresses these admission and recovery decisions.
Its 35 fault tests cover missing/duplicate ranks, changed source/configuration,
profile/image/mount drift, foreign jobs, stale ownership, partial launches,
missing queue telemetry, and healthy-service no-op. It is deliberately a
**plan-only review tool**, not an installed runtime controller. Passing these
tests does not prove watchdog deployment or a physical restore.

## Required execution wiring before a later window

1. Obtain the owner's explicit runtime authorization. Freeze and publish the
   prepared candidate revision in this fork; keep PR #56 unmerged.
2. Stage one verified image archive/profile release and a clean candidate
   checkout on all four ranks. Verify IDs/hashes before reserving downtime.
   Keep the retained checkout and its ignored site files unchanged.
3. Prepare a separate execution wrapper and recovery watchdog on 01 with this
   policy. Both must use one operation lock. Run CPU-only injected-failure
   tests of their **actual command wiring**, including controller loss during
   launch, failed rank stop, recovery failure and hold retention. This wiring
   and its installation are not included in this offline review and remain
   an explicit pre-window gate.
4. The watchdog must run independently of the Mac, use a window UUID, refresh
   a heartbeat during long work, stop only recorded owned jobs, and restore
   the pinned retained service after heartbeat expiry. The historical 900 s
   expiry is a trigger delay, not a 15-minute recovery SLA. Never take over
   another holder's lease. No automatic retry after a GPU/driver fault or
   management-path loss; halt the experiment and escalate for review.
5. Confirm the hold is free, snapshot all ranks and gateway aliases, drain
   traffic, then obtain the hold atomically. Read the printed `live:` and
   `shape:` lines before every authorized application. Never use
   `--change-shape`, a one-rank launcher, or a dirty checkout.

## Recovery sequence after an authorized window

Use the existing retained directory; recovery needs no Git fetch, package
installation, model copy, image build or network download.

1. Stop scheduling experiments and terminate only owned component jobs.
   Stop every candidate rank using its exact `bin/spark ... cluster stop`
   recipe; confirm containers, GPU processes and memory guards are gone.
2. Revalidate retained source, ignored configuration hashes, image ID and
   profile on all ranks. Reobserve the retained service. If already healthy,
   verify and leave it running. If partial, stop all its ranks first.
3. From the retained checkout, inspect the pinned start plan in
   `runs/recovery-plan.txt`; apply only after the above checks and the later
   runtime authorization. The existing startup memory guards remain active.
4. Require all four ranks on the retained image, then `doctor --live`,
   `/health`, `/v1/models` and one bounded stock/steered request through the
   agreed API/gateway entry points. Check the original aliases, capacity,
   cache/mount identity, errors and restart counts. These request checks have
   **not** been performed during phase 1.
5. Release only the matching hold after success. Keep receipts and stop further
   experiments on failure; do not remove the hold merely because time expired.

The 15-minute restoration allowance assumes a responsive 01, healthy hardware
and available retained assets. A failed node or driver fault can exceed it.
