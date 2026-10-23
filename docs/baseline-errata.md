# Baseline errata

Corrections to published baseline descriptions are appended here. The original
manifests and their source, image and measurement identities remain unchanged.

## 2026-10-23: 1022 sparse-MLA backend description

Affected record:
[2026-10-22-spark-ds41f-1022.json](../manifests/baselines/2026-10-22-spark-ds41f-1022.json),
field `runtime_choices.kernel_backend`, as retained at repository commit
`7252899c6498aae4a9149a6429e9f79d0dfdb538`.
The unchanged file's SHA-256 is
`679242cf1e1e114452484d35511612b6e5e11427c34fb4a1b7ef0df1bf3b8d0b`.

The final clause says that sparse-MLA attention stays a B12X kernel. The correct
description for this TileLang recipe is:

- Sparse-MLA attention itself uses TileLang, introduced by vLLM patch 0029 with
  r6 and selected by `--attention-backend TILELANG`.
- B12X supplies the vision tower kernels and checkpoint loader in this serving
  path. It also remains available as the separate alternative backend.

Evidence: [the backend mapping](../scripts/kernel_backend.py),
[the recipe](../config/cluster.json), and
[patch 0029](../experiments/2026-10-08-lil-rebase/vllm/0029-deepseek-v41-tilelang-kernels.patch).
The correction follows [upstream PR #468](https://github.com/christopherowen/spark-ds41f/pull/468),
commit [`27260ab78ee0055bcfba912d07ab678dd554cb94`](https://github.com/christopherowen/spark-ds41f/commit/27260ab78ee0055bcfba912d07ab678dd554cb94).
Upstream edited its manifest; this fork records the correction here to preserve
the published evidence under [the baseline policy](../AGENTS.md#sources-of-truth).

This is a description correction, not a kernel switch, image rebuild or new
qualification result. It does not promote the local 1022 steering candidate.
