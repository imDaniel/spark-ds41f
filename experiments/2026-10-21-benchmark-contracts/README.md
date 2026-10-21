# Benchmark receipt and scoring contracts

Base deployment: b8b6886b2a21d198b2be6f9a9fd6d8a35f8b1a55.

This is a separate tooling candidate, not a replacement for an active deployment controller. No engine, component source, mailbox series, image, model parameters, workload defaults, hardware settings, or service lifecycle changes are included.

## Changes

1. Burst CLI summaries accept scalar metrics as well as distribution summaries without changing recorded metric values.
2. Teacher-forced scoring forwards only the supported steering profile/scale request fields, including their vllm_xargs forms. Explicit zero and null remain distinct from absence. Fixed scoring arguments are not overridable. The receipt records the selected scoring request fields and requested prompt IDs; these are not server echoes or proof of applied runtime state.
3. Model comparison checks complete recorded generation/scoring IDs, duplicates, errors, prefixes, corpus coverage, and same-arm/cross-arm recorded scoring state. It reports complete, incomplete, or incomparable input alignment. Complete is not a quality or noninferiority verdict. Boolean values are distinct from numeric values; numeric zero and zero-point-zero remain equivalent.

Missing scores when scoring was declared are incomplete. Intentional generation-only runs remain supported. Legitimate empty generations are unscorable, not invented request failures. Legacy missing state provenance is not silently upgraded to verified parity.

An external corpus that was already truncated before either receipt was written cannot be reconstructed from those receipts. Qualification still requires independently frozen expected corpus identities and IDs. Generic performance report comparison and CLI exit policy are unchanged.

## Validation

Run from the repository root, using Python 3.12:

    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests:. python3 -m unittest -v test_q03_burst_summary_contract test_q03_same_state_scoring test_q03_comparison_alignment
    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_servebench.py -v
    git diff --check

The combined tooling tree passed 50 focused contract methods and 36 existing servebench methods. Tests use finite CPU fixtures and local fake HTTP responses, not production model requests. Independent review reproduced and corrected two comparison defects before this integration: declared-but-missing scoring output, and boolean/numeric request-identity conflation.

No GPU, full-model, TP4, capacity, numerical quality, or performance result is claimed. No native build or component patch replay is needed for this tooling-only diff; no native build was run. The full deployment test suite has not been rerun for this candidate. Historical receipts and negative controls remain preserved outside this public source record.
