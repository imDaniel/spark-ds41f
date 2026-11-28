# 1126 Attention maintenance candidate

This candidate extends the site release at `63b0c0d31` with three patches. The [patch map](patch-map.json) records the individual commits and the exact 753-patch replay. The candidate recipe extends the frozen TP4 release and disables launch. The image tag in the recipe names a future build.

| Patch | Change | Regression on the released source |
| --- | --- | --- |
| 0500 | Decode the OpenAI assistant tool-call envelope at its protocol boundary. Preserve the types and spelling of business values named `arguments`, including nested values in `role`, `type` and schemas. DSML renders literal image markers as text and preserves both values when escaped business keys have the same spelling. | 14 failures across the byte and real checkpoint tokenizers, covering argument types and literal image markers. Three fallback cases passed. |
| 0501 | Keep the caller's salt intact. At engine admission, validate the selected profile and give the first KV block a separate key containing its identity and canonical scale bits. Further blocks inherit the first block's hash. | All four combinations of frontend/prebuilt requests and pickle/CBOR SHA-256 exposed incorrect identity handling. |
| 0502 | Include the model-input variant in the CUDA Graph warmup family. Stock and Attention each get their first eager forward before capture, and subsequent sizes share the warmup. | Both the full and sampled capture orchestration tests missed the first Attention warmup. The stock-only control passed. |

## Offline verification

The [test receipt](offline-results.json) records the parent failures and the results against the combined checkout. Tests ran with a read-only candidate source tree on Spark01, using the accepted ARM64 image for dependencies, the runc runtime, no network and zero visible GPUs.

| Test group | Passed |
| --- | ---: |
| Encoder and tool argument round trips, including 35 real 1017 tokenizer cases | 83 |
| Complete native tokenizer test file | 159 |
| Recorded tool output, DSML provenance and token scanner | 104 |
| Prefix hashing, including caller salts, scale zero, multimodal and LoRA keys | 23 |
| Fixed steering CPU contract collection | 129 |
| Graph capture and profiling control flow on a simulated device | 37 |

The encoder wrapper exercises cases from the native tokenizer file. The counts describe individual runs. Two hardware-only graph cases were excluded. The deployment test suite passed all 1,067 tests. An independent fresh checkout reproduced the component head and tree from the locked base, passed the candidate doctor and full build-input check, and classified all 242 serving test files. GPU/TP4 execution and a candidate image build are pending.

## Reproduction

Run the checks from the deployment checkout with a configured site map and the locked build inputs.

```sh
e=experiments/2026-11-28-1126-attention-fixes
bin/spark --cluster-config "$e/cluster.json" doctor
bin/spark --cluster-config "$e/cluster.json" build prepare
bin/spark --cluster-config "$e/cluster.json" build check
python3 scripts/check_steering_cpu.py --upstreams "$e/upstreams.lock.json" \
  --cases experiments/2026-11-28-1126-attention-rc3/cpu-cases.json --report /tmp/1126-steering-cpu.xml
```

For the native CPU checks, mount the prepared vLLM tree at `/src`, the deployment checkout at `/repo` and the checkpoint metadata at `/metadata` in the dependency image named in the receipt. Use `--runtime=runc --network=none`, hide GPU devices and run from `/src`.

```sh
export PYTHONPATH=/src:/repo STEERING_VLLM_SOURCE=/src STEERING_MODEL_IDENTITY=/metadata
export CUDA_VISIBLE_DEVICES= NVIDIA_VISIBLE_DEVICES=void HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
pt() { python3 -m pytest --noconftest -o addopts= -p no:cacheprovider -q "$@"; }
pt /repo/experiments/2026-11-28-1126-attention-fixes/tools/test_encoder_offline.py
pt tests/tokenizers_/test_deepseek_v41.py
pt /repo/experiments/2026-11-28-1126-attention/tools/test_recorded_output.py \
  tests/parser/engine/test_deepseek_v41_token_provenance.py tests/parser/engine/test_token_id_scanner.py
pt tests/v1/core/test_kv_cache_utils.py -k 'attention_cache_namespace or generate_block_hash_extra_keys or request_block_hasher or hash_tokens_different_mm_input or hash_request_tokens_no_mm_inputs or test_hash_block_tokens'
python3 -c 'from vllm.tilelang_utils import _ensure_tilelang_imported; _ensure_tilelang_imported(); import pytest; raise SystemExit(pytest.main(["--noconftest", "-o", "addopts=", "-p", "no:cacheprovider", "-q", "tests/v1/worker/test_gpu_model_runner_v2_cudagraph_profiling.py", "-k", "not test_profile_cudagraph_memory_frees_throwaway_pool"]))'
```

Both encoder modules contribute to the profile's template identity. Run the following command with the clean prepared vLLM checkout, the official 1017 metadata, and the accepted 1126 profile. The [rebind receipt](profile-rebind.json) records the expected output. Two independent runs produced identical artifacts, indexes and receipts. All 778,240 direction bytes, the layer schedule and the operator match the accepted profile.

```sh
python3 "$e/tools/rebind_profile.py" --input "$PROFILE_1126" --model-identity "$CHECKPOINT_DIR" \
  --vllm-source "$VLLM_SOURCE" --output "$NEW_RELEASE_DIR"
```
