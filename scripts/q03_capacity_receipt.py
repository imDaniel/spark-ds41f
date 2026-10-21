#!/usr/bin/env python3
"""Offline Q03 exact-cell receipt validation. No collection or runtime operations."""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import re
import sys

CASES = {"four_64k": (4, 64512, 1024), "four_256k": (4, 261120, 1024),
         "single_1m": (1, 1047552, 1024)}
RESOURCES = {"topology": "tp4-ring", "tp": 4, "max_model_len": 1048576,
             "max_num_seqs": 16, "max_num_batched_tokens": 8192,
             "kv_bytes_per_rank": 11274289152, "draft_budget": 5}
FROZEN = {"native_commit": "154159dc3e38ca8b2321c01a153a1b2ccaf8663b",
          "image_digest": "sha256:90bd2bbc4709b14a2210a6752c707d9d16661180248949e183fa0c25b6691ff6",
          "runtime_manifest_sha256": "24315da1b6138aef8acccb7244000feceef71b74486eeddce31bec25264919b9"}
STARTUP_FLOOR = 5 * 1024**3
STEADY_FLOOR = 3 * 1024**3
SUCCESS = "vllm:request_success_total"
PREEMPT = "vllm:num_preemptions_total"
RUNNING = "vllm:num_requests_running"
WAITING = "vllm:num_requests_waiting"
SHA = re.compile(r"[0-9a-f]{64}\Z")
METRIC = re.compile(r'([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})?\s+([^\s]+)(?:\s+([^\s]+))?\Z')
NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*("(?:[^"\\]|\\[\\"n])*")')


class EvidenceError(ValueError):
    pass


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise EvidenceError("duplicate JSON key: " + key)
            result[key] = value
        return result
    def constant(value):
        raise EvidenceError("nonfinite JSON constant: " + value)
    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise EvidenceError("nonfinite JSON number: " + value)
        return number
    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant, parse_float=finite_float)


def exact(a, b):
    """JSON structural equality without Python's True == 1 conflation."""
    if type(a) is not type(b):
        return False
    if type(a) is dict:
        return a.keys() == b.keys() and all(exact(a[k], b[k]) for k in a)
    if type(a) is list:
        return len(a) == len(b) and all(exact(x, y) for x, y in zip(a, b))
    return a == b


def integer(value, path, minimum=0):
    if type(value) is not int or value < minimum:
        raise EvidenceError(path + " must be an integer >= " + str(minimum))
    return value


def boolean(value, path):
    if type(value) is not bool:
        raise EvidenceError(path + " must be a boolean")
    return value


def token_ids(value, path):
    if type(value) is not list:
        raise EvidenceError(path + " must contain actual token IDs")
    for x in value:
        integer(x, path)
    return value


def nonempty(value, path):
    if type(value) is not str or not value or value.strip() != value or "<" in value:
        raise EvidenceError(path + " is missing or unbound")
    return value


def parse_prometheus(text):
    """Preserve every label set. Never sum absent, duplicate or invalid series."""
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = METRIC.fullmatch(line)
        if not match:
            raise EvidenceError("malformed Prometheus line")
        name, label_text, value, explicit_time = match.groups()
        if explicit_time is not None:
            raise EvidenceError("explicit metric timestamps need an independently bound clock conversion; unsupported")
        labels = {}
        rest = label_text or ""
        while rest:
            match = LABEL.match(rest)
            if not match or match[1] in labels:
                raise EvidenceError("malformed or duplicate metric label")
            # Prometheus accepts only \\, \", and \n escapes, all JSON compatible.
            labels[match[1]] = strict_json(match[2])
            rest = rest[match.end():].lstrip()
            if rest:
                if not rest.startswith(","):
                    raise EvidenceError("malformed label separator")
                rest = rest[1:].lstrip()
                if not rest:
                    raise EvidenceError("trailing label separator")
        if not NUMBER.fullmatch(value):
            raise EvidenceError("invalid native Prometheus numeric spelling")
        try:
            number = Decimal(value)
        except InvalidOperation as exc:
            raise EvidenceError("invalid metric value") from exc
        if not number.is_finite() or number < 0 or number != number.to_integral_value():
            raise EvidenceError("required metric values must be finite nonnegative integers")
        key = (name, tuple(sorted(labels.items())))
        if key in result:
            raise EvidenceError("duplicate metric series")
        result[key] = int(number)
    return result


