#!/usr/bin/env python3
"""Candidate-only release requests. Every generation explicitly names its scale."""

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "2026-10-23-1022-window"))
import request_screen as client

sys.path.insert(0, str(HERE.parent / "2026-10-23-1022-scale-validation"))
import scale_requests as previous


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def request(path, body, stream=False, cancel=False):
    if path in ("/v1/chat/completions", "/v1/completions"):
        if (
            body.get("steering_profile") != client.PROFILE
            or type(body.get("steering_scale")) not in (int, float)
            or body["steering_scale"] not in (0, 1, 4)
        ):
            raise ValueError(
                "Generation requires the pinned profile and explicit scale 0, 1 or 4"
            )
    elif path != "/tokenize":
        raise ValueError("Unexpected endpoint")
    start = time.monotonic()
    started_at = time.time()
    req = urllib.request.Request(
        client.BASE + path,
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=1200) as response:
        if not stream:
            result = json.load(response)
            if result.get("error") or not result.get("choices") and path != "/tokenize":
                raise ValueError("Invalid non-streaming response")
            return result
        message = {"role": "assistant", "content": "", "reasoning": ""}
        calls = {}
        usage = None
        finish = None
        first = None
        done = False
        token_ids = []
        for line in response:
            if not line.startswith(b"data: "):
                continue
            raw = line[6:].strip()
            if raw == b"[DONE]":
                done = True
                break
            item = json.loads(raw)
            if item.get("error"):
                raise ValueError("SSE error: " + str(item["error"]))
            usage = item.get("usage") or usage
            for choice in item.get("choices", []):
                delta = choice.get("delta", {})
                if choice.get("text"):
                    delta = {"content": choice["text"]}
                token_ids.extend(choice.get("token_ids") or [])
                meaningful = (
                    delta.get("content")
                    or delta.get("reasoning")
                    or delta.get("tool_calls")
                )
                if meaningful and first is None:
                    first = time.monotonic()
                if meaningful and cancel:
                    return {"cancelled": True, "ttft": first - start}
                for field in ("content", "reasoning"):
                    message[field] += delta.get(field) or ""
                for call in delta.get("tool_calls") or []:
                    target = calls.setdefault(
                        call["index"],
                        {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        },
                    )
                    if call.get("id"):
                        if target["id"] and target["id"] != call["id"]:
                            raise ValueError("Tool call ID changed during streaming")
                        target["id"] = call["id"]
                    for field in ("name", "arguments"):
                        target["function"][field] += (
                            call.get("function", {}).get(field) or ""
                        )
                finish = choice.get("finish_reason") or finish
        if not done or first is None or not usage or not finish:
            raise ValueError("Incomplete or empty stream")
    if calls:
        message["tool_calls"] = [calls[i] for i in sorted(calls)]
    elapsed = time.monotonic() - start
    text = message["content"]
    return {
        "text": text,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "ttft": first - start,
        "duration": elapsed,
        "usage": usage,
        "finish_reason": finish,
        "message": message,
        "token_ids": token_ids,
        "started_at": started_at,
        "finished_at": time.time(),
        "first_visible_at": started_at + first - start,
        "decode_tps": max(0, usage["completion_tokens"] - 1)
        / max(0.001, time.monotonic() - first),
    }


CASES = [
    (8192, [0, 1, 4, 1] * 4, 256),
    (262144, [0, 1, 4, 1], 768),
    (1000000, [1] * 4, 3072),
]


def parse_metrics(text):
    names = ("num_requests_running", "num_requests_waiting", "kv_cache_usage_perc")
    result = {}
    for name in names:
        values = re.findall(
            r"^vllm:" + name + r"(?:\{[^\n]*\})? ([\d.e+-]+)$", text, re.MULTILINE
        )
        if not values:
            raise ValueError("Missing capacity metric: " + name)
        result[name] = max(map(float, values))
    return result


def check_capacity(row):
    if len(row["results"]) != row["concurrency"]:
        raise ValueError("Missing capacity responses")
    for result in row["results"]:
        usage = result["usage"]
        if (
            usage["prompt_tokens"] != row["input_tokens"]
            or usage["completion_tokens"] != row["output_tokens"]
            or usage["prompt_tokens_details"]["cached_tokens"] != 0
            or result["finish_reason"] != "length"
        ):
            raise ValueError("Capacity usage or finish reason differs")
    if not row["samples"]:
        raise ValueError("Missing capacity admission metrics")
    if max(s["num_requests_running"] for s in row["samples"]) < row["concurrency"]:
        raise ValueError("Requested concurrency was not observed")


