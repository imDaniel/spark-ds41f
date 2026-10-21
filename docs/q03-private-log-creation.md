# New-generation private operation logs

This source-only fix starts from `3a49389f7ba0e93f05bab1c9a52b4425d4dd7fde`
(tree `63f016bbf9389570c44c1279208b8dac324688f1`). It changes no runtime image,
promoted profile, model, historic capsule, historical log, or deployed helper.
Recovery of an existing window requires separately reviewed, explicitly bound
recovery authorization. This source change alone is not deployment authority.

## Failure and invariant

The original helper (`1ddb0c79d6e50fd71245cff1c53c32fe9bf862e1a57f4078e6fed1ecafd7484a`)
opened stdout and stderr in append mode with the default creation mode. Under
SSH umask `0002`, a successful child produced mode `0664` artifacts. The strict
reader correctly rejected their shared-writable mode; process success did not
mean complete log evidence. A local Linux reproduction preserves that failure
separately, including original helper bytes, raw logs and terminal receipts.

New output, error and supervisor logs use exclusive, dirfd-relative
`O_CREAT | O_EXCL | O_NOFOLLOW` creation with mode `0600`. Before use, the helper
checks an absolute canonical owned `0700` operation directory and an owned,
regular, unaliased `0600` file with one link. It verifies file and directory
identities before returning the descriptor. Existing logs are refused, never
appended, truncated, chmodded, repaired or silently replaced. Failed creation
leaves evidence in place and does not manufacture a terminal receipt.

The child inherits the caller's umask unchanged. Normal masks `0000`, `0002`,
`0022` and `0077` all yield `0600` from creation, before the gate releases. An
unusual mask removing owner read/write bits fails closed, without permission
repair. Both stream files must exist privately before spawning the child;
pipe/spawn failure closes all newly held descriptors. The strict log reader,
byte hashes, size limits, sealing rules and receipt classification are unchanged.

## CPU-only regression coverage

`tests/test_q03_private_log_creation.py` executes the actual helper launcher,
detached supervisor and a local inert child under all four normal masks. It
checks all three log modes, inherited child descriptors, distinct inodes,
ownership, raw binary bytes, hashes and strict-reader completion. No Docker
daemon, network, SSH, GPU or service is used.

Additional real-file fault probes cover existing regular files, symlinks,
hardlinks, FIFOs, directories, path aliases, creation-time replacement,
shared directories, ownership mismatch (injected), restrictive umask,
full-filesystem errors (injected), partial creation, pipe and spawn failures.
The retained historical `0664` fixtures must still fail the reader unchanged.
Existing remote-operation, stream-bound and remote-log regressions remain part
of the focused checks. Full repository validation is a separate serial check;
these local results do not claim site recovery or runtime qualification.
