"""Check the maintenance patches through an isolated candidate's real HTTP API."""
import concurrent.futures
import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.request
import uuid


MODEL = "deepseek-v4.1-flash"
PROFILE = "heretic-1017-attn-v1"
CONTROL = "<｜User｜>"
JSON_STRING = '{ "a": 1 }'


def check(condition, message):
    if not condition:
        raise ValueError(message)


def open_request(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        return urllib.request.urlopen(req, timeout=180)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{path} returned HTTP {exc.code}: {exc.read(2048)!r}") from exc


def post(base, path, body):
    with open_request(base, path, body) as response:
        result = json.load(response)
    check(not result.get("error"), f"{path} returned {result.get('error')}")
    return result


def events(response):
    for line in response:
        if not line.startswith(b"data:"):
            continue
        payload = line[5:].strip()
        if payload == b"[DONE]":
            return
        item = json.loads(payload)
        check(not item.get("error"), f"SSE error: {item.get('error')}")
        yield item
    raise ValueError("Incomplete SSE stream")


def tool_history(reordered):
    messages = [{"role": "user", "content": "Check the six warehouse records."}]
    for turn in (1, 2):
        calls, replies = [], []
        for slot in range(3):
            # Distinct keys with repeated literal control spellings stress the
            # result-to-call join independently of the names and result data.
            call_id = f"call-{turn}-{slot}" + (CONTROL if reordered and slot != 1 else "")
            arguments = {
                "arguments": JSON_STRING, "code": "001", "quantity": 3,
                "enabled": False, "nested": {"arguments": JSON_STRING},
                CONTROL: True,
            }
            calls.append({"id": call_id, "type": "function", "function": {
                "name": "read_record", "arguments": json.dumps(arguments, ensure_ascii=False)}})
            replies.append({"role": "tool", "tool_call_id": call_id,
                            "content": f"warehouse-{turn}-{slot} {CONTROL}"})
        messages.append({"role": "assistant", "content": "", "tool_calls": calls})
        order = ((2, 0, 1) if turn == 1 else (1, 2, 0)) if reordered else (0, 1, 2)
        messages.extend(replies[i] for i in order)
        messages.append({"role": "user", "content": "Check the next set." if turn == 1 else "Acknowledge."})
    return messages


def chat_prompt(base, reordered, stream):
    body = {
        "model": MODEL, "messages": tool_history(reordered), "reasoning_effort": "none",
        "steering_profile": PROFILE, "steering_scale": 1, "temperature": 0,
        "max_tokens": 2, "return_token_ids": True, "stream": stream,
    }
    path = "/v1/chat/completions"
    if not stream:
        answer = post(base, path, body)
        ids = answer["prompt_token_ids"]
        usage = answer["usage"]
        finish = answer["choices"][0]["finish_reason"]
    else:
        body["stream_options"] = {"include_usage": True}
        ids, usage, finish = None, None, None
        with open_request(base, path, body) as response:
            for item in events(response):
                sequence = item.get("prompt_token_ids")
                if sequence:
                    check(ids is None or ids == sequence, "Conflicting streamed prompt IDs")
                    ids = sequence
                usage = item.get("usage") or usage
                for choice in item["choices"]:
                    finish = choice.get("finish_reason") or finish
    check(ids and usage and len(ids) == usage["prompt_tokens"] and finish,
          "Missing engine prompt token IDs, finish reason or usage")
    return ids


def check_render(text):
    fragments = [
        f'name="arguments" string="true">{JSON_STRING}</', 'name="code" string="true">001</',
        'name="quantity" string="false">3</', 'name="enabled" string="false">false</',
        f'name="nested" string="false">{json.dumps({"arguments": JSON_STRING})}</',
        f'name="{CONTROL}" string="false">true</',
    ]
    for fragment in fragments:
        check(text.count(fragment) == 6, f"Wrong argument rendering: {fragment!r}")
    views = re.findall(r"<tool_result>(.*?)</tool_result>", text, re.DOTALL)
    expected = [f"warehouse-{turn}-{slot} {CONTROL}" for turn in (1, 2) for slot in range(3)]
    check(views == expected, f"Wrong tool result ordering: {views!r}")


def check_tools(base, record):
    reference = None
    for reordered in (False, True):
        for stream in (False, True):
            ids = chat_prompt(base, reordered, stream)
            text = post(base, "/detokenize", {"model": MODEL, "tokens": ids})["prompt"]
            record("tool-history", stream=stream, reordered=reordered, prompt_ids=ids, rendered=text)
            check_render(text)
            if reference is None:
                reference = ids
            check(ids == reference, "Parallel result arrival order or transport changed engine prompt IDs")


def completion(tokens, scale, salt, length=16):
    body = {"model": MODEL, "prompt": tokens, "temperature": 0, "seed": 1022,
            "min_tokens": length, "max_tokens": length, "ignore_eos": True, "return_token_ids": True}
    if scale is not None:
        body.update(steering_scale=scale, steering_profile=PROFILE)
    if salt is not None:
        body["cache_salt"] = salt
    return body


def complete(base, tokens, scale, salt, barrier=None):
    body = completion(tokens, scale, salt)
    if barrier is not None:
        barrier.wait(timeout=10)
    start = time.monotonic()
    answer = post(base, "/v1/completions", body)
    choice, usage = answer["choices"][0], answer["usage"]
    ids = choice["token_ids"]
    check(choice["finish_reason"] == "length" and usage["prompt_tokens"] == len(tokens)
          and len(ids) == usage["completion_tokens"] == body["max_tokens"],
          "Incomplete generated token IDs or prompt usage")
    cached = usage["prompt_tokens_details"]["cached_tokens"]
    return {"scale": scale, "salt": salt, "cached": cached, "token_ids": ids,
            "started": start, "ended": time.monotonic()}


def wait_requests(base, running, seconds):
    deadline = time.monotonic() + seconds
    while True:
        with urllib.request.urlopen(base + "/metrics", timeout=5) as response:
            text = response.read().decode()
        counts = {}
        for name in ("num_requests_running", "num_requests_waiting"):
            values = re.findall(r"^vllm:" + name + r"(?:\{[^\n]*\})? ([\d.e+-]+)$", text, re.MULTILINE)
            check(values, f"Missing {name} metric")
            counts[name] = sum(map(float, values))
        if running and counts["num_requests_running"] >= 1 or not running and not any(counts.values()):
            return counts
        check(time.monotonic() < deadline, f"Engine did not reach expected request count: {counts}")
        time.sleep(0.25)


def cancel_and_drain(base, tokens, salt, record):
    # A generation this long cannot naturally finish in the drain interval.
    # Observe it running after receiving real tokens, disconnect, and verify
    # its scheduler slot disappears before any subsequent request is sent.
    body = completion(tokens, 1, salt, length=16384)
    body.update(stream=True, stream_options={"include_usage": True})
    wait_requests(base, False, 20)
    with open_request(base, "/v1/completions", body) as response:
        for item in events(response):
            first = next((c for c in item["choices"] if c.get("token_ids")), None)
            if first:
                check(not first.get("finish_reason"), "Generation ended before cancellation")
                record("cancelling", id=item["id"], first_ids=first["token_ids"],
                       counts=wait_requests(base, True, 12))
                break
        else:
            raise ValueError("Cancellation stream produced no token IDs")
    record("cancel-drained", counts=wait_requests(base, False, 30))


def check_cache(base, record):
    nonce = uuid.uuid4().hex
    text = nonce + "\n" + "This is a neutral prefix cache isolation measurement sentence. " * 6000
    tokens = post(base, "/tokenize", {"model": MODEL, "prompt": text})["tokens"][:32768]
    check(len(tokens) == 32768, "Insufficient cache probe tokens")
    record("cache-input", nonce=nonce, n=len(tokens), sha256=hashlib.sha256(json.dumps(tokens).encode()).hexdigest())
    salt_a, salt_b = "pr60-a-" + nonce, "pr60-b-" + nonce
    reference = {}

    def verify(row, cold):
        check(row["cached"] == 0 if cold else row["cached"] >= 8192,
              f"Unexpected cached token count: {row}")
        # Changing caller salt partitions physical KV reuse, while the model
        # sees the same inputs. Stock and explicit scale zero share the key.
        canonical = row["scale"] or 0
        expected = reference.setdefault(canonical, row["token_ids"])
        check(row["token_ids"] == expected, f"Generated IDs changed for scale {canonical}: {row}")

    for salt in (None, salt_a, salt_b):
        for scale in (0, 1, 4):
            for cold in (True, False):
                row = complete(base, tokens, scale, salt)
                record("sequential", cold=cold, **row)
                verify(row, cold)
    # One functional equivalence check uses the default selection. Every timed
    # workload is separate and explicitly names its scale.
    row = complete(base, tokens, None, salt_a)
    record("default-stock", **row)
    verify(row, False)
    barrier = threading.Barrier(4)
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        futures = [pool.submit(complete, base, tokens, scale, salt, barrier)
                   for scale, salt in ((0, None), (1, salt_a), (4, salt_b), (1, salt_b))]
        rows = [future.result() for future in futures]
    for row in rows:
        record("mixed-concurrency", **row)
        verify(row, False)
    check(max(r["started"] for r in rows) < min(r["ended"] for r in rows), "Four requests did not overlap")
    cancel_and_drain(base, tokens, salt_a, record)
    for scale, salt in ((1, salt_a), (0, salt_a), (4, salt_b)):
        row = complete(base, tokens, scale, salt)
        record("after-cancel", **row)
        verify(row, False)


def run(output, base="http://127.0.0.1:8000"):
    output.mkdir(exist_ok=False)
    report = {"status": "running", "rows": []}
    def save():
        (output / "targeted.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    def record(kind, **row):
        report["rows"].append({"kind": kind, **row})
        save()
    try:
        check_tools(base, record)
        check_cache(base, record)
        report["status"] = "passed"
    except Exception as exc:
        report.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        save()
