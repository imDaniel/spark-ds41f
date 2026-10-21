"""Offline profile derivation only: no service, Docker, SSH or Git execution."""
import argparse
import copy
import hashlib
import json
from pathlib import Path


def checked_json(path, expected):
    raw = path.read_bytes()
    if len(expected) != 64 or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError(f"input digest differs: {path.name}")
    return json.loads(raw), raw


def derive(profile, *, checkout, cache, image, tree, baseline, port, master_port, protected_ports):
    p = copy.deepcopy(profile)
    if p.get("shape") != "tp4-ring":
        raise ValueError("expected reviewed TP4 baseline profile")
    args = p["serve_args"]
    if {port, master_port} & set(protected_ports) or port == master_port:
        raise ValueError("ports collide with known protected service ports")
    if not all(1024 <= x <= 65535 for x in (port, master_port)):
        raise ValueError("invalid candidate ports")
    if len(tree) != 40 or any(c not in "0123456789abcdef" for c in tree):
        raise ValueError("invalid source tree")
    if not image.startswith("q03-validation:") or any(c.isspace() for c in image):
        raise ValueError("fresh q03-validation image tag required")
    mounts = p["container"]["mounts"]
    writable = [m for m in mounts if m[2] != "ro"]
    if len(writable) != 1 or writable[0][1:] != ["/cache", "rw"]:
        raise ValueError("unexpected writable source mounts")
    writable[0][0] = str(cache)
    p["container"]["name"] = "q03-validation-isolated"
    p["container"]["image"] = image
    p["container"]["expected_labels"]["local.spark3.vllm.tree"] = tree
    p["deployment"].update(launch_enabled=False, path=str(checkout),
                           repository="https://github.com/imDaniel/spark-ds41f.git",
                           branch="experiment/mhc-input-validation-20261021",
                           ready_url=f"http://127.0.0.1:{port}/v1/models")
    args[args.index("--port") + 1] = str(port)
    p["distributed"]["master_port"] = str(master_port)
    p["environment"]["VLLM_DS41_Q03_MHC_MXFP8"] = "0"
    p["nodes_config"] = "config/q03-window-nodes.local.json"
    p["upstreams_config"] = "experiments/2026-10-21-mhc-input-validation/upstreams.lock.json"
    p["promoted_baseline"] = baseline
    return p


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ("source-profile", "source-map", "checkout", "cache", "independent-root"):
        ap.add_argument("--" + name, type=Path, required=True)
    for name in ("profile-sha256", "map-sha256", "image-tag", "source-tree"):
        ap.add_argument("--" + name, required=True)
    ap.add_argument("--protected-root", type=Path, action="append", required=True)
    ap.add_argument("--protected-port", type=int, action="append", required=True)
    ap.add_argument("--http-port", type=int, required=True)
    ap.add_argument("--master-port", type=int, required=True)
    args = ap.parse_args()
    profile, _ = checked_json(args.source_profile, args.profile_sha256)
    nodes, raw_map = checked_json(args.source_map, args.map_sha256)
    checkout, cache = args.checkout.resolve(), args.cache.resolve()
    approved_root = args.independent_root.resolve()
    if not args.independent_root.is_absolute() or approved_root == Path("/"):
        raise ValueError("reviewed absolute independent root required")
    if not all(path.is_relative_to(approved_root) and path != approved_root for path in (checkout, cache)):
        raise ValueError("candidate paths must remain within the approved independent root")
    protected = [p.resolve() for p in args.protected_root]
    for path in (checkout, cache):
        if any(path == p or path.is_relative_to(p) or p.is_relative_to(path) for p in protected):
            raise ValueError("candidate write path overlaps protected namespace")
    if len(nodes.get("nodes", [])) != 4:
        raise ValueError("expected four-node map")
    # Pins the new source tree to the reviewed deployment's own frozen manifest.
    source = json.loads((checkout / "experiments/2026-10-21-mhc-input-validation/source.json").read_text())
    if source["vllm"]["expected_tree"] != args.source_tree:
        raise ValueError("candidate tree differs from deployment source manifest")
    lock = json.loads((checkout / "experiments/2026-10-21-mhc-input-validation/upstreams.lock.json").read_text())
    baseline = lock["generated_from_baseline"]
    if not isinstance(baseline, str) or not baseline:
        raise ValueError("candidate lock must name its baseline")
    output = checkout / "config/q03-window.local.json"
    output_map = checkout / "config/q03-window-nodes.local.json"
    for p in (output, output_map):
        if p.exists() or p.is_symlink() or p.parent.resolve() != checkout / "config":
            raise ValueError("output must be new and inside independent config directory")
    result = derive(profile, checkout=checkout, cache=cache, image=args.image_tag,
                    tree=args.source_tree, baseline=baseline,
                    port=args.http_port, master_port=args.master_port,
                    protected_ports=args.protected_port)
    # Exclusive writes prevent replacement; an interrupted attempt retains partial evidence.
    with output_map.open("xb") as f:
        f.write(raw_map)
    with output.open("x") as f:
        json.dump(result, f, indent=2)
        f.write("\n")
    print(json.dumps({"profile": str(output), "map": str(output_map),
                      "profile_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                      "map_sha256": hashlib.sha256(raw_map).hexdigest(),
                      "status": "generated only; live identity, collision and publication checks still required"}))


if __name__ == "__main__":
    main()
