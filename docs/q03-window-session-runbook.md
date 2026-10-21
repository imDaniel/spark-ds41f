# Staged isolated validation sessions

This is a plan, not a device result. The reviewed source enables a
**normal-success-only build window**, not universal crash-safe restoration.
Buildx can recreate a missing builder after a precheck; abnormal build outcomes
therefore remain UNKNOWN until manually reconciled. The frozen published
deployment identity, integrated tests and actual private site preflight are
still prerequisites. The commands below do not claim that a device run occurred.

The adjacent JSON templates contain
literal `<...>` placeholders and **are not executable manifests**. Fill them
from the private, reviewed site evidence on the actual execution host. Do not
commit the filled manifests, inventories, SSH details, or service identities.
Never use an example profile or a running process as restoration authority.

## Required release and site inputs

Before any session, record:

- The published exact deployment branch ref and 40-character commit in the
  owner-controlled fork. The running core and every sibling helper must come
  from that clean independent checkout, not an older worktree or overlay.
- The frozen component HEAD/tree from `validation-selection.json`; the templates
  name the 197-patch component. Do not alter the frozen 610 candidate or substitute
  a baseline installation. Materialize a clean standalone component checkout
  with its own `.git` directory for P0.
- The approved independent root and complete protected namespaces, both lexical
  and resolved. All candidate source, cache, metadata, outputs and containers
  must remain independent. Check ownership, free disk and symlink resolution
  before creating the independent root. Do not change original files to pass.
- The existing registered restore policy path/hash, original source revision,
  `bin/spark` hash, restore profile hash, image ID, container identity, and both
  registered controller hashes. These are private inputs, not invented values.
- Four complete protected-inventory receipts with hashes, actual node names,
  ranks, SSH destinations, and exactly one local rank 1. Preserve existing key
  files; the manifest contains only the already-authorized identity-file path.
- Actual shared hold/request and legacy guard paths. Do not create alternative
  guards or remove a foreign/stale/unknown hold to make the session proceed.
- Identical verified remote helper bytes and a private authority JSON on all
  four nodes at the declared independent paths. The private authority follows `remote-authority.template.json` and contains
  `independent_root` and `protected_roots`; hash it and the helper. Provisioning
  these independent files is an explicit local-executor prerequisite, not an
  action hidden in the window controller.

If private evidence identifies a preexisting difference between the observed
live config and the already-registered restore policy, preserve both files and
the policy unchanged. Restore the registered policy, not an old profile inferred
from the running process. Record any expected first-effective flag transition
separately; it is not a Q03 benefit. Preserve the second service's evidenced
nonrunning state on every node, whether stopped or absent. Never start or stop
that service; its registered image/config/runtime/profile remain protected.

The preflight captures the observed stopped/absent identity automatically. Do
not invent container IDs or manually prepopulate the generated `glm_identity`.

## 1. Prepare independent build inputs

Read `q03-independent-build.md` and the reviewed BuildKit provisioning plan.
Prepare only the independent checkout and ignored candidate profile/map using
hash-verified registered inputs. Do not sync/fetch/checkout the original tree.
The fixed candidate profile is `config/q03-window.local.json`; its selected
node map is independent and retains the actual four-node topology.

The deployment and BuildKit identity placeholders below must be replaced with
reviewed values. Select an existing approved Python interpreter; the examples
use the independent environment's `.venv/bin/python` only if it exists and is
verified. Do not install software implicitly to satisfy the example.

```sh
DEPLOYMENT='<ABS_INDEPENDENT_DEPLOYMENT>'
PYTHON="$DEPLOYMENT/.venv/bin/python"
BUILD_MANIFEST='<ABS_PRIVATE_BUILD_SESSION_JSON>'
BUILD_SESSION='<ABS_NEW_INDEPENDENT_BUILD_SESSION_DIRECTORY>'
TEST_MANIFEST='<ABS_PRIVATE_TEST_SESSION_JSON>'
TEST_SESSION='<ABS_NEW_INDEPENDENT_TEST_SESSION_DIRECTORY>'
```

The official preparation/check commands are:

