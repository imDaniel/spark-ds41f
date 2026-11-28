# Site service controls

The site selects the qualified 1126 Attention maintenance release from PR #60.
The four-rank backend and ordered shutdown program target the production
container `ds41f-1126-attention-fixes` in
`/srv/ai-cluster/runtimes/1126-attention-fixes/source/spark-ds41f`.
The display name is `DeepSeek v4.1 Flash 1017`. Existing clients retain the
service and launcher identities `deepseek-1017-attention-1022` and `deepseek-1022`.
[Site baseline](../manifests/baselines/2026-11-28-homelab-1126-attention-fixes-rc3.json) ·
[Qualification results](../experiments/2026-11-28-pr60-tp4/RESULTS.md)

`host/site/render_controls.py` renders host policy from a clean published
checkout and a snapshot of the installed controls. The policies pin the
deployment revision, CLI source hashes, frozen lock, node map, read-only
profile, image, complete rank commands, environment and mounts. Installation
preserves unrelated services and launcher registrations. The backend probe
uses the fixed admin identity for the coordinator and cluster SSH access.

For this promotion, acquire the global window and synchronize the new checkout
on all four nodes while the previous service is running. Verify the image,
profile and merged revision on every node before draining ingress. Stop the
previous four-rank group and start the new group with `config/cluster.json`. Use
the journaled host-control installation and validate the running image, model
and profile, four active memory guards and scheduler readiness. After reopening
ingress, verify one real completion each on `group/deepseek-v4.1-flash-sec-1.1`
and `group/deepseek-v4.1-flash-sec-4.0` through the existing gateway route.
The existing two-start benchmark is the performance reference for this release.
The activation and gateway receipts belong in `/srv/ai-cluster/operations/pr60-tp4-promotion/0086dc06-c4b9-4da5-9a4e-a9bc32903ed6`.

The previous formal 1126 runtime at
`/srv/ai-cluster/runtimes/1126-attention/source/spark-ds41f`, commit
`63b0c0d31e4378888951295325179211d00cbde6`, uses its own container, cache
and read-only profile. Recover that complete group and its saved controls if
cutover validation fails. The frozen recipe at
`releases/homelab-1126-attention/` records its build and configuration.
The historical 1022 source remains at `archive/1022-attention-20261128`
(`3e5eb7693f4b83c6232448270615e61e8e5520b2`).

The owner's 2026-10-24 decision retired the older physical r6x deployment and
node-local configuration. Its published baseline, raw evidence and
`archive/r6x-attention-steering-20261023` (`223a3bb3498d12f470e876db3b9e1f5bdc8772b6`)
remain available for a reviewed future rebuild. No r6x rebuild is claimed here.
Model weights, direction sources, active caches and unrelated applications are
preserved; no broad Docker prune or manual layer deletion is part of adoption.
