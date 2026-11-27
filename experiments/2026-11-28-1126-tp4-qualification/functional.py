"""The final 1022 tool, quality and cache gates plus vision on the 1126 service."""
import base64
import json
import struct
import zlib

from qualification_requests import QUALITY, cache_screen, check_quality, client, previous, request


def image_url(rgb):
    def chunk(name, data):
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))
    size = 256
    header = struct.pack(">2I5B", size, size, 8, 2, 0, 0, 0)
    rows = (b"\x00" + bytes(rgb) * size) * size
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(png).decode()


def run(output):
    output.mkdir(exist_ok=False)
    report = {"status": "running", "rows": []}
    def save():
        (output / "requests.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    def record(kind, **row):
        report["rows"].append({"kind": kind, **row})
        save()
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
        scores = []
        for scale in (0, 1, 4, 0):
            body = {"model":client.MODEL, "prompt":"The chemical symbol for silver is",
                    "temperature":0, "seed":2048, "max_tokens":8, "logprobs":5,
                    "steering_profile":client.PROFILE, "steering_scale":scale}
            result = request("/v1/completions", body)
            choice = result["choices"][0]
            first = choice["logprobs"]["top_logprobs"][0]
            if not first:
                raise ValueError("No first-step log probabilities")
            record("dose", scale=scale, text=choice["text"], first_token_logprobs=first)
            scores.append(first)
        if scores[0] != scores[-1] or scores[0] == scores[1] or scores[1] == scores[2]:
            raise ValueError("Scale failed the dose change or return-to-zero logit check")
        for name, rgb, scale in (("red", (235, 0, 0), 1), ("blue", (0, 0, 240), 4)):
            body = client.body("", scale)
            body["max_tokens"] = 256
            body["messages"] = [{"role":"user","content":[
                {"type":"image_url","image_url":{"url":image_url(rgb)}},
                {"type":"text","text":"What is the dominant color in this image? Reply with one English color word."}]}]
            answer = request("/v1/chat/completions", body, True)
            record("vision", expected=name, scale=scale, answer=answer)
            if answer["text"].strip().lower().strip(".*") != name or answer["finish_reason"] != "stop":
                raise ValueError("Incorrect image answer: " + name)
        for field, value in (("steering_scale", 5), ("steering_scale", True), ("steering_profile", "missing-profile")):
            body = client.body("Return only 42.", 1)
            body[field] = value
            for streaming in (False, True):
                rejection = client.expect_rejection(body, streaming)
                record("invalid-request", field=field, value=value, **rejection)
        report["status"] = "passed"
    except BaseException as exc:
        report.update(status="failed", error=str(exc))
        raise
    finally:
        save()
