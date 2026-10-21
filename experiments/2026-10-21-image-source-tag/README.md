# Preserve the source build tag during same-image distribution

Base2f5babc1f8a85eed3b695f8aaac23a3cb4476df5 is preserved. First field
preparation stopped all four originals but rejected the correct90bd source
because it retained its candidate build tag. No mutating preparation intent,
peer transfer, load or owner test had started. The new keep-stopped cleanup
positively closed the window without starting original models.

Saving an image by ID produces the measured tagless archive without deleting
the source image's build tag. Require the exact immutable source ID, preserve
its tags, retain the hash-bound successful export receipt/tagless archive checks,
require newly imported peers tagless, compare Config/RootFS/OS/architecture and
preserve all pre-existing image/tag/container identities. No retagging, rebuild,
new image or native/owner fixture changes.

Actual Preparation.run CPU cases cover the real tagged source with tagless
peer images and original tag retention, reject wrong source ID before peer
operations, and retain archive-tag/hash negative cases. Cloud integrated
review must examine the full import path before another field window.
All failed field and CPU results are retained privately outside this repository.

Existing exact candidate images retain their original tags unchanged; only
newly imported peers must be tagless. The full before/after image comparison
rejects tag rewrites. Strict disk capacity guards remain unchanged.

Local full related Q03 suite:331 tests passed on the final source with canonical
TMPDIR; doctor and diff-check passed. An earlier default macOS temp root used
a symlink alias and produced22 canonical-root errors in330 tests; those logs
are retained. No source guard was relaxed to accommodate that test environment.
An intermediate330-test run with canonical TMPDIR also passed before adding
the existing-peer tag preservation case. Independent integrated review pending.
