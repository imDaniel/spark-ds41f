#!/usr/bin/env python3
"""Rebind the retained attention directions to the reviewed 1022 encoder.

Offline metadata/artifact work only. No weights, service or GPU are opened.
The input file and both template identities are pinned; checks remain strict.
"""

import argparse
import copy
import hashlib
import importlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

INPUT_SHA256 = "d7f0b997ea2748b03a353188583d494b2bfe7c40838aff98945941032da6a093"
OLD_TEMPLATE = "257e32a70aa22dcb203b6381bb21ee9f969c605c275e9a695740adcf12d97359"
NEW_TEMPLATE = "e27cffc1791e4da15a669f16223dd4b7b7745e0fed48de8d61f958dea939c8cb"
MODEL_REVISION = "1bae0e1088993b9b82de08f59de5be5a34e42170"
MODEL_IDENTITY = "b35c43fda128481f17b7ee878df2c2f93fd6db416df451b080cc8d00e166f7d0"
TOOL = "experiments/2026-10-23-1022-attention-review/tools/rebind_profile.py"


def git(source, *args):
    return subprocess.check_output(["git", "-C", str(source), *args], text=True).strip()


def rebind(args):
    if args.output.exists():
        raise ValueError("Refuse to overwrite an existing profile release")
    record = json.loads(args.source_manifest.read_text())["vllm"]
    if (
        git(args.vllm_source, "rev-parse", "HEAD") != record["patch_head"]
        or git(args.vllm_source, "rev-parse", "HEAD^{tree}") != record["expected_tree"]
        or git(args.vllm_source, "status", "--porcelain", "--untracked-files=all")
    ):
        raise ValueError("Rebinding requires the exact clean candidate source")
    package = args.vllm_source / "vllm/models/deepseek_v4_1/steering"
    name = "steering_1022_rebind"
    spec = importlib.util.spec_from_file_location(
        name, package / "__init__.py", submodule_search_locations=[str(package)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    profile = importlib.import_module(name + ".profile")
    codec = importlib.import_module(name + ".artifact")
    model_identity = profile.model_identity(args.model_identity)
    template_identity = profile.template_identity(args.model_identity)
    if model_identity != MODEL_IDENTITY or template_identity != NEW_TEMPLATE:
        raise ValueError("Model metadata or 1022 template differs from the reviewed binding")
    previous = profile.load_profile(
        args.input, INPUT_SHA256,
        expected_model_identity=MODEL_IDENTITY,
        expected_template_identity=OLD_TEMPLATE,
        expected_revision=MODEL_REVISION,
        expected_tokenizer_revision=MODEL_REVISION,
    )
    original, payload, _ = codec.read_artifact(args.input, INPUT_SHA256)
    updated = copy.deepcopy(original)
    updated["model"]["chat_template_sha256"] = template_identity
    updated["provenance"]["conversion"] = {
        "tool": TOOL, "sha256": profile.sha256_file(Path(__file__)),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".profile-1022-", dir=args.output.parent))
    try:
        materialized = stage / "materialized" / previous.profile_id
        materialized.mkdir(parents=True)
        (materialized / "manifest.json").write_bytes(profile.canonical_json(updated) + b"\n")
        (materialized / "directions.f32").write_bytes(payload)
        # The bundled-index contract fixes the basename to the logical profile ID.
        # The separate immutable release directory distinguishes 1022 from r6x.
        artifact = stage / "steering" / (previous.profile_id + ".safetensors")
        codec.pack(materialized, artifact)
        digest = profile.sha256_file(artifact)
        current = profile.load_profile(
            artifact, digest, expected_model_identity=MODEL_IDENTITY,
            expected_template_identity=NEW_TEMPLATE,
            expected_revision=MODEL_REVISION,
            expected_tokenizer_revision=MODEL_REVISION,
        )
        assert previous.directions.tobytes() == current.directions.tobytes() == payload
        assert previous.layers == current.layers and previous.alphas == current.alphas
        assert previous.operator_version == current.operator_version
        assert previous.schedule_sha256 == current.schedule_sha256
        assert previous.identity != current.identity
        index = {
            "schema_version": 1, "default_profile": current.profile_id,
            "profiles": [{"name": current.profile_id, "file": "steering/" + artifact.name,
                          "sha256": digest, "byte_count": artifact.stat().st_size}],
        }
        (stage / "steering/index.json").write_bytes(profile.canonical_json(index) + b"\n")
        selected, selected_path, selected_sha = codec.resolve_bundled(stage, "hf:default")
        assert selected == current.profile_id and selected_sha == digest
        assert Path(selected_path) == artifact
        assert profile.load_profile(materialized, current.manifest_sha256).identity == current.identity
        receipt = {
            "schema_version": 1, "profile_id": current.profile_id,
            "old_artifact_sha256": INPUT_SHA256, "new_artifact_sha256": digest,
            "artifact_file": "steering/" + artifact.name,
            "artifact_bytes": artifact.stat().st_size,
            "old_template_identity": OLD_TEMPLATE, "new_template_identity": NEW_TEMPLATE,
            "old_profile_identity": previous.identity, "new_profile_identity": current.identity,
            "directions_sha256": hashlib.sha256(payload).hexdigest(),
            "direction_payload_bytes": len(payload), "schedule_sha256": current.schedule_sha256,
            "layers": list(current.layers), "alphas": list(current.alphas),
            "model_revision": MODEL_REVISION, "model_identity": MODEL_IDENTITY,
            "operator_version": current.operator_version,
            "source_head": record["patch_head"], "source_tree": record["expected_tree"],
            "source_patchset_sha256": record["patchset_sha256"],
            "conversion": updated["provenance"]["conversion"],
            "previous_conversion": original["provenance"]["conversion"],
            "weights_modified": False, "directions_reextracted": False,
            "gpu_qualified": False,
        }
        (stage / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        if args.output.exists():
            raise ValueError("Profile destination appeared during rebinding")
        os.rename(stage, args.output)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--model-identity", type=Path, required=True,
                        help="Verified local metadata/tokenizer/inventory snapshot; no weight shards")
    parser.add_argument("--vllm-source", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(rebind(parser.parse_args()), indent=2))


if __name__ == "__main__":
    main()
