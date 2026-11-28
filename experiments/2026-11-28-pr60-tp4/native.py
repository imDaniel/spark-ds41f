"""Run the native gate on an idle, leased Spark, using only the built image's vLLM."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from xml.etree import ElementTree

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CONFIG = str((HERE / "cluster.json").relative_to(ROOT))
HIST = ROOT / "experiments/2026-11-28-1126-tp4-qualification/runs/native-rc3"
HIST_MANIFEST = ROOT / "experiments/2026-11-28-1126-attention-rc3/component-tests.json"
RC3 = ROOT / "experiments/2026-11-28-1126-attention-fixes-rc3"
RC2 = ROOT / "experiments/2026-11-28-1126-attention-fixes-rc2"
IMAGE_SOURCE = Path("/opt/spark-ds41f/candidate/vllm")
CONTAINER = "ds41f-pr60-component-check"
LABEL = "local.spark-ds41f.qualification.window"
GRAPH = "tests/v1/worker/test_gpu_model_runner_v2_cudagraph_profiling.py"
SALT = "tests/v1/core/test_kv_cache_utils.py"
TARGETS = {
    GRAPH,
    SALT,
    "tests/tokenizers_/test_deepseek_v41.py",
    "tests/models/test_deepseek_v4_1_steering_profile.py",
    "tests/models/test_deepseek_v4_1_steering_request.py",
}
SUPPLEMENTAL_MINIMUM = {"variant": 2, "warmup": 7, "encoder": 87, "parser": 104}


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def output(*args):
    return subprocess.check_output(args, text=True).strip()


def make_manifest(source):
    previous = read(HIST_MANIFEST)
    canonical = read(ROOT / "config/release-tests.json")
    excluded = set(previous["scope_exclusions"])
    historical = set(previous["files"])
    files = sorted(historical | (set(canonical["gate"]) - excluded) | TARGETS)
    if set(files) & excluded:
        raise ValueError("GLP files included in Attention gate")
    # Keep the immutable site's deselections and newly declared canonical ones.
    deselect = {**canonical["deselect"], **previous["deselect"]}
    return {"source_head": source["patch_head"], "source_tree": source["expected_tree"],
            "files": files, "deselect": {k: v for k, v in deselect.items() if k.split("::")[0] in files},
            "scope_exclusions": previous["scope_exclusions"]}


def test_outcomes(xml_path, *, supplemental=False):
    result = {}
    for case in ElementTree.parse(xml_path).iter("testcase"):
        key = (case.get("file") or "", case.get("classname") or "", case.get("name"))
        if not key[2] or (not supplemental and not all(key)) or key in result:
            raise ValueError(f"Missing or duplicate JUnit case identity {key} in {xml_path}")
        result[key] = next((status for tag, status in (("failure", "failed"), ("error", "error"),
                           ("skipped", "skipped")) if case.find(tag) is not None), "passed")
    if supplemental:
        # Pytest xunit2 can omit both module fields for an external test. Its
        # complete parametrized name must then be unique within this report.
        counts = Counter(name for _, _, name in result)
        for path, classname, name in result:
            if not path and not classname and counts[name] != 1:
                raise ValueError(f"Ambiguous supplemental JUnit case {name} in {xml_path}")
    return result


def component_cases(folder, files):
    files = sorted(files)
    cases = {}
    reports = set(folder.glob("chunk*.xml"))
    for start in range(0, len(files), 10):
        chunk = files[start:start + 10]
        prefix = f"chunk{start // 10:03d}"
        whole = folder / f"{prefix}.xml"
        parts = sorted(folder.glob(f"{prefix}-*.xml"))
        if parts:
            # A timed-out chunk can leave its own partial XML. The runner then
            # emits one authoritative report per file, in manifest order.
            expected = [folder / f"{prefix}-{index:02d}.xml" for index in range(len(chunk))]
            if parts != expected:
                raise ValueError(f"Incomplete per-file fallback for {prefix}")
            groups = [(part, {path}) for part, path in zip(parts, chunk)]
        else:
            groups = [(whole, set(chunk))]
        reports.discard(whole)
        for part, expected_files in groups:
            found = test_outcomes(part)
            if {key[0] for key in found} != expected_files:
                raise ValueError(f"Wrong or missing JUnit files in {part}")
            cases.update(found)
            reports.discard(part)
    if reports:
        raise ValueError(f"Unexpected JUnit reports: {sorted(reports)}")
    return cases


def check_file_counts(rows, cases):
    observed = defaultdict(Counter)
    for (path, _, _), outcome in cases.items():
        observed[path][outcome] += 1
    for row in rows:
        path = row["file"]
        for outcome in ("passed", "skipped", "failed", "error"):
            if type(row[outcome]) is not int or row[outcome] != observed[path][outcome]:
                raise ValueError(f"JSON/JUnit count mismatch for {path} {outcome}: "
                                 f"{row[outcome]} vs {observed[path][outcome]}")


def check_regressed_skips(previous, current):
    skipped = sorted(key for key, value in current.items()
                     if value == "skipped" and previous.get(key) == "passed")
    if skipped:
        raise ValueError(f"Previously passing release cases skipped: {skipped}")


def require_cases(cases, names):
    missing = [name for name in names if cases.get(name) != "passed"]
    if missing:
        raise ValueError(f"Missing mandatory passing cases: {missing}")


def audit_bulk(folder, manifest):
    rows = [json.loads(line) for line in (folder / "results.jsonl").read_text().splitlines()]
    by_path = {r["file"]: r for r in rows}
    if sorted(by_path) != sorted(manifest["files"]) or len(rows) != len(by_path):
        raise ValueError("Incomplete native per-file results")
    failed = [r["file"] for r in rows if r["status"] != "ok" or not (r["passed"] + r["skipped"])]
    if failed:
        raise ValueError(f"Missing or failing native file results: {failed}")
    cases = component_cases(folder, manifest["files"])
    check_file_counts(rows, cases)
    historical = read(HIST / "component-run-5-audit.json")
    for path, previous in historical["critical"].items():
        current = by_path[path]
        if current["skipped"] or current["passed"] < previous["passed"]:
            raise ValueError(f"Critical cases skipped or lost: {path} ({current})")
    errors = [key for key, outcome in cases.items() if outcome in {"failed", "error"}]
    if errors:
        raise ValueError(f"JUnit failures or collection errors: {errors[:10]}")
    required = [(GRAPH, f"test_profile_cudagraph_memory_frees_throwaway_pool[{n}]") for n in (0, 268435456)]
    required += [(GRAPH, f"test_full_capture_warms_each_input_variant_before_capture[{v}]") for v in ("False", "True")]
    required.append(("tests/models/test_deepseek_v4_1_steering_gpu.py", "test_rows_graph_replay_reuses_controls_and_output"))
    require_cases(cases, [(path, path.removesuffix(".py").replace("/", "."), name) for path, name in required])
    for path in TARGETS:
        if not any(key[0] == path and result == "passed" for key, result in cases.items()):
            raise ValueError(f"No maintenance tests passed in {path}")
    salt = [value for (path, _, name), value in cases.items()
            if path == SALT and name.startswith("test_attention_cache_namespace")]
    if not salt or any(result != "passed" for result in salt):
        raise ValueError("Attention cache identity cases missing or skipped")
    previous = component_cases(HIST / "component-run-5", read(HIST_MANIFEST)["files"])
    check_regressed_skips(previous, cases)
    return {"files": len(rows), "totals": {outcome: sum(r[outcome] for r in rows)
            for outcome in ("passed", "skipped", "failed", "error")}, "required_graph_cases": [list(k) for k in required],
            "passing_cache_namespace_cases": len(salt), "critical_files": {p: by_path[p] for p in historical["critical"]}}


def supplemental_result(report):
    cases = test_outcomes(report, supplemental=True)
    name = report.stem
    minimum = SUPPLEMENTAL_MINIMUM[name]
    if len(cases) < minimum or any(v != "passed" for v in cases.values()):
        raise ValueError(f"{name} incomplete: {len(cases)} cases, expected at least {minimum} all passing")
    result = {name: len(cases)}
    if name == "encoder":
        result["real_tokenizer"] = sum("checkpoint_tokenizer" in node for _, _, node in cases)
        if result["real_tokenizer"] < 37:
            raise ValueError("Not all official 1017 tokenizer cases ran")
    return result


def audit_saved(directory):
    manifest = read(directory / "manifest.json")
    if manifest != make_manifest(read(RC3 / "source.json")["vllm"]):
        raise ValueError("Recorded manifest differs from the RC3 native gate")
    extras = {}
    for name in SUPPLEMENTAL_MINIMUM:
        extras.update(supplemental_result(directory / f"{name}.xml"))
    if extras != read(directory / "supplemental.json"):
        raise ValueError("Supplemental JSON/JUnit count mismatch")
    bulk = audit_bulk(directory / "component", manifest)
    identity = read(directory / "identity.json")
    if (identity["source_head"], identity["source_tree"]) != (manifest["source_head"], manifest["source_tree"]):
        raise ValueError("Recorded source identity differs from the manifest")
    result = {**identity, **bulk, "supplemental": extras, "status": "passed"}
    if result != read(directory / "audit.json"):
        raise ValueError("Stored native audit differs from the rechecked evidence")
    return result


def supplemental(name, args, *, tilelang=False):
    report = Path("/out") / f"{name}.xml"
    options = ["--noconftest", "-p", "no:cacheprovider", "-o", "addopts=", "-q", "-rfEs",
               "--junitxml", str(report), *map(str, args)]
    cmd = [sys.executable, "-m", "pytest", *options]
    if tilelang:
        cmd = [sys.executable, "-c",
               "from vllm.tilelang_utils import _ensure_tilelang_imported; _ensure_tilelang_imported(); "
               "import pytest,sys; sys.exit(pytest.main(sys.argv[1:]))", *options]
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    with (report.with_suffix(".log")).open("w") as log:
        status = subprocess.run(cmd, env=env, stdout=log, stderr=subprocess.STDOUT).returncode
    if status:
        raise RuntimeError(f"{name} failed ({status}):\n" + report.with_suffix(".log").read_text()[-4000:])
    return supplemental_result(report)


def inside():
    # Release this process's CUDA context before measuring native pool behaviour.
    device = json.loads(output(sys.executable, "-c",
        "import json,torch,vllm; print(json.dumps(dict(name=torch.cuda.get_device_name(), "
        "compute_capability=torch.cuda.get_device_capability(), torch=torch.__version__, "
        "cuda=torch.version.cuda, vllm_source=vllm.__file__)))").splitlines()[-1])
    if device["compute_capability"] != [12, 1]:
        raise RuntimeError("A real SM121 device is required")
    if Path(device["vllm_source"]).resolve().parent != IMAGE_SOURCE / "vllm":
        raise RuntimeError(f"Imported vLLM outside the image's source tree: {device['vllm_source']}")
    out = Path("/out")
    receipt = read(out / "identity.json")
    receipt["device"] = device
    write(out / "identity.json", receipt)
    manifest = read(out / "manifest.json")
    touched = [p for p in manifest["files"] if not Path(p).is_file()]
    if touched:
        raise RuntimeError(f"Tests absent from the image: {touched}")
    # Fail early on new GPU and wire-format cases before the full gate.
    extras = {}
    for name, selections, preload in [
        ("variant", [HERE / "test_native_variant.py"], True),
        ("warmup", [RC2 / "tools/test_graph_warmup_offline.py"], True),
        ("encoder", [RC2 / "tools/test_encoder_offline.py"], False),
        ("parser", [ROOT / "experiments/2026-11-28-1126-attention/tools/test_recorded_output.py",
                    "tests/parser/engine/test_deepseek_v41_token_provenance.py",
                    "tests/parser/engine/test_token_id_scanner.py"], False),
    ]:
        extras.update(supplemental(name, selections, tilelang=preload))
        print(f"{name}: {extras[name]} passed", flush=True)
    write(out / "supplemental.json", extras)
    subprocess.run([sys.executable, "/b/release_test_runner.py", str(out / "manifest.json"),
                    str(out / "component")], check=True)
    result = audit_bulk(out / "component", manifest)
    write(out / "audit.json", {**receipt, **result, "supplemental": extras, "status": "passed"})
    print(json.dumps({"status": "passed", "totals": result["totals"], "supplemental": extras}), flush=True)


def launch(window_id, image_id, directory):
    import window
    sys.path.insert(0, str(ROOT))
    from spark_ds41f.build import require_idle_build_host
    from spark_ds41f.config import read_cluster

    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
        raise ValueError("Pass the literal native image ID")
    config = read_cluster(CONFIG)
    if directory == window.OP or not directory.is_relative_to(window.OP):
        raise ValueError("Select a fresh receipt directory within this window's operation path")
    source = read(RC3 / "source.json")["vllm"]
    metadata = json.loads(output("docker", "image", "inspect", image_id))[0]
    labels = metadata["Config"]["Labels"]
    if (metadata["Id"] != image_id or metadata["Architecture"] != "arm64" or
            labels["local.spark-ds41f.vllm.patch-head"] != source["patch_head"] or
            labels["local.spark-ds41f.vllm.tree"] != source["expected_tree"] or
            any(labels.get(k) != v for k, v in config["container"]["expected_labels"].items())):
        raise ValueError("The image does not match the candidate recipe and source")
    mounts = [m for m in config["container"]["mounts"] if m[1] in {"/models", "/blobs", "/hub"}]
    model = next(m[0] for m in mounts if m[1] == "/models")
    tokenizer = Path(model) / "tokenizer.json"
    if not tokenizer.is_file():
        raise FileNotFoundError(tokenizer)
    manifest = make_manifest(source)
    with window.lock():
        hold = window.active(window_id)
        window.require_watchdog(window_id)
        if window.site.ingress_stage(hold) != "blocked":
            raise RuntimeError("The component gate requires closed ingress")
        if (window.OP / "candidate-image.txt").read_text().split()[0] != image_id:
            raise RuntimeError("Window image receipt differs from the component image")
        require_idle_build_host(config)
        if output("nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader"):
            raise RuntimeError("GPU is occupied before the component gate")
        directory.mkdir(parents=True, exist_ok=False)
        scratch = directory / "scratch"
        scratch.mkdir()
        write(directory / "identity.json", {"image_id": image_id, "window_id": window_id,
              "deployment_commit": output("git", "-C", str(ROOT), "rev-parse", "HEAD"),
              "source_head": source["patch_head"], "source_tree": source["expected_tree"],
              "tokenizer_sha256": hashlib.sha256(tokenizer.read_bytes()).hexdigest()})
        write(directory / "manifest.json", manifest)
        args = ["docker", "run", "-d", "--name", CONTAINER, "--label", f"{LABEL}={window_id}",
                "--network=none", "--gpus=all", "--ipc=host", "--device=/dev/infiniband:/dev/infiniband:rwm",
                "--security-opt=seccomp=unconfined", "--cap-add=IPC_LOCK", "--ulimit=memlock=-1:-1",
                "--ulimit=stack=67108864:67108864", "--ulimit=core=0", "--memory=112g", "--memory-swap=112g",
                "--entrypoint=python3", "-w", str(IMAGE_SOURCE), "-v", f"{ROOT}:/repo:ro",
                "-v", f"{ROOT}/scripts/release_tests:/b:ro", "-v", f"{directory}:/out:rw", "-v", f"{scratch}:/c:rw"]
        for host, target, mode in mounts:
            args.extend(["-v", f"{host.replace('{home}', str(Path.home()))}:{target}:{mode}"])
        for variable, value in {"PYTHONPATH": f"{IMAGE_SOURCE}:/b:/repo", "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1", "DS41F_SNAPSHOT": "/models", "STEERING_MODEL_IDENTITY": "/models",
                "STEERING_VLLM_SOURCE": str(IMAGE_SOURCE), "TILELANG_CACHE_DIR": "/c/tilelang-release-tests",
                "ENGRAM_ROWS_TEST_DIR": "/c/engram-rows-test", "DIRECT_IO_TEST_DIR": "/c/direct-io-test",
                "PYTHONDONTWRITEBYTECODE": "1", "VLLM_NO_USAGE_STATS": "1"}.items():
            args.extend(["-e", f"{variable}={value}"])
        args.extend([image_id, str(Path("/repo") / HERE.relative_to(ROOT) / "native.py"), "--inside"])
        container_id = output(*args)
    # Detached launch under the lease lock lets the independent watchdog identify
    # and stop this exact container if the SSH controller disappears.
    subprocess.run(["docker", "logs", "--follow", container_id], check=False)
    status = int(output("docker", "wait", container_id))
    subprocess.run(["docker", "rm", container_id], check=True)
    if status:
        raise SystemExit(status)
    if read(directory / "audit.json")["status"] != "passed":
        raise RuntimeError("Missing passing native audit")


if __name__ == "__main__":
    if sys.argv[1:] == ["--inside"]:
        inside()
    elif sys.argv[1:2] == ["--audit-only"]:
        parser = argparse.ArgumentParser(description="Recheck saved JUnit and JSON receipts without starting a container.")
        parser.add_argument("directory", type=Path)
        args = parser.parse_args(sys.argv[2:])
        print(json.dumps(audit_saved(args.directory), indent=2))
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("window")
        parser.add_argument("image_id")
        parser.add_argument("output", type=Path)
        cli = parser.parse_args()
        launch(cli.window, cli.image_id, cli.output.resolve())
