"""Run one isolated PR 60 hardware stage against its attested four-rank engine."""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CFG = (HERE / "cluster.json").relative_to(ROOT).as_posix()
PREVIOUS = HERE.parent / "2026-11-28-1126-tp4-qualification"


def rank_identity(node, cluster, image_id, revision, profile):
    from spark_ds41f.config import expected_binds, expected_command

    address = node["management_ip"]
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "admin@" + address]

    def read(command):
        return subprocess.check_output([*ssh, command], text=True, timeout=30)

    container = cluster["container"]
    obj, = json.loads(read(shlex.join(["docker", "inspect", container["name"]])))
    actual = obj["Config"]
    state = obj["State"]
    labels = actual["Labels"]
    if (obj["Image"] != image_id or state["Status"] != "running" or state["Paused"]
            or not state["Running"] or state["Restarting"] or state["OOMKilled"]
            or actual["Image"] != container["image"] or labels.get("spark.cluster-config") != CFG
            or any(labels.get(k) != v for k, v in container["expected_labels"].items())
            or actual["Cmd"] != expected_command(cluster, node)
            or sorted(obj["HostConfig"]["Binds"]) != sorted(expected_binds(cluster))):
        raise ValueError(f"{address}: candidate identity or launch arguments differ")
    path = Path(profile["profile_path"])
    mounts = [m for m in obj["Mounts"] if m["Destination"] == "/steering-1126" and not m["RW"]]
    mount, = mounts
    digest_path = Path(mount["Source"]) / path.relative_to(mount["Destination"])
    hashes = read(shlex.join(["sha256sum", str(digest_path)]) + " && "
                  + shlex.join(["git", "-C", cluster["deployment"]["path"], "rev-parse", "HEAD"]))
    if hashes.split() != [profile["profile_sha256"], str(digest_path), revision]:
        raise ValueError(f"{address}: mounted profile or deployed revision differs")
    return node["name"], {"container_id": obj["Id"], "started_at": state["StartedAt"]}


def capacity_cases(original_cases):
    cold = [(n, [1], 32) for n in (32768, 131072, 262144, 524288, 1000000)]
    sustained = [(n, scales, tokens) for (n, scales, _), tokens in
                 zip(original_cases, (1024, 4096, 16384), strict=True)]
    return cold + sustained


def workload():
    sys.path.insert(0, str(ROOT))
    sys.path.extend([str(HERE.parent / "2026-10-24-1022-matched-performance"),
                     str(HERE.parent / "2026-10-23-1022-capacity-validation"), str(PREVIOUS)])
    import benchmark
    return benchmark


def performance_inputs(benchmark):
    from spark_ds41f import sonar

    identity = sonar.revision()
    if not identity["pinned"] or identity["dirty"]:
        raise ValueError("Dirty or unpinned Sonar checkout")
    for name in ("client", "load", "prom", "prompts", "report"):
        setattr(benchmark, name, sonar.module(name))
    files = json.loads((benchmark.HERE / "external-inputs.json").read_text())["files"]
    if {str(p) for p in benchmark.prompts.standard_library_sources()} != set(files):
        raise ValueError("Host source inventory differs from the frozen workload")
    for path, digest in files.items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
            raise ValueError(f"Host document source differs: {path}")
    history = json.loads((benchmark.HERE / "history.json").read_text())
    digest = benchmark.prompts.digest(benchmark.prompts.load(str(benchmark.HERE / "prompts.jsonl")))
    if digest != history["prompts_sha256"]:
        raise ValueError("Historical prompt digest differs")
    identity.update(stream_collector="text-chunks", thermal_freshness="controller-monotonic-query-v1",
                    host_source_files=len(files), prompts_sha256=digest)
    return identity


def performance(benchmark, output, verify):
    from thermal import gate

    identity = performance_inputs(benchmark)

    def thermal(manifest, out):
        rows = gate(manifest, out)
        verify()
        return rows

    benchmark.require_window = verify
    benchmark.thermal_gate = thermal
    os.environ["SONAR_STREAM_COLLECTOR"] = "text-chunks"
    try:
        benchmark.run(output)
    finally:
        if output.is_dir():
            (output / "sonar.json").write_text(json.dumps(identity, indent=2) + "\n")


def qualify(stage, window_id, image_id, output):
    from window import OP, active, require_watchdog, site

    if not __debug__:
        raise RuntimeError("Qualification requires Python assertions to be enabled")
    sys.path.insert(0, str(ROOT))
    from spark_ds41f.config import configuration

    if output.parent != OP or output.exists() or output.is_symlink():
        raise ValueError("Choose a new result directory directly inside the window's operation directory")
    if (not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id)
            or (OP / "candidate-image.txt").read_text().split()[0] != image_id):
        raise ValueError("Image ID does not match the staged candidate receipt")
    cluster, site_map, _ = configuration(argparse.Namespace(cluster_config=CFG))
    if not cluster["deployment"]["launch_enabled"] or cluster["container"]["name"] != "ds41f-1126-attention-fixes":
        raise ValueError("Qualification requires the launch-enabled candidate")
    nodes = site_map["nodes"]
    addresses = {n["name"]: n["management_ip"] for n in nodes}
    if set(addresses) != {"spark01", "spark02", "spark03", "spark04"} or len(nodes) != 4:
        raise ValueError("The qualification requires the four site ring nodes")
    args = cluster["serve_args"]
    profile = json.loads(args[args.index("--additional-config") + 1])["ds41_steering"]
    revision = subprocess.check_output(["git", "-C", ROOT, "rev-parse", "HEAD"], text=True).strip()
    attested = None

    def verify():
        nonlocal attested
        hold = active(window_id)
        require_watchdog(window_id)
        if hold["phase"] != "testing" or site.ingress_stage(hold) != "blocked":
            raise RuntimeError("The owned test window is not isolated")
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            ranks = dict(pool.map(lambda n: rank_identity(n, cluster, image_id, revision, profile), nodes))
        if attested is not None and ranks != attested:
            raise ValueError("A candidate rank restarted within this stage")
        attested = ranks
        return {"site_nodes": addresses}, OP

    verify()
    benchmark = workload()
    benchmark.functional.client.BASE = benchmark.BASE
    if stage == "performance":
        performance(benchmark, output, verify)
    elif stage == "capacity":
        import capacity_requests
        capacity_requests.client.BASE = benchmark.BASE
        capacity_requests.CASES = capacity_cases(capacity_requests.CASES)
        capacity_requests.run(output)
    elif stage == "functional":
        from functional import run
        run(output)
    elif stage == "targeted":
        from targeted import run
        run(output)
    else:
        raise ValueError(f"Unknown stage: {stage}")
    verify()
    receipt = {"window_id": window_id, "stage": stage, "deployment_commit": revision,
               "image_id": image_id, "profile_sha256": profile["profile_sha256"], "ranks": attested,
               "vllm_tree": cluster["container"]["expected_labels"]["local.spark-ds41f.vllm.tree"]}
    with (output / "engine-start.json").open("x") as stream:
        json.dump(receipt, stream, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="stage", required=True)
    subparsers.add_parser("inputs", help="Verify host performance inputs before reserving the ring")
    for stage in ("functional", "targeted", "capacity", "performance"):
        stage_parser = subparsers.add_parser(stage)
        stage_parser.add_argument("window")
        stage_parser.add_argument("image_id")
        stage_parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    if arguments.stage == "inputs":
        print(json.dumps(performance_inputs(workload()), indent=2))
    else:
        qualify(arguments.stage, arguments.window, arguments.image_id, arguments.output.resolve())