```sh
cd "$DEPLOYMENT"
bin/spark --shape tp4-ring --cluster-config config/q03-window.local.json build prepare
bin/spark --shape tp4-ring --cluster-config config/q03-window.local.json build check
```

They prepare CPU/network inputs, not an authorized alternate route to GPU smoke.
Preserve all official memory/lock/recipe checks. The GPU-containing build command
must run only inside the typed build session below, after coordinated stop.

Provision a fresh dedicated builder using `scripts/q03_buildkit_plan.py` and
its reviewed exact pinned BuildKit image. Its default is plan-only; `--apply`
creates only the independent builder/metadata and records fresh preabsence.
The exact generic helper CLI is:

```sh
"$PYTHON" -B "$DEPLOYMENT/scripts/q03_buildkit_plan.py" \
  --identity '<UNIQUE_Q03_BUILDER_IDENTITY>' \
  --root '<ABS_NEW_BUILDER_METADATA_LEAF>' \
  --independent-root '<APPROVED_ABS_INDEPENDENT_ROOT>' \
  --buildkit-image 'moby/buildkit@sha256:<REVIEWED_64_HEX_DIGEST>'
```

Append `--apply` only for the reviewed fresh independent provisioning step,
not to bypass a refused or UNKNOWN build window. Provisioning does not run GPU
qualification and does not itself solve the Buildx recreation race. Retain `provision-receipt.json` and `provision-log.json`. Do not reuse
the original/default builder, retag an original image, prune caches, or delete
volumes. Cleanup removes only exact independently owned builder/smoke container
identities and preserves builder volumes/evidence.

Fill `build-session.template.json`, replacing every placeholder and converting
integer placeholders such as the policy port to JSON integers. Bind the exact
prepared `inputs.json`, independent profile, fresh builder receipt and unused
candidate image tag by hash. The build output directory and session directory
must be new, separate from immutable input source. The example 24-hour ceiling
is not an instruction to reserve that long; choose a reviewed bounded duration.

## 2. Run one build-only window, then restore

```sh
"$PYTHON" -B "$DEPLOYMENT/scripts/q03_window.py" run-session "$BUILD_MANIFEST" --session-dir "$BUILD_SESSION"
"$PYTHON" -B "$DEPLOYMENT/scripts/q03_window.py" run-session "$BUILD_MANIFEST" --session-dir "$BUILD_SESSION" --apply
```

The first command only prints a job summary. It does **not** validate the manifest,
prove idleness, or authorize execution. The second performs actual preflight,
owned hold/watchdog/GPU lock acquisition, coordinated original stop, the typed
build, cleanup and registered-policy restoration. Do not run the adapter or
raw `bin/spark ... build image --apply` separately to bypass a refusal.

The integrated build helper must report `uses_supervised_adapter=true` and
`requires_supervised_quiescence=false` only after its adapter/ownership review.
The reviewed normal-success-only design requires a durable successful completion
marker (`official_build_completed`, after normal return including smoke) and
an exact unchanged builder identity. `recipe_launch_intent` is durable before
the official call. A proven pre-recipe abort can restore only after group drain.
A timeout, signal, nonzero exit,
crash, missing marker or replaced builder is UNKNOWN: retain the hold and require
manual evidence-led reconciliation. Automatic original restoration is not
promised in those cases; originals may remain stopped. Do not treat local
process termination as proof that accepted Docker operations completed.

An earlier, blocked helper refuses before mutation. This refusal is not permission to
edit the flags locally. A build-only manifest has no candidate image requirement,
so there is no prebuilt-image bootstrap dependency.

Require successful exit plus `state.json` showing `outcome=completed`,
`restored=true`, and `hold_released=true`. Review `build-result.json`, original
restoration observations, unchanged protected inventories, unchanged evidenced nonrunning
second service, and the separately recorded preexisting flag transition.
A successful official GPU import smoke is **not** P0/G1/G2 acceptance.

## 3. Capture and review image-native identity without GPU access

