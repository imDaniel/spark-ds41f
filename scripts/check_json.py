#!/usr/bin/env python3
"""Validate JSON inputs and verify the bytes of recorded upstream log exceptions."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCEPTIONS = ROOT / ".github/json-evidence-exceptions.json"


def check(root, exceptions):
    problems = []
    files = sorted(path for directory in ("config", "manifests", "experiments")
                   for path in (root / directory).rglob("*.json"))
    found = set()
    for path in files:
        name = path.relative_to(root).as_posix()
        try:
            raw = path.read_bytes()
            if name in exceptions:
                found.add(name)
                entry = exceptions[name]
                if "/receipts/" not in name or not entry.get("reason", "").strip():
                    raise ValueError("exception must describe a recorded receipt")
                if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
                    raise ValueError("recorded receipt differs from its reviewed SHA-256")
            else:
                json.loads(raw)
        except (OSError, ValueError, KeyError) as error:
            problems.append(f"{name}: {error}")
    problems.extend(f"{name}: recorded exception file is missing" for name in sorted(set(exceptions) - found))
    return problems, len(files)


def main():
    exceptions = json.loads(EXCEPTIONS.read_text())
    problems, count = check(ROOT, exceptions)
    for problem in problems:
        print(problem)
    print(f"Checked {count} JSON paths and {len(exceptions)} byte-pinned upstream log receipts")
    return int(bool(problems))


if __name__ == "__main__":
    raise SystemExit(main())
