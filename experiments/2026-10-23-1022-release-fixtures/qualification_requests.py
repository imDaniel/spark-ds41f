#!/usr/bin/env python3
"""Candidate-only release requests. Every generation explicitly names its scale."""
import argparse
import ast
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
import urllib.request
import uuid

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "2026-10-23-1022-window"))
import request_screen as client
import cache_screen
sys.path.insert(0, str(HERE.parent / "2026-10-23-1022-scale-validation"))
import scale_requests as previous

QUALITY = [
    ("arithmetic", "Return only the integer result of 19 + 23."),
    ("sort", "Sort these integers ascending. Return only a JSON array: 8, -3, 5, 0."),
    ("json", 'Return only JSON with exactly these fields: name="Ada", count=3, active=true.'),
    ("code", "Write only a Python function named add(a, b) returning a + b. No example or explanation."),
    ("chinese", "用三句话说明 TCP 重传与应用层重试的区别。"),
]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def request(path, body, stream=False, cancel=False):
    if path in ("/v1/chat/completions", "/v1/completions"):
        if (body.get("steering_profile") != client.PROFILE
                or type(body.get("steering_scale")) not in (int, float)
                or body["steering_scale"] not in (0, 1, 4)):
            raise ValueError("Generation requires the pinned profile and explicit scale 0, 1 or 4")
    elif path != "/tokenize":
        raise ValueError("Unexpected endpoint")
    start = time.monotonic()
    req = urllib.request.Request(client.BASE + path, data=json.dumps(body, ensure_ascii=False).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as response:
        if not stream:
            result = json.load(response)
            if result.get("error") or not result.get("choices") and path != "/tokenize":
                raise ValueError("Invalid non-streaming response")
            return result
        message = {"role": "assistant", "content": "", "reasoning": ""}
        calls = {}; usage = None; finish = None; first = None; done = False; token_ids = []
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
                meaningful = delta.get("content") or delta.get("reasoning") or delta.get("tool_calls")
                if meaningful and first is None:
                    first = time.monotonic()
                if meaningful and cancel:
                    return {"cancelled": True, "ttft": first - start}
                for field in ("content", "reasoning"):
                    message[field] += delta.get(field) or ""
                for call in delta.get("tool_calls") or []:
                    target = calls.setdefault(call["index"], {"id": "", "type": "function",
                                                             "function": {"name": "", "arguments": ""}})
                    if call.get("id"):
                        if target["id"] and target["id"] != call["id"]:
                            raise ValueError("Tool call ID changed during streaming")
                        target["id"] = call["id"]
                    for field in ("name", "arguments"):
                        target["function"][field] += call.get("function", {}).get(field) or ""
                finish = choice.get("finish_reason") or finish
        if not done or first is None or not usage or not finish:
            raise ValueError("Incomplete or empty stream")
    if calls:
        message["tool_calls"] = [calls[i] for i in sorted(calls)]
    elapsed = time.monotonic() - start
    text = message["content"]
    return {"text": text, "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "ttft": first - start, "duration": elapsed, "usage": usage, "finish_reason": finish,
            "message": message, "token_ids": token_ids,
            "decode_tps": max(0, usage["completion_tokens"] - 1) / max(.001, time.monotonic() - first)}


def clean_json(text):
    return json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip()))


def check_quality(kind, result):
    if result["finish_reason"] != "stop":
        raise ValueError("Quality response did not finish: " + kind)
    text = result["text"].strip()
    if kind == "arithmetic":
        assert text == "42"
    elif kind == "sort":
        assert clean_json(text) == [-3, 0, 5, 8]
    elif kind == "json":
        assert clean_json(text) == {"name": "Ada", "count": 3, "active": True}
    elif kind == "code":
        source = re.sub(r"^```(?:python)?\s*|\s*```$", "", text)
        module = ast.parse(source)
        assert len(module.body) == 1 and isinstance(module.body[0], ast.FunctionDef)
        fn = module.body[0]
        assert fn.name == "add" and [v.arg for v in fn.args.args] == ["a", "b"]
        returns = [n for n in fn.body if isinstance(n, ast.Return)]
        assert len(returns) == 1
        assert ast.dump(returns[0].value) == ast.dump(ast.parse("a + b", mode="eval").body)
    elif kind == "chinese":
        # Semantic quality is also reviewed from the preserved complete text.
        assert "TCP" in text and "重传" in text and "重试" in text and len(text) > 30


