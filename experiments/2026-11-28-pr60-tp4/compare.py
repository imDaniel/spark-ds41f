"""Compare two independent candidate TP4 starts with the two promoted 1126 starts."""
import argparse
import gzip
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "manifests/benchmarks/2026-11-28-homelab-1126-attention-tp4.json"
HISTORICAL = ROOT / "experiments/2026-11-28-1126-tp4-qualification/compare.py"
sys.path.insert(0, str(ROOT))
from spark_ds41f import sonar

# Use the same time boundaries, token counting, tail pooling and raw-file reader
# that produced the published 1126 reference.
spec = importlib.util.spec_from_file_location("accepted_1126_compare", HISTORICAL)
historical = importlib.util.module_from_spec(spec)
spec.loader.exec_module(historical)
RANKS = {f"spark{i:02}" for i in range(1, 5)}


def read_cohort(directory, pin):
    runs, inputs = historical.read_runs(directory)
    client = json.loads((directory / "sonar.json").read_text())
    if any(client.get(key) != value for key, value in pin.items()) or client.get("stream_collector") != "text-chunks":
        raise ValueError(f"Unexpected Sonar pin or collector in {directory}")
    workloads = []
    for run, source, target in zip(runs, inputs, historical.CASES, strict=True):
        if historical.cell(run) != target:
            raise ValueError(f"Receipt workload differs from the six-case order: {source['path']}")
        temperatures = run["thermal_start"]
        if (len(temperatures) != 4 or {r["node"] for r in temperatures} != RANKS
                or not all(0 <= r["hottest_c"] < 55 for r in temperatures)):
            raise ValueError(f"Unqualified temperature before {source['path']}")
        hit = run["summary"]["server"]["prefix_cache_hit_rate"]["mean"]
        if not hit >= 0.9 or not run["requests"] or any(
            r["error"] or r["completion_tokens"] != 256 or r["finish_reason"] != "length" for r in run["requests"]
        ):
            raise ValueError(f"Incomplete output or prefix-cache target missed: {source['path']}")
        label = Path(source["path"]).name.removesuffix(".json.gz")
        with gzip.open(directory / f"{label}-documents.json.gz", "rt") as stream:
            docs = json.load(stream)
        # A fresh cache salt for each document is part of cold priming. The text,
        # slot number and exact token length must still match between cohorts.
        contents = [{k: doc[k] for k in ("slot", "text", "tokens")} for doc in docs]
        if len(contents) != target[1]:
            raise ValueError(f"Missing concurrent documents in {source['path']}")
        doc_digest = hashlib.sha256(json.dumps(contents, sort_keys=True).encode()).hexdigest()
        workloads.append((run["workload"], doc_digest))
    return {"runs": runs, "inputs": inputs, "workloads": workloads, "client": client}


def independent_starts(directories, reference_image):
    if len(directories) != 2 or len(set(directories)) != 2:
        raise ValueError("Two distinct candidate result directories are required")
    starts = [json.loads((d / "engine-start.json").read_text()) for d in directories]
    a, b = starts
    for key, pattern in (("deployment_commit", "[0-9a-f]{40}"), ("vllm_tree", "[0-9a-f]{40}"),
                         ("profile_sha256", "[0-9a-f]{64}"), ("image_id", "sha256:[0-9a-f]{64}")):
        if not re.fullmatch(pattern, a[key]) or b[key] != a[key]:
            raise ValueError(f"Candidate starts have invalid or different {key}")
    if a["image_id"] == reference_image:
        raise ValueError("Candidate measurements use the reference image")
    if any(set(start["ranks"]) != RANKS for start in starts):
        raise ValueError("Missing candidate rank identity")
    ids = set()
    for start in starts:
        for name, rank in start["ranks"].items():
            container = rank["container_id"]
            if not re.fullmatch("[0-9a-f]{64}", container) or container in ids:
                raise ValueError(f"Invalid or reused container for {name}")
            ids.add(container)
            if not rank["started_at"] or a["ranks"][name]["started_at"] == b["ranks"][name]["started_at"]:
                raise ValueError(f"Missing independent engine start for {name}")
    return starts


def pct(before, after):
    return 100 * (after / before - 1)


