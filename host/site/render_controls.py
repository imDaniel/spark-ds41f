#!/usr/bin/env python3
"""Render this site's fixed service controls; never install files or change services."""

import argparse
import hashlib
import json
from pathlib import Path
import runpy
import subprocess


ROOT = Path(__file__).resolve().parents[2]
SERVICE = "deepseek-1017-attention-1022"
OLD_SERVICE = "deepseek-1017-heretic-r6x-test"
BACKEND = "/usr/local/libexec/ai-cluster/services/deepseek-model-control.py"
POLICY = f"/etc/ai-cluster/services/{SERVICE}.json"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def render(existing, output):
    existing, output = Path(existing), Path(output)
    spark = runpy.run_path(str(ROOT / "bin/spark"))
    cluster = spark["read_cluster"]("config/cluster.json", "tp4-ring")
    nodes = json.loads((ROOT / cluster["nodes_config"]).read_text())
    baseline = json.loads((ROOT / "manifests/baselines" / (cluster["promoted_baseline"] + ".json")).read_text())
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    assert not subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip(), "Publish a clean commit first"
    repository = cluster["deployment"]["path"]
    files = {str(p.relative_to(ROOT)) for p in (ROOT / "scripts").glob("*.py")}
    files.update(["bin/spark", "config/cluster.json", "upstreams.lock.json", cluster["nodes_config"], cluster["host"]["kernel_policy"]])
    hashes = {f: digest((ROOT / f).read_bytes()) for f in sorted(files)}
    additional = json.loads(cluster["serve_args"][cluster["serve_args"].index("--additional-config") + 1])["ds41_steering"]
    mounts = {dst: {"source": src.format(home=cluster["host"]["home"]), "rw": mode == "rw"} for src, dst, mode in cluster["container"]["mounts"]}
    for argument in cluster["container"]["docker_run_args"]:
        if argument.startswith("--mount="):
            fields = dict(item.split("=", 1) for item in argument.removeprefix("--mount=").split(",") if "=" in item)
            assert fields["type"] == "bind"
            mounts[fields["target"]] = {"source": fields["source"], "rw": "readonly" not in argument.split(",")}
    profile_mount = next(dst for dst in mounts if additional["profile_path"].startswith(dst + "/"))
    profile = str(Path(mounts[profile_mount]["source"]) / additional["profile_path"][len(profile_mount) + 1:])
    policy = {
        "schema_version": 1, "container": cluster["container"]["name"], "api_port": 8000,
        "model_id": "deepseek-v4.1-flash", "repository": repository,
        "cluster_config": "config/cluster.json", "config_sha256": hashes["config/cluster.json"],
        "revision": revision, "image_id": baseline["image_id"], "repository_files": hashes,
        "profile_path": profile, "profile_sha256": additional["profile_sha256"],
        "container_profile_path": additional["profile_path"], "operator_version": additional["operator_version"],
        "mounts": mounts,
        "ranks": {str(n["rank"] + 1): {"command": spark["expected_command"](cluster, n), "environment": spark["expected_environment"](cluster, n)} for n in nodes["nodes"]},
    }
    rendered = {}

    def put(path, value, mode=0o644):
        data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode() if isinstance(value, dict) else value
        target = output / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(mode)
        rendered[path] = {"sha256": digest(data), "mode": mode}

    def read(path):
        return json.loads((existing / path.lstrip("/")).read_text())

    put(BACKEND, (ROOT / "host/site/deepseek-model-control.py").read_bytes(), 0o755)
    put(POLICY, policy)
    power_path = "/usr/local/libexec/ai-cluster/cluster-shutdown.py"
    put(power_path, (ROOT / "host/site/cluster-shutdown.py").read_bytes(), 0o755)
    power = read("/etc/ai-cluster/cluster-power.json")
    power["pinned_files"] = {f"{repository}/{f}": h for f, h in hashes.items()}
    for name in ["engine-power.py", "engine-control.py", "model-control.py"]:
        path = "/usr/local/libexec/ai-cluster/" + name
        power["pinned_files"][path] = digest((existing / path.lstrip("/")).read_bytes())
    power["pinned_files"][power_path] = rendered[power_path]["sha256"]
    power["purpose"] = "Stop registered TP4 services and verify every rank before worker-then-head poweroff"
    put("/etc/ai-cluster/cluster-power.json", power)
    registry = read("/etc/ai-cluster/engine-control.json")
    old = registry["services"].pop(OLD_SERVICE)
    old["name"] = "DeepSeek 1017 Attention · 1022"
    old["launcher"]["dir"] = "~/sparkdash-launchers/" + SERVICE
    old["backend"].update({action: [BACKEND, action] for action in ["start", "stop", "probe"]})
    old["backend"]["pinned_files"] = {p: rendered[p]["sha256"] for p in [BACKEND, POLICY]}
    registry["services"][SERVICE] = old
    registry["modes"] = {k: v for k, v in registry["modes"].items() if OLD_SERVICE not in v["services"]}
    registry["modes"]["deepseek-1022"] = {"name": old["name"], "services": [SERVICE], "resources": old["resources"]}
    registry["pinned_files"][power_path] = rendered[power_path]["sha256"]
    put("/etc/ai-cluster/engine-control.json", registry)
    put("/etc/sudoers.d/93-sparkdash-deepseek", (f"spark-monitor ALL=(admin) NOPASSWD: /usr/local/sbin/spark-model-control start {SERVICE}, /usr/local/sbin/spark-model-control stop {SERVICE}\n").encode(), 0o440)
    for action in ["start", "stop"]:
        put(f"/var/lib/spark-monitor/sparkdash-launchers/{SERVICE}/{action}.sh", (f"#!/bin/sh\nexec /usr/bin/sudo -n -u admin /usr/local/sbin/spark-model-control {action} {SERVICE}\n").encode(), 0o755)
    launcher_path = "/srv/ai-cluster/config/sparkdash/state/llm-launchers.json"
    launchers = read(launcher_path)
    rows = launchers["launchers"]["spark01"]
    matches = [l for l in rows if l["dir"] == "~/sparkdash-launchers/" + OLD_SERVICE]
    assert len(matches) == 1
    matches[0].update(id=SERVICE, name=old["name"], dir=old["launcher"]["dir"], notes="正式 1022 + attention steering · TP4 · 1M context · C16 · alpha 2.5 · 请求 scale 0–4 · 原网关别名")
    put(launcher_path, launchers)
    manifest = {"revision": revision, "baseline": cluster["promoted_baseline"], "files": rendered,
                "retire_after_verification": [f"/etc/ai-cluster/services/{OLD_SERVICE}.json", "/usr/local/libexec/ai-cluster/services/steering-r6x-model-control.py", "/etc/sudoers.d/93-sparkdash-steering-r6x", f"/var/lib/spark-monitor/sparkdash-launchers/{OLD_SERVICE}"]}
    (output / "install-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--existing-root", required=True, help="root containing current host controls")
    parser.add_argument("--output", required=True, help="new staging directory")
    args = parser.parse_args()
    result = render(args.existing_root, args.output)
    print(json.dumps({"revision": result["revision"], "files": len(result["files"]), "installed": False}))
