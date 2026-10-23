#!/usr/bin/env python3
"""Short request-only validation; every generation request carries an explicit scale."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
PREVIOUS = HERE.parent / "2026-10-23-1022-window"
sys.path.insert(0, str(PREVIOUS))
import request_screen as client
import cache_screen

PROMPTS = ["Return only the integer result of 19 + 23.",
           "Write a Python function named add(a, b) that returns their sum.",
           "用三句话说明 TCP 重传与应用层重试的区别。"]
TOOL_VALUE = {"role": "literal user value", "type": "literal string value",
              "nested": {"quote\"key": "<｜end▁of▁sentence｜>"}}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def guarded_request(path, body, stream=False, cancel=False):
    if path in ("/v1/chat/completions", "/v1/completions"):
        if body.get("steering_profile") != client.PROFILE or type(body.get("steering_scale")) not in (int, float):
            raise ValueError("Every generation request must explicitly name profile and numeric scale")
        if body["steering_scale"] not in (0, 1, 4):
            raise ValueError("This short plan permits only scales 0, 1 and 4")
    elif path != "/tokenize":
        raise ValueError("Unexpected request path")
    return client.request(path, body, stream, cancel)


def tool_body(scale):
    body = client.body("Call record_value with exactly this JSON value: " +
                       json.dumps(TOOL_VALUE, ensure_ascii=False), scale)
    body["stream"] = False
    body.pop("stream_options")
    body["max_tokens"] = 256
    body["tools"] = [{"type": "function", "function": {"name": "record_value",
        "description": "Record the exact literal fields supplied by the user.",
        "parameters": {"type": "object", "properties": {
            "role": {"type": "string"}, "type": {"type": "string"},
            "nested": {"type": "object", "properties": {"quote\"key": {"type": "string"}},
                       "required": ["quote\"key"], "additionalProperties": False}},
            "required": ["role", "type", "nested"], "additionalProperties": False}}}]
    body["tool_choice"] = {"type": "function", "function": {"name": "record_value"}}
    return body


def check_tool(answer):
    message = answer["choices"][0]["message"]
    calls = message.get("tool_calls", [])
    if len(calls) != 1 or calls[0]["function"]["name"] != "record_value":
        raise ValueError("Expected exactly one record_value call")
    if json.loads(calls[0]["function"]["arguments"]) != TOOL_VALUE:
        raise ValueError("Tool argument spelling or value changed")
    if not calls[0].get("id"):
        raise ValueError("Missing tool call id")
    return message, calls[0]["id"]


def run(output):
    output = Path(output)
    output.mkdir(exist_ok=False)
    report = {"status": "running", "rows": []}

    def save():
        (output / "requests.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    try:
        for cycle, scale in enumerate((0, 1, 4, 0)):
            for index, prompt in enumerate(PROMPTS):
                result = guarded_request("/v1/chat/completions", client.body(prompt, scale), True)
                report["rows"].append({"kind": "functional", "cycle": cycle, "scale": scale,
                                       "prompt": index, **result})
                save()
                if index == 0 and result["text"].strip() != "42":
                    raise ValueError("Arithmetic quality check failed")
        first = [r["text_sha256"] for r in report["rows"] if r["cycle"] == 0]
        last = [r["text_sha256"] for r in report["rows"] if r["cycle"] == 3]
        if first != last:
            raise ValueError("Scale zero changed after active scales")
        for scale in (1, 4):
            body = tool_body(scale)
            answer = guarded_request("/v1/chat/completions", body)
            # Keep the failing answer before checking it.
            report["rows"].append({"kind": "tool-call", "scale": scale, "answer": answer}); save()
            message, call_id = check_tool(answer)
            followup = client.body("", scale)
            followup["messages"] = body["messages"] + [message,
                {"role": "tool", "tool_call_id": call_id, "content": '{"recorded":true}'},
                {"role": "user", "content": "Confirm in one sentence that the value was recorded."}]
            result = guarded_request("/v1/chat/completions", followup, True)
            report["rows"].append({"kind": "tool-followup", "scale": scale, **result}); save()
        # The previously hardware-validated cache harness already explicitly
        # names scale on all 13 generations, including cancel and return-to-zero.
        original = cache_screen.request
        try:
            cache_screen.request = guarded_request
            cache_screen.main(output / "cache.json")
        finally:
            cache_screen.request = original
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
    pinned = manifest["implementation_files"].get(str(Path(__file__).resolve()))
    if pinned != hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
        raise ValueError("Request script is not pinned")
    return Path(context["state_dir"]).resolve()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.execute:
        print((HERE / "plan.json").read_text()); return
    state_dir = require_window()
    if args.output is None or not args.output.resolve().is_relative_to(state_dir):
        raise ValueError("Output must be a new directory inside this owned window")
    run(args.output)


if __name__ == "__main__":
    main()
