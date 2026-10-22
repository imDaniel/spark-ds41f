"""Typed independent build bootstrap; no commands execute at import time.

The window core owns stop, supervised execution, proven build quiescence and
restore. These helpers only validate a fixed build plan and verify its receipt.
A successful build receipt is not P0/G1/G2 acceptance.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re

EXPERIMENT = "experiments/2026-10-21-mhc-input-validation"
PROFILE = "config/q03-window.local.json"
SOURCE_HEAD = "154159dc3e38ca8b2321c01a153a1b2ccaf8663b"
SOURCE_TREE = "ae15047b376c7792062a2607c4ef1dfbc78df18a"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def inside(path, root, ops):
    path, root = Path(ops.resolve(path)), Path(ops.resolve(root))
    require(path != root and path.is_relative_to(root), "Build path escapes independent root")
    return str(path)


def run(ops, argv, cwd=None, timeout=120):
    result = ops.run(argv, cwd=cwd, timeout=timeout)
    require(result.returncode == 0, "Build read-only check failed")
    return result.stdout.strip()


def environment_prefix(environment):
    """Clear inherited Docker selectors before applying the reviewed builder."""
    names = {key for key in os.environ
             if key.startswith(("DOCKER_", "BUILDX_", "BUILDKIT_"))}
    names.update(("DOCKER_CONTEXT", "DOCKER_HOST", "DOCKER_CONFIG", "BUILDX_CONFIG",
                  "BUILDX_BUILDER", "BUILDKIT_HOST"))
    argv = ["/usr/bin/env"]
    for name in sorted(names):
        argv += ["-u", name]
    argv += ["GIT_OPTIONAL_LOCKS=0", "PYTHONDONTWRITEBYTECODE=1"]
    argv += [key + "=" + environment[key] for key in sorted(environment)]
    return argv


def _context(manifest, ops):
    build = manifest["build"]
    root = manifest["independent_root"]
    checkout = inside(manifest["preflight"]["independent_checkout"], root, ops)
    directory = inside(build["build_directory"], checkout + "/.work/build", ops)
    output = inside(build["output_path"], root, ops)
    for protected in (".git", "bin", "scripts", "config", "experiments", "docker"):
        target = Path(checkout) / protected
        require(not Path(output).is_relative_to(target) and not target.is_relative_to(output),
                "Build output overlaps immutable deployment inputs")
    for protected in (directory + "/src", directory + "/context", directory + "/inputs.json"):
        require(not Path(output).is_relative_to(protected)
                and not Path(protected).is_relative_to(output),
                "Build output overlaps prepared source inputs")
    profile = inside(checkout + "/" + PROFILE, checkout, ops)
    require(profile == checkout + "/" + PROFILE, "Build profile symlink is not allowed")
    require(ops.sha256(profile) == build["profile_sha256"], "Build profile drift")
    config = ops.read_json(profile)
    require(config["upstreams_config"] == EXPERIMENT + "/upstreams.lock.json",
            "Build must use the reviewed candidate lock")
    selection = ops.read_json(checkout + "/" + EXPERIMENT + "/validation-selection.json")
    require((selection["source_head"], selection["source_tree"]) == (SOURCE_HEAD, SOURCE_TREE),
            "Build requires the full 197-patch candidate")
    source = ops.read_json(checkout + "/" + EXPERIMENT + "/source.json")
    require((source["vllm"]["patch_head"], source["vllm"]["expected_tree"]) ==
            (SOURCE_HEAD, SOURCE_TREE), "Source manifest differs from selected candidate")
    record_path = directory + "/inputs.json"
    require(ops.sha256(record_path) == build["inputs_sha256"], "Prepared build inputs drift")
    inputs = ops.read_json(record_path)["inputs"]
    require((inputs["vllm"]["patch_head"], inputs["vllm"]["expected_tree"]) ==
            (SOURCE_HEAD, SOURCE_TREE), "Prepared tree differs from selected candidate")
    digest = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    expected_directory = (checkout + "/.work/build/vllm-" + inputs["vllm"]["revision"][:12]
                          + "-b12x-" + inputs["b12x"]["revision"][:12] + "-" + digest[:8])
    require(directory == expected_directory, "Unexpected official build directory")
    for name, record in inputs.items():
        if isinstance(record, dict) and "expected_tree" in record:
            require(config["container"]["expected_labels"].get("local.spark3." + name + ".tree")
                    == record["expected_tree"], "Profile component label mismatch")
    tag = build["tag"]
    require(re.fullmatch(r"q03-validation:[a-z0-9][a-z0-9_.-]{7,80}", tag), "Unique Q03 tag required")
    require(config["container"]["image"] == tag, "Profile/tag mismatch")
    require(config["container"]["name"] == "q03-validation-isolated", "Independent container name required")
    return build, checkout, directory, output, inputs


def _builder(build, ops, root):
    from q03_buildkit_plan import make_plan, verify_live

    receipt_path = inside(build["builder_receipt_path"], root, ops)
    require(ops.sha256(receipt_path) == build["builder_receipt_sha256"], "Builder receipt drift")
    receipt = ops.read_json(receipt_path)
    plan = receipt["plan"]
    require(plan == make_plan(plan["identity"], plan["root"], plan["image"], independent_root=root),
            "Unreviewed builder plan")
    require(receipt_path == plan["root"] + "/provision-receipt.json", "Wrong builder receipt path")
    prefix = environment_prefix(plan["build_environment"])
    observed = verify_live(plan, lambda argv: run(ops, prefix + argv))
    require(observed == receipt["result"], "Provisioned builder identity changed")
    require(receipt.get("preabsence") == {"container": True, "volume": True, "metadata": True},
            "Builder lacks pinned fresh-provision evidence")
    return plan, prefix


def validate_build(manifest, ops):
    """Read-only validation BEFORE hold acquisition; no builder creation here."""
    require([job.get("kind") for job in manifest["jobs"]] == ["build"],
            "Build-only window cannot run qualification gates")
    build, checkout, directory, output, inputs = _context(manifest, ops)
    plan, prefix = _builder(build, ops, manifest["independent_root"])
    # Successful listing, rather than failed inspect, establishes tag absence.
    tags = run(ops, prefix + ["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"])
    require(build["tag"] not in tags.splitlines(), "Candidate image tag already exists")
    command = [checkout + "/bin/spark", "--shape", "tp4-ring", "--cluster-config", PROFILE]
    run(ops, prefix + command + ["build", "check"], cwd=checkout)
    # Official command retains 64 GiB admission, 24 GiB reserve, its build lock,
    # native labels and GPU import smoke. Never substitute raw docker build.
    return {"argv": prefix + command + ["build", "image", "--tag", build["tag"], "--apply"],
            "cwd": checkout, "builder": plan, "output_path": output,
            "uses_supervised_adapter": True, "requires_supervised_quiescence": False,
            "automatic_recovery_scope": "proven prelaunch abort or durable normal build+smoke completion",
            "abnormal_build_outcome": "UNKNOWN; retain hold for manual daemon reconciliation"}


def verify_image(image, receipt, inputs, tag, deployment_sha):
    """Pure post-build identity checks; useful for CPU fault injection."""
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", image["Id"]), "Unpinned image digest")
    labels = image["Config"].get("Labels") or {}
    require(labels.get("org.opencontainers.image.revision") == deployment_sha,
            "Image deployment revision mismatch")
    require(labels.get("org.opencontainers.image.base.digest") == inputs["base_image"].split("@", 1)[1],
            "Image base digest mismatch")
    for name, record in inputs.items():
        if isinstance(record, dict) and "patch_head" in record:
            require(labels.get("local.spark3." + name + ".patch-head") == record["patch_head"]
                    and labels.get("local.spark3." + name + ".tree") == record["expected_tree"],
                    "Image component identity mismatch: " + name)
    require(receipt["tag"] == tag and receipt["image_id"] == image["Id"]
            and receipt["deployment_commit"] == deployment_sha, "Official build receipt mismatch")
    require(type(receipt["compile_jobs"]) is int and receipt["compile_jobs"] > 0,
            "Invalid compile receipt")
    require(type(receipt["min_available_gib"]) in (int, float)
            and math.isfinite(receipt["min_available_gib"])
            and receipt["min_available_gib"] >= 24, "Build reserve was not preserved")
    require(bool(receipt["built_utc"]), "Missing official smoke-complete receipt")
    return image["Id"]


def validate_candidate_build(manifest, ops):
    """Verify immutable image evidence independently of native runtime capture.

    A newer controller may inspect an older published build, but may never
    relabel that image or substitute its own revision for the build revision.
    """
    from q03_window_preflight import verify_build_publication

    candidate = manifest["candidate"]
    require(candidate.get("component_head") == SOURCE_HEAD, "Unexpected component head")
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", candidate.get("image", "")),
            "Immutable candidate image required")
    for path_key, hash_key in (("build_receipt_path", "build_receipt_sha256"),
                               ("build_inputs_path", "build_inputs_sha256")):
        expected = candidate.get(hash_key, "")
        require(re.fullmatch(r"[0-9a-f]{64}", expected)
                and ops.sha256(candidate[path_key]) == expected,
                "Candidate evidence hash mismatch: " + path_key)
    receipt = ops.read_json(candidate["build_receipt_path"])
    inputs = ops.read_json(candidate["build_inputs_path"])["inputs"]
    require(receipt.get("result") == "built-official-smoke-passed"
            and receipt.get("image_id") == candidate["image"]
            and receipt.get("source_head") == SOURCE_HEAD
            and receipt.get("source_tree") == SOURCE_TREE
            and inputs["vllm"].get("patch_head") == SOURCE_HEAD
            and inputs["vllm"].get("expected_tree") == SOURCE_TREE,
            "Candidate build/source identity mismatch")
    publication = candidate.get("build_publication", manifest["preflight"]["publication"])
    verify_build_publication(publication, ops, manifest)
    images = json.loads(run(ops, ["docker", "image", "inspect", candidate["image"]]))
    require(isinstance(images, list) and len(images) == 1, "Ambiguous candidate image")
    image = images[0]
    require(image.get("Config", {}).get("Labels") == receipt.get("image_labels"),
            "Actual image labels differ from build receipt")
    official = receipt["official_receipt"]
    digest = verify_image(image, official, inputs, official["tag"], publication["sha"])
    require(digest == candidate["image"], "Actual candidate image differs")
    return receipt


def native_capture_command(image_id, window_id):
    """Driver-library-enabled native capture; no GPU devices are requested.

    NVIDIA's `none` mode exposes selected driver capabilities without assigning
    GPUs. This is not proof of no GPU interaction; capture remains STOP-gated.
    """
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", image_id), "Immutable capture image required")
    require(re.fullmatch(r"[0-9a-f]{32}", window_id), "Bound capture window required")
    name = "q03-native-capture-" + window_id
    program = "HEAD=" + repr(SOURCE_HEAD) + "\n" + _CAPTURE
    return name, ["docker", "run", "--pull", "never", "--rm", "--name", name,
                  "--label", "q03.window=" + window_id,
                  "--label", "q03.role=native-capture", "--network=none",
                  "--read-only", "--runtime=nvidia", "--env", "NVIDIA_VISIBLE_DEVICES=none",
                  "--env", "NVIDIA_DRIVER_CAPABILITIES=compute",
                  "--tmpfs", "/tmp:rw,nosuid,nodev", "--entrypoint", "python3",
                  image_id, "-B", "-c", program]


def validate_native_capture_output(text):
    """Parse one marked native record, retaining other log text as evidence.

    Validation is structural and source-bound, not human review or P0 success.
    """
    require(isinstance(text, str) and len(text.encode("utf-8")) <= 1024 * 1024,
            "Native capture log exceeds bound")
    prefix = "Q03_NATIVE_MANIFEST="
    records = [line[len(prefix):] for line in text.splitlines() if line.startswith(prefix)]
    require(len(records) == 1, "Native capture requires exactly one marked record")
    def unique_fields(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, "Duplicate native manifest field")
            value[key] = item
        return value

    result = json.loads(records[0], object_pairs_hook=unique_fields)
    require(isinstance(result, dict)
            and set(result) == {"source_head", "installation", "versions", "native_paths", "native_sha256"},
            "Unexpected native manifest schema")
    require(result["source_head"] == SOURCE_HEAD, "Native source identity differs")
    installation = result["installation"]
    require(isinstance(installation, dict) and set(installation) == {"mode", "package_root"}
            and installation["mode"] == "image-source"
            and isinstance(installation["package_root"], str)
            and Path(installation["package_root"]).is_absolute(), "Invalid image installation")
    versions = result["versions"]
    require(isinstance(versions, dict) and set(versions) == {"python", "torch", "tilelang", "tile_kernels"}
            and all(isinstance(v, str) and v.strip() for v in versions.values()),
            "Incomplete native versions")
    names = {"vllm._C_stable_libtorch", "vllm._moe_C_stable_libtorch"}
    paths, hashes = result["native_paths"], result["native_sha256"]
    require(isinstance(paths, dict) and isinstance(hashes, dict)
            and set(paths) == names and set(hashes) == names, "Incomplete native module identities")
    for name in names:
        require(isinstance(paths[name], str) and Path(paths[name]).is_absolute()
                and isinstance(hashes[name], str) and re.fullmatch(r"[0-9a-f]{64}", hashes[name]),
                "Invalid native path/hash")
    return result


# Captured FROM the image, never from a baseline or host source overlay. HEAD is
# a receipt field, not independent proof: validate_candidate_build binds image
# labels and the hashed official evidence first. Native imports may still fail;
# preserve that failure, never synthesize native identities or alter the runtime.
_CAPTURE = r'''
import hashlib,importlib,importlib.metadata,json,pathlib,platform
import torch,vllm
names=('vllm._C_stable_libtorch','vllm._moe_C_stable_libtorch')
paths={name:pathlib.Path(importlib.import_module(name).__file__).resolve() for name in names}
print('Q03_NATIVE_MANIFEST='+json.dumps({'source_head':HEAD,'installation':{'mode':'image-source','package_root':str(pathlib.Path(vllm.__file__).resolve().parent)},'versions':{'python':platform.python_version(),'torch':torch.__version__,'tilelang':importlib.metadata.version('tilelang'),'tile_kernels':importlib.metadata.version('tile_kernels')},'native_paths':{k:str(v) for k,v in paths.items()},'native_sha256':{k:hashlib.sha256(v.read_bytes()).hexdigest() for k,v in paths.items()}},sort_keys=True))
'''


def verify_build(manifest, ops):
    build, checkout, directory, output, inputs = _context(manifest, ops)
    plan, prefix = _builder(build, ops, manifest["independent_root"])
    images = json.loads(run(ops, prefix + ["docker", "image", "inspect", build["tag"]]))
    require(len(images) == 1, "Ambiguous built image")
    image = images[0]
    receipt_path = directory + "/images/" + image["Id"].removeprefix("sha256:")[:16] + ".json"
    receipt = ops.read_json(receipt_path)
    digest = verify_image(image, receipt, inputs, build["tag"], manifest["preflight"]["publication"]["sha"])
    window = manifest["preflight"]["window_id"]
    require(re.fullmatch(r"[0-9a-f]{32}", window), "Unbound capture window")
    _, capture = native_capture_command(digest, window)
    return {"schema_version": 1, "result": "built-official-smoke-passed",
            "image_id": digest, "source_head": SOURCE_HEAD, "source_tree": SOURCE_TREE,
            "image_labels": image["Config"].get("Labels") or {},
            "official_receipt": receipt, "official_receipt_sha256": ops.sha256(receipt_path),
            "builder_container_id": ops.read_json(build["builder_receipt_path"])["result"]["container_id"],
            "runtime_manifest_capture_plan": {"argv": capture, "cwd": checkout,
                "output": output + "/runtime-manifest.unreviewed.json", "status": "not executed; review required", "timeout_seconds": 120,
                "preconditions": ["execute only through a separately authorized capture-only window after coordinated stop",
                                  "local CPU and memory availability checked",
                                  "unique capture container name absent"]},
            "qualification": "P0/G1/G2 not run; restoration and review required before a new test window"}
