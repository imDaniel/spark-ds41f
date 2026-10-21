"""All captures here are CPU-only synthetic fixtures, never real Q03 evidence."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "scripts/q03_capacity_receipt.py"
spec = importlib.util.spec_from_file_location("q03_capacity_receipt", MODULE)
q = importlib.util.module_from_spec(spec)
spec.loader.exec_module(q)


class Fixture:
    def __init__(self, root, case="four_64k"):
        self.root = Path(root)
        self.case = case
        self.count, self.inputs, self.outputs = q.CASES[case]
        self.binding = dict(q.FROZEN, engine_id="synthetic-engine", boot_id="synthetic-boot",
                            model_name="synthetic-model", endpoint="http://synthetic.invalid/v1",
                            resources=copy.deepcopy(q.RESOURCES),
                            state={"mode": "off", "steering": "unloaded", "profile": None,
                                   "profile_sha256": None, "scale": None})
        # Actual hashes of synthetic identity bytes, explicitly not production identities.
        for name in ("model_assets_sha256", "tokenizer_assets_sha256", "request_semantics_sha256"):
            self.binding[name] = hashlib.sha256(("synthetic-" + name).encode()).hexdigest()
        corpus = [{"request_id": "r" + str(i), "prompt_token_ids": [i] * self.inputs} for i in range(self.count)]
        self.clock = {"clock_id": "synthetic-clock", "source_id": "synthetic-collector-origin",
                      "domain": "monotonic", "unit": "ns"}
        self.binding["clock"] = self.clock
        self.expected = {"schema": "q03.capacity.expected.v1", "case": case,
                         "clock_source": self.write("clock.json", self.clock),
                         "binding": self.binding,
                         "metric_labels": {"model_name": "synthetic-model", "engine": "0"},
                         "corpus": self.write("corpus.jsonl", corpus, jsonl=True)}
        self.rows = [dict(row, binding=self.binding, output_token_ids=[7] * self.outputs,
                          prompt_tokens=self.inputs, completion_tokens=self.outputs,
                          status_code=200, finish_reason="length", error=None, truncated=False,
                          sent_ns=20 + i, end_ns=70 + i) for i, row in enumerate(corpus)]
        self.receipt = {"schema": "q03.capacity.receipt.v1", "case": case,
                        "requests": self.write("requests.jsonl", self.rows, jsonl=True)}
        self.samples = []
        for i, (when, completed, running) in enumerate(((10, 0, 0), (40, 0, self.count), (80, self.count, 0))):
            self.samples.append({"observed_ns": when, "binding": self.binding,
                                 "source": self.write("metrics-" + str(i) + ".prom", self.metrics(completed, running), raw=True)})
        self.counter_capture = {"binding": self.binding, "samples": self.samples}
        self.receipt["counters"] = self.write("counters.json", self.counter_capture)
        self.rank_rows = []
        for rank in range(4):
            self.rank_rows.append({"rank": rank, "node_id": "synthetic-node-" + str(rank), "binding": self.binding,
                 "startup": {"observed_ns": 5, "guard_floor_bytes": q.STARTUP_FLOOR,
                             "free_bytes": q.STARTUP_FLOOR, "passed": True},
                 "steady": {"start_ns": 8, "end_ns": 90, "complete": True,
                            "guard_floor_bytes": q.STEADY_FLOOR, "min_free_bytes": q.STEADY_FLOOR,
                            "swap_in_bytes": 0, "swap_out_bytes": 0,
                            "guard_halts": 0, "oom_events": 0, "allocation_retries": 0,
                            "driver_errors": 0, "errors": 0, "engine_restarts": 0},
                 "drain": {"observed_ns": 100, "daemon_active": 0, "engine_processes": 0,
                           "gpu_processes": 0, "pending_epochs": 0, "guard_processes": 0,
                           "stopped": True, "quiescent": True, "protected_inputs_unchanged": True,
                           "policy": "keep-stopped", "owned_ids": []}})
        self.save_ranks()

    def write(self, name, value, jsonl=False, raw=False):
        if raw:
            data = value.encode()
        elif jsonl:
            data = ("\n".join(json.dumps(row, separators=(",", ":")) for row in value) + "\n").encode()
        else:
            data = json.dumps(value, separators=(",", ":")).encode()
        (self.root / name).write_bytes(data)
        return {"path": name, "sha256": hashlib.sha256(data).hexdigest()}

    def metrics(self, completed, running, preempt=0, errors=2, model="synthetic-model", engine="0"):
        labels = 'model_name="' + model + '",engine="' + engine + '"'
        return (f'{q.SUCCESS}{{{labels},finished_reason="length"}} {completed}\n'
                f'{q.SUCCESS}{{{labels},finished_reason="error"}} {errors}\n'
                f'{q.PREEMPT}{{{labels}}} {preempt}\n'
                f'{q.RUNNING}{{{labels}}} {running}\n'
                f'{q.WAITING}{{{labels}}} 0\n')

    def change_metrics(self, index, text):
        self.samples[index]["source"] = self.write("metrics-" + str(index) + ".prom", text, raw=True)
        self.receipt["counters"] = self.write("counters.json", self.counter_capture)

    def save_requests(self):
        self.receipt["requests"] = self.write("requests.jsonl", self.rows, jsonl=True)

    def save_ranks(self):
        self.receipt["ranks"] = [self.write("rank-" + str(i) + ".json", row) for i, row in enumerate(self.rank_rows)]

    def validate(self):
        return q.validate_capacity_receipt(self.receipt, expected=self.expected, evidence_root=self.root)


class CapacityContractTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.f = Fixture(self.temp.name)

    def assert_result(self, status, code=None):
        result = self.f.validate()
        self.assertEqual(status, result["status"], result)
        if code:
            self.assertIn(code, {x["code"] for x in result["findings"]}, result)
        return result

    def test_all_three_exact_cases(self):
        for case in q.CASES:
            with self.subTest(case=case):
                self.f = Fixture(self.temp.name, case)
                result = self.assert_result("PASS")
                self.assertEqual("UNKNOWN", result["claims"]["full_concurrent_long_context_residency"])
                self.assertEqual("NOT_ESTABLISHED", result["claims"]["four_1m_or_sixteen_1m_capacity"])

    def test_historical_nonzero_error_counter_not_new_failure(self):
        self.assert_result("PASS")

    def test_deterministic_output(self):
        self.assertEqual(self.f.validate(), self.f.validate())

    def test_missing_receipt_source_never_passes(self):
        del self.f.receipt["requests"]
        self.assert_result("INCONCLUSIVE", "UNPROVEN_EVIDENCE")

    def test_missing_source_file(self):
        (self.f.root / "rank-3.json").unlink()
        self.assert_result("INCONCLUSIVE", "UNPROVEN_EVIDENCE")

    def test_hash_mismatch_not_verified_or_repaired(self):
        (self.f.root / "rank-3.json").write_text("{}")
        result = self.assert_result("INCONCLUSIVE", "UNPROVEN_EVIDENCE")
        self.assertNotIn("rank-3.json", result["verified_source_sha256"])

    def test_absent_source_hash_not_fabricated(self):
        del self.f.receipt["requests"]["sha256"]
        result = self.assert_result("INCONCLUSIVE")
        self.assertNotIn("requests.jsonl", result["verified_source_sha256"])

    def test_path_escape_and_symlink_escape(self):
        self.f.receipt["requests"]["path"] = "../outside.jsonl"
        self.assert_result("INCONCLUSIVE")
        (self.f.root / "escape").symlink_to("/tmp")
        self.f.receipt["requests"]["path"] = "escape/outside.jsonl"
        self.assert_result("INCONCLUSIVE")

    def test_summary_success_boolean_is_not_evidence(self):
        self.f.receipt = {"schema": "q03.capacity.receipt.v1", "case": "four_64k", "success": True,
                          "requests": {"success": True}}
        self.assert_result("INCONCLUSIVE")

    def test_duplicate_request_id(self):
        self.f.rows[1]["request_id"] = "r0"
        self.f.save_requests()
        self.assert_result("FAIL", "DUPLICATE_REQUEST")

    def test_missing_request(self):
        self.f.rows.pop()
        self.f.save_requests()
        self.assert_result("FAIL", "REQUEST_COUNT")

    def test_unexpected_request(self):
        self.f.rows[0]["request_id"] = "extra"
        self.f.save_requests()
        self.assert_result("FAIL", "REQUEST_ID_COVERAGE")

    def test_nominal_usage_cannot_hide_truncated_ids(self):
        self.f.rows[0]["output_token_ids"].pop()
        self.f.save_requests()
        self.assert_result("FAIL", "TOKEN_LENGTH")

    def test_equal_lengths_do_not_hide_input_changes(self):
        self.f.rows[0]["prompt_token_ids"][20] = 77
        self.f.save_requests()
        self.assert_result("FAIL", "INPUT_MISMATCH")

    def test_boolean_token_id_is_not_integer(self):
        self.f.rows[0]["output_token_ids"][0] = True
        self.f.save_requests()
        self.assert_result("INCONCLUSIVE")

    def test_boolean_usage_is_not_integer(self):
        self.f.rows[0]["completion_tokens"] = True
        self.f.save_requests()
        self.assert_result("INCONCLUSIVE")

    def test_numeric_truncation_is_not_boolean(self):
        self.f.rows[0]["truncated"] = 0
        self.f.save_requests()
        self.assert_result("INCONCLUSIVE")

    def test_finish_reason_stop_is_failure(self):
        self.f.rows[0]["finish_reason"] = "stop"
        self.f.save_requests()
        self.assert_result("FAIL", "REQUEST_ERROR")

    def test_success_status_does_not_erase_error(self):
        self.f.rows[0]["error"] = "OOM"
        self.f.save_requests()
        self.assert_result("FAIL", "REQUEST_ERROR")

    def test_truncation_flag(self):
        self.f.rows[0]["truncated"] = True
        self.f.save_requests()
        self.assert_result("FAIL", "TRUNCATION")

    def test_changed_actual_binding_fields(self):
        for key, value in (("engine_id", "other"), ("boot_id", "new-boot"),
                           ("image_digest", "sha256:" + "f" * 64), ("state", {"mode": "dual"}),
                           ("resources", dict(q.RESOURCES, max_num_seqs=True))):
            with self.subTest(key=key):
                original = self.f.rows[0]["binding"]
                self.f.rows[0]["binding"] = dict(original, **{key: value})
                self.f.save_requests()
                self.assert_result("FAIL", "BINDING_MISMATCH")
                self.f.rows[0]["binding"] = original

    def test_expected_reduced_resources_is_not_valid_freeze(self):
        self.f.expected["binding"]["resources"]["kv_bytes_per_rank"] -= 1
        self.assert_result("INCONCLUSIVE")

    def test_duplicate_frozen_prompts_not_accepted(self):
        corpus = [{"request_id": "r" + str(i), "prompt_token_ids": [0] * self.f.inputs} for i in range(4)]
        self.f.expected["corpus"] = self.f.write("corpus.jsonl", corpus, jsonl=True)
        self.assert_result("INCONCLUSIVE")

    def test_no_running4_is_unknown_not_failure_or_full_capacity(self):
        self.f.change_metrics(1, self.f.metrics(0, 3))
        result = self.assert_result("INCONCLUSIVE", "CONCURRENT_ADMISSION_UNPROVEN")
        self.assertEqual("UNKNOWN", result["claims"]["observed_concurrent_admission"])

    def test_running4_outside_interval_does_not_count(self):
        self.f.samples[1]["observed_ns"] = 15
        self.f.receipt["counters"] = self.f.write("counters.json", self.f.counter_capture)
        self.assert_result("INCONCLUSIVE", "CONCURRENT_ADMISSION_UNPROVEN")

    def test_disjoint_request_intervals_do_not_prove_admission(self):
        for i, row in enumerate(self.f.rows):
            row["sent_ns"] = 20 + 12 * i
            row["end_ns"] = 25 + 12 * i
        self.f.save_requests()
        result = self.assert_result("INCONCLUSIVE", "CONCURRENT_ADMISSION_UNPROVEN")
        self.assertEqual("UNKNOWN", result["claims"]["observed_concurrent_admission"])

    def test_partial_overlap_does_not_prove_all_four_admission(self):
        self.f.rows[3]["sent_ns"] = 45
        self.f.save_requests()
        result = self.assert_result("INCONCLUSIVE", "CONCURRENT_ADMISSION_UNPROVEN")
        self.assertEqual("UNKNOWN", result["claims"]["observed_concurrent_admission"])

    def test_sample_on_request_send_or_end_boundary_is_ambiguous(self):
        for key in ("sent_ns", "end_ns"):
            with self.subTest(key=key):
                original = self.f.rows[3][key]
                self.f.rows[3][key] = 40
                self.f.save_requests()
                self.assert_result("INCONCLUSIVE", "CONCURRENT_ADMISSION_UNPROVEN")
                self.f.rows[3][key] = original

    def test_reversed_request_interval_is_not_observed_admission(self):
        self.f.rows[3]["sent_ns"] = 50
        self.f.rows[3]["end_ns"] = 30
        self.f.save_requests()
        result = self.assert_result("FAIL", "REQUEST_TIME")
        self.assertEqual("UNKNOWN", result["claims"]["observed_concurrent_admission"])

    def test_incomplete_requests_cannot_claim_observed_admission(self):
        self.f.rows.pop()
        self.f.save_requests()
        result = self.assert_result("FAIL", "REQUEST_COUNT")
        self.assertEqual("UNKNOWN", result["claims"]["observed_concurrent_admission"])

    def test_null_request_binding_cannot_pass_single1m(self):
        self.f = Fixture(self.temp.name, "single_1m")
        self.f.rows[0]["binding"] = None
        self.f.save_requests()
        self.assert_result("FAIL", "BINDING_MISMATCH")

    def test_missing_clock_source_is_inconclusive(self):
        del self.f.expected["clock_source"]
        self.assert_result("INCONCLUSIVE")

    def test_clock_source_hash_mismatch_is_inconclusive(self):
        (self.f.root / "clock.json").write_text("{}")
        self.assert_result("INCONCLUSIVE")

    def test_unbound_clock_unit_is_inconclusive(self):
        self.f.clock["unit"] = "ms"
        self.f.expected["clock_source"] = self.f.write("clock.json", self.f.clock)
        self.assert_result("INCONCLUSIVE")

    def test_cross_clock_request_binding_fails(self):
        self.f.rows[0]["binding"] = dict(self.f.binding, clock=dict(self.f.clock, clock_id="foreign-clock"))
        self.f.save_requests()
        result = self.assert_result("FAIL", "BINDING_MISMATCH")
        self.assertEqual("UNKNOWN", result["claims"]["observed_concurrent_admission"])

    def test_cross_clock_scrape_binding_not_observed(self):
        self.f.samples[1]["binding"] = dict(self.f.binding, clock=dict(self.f.clock, clock_id="foreign-clock"))
        self.f.receipt["counters"] = self.f.write("counters.json", self.f.counter_capture)
        result = self.assert_result("FAIL", "BINDING_MISMATCH")
        self.assertEqual("UNKNOWN", result["claims"]["observed_concurrent_admission"])

    def test_boolean_or_float_observation_time_is_inconclusive(self):
        for value in (True, 40.0, "40", None):
            with self.subTest(value=value):
                self.f.samples[1]["observed_ns"] = value
                self.f.receipt["counters"] = self.f.write("counters.json", self.f.counter_capture)
                self.assert_result("INCONCLUSIVE")

    def test_nonfinite_observation_time_is_inconclusive(self):
        self.f.samples[1]["observed_ns"] = float("inf")
        self.f.receipt["counters"] = self.f.write("counters.json", self.f.counter_capture)
        self.assert_result("INCONCLUSIVE")

    def test_wrong_model_and_engine_labels(self):
        for model, engine in (("other", "0"), ("synthetic-model", "1")):
            self.f.change_metrics(1, self.f.metrics(0, 4, model=model, engine=engine))
            self.assert_result("INCONCLUSIVE")

    def test_unlabeled_counter_does_not_become_zero(self):
        self.f.change_metrics(0, self.f.metrics(0, 0).replace('model_name="synthetic-model",', ""))
        self.assert_result("INCONCLUSIVE")

    def test_sample_engine_boot_mismatch(self):
        self.f.samples[1]["binding"] = dict(self.f.binding, boot_id="other")
        self.f.receipt["counters"] = self.f.write("counters.json", self.f.counter_capture)
        self.assert_result("FAIL", "BINDING_MISMATCH")

    def test_missing_counter_not_zero(self):
        self.f.change_metrics(0, "\n".join(x for x in self.f.metrics(0, 0).splitlines() if not x.startswith(q.PREEMPT)))
        self.assert_result("INCONCLUSIVE")

    def test_disappearing_error_series_not_zero(self):
        self.f.change_metrics(2, "\n".join(x for x in self.f.metrics(4, 0).splitlines() if 'finished_reason="error"' not in x))
        self.assert_result("INCONCLUSIVE", "COUNTER_SERIES_CHANGED")

    def test_new_error_series_not_baselined_to_zero(self):
        self.f.change_metrics(0, "\n".join(x for x in self.f.metrics(0, 0).splitlines() if 'finished_reason="error"' not in x))
        self.assert_result("INCONCLUSIVE", "COUNTER_SERIES_CHANGED")

    def test_observed_counter_reset_even_if_endpoint_delta_positive(self):
        self.f.change_metrics(0, self.f.metrics(8, 0))
        self.f.change_metrics(1, self.f.metrics(1, 4))
        self.f.change_metrics(2, self.f.metrics(12, 0))
        self.assert_result("INCONCLUSIVE", "COUNTER_RESET")

    def test_malformed_native_numeric_spelling_never_passes(self):
        for spelling in ("0_4", "4_", "_4", "4__0", "٤", "0x4", "0x1p2"):
            with self.subTest(spelling=spelling):
                self.f.change_metrics(1, self.f.metrics(0, 4).replace("} 4\n", "} " + spelling + "\n"))
                self.assert_result("INCONCLUSIVE")

    def test_missing_later_scrape_preserves_preemption_failure(self):
        self.f.change_metrics(1, self.f.metrics(0, 4, preempt=1))
        del self.f.samples[2]["source"]
        self.f.receipt["counters"] = self.f.write("counters.json", self.f.counter_capture)
        result = self.assert_result("FAIL", "PREEMPTION")
        self.assertIn("UNPROVEN_EVIDENCE", {item["code"] for item in result["findings"]})

    def test_missing_intermediate_scrape_preserves_endpoint_preemption(self):
        self.f.change_metrics(2, self.f.metrics(4, 0, preempt=1))
        del self.f.samples[1]["source"]
        self.f.receipt["counters"] = self.f.write("counters.json", self.f.counter_capture)
        self.assert_result("FAIL", "PREEMPTION")

    def test_missing_first_rank_preserves_later_driver_error(self):
        self.f.rank_rows[3]["steady"]["driver_errors"] = 1
        self.f.save_ranks()
        (self.f.root / "rank-0.json").unlink()
        result = self.assert_result("FAIL", "RANK_HEALTH")
        self.assertIn("UNPROVEN_EVIDENCE", {item["code"] for item in result["findings"]})

    def test_missing_guard_field_preserves_same_rank_error(self):
        del self.f.rank_rows[0]["startup"]
        self.f.rank_rows[0]["steady"]["oom_events"] = 1
        self.f.save_ranks()
        self.assert_result("FAIL", "RANK_HEALTH")

    def test_missing_requests_preserves_rank_failure(self):
        del self.f.receipt["requests"]
        self.f.rank_rows[3]["steady"]["driver_errors"] = 1
        self.f.save_ranks()
        self.assert_result("FAIL", "RANK_HEALTH")

    def test_malformed_earlier_request_preserves_later_request_error(self):
        self.f.rows[0]["prompt_tokens"] = True
        self.f.rows[-1]["error"] = "OOM"
        self.f.save_requests()
        result = self.assert_result("FAIL", "REQUEST_ERROR")
        self.assertIn("UNPROVEN_EVIDENCE", {item["code"] for item in result["findings"]})

    def test_malformed_usage_preserves_same_request_error(self):
        self.f.rows[0]["prompt_tokens"] = True
        self.f.rows[0]["error"] = "OOM"
        self.f.save_requests()
        self.assert_result("FAIL", "REQUEST_ERROR")

    def test_error_response_without_success_token_fields_is_failure(self):
        self.f.rows[0]["status_code"] = 500
        self.f.rows[0]["error"] = "OOM"
        del self.f.rows[0]["output_token_ids"]
        self.f.save_requests()
        result = self.assert_result("FAIL", "REQUEST_ERROR")
        self.assertIn("UNPROVEN_EVIDENCE", {item["code"] for item in result["findings"]})

    def test_missing_other_metric_preserves_preemption_failure(self):
        text = self.f.metrics(0, 4, preempt=1)
        self.f.change_metrics(1, "\n".join(line for line in text.splitlines() if not line.startswith(q.WAITING)))
        self.assert_result("FAIL", "PREEMPTION")

    def test_absent_length_series_remains_unknown_not_invented_zero(self):
        for i, pair in enumerate(((0, 0), (0, 4), (4, 0))):
            self.f.change_metrics(i, "\n".join(line for line in self.f.metrics(*pair).splitlines() if 'finished_reason="length"' not in line))
        self.assert_result("INCONCLUSIVE")

    def test_preemption_is_failure(self):
        self.f.change_metrics(2, self.f.metrics(4, 0, preempt=1))
        self.assert_result("FAIL", "PREEMPTION")

    def test_labelled_error_increase_is_failure(self):
        self.f.change_metrics(2, self.f.metrics(4, 0, errors=3))
        self.assert_result("FAIL", "LABELED_NONLENGTH_FINISH")

    def test_missing_finish_label_not_length(self):
        self.f.change_metrics(0, self.f.metrics(0, 0).replace(',finished_reason="length"', ""))
        self.f.change_metrics(1, self.f.metrics(0, 4).replace(',finished_reason="length"', ""))
        self.f.change_metrics(2, self.f.metrics(4, 0).replace(',finished_reason="length"', ""))
        self.assert_result("INCONCLUSIVE")

    def test_extra_completed_request_is_contamination(self):
        self.f.change_metrics(2, self.f.metrics(5, 0))
        self.assert_result("FAIL", "COMPLETION_COUNTER")

    def test_nonidle_fence(self):
        self.f.change_metrics(2, self.f.metrics(4, 1))
        self.assert_result("FAIL", "NONIDLE_FENCE")

    def test_unfenced_counter_interval(self):
        self.f.samples[-1]["observed_ns"] = 72
        self.f.receipt["counters"] = self.f.write("counters.json", self.f.counter_capture)
        self.assert_result("INCONCLUSIVE", "COUNTER_FENCE")

    def test_drain_before_steady_capture_end_fails(self):
        self.f.rank_rows[3]["drain"]["observed_ns"] = 85
        self.f.save_ranks()
        self.assert_result("FAIL", "DRAIN_ORDER")

    def test_explicit_metric_timestamp_needs_clock_binding(self):
        self.f.change_metrics(1, self.f.metrics(0, 4).replace("} 4\n", "} 4 123\n"))
        self.assert_result("INCONCLUSIVE")

    def test_missing_fourth_rank(self):
        self.f.receipt["ranks"].pop()
        self.assert_result("INCONCLUSIVE")

    def test_duplicate_rank_and_node(self):
        self.f.rank_rows[3] = copy.deepcopy(self.f.rank_rows[0])
        self.f.save_ranks()
        self.assert_result("FAIL", "RANK_COVERAGE")

    def test_all_four_drain_required(self):
        self.f.rank_rows[3]["drain"]["gpu_processes"] = 1
        self.f.save_ranks()
        self.assert_result("FAIL", "DRAIN_NOT_QUIESCENT")

    def test_unknown_drain_not_stopped(self):
        self.f.rank_rows[3]["drain"]["stopped"] = None
        self.f.save_ranks()
        self.assert_result("INCONCLUSIVE")

    def test_no_restored_running_substitution(self):
        self.f.rank_rows[3]["drain"]["policy"] = "restored-running"
        self.f.save_ranks()
        self.assert_result("FAIL", "DRAIN_POLICY")

    def test_guard_and_memory_health_failures(self):
        for key in ("oom_events", "allocation_retries", "driver_errors", "guard_halts", "errors", "engine_restarts"):
            with self.subTest(key=key):
                self.f.rank_rows[3]["steady"][key] = 1
                self.f.save_ranks()
                self.assert_result("FAIL", "RANK_HEALTH")
                self.f.rank_rows[3]["steady"][key] = 0

    def test_guard_floor_cannot_be_lowered(self):
        self.f.rank_rows[3]["steady"]["guard_floor_bytes"] -= 1
        self.f.save_ranks()
        self.assert_result("FAIL", "STEADY_GUARD")

    def test_incomplete_health_capture_unknown(self):
        self.f.rank_rows[3]["steady"]["complete"] = False
        self.f.save_ranks()
        self.assert_result("INCONCLUSIVE", "STEADY_COVERAGE")

    def test_swap_recorded_without_invented_threshold(self):
        self.f.rank_rows[3]["steady"]["swap_in_bytes"] = 25
        self.f.save_ranks()
        result = self.assert_result("PASS")
        self.assertEqual(25, result["swap_observations"][3]["swap_in_bytes"])

    def test_loaded_zero_and_active_binding(self):
        for state, scale in (("loaded_zero", 0), ("active_1", 1)):
            self.f.binding["state"] = {"mode": "dual", "steering": state, "profile": "heretic-1017-attn-v1",
                                      "profile_sha256": hashlib.sha256(b"synthetic-vector").hexdigest(), "scale": scale}
            self.f.save_requests()
            self.f.save_ranks()
            self.f.receipt["counters"] = self.f.write("counters.json", self.f.counter_capture)
            self.assert_result("PASS")
        self.f.binding["state"]["scale"] = True
        self.assert_result("INCONCLUSIVE")

    def test_cli_success_and_missing_source(self):
        expected = self.f.root / "expected.json"
        receipt = self.f.root / "receipt.json"
        expected.write_text(json.dumps(self.f.expected))
        receipt.write_text(json.dumps(self.f.receipt))
        argv = [sys.executable, "-B", str(MODULE), "--expected", str(expected), "--receipt", str(receipt), "--evidence-root", str(self.f.root)]
        result = subprocess.run(argv, text=True, capture_output=True, check=False)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("PASS", json.loads(result.stdout)["status"])
        (self.f.root / "counters.json").unlink()
        result = subprocess.run(argv, text=True, capture_output=True, check=False)
        self.assertEqual(2, result.returncode)
        self.assertEqual("INCONCLUSIVE", json.loads(result.stdout)["status"])


class ParserTest(unittest.TestCase):
    def test_json_rejects_duplicate_and_nonfinite(self):
        for text in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', '{"a":1e999}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                q.strict_json(text)

    def test_strict_structural_equality(self):
        self.assertFalse(q.exact({"x": True}, {"x": 1}))
        self.assertFalse(q.exact({"x": 0}, {"x": 0.0}))

    def test_prometheus_rejects_malformed_duplicate_or_nonfinite(self):
        for text in ('x{a="b"} NaN', 'x{a="b"} Infinity', 'x{a="b"} -1', 'x{a="b"} 0.5',
                     'x{a="b",a="c"} 1', 'x{a="b"} 1\nx{a="b"} 2', 'x{a="b",} 1', 'x{a="b"} true', 'x{a="b"} 0_4', 'x{a="b"} ٤'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                q.parse_prometheus(text)

    def test_scientific_integer_and_labels_preserved(self):
        result = q.parse_prometheus('x{model_name="a,b",engine="0"} 4e0')
        self.assertEqual({("x", (("engine", "0"), ("model_name", "a,b"))): 4}, result)


if __name__ == "__main__":
    unittest.main()
