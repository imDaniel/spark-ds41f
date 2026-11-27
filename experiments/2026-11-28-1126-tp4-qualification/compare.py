"""Compare two candidate engine starts with the immutable site's 1022 receipts."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from spark_ds41f import sonar

CASES = [(1, 1), (1, 4), (4, 1), (1, 4), (4, 4), (1, 4)]
REFERENCE = ROOT / "experiments/2026-10-24-1022-matched-performance/runs/attempt-2/state/performance"


def read_runs(directory):
    directory = Path(directory).resolve()
    progress = json.loads((directory / "progress.json").read_text())
    if progress["status"] != "passed" or [(r["scale"], r["concurrency"]) for r in progress["runs"]] != CASES:
        raise ValueError(f"Incomplete matched workload: {directory}")
    runs, inputs = [], []
    for row in progress["runs"]:
        path = (directory / row["file"]).resolve()
        if path.parent != directory:
            raise ValueError("Receipt path escapes its run directory")
        raw = path.read_bytes()
        run = json.loads(gzip.decompress(raw))
        counts = run["summary"]["requests"]
        if any(counts[k] for k in ("failed", "rejected", "foreign", "approximate_token_times")):
            raise ValueError(f"Contaminated or failed measurement: {path}")
        if run["summary"]["output_tps"]["n"] != 6:
            raise ValueError("Six measured subwindows are required")
        runs.append(run)
        inputs.append({"path": str(path), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    return runs, inputs


def cell(run):
    return (run["workload"]["extra_body"]["steering_scale"], run["workload"]["concurrency"])


def pooled(runs, report, stats):
    records = [r for run in runs for r in run["requests"] if 15 <= r["sent"] < 105]
    latency = report._latency(records, None, None, 90 * len(runs))
    metrics = {"output_tps": statistics.mean(r["summary"]["output_tps"]["mean"] for r in runs)}
    for name, unit in (("ttft_s", 1000), ("tpot_ms", 1), ("e2e_s", 1)):
        prefix = {"ttft_s": "ttft_ms", "tpot_ms": "tpot_ms", "e2e_s": "e2e_s"}[name]
        for percentile in ("mean", "p50", "p90", "p99"):
            metrics[f"{prefix}_{percentile}"] = latency[name][percentile] * unit
    gaps = []
    for record in records:
        events = [at for at, tokens in record["events"] if tokens > 0]
        gaps.extend(1000 * (b - a) for a, b in zip(events, events[1:]))
    gap_stats = stats.percentiles(gaps)
    if not gaps:
        raise ValueError("Missing stream event intervals")
    metrics["sse_event_gap_ms_p99"] = gap_stats["p99"]
    metrics["sse_event_gap_ms_max"] = max(gaps)
    return {"runs": len(runs), "requests": len(records), "metrics": metrics,
            "stream_event_intervals": len(gaps)}


def compare(reference, candidates):
    if len(candidates) != 2 or len({Path(p).resolve() for p in candidates}) != 2:
        raise ValueError("Two distinct candidate engine-start directories are required")
    identity = sonar.revision()
    if not identity["pinned"] or identity["dirty"]:
        raise ValueError("The comparison requires the deployment's exact Sonar pin")
    report, stats = sonar.module("report"), sonar.module("stats")
    old, old_inputs = read_runs(reference)
    new, new_inputs, clients = [], [], []
    for directory in candidates:
        runs, inputs = read_runs(directory)
        client = json.loads((Path(directory) / "sonar.json").read_text())
        if client.get("stream_collector") != "text-chunks" or client.get("revision") != identity["revision"]:
            raise ValueError("Candidate stream timing policy or Sonar pin differs")
        for before, after in zip(old, runs):
            for key, value in before["workload"].items():
                if after["workload"].get(key) != value:
                    raise ValueError(f"Historical workload changed: {key}")
        new.append(runs); new_inputs.append(inputs); clients.append(client)
    rows = []
    for target in ((1, 1), (1, 4), (4, 1), (4, 4)):
        reference_runs = [r for r in old if cell(r) == target]
        boot_runs = [[r for r in boot if cell(r) == target] for boot in new]
        before = pooled(reference_runs, report, stats)
        after = pooled([r for boot in boot_runs for r in boot], report, stats)
        boots = [pooled(boot, report, stats) for boot in boot_runs]
        means = [b["metrics"]["output_tps"] for b in boots]
        rows.append({"scale": target[0], "concurrency": target[1], "reference": before,
                     "candidate": after, "candidate_boots": boots,
                     "candidate_boot_mean_tps": stats.describe(means),
                     "reference_run_tps_intervals": [r["summary"]["output_tps"] for r in reference_runs],
                     "candidate_run_tps_intervals": [r["summary"]["output_tps"] for boot in boot_runs for r in boot],
                     "change_pct": {key: 100 * (value / before["metrics"][key] - 1)
                                    for key, value in after["metrics"].items() if before["metrics"][key]}})
    return {"schema_version": 1, "reference_inputs": old_inputs, "candidate_inputs": new_inputs,
            "comparison_client": identity, "candidate_clients": clients, "comparisons": rows,
            "limits": ["The archived 1022 reference is not rerun or contemporaneously paired.",
                       "Candidate boots are two engine starts with retained JIT caches, not host reboots.",
                       "Per-run intervals use six subwindows; the separate boot-mean interval has only two observations.",
                       "Latency percentiles pool actual requests admitted during measured windows, never run percentiles.",
                       "SSE event intervals are observed stream-chunk gaps, not within-chunk token timestamps.",
                       "The reference used the frozen legacy client; candidates use pinned Sonar with its text-chunks collector."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, default=REFERENCE)
    parser.add_argument("--candidate", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare(args.reference, args.candidate)
    with args.output.open("x") as output:
        output.write(json.dumps(result, indent=2) + "\n")
    print(json.dumps([{"scale": r["scale"], "concurrency": r["concurrency"],
                       "tps_change_pct": r["change_pct"]["output_tps"]} for r in result["comparisons"]]))


if __name__ == "__main__":
    main()
