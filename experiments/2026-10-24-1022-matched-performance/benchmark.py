#!/usr/bin/env python3
"""Measure the final candidate with the frozen historical load client."""

import argparse
import concurrent.futures
import gzip
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "measurement"))
from servebench import client, load, prom, prompts, report

sys.path.insert(0, str(HERE.parent / "2026-10-23-1022-parser-byte-alignment"))
import qualification_requests as functional

BASE = "http://127.0.0.1:8000"
PROFILE = "heretic-1017-attn-v1"
CASES = [(1, 1), (1, 4), (4, 1), (1, 4), (4, 4), (1, 4)]


def write(path, value):
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False)


def validate_payload(body):
    if (
        body.get("steering_profile") != PROFILE
        or type(body.get("steering_scale")) is not int
        or body["steering_scale"] not in (1, 4)
    ):
        raise ValueError("Every generation requires explicit scale 1 or 4")


def validate_run(run, expected_documents):
    summary = run["summary"]
    counts = summary["requests"]
    if any(
        counts[k] for k in ("failed", "rejected", "foreign", "approximate_token_times")
    ):
        raise ValueError("Failed, foreign or approximately timed benchmark requests")
    if [d["tokens"] for d in run["documents"]] != expected_documents or any(
        d["prime_error"] for d in run["documents"]
    ):
        raise ValueError("Historical document sizes or priming differ")
    if not run["requests"] or any(
        r["completion_tokens"] != 256 or r["finish_reason"] != "length" or r["error"]
        for r in run["requests"]
    ):
        raise ValueError("Incomplete or truncated benchmark response")
    if summary["output_tps"]["n"] != 6 or summary["output_tps"]["mean"] <= 0:
        raise ValueError("Incomplete measurement windows")
    hit_rate = summary.get("server", {}).get("prefix_cache_hit_rate", {}).get("mean")
    if hit_rate is None or hit_rate < 0.9:
        raise ValueError("Historical warm-prefix workload was not reproduced")
    for name in ("ttft_s", "tpot_ms", "e2e_s"):
        if not all(
            summary["latency"][name].get(k) is not None
            for k in ("mean", "p50", "p90", "p99")
        ):
            raise ValueError("Missing latency distribution")


def require_window():
    context = json.loads(os.environ["SPARK_WINDOW_CONTEXT"])
    m = json.loads(Path(context["manifest"]).read_text())
    state = json.loads((Path(context["state_dir"]) / "state.json").read_text())
    hold = json.loads(Path(m["hold_path"]).read_text())
    for item in (state, hold):
        if item["window_id"] != context["window_id"] or item[
            "manifest_sha256"
        ] != functional.digest(m):
            raise ValueError("Window ownership differs")
    if (
        state["phase"] != "testing"
        or state["last_arm"] != "s-loaded"
        or time.monotonic() >= state["restore_monotonic"]
    ):
        raise ValueError("No active candidate window")
    expected = json.loads((HERE / "base.json").read_text())["candidate"]
    actual = m["services"]["s-loaded"]["identity"]
    if any(
        actual[k] != expected[k] for k in ("image_id", "vllm_tree", "profile_sha256")
    ):
        raise ValueError("Candidate identity differs")
    files = json.loads((HERE / "external-inputs.json").read_text())["files"]
    if {str(p) for p in prompts.standard_library_sources()} != set(files):
        raise ValueError("Document source inventory changed")
    return m, Path(context["state_dir"])


