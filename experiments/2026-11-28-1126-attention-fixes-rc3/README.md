# Follow-up review of the 1126 Attention candidate

This revision extends the [reviewed RC2 source and evidence](../2026-11-28-1126-attention-fixes-rc2/README.md). Use the [candidate recipe](cluster.json), [source lock](upstreams.lock.json) and [patch map](patch-map.json) together.

## Corrections

- The lab runner checks that a recipe permits launch before stopping a serving group to boot or take a memory snapshot. The runner inventories every rank before sending stops. A failed Docker query or a peer label that resolves to a missing or mismatched profile blocks the coordinated change. The inventory includes active workers even when the head container has exited. A fresh checkout is checked after a sync. The candidate and promoted service keep separate container names.
- A direct Python caller can edit a prebuilt request. Sync and async frontends now reject an invalid cache salt before allocating a request ID or registering parent/child output state. A caller can correct the same object and retry.
- Each profile conversion loads its codec from the pinned checkout. In RC2, a process which first converted RC1 could silently reuse the RC1 encoder binding; the RC2 loader then rejected the produced artifact. Fresh RC2 CLI output already matched its receipt.

## Offline evidence

The [test receipt](offline-results.json) binds the runs to vLLM head `f54bee45a791` and tree `50402286dbe6`. Replaying 757 patches from the locked base reproduced both identities. The build-input check passed. The CPU runtime suite used runc with GPU visibility and networking disabled.

| Group | Passed |
| --- | ---: |
| Encoder wrapper, including 37 real 1017 tokenizer cases | 87 |
| Complete native tokenizer file | 161 |
| Recorded outputs and parser | 104 |
| KV prefix hashing and request admission | 41 |
| Fixed steering CPU cases | 129 |
| Graph orchestration with a simulated device | 44 |
| Profile conversion, paths and concurrency | 7 |
| Deployment repository | 1,090 |

The encoder groups overlap. Two CUDA-only graph cases were excluded. A separate check of two rounds of parallel tool results on RC2 produced reference-identical tokens for all 1,080 combinations. The RC3 encoder is byte-identical to RC2. Additional native msgpack checks covered parallel output with shared and independent seeds and multiple scales.

Two RC3 conversions produced identical artifacts, indexes and receipts. The [new profile](profile-rebind.json) binds the same 1017 template and all 778,240 original direction bytes. Its new digest reflects the conversion tool recorded in its provenance.

## Reproducing the checks

Run the deployment checks from a checkout with the configured site map and the locked inputs.

```sh
e=experiments/2026-11-28-1126-attention-fixes-rc3
bin/spark --cluster-config "$e/cluster.json" doctor
bin/spark --cluster-config "$e/cluster.json" build prepare
bin/spark --cluster-config "$e/cluster.json" build check
```

The [RC2 native CPU test commands](../2026-11-28-1126-attention-fixes-rc2/README.md#reproduction) apply to this revision's prepared component. Use the RC2 encoder wrapper and graph suite. In the KV file, select the `attention_cache_namespace` cases along with the existing multimodal, LoRA and prefix-hash cases. To reproduce the [profile receipt](profile-rebind.json), supply the accepted 1126 profile and the official 1017 metadata.

```sh
python3 "$e/tools/rebind_profile.py" --input "$PROFILE_1126" --model-identity "$CHECKPOINT_DIR" \
  --vllm-source "$VLLM_SOURCE" --output "$NEW_RELEASE_DIR"
```
