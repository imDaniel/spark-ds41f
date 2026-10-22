# Genuine r6x baseline: source-only admission delta

Status: CPU-tested source adaptation, not an execution freeze or GPU result.
Historical `r6x188` and `native197-off` retain their original meaning and pins.
The new selections use the same schema 6/7 entry points, guards and fixed tests.
There is no arbitrary-image, arbitrary-argv or alternate deployment interface.

## Fixed source and retained image identities

- `r6x-unpatched/absent`: image `sha256:fc6a9ef83dd1a741a32c2724263e9c05a2635dc8154784ffe0f9c36709018082`, component head `7316c7f9fae1bc4a0b7ab1848de0db4143eec5d2`, tree `4d6851c8bb1397a55254ba88905a87d9785b98dc`.
- `r6x-steer`: image `sha256:8ad0da0366af0560678e09dbccefedc6688a58d79758b4f11fc63ef69ede4a0a`, component head `ebb44766d6f04d3a9272b712778d7f2fa2cec1fd`, tree `ac79557915e3ee2778f8f6353a3d35f848f564e4`.
- Upstream deployment head `a125256eb47f2d76f077117cf6e437eb42bfaa1d`, tree `9834bc0d20f29bce89e8dec8c01162923110411f`, 176-patch manifest SHA256 `6519df98b2039f508a0b38677ecef889201c8117325cf944a8b5a787844a5f7c`.
- S is exactly U plus the twelve exported 0500–0511 steering patches. The ordered list of `{path, sha256}` objects, using each patch basename, canonical JSON with sorted keys and compact separators, hashes to `e6bdcb3147290a6cb7df6471bcb2c3e658a8780b1e594a561e59875bcf0cc1e0`.
- Both retained images share native parent `sha256:a63259a196d82dae51016d487ed43093f2045261adcb41045cfe9c1be29f70d2`. Frozen RootFS and non-vLLM component identities are in `REVISED_FREEZE`. Historical input-record and build-log hashes identify retained evidence, not a newly completed build or current runtime capture.

The historical source overlays verified 7678 U / 7699 S source files and 32
unchanged native libraries. U removed the parent's steering-only files. These
facts support using the retained images; neither an old label nor a Python
source overlay alone requires rebuilding native libraries. Current effective
filesystem/import-path qualification is a separate, still-unbound requirement.

## Exact native source compatibility checked

Read-only inspection of both native Git trees verifies every selected hash in
`REVISED_SOURCE_SHA256` (19 U / 31 S files). The U worker extension CLI/config,
worker injection, safe string RPC route and route registration already exist.
The relevant sources are identical between U and S: `engine/arg_utils.py`,
`config/parallel.py`, `v1/worker/worker_base.py`, the API launcher/serve RPC files,
`envs.py`, and `v1/serial_utils.py`. No steering API or serialized callable is
needed to observe U. Insecure serialization stays disabled.

All thirteen existing performance metrics are native U counters/gauges or
histogram samples in `v1/metrics/loggers.py` and `v1/spec_decode/metrics.py`.
There are no steering/Q03 metrics added to the request contract. Both native
tokenizer modules have exactly equal U/S hashes. This is source compatibility,
not proof of mounted checkpoint/tokenizer assets or successful native execution.

## Behavior changed, and deliberately unchanged

- U has only `absent`, never `unloaded`; its requests contain no steering fields.
  Its profile contains no steering configuration or mount, and its source tree,
  imported modules and actual model must have no steering capability.
- S retains `unloaded` and `loaded0`–`loaded4`, exact profile artifact/request
  proof and present steering capability. Reusing the exact 8ad image under this
  truthful new S identity is permitted. It cannot be relabelled as U.
- Neither revised arm accepts the later Q03 model patch or runtime switch.
- The existing renderer, safe worker observer, all-four-rank readiness, idle
  checks, controller bounds, private evidence and source pinning remain.
- Both use service context limit 1048576, TP4, identical model/tokenizer assets,
  common runtime and common serving recipe. Removing the declared image/source
  labels, source/build references and steering delta must yield the same frozen recipe. Quality remains
  the existing 60-case material and decoder contract; performance remains the
  existing fixed cases. Separate historical-method adapters are not in this patch.

## Exact freeze blockers

`validate_revised_freeze()` rejects both new arms before file reads or operations
while any required field is unbound. The retained S profile (`e935b492...`), shared recipe
(`cb76902e...`) and expected effective configuration (`a144ea7b...`) have been
verified from exact file bytes and frozen. The remaining five hash values are:

1. Actual U normal profile file (`profile_sha256["r6x-unpatched"]`).
2. Actual shared runtime/package/native-library/path closure (`common_runtime_sha256`).
3. Mounted model weights/tokenizer asset identity (`model_tokenizer_sha256`).
4. Two actual immutable runtime manifests (`runtime_manifest_sha256`).

A deployable freeze additionally needs the existing schema's concrete corpus,
site inventory, independent checkout/source pins and publication evidence. The
historical build receipts do not substitute for current image config, import
origins, package/native bytes, actual model mounts or four-rank runtime evidence.
Existing trusted immutable-image evidence or a bounded inspection can provide
that evidence; a fresh build or full filesystem export is not assumed necessary.
The source-only tests do not supply any of these missing bindings.

## CPU regression scope

The added tests cover fail-before-operations for both schema entries, each
missing freeze field, truthful image reuse, exact image layers/native parent,
absent-versus-unloaded capabilities, source inventories, shared runtime,
fixed 1M plans and four-rank readiness. Existing baseline, legacy replay,
quality capture/client/scoring and HTTP guard tests remain applicable. Loopback
quality fixtures exercise both new selections without a native service.
No image build, deployment, model load, device access or GPU test is performed.
