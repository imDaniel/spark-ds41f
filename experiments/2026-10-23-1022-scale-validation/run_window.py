#!/usr/bin/env python3
"""One candidate boot, explicit-scale requests, then verified pinned restoration."""
import argparse
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "2026-10-23-1022-recovery-executor"))
from window_executor import Executor, read, sha


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", type=Path)
    p.add_argument("--authorization", type=Path)
    p.add_argument("--execute", action="store_true")
    a = p.parse_args()
    if not a.execute:
        print((HERE / "plan.json").read_text()); return
    x = Executor(a.manifest, a.authorization)
    expected = read(HERE / "base.json")["candidate"]
    actual = x.m["services"]["s-loaded"]["identity"]
    if any(actual.get(k) != expected[k] for k in ("image_id", "profile_sha256", "vllm_tree")):
        raise ValueError("Candidate image or profile differs")
    if x.m["window_seconds"] > 2700 or x.m["restore_reserve_seconds"] < 900:
        raise ValueError("Requires a <=45-minute window with >=15-minute restore reserve")
    required = [HERE / p for p in ("run_window.py", "scale_requests.py", "base.json", "plan.json")]
    required += [HERE.parent / "2026-10-23-1022-window" / p
                 for p in ("request_screen.py", "cache_screen.py", "site_adapter.py")]
    required += [HERE.parent / "2026-10-23-1022-recovery-executor" / p
                 for p in ("window_executor.py", "command_worker.py")]
    required.append(Path("/usr/bin/python3"))
    if any(x.m["implementation_files"].get(str(p)) != sha(p) for p in required):
        raise ValueError("Incomplete or drifted implementation pins")
    x.check_files(x.m["implementation_files"])
    x.open()
    try:
        x.switch("s-loaded")
        with x.lock():
            x.load_owned()
            script = HERE / "scale_requests.py"
            x.run(["/usr/bin/python3", str(script), "--execute", "--output", str(x.directory / "scale-requests")],
                  str(HERE), x.m["implementation_files"], "explicit-scale-requests", timeout=1200)
            data = x.observe()
            if not all(n["services"]["s-loaded"]["running"] and n["services"]["s-loaded"]["guard_active"] for n in data["nodes"]):
                raise ValueError("Candidate or memory guard stopped")
            x.event("explicit_scale_validation_complete")
    finally:
        if x.hold.exists() and read(x.state_path)["phase"] != "manual":
            x.recover()
        if read(x.state_path)["phase"] != "closed":
            raise RuntimeError("Window needs manual recovery; retain hold")


if __name__ == "__main__":
    main()
