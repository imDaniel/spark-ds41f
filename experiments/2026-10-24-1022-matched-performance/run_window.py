#!/usr/bin/env python3
"""Bounded final-candidate performance window with pinned r6x recovery."""

import argparse
import gzip
import runpy
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
parent = runpy.run_path(
    str(HERE.parent / "2026-10-23-1022-capacity-validation/run_window.py")
)
import sys
import time

sys.path.insert(0, str(HERE))
from trial_executor import TrialExecutor as Executor

read, sha = (parent[k] for k in ("read", "sha"))
HOST_EXECUTABLES = (*parent["HOST_EXECUTABLES"], "/usr/bin/ssh")


def implementation_paths():
    paths = parent["implementation_paths"]()
    paths += [
        HERE / n
        for n in (
            "run_window.py",
            "benchmark.py",
            "history.json",
            "base.json",
            "prompts.jsonl",
            "plan.json",
            "external-inputs.json",
            "trial_executor.py",
            "trial_probe.py",
            "cluster.json",
        )
    ]
    paths += list((HERE / "measurement/servebench").glob("*.py"))
    paths += [ROOT / r["path"] for r in read(HERE / "history.json")["runs"]]
    return list(dict.fromkeys(paths + [Path(p) for p in HOST_EXECUTABLES]))


def validate_history():
    import json

    history = read(HERE / "history.json")
    for name, expected in history["client_hashes"].items():
        if sha(HERE / "measurement/servebench" / name) != expected:
            raise ValueError("Historical measurement client changed")
    expected = {
        "mode": "closed",
        "warmup_s": 15,
        "duration_s": 90,
        "batches": 6,
        "max_tokens": 256,
        "fixed_length": True,
        "seed": 2048,
        "context_tokens": 4096,
        "prompt_count": 16,
        "prompts_sha256": history["prompts_sha256"],
    }
    rows = history["runs"]
    if len(rows) != 7 or {(r["scale"], r["concurrency"]) for r in rows} != {
        (1, 1),
        (1, 4),
        (4, 1),
        (4, 4),
    }:
        raise ValueError("Historical comparison coverage differs")
    for row in rows:
        path = ROOT / row["path"]
        if sha(path) != row["sha256"]:
            raise ValueError("Historical benchmark receipt changed")
        run = json.loads(gzip.decompress(path.read_bytes()))
        if any(run["workload"].get(k) != v for k, v in expected.items()):
            raise ValueError("Historical workload differs")
        if run["documents"] != row["documents"] or run["workload"] != row["workload"]:
            raise ValueError("Historical metadata differs from raw receipt")
        if any(
            run["summary"]["requests"][k]
            for k in ("failed", "foreign", "rejected", "approximate_token_times")
        ):
            raise ValueError("Historical reference contains failed or foreign requests")
    return len(rows)


def validate_trial_config():
    original = read(HERE.parent / "2026-10-23-1022-parser-byte-alignment/cluster.json")
    candidate = read(HERE / "cluster.json")
    args = candidate["serve_args"]
    if (
        args[args.index("--host") + 1] != "0.0.0.0"
        or args[args.index("--port") + 1] != "8000"
    ):
        raise ValueError("Trial endpoint differs")
    if candidate["deployment"]["ready_url"] != "http://127.0.0.1:8000/v1/models":
        raise ValueError("Trial readiness endpoint differs")
    for flag in ("--host", "--port"):
        args[args.index(flag) + 1] = original["serve_args"][
            original["serve_args"].index(flag) + 1
        ]
    candidate["deployment"]["ready_url"] = original["deployment"]["ready_url"]
    if candidate != original:
        raise ValueError("Trial changed non-network compute configuration")


def validate_implementation(x):
    paths = implementation_paths() + [
        Path(p) for p in read(HERE / "external-inputs.json")["files"]
    ]
    if any(x.m["implementation_files"].get(str(p)) != sha(p) for p in paths):
        raise ValueError("Incomplete or changed implementation pins")
    x.check_files(x.m["implementation_files"])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", type=Path)
    p.add_argument("--authorization", type=Path)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--watchdog", action="store_true")
    p.add_argument("--recover", action="store_true")
    args = p.parse_args()
    if not args.execute:
        print((HERE / "plan.json").read_text())
        return
    x = Executor(args.manifest, args.authorization)
    if args.watchdog:
        while True:
            result = x.watch_once()
            if result in ("restored", "manual"):
                return
            time.sleep(x.m["poll_seconds"])
    if args.recover:
        x.recover()
        return
    actual = x.m["services"]["s-loaded"]["identity"]
    expected = read(HERE / "base.json")["candidate"]
    if any(
        actual[k] != expected[k] for k in ("image_id", "vllm_tree", "profile_sha256")
    ):
        raise ValueError("Candidate identity differs")
    if x.m["window_seconds"] > 3600 or x.m["restore_reserve_seconds"] < 900:
        raise ValueError("Requires <=60 minutes and >=15-minute recovery reserve")
    validate_implementation(x)
    validate_history()
    validate_trial_config()
    parent["validate_component_reuse"]()
    parent["validate_request_reuse"]()
    x.open()
    try:
        x.event("matched_performance_history_verified", receipts=7)
        x.switch("s-loaded")
        with x.lock():
            x.load_owned()
            x.run(
                [
                    "/usr/bin/python3",
                    str(HERE / "benchmark.py"),
                    "--output",
                    str(x.directory / "performance"),
                ],
                str(HERE),
                x.m["implementation_files"],
                "matched-explicit-scale-performance",
                timeout=2700,
            )
            data = x.observe()
            if not all(
                n["services"]["s-loaded"]["running"]
                and n["services"]["s-loaded"]["guard_active"]
                for n in data["nodes"]
            ):
                raise ValueError("Candidate rank or memory guard stopped")
            x.event("matched_performance_completed")
        x.handoff(x.m["implementation_files"])
    finally:
        if x.hold.exists() and read(x.state_path)["phase"] not in (
            "manual",
            "owner_trial",
        ):
            x.recover()
        if read(x.state_path)["phase"] not in ("closed", "owner_trial"):
            raise RuntimeError("Manual recovery required; retain hold")


if __name__ == "__main__":
    main()
