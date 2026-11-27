"""Run the site's preserved 1022 workloads against the verified 1126 Attention service."""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys

from window import HERE, OP, ROOT, active, cmd, site
sys.path.insert(0, str(ROOT))
from spark_ds41f import sonar

sys.path.insert(0, str(HERE.parent / "2026-10-24-1022-matched-performance"))
import benchmark
sys.path.insert(0, str(HERE.parent / "2026-10-23-1022-capacity-validation"))
import capacity_requests

NODES = {f"spark{i:02}": f"10.42.0.{i}" for i in range(1, 5)}
TREE = "7c445f265a7bf21b4860735cd972b326968150c3"
PROFILE = "81ca5d96cf78a1c0193ab80071efab43d666b5a57e8633ced71b227031bde1f2"


def qualify(stage, window, image_id, output):
    def verify():
        hold = active(window)
        if hold["phase"] != "testing" or site.ingress_stage(hold) != "blocked":
            raise RuntimeError("The owned, isolated test window is not active")
        revision = cmd(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture=True).stdout.strip()
        def rank(addr):
            query = shlex.join(["docker", "inspect", "--format", "{{json .}}", "ds41f-1126-attention"])
            obj = json.loads(cmd(["ssh", "-o", "BatchMode=yes", "admin@" + addr, query], capture=True).stdout)
            if obj["Image"] != image_id or not obj["State"]["Running"]:
                raise ValueError(f"{addr}: wrong running image")
            labels = obj["Config"]["Labels"]
            if labels.get("local.spark-ds41f.vllm.tree") != TREE:
                raise ValueError(f"{addr}: wrong vLLM tree")
            source = next(m["Source"] for m in obj["Mounts"] if m["Destination"] == "/steering-1126" and not m["RW"])
            file = str(Path(source) / "steering/heretic-1017-attn-v1.safetensors")
            checks = shlex.join(["sha256sum", file]) + " && " + shlex.join(["git","-C",str(ROOT),"rev-parse","HEAD"])
            values = cmd(["ssh", "-o", "BatchMode=yes", "admin@" + addr, checks], capture=True).stdout.split()
            if values != [PROFILE, file, revision]:
                raise ValueError(f"{addr}: checkout or mounted profile differs")
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            list(pool.map(rank, NODES.values()))
        return {"site_nodes": NODES}, OP
    verify()
    if output.parent != OP or output.exists():
        raise ValueError("A new result directory inside the window is required")
    benchmark.functional.client.BASE = benchmark.BASE
    if stage == "functional":
        from functional import run
        run(output)
    elif stage in ("capacity", "capacity-overlap", "capacity-sustained"):
        capacity_requests.client.BASE = benchmark.BASE
        original_cases = capacity_requests.CASES
        if stage == "capacity-sustained":
            # Long prefills also outlast short early responses. Preserve those
            # attempts and prove four resident requests with longer generations.
            capacity_requests.CASES = [
                (length, scales, output_tokens)
                for (length, scales, _), output_tokens in zip(
                    original_cases[1:], (4096, 16384), strict=True
                )
            ]
        elif stage == "capacity-overlap":
            # Keep the original C16/256 receipt, whose early completions only
            # overlapped ten admitted requests. Sustain the same mixed scales
            # for longer without relaxing the sixteen-running admission gate.
            capacity_requests.CASES = [
                (*original_cases[0][:2], 1024), *original_cases[1:]
            ]
        else:
            capacity_requests.CASES = [
                (length, [1], 32) for length in (32768, 131072, 262144, 524288, 1000000)
            ] + original_cases
        try:
            capacity_requests.run(output)
        finally:
            capacity_requests.CASES = original_cases
    elif stage == "performance":
        # Retain the historical workload and thermal gate while using the
        # deployment's pinned measurement client for every new timing run.
        identity = sonar.revision()
        if not identity["pinned"] or identity["dirty"]:
            raise ValueError("Sonar checkout differs from the deployment pin")
        for name in ("client", "load", "prom", "prompts", "report"):
            setattr(benchmark, name, sonar.module(name))
        expected = json.loads((benchmark.HERE / "external-inputs.json").read_text())["files"]
        if {str(p) for p in benchmark.prompts.standard_library_sources()} != set(expected):
            raise ValueError("Host document source inventory differs from 1022")
        for path, digest in expected.items():
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
                raise ValueError(f"Host document source differs: {path}")
        benchmark.require_window = verify
        previous_policy = os.environ.get("SONAR_STREAM_COLLECTOR")
        os.environ["SONAR_STREAM_COLLECTOR"] = "text-chunks"
        identity["stream_collector"] = "text-chunks"
        from thermal import gate
        previous_thermal = benchmark.thermal_gate
        benchmark.thermal_gate = gate
        identity["thermal_freshness"] = "controller-monotonic-query-v1"
        try:
            benchmark.run(output)
        finally:
            benchmark.thermal_gate = previous_thermal
            if previous_policy is None:
                os.environ.pop("SONAR_STREAM_COLLECTOR", None)
            else:
                os.environ["SONAR_STREAM_COLLECTOR"] = previous_policy
            if output.is_dir():
                (output / "sonar.json").write_text(json.dumps(identity, indent=2) + "\n")
    verify()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("stage", choices=["functional", "capacity", "capacity-overlap", "capacity-sustained", "performance"])
    p.add_argument("window")
    p.add_argument("image_id")
    p.add_argument("output", type=Path)
    a = p.parse_args()
    qualify(a.stage, a.window, a.image_id, a.output.resolve())
