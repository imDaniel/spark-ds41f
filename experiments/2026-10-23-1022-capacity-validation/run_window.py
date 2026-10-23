#!/usr/bin/env python3
"""Continue same-image capacity qualification and restore the pinned service."""

import argparse
import runpy
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
PARENT = HERE.parent / "2026-10-23-1022-parser-byte-alignment"
prior = runpy.run_path(str(PARENT / "run_window.py"))
checks = runpy.run_path(str(PARENT / "qualification_requests.py"))
Executor, read, sha = (prior[k] for k in ("Executor", "read", "sha"))
HOST_EXECUTABLES = prior["HOST_EXECUTABLES"]
validate_component_reuse = prior["validate_component_reuse"]


def implementation_paths():
    paths = prior["implementation_paths"]()
    paths += [
        HERE / p
        for p in (
            "run_window.py",
            "capacity_requests.py",
            "base.json",
            "plan.json",
            "request-reuse.json",
        )
    ]
    paths += [ROOT / p for p in read(HERE / "request-reuse.json")["files"]]
    return paths


def validate_implementation(x):
    if any(
        x.m["implementation_files"].get(str(p)) != sha(p)
        for p in implementation_paths()
    ):
        raise ValueError("Incomplete or changed implementation pins")
    x.check_files(x.m["implementation_files"])


def validate_request_reuse():
    receipt = read(HERE / "request-reuse.json")
    for name, expected in receipt["files"].items():
        if sha(ROOT / name) != expected:
            raise ValueError("Prior request receipt changed")
    directory = HERE / "runs/attempt-4"
    manifest = read(directory / "manifest.json")
    candidate = read(HERE / "base.json")["candidate"]
    actual = manifest["services"]["s-loaded"]["identity"]
    if (
        receipt["previous_window_id"] != manifest["window_id"]
        or receipt["image_id"] != candidate["image_id"]
        or any(
            actual[k] != candidate[k]
            for k in ("image_id", "profile_sha256", "vllm_tree")
        )
        or actual["config_sha256"] != sha(PARENT / "cluster.json")
    ):
        raise ValueError("Prior request runtime identity differs")
    report = read(directory / "state/requests/requests.json")
    rows = report["rows"]
    if (
        report["status"] != "failed"
        or len(rows) != 40
        or report["error"] != "AssertionError: Requested concurrency was not observed"
    ):
        raise ValueError("Unexpected prior qualification failure")
    tools = [r for r in rows if r["kind"] == "tool"]
    if {(r["scale"], r["choice"], r["stream"]) for r in tools} != {
        (s, c, stream)
        for s in (1, 4)
        for c in ("named", "required", "auto")
        for stream in (False, True)
    } or len(tools) != 12:
        raise ValueError("Incomplete tool coverage")
    for row in tools:
        answer = row["answer"]
        normalized = (
            {"choices": [{"message": answer["message"]}]} if row["stream"] else answer
        )
        checks["previous"].check_tool(normalized)
        finish = (
            answer["finish_reason"]
            if row["stream"]
            else answer["choices"][0]["finish_reason"]
        )
        if finish not in ("stop", "tool_calls"):
            raise ValueError("Truncated tool response")
    followups = [r for r in rows if r["kind"] == "tool-followup"]
    if (
        len(followups) != 2
        or {r["scale"] for r in followups} != {1, 4}
        or any(r["finish_reason"] != "stop" for r in followups)
    ):
        raise ValueError("Incomplete tool followups")
    quality = [r for r in rows if r["kind"] == "quality"]
    if len(quality) != 20 or {(r["cycle"], r["scale"], r["case"]) for r in quality} != {
        (cycle, scale, case)
        for cycle, scale in enumerate((0, 1, 4, 0))
        for case, _ in checks["QUALITY"]
    }:
        raise ValueError("Incomplete quality coverage")
    for row in quality:
        checks["check_quality"](row["case"], row)
    if [r["text_sha256"] for r in quality if r["cycle"] == 0] != [
        r["text_sha256"] for r in quality if r["cycle"] == 3
    ]:
        raise ValueError("Return-to-zero failed")
    cache = read(directory / "state/requests/cache.json")
    if cache["passed"] is not True or len(cache["rows"]) != 13:
        raise ValueError("Cache coverage incomplete")
    capacity = [r for r in rows if r["kind"] == "capacity"]
    if [(r["input_tokens"], r["concurrency"]) for r in capacity] != [
        (32768, 1),
        (131072, 1),
        (262144, 1),
        (524288, 1),
        (1000000, 1),
        (8192, 16),
    ]:
        raise ValueError("Prior capacity coverage differs")
    for row in capacity:
        if len(row["results"]) != row["concurrency"]:
            raise ValueError("Incomplete capacity responses")
        for result in row["results"]:
            usage = result["usage"]
            if (
                usage["prompt_tokens"] != row["input_tokens"]
                or usage["completion_tokens"] != 32
                or usage["prompt_tokens_details"]["cached_tokens"] != 0
            ):
                raise ValueError("Capacity usage differs")
    return {"tool": 12, "followup": 2, "quality": 20, "cache": 13, "cold_c1": 5}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print((HERE / "plan.json").read_text())
        return
    x = Executor(args.manifest, args.authorization)
    expected = read(HERE / "base.json")["candidate"]
    actual = x.m["services"]["s-loaded"]["identity"]
    if any(
        actual.get(k) != expected[k]
        for k in ("image_id", "profile_sha256", "vllm_tree")
    ):
        raise ValueError("Candidate image/profile differs")
    if x.m["window_seconds"] > 4500 or x.m["restore_reserve_seconds"] < 900:
        raise ValueError("Requires <=75 minutes with >=15-minute recovery reserve")
    validate_implementation(x)
    totals = validate_component_reuse()
    reused = validate_request_reuse()
    x.open()
    try:
        x.event("release_receipts_reused", totals=totals, requests=reused)
        x.switch("s-loaded")
        with x.lock():
            x.load_owned()
            x.run(
                [
                    "/usr/bin/python3",
                    str(HERE / "capacity_requests.py"),
                    "--execute",
                    "--output",
                    str(x.directory / "requests"),
                ],
                str(HERE),
                x.m["implementation_files"],
                "release-capacity-continuation",
                timeout=2700,
            )
            data = x.observe()
            if not all(
                n["services"]["s-loaded"]["running"]
                and n["services"]["s-loaded"]["guard_active"]
                for n in data["nodes"]
            ):
                raise ValueError("Candidate rank or memory guard stopped")
            x.event("release_capacity_and_stability_passed")
    finally:
        if x.hold.exists() and read(x.state_path)["phase"] != "manual":
            x.recover()
        if read(x.state_path)["phase"] != "closed":
            raise RuntimeError("Manual recovery required; retain hold")


if __name__ == "__main__":
    main()