def thermal_gate(m, output):
    def one(pair):
        name, ip = pair
        code = "import pathlib,json,time;print(json.dumps({'at':time.time(),'hottest_c':max(int(p.read_text()) for p in pathlib.Path('/sys/class/thermal').glob('thermal_zone*/temp'))/1000}))"
        result = subprocess.run(
            [
                "/usr/bin/ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=5",
                "admin@" + ip,
                shlex.join(["/usr/bin/python3", "-c", code]),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        return {"node": name, **json.loads(result.stdout)}

    deadline = time.monotonic() + 600
    while True:
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            rows = list(pool.map(one, m["site_nodes"].items()))
        with (output / "thermal.jsonl").open("a") as stream:
            stream.write(json.dumps(rows) + "\n")
        if len(rows) == 4 and all(
            0 <= r["hottest_c"] < 55 and 0 <= time.time() - r["at"] < 20 for r in rows
        ):
            return rows
        if time.monotonic() >= deadline:
            raise ValueError("Four-node thermal baseline unavailable")
        time.sleep(5)


def quality(output, stage):
    functional.client.BASE = BASE
    rows = []
    for scale in (1, 4):
        body = functional.client.body(
            "Return only the integer result of 19 + 23.", scale
        )
        result = functional.request("/v1/chat/completions", body, True)
        rows.append({"kind": "arithmetic", "scale": scale, "answer": result})
        write(output / (stage + "-quality.json.gz"), rows)
        functional.check_quality("arithmetic", result)
        result = functional.request(
            "/v1/chat/completions", functional.previous.tool_body(scale)
        )
        rows.append({"kind": "tool", "scale": scale, "answer": result})
        write(output / (stage + "-quality.json.gz"), rows)
        functional.previous.check_tool(result)


def run(output):
    m, directory = require_window()
    if not output.resolve().is_relative_to(directory.resolve()):
        raise ValueError("Output must be inside the owned window")
    output.mkdir(exist_ok=False)
    history = json.loads((HERE / "history.json").read_text())
    prompt_set = prompts.load(str(HERE / "prompts.jsonl"))
    if prompts.digest(prompt_set) != history["prompts_sha256"]:
        raise ValueError("Historical prompts differ")
    progress = {"status": "running", "runs": []}

    def save():
        p = output / "progress.tmp"
        p.write_text(json.dumps(progress, indent=2) + "\n")
        p.replace(output / "progress.json")

    original_stream, original_documents = client.stream_chat, load.documents

    def guarded_stream(url, body, **kwargs):
        validate_payload(body)
        return original_stream(url, body, **kwargs)

    client.stream_chat = guarded_stream

    def recorded_documents(*args):
        docs = original_documents(*args)
        write(output / (current_label + "-documents.json.gz"), docs)
        return docs

    load.documents = recorded_documents
    save()
    try:
        quality(output, "before")
        for index, (scale, concurrency) in enumerate(CASES):
            require_window()
            thermal = thermal_gate(m, output)
            prom.require_idle(BASE, "matched performance")
            req = urllib.request.Request(
                BASE + "/reset_prefix_cache",
                data=b"{}",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=120) as response:
                if response.status != 200:
                    raise ValueError("Cache reset failed")
            current_label = f"run-{index}-s{scale}-c{concurrency}"
            spec = load.Load(
                mode="closed",
                concurrency=concurrency,
                warmup_s=15,
                duration_s=90,
                batches=6,
                max_tokens=256,
                seed=2048,
                context_tokens=4096,
            )
            extra = {
                "temperature": 0,
                "seed": 2048,
                "steering_profile": PROFILE,
                "steering_scale": scale,
            }
            raw = load.run(
                BASE, "deepseek-v4.1-flash", prompt_set, spec, extra_body=extra
            )
            origin = raw["start"]
            result = {
                "schema": "servebench/1",
                "label": current_label,
                "workload": spec.describe()
                | {
                    "prompt_count": len(prompt_set),
                    "prompts_sha256": prompts.digest(prompt_set),
                    "extra_body": extra,
                },
                "summary": report.summarize(raw),
                "documents": raw["documents"],
                "thermal_start": thermal,
                "requests": [
                    {
                        key: value - origin
                        if key in ("sent", "first", "end") and value is not None
                        else value
                        for key, value in row.items()
                        if key != "events"
                    }
                    | {"events": [[at - origin, n] for at, n in row["events"]]}
                    for row in raw["records"]
                ],
            }
            path = output / (current_label + ".json.gz")
            write(path, result)
            expected = next(
                r
                for r in history["runs"]
                if r["kind"] == "initial"
                and r["scale"] == scale
                and r["concurrency"] == concurrency
            )
            validate_run(result, [d["tokens"] for d in expected["documents"]])
            progress["runs"].append(
                {
                    "scale": scale,
                    "concurrency": concurrency,
                    "file": path.name,
                    "summary": result["summary"],
                }
            )
            save()
            print(
                json.dumps(
                    {
                        "completed": index + 1,
                        "scale": scale,
                        "concurrency": concurrency,
                        "tps": result["summary"]["output_tps"]["mean"],
                    }
                ),
                flush=True,
            )
        quality(output, "after")
        progress["status"] = "passed"
    except BaseException as error:
        progress["status"] = "failed"
        progress["error"] = str(error)
        raise
    finally:
        client.stream_chat, load.documents = original_stream, original_documents
        save()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    run(p.parse_args().output)
