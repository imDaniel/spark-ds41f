#!/usr/bin/env python3
"""Qualify the parser-only successor, then restore the pinned four-rank service."""
import argparse
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent / "2026-10-23-1022-recovery-executor"))
from window_executor import Executor, read, sha

HOST_EXECUTABLES = ("/usr/bin/python3", "/usr/bin/docker")
ALLOWED_SOURCE_CHANGES = {
    "tests/parser/engine/test_token_id_scanner.py",
    "vllm/parser/engine/token_id_scanner.py",
}


def implementation_paths():
    paths = [HERE / name for name in (
        "run_window.py", "qualification_requests.py", "base.json", "plan.json",
        "component-reuse.json", "source.json", "image.json", "release-tests.json",
    )]
    paths += [HERE.parent / "2026-10-23-1022-window" / name
              for name in ("site_adapter.py", "request_screen.py", "cache_screen.py")]
    paths += [HERE.parent / "2026-10-23-1022-scale-validation/scale_requests.py"]
    paths += [HERE.parent / "2026-10-23-1022-recovery-executor" / name
              for name in ("window_executor.py", "command_worker.py")]
    paths += [ROOT / read(HERE / "component-reuse.json")["receipt"]]
    paths += [HERE / "runs/exact-image-parser.xml"]
    return paths + [Path(p) for p in HOST_EXECUTABLES]


def validate_implementation(x):
    if any(x.m["implementation_files"].get(str(p)) != sha(p) for p in implementation_paths()):
        raise ValueError("Incomplete or changed implementation pins")
    x.check_files(x.m["implementation_files"])


def validate_component_reuse():
    import json
    import xml.etree.ElementTree as ET

    receipt = read(HERE / "component-reuse.json")
    image = read(HERE / "image.json")
    source = read(HERE / "source.json")["vllm"]
    if (set(receipt["source_changes"]) != ALLOWED_SOURCE_CHANGES
            or set(image["changed_source_files"]) != ALLOWED_SOURCE_CHANGES
            or image["native_inputs_changed"] is not False
            or receipt["native_inputs_changed"] is not False
            or receipt["candidate_vllm_head"] != source["patch_head"]
            or image["source_head"] != source["patch_head"]
            or image["source_tree"] != source["expected_tree"]
            or image["parent_image_id"] != receipt["previous_image_id"]):
        raise ValueError("GPU receipt reuse exceeds the verified parser-only scope")
    path = ROOT / receipt["receipt"]
    if sha(path) != receipt["sha256"]:
        raise ValueError("Component receipt changed")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    new_parser_file = "tests/parser/engine/test_token_id_scanner.py"
    if set(read(HERE / "release-tests.json")["gate"]) != {row["file"] for row in rows} | {new_parser_file}:
        raise ValueError("Current release gate coverage differs from the receipts")
    totals = {key: sum(row[key] for row in rows) for key in ("passed", "failed", "error", "skipped")}
    if (len({row["file"] for row in rows}) != receipt["file_count"] or len(rows) != 73
            or totals != receipt["totals"] or totals["failed"] or totals["error"]
            or any(row["status"] != "ok" for row in rows)):
        raise ValueError("Incomplete or failed component receipt")
    parser = HERE / "runs/exact-image-parser.xml"
    if sha(parser) != image["parser_cpu_sha256"]:
        raise ValueError("Exact-image parser receipt changed")
    cases = ET.parse(parser).findall(".//testcase")
    if len(cases) != 4379 or any(c.find(tag) is not None for c in cases
                              for tag in ("failure", "error", "skipped")):
        raise ValueError("Parser gate failed or skipped cases")
    if sum("test_token_id_scanner" in c.get("classname", "") for c in cases) != 40:
        raise ValueError("Byte-token scanner coverage is incomplete")
    if image["image_id"] != read(HERE / "base.json")["candidate"]["image_id"]:
        raise ValueError("Parser receipt image differs from candidate")
    return totals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path); parser.add_argument("--authorization", type=Path)
    parser.add_argument("--execute", action="store_true"); args = parser.parse_args()
    if not args.execute:
        print((HERE / "plan.json").read_text()); return
    x = Executor(args.manifest, args.authorization)
    expected = read(HERE / "base.json")["candidate"]
    actual = x.m["services"]["s-loaded"]["identity"]
    if any(actual.get(k) != expected[k] for k in ("image_id", "profile_sha256", "vllm_tree")):
        raise ValueError("Candidate image/profile differs")
    if x.m["window_seconds"] > 4500 or x.m["restore_reserve_seconds"] < 900:
        raise ValueError("Requires <=75 minutes with >=15-minute recovery reserve")
    validate_implementation(x)
    totals = validate_component_reuse()
    x.open()
    try:
        x.event("release_component_receipt_reused", totals=totals)
        x.switch("s-loaded")
        with x.lock():
            x.load_owned()
            x.run(["/usr/bin/python3", str(HERE / "qualification_requests.py"), "--execute",
                   "--output", str(x.directory / "requests")], str(HERE), x.m["implementation_files"],
                  "release-explicit-scale-requests", timeout=2700)
            data = x.observe()
            if not all(n["services"]["s-loaded"]["running"] and n["services"]["s-loaded"]["guard_active"] for n in data["nodes"]):
                raise ValueError("Candidate rank or memory guard stopped")
            x.event("release_explicit_scale_requests_passed")
    finally:
        if x.hold.exists() and read(x.state_path)["phase"] != "manual":
            x.recover()
        if read(x.state_path)["phase"] != "closed":
            raise RuntimeError("Manual recovery required; retain hold")


if __name__ == "__main__":
    main()