def run(output):
    output.mkdir(exist_ok=False)
    report = {"status": "running", "rows": []}
    def save():
        (output / "requests.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    def record(kind, **row):
        report["rows"].append({"kind": kind, **row}); save()
    try:
        # Reproduce the failed non-streaming named call first, without changing
        # its prompt, schema, scale or token budget. Token IDs add observability.
        for scale in (1, 4):
            for choice in ("named", "required", "auto"):
                for streaming in (False, True):
                    body = previous.tool_body(scale)
                    body["return_token_ids"] = True
                    if choice != "named":
                        body["tool_choice"] = choice
                    body["stream"] = streaming
                    if streaming:
                        body["stream_options"] = {"include_usage": True}
                    answer = request("/v1/chat/completions", body, streaming)
                    record("tool", scale=scale, choice=choice, stream=streaming, answer=answer)
                    normalized = {"choices": [{"message": answer["message"]}]} if streaming else answer
                    message, call_id = previous.check_tool(normalized)
                    finish = answer["finish_reason"] if streaming else answer["choices"][0]["finish_reason"]
                    assert finish in ("stop", "tool_calls"), "Truncated tool call"
                    if choice == "named" and not streaming:
                        followup = client.body("", scale); followup["max_tokens"] = 256
                        followup["messages"] = body["messages"] + [message,
                            {"role": "tool", "tool_call_id": call_id, "content": '{"recorded":true}'},
                            {"role": "user", "content": "Confirm in one sentence that the value was recorded."}]
                        result = request("/v1/chat/completions", followup, True)
                        record("tool-followup", scale=scale, **result)
                        assert result["finish_reason"] == "stop"
        for cycle, scale in enumerate((0, 1, 4, 0)):
            for kind, prompt in QUALITY:
                body = client.body(prompt, scale); body["max_tokens"] = 512
                result = request("/v1/chat/completions", body, True)
                record("quality", case=kind, cycle=cycle, scale=scale, **result)
                check_quality(kind, result)
        first = [r["text_sha256"] for r in report["rows"] if r.get("cycle") == 0]
        last = [r["text_sha256"] for r in report["rows"] if r.get("cycle") == 3]
        assert first == last, "Return-to-zero changed output"
        original = cache_screen.request
        try:
            cache_screen.request = request
            cache_screen.main(output / "cache.json")
        finally:
            cache_screen.request = original
        # Unique per-request cache salts make each capacity input cold even
        # though controlled repeated token IDs keep the workload reproducible.
        seed = request("/tokenize", {"model": client.MODEL,
            "prompt": "This is a neutral capacity qualification sentence. " * 6000})["tokens"]
        tokens = (seed * (1000000 // len(seed) + 1))[:1000000]
        cases = [(n, [1]) for n in (32768, 131072, 262144, 524288, 1000000)]
        cases += [(8192, [0, 1, 4, 1] * 4), (262144, [0, 1, 4, 1]), (1000000, [1] * 4)]
        for length, scales in cases:
            running = []; stop = threading.Event()
            def sample():
                while not stop.is_set():
                    try:
                        with urllib.request.urlopen(client.BASE + "/metrics", timeout=3) as f:
                            text = f.read().decode()
                        values = re.findall(r'^vllm:num_requests_running(?:\{[^\n]*\})? ([\d.e+]+)$', text, re.M)
                        running.append(max([float(v) for v in values], default=0))
                    except Exception:
                        pass
                    stop.wait(.5)
            monitor = threading.Thread(target=sample, daemon=True); monitor.start()
            def one(scale):
                body = {"model": client.MODEL, "prompt": tokens[:length], "max_tokens": 32,
                    "min_tokens": 32, "ignore_eos": True, "temperature": 0, "seed": 1022,
                    "stream": True, "stream_options": {"include_usage": True},
                    "steering_profile": client.PROFILE, "steering_scale": scale,
                    "cache_salt": uuid.uuid4().hex}
                return {"scale": scale, **request("/v1/completions", body, True)}
            started = time.monotonic()
            try:
                with concurrent.futures.ThreadPoolExecutor(len(scales)) as pool:
                    results = list(pool.map(one, scales))
            finally:
                stop.set(); monitor.join(4)
            record("capacity", input_tokens=length, concurrency=len(scales),
                   peak_running=max(running, default=0), batch_seconds=time.monotonic() - started, results=results)
            assert running, "Missing capacity admission metrics"
            for result in results:
                assert result["usage"]["prompt_tokens"] == length
                assert result["usage"]["completion_tokens"] == 32
                assert result["usage"]["prompt_tokens_details"]["cached_tokens"] == 0
            if len(scales) > 1:
                assert max(running) >= len(scales), "Requested concurrency was not observed"
        until = time.monotonic() + 120; batch = 0
        while time.monotonic() < until:
            scales = [0, 1, 4, 0]
            def one_short(scale):
                body = client.body("Return only the integer result of 19 + 23.", scale)
                return {"scale": scale, **request("/v1/chat/completions", body, True)}
            with concurrent.futures.ThreadPoolExecutor(4) as pool:
                results = list(pool.map(one_short, scales))
            record("stability", batch=batch, results=results)
            for result in results:
                check_quality("arithmetic", result)
            batch += 1
        report["status"] = "passed"
    except BaseException as error:
        report["status"] = "failed"; report["error"] = f"{type(error).__name__}: {error}"
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
        if record.get("window_id") != context["window_id"] or record.get("manifest_sha256") != digest(manifest):
            raise ValueError("Window ownership or manifest identity differs")
    if manifest.get("runtime_enabled") is not True or state.get("phase") != "testing" or state.get("last_arm") != "s-loaded":
        raise ValueError("No active S-loaded test window")
    if time.monotonic() >= state["restore_monotonic"]:
        raise ValueError("Restoration deadline reached")
    expected = json.loads((HERE / "base.json").read_text())["candidate"]
    actual = manifest["services"]["s-loaded"]["identity"]
    if any(actual.get(k) != expected[k] for k in ("image_id", "profile_sha256", "vllm_tree")):
        raise ValueError("Candidate image or profile differs")
    if manifest["implementation_files"].get(str(Path(__file__).resolve())) != hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
        raise ValueError("Request script is not pinned")
    return Path(context["state_dir"]).resolve()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__); p.add_argument("--execute", action="store_true")
    p.add_argument("--output", type=Path); args = p.parse_args()
    if not args.execute:
        print((HERE / "plan.json").read_text())
    else:
        directory = require_window()
        if args.output is None or not args.output.resolve().is_relative_to(directory):
            raise ValueError("Output must be a new directory inside this window")
        run(args.output)
