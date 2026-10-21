# Isolated Buildx client pin

This source-only correction does not replace the system Docker CLI or Buildx.
Reviewed independent provisioning without automatic GPU probing, and read-only
identity checks, are preflight prerequisites. CPU suites, serving imports, image
and native builds, and GPU/model tests remain subject to the operator's
coordinated four-node service-stop gate. No execution is evidenced by this
document.

## Reason and provenance

Buildx v0.35.0 commit `a319e5b15052cf6557ceb666eb8ff6e32380b782`
[factory.go](https://github.com/docker/buildx/blob/a319e5b15052cf6557ceb666eb8ff6e32380b782/driver/docker-container/factory.go)
initializes GPU options to `all`; its driver-option switch has no disabling
option. Its [driver.go](https://github.com/docker/buildx/blob/a319e5b15052cf6557ceb666eb8ff6e32380b782/driver/docker-container/driver.go)
probes GPU support and attaches DeviceRequests. Setting the BuildKit container's
NVIDIA_VISIBLE_DEVICES alone does not disable that path. Do not use an invented
`gpus=none` option or relax the existing DeviceRequests rejection.

The reviewed isolated client is **v0.21.3**, commit
`7b5fecbd7a62d73843f7a73a6d4ec353c0555ef5`. Its
[factory](https://github.com/docker/buildx/blob/7b5fecbd7a62d73843f7a73a6d4ec353c0555ef5/driver/docker-container/factory.go)
and [driver](https://github.com/docker/buildx/blob/7b5fecbd7a62d73843f7a73a6d4ec353c0555ef5/driver/docker-container/driver.go)
do not contain that auto-GPU path. Version 0.21.3 also fixes the
[credential telemetry advisory affecting earlier 0.21 releases](https://github.com/docker/buildx/security/advisories/GHSA-m4gq-fm9h-8q75).
This is not a claim of general current security support or runtime compatibility.
The standard docker-container driver still sets `Privileged=true`. This pin
suppresses automatic GPU probing and explicit GPU DeviceRequests; it is **not**
hard GPU isolation or a security sandbox. Existing container inspection guards
remain necessary, and privileged BuildKit must be treated accordingly.

- [Official Linux ARM64 artifact](https://github.com/docker/buildx/releases/download/v0.21.3/buildx-v0.21.3.linux-arm64):
  65,208,472 bytes; SHA256
  `90157681a0a033b285e80d3350741452d7647994fb371c9ee3a423f2ca4f22cc`.
- [Official checksums](https://github.com/docker/buildx/releases/download/v0.21.3/checksums.txt):
  SHA256 `2146ff3dac8e61aff70d0fcc90a516f80c818b83f020b53d4e8675ec2c8f10fc`.
- Official GitHub release ID 206306100; artifact asset 238267470;
  checksum asset 238267461. Downloaded bytes were hashed and matched the official
  checksum. The binary was not executed during cloud preparation.

## Provisioning contract

Supply `--buildx-binary` with the canonical path of the staged official ARM64
file inside the approved independent root. A non-executable staged file is valid:
the helper only reads and hashes it, and does not change its mode or contents.
Use a **new** identity, metadata directory, container name and volume. Never
reuse or overwrite a failed attempt; retain its stopped container, logs and cache.
All existing image-digest, volume, network and no-device checks remain mandatory.

Only after byte verification and successful absence checks does provisioning
create the private `docker/config.json` and `docker/cli-plugins/docker-buildx`.
The plugin copy is executable; the exact empty config prevents extra plugin
directories from overriding it. Docker CLI v29.6.0's actual
[plugin discovery implementation](https://github.com/docker/cli/blob/v29.6.0/cli-plugins/manager/manager.go#L39-L47)
searches configured extra directories, then the config's cli-plugins directory,
then system directories. The private empty config and sanitized Docker/Buildx
environment therefore select this copy without modifying the global plugin.

The canonical plan includes the fixed plugin path/version/hash. `verify_live`
rechecks the config and plugin bytes before builder inspection. The existing
official-build `_builder` path calls that verifier and supplies the same private
DOCKER_CONFIG to the unchanged guarded recipe, so the pin applies to both
provisioning and building. Do not run the recipe with a manually substituted
environment or receipt.

The normal-success-only restoration contract is unchanged. Abnormal build
outcomes remain UNKNOWN, retain the hold and require manual reconciliation.
This patch does not authorize a new build, weaken production protection, or
establish that a real builder/runtime test has passed.