def metric_comparison(key, reference, candidate, stats):
    old = [b["metrics"][key] for b in reference]
    new = [b["metrics"][key] for b in candidate]
    pairings = [{"reference_boot": i, "candidate_boot": j, "change_pct": pct(before, after)}
                for i, before in enumerate(old, 1) for j, after in enumerate(new, 1)]
    old_spread, new_spread = abs(pct(*old)), abs(pct(*new))
    deltas = [p["change_pct"] for p in pairings]
    margin = max(old_spread, new_spread)
    beyond_repeats = all(d > margin for d in deltas) or all(d < -margin for d in deltas)
    interval = stats.welch(old, new)
    higher_is_better = key in ("output_tps", "per_user_decode_tps")
    verdict = stats.verdict(interval["change_pct"], interval["ci95_pct"], higher_is_better, 0)
    if not beyond_repeats or verdict == "same":
        verdict = "no-proven-change"
    return {"boot_pairings": pairings, "reference_repeat_pct": old_spread,
            "candidate_repeat_pct": new_spread, "all_pairs_beyond_both_repeats": beyond_repeats,
            "boot_mean_welch": interval, "verdict": verdict}


def cell_summary(cohorts, target, report, stats):
    cells = [[r for r in cohort["runs"] if historical.cell(r) == target] for cohort in cohorts]
    all_runs = [r for boot in cells for r in boot]
    return {"pooled": historical.pooled(all_runs, report, stats),
            "boots": [historical.pooled(boot, report, stats) for boot in cells],
            "run_tps_intervals": [[r["summary"]["output_tps"] for r in boot] for boot in cells]}


def compare(directories):
    manifest = json.loads(REFERENCE.read_text())
    reference_dirs = [ROOT / p for p in manifest["native_receipts"]]
    pin = manifest["identity"]["sonar"]
    if sonar.revision() != pin:
        raise ValueError("The comparison requires the exact clean Sonar revision used by 1126")
    starts = independent_starts(directories, manifest["identity"]["image_id"])
    old = [read_cohort(d, pin) for d in reference_dirs]
    new = [read_cohort(d, pin) for d in directories]
    accepted_inputs = manifest["comparison"]["candidate_inputs"]
    for cohort, accepted in zip(old, accepted_inputs, strict=True):
        for raw, pinned in zip(cohort["inputs"], accepted, strict=True):
            if any(raw[k] != pinned[k] for k in ("bytes", "sha256")):
                raise ValueError(f"Recorded 1126 measurement changed: {raw['path']}")
    all_cohorts = old + new
    if any(c["workloads"] != old[0]["workloads"] for c in all_cohorts):
        raise ValueError("Timed workload, prompt digest or source document changed")
    hashes = [r["sha256"] for c in all_cohorts for r in c["inputs"]]
    if len(set(hashes)) != len(hashes):
        raise ValueError("A raw timed receipt was reused across starts")
    report, stats = sonar.module("report"), sonar.module("stats")
    rows = []
    for archived in manifest["comparison"]["comparisons"]:
        target = archived["scale"], archived["concurrency"]
        before = cell_summary(old, target, report, stats)
        after = cell_summary(new, target, report, stats)
        if before["pooled"] != archived["candidate"]:
            raise ValueError(f"1126 pooling did not reproduce accepted cell {target}")
        base, result = before["pooled"]["metrics"], after["pooled"]["metrics"]
        rows.append({"scale": target[0], "concurrency": target[1], "reference": before, "candidate": after,
                     "change_pct": {k: pct(base[k], v) for k, v in result.items()},
                     "boot_comparison": {k: metric_comparison(k, before["boots"], after["boots"], stats)
                                         for k in result}})
    return {"schema": "matched-1126-tp4-comparison/1", "reference": str(REFERENCE.relative_to(ROOT)),
            "client": pin, "engine_starts": starts, "raw_inputs": [c["inputs"] for c in all_cohorts],
            "comparisons": rows, "limits": [
                "Each arm has two independent engine starts with retained JIT caches.",
                "Throughput averages six-subwindow run means; latency percentiles pool requests rather than percentiles.",
                "Boot contrasts use each start's metric, including its own tail percentile. All four cross-boot pairs and two-boot Welch intervals are descriptive of these starts.",
                "Directional verdicts require all four contrasts beyond both observed repeat margins and the two-boot Welch interval excluding zero.",
                "SSE event gaps time visible chunks; individual token emission times inside a chunk are unavailable.",
            ]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True, action="append")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare([p.resolve() for p in args.candidate])
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps([{"scale": r["scale"], "concurrency": r["concurrency"],
                       "output_tps_change_pct": r["change_pct"]["output_tps"],
                       "verdict": r["boot_comparison"]["output_tps"]["verdict"]} for r in result["comparisons"]]))
