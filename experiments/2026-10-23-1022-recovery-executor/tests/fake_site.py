#!/usr/bin/env python3
"""CPU-only site fixture. It operates solely on JSON under its own temp root.

Acts both as a bin/spark executable and as observation/ingress/API adapters.
There is no SSH, Docker, network, GPU, or real cluster dependency.
"""
import json
import os
from pathlib import Path
import sys
import time


def main():
    context = json.loads(os.environ["SPARK_WINDOW_CONTEXT"])
    manifest = json.loads(Path(context["manifest"]).read_text())
    root = Path(manifest["fixture_root"]).resolve()
    assert root.name.startswith("spark-window-fixture-")
    assert Path(__file__).resolve().is_relative_to(root)
    world_path = root / "world.json"
    world = json.loads(world_path.read_text())
    args = sys.argv[1:]
    label = args[0] if "--cluster-config" not in args else "spark"
    with (root / "calls.jsonl").open("a") as out:
        out.write(json.dumps({"argv": args, "context": context, "pid": os.getpid()}) + "\n")

    def save():
        temp = root / f"world-{os.getpid()}.tmp"
        temp.write_text(json.dumps(world))
        temp.replace(world_path)

    def fail(name):
        return world.get("faults", {}).get(name)

    if label == "observe":
        world["observed_at"] = time.time() - (100 if fail("stale") else 0)
        if fail("missing_rank"):
            world["nodes"] = world["nodes"][:3]
        print(json.dumps(world))
    elif label in ("block_ingress", "restore_ingress"):
        if fail(label):
            return 7
        world["ingress"]["blocked"] = label == "block_ingress"
        save()
    elif label == "verify_retained":
        print(json.dumps({k: not (fail(k) or
                                 (context.get("recovery_step") == "gateway_after_ingress" and fail("gateway_only"))) for k in
                          ("health", "models", "stock_request", "steered_request", "gateway_aliases")}))
    elif label == "stop_owned_jobs":
        for node in world["nodes"]:
            assert all(j["window_id"] == context["window_id"] for j in node["owned_jobs"])
            if not fail("owned_jobs_stuck"):
                node["owned_jobs"] = []
        save()
    elif label == "spark":
        config = args[args.index("--cluster-config") + 1]
        service_name = next(k for k, v in manifest["services"].items() if v["config"] == config)
        service = manifest["services"][service_name]
        if "doctor" in args:
            print("configuration OK")
            return 9 if fail("doctor") else 0
        action = args[args.index("cluster") + 1]
        assert action in ("start", "stop") and "--change-shape" not in args
        running = any(n["services"][service_name]["running"] for n in world["nodes"])
        print(f"live: {'running' if running else 'stopped'}: 4 nodes, TP4, context 1,048,576, KV 10.5 GiB")
        if action == "start":
            print(f"profile: {config} on " + ", ".join(manifest["nodes"]))
            print("plan: 4 nodes, TP4, context 1,048,576, KV 10.5 GiB")
            print("shape: " + ("CHANGES tp" if fail("bad_plan") else "unchanged"))
        else:
            for node in manifest["nodes"]:
                print(f"{node}: stop exact container {service['container']} and stop its memguard")
        if "--apply" not in args:
            if fail("refused_plan"):
                print("--apply would refuse: fixture refusal")
            return 0
        fault = f"{service_name}:{action}"
        if fail(fault + ":fail"):
            return 8
        for number, node in enumerate(world["nodes"]):
            if fail(fault + ":partial") and number > 1:
                continue
            state = node["services"][service_name]
            state["running"] = action == "start"
            state["guard_active"] = action == "start" or bool(fail(fault + ":guard"))
            state["owned_processes"] = int(action == "start" or bool(fail(fault + ":process")))
        if service_name == "retained":
            world["retained_ready"] = action == "start"
        save()
        if fail(fault + ":hang"):
            print("fixture hanging after partial/complete state write", flush=True)
            time.sleep(120)
    else:
        raise RuntimeError(label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
