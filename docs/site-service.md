# Site service controls

The owner's 2026-10-24 instruction retires the physical r6x deployment and its
node-local configuration. This supersedes the earlier asset-retention decision;
published baselines, the archive branch and raw validation records remain immutable.
The accepted 1022 compute recipe, image, profile, gateway and model weights stay
unchanged. No performance rerun is required for this control-plane change.

`host/site/` owns the fixed 1022 backend and the updated ordered shutdown program.
`render_controls.py` renders reviewed host policy into a staging directory from a
clean published checkout and a snapshot of the existing host controls. It never
installs files, starts a model or schedules poweroff. The generated policies pin
the deployment commit, source hashes, profile, image, full command, configured
environment and mounts on every rank. GLM registration and permissions stay intact.

Install the reviewed root-owned files together under an exclusive cluster hold,
verify hashes and `visudo`, then verify the backend probe, registry status,
service check and power dry-run. The service must be recognized as serving and
own every GPU PID, with all four memory guards active. Exercise transitions only
with explicit authorization; the controls can adopt the already running service.
Keep no automatic model restart/recovery or Docker restart policy.

After acceptance remove the old r6x registration, launcher, backend, sudo rule,
stopped container, node-local runtime checkout/configuration and obsolete physical
reinstallation bundle. Preserve model weights, steering direction source and
current caches; no broad Docker prune or manual layer deletion is part of this work.
Old image layers shared by the current image remain Docker-managed.

For a future r6x return, check out
`archive/r6x-attention-steering-20261023` (`223a3bb3498d12f470e876db3b9e1f5bdc8772b6`),
replay its pinned source series and rebuild. Recreate the site's node map from the
current cabling, bind the historical profile from the direction source/backup and
validate its historical identity before a new coordinated deployment. This is a
rebuild procedure, not an instant physical rollback; no rebuild is claimed to
have been run as part of retiring the old installation.

The old artifact names in the accepted baseline identify the tested image and
container; the operational service ID is `deepseek-1017-attention-1022`, using
`config/cluster.json`. A new service name does not imply a new compute build.