Only after verified restoration, inspect the exact
`build-result.json.runtime_manifest_capture_plan` from that successful session.
The fixed plan uses the built immutable image, no GPU/device exposure, no network,
a read-only root, independent ephemeral name/tmpfs, and no original writable
mount/cache. Verify its image/source, command and output path, hash the plan,
and check adequate current CPU/memory before the local executor runs that exact
argv with its declared 120-second timeout. The reviewed plan forces `runc`
and `NVIDIA_VISIBLE_DEVICES=void`; preserve those settings. Do not add `--gpus`, device mounts, host Python
source overlays or original cache mounts to overcome import failure.

The plan is emitted as argv, not a shell command. Execute it as argv through the
approved local executor, saving stdout exclusively to its declared independent
output. Preserve stderr and the exit code separately. Do not use `eval` or run
an arbitrary command substituted into the receipt. Failure is an identity-capture
failure; leave originals running and review it, rather than claiming qualification.

Review the captured `source_head`, image-native package location, versions,
native-library paths and hashes. The baked image source path is legitimate;
do not clear it and accidentally import baseline site-packages. Save a reviewed
runtime manifest separately and hash its exact bytes. No capture has been
executed as part of authoring this runbook.

## 4. Run P0 followed by G1 in a second window

Fill `test-session.template.json` with the actual image ID, hashes of the completed
build result, official inputs and reviewed runtime manifest. Pre-hold checks
reinspect actual image labels/ID and bind them to the frozen component and
published deployment. Use a fresh output directory and independent cache.
No image tag substitutes for an immutable ID.

```sh
"$PYTHON" -B "$DEPLOYMENT/scripts/q03_window.py" run-session "$TEST_MANIFEST" --session-dir "$TEST_SESSION"
"$PYTHON" -B "$DEPLOYMENT/scripts/q03_window.py" run-session "$TEST_MANIFEST" --session-dir "$TEST_SESSION" --apply
```

P0 and G1 run back-to-back under one hold, without a restore between them. Each
container is created, its exact owned ID durably recorded, then started by ID.
P0 mounts the standalone checkout read-only for Git checks. G1 mounts only its
tests subtree, preventing a host component-source import overlay. Both retain
the image-native runtime and write only independent cache/output paths.

Require all P0 default/string registration receipts and the exact frozen G1
inventory receipt, not merely pytest exit zero or a case count. Zero cases,
skips, duplicates, missing receipts and cancellation are not success. Preserve
all errors/unfavorable results. G2 is not included in this first test manifest;
its later prefix must include P0/G1 and does not constitute full-model/TP4 service
qualification. Do not infer performance gains from build or registration checks.

## Recovery and evidence boundary

The capsule is generated by the core, never hand-authored or edited after start.
It binds manifest, window UUID/holder, core PID/start ticks, deadline, policy,
restore budget, recovery command and SHA256 of these exact sibling files:

1. `q03_window.py`
2. `q03_window_preflight.py`
3. `q03_window_watchdog.py`
4. `q03_original_spark_adapter.py`
5. `q03_remote_operation.py`
6. `q03_build_adapter.py`
7. `q03_window_build.py`
8. `q03_buildkit_plan.py`

`capsule.sha256`, `state.json`, command logs, original-transition epochs, remote
sealed receipts, watchdog records and phase observations remain independent
session evidence. Restoration must not depend on GitHub, a newly built image,
or successful candidate tests. The original policy/source/inventory still must
match. Recovery never silently substitutes an older config.

If the core is dead and an owned session needs explicit reconciliation, the
same pinned release exposes:

```sh
"$PYTHON" -B "$DEPLOYMENT/scripts/q03_window.py" recover --session-dir '<EXACT_EXISTING_SESSION_DIRECTORY>'
```

Do not race a healthy core/watchdog. Its locks and owned-hold checks must pass.
Unknown remote completion, malformed/foreign ownership, nonempty unknown local
process groups, build cleanup failure or changed original evidence retain the
hold and require evidence-led recovery, not forced hold removal. A possible late
inert candidate creation is recorded as such, not misreported as fully absent.
Never execute raw transitions to get around a sealed-epoch refusal.
