"""Verify explicit-scale routes before handing the candidate to its owner."""

import json
import os
import sys
import urllib.request
from pathlib import Path


def main(mode):
    context = json.loads(os.environ["SPARK_WINDOW_CONTEXT"])
    m = json.loads(Path(context["manifest"]).read_text())
    state = json.loads((Path(context["state_dir"]) / "state.json").read_text())
    if state["window_id"] != context["window_id"] or state["phase"] not in (
        "testing",
        "trial_handoff",
    ):
        raise ValueError("No owner-trial handoff window")
    gateway = mode == "gateway"
    if mode not in ("private", "gateway"):
        raise ValueError("Unknown trial probe")
    base = m["gateway_base"] if gateway else "http://127.0.0.1:8000"
    headers = {"Content-Type": "application/json"}
    if gateway:
        headers["Authorization"] = (
            "Bearer " + Path(m["gateway_key_file"]).read_text().strip()
        )
    rows = []
    for scale in (1, 4):
        model = (
            f"DeepSeek-V4.1-Flash-heretic-{scale}" if gateway else "deepseek-v4.1-flash"
        )
        if gateway and model not in m["gateway_model_ids"]:
            raise ValueError("Required existing alias missing")
        body = {
            "model": model,
            "messages": [{"role": "user", "content": "Reply with OK."}],
            "temperature": 0,
            "reasoning_effort": "none",
            "max_tokens": 32,
            "steering_profile": "heretic-1017-attn-v1",
            "steering_scale": scale,
        }
        req = urllib.request.Request(
            base + "/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=120) as response:
            answer = json.load(response)
        choice = answer["choices"][0]
        if not choice["message"].get("content") or choice["finish_reason"] != "stop":
            raise ValueError("Trial route returned empty or truncated output")
        rows.append({"scale": scale, "model": model, "answer": answer})
    out = Path(context["state_dir"]) / ("owner-trial-" + mode + ".json")
    out.write_text(
        json.dumps({"passed": True, "rows": rows}, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps({"mode": mode, "passed": True, "scales": [1, 4]}))


if __name__ == "__main__":
    main(sys.argv[1])
