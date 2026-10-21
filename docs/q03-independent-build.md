# Independent mHC validation build and P0 handoff

This is an execution plan, not a build or device result. Run only through the
reviewed independent window controller, after its live preservation, hold,
request, idle, identity, GPU-ownership and resource checks. Do not run these
steps as an alternative entry point when a guard refuses.

The protected production checkouts are read-only. Candidate deployment source,
component source, build inputs, writable caches, output, container names/ports
and image tags must be independent. Do not fetch, checkout, sync, edit scripts
or create outputs in either protected service's tree. Do not invoke a legacy
window helper with a different restore target, or nest the registered service
controller's transition hold inside the independent window's hold.

## Inputs and immutable identities

The external site capsule contains private paths and before-inventories; it is
not committed here. Bind the exact reviewed deployment revision, component
HEAD/tree, mailbox fingerprint, two protected service identities, restoration
policy and controller hashes. Resolve all paths/symlinks on the actual host;
check candidate and protected paths are disjoint and proposed ports/tags/names
are unused. A sample profile is never authority for a real site node map.

Stage tests from a standalone clean component Git checkout with a self-contained
`.git` directory. A linked worktree's `.git` pointer may reference an unmounted
outside directory, and cannot satisfy P0's Git identity checks inside Docker.
Mount that whole checkout at `/validation:ro`, not as an import overlay. Test
files are not assumed to be installed in the image.

## Typed build preparation and image build

From the independent, clean, published deployment checkout, the project command
vectors below use the separately derived, reviewed site profile, not the repository
sample node map. The offline profile generator writes only new ignored files
in the independent checkout after checking the supplied input hashes. Commands are:

```
bin/spark --shape tp4-ring --cluster-config config/q03-window.local.json build prepare
bin/spark --shape tp4-ring --cluster-config config/q03-window.local.json build check
bin/spark --shape tp4-ring --cluster-config config/q03-window.local.json build image --tag UNIQUE_NEW_TAG
bin/spark --shape tp4-ring --cluster-config config/q03-window.local.json build image --tag UNIQUE_NEW_TAG --apply
```

The final command belongs only in the controller's fixed typed build job. The
non-apply command prints a plan, not a successful build. Do not allow an arbitrary
shell command or user-supplied executable as a job. Preparation writes only the
independent checkout's `.work/build`; private site metadata can remain external.
All complete contexts, pinned submodules and hashed wheels must pass build check.

The original build requires 64 GiB MemAvailable at start, reserves24 GiB and
cancels below that reserve. It sizes jobs at8 GiB each, reserves4 CPUs, uses2
NVCC threads and caps jobs at10. Preserve its lock, memory sampling and process
group cancellation. A successful build includes GPU import smoke and writes
`.work/build/<identity>/images/<image-id-prefix>.json`; do not call it CPU-only.

The built-in idle-name check alone is insufficient for arbitrary site service
names. The independent window must prove BOTH protected services are stopped
or in their authorized nonrunning initial state, and reject unexpected GPU
owners, before build or smoke. Current production low free memory is not a
reason to lower thresholds or reduce original KV capacity.

The build tag must be fresh. It is never an original image tag or a tag reused
for a rebuild. On success freeze the returned OCI image ID and verify its source
and deployment labels against the build receipt; all later containers use that
ID, not the mutable tag. A retry gets a new output/run/tag identity. Build once;
if later multi-node tests need the image, distribute exactly the same digest
under the independent plan, never overwrite original tags.

Candidate compile/runtime caches must be dedicated. The project's own Dockerfile
uses BuildKit cache mounts; use an independently provisioned buildx builder/cache
namespace when required by the site preservation plan. Do not assume default
shared builder state is private or disable any guard to work around setup.

## Runtime manifest is a verified build artifact

P0 requires a reviewed runtime manifest with candidate source HEAD, installed
package root/mode, exact Python/Torch/TileLang/TileKernels versions, and both
native vLLM extension paths and SHA256 values. Construct it only after binding
these observations to the successful candidate build's immutable image ID,
source/deployment labels, original build command/log and image receipt.

Observe native hashes from that exact immutable image; verify the two rebuilt
extensions are ELF and at the paths the reviewed Dockerfile installs. Compare
all P0 source-file hashes to the frozen component bytes. Record actual versions,
then compare them with the approved target set. If they differ, stop and review;
do not rewrite expectations to whatever an unchecked runtime happens to contain.
Image inspection or file hashing without this build/source binding is not enough.

