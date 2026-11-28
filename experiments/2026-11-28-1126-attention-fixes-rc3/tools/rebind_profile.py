#!/usr/bin/env python3
"""Bind the accepted 1126 Attention directions to this candidate's encoder."""

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path
from uuid import uuid4


EXPERIMENT = Path(__file__).resolve().parents[1]
PARENT_RECEIPT = EXPERIMENT.parent / "2026-11-28-1126-attention/profile-rebind.json"


@contextmanager
def candidate_codec(source):
    """Load only this checkout's tracked codec source into a private package."""
    directory = source.resolve() / "vllm/models/deepseek_v4_1/steering"
    package = "_ds41_rebind_" + uuid4().hex
    with ExitStack() as cleanup:
        # profile uses .artifact and dataclasses consult sys.modules. Keep these
        # three modules registered for this conversion, including concurrent calls.
        for suffix, filename in (("", "__init__.py"), (".artifact", "artifact.py"),
                                 (".profile", "profile.py")):
            name = package + suffix
            path = directory / filename
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            cleanup.callback(sys.modules.pop, name)
            # A verified checkout may be read-only. Bypass both loading and
            # writing .pyc without changing interpreter-global bytecode settings.
            exec(compile(path.read_bytes(), str(path), "exec", dont_inherit=True), module.__dict__)
        yield sys.modules[package + ".artifact"], sys.modules[package + ".profile"]


def rebind(source, checkpoint, original, output):
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    parent = json.loads(PARENT_RECEIPT.read_text())
    pinned = json.loads((EXPERIMENT / "source.json").read_text())["vllm"]
    git = ["git", "-C", str(source)]
    revision, tree = subprocess.check_output(
        [*git, "rev-parse", "HEAD", "HEAD^{tree}"], text=True
    ).splitlines()
    if (revision, tree) != (pinned["patch_head"], pinned["expected_tree"]):
        raise ValueError("The vLLM checkout differs from the candidate source manifest")
    if subprocess.check_output([*git, "status", "--porcelain", "--untracked-files=all"]):
        raise ValueError("The candidate vLLM checkout must be clean")

    with candidate_codec(source) as (artifact, profile):
        return pack_profile(checkpoint, original, output, parent, pinned, artifact, profile)


def pack_profile(checkpoint, original, output, parent, pinned, artifact, profile):
    model_identity = profile.model_identity(checkpoint)
    template = profile.template_identity(checkpoint)
    if model_identity != parent["model_identity"] or template == parent["new_template_identity"]:
        raise ValueError("Expected the original checkpoint and a changed encoder binding")
    identity = dict(
        expected_model_identity=model_identity,
        expected_revision=parent["model_revision"],
        expected_tokenizer_revision=parent["model_revision"],
    )
    old_digest = parent["new_artifact_sha256"]
    previous = profile.load_profile(
        original, old_digest, expected_template_identity=parent["new_template_identity"], **identity
    )
    manifest, payload, _ = artifact.read_artifact(original, old_digest)
    previous_conversion = manifest["provenance"]["conversion"]
    if previous_conversion != parent["conversion"]:
        raise ValueError("Input profile conversion differs from the accepted receipt")
    manifest["model"]["chat_template_sha256"] = template
    manifest["provenance"]["conversion"] = {
        "tool": Path(__file__).resolve().relative_to(EXPERIMENT.parents[1]).as_posix(),
        "sha256": profile.sha256_file(Path(__file__)),
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".profile-rebind-", dir=output.parent) as scratch:
        inputs = Path(scratch) / "inputs"
        inputs.mkdir()
        (inputs / "manifest.json").write_bytes(profile.canonical_json(manifest) + b"\n")
        (inputs / "directions.f32").write_bytes(payload)
        release = Path(scratch) / "release"
        relative = "steering/" + previous.profile_id + ".safetensors"
        target = release / relative
        artifact.pack(inputs, target)
        digest = profile.sha256_file(target)
        index = {
            "schema_version": 1, "default_profile": previous.profile_id,
            "profiles": [{"name": previous.profile_id, "file": relative,
                          "byte_count": target.stat().st_size, "sha256": digest}],
        }
        (release / "steering/index.json").write_bytes(profile.canonical_json(index) + b"\n")
        name, selected, actual = artifact.resolve_bundled(release, "hf:default")
        if (name, Path(selected).resolve(), actual) != (previous.profile_id, target.resolve(), digest):
            raise ValueError("Candidate profile index does not resolve to the packed artifact")
        current = profile.load_profile(target, digest, expected_template_identity=template, **identity)
        packed_manifest, packed_payload, _ = artifact.read_artifact(target, digest)
        if (packed_manifest != manifest or packed_payload != payload
                or current.schedule_sha256 != parent["schedule_sha256"]
                or current.operator_version != parent["operator_version"]
                or previous.identity != parent["new_profile_identity"]
                or current.identity == previous.identity):
            raise ValueError("Candidate release failed the profile round trip")
        receipt = {
            "schema_version": 1, "profile_id": name,
            "artifact_file": relative, "artifact_bytes": target.stat().st_size,
            "old_artifact_sha256": old_digest, "new_artifact_sha256": digest,
            "old_template_identity": previous.template_identity, "new_template_identity": template,
            "old_profile_identity": previous.identity, "new_profile_identity": current.identity,
            "directions_sha256": hashlib.sha256(payload).hexdigest(),
            "direction_payload_bytes": len(payload), "schedule_sha256": current.schedule_sha256,
            "layers": list(current.layers), "alphas": list(current.alphas),
            "model_revision": current.model_revision, "model_identity": model_identity,
            "operator_version": current.operator_version,
            "source_head": pinned["patch_head"], "source_tree": pinned["expected_tree"],
            "source_patchset_sha256": pinned["patchset_sha256"],
            "conversion": manifest["provenance"]["conversion"], "previous_conversion": previous_conversion,
            "weights_modified": False, "directions_reextracted": False, "gpu_qualified": False,
        }
        (release / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        release.rename(output)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vllm-source", required=True, type=Path)
    parser.add_argument("--model-identity", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(rebind(args.vllm_source, args.model_identity, args.input, args.output), indent=2))
