# Site service controls

The site recipe selects the qualified 1126 Attention image and rebound profile.
The official 1017 weights and all direction bytes, alpha, schedule and request
scale range are unchanged. See the [site baseline](../manifests/baselines/2026-11-28-homelab-1126-attention.json)
and [qualification results](../experiments/2026-11-28-1126-tp4-qualification/RESULTS.md).

`host/site/` owns the fixed four-rank backend and ordered shutdown program.
The coordinator targets `ds41f-1126-attention` and the selected 1126 checkout.
The display name is `DeepSeek v4.1 Flash 1017`. The stable service/mode/launcher
identities retain `deepseek-1017-attention-1022` and `deepseek-1022` for existing
clients; these identifiers do not select an old compute image.

`render_controls.py` renders host policy from a clean published checkout and a
snapshot of existing controls. It never installs files, starts a model or
schedules poweroff. Generated policies pin the deployment commit, modular CLI
source hashes, selected frozen lock, node map, profile, image, full rank commands,
configured environment and mounts. Unrelated service and launcher registrations
are preserved.

The qualification's fixed `install_controls.py` prepares the nine target files
and three shared dependency snapshots under an owned global lease and resource
locks. Installation validates staged hashes and sudo rules, journals the
transaction, writes atomically and verifies the backend. Partial writes or a
failed probe restore the original files. Unknown later edits and changed backups
are refused. The independent window watchdog restores the accepted 1022 engine
and matching saved controls before reopening ingress if the window expires.
File installation runs as root; its readiness probe explicitly runs as admin,
the same identity required by the fixed service coordinator and cluster SSH key.

After the selected source is merged, synchronize every node cleanly to that
exact commit with the named canonical profile. Compare all running commands,
environments, mounts, image IDs and start times against the qualified fleet.
Prepare and render the controls, review their differences, then install them
through the fixed lease-bound installer. Verify backend and registry readiness,
all four active memory guards, configured model identity, GPU PID ownership and
power **dry-run**. Adoption keeps the already running qualified containers.
Operational receipts remain under
`/srv/ai-cluster/operations/1126-attention-20261128`; their final source pin is
created after the release evidence is committed.

No automatic model restart/recovery or Docker restart policy is introduced.
Transitions and actual poweroff require explicit authorization. The normal
window watchdog is removed after production admission is restored and the hold
is closed.

The prior accepted 1022 source is preserved at
`archive/1022-attention-20261128` (`3e5eb7693f4b83c6232448270615e61e8e5520b2`).
Its physical checkout, image and profile remain the qualification recovery target.
The additional `releases/homelab-1022-attention/` snapshots preserve its input
configuration and lock for inspection; rebuild using the exact historical Git
revision and its build system, rather than the current build system.

The owner's 2026-10-24 decision retired the older physical r6x deployment and
node-local configuration. Its published baseline, raw evidence and
`archive/r6x-attention-steering-20261023` (`223a3bb3498d12f470e876db3b9e1f5bdc8772b6`)
remain available for a reviewed future rebuild. No r6x rebuild is claimed here.
Model weights, direction sources, active caches and unrelated applications are
preserved; no broad Docker prune or manual layer deletion is part of adoption.
