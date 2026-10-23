"""Validate the real retained artifact, deterministic rebinding and startup gates."""

import copy
import hashlib
import importlib
import importlib.util
import json
import os
import shutil
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("profile_rebinder_test", HERE / "rebind_profile.py")
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)


@pytest.fixture(scope="module")
def release(tmp_path_factory):
    folder = tmp_path_factory.mktemp("profile-1022")
    args = SimpleNamespace(
        input=Path(os.environ["STEERING_OLD_PROFILE"]),
        model_identity=Path(os.environ["STEERING_MODEL_IDENTITY"]),
        vllm_source=Path(os.environ["STEERING_VLLM_SOURCE"]),
        source_manifest=HERE.parent / "source.json",
        output=folder / "reproduced-release",
    )
    receipt = TOOL.rebind(args)
    module = importlib.import_module("steering_1022_rebind.profile")
    config = importlib.import_module("steering_1022_rebind.config")
    combined = folder / "model-with-profile"
    shutil.copytree(args.model_identity, combined)
    shutil.copytree(args.output / "steering", combined / "steering")
    return args, receipt, module, config, combined


def test_rebinding_reproduces_release_without_changing_directions(release):
    args, receipt, profile, _, _ = release
    retained = Path(os.environ["STEERING_NEW_RELEASE"])
    generated = args.output / receipt["artifact_file"]
    assert generated.read_bytes() == (retained / receipt["artifact_file"]).read_bytes()
    old = profile.load_profile(args.input, TOOL.INPUT_SHA256)
    new = profile.load_profile(generated, receipt["new_artifact_sha256"])
    assert old.directions.tobytes() == new.directions.tobytes()
    assert old.alphas == new.alphas and old.layers == new.layers
    assert old.operator_version == new.operator_version
    assert old.schedule_sha256 == new.schedule_sha256
    assert old.model_identity == new.model_identity == TOOL.MODEL_IDENTITY
    assert old.identity != new.identity
    assert hashlib.sha256(args.input.read_bytes()).hexdigest() == TOOL.INPUT_SHA256


@pytest.mark.parametrize("selector", ["file", "candidate_mount", "hf:default", "hf:heretic-1017-attn-v1"])
def test_actual_startup_loader_accepts_new_binding(release, monkeypatch, selector):
    args, receipt, _, config, combined = release
    monkeypatch.setenv("VLLM_USE_V2_MODEL_RUNNER", "1")
    monkeypatch.setenv("VLLM_DS41_KERNEL_BACKEND", "tilelang")
    selected = str(args.output / receipt["artifact_file"]) if selector == "file" else selector
    expected_sha = receipt["new_artifact_sha256"]
    if selector == "candidate_mount":
        candidate = json.loads((HERE.parent / "cluster.json").read_text())
        serve_args = candidate["serve_args"]
        startup = json.loads(serve_args[serve_args.index("--additional-config") + 1])["ds41_steering"]
        mount = next(m for m in candidate["container"]["mounts"] if m[1] == "/steering-1022")
        relative = PurePosixPath(startup["profile_path"]).relative_to(mount[1])
        # Resolve the container's path against the real release directory on disk.
        selected = str(Path(os.environ["STEERING_NEW_RELEASE"]) / relative)
        expected_sha = startup["profile_sha256"]
    runtime = SimpleNamespace(
        additional_config={"ds41_steering": {
            "schema_version": 1, "operator_version": receipt["operator_version"],
            "profile_path": selected, "profile_sha256": expected_sha,
        }},
        parallel_config=SimpleNamespace(
            tensor_parallel_size=4, pipeline_parallel_size=1, data_parallel_size=1,
            decode_context_parallel_size=1, prefill_context_parallel_size=1,
            use_ubatching=False,
        ),
        lora_config=None, kv_transfer_config=None, use_request_boundary_checkpoints=False,
        cache_config=SimpleNamespace(),
        offload_config=SimpleNamespace(
            uva=SimpleNamespace(cpu_offload_gb=0),
            prefetch=SimpleNamespace(offload_group_size=0),
        ),
        model_config=SimpleNamespace(
            model=str(combined), tokenizer_mode="deepseek_v41",
            hf_config=SimpleNamespace(model_type="deepseek_v41", hidden_size=5120, num_hidden_layers=40),
        ),
    )
    assert config.load_configured_profile(runtime).identity == receipt["new_profile_identity"]
    runtime.additional_config["ds41_steering"]["profile_sha256"] = TOOL.INPUT_SHA256
    with pytest.raises(ValueError):
        config.load_configured_profile(runtime)


def test_old_artifact_remains_rejected_on_1022(release):
    args, _, profile, _, _ = release
    with pytest.raises(ValueError, match="chat_template_sha256 identity mismatch"):
        profile.load_profile(args.input, TOOL.INPUT_SHA256, expected_template_identity=TOOL.NEW_TEMPLATE)


@pytest.mark.parametrize("fault", ["input", "model", "source", "existing_output"])
def test_rebinder_fails_closed_without_publishing(release, tmp_path, fault):
    original, _, _, _, _ = release
    args = copy.copy(original)
    args.output = tmp_path / "release"
    if fault == "input":
        args.input = tmp_path / "changed.safetensors"
        args.input.write_bytes(original.input.read_bytes() + b"changed")
    elif fault == "model":
        args.model_identity = tmp_path / "changed-model"
        shutil.copytree(original.model_identity, args.model_identity)
        with (args.model_identity / "tokenizer_config.json").open("ab") as stream:
            stream.write(b"\n")
    elif fault == "source":
        args.source_manifest = tmp_path / "wrong-source.json"
        manifest = json.loads(original.source_manifest.read_text())
        manifest["vllm"]["expected_tree"] = "0" * 40
        args.source_manifest.write_text(json.dumps(manifest))
    else:
        args.output.mkdir()
        (args.output / "keep").write_bytes(b"preserve")
    with pytest.raises(ValueError):
        TOOL.rebind(args)
    if fault == "existing_output":
        assert (args.output / "keep").read_bytes() == b"preserve"
    else:
        assert not args.output.exists()


def test_unfixed_1022_profile_is_rejected_on_the_reviewed_encoder(release):
    _, _, profile, _, _ = release
    previous = HERE.parent.parent / "2026-10-23-1022-attention-steering/profile-rebind.json"
    receipt = json.loads(previous.read_text())
    artifact = Path(os.environ["STEERING_PREVIOUS_RELEASE"]) / receipt["artifact_file"]
    with pytest.raises(ValueError, match="chat_template_sha256 identity mismatch"):
        profile.load_profile(artifact, receipt["new_artifact_sha256"],
                             expected_template_identity=TOOL.NEW_TEMPLATE)