Important: the project's Dockerfile installs serving source and rebuilt native
extensions under `/opt/spark3/candidate/vllm` and sets its own image-native
`PYTHONPATH=/opt/spark3/candidate/vllm:/opt/spark3/candidate/b12x`. Preserve this
verified baked-in layout. Do not inject the host `/validation` tree into
PYTHONPATH and do not clear the image's own setting, which could expose baseline
site-packages. Describe the installation truthfully as the built image's source
layout, not an invented wheel install. The P0 launcher must explicitly support
that reviewed mode before such a manifest is accepted.

The runtime manifest is external, mounted read-only separately from outputs.
Unknown image IDs, native hashes, installation paths or versions stay pending;
no placeholder may pass an execution preflight. The build does not change the
protected production files or prove numerical/GPU correctness by itself.

## First-head P0 and later gates

The factory must produce a bounded Docker job on the approved head with exact
image ID, isolated container name, `/validation:ro`, runtime manifest read-only,
dedicated `/cache:rw` and `/results:rw`. Do not mount Docker socket, protected
source directories or protected writable caches. No service ports are required
for P0. It must preserve approved image-native imports and record full exit/logs.

From `/validation`, the fixed launcher form is:

```
python3 tests/standalone_tests/deepseek_v41_q03_registration.py --run --allow-platform-initialization --expected-platform cuda --expected-head FROZEN_COMPONENT_HEAD --runtime-manifest /runtime/runtime-manifest.json --output-dir /results/p0-registration-run-01
```

P0 imports full serving modules and can initialize CUDA. Its two isolated modes
must both pass; it is not numerical execution, full opcheck or functionalization.
The exact component HEAD and launcher revision must be set by the current frozen
release, especially if installation-mode support requires another test patch.
Do not execute an old launcher with a newly invented manifest mode.

Stop on any P0 failure before G1. The frozen validation runbook defines G1's exact
20-case and G2's12-case inventories and receipt checker. If component tests are
changed, freeze/replay a new candidate and expected inventory before execution.
G2 is a bounded producer-to-plan test, not full MoERunner, model or TP4 coverage.

## Restoration is part of the session

The implemented build window permits automatic restoration only after a
durably recorded normal build plus smoke completion, or a proven pre-recipe
abort after its owned process group has drained. Normal completion additionally
requires the exact original independently provisioned builder identity before
cleanup. A timeout, signal, nonzero exit, missing completion marker, missing or
replaced builder, or uncertain Docker operation is UNKNOWN: retain the hold and
require manual evidence-led reconciliation. Protected services may remain
stopped; automatic restoration is not promised for abnormal build outcomes.
Never infer daemon quiescence from local process termination alone. See
`q03-window-session-runbook.md` for the fixed session and recovery procedure.
A service initially stopped remains stopped; do not call its start merely to
obtain a green health probe. For a running service with a previously authorized
pending policy update, restoration follows the registered policy, not blindly
the older observed running label. Record that transition separately from Q03.

Require all expected ranks, image/source/config/steering/capacity identity,
readiness and preservation checks before releasing the hold. Retain the hold
and evidence if restoration fails. Do not use candidate success to erase failed
attempts, existing warnings or preservation differences. No automatic promotion,
main merge, driver/kernel/firmware change or original-service file rewrite occurs.

Implementation status: typed build dispatch and the bounded normal-success-only
ownership adapter are implemented. Exact published release identity, integrated
CPU tests and actual private site preflight remain execution prerequisites.
These command vectors are plans, not hardware results or a claim of universal
crash-safe recovery. Candidate-internal cache nesting
is allowed when original-service protection and runtime mount checks pass.

## Private site parameters

Public tools do not contain live site roots, accounts, interfaces, ports or policy
hashes. Supply the independently reviewed private parameters at invocation time.
The profile generator additionally requires `--independent-root`, repeated
`--protected-root` and `--protected-port`, plus explicit `--http-port` and
`--master-port`; it preserves the supplied baseline's sizing, network and host
fields rather than embedding site defaults. These supplied values do not replace
live preflight or the independent preservation descriptor.

Builder planning/provisioning uses:

```
python3 scripts/q03_buildkit_plan.py --identity RUN_ID --root NEW_METADATA_LEAF --independent-root APPROVED_PRIVATE_ROOT --buildkit-image moby/buildkit@sha256:REVIEWED_DIGEST
```

The placeholders must be replaced from the reviewed plan. Add `--apply` only for
provisioning in the authorized environment. Export all four returned environment
keys for the later guarded build. The fresh-provision receipt records resource
preabsence, observed container ID/creation timestamp, actual labels (possibly
empty), dedicated volume and a canonical immutable configuration fingerprint.
Cleanup verifies this receipt and journal without requiring the builder still
be running; missing or mismatched evidence refuses cleanup. This helper itself
never removes resources. A dedicated BuildKit cache is not an unprivileged
security sandbox. No Docker provisioning was performed during CPU-only tests.
