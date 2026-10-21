"""Offline Q03 paired-boot noninferiority. No runner, network, CLI, or retry selection.

See experiments/2026-10-21-paired-performance/README.md for the receipt contract.
Artifact digests establish byte integrity, not that an observer told the truth.
"""

import hashlib
import json
import math
import re
import statistics
import struct
from decimal import Decimal, DecimalException, localcontext

from . import stats

SCHEMA = "q03-paired-performance/1"
ORDERS = (("OFF", "DUAL"), ("DUAL", "OFF"), ("DUAL", "OFF"), ("OFF", "DUAL"))
COUNTERS = {"finished": "vllm:request_success_total",
            "generation": "vllm:generation_tokens_total",
            "preemptions": "vllm:num_preemptions_total"}
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
LABEL = re.compile(r'[ \t]*([a-zA-Z_][a-zA-Z0-9_]*)=("(?:[^"\\\r\n]|\\["\\n])*")[ \t]*(?:,|$)')
COUNTER_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
TIMESTAMP = re.compile(r"[+-]?[0-9]+\Z")
MAX_NATIVE_COUNTER = Decimal.from_float(float.fromhex("0x1.fffffffffffffp+1023"))
STEERING = ("steering_profile", "steering_scale")


class InvalidReceipt(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise InvalidReceipt(message)


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _same(left, right):
    return canonical_sha256(left) == canonical_sha256(right)


def _positive(value):
    return _number(value) and value > 0


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _hash(value):
    return isinstance(value, str) and DIGEST.fullmatch(value) is not None


def _keys(value, keys, name):
    _require(isinstance(value, dict) and set(keys) <= value.keys(), f"{name}: missing fields")


def canonical_sha256(value):
    """Digest JSON meanings; raw-artifact hashes always use original bytes instead."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _json(raw):
    def invalid_constant(value):
        raise InvalidReceipt(f"nonfinite JSON value: {value}")
    def finite_float(value):
        parsed = float(value)
        _require(math.isfinite(parsed) and not (parsed == 0 and Decimal(value) != 0),
                 "nonfinite or underflowed JSON number")
        return parsed
    return json.loads(raw, object_pairs_hook=_unique_object, parse_constant=invalid_constant,
                      parse_float=finite_float)


def _artifact(artifacts, digest):
    _require(_hash(digest), "invalid artifact digest")
    raw = artifacts.get(digest)
    _require(isinstance(raw, bytes), f"missing artifact bytes: {digest}")
    _require(hashlib.sha256(raw).hexdigest() == digest, f"artifact hash mismatch: {digest}")
    return raw


def _document(artifacts, digest):
    value = _json(_artifact(artifacts, digest))
    _require(isinstance(value, dict), "artifact must be a JSON object")
    return value


def _steering_controls(body):
    """Preserve each supported location; never guess precedence or merge conflicts."""
    controls = {key: body[key] for key in STEERING if key in body}
    nested = body.get("vllm_xargs")
    _require(nested is None or isinstance(nested, dict), "vllm_xargs must be an object or null")
    if nested is not None:
        selected = {key: nested[key] for key in STEERING if key in nested}
        if selected:
            controls["vllm_xargs"] = selected
    return controls


def _preregister(plan, artifacts):
    _keys(plan, ("schema", "cell_id", "kind", "blocks", "identity", "state", "arm_configs",
                 "corpus", "workload", "client_source_sha256"), "preregistration")
    _require(plan["schema"] == SCHEMA and _text(plan["cell_id"]), "invalid preregistration schema/cell")
    _require(plan["kind"] in ("sustained", "burst", "cold_prefill"), "invalid cell kind")
    blocks = plan["blocks"]
    _require(isinstance(blocks, list) and len(blocks) == 4 and all(_text(x) for x in blocks)
             and len(set(blocks)) == 4, "exactly four distinct block IDs required")
    identity = plan["identity"]
    _keys(identity, ("model", "endpoint", "metrics_endpoint", "metric_labels", "checkpoint_sha256", "tokenizer_sha256", "template_sha256",
                     "runtime_sha256", "image_sha256", "resources_sha256"), "identity")
    _require(_text(identity["model"]) and _text(identity["endpoint"]) and _text(identity["metrics_endpoint"]),
             "missing model/service/metrics endpoint")
    _require(isinstance(identity["metric_labels"], dict) and set(identity["metric_labels"]) == {"model_name", "engine"}
             and identity["metric_labels"]["model_name"] == identity["model"]
             and _text(identity["metric_labels"]["engine"]), "missing native model/engine metric labelset")
    _require(all(_hash(v) for k, v in identity.items() if k.endswith("_sha256")), "invalid identity digest")
    _require(_hash(plan["client_source_sha256"]), "missing timing collector source digest")
    _artifact(artifacts, plan["client_source_sha256"])
    state = plan["state"]
    _keys(state, ("name", "profile_name", "profile_sha256", "vector_sha256", "scale_fp32_hex", "extra_body"), "state")
    _require(state["name"] in ("unloaded", "loaded_zero", "active"), "invalid steering state")
    _require(isinstance(state["extra_body"], dict) and isinstance(state["scale_fp32_hex"], str)
             and re.fullmatch(r"[0-9a-f]{8}", state["scale_fp32_hex"]), "incomplete request state")
    scale = struct.unpack("!f", bytes.fromhex(state["scale_fp32_hex"]))[0]
    _require(math.isfinite(scale), "nonfinite FP32 steering scale")
    controls = _steering_controls(state["extra_body"])
    if state["name"] == "unloaded":
        _require(state["profile_name"] is None and state["profile_sha256"] is None and state["vector_sha256"] is None
                 and scale == 0 and not controls,
                 "unloaded state cannot carry a profile/vector")
    else:
        _require(_text(state["profile_name"]) and _hash(state["profile_sha256"]) and _hash(state["vector_sha256"]),
                 "missing loaded profile/vector")
        _require((scale == 0 if state["name"] == "loaded_zero" else scale != 0), "scale does not match state")
        _require(state["extra_body"].get("steering_profile") == state["profile_name"]
                 and _number(state["extra_body"].get("steering_scale"))
                 and struct.pack("!f", state["extra_body"]["steering_scale"]).hex() == state["scale_fp32_hex"],
                 "request steering profile/FP32 scale differs")
        for location in (controls, controls.get("vllm_xargs", {})):
            if "steering_profile" in location:
                _require(_same(location["steering_profile"], state["profile_name"]), "conflicting steering profile location")
            if "steering_scale" in location:
                value = location["steering_scale"]
                _require(_number(value) and (value == 0 if scale == 0 else value != 0)
                         and struct.pack("!f", value).hex() == state["scale_fp32_hex"],
                         "conflicting or underflowed steering scale location")
    _require(set(plan["arm_configs"]) == {"OFF", "DUAL"}, "both arm configurations required")
    for arm in ("OFF", "DUAL"):
        ranks = plan["arm_configs"][arm]
        _require(isinstance(ranks, list) and len(ranks) == 4, "four rank configurations required")
        for rank, config in enumerate(ranks):
            _keys(config, ("rank", "worker_config_sha256", "runner_config_sha256"), "rank configuration")
            _require(type(config["rank"]) is int and config["rank"] == rank
                     and _hash(config["worker_config_sha256"]) and _hash(config["runner_config_sha256"]),
                     "rank/config identity invalid")
            for key in ("worker_config_sha256", "runner_config_sha256"):
                frozen = _document(artifacts, config[key])
                _require(len(frozen) == 58, "complete frozen 58-field config snapshot required")
    work = plan["workload"]
    _keys(work, ("mode", "concurrency", "input_tokens", "output_tokens", "warmup_s", "duration_s",
                 "subwindows", "temperature", "seed", "request_template_sha256", "cache_policy"), "workload")
    for key in ("concurrency", "input_tokens", "output_tokens", "subwindows"):
        _require(type(work[key]) is int and work[key] > 0, f"invalid workload {key}")
    _require(_number(work["temperature"]) and type(work["seed"]) is int
             and _number(work["warmup_s"]) and work["warmup_s"] >= 0
             and _number(work["duration_s"]) and work["duration_s"] >= 0
             and _hash(work["request_template_sha256"]) and _text(work["cache_policy"]), "invalid workload identity")
    request = _document(artifacts, work["request_template_sha256"])
    expected_request = {"model": identity["model"], "temperature": work["temperature"], "seed": work["seed"],
                        "max_tokens": work["output_tokens"], "min_tokens": work["output_tokens"], "ignore_eos": True}
    _require(not set(expected_request).intersection(state["extra_body"]), "state request fields override fixed workload")
    expected_request.update(state["extra_body"])
    _require(_same(_steering_controls(request), controls),
             "frozen request steering controls differ from registered state/locations")
    _require(all(k in request and _same(request[k], v) for k, v in expected_request.items()),
             "frozen request template differs from workload/state")
    if plan["kind"] == "sustained":
        _require(work["mode"] in ("closed", "poisson") and work["duration_s"] > 0
                 and work["output_tokens"] > 1, "invalid sustained workload")
    else:
        _require(work["mode"] == "burst" and type(work.get("requests_per_boot")) is int
                 and work["requests_per_boot"] > 0 and type(work.get("bursts_per_boot")) is int
                 and work["bursts_per_boot"] > 0
                 and work["requests_per_boot"] == work["bursts_per_boot"] * work["concurrency"],
                 "finite request/burst inventory required")
        _require(work["output_tokens"] == 1 if plan["kind"] == "cold_prefill" else work["output_tokens"] > 1,
                 "invalid output length for cell kind")
    corpus = plan["corpus"]
    _keys(corpus, ("raw_sha256", "parsed_sha256"), "corpus")
    raw = _artifact(artifacts, corpus["raw_sha256"])
    rows = [_json(line) for line in raw.splitlines() if line.strip()]
    _require(rows and canonical_sha256(rows) == corpus["parsed_sha256"], "parsed corpus digest mismatch")
    prompts = {}
    for row in rows:
        _keys(row, ("id", "prompt_token_ids"), "corpus row")
        ids = row["prompt_token_ids"]
        _require(_text(row["id"]) and row["id"] not in prompts and isinstance(ids, list)
                 and len(ids) == work["input_tokens"] and all(type(t) is int and t >= 0 for t in ids),
                 "corpus IDs/actual input lengths invalid")
        prompts[row["id"]] = canonical_sha256(ids)
    return prompts


def _counter_series(raw, names, expected_labels):
    """Keep exact series labels, including finish_reason; never silently sum foreign engines."""
    series = {}
    for line in raw.decode("utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        name = re.split(r"[\s{]", line, maxsplit=1)[0]
        if name not in names.values():
            continue
        match = re.fullmatch(r"([^\s{]+)\{(.*)\}[ \t]+([^\s]+)(?:[ \t]+([^\s]+))?[ \t]*", line)
        _require(match is not None, f"unlabeled/malformed required counter: {name}")
        labels, position = {}, 0
        while position < len(match[2]):
            label = LABEL.match(match[2], position)
            _require(label is not None and label[1] not in labels, "invalid/duplicate native metric labels")
            labels[label[1]] = re.sub(r'\\(["\\n])', lambda m: {"n": "\n", "\\": "\\", '"': '"'}[m[1]], label[2][1:-1])
            position = label.end()
        _require(all(labels.get(k) == v for k, v in expected_labels.items()), "foreign/missing engine/model counter labels")
        _require(COUNTER_NUMBER.fullmatch(match[3]) is not None, "invalid native counter number")
        value = Decimal(match[3])
        _require(value.is_finite() and 0 <= value <= MAX_NATIVE_COUNTER
                 and value == value.to_integral_value(), "missing/nonfinite/nonintegral native counter")
        value = Decimal(int(value))  # Exact integral normalization, including zero with a large exponent.
        if match[4] is not None:
            _require(TIMESTAMP.fullmatch(match[4]) is not None, "invalid native sample timestamp")
            _require(-(2 ** 63) <= int(match[4]) < 2 ** 63, "native sample timestamp out of range")
        key = (name, tuple(sorted(labels.items())))
        _require(key not in series, "duplicate counter series")
        series[key] = value
    _require(set(names.values()) == {key[0] for key in series}, "missing required counters")
    return series


def _run(plan, prompts, slot, digest, artifacts, plan_hash):
    run = _document(artifacts, digest)
    arm = ORDERS[slot // 2][slot % 2]
    _keys(run, ("schema", "slot", "block_id", "arm", "preregistration_sha256", "identity", "state",
                "workload", "corpus", "boot", "timing", "records", "start", "window", "boundaries",
                "counter_samples", "rejected"), "run")
    _require(run["schema"] == "q03-performance-run/1" and type(run["slot"]) is int and run["slot"] == slot
             and run["block_id"] == plan["blocks"][slot // 2] and run["arm"] == arm
             and run["preregistration_sha256"] == plan_hash, "wrong preregistered slot/arm/block")
    for key in ("identity", "state", "workload", "corpus"):
        _require(_same(run[key], plan[key]), f"{key} differs from preregistration")
    boot = run["boot"]
    _keys(boot, ("id", "engine_id", "window_id", "attempt_id", "started_at_ns", "admission_sha256"), "boot")
    _require(all(_text(boot[k]) for k in ("id", "engine_id", "window_id", "attempt_id"))
             and type(boot["started_at_ns"]) is int
             and boot["started_at_ns"] > 0, "missing boot identity")
    admission = _document(artifacts, boot["admission_sha256"])
    _keys(admission, ("schema", "boot_id", "engine_id", "window_id", "attempt_id", "started_at_ns", "arm", "identity", "state", "ranks",
                      "ready", "steady_guards", "cleanup"), "boot admission")
    _require(admission["schema"] == "q03-performance-admission/1"
             and admission["boot_id"] == boot["id"] and admission["engine_id"] == boot["engine_id"]
             and admission["window_id"] == boot["window_id"] and admission["attempt_id"] == boot["attempt_id"]
             and type(admission["started_at_ns"]) is int
             and admission["started_at_ns"] == boot["started_at_ns"] and admission["arm"] == arm
             and _same(admission["identity"], plan["identity"]) and _same(admission["state"], plan["state"]),
             "boot admission identity mismatch")
    _require(admission["ready"] is True and admission["steady_guards"] is True and admission["cleanup"] is True,
             "boot lifecycle admission incomplete")
    ranks = admission["ranks"]
    _require(isinstance(ranks, list) and len(ranks) == 4, "four admitted ranks required")
    containers = []
    for expected, actual in zip(plan["arm_configs"][arm], ranks):
        _keys(actual, (*expected, "container_id"), "admitted rank")
        _require(_same({k: actual[k] for k in expected}, expected) and _text(actual["container_id"]),
                 "actual worker/runner configuration mismatch")
        containers.append(actual["container_id"])
    _require(len(set(containers)) == 4, "duplicate rank containers")
    timing = run["timing"]
    _keys(timing, ("clock", "clock_id", "source", "source_sha256", "units"), "timing provenance")
    _require(timing["clock"] == "time.monotonic" and _text(timing["clock_id"])
             and timing["source"] == "servebench.client.stream_chat" and timing["units"] == "seconds"
             and timing["source_sha256"] == plan["client_source_sha256"], "invalid timing provenance")
    _require(type(run["rejected"]) is int and run["rejected"] == 0, "rejected/missing requests")
    records = run["records"]
    _require(isinstance(records, list) and records, "no raw request records")
    seen = set()
    slots = set()
    bursts = set()
    for r in records:
        _keys(r, ("index", "slot", "prompt_id", "input_ids_sha256", "sent", "first", "end", "events",
                  "prompt_tokens", "completion_tokens", "finish_reason", "token_times", "error"), "request")
        _require(type(r["index"]) is int and r["index"] >= 0 and r["index"] not in seen, "duplicate/missing request ID")
        seen.add(r["index"])
        _require(type(r["slot"]) is int and 0 <= r["slot"] < plan["workload"]["concurrency"], "invalid client slot")
        slots.add(r["slot"])
        if plan["kind"] != "sustained":
            _require(type(r.get("burst")) is int and 0 <= r["burst"] < plan["workload"]["bursts_per_boot"]
                     and (r["burst"], r["slot"]) not in bursts, "invalid/duplicate burst request slot")
            bursts.add((r["burst"], r["slot"]))
        _require(r["prompt_id"] in prompts and r["input_ids_sha256"] == prompts[r["prompt_id"]],
                 "request input/corpus identity mismatch")
        _require(r["error"] is None and not r.get("stopped", False) and r["finish_reason"] == "length",
                 "failed/stopped/non-length request retained")
        _require(type(r["prompt_tokens"]) is int and r["prompt_tokens"] == plan["workload"]["input_tokens"]
                 and type(r["completion_tokens"]) is int and r["completion_tokens"] == plan["workload"]["output_tokens"],
                 "missing or inexact actual token counts")
        _require(all(_number(r[k]) for k in ("sent", "first", "end"))
                 and 0 <= r["sent"] < r["first"] <= r["end"], "invalid/nonfinite request timing")
        _require(r["token_times"] == "exact" and isinstance(r["events"], list) and r["events"],
                 "missing or interpolated raw chunk timing")
        last_time, last_count = r["sent"], 0
        for event in r["events"]:
            _require(isinstance(event, list) and len(event) == 2 and _number(event[0])
                     and r["first"] <= event[0] <= r["end"] and event[0] >= last_time
                     and type(event[1]) is int and last_count <= event[1] <= r["completion_tokens"],
                     "invalid/nonmonotonic raw token event")
            last_time, last_count = event
        _require(r["events"][0][0] == r["first"] and r["events"][0][1] > 0
                 and last_count == r["completion_tokens"], "incomplete output timing events")
    _require(seen == set(range(len(records))), "missing attempted request records")
    _require(slots == set(range(plan["workload"]["concurrency"])), "incomplete client slot coverage")
    _require({r["prompt_id"] for r in records} == set(prompts), "incomplete frozen corpus coverage")
    _require(_number(run["start"]) and run["start"] <= min(r["sent"] for r in records), "invalid run start")
    if plan["kind"] == "sustained":
        window = run["window"]
        _require(isinstance(window, list) and len(window) == 2 and all(_number(x) for x in window), "missing measured window")
        w0, w1 = window
        work = plan["workload"]
        _require(w1 > w0, "nonpositive observed measurement span")
        _require(w0 == run["start"] + work["warmup_s"] and w1 == w0 + work["duration_s"],
                 "measurement duration/warmup mismatch")
        expected = [w0 + i * work["duration_s"] / work["subwindows"] for i in range(work["subwindows"] + 1)]
        _require(isinstance(run["boundaries"], list) and len(run["boundaries"]) == len(expected)
                 and all(_number(a) and a == b for a, b in zip(run["boundaries"], expected))
                 and all(a < b for a, b in zip(run["boundaries"], run["boundaries"][1:])),
                 "subwindow boundary mismatch or collapsed subwindow")
        _require(all(r["sent"] < w1 for r in records), "requests sent beyond frozen measurement window")
        timed = [r for r in records if w0 <= r["sent"] < w1]
        produced = 0
        for r in records:
            count = 0
            for at, cumulative in r["events"]:
                if w0 < at <= w1:
                    produced += cumulative - count
                count = cumulative
        throughput = produced / (w1 - w0)
    else:
        _require(run["window"] is None and run["boundaries"] == []
                 and len(records) == plan["workload"]["requests_per_boot"], "finite workload record count/bounds mismatch")
        timed = records
        throughput = sum(r["completion_tokens"] for r in records) / (max(r["end"] for r in records) - min(r["sent"] for r in records))
    _require(timed and {r["prompt_id"] for r in timed} == set(prompts), "missing timed requests/frozen corpus coverage")
    samples = run["counter_samples"]
    _require(isinstance(samples, list) and len(samples) >= 2, "missing raw counter fences")
    names = dict(COUNTERS)
    if plan["kind"] == "cold_prefill":
        names["cache_hits"] = "vllm:prefix_cache_hits_total"
    series = []
    times = []
    scrape_binding = {"endpoint": plan["identity"]["metrics_endpoint"], "window_id": boot["window_id"],
                      "attempt_id": boot["attempt_id"], "boot_id": boot["id"],
                      "admission_sha256": boot["admission_sha256"], "clock_id": timing["clock_id"],
                      "preregistration_sha256": plan_hash, "slot": slot, "window": run["window"]}
    for sample in samples:
        _keys(sample, ("at", "engine_id", "raw_sha256", "binding"), "counter sample")
        _require(_same(sample["binding"], scrape_binding), "raw scrape endpoint/window/attempt/boot binding mismatch")
        _require(_number(sample["at"]) and sample["engine_id"] == boot["engine_id"], "counter clock/engine mismatch")
        times.append(sample["at"])
        series.append(_counter_series(_artifact(artifacts, sample["raw_sha256"]), names,
                                     plan["identity"]["metric_labels"]))
    _require(all(a < b for a, b in zip(times, times[1:])) and times[0] <= min(r["sent"] for r in records)
             and times[-1] >= max(r["end"] for r in records), "counter fences do not cover whole run/drain")
    for a, b in zip(series, series[1:]):
        _require(a.keys() == b.keys() and all(b[k] >= a[k] for k in a), "counter reset or missing/changed labeled series")
    if plan["kind"] == "sustained":
        _require(len(samples) == len(run["boundaries"]) + 2
                 and all(_number(sample.get("boundary")) and sample["boundary"] == boundary
                         for sample, boundary in zip(samples[1:-1], run["boundaries"])),
                 "missing whole-interval/subwindow raw scrapes")
        _require(all(boundary <= sample["at"] < following["at"]
                     for boundary, sample, following in zip(run["boundaries"], samples[1:-1], samples[2:])),
                 "counter scrape precedes its declared boundary")
    # Decimal construction is exact, but arithmetic otherwise uses a finite default
    # context. Size precision for every integral endpoint plus carry from summation.
    with localcontext() as context:
        context.prec = max(v.adjusted() + 1 for sample in series for v in sample.values()) + len(str(len(series[0]))) + 1
        delta = {key: sum((series[-1][s] - series[0][s] for s in series[0] if s[0] == name), Decimal(0))
                 for key, name in names.items()}
    _require(delta["finished"] == len(records) and delta["generation"] == sum(r["completion_tokens"] for r in records),
             "client/server whole-interval count mismatch or foreign requests")
    _require(delta["preemptions"] == 0, "preemption counter changed")
    if plan["kind"] == "cold_prefill":
        _require(delta["cache_hits"] == 0, "cold-prefill cache hits observed")
    ttft = [r["first"] - r["sent"] for r in timed]
    if plan["kind"] == "cold_prefill":
        metrics = {"cold_prefill_tps": statistics.fmean(r["prompt_tokens"] / t for r, t in zip(timed, ttft)),
                   "ttft_mean_s": statistics.fmean(ttft)}
    else:
        tpot = [(r["end"] - r["first"]) / (r["completion_tokens"] - 1) for r in timed]
        _require(all(_positive(v) for v in tpot), "missing/nonpositive TPOT")
        ttft_p, tpot_p = stats.percentiles(ttft, (50, 99)), stats.percentiles(tpot, (50, 99))
        metrics = {"output_tps": throughput, "ttft_p50_s": ttft_p["p50"], "ttft_p99_s": ttft_p["p99"],
                   "tpot_p50_s": tpot_p["p50"], "tpot_p99_s": tpot_p["p99"]}
    _require(all(_positive(v) for v in metrics.values()), "missing/nonfinite/nonpositive metric")
    return {"slot": slot, "arm": arm, "raw_sha256": digest, "boot_id": boot["id"], "engine_id": boot["engine_id"],
            "boot_started_at_ns": boot["started_at_ns"], "container_ids": containers, "clock_id": timing["clock_id"],
            "attempt_identity": [boot["window_id"], boot["attempt_id"]], "metric_labels": plan["identity"]["metric_labels"],
            "timed_requests": len(timed), "metrics": metrics, "issues": []}


def evaluate(preregistration, receipts, artifacts):
    """Validate one cell. Missing or invalid evidence returns INCONCLUSIVE, never zero.

    ``artifacts`` maps SHA-256 strings to original bytes, and is never mutated.
    ``receipts`` contains ordered ``run_sha256``, ``ledger_sha256``, ``control_sha256``.
    Overall status stays INCONCLUSIVE until external control/lifecycle integration;
    statistical_status reports only whether the supplied numeric bounds meet margins.
    Output preserves all supplied attempt digests, four paired differences and reasons.
    """
    result = {"schema": "q03-paired-performance-result/1", "status": "INCONCLUSIVE", "issues": [],
              "runs": [], "metrics": {}, "statistical_status": "INCONCLUSIVE",
              "prerequisites": [{"code": "CONTROL_ADMISSION_UNVERIFIED",
                                 "detail": "External control receipt is provenance only; integrate the matching full OFF/OFF maps validator."},
                                {"code": "EXTERNAL_PROVENANCE_REVIEW_REQUIRED",
                                 "detail": "Hashes and declarations do not prove ledger completeness, physical boot independence, or lifecycle admission."}],
              "claim_scope": "one preregistered cell only; not acceleration or a matrix claim"}
    try:
        _require(isinstance(artifacts, dict), "artifacts must map hashes to original bytes")
        prompts = _preregister(preregistration, artifacts)
        plan_hash = canonical_sha256(preregistration)
        result.update(cell_id=preregistration["cell_id"], preregistration_sha256=plan_hash)
        _keys(receipts, ("run_sha256", "ledger_sha256", "control_sha256"), "receipts")
        hashes = receipts["run_sha256"]
        result["submitted_run_sha256"] = hashes
        _require(isinstance(hashes, list) and len(hashes) == 8 and all(_hash(h) for h in hashes)
                 and len(set(hashes)) == 8, "exactly eight distinct unselected run artifacts required")
        ledger = _document(artifacts, receipts["ledger_sha256"])
        _require(ledger.get("preregistration_sha256") == plan_hash
                 and _same(ledger.get("attempts"), [{"slot": i, "run_sha256": h, "outcome": "completed"}
                                                    for i, h in enumerate(hashes)]),
                 "incomplete/failed/extra/reordered attempts: do not select retries")
        control = _document(artifacts, receipts["control_sha256"])
        _require(control.get("preregistration_sha256") == plan_hash and control.get("status") == "stable"
                 and isinstance(control.get("evidence_sha256"), list) and bool(control["evidence_sha256"]),
                 "missing/noisy/unstable control admission")
        for digest in control["evidence_sha256"]:
            _artifact(artifacts, digest)
    except (InvalidReceipt, ValueError, TypeError, KeyError, OverflowError, DecimalException) as error:
        result["issues"].append(str(error))
        return result
    for slot, digest in enumerate(hashes):
        try:
            run = _run(preregistration, prompts, slot, digest, artifacts, plan_hash)
        except (InvalidReceipt, ValueError, TypeError, KeyError, OverflowError, DecimalException) as error:
            run = {"slot": slot, "arm": ORDERS[slot // 2][slot % 2], "raw_sha256": digest,
                   "issues": [str(error)], "metrics": {}}
        result["runs"].append(run)
    valid = [r for r in result["runs"] if not r["issues"]]
    for key in ("boot_id", "engine_id"):
        if len({r[key] for r in valid}) != len(valid):
            result["issues"].append(f"reused {key}: subwindows/repeats cannot increase independent boot n")
    if len({tuple(r["attempt_identity"]) for r in valid}) != len(valid):
        result["issues"].append("reused window/attempt identity: independent boot lineage not established")
    containers = [c for r in valid for c in r["container_ids"]]
    if len(set(containers)) != len(containers):
        result["issues"].append("reused rank container: independent paired boots not established")
    if any(a["boot_started_at_ns"] >= b["boot_started_at_ns"] for a, b in zip(valid, valid[1:])):
        result["issues"].append("boot chronology does not follow preregistered order")
    metrics = ("cold_prefill_tps", "ttft_mean_s") if preregistration["kind"] == "cold_prefill" else (
        "output_tps", "ttft_p50_s", "ttft_p99_s", "tpot_p50_s", "tpot_p99_s")
    for metric in metrics:
        pairs, differences, issues = [], [], list(result["issues"])
        for block in range(4):
            a, b = result["runs"][2 * block:2 * block + 2]
            arms = {r["arm"]: r for r in (a, b)}
            off, dual = arms["OFF"], arms["DUAL"]
            pair_issues = a["issues"] + b["issues"]
            if "p99" in metric and any(r.get("timed_requests", 0) < 100 for r in (a, b)):
                pair_issues = pair_issues + ["p99 tail support insufficient: fewer than 100 timed requests per arm/boot"]
            d = None
            if not a["issues"] and not b["issues"]:
                d = 100 * (dual["metrics"][metric] / off["metrics"][metric] - 1)
                if not math.isfinite(d):
                    pair_issues.append("nonfinite paired difference")
                    d = None
                elif not pair_issues:
                    differences.append(d)
            pairs.append({"block_id": preregistration["blocks"][block], "off": off["metrics"].get(metric),
                          "dual": dual["metrics"].get(metric), "change_pct": d, "issues": pair_issues})
            issues.extend(pair_issues)
        row = {"status": "INCONCLUSIVE", "pairs": pairs, "n": len(differences), "issues": issues}
        if len(differences) == 4 and not issues:
            try:
                summary = stats.describe(differences)
                lower, upper = summary["mean"] - summary["ci95"], summary["mean"] + summary["ci95"]
                _require(_number(lower) and _number(upper), "nonfinite confidence interval")
                higher = metric.endswith("tps")
                passed = lower >= -3 if higher else upper <= 5
                row.update(summary=summary, df=3, t95=stats.t95(3), lower95_pct=lower, upper95_pct=upper,
                           margin_pct=-3 if higher else 5, bound="lower" if higher else "upper",
                           status="NONINFERIOR" if passed else "INCONCLUSIVE")
                if not passed:
                    row["issues"].append("confidence bound does not establish the preregistered noninferiority margin")
            except (InvalidReceipt, ValueError, OverflowError) as error:
                row["issues"].append(str(error))
        result["metrics"][metric] = row
    if not result["issues"] and all(row["status"] == "NONINFERIOR" for row in result["metrics"].values()):
        result["statistical_status"] = "NONINFERIOR"
    return result