def run(output):
    output.mkdir(exist_ok=False)
    report = {"status": "running", "rows": []}

    def save():
        temporary = output / "requests.tmp"
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(output / "requests.json")

    def record(kind, **row):
        report["rows"].append({"kind": kind, **row})
        save()

    try:
        body = previous.tool_body(1)
        body["return_token_ids"] = True
        answer = request("/v1/chat/completions", body)
        record("tool-smoke", scale=1, answer=answer)
        previous.check_tool(answer)
        assert answer["choices"][0]["finish_reason"] in ("stop", "tool_calls")
        seed = request(
            "/tokenize",
            {
                "model": client.MODEL,
                "prompt": "This is a neutral capacity qualification sentence. " * 6000,
            },
        )["tokens"]
        tokens = (seed * (1000000 // len(seed) + 1))[:1000000]
        for length, scales, output_tokens in CASES:
            samples = []
            errors = []
            stop = threading.Event()

            def sample(stop=stop, samples=samples, errors=errors):
                while not stop.is_set():
                    try:
                        with urllib.request.urlopen(
                            client.BASE + "/metrics", timeout=3
                        ) as response:
                            values = parse_metrics(response.read().decode())
                        samples.append({"at": time.time(), **values})
                    except (OSError, ValueError) as error:
                        errors.append({"at": time.time(), "error": str(error)})
                    stop.wait(0.5)

            monitor = threading.Thread(target=sample, daemon=True)
            monitor.start()
            barrier = threading.Barrier(len(scales), timeout=10)

            def one(scale, length=length, output_tokens=output_tokens, barrier=barrier):
                body = {
                    "model": client.MODEL,
                    "prompt": tokens[:length],
                    "max_tokens": output_tokens,
                    "min_tokens": output_tokens,
                    "ignore_eos": True,
                    "temperature": 0,
                    "seed": 1022,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                    "steering_profile": client.PROFILE,
                    "steering_scale": scale,
                    "cache_salt": uuid.uuid4().hex,
                }
                barrier.wait()
                return {"scale": scale, **request("/v1/completions", body, True)}

            started = time.monotonic()
            results = []
            # Write completed responses and observations even if a peer fails.
            try:
                with concurrent.futures.ThreadPoolExecutor(len(scales)) as pool:
                    futures = [pool.submit(one, scale) for scale in scales]
                    for future in concurrent.futures.as_completed(futures):
                        results.append(future.result())
            finally:
                stop.set()
                monitor.join(4)
                record(
                    "capacity",
                    input_tokens=length,
                    output_tokens=output_tokens,
                    concurrency=len(scales),
                    samples=samples,
                    metric_errors=errors,
                    peak_running=max(
                        (s["num_requests_running"] for s in samples), default=0
                    ),
                    batch_seconds=time.monotonic() - started,
                    results=results,
                )
            check_capacity(report["rows"][-1])
        until = time.monotonic() + 120
        batch = 0
        while time.monotonic() < until:

            def one_short(scale):
                body = client.body("Return only the integer result of 19 + 23.", scale)
                return {"scale": scale, **request("/v1/chat/completions", body, True)}

            with concurrent.futures.ThreadPoolExecutor(4) as pool:
                results = list(pool.map(one_short, [0, 1, 4, 0]))
            record("stability", batch=batch, results=results)
            if any(
                r["text"].strip() != "42" or r["finish_reason"] != "stop"
                for r in results
            ):
                raise ValueError("Stability response differs")
            batch += 1
        report["status"] = "passed"
    except BaseException as error:
        report["status"] = "failed"
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        save()


def require_window():
    context = json.loads(os.environ.get("SPARK_WINDOW_CONTEXT", "{}"))
    manifest = json.loads(Path(context["manifest"]).read_text())
    state = json.loads((Path(context["state_dir"]) / "state.json").read_text())
    hold = json.loads(Path(manifest["hold_path"]).read_text())
    if manifest.get("window_id") != context["window_id"]:
        raise ValueError("Window id differs")
    for record in (state, hold):
        if record.get("window_id") != context["window_id"] or record.get(
            "manifest_sha256"
        ) != digest(manifest):
            raise ValueError("Window ownership or manifest identity differs")
    if (
        manifest.get("runtime_enabled") is not True
        or state.get("phase") != "testing"
        or state.get("last_arm") != "s-loaded"
    ):
        raise ValueError("No active S-loaded test window")
    if time.monotonic() >= state["restore_monotonic"]:
        raise ValueError("Restoration deadline reached")
    expected = json.loads((HERE / "base.json").read_text())["candidate"]
    actual = manifest["services"]["s-loaded"]["identity"]
    if any(
        actual.get(k) != expected[k]
        for k in ("image_id", "profile_sha256", "vllm_tree")
    ):
        raise ValueError("Candidate image or profile differs")
    if (
        manifest["implementation_files"].get(str(Path(__file__).resolve()))
        != hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    ):
        raise ValueError("Request script is not pinned")
    return Path(context["state_dir"]).resolve()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    if not args.execute:
        print((HERE / "plan.json").read_text())
    else:
        directory = require_window()
        if args.output is None or not args.output.resolve().is_relative_to(directory):
            raise ValueError("Output must be a new directory inside this window")
        run(args.output)