class Validator:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.findings = []
        self.verified = {}
        self.observed_running = False
        self.swap = []

    def issue(self, code, path, message, failure=False):
        self.findings.append({"severity": "FAIL" if failure else "INCONCLUSIVE",
                              "code": code, "path": path, "message": message})

    def check(self, condition, code, path, message, failure=True):
        if not condition:
            self.issue(code, path, message, failure)
        return condition

    def source(self, ref, path, kind="json"):
        if type(ref) is not dict or type(ref.get("path")) is not str or not SHA.fullmatch(str(ref.get("sha256", ""))):
            raise EvidenceError(path + " requires a path and SHA256 of retained bytes")
        relative = Path(ref["path"])
        target = (self.root / relative).resolve()
        if relative.is_absolute() or not target.is_relative_to(self.root):
            raise EvidenceError(path + " escapes evidence root")
        raw = target.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != ref["sha256"]:
            raise EvidenceError(path + " source SHA256 mismatch")
        self.verified[ref["path"]] = digest
        text = raw.decode("utf-8")
        if kind == "text":
            return text
        if kind == "jsonl":
            return [strict_json(line) for line in text.splitlines() if line.strip()]
        return strict_json(text)

    def attempt(self, path, function):
        try:
            return function()
        except (EvidenceError, KeyError, TypeError, ValueError, OSError, UnicodeError) as exc:
            self.issue("UNPROVEN_EVIDENCE", path, str(exc))
            return None

    def binding(self, actual, expected, path):
        self.check(exact(actual, expected), "BINDING_MISMATCH", path,
                   "Actual state/image/engine/boot/model/resource binding differs from the independent freeze.")

    def expected(self, expected):
        if type(expected) is not dict or expected.get("schema") != "q03.capacity.expected.v1":
            raise EvidenceError("unsupported expected schema")
        case = expected["case"]
        if case not in CASES:
            raise EvidenceError("unknown capacity case")
        binding = expected["binding"]
        clock = self.source(expected["clock_source"], "expected.clock_source")
        if (type(clock) is not dict or clock.get("domain") != "monotonic"
                or clock.get("unit") != "ns"):
            raise EvidenceError("a retained common monotonic nanosecond clock source is required")
        nonempty(clock["clock_id"], "clock.clock_id")
        nonempty(clock["source_id"], "clock.source_id")
        if not exact(binding["clock"], clock):
            raise EvidenceError("frozen binding clock differs from retained clock source")
        for field in ("engine_id", "boot_id", "model_name", "endpoint"):
            nonempty(binding[field], "binding." + field)
        for field in ("model_assets_sha256", "tokenizer_assets_sha256", "request_semantics_sha256"):
            if not SHA.fullmatch(str(binding[field])):
                raise EvidenceError("unbound " + field)
        if not exact(binding["resources"], RESOURCES):
            raise EvidenceError("expected resources do not preserve the fixed protocol")
        for key, value in FROZEN.items():
            if binding[key] != value:
                raise EvidenceError("expected " + key + " differs from the frozen same197 protocol")
        state = binding["state"]
        if state["mode"] not in ("off", "dual") or state["steering"] not in ("unloaded", "loaded_zero", "active_1"):
            raise EvidenceError("unsupported capacity arm/state")
        steering = state["steering"]
        if steering == "unloaded":
            if any(state[k] is not None for k in ("profile", "profile_sha256", "scale")):
                raise EvidenceError("unloaded state must have no loaded profile or scale")
        else:
            if state["profile"] != "heretic-1017-attn-v1" or not SHA.fullmatch(str(state["profile_sha256"])):
                raise EvidenceError("loaded profile is unbound")
            scale = state["scale"]
            if type(scale) not in (int, float) or scale != (0 if steering == "loaded_zero" else 1):
                raise EvidenceError("incorrect capacity steering scale")
        labels = expected["metric_labels"]
        if type(labels) is not dict or set(labels) != {"model_name", "engine"}:
            raise EvidenceError("metric_labels must bind model_name and engine")
        if labels["model_name"] != binding["model_name"]:
            raise EvidenceError("metric model_name differs from binding")
        nonempty(labels["engine"], "metric_labels.engine")
        return case, binding

    def corpus(self, expected, case):
        rows = self.source(expected["corpus"], "expected.corpus", "jsonl")
        count, inputs, _ = CASES[case]
        if len(rows) != count:
            raise EvidenceError("frozen corpus does not have exact case request count")
        result, prompts = {}, set()
        for row in rows:
            rid = nonempty(row["request_id"], "corpus.request_id")
            ids = token_ids(row["prompt_token_ids"], "corpus.prompt_token_ids")
            if rid in result or len(ids) != inputs:
                raise EvidenceError("duplicate corpus ID or incorrect frozen input length")
            digest = hashlib.sha256(json.dumps(ids, separators=(",", ":")).encode()).hexdigest()
            if digest in prompts:
                raise EvidenceError("capacity prompts must be distinct")
            prompts.add(digest)
            result[rid] = ids
        return result

    def request(self, row, expected, case, corpus, seen, path):
        if type(row) is not dict:
            raise EvidenceError("request record must be an object")
        _, inputs, outputs = CASES[case]
        rid = self.attempt(path + ".request_id", lambda: nonempty(row["request_id"], path + ".request_id"))
        if rid is not None:
            self.check(rid not in seen, "DUPLICATE_REQUEST", path, "Duplicate request ID.")
            seen.add(rid)
        self.attempt(path + ".binding", lambda: self.binding(row["binding"], expected["binding"], path + ".binding"))
        actual_binding = row.get("binding")
        prompt = self.attempt(path + ".prompt_token_ids", lambda: token_ids(row["prompt_token_ids"], path + ".prompt_token_ids"))
        output = self.attempt(path + ".output_token_ids", lambda: token_ids(row["output_token_ids"], path + ".output_token_ids"))
        if prompt is not None:
            self.check(rid in corpus and exact(prompt, corpus.get(rid)), "INPUT_MISMATCH", path, "Actual fully templated prompt IDs differ from frozen IDs.")
            self.check(len(prompt) == inputs, "TOKEN_LENGTH", path, "Actual input length differs from the exact case.")
        if output is not None:
            self.check(len(output) == outputs, "TOKEN_LENGTH", path, "Actual output length differs from the exact case.")
        for key, count in (("prompt_tokens", inputs), ("completion_tokens", outputs)):
            self.attempt(path + "." + key, lambda key=key, count=count: self.check(integer(row[key], path + "." + key) == count, "USAGE_LENGTH", path, "Usage count differs from exact token contract."))
        self.attempt(path + ".status_code", lambda: self.check(integer(row["status_code"], path + ".status_code") == 200, "REQUEST_ERROR", path, "Request HTTP status was not successful."))
        self.attempt(path + ".finish_reason", lambda: self.check(row["finish_reason"] == "length", "REQUEST_ERROR", path, "Request was not length-finished."))
        self.attempt(path + ".error", lambda: self.check(row["error"] is None, "REQUEST_ERROR", path, "Request contains an error."))
        self.attempt(path + ".truncated", lambda: self.check(not boolean(row["truncated"], path + ".truncated"), "TRUNCATION", path, "Request was truncated."))
        start = self.attempt(path + ".sent_ns", lambda: integer(row["sent_ns"], path + ".sent_ns"))
        end = self.attempt(path + ".end_ns", lambda: integer(row["end_ns"], path + ".end_ns"))
        if start is None or end is None:
            return None
        self.check(start < end, "REQUEST_TIME", path, "Invalid request interval.")
        return start, end, exact(actual_binding, expected["binding"])

    def requests(self, receipt, expected, case, corpus):
        rows = self.source(receipt["requests"], "requests", "jsonl")
        count = CASES[case][0]
        self.check(len(rows) == count, "REQUEST_COUNT", "requests", "Expected exactly " + str(count) + " records, with no dropped failures.")
        seen, observations = set(), []
        for i, row in enumerate(rows):
            path = "requests[" + str(i) + "]"
            observations.append(self.attempt(path, lambda row=row, path=path: self.request(row, expected, case, corpus, seen, path)))
        self.check(seen == set(corpus), "REQUEST_ID_COVERAGE", "requests", "Missing or unexpected request IDs.")
        valid = [row for row in observations if row is not None]
        if not valid:
            raise EvidenceError("no usable request intervals")
        starts, ends = [row[0] for row in valid], [row[1] for row in valid]
        complete = (len(rows) == count and len(seen) == count and seen == set(corpus)
                    and len(valid) == count and all(row[2] for row in valid))
        overlap = (max(starts), min(ends)) if complete else None
        return min(starts), max(ends), overlap

    def counter_sample(self, sample, expected, path):
        self.binding(sample["binding"], expected["binding"], path + ".binding")
        when = integer(sample["observed_ns"], path + ".observed_ns")
        raw = self.source(sample["source"], path + ".source", "text")
        names = {SUCCESS, PREEMPT, RUNNING, WAITING}
        relevant = "\n".join(line for line in raw.splitlines() if any(line.strip().startswith(name + suffix) for name in names for suffix in ("{", " ", "\t")))
        series = parse_prometheus(relevant)
        selected = {}
        for (name, items), value in series.items():
            found = dict(items)
            if all(found.get(k) == v for k, v in expected["metric_labels"].items()):
                selected[(name, items)] = value
            elif not all(k in found for k in expected["metric_labels"]):
                self.issue("UNPROVEN_EVIDENCE", path, "Required metric lacks model_name/engine labels.")
            else:
                self.issue("FOREIGN_ENGINE_METRIC", path, "Capture contains a foreign model/engine series; isolation is unproven.")
        for name in sorted(names):
            if not any(key[0] == name for key in selected):
                self.issue("UNPROVEN_EVIDENCE", path, "Required labeled metric is absent: " + name)
        for name in (PREEMPT, RUNNING, WAITING):
            if sum(key[0] == name for key in selected) != 1:
                self.issue("UNPROVEN_EVIDENCE", path, "Absent or ambiguous series for " + name)
        if not any(key[0] == SUCCESS and dict(key[1]).get("finished_reason") == "length" for key in selected):
            self.issue("UNPROVEN_EVIDENCE", path, "Required labeled length-finish counter is absent.")
        return when, selected, exact(sample["binding"], expected["binding"])

    def counters(self, receipt, expected, case, interval):
        capture = self.source(receipt["counters"], "counters")
        self.binding(capture["binding"], expected["binding"], "counters.binding")
        samples = capture["samples"]
        if type(samples) is not list or len(samples) < 2:
            raise EvidenceError("before/after raw scrapes are required")
        parsed = [self.attempt("counters.samples[" + str(i) + "]",
                              lambda sample=sample, i=i: self.counter_sample(sample, expected, "counters.samples[" + str(i) + "]"))
                  for i, sample in enumerate(samples)]
        times = [row[0] for row in parsed if row is not None]
        self.check(all(a < b for a, b in zip(times, times[1:])), "SCRAPE_ORDER", "counters", "Scrape times must be strictly increasing.", False)
        if interval is not None:
            self.check(parsed[0] is not None and parsed[-1] is not None and parsed[0][0] <= interval[0] and parsed[-1][0] >= interval[1], "COUNTER_FENCE", "counters", "Scrapes do not bracket all requests.", False)
        for snapshot in (parsed[0], parsed[-1]):
            if snapshot is not None:
                self.check(all(value == 0 for (name, _), value in snapshot[1].items() if name in (RUNNING, WAITING)), "NONIDLE_FENCE", "counters", "Before/after scrape was not idle.")
        comparable = all(row is not None and any(key[0] == SUCCESS and dict(key[1]).get("finished_reason") == "length" for key in row[1]) for row in parsed)
        available = [row for row in parsed if row is not None]
        for previous, current in zip(available, available[1:]):
            left, right = previous[1], current[1]
            if set(left) != set(right):
                self.issue("COUNTER_SERIES_CHANGED", "counters", "Series appeared/disappeared; absent series are not zero.")
                comparable = False
            for key in sorted(left.keys() & right.keys()):
                if key[0] in (SUCCESS, PREEMPT) and right[key] < left[key]:
                    self.issue("COUNTER_RESET", "counters", "Monotone counter reset invalidates the interval.")
                    comparable = False
                if key[0] == PREEMPT and right[key] > left[key]:
                    self.issue("PREEMPTION", "counters", "Preemption counter increased.", True)
                if key[0] == SUCCESS:
                    reason = dict(key[1]).get("finished_reason")
                    if reason is None:
                        self.issue("UNPROVEN_EVIDENCE", "counters", "Request counter lacks finished_reason label.")
                        comparable = False
                    elif reason != "length" and right[key] > left[key]:
                        self.issue("LABELED_NONLENGTH_FINISH", "counters", "Non-length/error finish counter increased: " + reason, True)
        if comparable:
            left, right = parsed[0][1], parsed[-1][1]
            delta = sum(right[key] - value for key, value in left.items() if key[0] == SUCCESS and dict(key[1]).get("finished_reason") == "length")
            self.check(delta == CASES[case][0], "COMPLETION_COUNTER", "counters", "Labeled length-finish delta differs from exact request count.")
        if interval is not None and interval[2] is not None and exact(capture["binding"], expected["binding"]):
            for row in parsed:
                if row is not None:
                    when, series, same_binding = row
                    running = [value for (name, _), value in series.items() if name == RUNNING]
                    if same_binding and running == [CASES[case][0]] and interval[2][0] < when < interval[2][1]:
                        self.observed_running = True
        if CASES[case][0] == 4:
            self.check(self.observed_running, "CONCURRENT_ADMISSION_UNPROVEN", "counters", "No correctly labeled running=4 sample strictly inside all four exact request intervals on the bound common clock.", False)
        return max(times) if times else None

    def rank(self, ref, expected, interval, counter_end, ranks, nodes, path):
        row = self.source(ref, path)
        if type(row) is not dict:
            raise EvidenceError("rank capture must be an object")
        def identity():
            rank = integer(row["rank"], path + ".rank")
            node = nonempty(row["node_id"], path + ".node_id")
            self.check(rank in range(4) and rank not in ranks and node not in nodes, "RANK_COVERAGE", path, "Ranks and nodes must be unique 0..3.")
            ranks.add(rank)
            nodes.add(node)
        self.attempt(path + ".identity", identity)
        self.attempt(path + ".binding", lambda: self.binding(row["binding"], expected["binding"], path + ".binding"))
        startup, steady, drain = row.get("startup", {}), row.get("steady", {}), row.get("drain", {})
        if interval is not None:
            self.attempt(path + ".startup.time", lambda: self.check(integer(startup["observed_ns"], "startup.time") <= interval[0], "STARTUP_ORDER", path, "Startup guard must precede requests."))
            self.attempt(path + ".steady.coverage", lambda: self.check(integer(steady["start_ns"], "steady.start") <= interval[0] and integer(steady["end_ns"], "steady.end") >= interval[1] and boolean(steady["complete"], "steady.complete"), "STEADY_COVERAGE", path, "Whole workload memory/health guard capture is incomplete.", False))
        self.attempt(path + ".startup.guard", lambda: self.check(integer(startup["guard_floor_bytes"], "startup.floor") == STARTUP_FLOOR and integer(startup["free_bytes"], "startup.free") >= STARTUP_FLOOR and boolean(startup["passed"], "startup.passed"), "STARTUP_GUARD", path, "Existing 5 GiB startup guard failed or changed."))
        self.attempt(path + ".steady.guard", lambda: self.check(integer(steady["guard_floor_bytes"], "steady.floor") == STEADY_FLOOR and integer(steady["min_free_bytes"], "steady.min") >= STEADY_FLOOR, "STEADY_GUARD", path, "Existing 3 GiB steady guard failed or changed."))
        for name in ("guard_halts", "oom_events", "allocation_retries", "driver_errors", "errors", "engine_restarts"):
            self.attempt(path + ".steady." + name, lambda name=name: self.check(integer(steady[name], "steady." + name) == 0, "RANK_HEALTH", path, "Observed " + name + "."))
        self.attempt(path + ".swap", lambda: self.swap.append({"rank": integer(row["rank"], "rank"), "swap_in_bytes": integer(steady["swap_in_bytes"], "swap_in_bytes"), "swap_out_bytes": integer(steady["swap_out_bytes"], "swap_out_bytes")}))
        if interval is not None:
            self.attempt(path + ".drain.time", lambda: self.check(integer(drain["observed_ns"], "drain.time") >= max(interval[1], integer(steady["end_ns"], "steady.end"), counter_end or 0), "DRAIN_ORDER", path, "Drain proof precedes workload/guard/counter completion."))
        for name in ("daemon_active", "engine_processes", "gpu_processes", "pending_epochs", "guard_processes"):
            self.attempt(path + ".drain." + name, lambda name=name: self.check(integer(drain[name], "drain." + name) == 0, "DRAIN_NOT_QUIESCENT", path, "Nonzero " + name + " after workload."))
        for name in ("stopped", "quiescent", "protected_inputs_unchanged"):
            self.attempt(path + ".drain." + name, lambda name=name: self.check(boolean(drain[name], "drain." + name), "DRAIN_NOT_PROVEN", path, "Drain observation is false: " + name + "."))
        self.attempt(path + ".drain.policy", lambda: self.check(drain["policy"] == "keep-stopped" and exact(drain["owned_ids"], []), "DRAIN_POLICY", path, "Keep-stopped cleanup/owned-ID proof is not satisfied."))

    def ranks(self, receipt, expected, interval, counter_end=None):
        refs = receipt["ranks"]
        if type(refs) is not list:
            raise EvidenceError("rank references must be a list")
        self.check(len(refs) == 4, "UNPROVEN_EVIDENCE", "ranks", "Exactly four retained rank captures are required.", False)
        ranks, nodes = set(), set()
        for i, ref in enumerate(refs):
            path = "ranks[" + str(i) + "]"
            self.attempt(path, lambda ref=ref, path=path: self.rank(ref, expected, interval, counter_end, ranks, nodes, path))
        self.check(ranks == set(range(4)), "UNPROVEN_EVIDENCE", "ranks", "All four rank identities must be proven.", False)


