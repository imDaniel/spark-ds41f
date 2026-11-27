# TP4 steering on upstream 1126

This candidate integrates the upstream release at `890f8654bb484cb4b5006f7e9c26d50cb60c3610` with the site's promoted 1022 deployment at `3e5eb7693f4b83c6232448270615e61e8e5520b2`. Both recipes target the same image, official 1017 checkpoint, four-node ring, 1,048,576-token context, image input and concurrency 16. The promoted deployment remains in `config/cluster.json`. Candidate launch is disabled while GPU qualification is pending.

## Selecting a mode

Choose one cluster configuration at service startup. Requests then select stock or steered behavior inside that mode. The process rejects a configuration that enables both implementations. Changes between the two recipes require a coordinated restart of all four ranks.

| Startup configuration | Request to the inference server | Result |
| --- | --- | --- |
| `cluster.json` | `{"model":"deepseek-v4.1-flash"}` | Stock |
| `cluster.json` | `{"model":"deepseek-v4.1-flash","steering_profile":"heretic-1017-attn-v1","steering_scale":1.5}` | Site attention writer, scaled for this request |
| `glp.json` | `{"model":"deepseek-v4.1-flash"}` | Stock |
| `glp.json` | `{"model":"deepseek-v4.1-flash-weightless"}` | Upstream GLP at the configured alpha of 2.0 |

The table shows the steering fields of a chat request. Supply the usual `messages` field. With the OpenAI Python client, put `steering_profile` and `steering_scale` in `extra_body`. Attention accepts finite scales from 0 through 4. A named profile defaults to 1 and zero selects stock. A nonzero scale requires the profile name. In GLP mode the operator chooses alpha at startup. The steered alias defaults to temperature 0.3 and top_p 0.95. Set both sampling parameters explicitly when comparing modes.

Attention projects the attention output at layers 2–39 using the retained directions and schedule. The reference GLP spec-3 vector covers layers 11–37 and the runtime applies it to the attention write and folded residual streams before the next Engram. Equal numeric strengths across these recipes have different meanings.

## Preparation and maintenance window

1. Qualify the native image on GB10. Record one OCI digest, verify labels and distribute the same digest to all four nodes. Use the selected recipe's node map and read-only mounts. The attention file SHA-256 is recorded in `profile-rebind.json`. GLP requires the approved GGUF revision and SHA-256 in `glp.json` on every rank. A missing file or failed rank probe blocks explicit GLP selection.
2. Publish the reviewed candidate commit to the downstream deployment repository and qualify the selected mode in an owner-authorized window. Before a switch, check the cluster hold and live shape, drain ingress and establish the watchdog's recovery target. Use the coordinated cluster stop with the current configuration and start with the selected configuration. Review the plan's live cluster and topology before applying. These experiment configurations are launch-disabled. An authorized window needs a reviewed launch-enabled configuration.
3. Before reopening ingress, check all four ranks, the image digest and the backend's `/v1/models`. Smoke-test the stock path and the steered path using the table. On the gateway, also verify the chosen model name routes to this backend. A public GLP alias requires a gateway route. To return to the other mechanism, use the same coordinated procedure. Until promotion the service to restore on failure is the site's 1022 baseline.

## Offline reproduction

The candidate starts from vLLM `19f2c20ed4d64ae22bd009ceacadd85e2a81e354`. The first 742 patches reproduce upstream head `d8784b841c360f523ec20e44bf8fb0898f36d3e4`. The six downstream patches extend it to 748. See `source.json` for the component identities and `patch-map.json` for all 17 historical downstream patches. The six patches cover request identity, per-request state, attention writer integration, JSON encoding, byte alignment and test contracts.

From the repository root, with a configured site node map and the CPU test dependencies installed:

```sh
e=experiments/2026-11-28-1126-attention
bin/spark --cluster-config "$e/cluster.json" doctor
bin/spark --cluster-config "$e/glp.json" doctor
bin/spark --cluster-config "$e/cluster.json" build prepare
bin/spark --cluster-config "$e/cluster.json" build check
python scripts/check_steering_cpu.py --upstreams "$e/upstreams.lock.json" --cases "$e/cpu-cases.json"
python -m unittest discover -s tests
python scripts/check-recovery-fixtures.py
```

Set `STEERING_VLLM_SOURCE` to the prepared vLLM directory and `STEERING_MODEL_IDENTITY` to the verified official 1017 metadata/tokenizer directory. Supply an existing 1022 profile file in `PROFILE_1022` and a new output directory in `PROFILE_RELEASE`.

```sh
export PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export PYTHONPATH="$STEERING_VLLM_SOURCE"
python "$e/tools/rebind_profile.py" --input "$PROFILE_1022" --model-identity "$STEERING_MODEL_IDENTITY" \
  --vllm-source "$STEERING_VLLM_SOURCE" --source-manifest "$e/source.json" --output "$PROFILE_RELEASE"
python -m pytest --noconftest -p no:cacheprovider -q "$e/tools/test_encoder_offline.py"
python -m pytest --noconftest -p no:cacheprovider -q "$e/tools/test_recorded_output.py"
VLLM_DSV41_STRUCTURAL_DSML=0 python -m pytest --noconftest -p no:cacheprovider -q "$e/tools/test_recorded_output.py"
CUDA_VISIBLE_DEVICES= PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python "$e/tools/check_glp_host.py"
```

The profile release contains a safetensors file and its index. Rebinding checks the five checkpoint metadata files, old artifact, unchanged direction bytes and schedule, new template, and exact candidate source. See `results.json` for local qualification and remaining hardware checks.
