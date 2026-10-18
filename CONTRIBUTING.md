# Contributing

This repository owns deployment composition, configuration, experiments, and
evidence. It does not replace the vLLM, B12X, FlashInfer, or CUTLASS projects.

This fork develops, reviews and releases site-specific changes independently
of the upstream roadmap. Open review pull requests in `imDaniel/spark-ds41f` and
integrate the original author's updates here after validation. The author's
repositories are read-only integration sources; do not push our branches or
open patch pull requests there. Any future upstream contribution is a separate
collaboration decision requiring an explicit owner request and agreed scope.
The inherited contribution workflow below describes provenance and maintenance
mechanics, not authorization to submit changes upstream.

Development continues on the original project's evolving codebase. r6v is the
stable starting point for this steering modification and validation cycle,
not a permanent version constraint. Exact source pins identify each experiment;
upstream integration selects and validates a new baseline while retaining the
earlier experiment's evidence.

For a deployment change, open a branch here and follow `docs/methodology.md`. For
source work, first identify the owner in `upstreams.lock.json`, prepare that tree
with `bin/spark upstream prepare NAME`, and create one narrowly scoped branch in
the appropriate source fork. Do not combine unrelated vLLM and B12X changes into
one upstream contribution.

Remote names have fixed meanings:

- `upstream` is the canonical external project;
- `origin` is Christopher Owen's fork of that project, when recorded;
- this repository's `origin` is only the deployment repository.

After an upstream merge, advance the pinned commit, remove any now-redundant
local patch, rebuild one immutable image, and qualify it as an experiment before
promotion. Preserve the upstream pull-request link and benchmark receipts in the
promotion commit.

Operational changes are deployed only from a clean, published commit with
`bin/spark cluster sync --apply`. Do not add credentials, node-local environment
files, caches, or private benchmark inputs, and do not synchronize a working tree
with rsync or scp.