def validate_capacity_receipt(receipt, *, expected, evidence_root):
    validator = Validator(evidence_root)
    config = validator.attempt("expected", lambda: validator.expected(expected))
    if type(receipt) is not dict or receipt.get("schema") != "q03.capacity.receipt.v1":
        validator.issue("UNPROVEN_EVIDENCE", "receipt", "Unsupported receipt schema.")
        config = None
    if config:
        case, _ = config
        validator.check(receipt.get("case") == case, "CASE_MISMATCH", "case", "Receipt case differs from independent freeze.")
        corpus = validator.attempt("corpus", lambda: validator.corpus(expected, case))
        interval = validator.attempt("requests", lambda: validator.requests(receipt, expected, case, corpus)) if corpus is not None else None
        counter_end = validator.attempt("counters", lambda: validator.counters(receipt, expected, case, interval))
        validator.attempt("ranks", lambda: validator.ranks(receipt, expected, interval, counter_end))
    severities = {f["severity"] for f in validator.findings}
    status = "FAIL" if "FAIL" in severities else "INCONCLUSIVE" if severities else "PASS"
    return {"schema": "q03.capacity.validation.v1", "status": status,
            "scope": "One exact capacity cell/arm/state receipt contract only; not a full matrix or residency qualification.",
            "case": expected.get("case") if type(expected) is dict else None,
            "claims": {"exact_cell_receipt": status,
                       "observed_concurrent_admission": "OBSERVED" if validator.observed_running else "UNKNOWN",
                       "full_concurrent_long_context_residency": "UNKNOWN",
                       "four_1m_or_sixteen_1m_capacity": "NOT_ESTABLISHED"},
            "findings": validator.findings,
            "verified_source_sha256": dict(sorted(validator.verified.items())),
            "swap_observations": sorted(validator.swap, key=lambda x: x["rank"])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = validate_capacity_receipt(strict_json(args.receipt.read_text()),
                                          expected=strict_json(args.expected.read_text()),
                                          evidence_root=args.evidence_root)
    except (ValueError, OSError, UnicodeError) as exc:
        result = {"schema": "q03.capacity.validation.v1", "status": "INCONCLUSIVE",
                  "findings": [{"severity": "INCONCLUSIVE", "code": "UNPROVEN_EVIDENCE", "path": "input", "message": str(exc)}]}
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    return {"PASS": 0, "FAIL": 1, "INCONCLUSIVE": 2}[result["status"]]


if __name__ == "__main__":
    sys.exit(main())
