# Host harness development receipts

These runs precede the frozen candidate and do not qualify its final source.
The first run reported 19 tests with two failures and one error. Its metadata
adapter lacked untyped_storage, its arena-retention expectation referred to the
old placement, and its fake-signature assertion incorrectly compared annotations
rather than parameter names/defaults. The initial tee pipeline did not propagate
the failed test exit status; the raw FAILED report is authoritative. Later runs
used pipefail and corrected the fixtures. Subsequent coverage grew to 23, 25,
26 and 30 tests; all raw reports remain here. Frozen-source results are separate
in ../host-final, with source hashes and commit identity.

These are AST-adapted CPU host/static checks, not execution of the Torch
dispatcher, numerical kernels, CUDA graphs, or model inference.
