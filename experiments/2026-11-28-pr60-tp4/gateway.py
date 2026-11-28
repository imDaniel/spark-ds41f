"""Check production gateway aliases on the client that already owns the test credential."""

import argparse
import hashlib
import json
import math
import os
import re
import stat
import time
import urllib.request
import uuid
from pathlib import Path


BASE = "https://myapi.dlp.life"
ALIASES = ("group/deepseek-v4.1-flash-sec-1.1", "group/deepseek-v4.1-flash-sec-4.0")
CREDENTIALS = Path.home() / "Workspace/HomeLab/ops/cliproxyapi-migration-20261016/private/credentials.json"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def credential(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as handle:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077
                or st.st_uid != os.geteuid() or st.st_size > 65536):
            raise ValueError("Expected an owner-private gateway credentials file")
        token = json.load(handle)["test-1"]
    if not isinstance(token, str) or not token or any(char.isspace() for char in token):
        raise ValueError("Invalid test credential")
    return token


def request(opener, token, path, body=None, request_id=None):
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    if request_id:
        headers["X-Request-Id"] = request_id
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body else None,
                                 headers=headers)
    with opener.open(req, timeout=90) as response:
        if response.status != 200:
            raise RuntimeError(f"Gateway returned {response.status}")
        return json.load(response)


def request_tag(window, challenge, alias):
    # The answer must echo a challenge unique to both this restoration and this alias.
    uuid.UUID(window)
    if not re.fullmatch(r"[a-zA-Z0-9_-]{16,128}", challenge):
        raise ValueError("Invalid gateway challenge")
    return hashlib.sha256(f"{window}/{challenge}/{alias}".encode()).hexdigest()[:16]


def validate_receipt(row, window, challenge, reopened_at):
    if (row.get("passed") is not True or row.get("catalog_only") is not False
            or row.get("window") != window or row.get("challenge") != challenge
            or row.get("base") != BASE or row.get("aliases") != list(ALIASES)):
        raise ValueError("Gateway receipt does not belong to this restoration")
    checked = row["checked_at"]
    if (type(checked) not in (int, float) or not math.isfinite(checked)
            or checked < reopened_at - 10 or checked > time.time() + 10):
        raise ValueError("Gateway receipt timestamp is outside the recovery window")
    rows = row["rows"]
    if not isinstance(rows, list) or len(rows) != len(ALIASES):
        raise ValueError("Gateway receipt is missing an alias")
    ids = set()
    for alias, r in zip(ALIASES, rows):
        tag = request_tag(window, challenge, alias)
        if (r.get("alias") != alias or r.get("response_model") != alias or r.get("status") != 200
                or r.get("finish_reason") != "stop" or r.get("content") != "42"
                or r.get("response_text") != f"42 {tag}" or r.get("request_id") != f"spark-{tag}"
                or not isinstance(r.get("id"), str) or not r["id"] or r["id"] in ids):
            raise ValueError(f"Invalid gateway response for {alias}")
        ids.add(r["id"])
    return True


def verify(key_file, *, catalog_only=False, window=None, challenge=None):
    token = credential(key_file)
    opener = urllib.request.build_opener(NoRedirect())
    catalog = {model["id"] for model in request(opener, token, "/v1/models")["data"]}
    if not set(ALIASES) <= catalog:
        raise RuntimeError("The production gateway does not expose both expected aliases")
    report = {"base": BASE, "aliases": list(ALIASES), "rows": [], "catalog_only": catalog_only}
    if not catalog_only:
        report.update(window=window, challenge=challenge)
        for alias in ALIASES:
            tag = request_tag(window, challenge, alias)
            prompt = (f"What is 19 + 23? Return only the answer, one space and this exact code: {tag}. "
                      f"The single-line format is <integer> {tag}")
            body = {"model": alias, "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0, "seed": 1022, "max_tokens": 128,
                    "reasoning_effort": "none", "stream": False}
            request_id = f"spark-{tag}"
            answer = request(opener, token, "/v1/chat/completions", body, request_id=request_id)
            choice = answer["choices"][0]
            text = choice["message"]["content"].strip()
            report["rows"].append({"alias": alias, "status": 200, "id": answer["id"],
                                   "response_model": answer.get("model"), "response_text": text,
                                   "finish_reason": choice.get("finish_reason"), "request_id": request_id,
                                   "content": text.partition(" ")[0]})
    report.update(passed=True, checked_at=time.time())
    if not catalog_only:
        validate_receipt(report, window, challenge, 0)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", type=Path, default=CREDENTIALS)
    parser.add_argument("--catalog-only", action="store_true")
    parser.add_argument("--window")
    parser.add_argument("--challenge")
    args = parser.parse_args()
    if not args.catalog_only and (not args.window or not args.challenge):
        parser.error("Full verification requires --window and --challenge")
    print(json.dumps(verify(args.key_file, catalog_only=args.catalog_only,
                            window=args.window, challenge=args.challenge)))
