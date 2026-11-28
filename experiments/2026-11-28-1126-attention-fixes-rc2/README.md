# 1126 Attention candidate review

This revision builds on the [first maintenance candidate](../2026-11-28-1126-attention-fixes/README.md). The earlier patch series and its verification records remain available at their original paths. The [patch map](patch-map.json) links the review fixes to their component commits.

## Review findings

| Area | Observed behavior and correction |
| --- | --- |
| Deep JSON in tool history (0503) | The initial candidate exceeded Python's recursion limit on JSON arguments which the release rendered. Iterative traversal preserves the release's supported nesting, repeated references and literal text. |
| Admission and prefix hashing (0504) | Some Python paths validated a cache salt after the request had already crossed the client boundary. In-process prebuilt requests could also share mutable sampling parameters with their caller. Validate the final salt before dispatch and snapshot the Attention selection on admission so that the worker and hasher read the same scale. |
| Tool result ordering (0505) | Three out-of-order results on the real chat renderer were associated in the wrong order when IDs contained literal control spellings, or two distinct IDs contained the two image spellings. Both the release and the first candidate exhibited this behavior. Preserve request-local identifier equality through text protection. |
| Graph warmup | The original variant-specific warmup passed the extended review, including mixed FULL/PIECEWISE capture, sampled sizes, and releasing the capture buffers when active warmup fails. |
| Candidate preparation | Recognize both exact container names in the build and cache guards, and retain separate stop targets. Resolve both sides of the profile index path check so an output directory can be reached through a symlink. |

## Offline results

The [verification record](offline-results.json) binds the test results to vLLM head `f32ecc918435` and tree `2f003a3cd5fc`. All six runtime groups ran on the same read-only source using the accepted ARM64 image for dependencies, with networking disabled and no visible GPU.

| Group | Passed |
| --- | ---: |
| Encoder wrapper, including 37 real 1017 tokenizer cases | 87 |
| Full native tokenizer file | 161 |
| Recorded outputs and parser | 104 |
| KV hashing and request admission | 37 |
| Exact pinned steering CPU collection | 129 |
| Graph control flow on a simulated device | 44 |
| Profile publication paths, retry and concurrency | 6 |
| Deployment repository | 1,072 |

The encoder groups overlap. On the actual chat renderer, both tool-ID cases now produce the call order `0,1,2`. The original candidate emitted `0,2,1` and `1,0,2` respectively. Two hardware-only graph tests were explicitly deselected. An independent replay of all 756 patches reproduced the pinned source and passed the build-input check. Two profile conversions produced identical artifacts, receipts and indexes. The new artifact SHA-256 is `767555923afb91a7af3b0579b5d63a52e5e291ebca6884e5ed23b1914d286a74`. All 778,240 direction bytes and the layer schedule match the promoted profile.

## Reproduction

Set the experiment directory in a checkout with the site map and locked build inputs.

```sh
e=experiments/2026-11-28-1126-attention-fixes-rc2
bin/spark --cluster-config "$e/cluster.json" doctor
bin/spark --cluster-config "$e/cluster.json" build prepare
bin/spark --cluster-config "$e/cluster.json" build check
```

For the native CPU groups, follow the [initial candidate's verification commands](../2026-11-28-1126-attention-fixes/README.md#reproduction) with the prepared source from this revision. Select this revision's encoder wrapper and include `$e/tools/test_graph_warmup_offline.py` alongside the native graph profiling tests. Use this revision's rebind tool with the accepted 1126 profile and official 1017 checkpoint metadata. The launch-disabled recipe selects the artifact pinned in the [rebind receipt](profile-rebind.json).

```sh
export STEERING_VLLM_SOURCE="$VLLM_SOURCE" STEERING_MODEL_IDENTITY="$CHECKPOINT_DIR"
export STEERING_ACCEPTED_PROFILE="$PROFILE_1126"
python3 "$e/tools/rebind_profile.py" --input "$PROFILE_1126" --model-identity "$CHECKPOINT_DIR" \
  --vllm-source "$VLLM_SOURCE" --output "$NEW_RELEASE_DIR"
python3 -m pytest --noconftest -p no:cacheprovider -o addopts= "$e/tools/test_encoder_offline.py" "$e/tools/test_rebind_paths.py"
```
