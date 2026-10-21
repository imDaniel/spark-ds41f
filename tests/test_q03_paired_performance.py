"""Synthetic CPU fixtures only. They are not field receipts or performance results."""

import copy
import hashlib
import json
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from servebench import paired_performance as paired


def put(artifacts, value):
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(raw).hexdigest()
    artifacts[digest] = raw
    return digest


def fixture(kind="burst", count=100, scales=(1.01, 1.01, 1.01, 1.01), subwindows=3, state="unloaded"):
    artifacts = {}
    rows = [{"id": str(i), "prompt_token_ids": [i + 1] * 8} for i in range(2)]
    raw = b"".join(json.dumps(row).encode() + b"\n" for row in rows)
    common = {"model": "synthetic-model", "endpoint": "http://synthetic.invalid/v1",
              "metrics_endpoint": "http://synthetic.invalid/metrics",
              "metric_labels": {"model_name": "synthetic-model", "engine": "0"}}
    common.update({key: "a" * 64 for key in ("checkpoint_sha256", "tokenizer_sha256", "template_sha256",
                                             "runtime_sha256", "image_sha256", "resources_sha256")})
    output = 1 if kind == "cold_prefill" else 4
    work = {"mode": "closed" if kind == "sustained" else "burst", "concurrency": 1,
            "input_tokens": 8, "output_tokens": output, "warmup_s": 0, "duration_s": 1000,
            "subwindows": subwindows, "temperature": 0, "seed": 0,
            "request_template_sha256": put(artifacts, {"model": "synthetic-model", "temperature": 0, "seed": 0,
                "max_tokens": output, "min_tokens": output, "ignore_eos": True}), "cache_policy": "unique_request_salt"}
    if kind != "sustained":
        work["requests_per_boot"] = count
        work["bursts_per_boot"] = count
    plan = {"schema": paired.SCHEMA, "cell_id": "SYNTHETIC_ONLY", "kind": kind,
            "blocks": [f"pair-{i}" for i in range(4)], "identity": common,
            "state": {"name": "unloaded", "profile_name": None, "profile_sha256": None, "vector_sha256": None,
                      "scale_fp32_hex": "00000000", "extra_body": {}},
            "arm_configs": {arm: [{"rank": i, "worker_config_sha256": put(artifacts, {f"field-{i}": arm for i in range(58)}),
                                    "runner_config_sha256": put(artifacts, {f"field-{i}": arm for i in range(58)})}
                                   for i in range(4)] for arm in ("OFF", "DUAL")},
            "corpus": {"raw_sha256": put(artifacts, raw), "parsed_sha256": paired.canonical_sha256(rows)},
            "workload": work, "client_source_sha256": put(artifacts, b"SYNTHETIC source fixture; not executable")}
    if state != "unloaded":
        scale = 0 if state == "loaded_zero" else 1
        plan["state"] = {"name": state, "profile_name": "synthetic-profile", "profile_sha256": "1" * 64,
                         "vector_sha256": "2" * 64, "scale_fp32_hex": "00000000" if scale == 0 else "3f800000",
                         "extra_body": {"steering_profile": "synthetic-profile", "steering_scale": scale}}
        request = json.loads(artifacts[work["request_template_sha256"]])
        request.update(plan["state"]["extra_body"])
        work["request_template_sha256"] = put(artifacts, request)
    plan_hash = paired.canonical_sha256(plan)
    hashes = []
    for slot in range(8):
        arm = paired.ORDERS[slot // 2][slot % 2]
        scale = scales[slot // 2] if arm == "DUAL" else 1.0
        boot_id, engine_id = f"boot-{slot}", f"engine-{slot}"
        admission = {"schema": "q03-performance-admission/1", "boot_id": boot_id,
                     "engine_id": engine_id, "window_id": "owner-window", "attempt_id": f"attempt-{slot}",
                     "started_at_ns": slot + 1, "arm": arm,
                     "identity": common, "state": plan["state"], "ready": True, "steady_guards": True, "cleanup": True,
                     "ranks": [config | {"container_id": f"container-{slot}-{i}"}
                               for i, config in enumerate(plan["arm_configs"][arm])]}
        admission_hash = put(artifacts, admission)
        records = []
        for i in range(count):
            sent = 1 + i * 4 * scale
            records.append({"index": i, "slot": 0, "burst": i, "prompt_id": str(i % 2),
                            "input_ids_sha256": paired.canonical_sha256(rows[i % 2]["prompt_token_ids"]),
                            "sent": sent, "first": sent + scale, "end": sent + 2 * scale,
                            "events": [[sent + scale, 1], [sent + 2 * scale, output]],
                            "prompt_tokens": 8, "completion_tokens": output,
                            "finish_reason": "length", "token_times": "exact", "error": None})
        bounds = [1000 * i / subwindows for i in range(subwindows + 1)] if kind == "sustained" else []
        positions = [-1] + bounds + [1001 if kind == "sustained" else records[-1]["end"] + 1]
        samples = []
        for j, at in enumerate(positions):
            finished = sum(r["end"] <= at for r in records)
            metrics = dict(paired.COUNTERS)
            values = {"finished": finished, "generation": output * finished, "preemptions": 0, "cache_hits": 0}
            if kind == "cold_prefill":
                metrics["cache_hits"] = "vllm:prefix_cache_hits_total"
            text = "".join(f'{name}{{model_name="synthetic-model",engine="0"}} {values[key]}\n'
                           for key, name in metrics.items())
            sample = {"at": at, "engine_id": engine_id, "raw_sha256": put(artifacts, text.encode()),
                      "binding": {"endpoint": common["metrics_endpoint"], "window_id": "owner-window",
                                  "attempt_id": f"attempt-{slot}", "boot_id": boot_id,
                                  "admission_sha256": admission_hash, "clock_id": "same-client-clock-domain",
                                  "preregistration_sha256": plan_hash, "slot": slot,
                                  "window": [0, 1000] if kind == "sustained" else None}}
            if kind == "sustained" and 0 < j < len(positions) - 1:
                sample["boundary"] = bounds[j - 1]
            samples.append(sample)
        run = {"schema": "q03-performance-run/1", "slot": slot, "block_id": plan["blocks"][slot // 2], "arm": arm,
               "preregistration_sha256": plan_hash, "identity": common, "state": plan["state"], "workload": work,
               "corpus": plan["corpus"], "boot": {"id": boot_id, "engine_id": engine_id,
                   "window_id": "owner-window", "attempt_id": f"attempt-{slot}",
                   "started_at_ns": slot + 1, "admission_sha256": admission_hash},
               "timing": {"clock": "time.monotonic", "clock_id": "same-client-clock-domain",
                          "source": "servebench.client.stream_chat", "source_sha256": plan["client_source_sha256"], "units": "seconds"},
               "records": records, "start": 0, "window": [0, 1000] if kind == "sustained" else None,
               "boundaries": bounds, "counter_samples": samples, "rejected": 0}
        hashes.append(put(artifacts, run))
    control = {"preregistration_sha256": plan_hash, "status": "stable",
               "evidence_sha256": [put(artifacts, b"SYNTHETIC control provenance, NOT verified OFF/OFF maps")]}
    receipts = {"run_sha256": hashes, "control_sha256": put(artifacts, control)}
    reledger(plan, receipts, artifacts)
    return plan, receipts, artifacts


def reledger(plan, receipts, artifacts):
    receipts["ledger_sha256"] = put(artifacts, {"preregistration_sha256": paired.canonical_sha256(plan),
        "attempts": [{"slot": i, "run_sha256": h, "outcome": "completed"}
                     for i, h in enumerate(receipts["run_sha256"])]})


def mutate_run(data, slot, operation):
    plan, receipts, artifacts = data
    run = json.loads(artifacts[receipts["run_sha256"][slot]])
    operation(run, artifacts)
    receipts["run_sha256"][slot] = put(artifacts, run)
    reledger(plan, receipts, artifacts)


def rebind(data):
    plan, receipts, artifacts = data
    plan_hash = paired.canonical_sha256(plan)
    for slot in range(8):
        def op(run, artifacts):
            run["preregistration_sha256"] = plan_hash
            for key in ("identity", "state", "workload", "corpus"):
                run[key] = plan[key]
            admission = json.loads(artifacts[run["boot"]["admission_sha256"]])
            admission.update(identity=plan["identity"], state=plan["state"])
            run["boot"]["admission_sha256"] = put(artifacts, admission)
            for sample in run["counter_samples"]:
                sample["binding"].update(preregistration_sha256=plan_hash,
                                         admission_sha256=run["boot"]["admission_sha256"])
        mutate_run(data, slot, op)
    control = json.loads(artifacts[receipts["control_sha256"]])
    control["preregistration_sha256"] = plan_hash
    receipts["control_sha256"] = put(artifacts, control)
    reledger(*data)


def change_counters(data, operation):
    def op(run, artifacts):
        for index, sample in enumerate(run["counter_samples"]):
            raw = operation(artifacts[sample["raw_sha256"]], index)
            sample["raw_sha256"] = put(artifacts, raw)
    mutate_run(data, 0, op)


class PairedPerformanceTests(unittest.TestCase):
    def result(self, data):
        return paired.evaluate(*data)

    def test_good_numeric_result_does_not_clear_external_control_prerequisite(self):
        result = self.result(fixture())
        self.assertEqual(result["statistical_status"], "NONINFERIOR")
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertEqual(result["prerequisites"][0]["code"], "CONTROL_ADMISSION_UNVERIFIED")
        self.assertEqual(len(result["runs"]), 8)
        for row in result["metrics"].values():
            self.assertEqual(row["n"], 4)
            self.assertEqual((row["df"], row["t95"]), (3, 3.182))

    def test_latency_ratio_and_burst_full_wall_throughput(self):
        data = fixture()
        result = self.result(data)
        self.assertAlmostEqual(result["metrics"]["ttft_p50_s"]["summary"]["mean"], 1)
        self.assertAlmostEqual(result["metrics"]["tpot_p99_s"]["summary"]["mean"], 1)
        self.assertAlmostEqual(result["runs"][0]["metrics"]["output_tps"], 400 / 398)
        self.assertAlmostEqual(result["metrics"]["output_tps"]["summary"]["mean"], 100 * (1 / 1.01 - 1))

    def test_unfavorable_pairs_retained_and_margin_not_equivalence(self):
        result = self.result(fixture(scales=(1, 1, 1, 1.2)))
        row = result["metrics"]["ttft_p50_s"]
        self.assertEqual(row["status"], "INCONCLUSIVE")
        self.assertEqual(row["n"], 4)
        self.assertAlmostEqual(row["pairs"][-1]["change_pct"], 20)
        self.assertAlmostEqual(row["summary"]["mean"], 5)
        self.assertAlmostEqual(row["summary"]["ci95"], 3.182 * 10 / 2)

    def test_99_requests_does_not_support_p99(self):
        result = self.result(fixture(count=99))
        self.assertEqual(result["metrics"]["ttft_p50_s"]["status"], "NONINFERIOR")
        for name in ("ttft_p99_s", "tpot_p99_s"):
            self.assertEqual(result["metrics"][name]["status"], "INCONCLUSIVE")
            self.assertTrue(all(pair["change_pct"] is not None for pair in result["metrics"][name]["pairs"]))
        self.assertEqual(result["statistical_status"], "INCONCLUSIVE")

    def test_subwindows_do_not_inflate_boot_n(self):
        for windows in (1, 3, 12):
            result = self.result(fixture(kind="sustained", subwindows=windows))
            self.assertEqual(result["statistical_status"], "NONINFERIOR", result)
            self.assertTrue(all(row["n"] == 4 for row in result["metrics"].values()))

    def test_cold_prefill_uses_actual_input_over_ttft_not_requested_size(self):
        result = self.result(fixture(kind="cold_prefill", count=2))
        self.assertEqual(set(result["metrics"]), {"cold_prefill_tps", "ttft_mean_s"})
        self.assertAlmostEqual(result["runs"][0]["metrics"]["cold_prefill_tps"], 8)
        self.assertAlmostEqual(result["metrics"]["cold_prefill_tps"]["summary"]["mean"], 100 * (1 / 1.01 - 1))
        self.assertEqual(result["statistical_status"], "NONINFERIOR")

    def test_missing_and_nonfinite_timings_never_become_zero(self):
        for field, value in (("first", None), ("end", math.inf), ("prompt_tokens", None), ("completion_tokens", 0)):
            data = fixture()
            mutate_run(data, 0, lambda run, artifacts: run["records"][0].__setitem__(field, value))
            result = self.result(data)
            self.assertEqual(result["statistical_status"], "INCONCLUSIVE")
            self.assertEqual(result["runs"][0]["metrics"], {})
            self.assertIsNone(result["metrics"]["output_tps"]["pairs"][0]["change_pct"])

    def test_complete_identity_including_request_extra_body(self):
        for field in ("identity", "state", "workload", "corpus"):
            data = fixture()
            mutate_run(data, 0, lambda run, artifacts: run[field].update(unregistered=True))
            self.assertTrue(self.result(data)["runs"][0]["issues"])
        data = fixture()
        mutate_run(data, 0, lambda run, artifacts: run["state"]["extra_body"].update(scale=1))
        self.assertTrue(self.result(data)["runs"][0]["issues"])

    def test_missing_fields_and_bool_are_not_counts(self):
        operations = [lambda run, _: run.pop("timing"), lambda run, _: run["records"][0].pop("events"),
                      lambda run, _: run["records"][0].__setitem__("prompt_tokens", True),
                      lambda run, _: run.__setitem__("rejected", False)]
        for op in operations:
            data = fixture()
            mutate_run(data, 0, op)
            self.assertTrue(self.result(data)["runs"][0]["issues"])

    def test_missing_or_tampered_artifact(self):
        data = fixture()
        data[2][data[1]["run_sha256"][0]] = b"{}"
        self.assertIn("hash mismatch", self.result(data)["runs"][0]["issues"][0])
        data = fixture()
        del data[2][data[1]["run_sha256"][0]]
        self.assertIn("missing artifact", self.result(data)["runs"][0]["issues"][0])

    def test_bad_request_is_not_removed(self):
        for op in (lambda run, _: run["records"][0].__setitem__("error", "OOM"),
                   lambda run, _: run["records"].pop(0),
                   lambda run, _: run["records"][0].__setitem__("index", 1),
                   lambda run, _: run["records"][0].__setitem__("input_ids_sha256", "f" * 64),
                   lambda run, _: run["records"][0].__setitem__("token_times", "approximate")):
            data = fixture()
            mutate_run(data, 0, op)
            result = self.result(data)
            self.assertTrue(result["runs"][0]["issues"])
            self.assertEqual(len(result["runs"]), 8)
            self.assertEqual(len(result["metrics"]["output_tps"]["pairs"]), 4)

    def test_wrong_order_and_extra_retry(self):
        data = fixture()
        data[1]["run_sha256"][0:2] = reversed(data[1]["run_sha256"][0:2])
        reledger(*data)
        self.assertTrue(self.result(data)["runs"][0]["issues"])
        data = fixture()
        ledger = json.loads(data[2][data[1]["ledger_sha256"]])
        ledger["attempts"].append({"slot": 8, "outcome": "failed", "run_sha256": "f" * 64})
        data[1]["ledger_sha256"] = put(data[2], ledger)
        self.assertIn("do not select retries", self.result(data)["issues"][0])

    def test_reused_boot_and_rank_config_mismatch(self):
        for field in ("id", "engine_id"):
            data = fixture()
            def op(run, artifacts):
                run["boot"][field] = "boot-0" if field == "id" else "engine-0"
                admission = json.loads(artifacts[run["boot"]["admission_sha256"]])
                admission["boot_id" if field == "id" else field] = run["boot"][field]
                run["boot"]["admission_sha256"] = put(artifacts, admission)
                for sample in run["counter_samples"]:
                    sample["binding"].update(boot_id=run["boot"]["id"], admission_sha256=run["boot"]["admission_sha256"])
                if field == "engine_id":
                    for sample in run["counter_samples"]:
                        sample["engine_id"] = "engine-0"
                        raw = artifacts[sample["raw_sha256"]].replace(b"engine-1", b"engine-0")
                        sample["raw_sha256"] = put(artifacts, raw)
            mutate_run(data, 1, op)
            self.assertTrue(any("reused" in issue for issue in self.result(data)["issues"]))
        data = fixture()
        def mismatch(run, artifacts):
            admission = json.loads(artifacts[run["boot"]["admission_sha256"]])
            admission["ranks"][3]["runner_config_sha256"] = "f" * 64
            run["boot"]["admission_sha256"] = put(artifacts, admission)
        mutate_run(data, 0, mismatch)
        self.assertIn("configuration mismatch", self.result(data)["runs"][0]["issues"][0])

    def test_counter_reset_missing_nonfinite_foreign_and_preemption(self):
        changes = [lambda raw: raw.replace(b" 100\n", b" 0\n"),
                   lambda raw: b"\n".join(raw.splitlines()[1:]),
                   lambda raw: raw.replace(b" 100\n", b" NaN\n"),
                   lambda raw: raw.replace(b'engine="0"', b'engine="foreign-engine"'),
                   lambda raw: raw.replace(b'engine="0"} 0\n', b'engine="0"} 1\n')]
        for change in changes:
            data = fixture()
            def op(run, artifacts):
                sample = run["counter_samples"][-1]
                sample["raw_sha256"] = put(artifacts, change(artifacts[sample["raw_sha256"]]))
            mutate_run(data, 0, op)
            self.assertTrue(self.result(data)["runs"][0]["issues"])

    def test_internal_counter_reset_cannot_hide_between_valid_endpoints(self):
        data = fixture(kind="sustained")
        def op(run, artifacts):
            sample = run["counter_samples"][-2]
            raw = artifacts[sample["raw_sha256"]].replace(b" 100\n", b" 0\n")
            sample["raw_sha256"] = put(artifacts, raw)
        mutate_run(data, 0, op)
        self.assertIn("counter reset", self.result(data)["runs"][0]["issues"][0])

    def test_missing_boundary_scrape(self):
        data = fixture(kind="sustained")
        mutate_run(data, 0, lambda run, _: run["counter_samples"].pop(2))
        self.assertIn("subwindow raw scrapes", self.result(data)["runs"][0]["issues"][0])

    def test_unstable_control_and_missing_control_provenance(self):
        for status in ("unstable", None):
            data = fixture()
            control = json.loads(data[2][data[1]["control_sha256"]])
            control["status"] = status
            data[1]["control_sha256"] = put(data[2], control)
            self.assertIn("unstable control", self.result(data)["issues"][0])

    def test_cold_cache_hits_and_inexact_inputs_are_inconclusive(self):
        data = fixture(kind="cold_prefill", count=2)
        def op(run, artifacts):
            sample = run["counter_samples"][-1]
            raw = artifacts[sample["raw_sha256"]].replace(
                b'vllm:prefix_cache_hits_total{model_name="synthetic-model",engine="0"} 0',
                b'vllm:prefix_cache_hits_total{model_name="synthetic-model",engine="0"} 1')
            sample["raw_sha256"] = put(artifacts, raw)
        mutate_run(data, 0, op)
        self.assertIn("cache hits", self.result(data)["runs"][0]["issues"][0])
        data = fixture(kind="cold_prefill", count=2)
        mutate_run(data, 0, lambda run, _: run["records"][0].__setitem__("prompt_tokens", 7))
        self.assertTrue(self.result(data)["runs"][0]["issues"])

    def test_input_completeness_and_noisy_state_are_not_inferred_from_seed(self):
        data = fixture()
        mutate_run(data, 0, lambda run, _: run["records"].__setitem__(0, dict(run["records"][0], prompt_id="unknown")))
        self.assertIn("corpus identity", self.result(data)["runs"][0]["issues"][0])
        data = fixture()
        del data[0]["state"]["profile_name"]
        self.assertTrue(self.result(data)["issues"])
        data = fixture()
        data[0]["state"]["scale_fp32_hex"] = "7fc00000"
        self.assertIn("nonfinite FP32", self.result(data)["issues"][0])

    def test_frozen_configs_are_complete_artifact_bytes(self):
        data = fixture()
        del data[2][data[0]["arm_configs"]["OFF"][0]["worker_config_sha256"]]
        self.assertIn("missing artifact", self.result(data)["issues"][0])
        data = fixture()
        digest = put(data[2], {"only_one_field": "not a complete observed config"})
        data[0]["arm_configs"]["OFF"][0]["worker_config_sha256"] = digest
        self.assertIn("58-field", self.result(data)["issues"][0])

    def test_burst_inventory_and_actual_boundary_clock(self):
        data = fixture()
        mutate_run(data, 0, lambda run, _: run["records"][1].__setitem__("burst", 0))
        self.assertIn("burst request slot", self.result(data)["runs"][0]["issues"][0])
        data = fixture(kind="sustained")
        mutate_run(data, 0, lambda run, _: run["counter_samples"][2].__setitem__("at", 100))
        self.assertIn("precedes", self.result(data)["runs"][0]["issues"][0])

    def test_duplicate_json_keys_are_not_last_write_wins(self):
        data = fixture()
        digest = put(data[2], b'{"slot":0,"slot":1}')
        data[1]["run_sha256"][0] = digest
        reledger(*data)
        self.assertIn("duplicate JSON key", self.result(data)["runs"][0]["issues"][0])

    def test_all_three_states_bind_actual_profile_and_scale_request(self):
        for state in ("unloaded", "loaded_zero", "active"):
            data = fixture(state=state)
            self.assertEqual(self.result(data)["statistical_status"], "NONINFERIOR")
        data = fixture(state="active")
        data[0]["state"]["extra_body"]["steering_scale"] = 0
        self.assertIn("FP32 scale differs", self.result(data)["issues"][0])

    def test_missing_collector_source_and_wrong_request_template(self):
        data = fixture()
        del data[2][data[0]["client_source_sha256"]]
        self.assertIn("missing artifact", self.result(data)["issues"][0])
        data = fixture()
        request = json.loads(data[2][data[0]["workload"]["request_template_sha256"]])
        request["seed"] = 42
        data[0]["workload"]["request_template_sha256"] = put(data[2], request)
        self.assertIn("request template differs", self.result(data)["issues"][0])

    def test_native_counter_lexemes_are_not_python_float_literals(self):
        for value in (b"1_00", b"0x1.9p6", b"100.000000000000000001", b"NaN", b"+Inf",
                      b"1e999999999999999999999", b"1e309", b"1e-999999999999999999999"):
            with self.subTest(value=value):
                data = fixture()
                change_counters(data, lambda raw, _: raw.replace(b" 100\n", b" " + value + b"\n"))
                self.assertEqual(self.result(data)["statistical_status"], "INCONCLUSIVE")

    def test_tiny_counter_increment_does_not_underflow_to_zero(self):
        for value in (b"1e-400", b"-1e-400", b"0.0000000000000000000000000000000001"):
            data = fixture()
            change_counters(data, lambda raw, i: raw.replace(b" 0\n", b" " + value + b"\n") if i else raw)
            self.assertEqual(self.result(data)["statistical_status"], "INCONCLUSIVE")

    def test_exact_decimal_counter_differences_above_53_bits_and_context_precision(self):
        for baseline in (2 ** 53 + 1, 10 ** 80 + 1, 10 ** 300 + 1):
            data = fixture()
            def transform(raw, _):
                rows = []
                for line in raw.decode().splitlines():
                    name, value = line.rsplit(" ", 1)
                    rows.append(f"{name} {baseline + int(value)}\n")
                return "".join(rows).encode()
            change_counters(data, transform)
            self.assertEqual(self.result(data)["statistical_status"], "NONINFERIOR")
            data = fixture()
            def deficit(raw, i):
                return transform(raw, i).replace(str(baseline + 100).encode(), str(baseline + 99).encode()) if i else transform(raw, i)
            change_counters(data, deficit)
            self.assertEqual(self.result(data)["statistical_status"], "INCONCLUSIVE")

    def test_exact_decimal_catches_reset_hidden_by_binary_rounding(self):
        data = fixture()
        def transform(raw, i):
            if i == 0:
                return raw.replace(b" 0\n", b" 9007199254740993\n")
            return raw.replace(b" 100\n", b" 9007199254741092\n").replace(
                b" 400\n", b" 9007199254741392\n").replace(b" 0\n", b" 9007199254740992\n")
        change_counters(data, transform)
        self.assertIn("reset", self.result(data)["runs"][0]["issues"][0])

    def test_valid_native_scientific_counts_and_timestamp_remain_supported(self):
        data = fixture()
        change_counters(data, lambda raw, _: raw.replace(b" 100\n", b" +1.00e2\n").replace(
            b" 400\n", b" 4E+2\n").replace(b" 0\n", b" 0e400\n").replace(b"\n", b" 1234567890\n"))
        self.assertEqual(self.result(data)["statistical_status"], "NONINFERIOR")

    def test_native_optional_timestamps_are_validated(self):
        for timestamp in (b"not-a-timestamp", b"1_000", b"1.5", b"1e3", b"9223372036854775808"):
            data = fixture()
            change_counters(data, lambda raw, _: raw.replace(b"\n", b" " + timestamp + b"\n"))
            self.assertIn("timestamp", self.result(data)["runs"][0]["issues"][0])

    def test_only_native_label_escapes_are_decoded(self):
        data = fixture()
        change_counters(data, lambda raw, _: raw.replace(b'synthetic-model', b'synthetic\\u002dmodel'))
        self.assertIn("native metric labels", self.result(data)["runs"][0]["issues"][0])
        raw = b'count{model_name="name\\n\\\"\\\\tail",engine="engine"} 1\n'
        values = paired._counter_series(raw, {"count": "count"}, {"model_name": 'name\n"\\tail', "engine": "engine"})
        self.assertEqual(list(values.values()), [1])

    def test_ledger_and_admission_booleans_are_not_integer_identities(self):
        data = fixture()
        ledger = json.loads(data[2][data[1]["ledger_sha256"]])
        ledger["attempts"][0]["slot"] = False
        ledger["attempts"][1]["slot"] = True
        data[1]["ledger_sha256"] = put(data[2], ledger)
        self.assertEqual(self.result(data)["statistical_status"], "INCONCLUSIVE")
        data = fixture()
        def op(run, artifacts):
            admission = json.loads(artifacts[run["boot"]["admission_sha256"]])
            admission["started_at_ns"] = True
            run["boot"]["admission_sha256"] = put(artifacts, admission)
        mutate_run(data, 0, op)
        self.assertIn("admission identity", self.result(data)["runs"][0]["issues"][0])
        data = fixture(kind="sustained")
        mutate_run(data, 0, lambda run, _: run["counter_samples"][1].__setitem__("boundary", False))
        self.assertEqual(self.result(data)["statistical_status"], "INCONCLUSIVE")

    def test_unloaded_request_rejects_unregistered_direct_or_nested_controls(self):
        for controls in ({"steering_profile": "other", "steering_scale": 1},
                         {"steering_profile": None}, {"steering_scale": 0},
                         {"vllm_xargs": {"steering_scale": 1}}, {"vllm_xargs": {"steering_profile": "other"}}):
            data = fixture()
            request = json.loads(data[2][data[0]["workload"]["request_template_sha256"]])
            request.update(controls)
            data[0]["workload"]["request_template_sha256"] = put(data[2], request)
            rebind(data)
            self.assertIn("steering controls differ", self.result(data)["issues"][0])

    def test_frozen_nested_controls_are_checked_without_guessing_precedence(self):
        for state in ("active", "loaded_zero"):
            data = fixture(state=state)
            registered = data[0]["state"]["extra_body"]
            registered["vllm_xargs"] = {"steering_profile": registered["steering_profile"],
                                        "steering_scale": registered["steering_scale"]}
            request = json.loads(data[2][data[0]["workload"]["request_template_sha256"]])
            request.update(registered)
            data[0]["workload"]["request_template_sha256"] = put(data[2], request)
            rebind(data)
            self.assertEqual(self.result(data)["statistical_status"], "NONINFERIOR")
        for value in (0, True, None, "1"):
            data = fixture(state="active")
            data[0]["state"]["extra_body"]["vllm_xargs"] = {"steering_scale": value}
            self.assertIn("steering scale location", self.result(data)["issues"][0])
        data = fixture(state="active")
        request = json.loads(data[2][data[0]["workload"]["request_template_sha256"]])
        request["steering_scale"] = True
        data[0]["workload"]["request_template_sha256"] = put(data[2], request)
        self.assertIn("steering controls differ", self.result(data)["issues"][0])

    def test_no_blanket_rejection_of_unrelated_nested_request_fields(self):
        data = fixture()
        request = json.loads(data[2][data[0]["workload"]["request_template_sha256"]])
        request["vllm_xargs"] = {"unrelated_frozen_setting": "synthetic"}
        data[0]["workload"]["request_template_sha256"] = put(data[2], request)
        rebind(data)
        self.assertEqual(self.result(data)["statistical_status"], "NONINFERIOR")

    def test_collapsed_observed_window_is_inconclusive_not_zero_division(self):
        data = fixture(kind="sustained")
        data[0]["workload"].update(warmup_s=1000, duration_s=1e-10)
        rebind(data)
        for slot in range(8):
            mutate_run(data, slot, lambda run, _: run.update(window=[1000, 1000], boundaries=[1000] * 4))
        result = self.result(data)
        self.assertEqual(result["statistical_status"], "INCONCLUSIVE")
        self.assertTrue(all("nonpositive observed measurement span" in run["issues"] for run in result["runs"]))

    def test_tiny_duration_mismatch_not_hidden_by_absolute_tolerance(self):
        data = fixture(kind="sustained")
        data[0]["workload"].update(warmup_s=1000, duration_s=1e-8)
        rebind(data)
        mutate_run(data, 0, lambda run, _: run.update(window=[1000, 1000 + 1.05e-8]))
        self.assertIn("duration/warmup mismatch", self.result(data)["runs"][0]["issues"][0])

    def test_raw_json_nonzero_underflow_is_not_silently_zero(self):
        for raw in (b'{"number":1e-400}', b'{"number":-1e-400}', b'{"number":1e400}'):
            with self.assertRaises(paired.InvalidReceipt):
                paired._json(raw)

    def test_native_engine_index_can_repeat_across_distinct_boots(self):
        result = self.result(fixture())
        self.assertEqual(result["statistical_status"], "NONINFERIOR")
        self.assertEqual({r["metric_labels"]["engine"] for r in result["runs"]}, {"0"})
        self.assertEqual(len({r["boot_id"] for r in result["runs"]}), 8)
        self.assertEqual(len({r["engine_id"] for r in result["runs"]}), 8)

    def test_stale_endpoint_and_wrong_boot_scrape_binding_are_rejected(self):
        for key, value in (("endpoint", "http://stale.invalid/metrics"), ("boot_id", "old-boot"),
                           ("attempt_id", "old-attempt"), ("window_id", "old-window"),
                           ("admission_sha256", "f" * 64), ("slot", False),
                           ("clock_id", "other-clock"), ("window", [1, 2])):
            data = fixture()
            mutate_run(data, 0, lambda run, _: run["counter_samples"][0]["binding"].__setitem__(key, value))
            self.assertIn("binding mismatch", self.result(data)["runs"][0]["issues"][0])
        data = fixture()
        first = json.loads(data[2][data[1]["run_sha256"][0]])
        mutate_run(data, 1, lambda run, _: run.__setitem__("counter_samples", first["counter_samples"]))
        self.assertEqual(self.result(data)["statistical_status"], "INCONCLUSIVE")

    def test_inputs_not_mutated_and_result_is_json(self):
        data = fixture()
        before = copy.deepcopy(data)
        result = self.result(data)
        self.assertEqual(data, before)
        json.dumps(result, allow_nan=False)

    def test_invalid_top_level_values_are_inconclusive(self):
        for values in ((None, {}, {}), ({}, {}, {}), ({}, None, None)):
            self.assertEqual(paired.evaluate(*values)["status"], "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
