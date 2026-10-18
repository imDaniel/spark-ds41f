#!/usr/bin/env python3
"""Offline, pinned conversion of published direction tensors to a serving profile.

The serving process only reads safetensors. PyTorch's restricted weights loader
is used here, once, outside the service. No checkpoint weight is modified.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import torch

SOURCE_SHA = "87e81bffab36b046786eb7a3888c8d12c69ddef145235e363e0d4fb2c8fe31b4"
SOURCE_REV = "12d58ad0878b5008e80d720251adb969a0069412"
BASE_REV = "1bae0e1088993b9b82de08f59de5be5a34e42170"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directions", required=True, type=Path)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--vllm-source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refuse to overwrite an existing profile release directory")
    if hashlib.sha256(args.directions.read_bytes()).hexdigest() != SOURCE_SHA:
        raise ValueError("Direction source does not match the pinned artifact")
    if (
        hashlib.sha256(args.spec.read_bytes()).hexdigest()
        != "238b1cdeb65b585ccb5ff22fa9cd91dddebc575726e348f73e5a97108ce77e3b"
    ):
        raise ValueError("Unsupported source ablation schedule")
    package = args.vllm_source / "vllm/models/deepseek_v4_1/steering"
    package_name = "steering_profile_converter"
    package_spec = importlib.util.spec_from_file_location(
        package_name, package / "__init__.py", submodule_search_locations=[str(package)]
    )
    loaded_package = importlib.util.module_from_spec(package_spec)
    sys.modules[package_name] = loaded_package
    package_spec.loader.exec_module(loaded_package)
    from importlib import import_module

    module = import_module(package_name + ".profile")
    codec = import_module(package_name + ".artifact")
    tensors = torch.load(args.directions, weights_only=True, map_location="cpu")
    if set(tensors) != {"bad_mean", "good_mean", "directions", "projected"}:
        raise ValueError("Unexpected source tensor schema")
    if any(t.shape != (40, 5120) or t.dtype != torch.float32 for t in tensors.values()):
        raise ValueError("Unexpected source tensor geometry or dtype")
    d = tensors["projected"][2:40].contiguous().numpy()
    if not np.isfinite(d).all() or not np.all(
        np.abs(np.linalg.norm(d.astype(np.float64), axis=1) - 1) <= module.NORM_ATOL
    ):
        raise ValueError("Published directions must already be finite unit vectors")
    metadata = {
        "schema_version": 1,
        "operator_version": module.OPERATOR_VERSION,
        "profile_name": "heretic-1017-attn-v1",
        "model": {
            "name": module.MODEL_NAME,
            "revision": BASE_REV,
            "weight_set": module.model_identity(args.model),
            "tokenizer_revision": BASE_REV,
            "chat_template_sha256": module.template_identity(args.model),
            "hidden_size": 5120,
            "layer_count": 40,
        },
        "scope": "attention",
        "layers": list(range(2, 40)),
        "alphas": [2.5] * 38,
        "directions": {
            "file": "directions.f32",
            "dtype": "float32-le",
            "byte_count": d.nbytes,
            "sha256": hashlib.sha256(d.tobytes()).hexdigest(),
        },
        "provenance": {
            "kind": "imported",
            "source": {
                "repository": "pqhaz/DeepSeek-V4.1-Flash-1017-heretic",
                "revision": SOURCE_REV,
                "license": "MIT",
                "directions": {
                    "file": "ablation_directions.pt",
                    "sha256": SOURCE_SHA,
                    "tensor": "projected[2:40]",
                },
                "schedule": {
                    "file": "ablation_spec.txt",
                    "sha256": hashlib.sha256(args.spec.read_bytes()).hexdigest(),
                    "value": "m:2.5:0:0:2:1",
                },
            },
            "conversion": {
                "tool": "experiments/2026-10-18-request-steering/tools/make_profile.py",
                "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            },
            "dataset_manifests": None,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".profile-release-", dir=args.output.parent))
    try:
        name = metadata["profile_name"]
        materialized = stage / "materialized" / name
        materialized.mkdir(parents=True)
        raw = module.canonical_json(metadata) + b"\n"
        (materialized / "manifest.json").write_bytes(raw)
        (materialized / "directions.f32").write_bytes(d.tobytes())
        artifact = stage / "steering" / (name + ".safetensors")
        codec.pack(materialized, artifact)
        digest = module.sha256_file(artifact)
        profile = module.load_profile(artifact, digest)
        if (
            module.load_profile(materialized, profile.manifest_sha256).identity
            != profile.identity
        ):
            raise ValueError("Artifact and directory identities disagree")
        index = {
            "schema_version": 1,
            "default_profile": name,
            "profiles": [
                {
                    "name": name,
                    "file": "steering/" + artifact.name,
                    "sha256": digest,
                    "byte_count": artifact.stat().st_size,
                }
            ],
        }
        (stage / "steering/index.json").write_bytes(
            module.canonical_json(index) + b"\n"
        )
        selected, _, selected_digest = codec.resolve_bundled(stage, "hf:default")
        if selected != name or selected_digest != digest:
            raise ValueError("Bundled profile selection failed")
        receipt = {
            "schema_version": 1,
            "format": codec.FORMAT,
            "profile_sha256": digest,
            "manifest_sha256": profile.manifest_sha256,
            "profile_identity": profile.identity,
            "metadata": metadata,
            "profile_bytes": artifact.stat().st_size,
            "direction_payload_bytes": d.nbytes,
            "weights_modified": False,
            "behavioral_equivalence_to_baked_fp8_heretic": "not-assumed",
        }
        (stage / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        if args.output.exists():
            raise ValueError("Profile destination appeared during conversion")
        os.rename(stage, args.output)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    print(
        json.dumps(
            {
                k: receipt[k]
                for k in (
                    "profile_sha256",
                    "profile_identity",
                    "profile_bytes",
                    "direction_payload_bytes",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
