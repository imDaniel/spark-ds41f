#!/usr/bin/env python3
"""One protected candidate qualification window, followed by pinned restoration."""
import argparse
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "2026-10-23-1022-recovery-executor"))
from window_executor import Executor, read, sha


def implementation_paths():
    paths = [HERE / name for name in ("run_window.py", "qualification_requests.py", "base.json", "plan.json")]
    paths += [HERE.parent / "2026-10-23-1022-window" / name
              for name in ("site_adapter.py", "request_screen.py", "cache_screen.py")]
    paths += [HERE.parent / "2026-10-23-1022-scale-validation/scale_requests.py"]
    paths += [HERE.parent / "2026-10-23-1022-recovery-executor" / name
              for name in ("window_executor.py", "command_worker.py")]
    paths += list((HERE.parent / "2026-10-23-1022-output-parser/release-bundle").glob("*"))
    paths.append(Path("/usr/bin/python3"))
    return paths


def component_command(x):
    bundle = HERE.parent / "2026-10-23-1022-output-parser/release-bundle"
    candidate = read(bundle / "candidate.json")
    if candidate["image"] != x.m["services"]["s-loaded"]["identity"]["image_id"]:
        raise ValueError("Component gate image differs from TP4 candidate")
    output = x.directory / "component"
    output.mkdir(exist_ok=False)
    model = "/srv/ai-cluster/models/deepseek-v4.1-flash-1017-heretic-test/official-1bae0e10-steering-v1"
    argv = ["docker", "run", "--rm", "--name", "ds41f-release-" + x.m["window_id"][:8],
            "--label", "spark.window=" + x.m["window_id"], "--label", "spark.role=component",
            "--gpus=all", "--network=none", "--ipc=host", "--ulimit=core=0",
            "--memory=64g", "--memory-swap=64g", "--security-opt=seccomp=unconfined",
            "--ulimit=memlock=-1:-1", "-v", model + ":/models:ro", "-v", str(output) + ":/c"]
    for key, value in candidate["env"].items():
        argv += ["-e", key + "=" + value]
    for source, target in candidate["mounts"]:
        argv += ["-v", str(bundle / source) + ":" + target + ":ro"]
    argv += ["-w", candidate["workdir"], "--entrypoint", "python3", candidate["image"], *candidate["argv"]]
    return argv, output


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
    if any(x.m["implementation_files"].get(str(p)) != sha(p) for p in implementation_paths()):
        raise ValueError("Incomplete or changed implementation pins")
    x.check_files(x.m["implementation_files"])
    x.open()
    try:
        with x.lock():
            x.load_owned()
            x.cluster("retained", "stop")
            data = x.observe()
            if any(x.active(data, name) for name in x.m["services"]):
                raise ValueError("A serving rank is still active before component tests")
            argv, output = component_command(x)
            x.run(argv, str(HERE), x.m["implementation_files"], "release-component-gate", timeout=1500)
            results = [__import__("json").loads(line) for line in
                       (output / "release-tests/output-parser-rc1/results.jsonl").read_text().splitlines()]
            manifest = read(HERE.parent / "2026-10-23-1022-output-parser/release-bundle/manifest.json")
            if {r["file"] for r in results} != set(manifest["files"]):
                raise ValueError("Release gate file coverage incomplete")
            if any(r["status"] != "ok" or r["failed"] or r["error"] for r in results):
                raise ValueError("Release gate contains failed, missing or empty test files")
            critical = [r for r in results if any(k in r["file"] for k in ("steering", "token_provenance", "tokenizers_"))]
            if any(r["skipped"] or not r["passed"] for r in critical):
                raise ValueError("Critical steering/encoder/parser tests skipped")
            x.event("release_component_gate_passed", totals={key: sum(r[key] for r in results)
                    for key in ("passed", "failed", "error", "skipped")})
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
