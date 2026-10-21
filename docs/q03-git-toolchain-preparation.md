# Reviewed host Git bundle preparation

The candidate image is unchanged. This host-only preparer copies the specifically reviewed installed Ubuntu ARM64 Git, its loader/libraries and package copyright/license files into a new independent directory. It never runs Git, ldd, the copied loader, imports the launcher or executes container code. It downloads/installs nothing and does not alter host or original-service configuration.

Use the published controller checkout containing both scripts/q03_prepare_git_toolchain.py and scripts/q03_git_toolchain.py. The destination parent must already be canonical, owner-controlled and inside the existing private authority's independent_root; the destination leaf must not exist. Protected roots remain excluded by the existing authority, not by public site literals.

Host read-only plan:

```sh
python3 -I -B "$CONTROLLER/scripts/q03_prepare_git_toolchain.py" \
  --output "$NEW_GIT_TOOLCHAIN_DIRECTORY" \
  --authority-path "$EXISTING_PRIVATE_AUTHORITY" --authority-sha256 "$REVIEWED_AUTHORITY_SHA256" \
  --source-inputs "$REVIEWED_HOST_SOURCE_FACTS" --source-inputs-sha256 "$REVIEWED_SOURCE_FACTS_SHA256"
```

After plan review, the same arguments with --apply copy into the exclusive fresh leaf. No container action is implied. If a write fails, retain the partial directory as evidence; never overwrite it. All runtime files are regular copied bytes, not symlinks. Copied runtime executables are0555, libraries/licenses/launcher/manifest0444, directories0555.

Expected installed package identities: git1:2.43.0-1ubuntu7.3, libc6 2.39-0ubuntu8.9, libpcre2-8-0 10.42-4ubuntu2.1, zlib1g1:1.3.dfsg-3.1ubuntu2.2, all arm64 and installed. Git /usr/bin/git must be root-owned0755 and SHA256 aa6540695d076182256dd6e96c8b302e4d56381e3000bbfd5c71bbdfe94a4942. ELF64 little-endian/AArch64 identity and dependencies are parsed as data. Observed dependency closure is loader, libc.so.6, libpcre2-8.so.0, libz.so.1. Unexpected dependencies refuse preparation.

Bundle layout: real/git, lib/{ld-linux-aarch64.so.1,libc.so.6,libpcre2-8.so.0,libz.so.1}, launcher.py, bin/git fixed shim, licenses/{git,libc6,libpcre2-8-0,zlib1g}/copyright, and licenses/common/{GPL-2,LGPL-2.1}. Manifest records all thirteen payload file hashes/sizes/integer modes/source paths and package metadata. Its external SHA256 is emitted only after preparation; it is not invented here. Package database membership and version are checked, but are not a cryptographic re-download verification of installed package archives.

The prepared manifest is UNREVIEWED until actual library/license/launcher hashes and package metadata are returned and reviewed. Pin its exact byte SHA and bundle path in the next candidate manifest. Only the existing coordinated STOP-gated validation window may execute the mounted launcher/Git. No global LD_LIBRARY_PATH or fake Git emulation is used. A cloud syntax/mock result is not ARM64 execution qualification.

The private source-inputs document pins all eleven requested/resolved source paths, root ownership, original mode strings, sizes and SHA256 values. Merged-/usr aliases are allowed only when their resolved targets exactly match those facts. Every payload is copied from a verified byte snapshot. Output paths remain strictly canonical with no aliases. Target manifest modes are integer permissions, separate from input strings. The private facts remain outside Git.
