"""Recompute matched historical comparison from complete native receipts."""

import argparse
import gzip
import json
import pathlib
import statistics
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--receipts", type=pathlib.Path, required=True)
parser.add_argument("--output", type=pathlib.Path, required=True)
args = parser.parse_args()
exp = pathlib.Path(__file__).resolve().parent
root = exp.parent.parent
folder = args.receipts
sys.path.insert(0, str(exp / "measurement"))
from servebench import report

history = json.loads((exp / "history.json").read_text())
progress = json.loads((folder / "progress.json").read_text())
assert progress["status"] == "passed" and len(progress["runs"]) == 6
old = [
    (r, json.loads(gzip.decompress((root / r["path"]).read_bytes())))
    for r in history["runs"]
]
new = [
    json.loads(gzip.decompress((folder / r["file"]).read_bytes()))
    for r in progress["runs"]
]


def metrics(v):
    s = v["summary"]
    l = s["latency"]
    return {
        "tps": s["output_tps"]["mean"],
        "per_user_tps": l["per_user_tps"]["mean"],
        "ttft_mean_ms": l["ttft_s"]["mean"] * 1000,
        "ttft_p50_ms": l["ttft_s"]["p50"] * 1000,
        "ttft_p90_ms": l["ttft_s"]["p90"] * 1000,
        "ttft_p99_ms": l["ttft_s"]["p99"] * 1000,
        "tpot_mean_ms": l["tpot_ms"]["mean"],
        "tpot_p99_ms": l["tpot_ms"]["p99"],
        "e2e_mean_s": l["e2e_s"]["mean"],
        "e2e_p90_s": l["e2e_s"]["p90"],
        "e2e_p99_s": l["e2e_s"]["p99"],
        "prefix_hit_rate": s["server"]["prefix_cache_hit_rate"]["mean"],
        "steps_per_s": s["server"]["steps_per_s"]["mean"],
        "tokens_per_step": s["server"]["output_tokens_per_step"]["mean"],
        "draft_acceptance": s["server"]["accepted_per_draft"]["mean"],
    }


def changes(a, b):
    return {k: 100 * (b[k] / v - 1) for k, v in a.items() if v}


rows = []
for scale, c in [(1, 1), (1, 4), (4, 1), (4, 4)]:
    o = next(
        v
        for r, v in old
        if r["kind"] == "initial" and r["scale"] == scale and r["concurrency"] == c
    )
    n = next(
        v
        for v in new
        if v["workload"]["extra_body"]["steering_scale"] == scale
        and v["workload"]["concurrency"] == c
    )
    a, b = metrics(o), metrics(n)
    rows.append(
        {
            "scale": scale,
            "concurrency": c,
            "old": a,
            "new": b,
            "change_pct": changes(a, b),
            "old_tps_subwindow_sd": o["summary"]["output_tps"]["sd"],
            "new_tps_subwindow_sd": n["summary"]["output_tps"]["sd"],
        }
    )


def pooled(runs):
    records = [r for v in runs for r in v["requests"] if 15 <= r["sent"] < 105]
    l = report._latency(records, None, None, 90 * len(runs))
    return {
        "run_count": len(runs),
        "requests": len(records),
        "tps": statistics.mean(v["summary"]["output_tps"]["mean"] for v in runs),
        "per_user_tps": l["per_user_tps"]["mean"],
        "tpot_mean_ms": l["tpot_ms"]["mean"],
        "e2e_mean_s": l["e2e_s"]["mean"],
        "e2e_p90_s": l["e2e_s"]["p90"],
        "ttft_mean_ms": l["ttft_s"]["mean"] * 1000,
        "ttft_p50_ms": l["ttft_s"]["p50"] * 1000,
        "ttft_p90_ms": l["ttft_s"]["p90"] * 1000,
        "ttft_p99_ms": l["ttft_s"]["p99"] * 1000,
        "tpot_p99_ms": l["tpot_ms"]["p99"],
        "e2e_p99_s": l["e2e_s"]["p99"],
    }


a = pooled([v for r, v in old if r["kind"] == "daily-followup"])
b = pooled(
    [
        v
        for v in new
        if v["workload"]["extra_body"]["steering_scale"] == 1
        and v["workload"]["concurrency"] == 4
    ]
)
comparisons = {
    "initial_comparisons": rows,
    "daily_c4_scale1": {
        "old": a,
        "new": b,
        "change_pct": {
            k: 100 * (b[k] / a[k] - 1)
            for k in (
                "tps",
                "per_user_tps",
                "tpot_mean_ms",
                "e2e_mean_s",
                "e2e_p90_s",
                "ttft_mean_ms",
                "ttft_p50_ms",
                "ttft_p90_ms",
                "ttft_p99_ms",
                "tpot_p99_ms",
                "e2e_p99_s",
            )
        },
    },
    "all_new_runs": [
        {
            "label": v["label"],
            "metrics": metrics(v),
            "requests": v["summary"]["requests"],
            "tps_subwindows": v["summary"]["output_tps"]["batches"],
        }
        for v in new
    ],
}
args.output.write_text(json.dumps(comparisons, indent=2) + "\n")
print(
    json.dumps(
        {"comparisons": len(rows), "daily_c4_scale1": comparisons["daily_c4_scale1"]}
    )
)
