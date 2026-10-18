# Retained qualification evidence

<!-- markdownlint-disable MD013 -->

These files re-expose measurements already taken on the authorized hardware window. No model, profiler or GPU test was rerun to produce this package. The runtime remains the 184-patch `43608a373a5d` / `efcbb1ef861a` candidate. File identities are in [files.json](files.json). Keep the performance and coverage limits in [RESULTS.md](../RESULTS.md).

## CPU-only verification

Run from the deployment repository. Use new output paths: the verifiers reject an existing report rather than replace it.

```sh
python3 experiments/2026-10-19-steering-gpu/check_stock_evidence.py experiments/2026-10-19-steering-gpu/evidence --output /tmp/new-stock-check.json
python3 experiments/2026-10-19-steering-gpu/check_traces.py experiments/2026-10-19-steering-gpu/evidence/trace-manifest.json --archive experiments/2026-10-19-steering-gpu/evidence/trace-events.tar --output /tmp/new-trace-check.json
```

- [api-stock-observations.json](api-stock-observations.json): three benign prompts, fixed request settings and twelve selected API observations across official/parent/candidate-unloaded/candidate-loaded. The verifier validates 64 output IDs, completion usage, complete numeric top-5 logprobs and the length finish before comparing canonical JSON fingerprints. This is API observation equality, not proof about every hidden-state tensor. All 79 retained successful completion observations also passed the stricter checks; 33 stored fingerprints remain unchanged.
- [mhc-parent.json](mhc-parent.json), [mhc-candidate.json](mhc-candidate.json): twelve independently compiled stock mHC cases per image, with seed, input/output byte SHA, shapes and graph stability. [stock-mhc-driver.py.txt](stock-mhc-driver.py.txt) is the byte-for-byte archived GPU driver used in each independent image with separate caches. Invoke the archived driver directly with Python (the .txt suffix prevents accidental imports); pass `--arm` and a fresh `--output` directory inside each pinned image. Its timing is diagnostic. Reproduction requires those pinned images and an SM121 GPU; the CPU verifier only checks the retained records.
- [gpu-kernels.txt](gpu-kernels.txt), [gpu-kernels.xml](gpu-kernels.xml): the actual candidate GPU log and JUnit containing 29 passing cases, zero failure/error/skip. Only the JUnit hostname attribute was removed. [initial-c035-gpu-kernels.txt](initial-c035-gpu-kernels.txt) retains the preceding 25-pass/4-compile-failure run. Reading JUnit is not new GPU execution.
- [trace-events.tar](trace-events.tar), [trace-manifest.json](trace-manifest.json): all sixteen retained candidate loaded scale=0/1, prefill/decode traces for four ranks. Each archive member is a gzip JSON trace. Every non-`ph=M` event and its arguments are preserved, including CPU shapes, CUDA runtime, copy, allocation and flow events. Only identity metadata (`ph=M` events, top-level host/trace names/IDs and device UUID) is removed. The manifest records original compressed SHA, published SHA, event SHA, rank/stage/scale and removal counts. Phase/scale labels retain the original collector mapping; they are not inferred from the filename order.

The trace gate rejects missing/duplicate cohorts, missing files, wrong rank/world, changed hashes, empty CPU/GPU data, absent decode graph replay and named row/dot/project or steering custom ops in scale=0. Scale=1 is a positive control. Its result is **known steering event gate passed**; generic copy/launch/SP materialization remains **not evaluated**. A matched official/unloaded real-request trace was not collected, so this package cannot establish that broader comparison. Cross-arm long-prefill API observations were not collected either; the prior mixed long-input check compares within each arm.

[stock-evidence-check.json](stock-evidence-check.json) and [trace-event-check.json](trace-event-check.json) are the local CPU recheck receipts for this package. The scripts expose the same checks for independent repetition. They do not start services or reach a model API.

The retained mHC verifier pins each arm to its independently replayed source SHA, exact case geometry/seed and valid SHA-256 strings. GPU JUnit must match all 29 fixed classname/name identities, not only their count. Trace capture source names, source hashes, published hashes and event hashes must be unique; decode replay requires a complete positive-duration CUDA runtime event, not a same-name annotation. Decoded completion responses are saved individually before validation and CLI failures retain an error receipt. These checks are covered by 25 CPU regressions.
 Unparseable raw HTTP JSON bodies are outside the retained completion-record scope.
