# Held attempts and frozen source receipts

Mac source preparation authenticates the exact outer manifest projection. The
producer, receipt format and projection exclusions are unchanged. A held attempt
removes `held_window`, selects typed jobs, and appends its fixed attempt ID to
candidate cache/output paths. Those normal changes do not create a new source
publication or permit rewriting the original receipt.

The held controller passes explicit source-derivation context at admission and
before each gate. Host and publication validation regenerate the complete attempt
manifest from the frozen outer plus its typed attempt inputs, then compare
canonical JSON bytes, including otherwise unknown fields and JSON value types.
Only after that exact comparison may the original publication receipt be checked
against the outer projection. The selected publication must still belong to that
outer; bundle and receipt digests, checkout identity, and all existing source
checks remain required. Supplying held context cannot fall back to online
publication verification if source-delivery markers are missing.

This binding does not admit an attempt. The existing capsule, prior receipt,
command-plan, next HTTP selection, layout, hold, stopped/drain and time-budget
checks remain in the held admission path. Runtime checks still inspect the
derived paths. Ordinary non-held and final-close receipt verification stays
bound to the exact manifest supplied to it.

CPU regression coverage exercises the real source producer with tiny Git
repositories and fixture-local publication transport, followed by the real held
derivation and host/image/build/publication validators. Mac platform identity and
hardware-dependent checks are fixture boundaries, not Mac or GPU execution.
Negative coverage includes arbitrary drift, changed paths, forged outer input,
receipt tampering, untyped or excessive job inputs, source-marker stripping,
and propagation at admission and every gate.

Use this correction only in a newly frozen successor. Preserve every failed
window's source, capsule, manifest, receipts and refusal bytes. It neither
reopens a closed window nor authorizes START, model execution, a build rerun,
or migration.
