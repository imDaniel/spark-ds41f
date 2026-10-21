"""Finite CPU fixtures for recorded-input alignment, not model-quality evidence."""

import copy
import json
import math
import unittest

from servebench import behaviour


def model_run(*, scores=True):
    sequences = [
        {"id": i, "prompt_ids": [10, 20 + i], "output_ids": [30 + i, 40 + i],
         "finish_reason": "length"}
        for i in range(2)
    ]
    rows = [
        {"id": row["id"], "prompt_ids": row["prompt_ids"][:], "tokens": row["output_ids"][:],
         "logprob": [-0.2, -0.3], "rank": [1, 1],
         "top_ids": [[token, token + 1] for token in row["output_ids"]],
         "top_logprobs": [[-0.2, -2.0], [-0.3, -2.1]]}
        for row in sequences
    ]
    return {
        "workload": {"prompts_sha256": "fixture", "prompt_count": 2, "concurrency": 2,
                     "max_tokens": 2, "fixed_length": True, "top": 2,
                     "extra_body": {}, **({"score_extra_body": {}} if scores else {})},
        "generation": {"sequences": sequences, "acceptance": {"acceptance_length": 2.5}},
        "corpus": {"source": "self", "sha256": behaviour.corpus_digest(sequences), "sequences": 2},
        "scores": rows if scores else None,
    }


class ComparisonAlignmentTest(unittest.TestCase):
    def assert_status(self, base, candidate, status, reason):
        original = copy.deepcopy((base, candidate))
        result = behaviour.compare(base, candidate)
        self.assertEqual(result["status"], status, result)
        self.assertIn(reason, "\n".join(result["reasons"]))
        # Comparison never rewrites input artifacts or invents error records.
        self.assertEqual((base, candidate), original)
        return result

    def test_complete_metrics_are_unchanged_and_not_a_quality_verdict(self):
        base = model_run()
        candidate = copy.deepcopy(base)
        candidate["generation"]["sequences"].reverse()
        candidate["scores"].reverse()
        result = behaviour.compare(base, candidate)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["generation"]["identical"], 2)
        self.assertEqual(result["scores"]["positions"], 4)
        self.assertEqual(result["scores"]["kl"]["max"], 0.0)
        self.assertAlmostEqual(result["scores"]["perplexity"]["base"], math.exp(0.25))
        text = behaviour.format_comparison(result, "base", "candidate")
        self.assertIn("not a noninferiority verdict", text)
        self.assertIn("estimated top-K KL", text)
        self.assertNotIn("identical distributions", text)
        self.assertNotIn("PASS", text)

    def test_generation_missing_both_sides_cannot_shrink_denominator(self):
        base = model_run(scores=False)
        base["generation"]["sequences"].pop()
        self.assert_status(base, copy.deepcopy(base), "incomplete", "missing ids [1]")

    def test_generation_missing_duplicate_error_and_invalid_ids(self):
        for arm in ("base", "candidate"):
            for defect in ("missing", "duplicate", "error", "invalid", "unexpected"):
                with self.subTest(arm=arm, defect=defect):
                    base, candidate = model_run(scores=False), model_run(scores=False)
                    rows = (base if arm == "base" else candidate)["generation"]["sequences"]
                    if defect == "missing":
                        rows.pop()
                    elif defect == "duplicate":
                        rows.append(copy.deepcopy(rows[0]))
                    elif defect == "error":
                        rows[0] = {"id": 0, "error": "fixture timeout"}
                    elif defect == "invalid":
                        rows[0]["id"] = True
                    else:
                        rows[0]["id"] = 9
                    result = behaviour.compare(base, candidate)
                    self.assertEqual(result["status"], "incomplete", result)

    def test_missing_sequence_collection_is_incomplete(self):
        base, candidate = model_run(scores=False), model_run(scores=False)
        candidate["generation"].pop("sequences")
        self.assert_status(base, candidate, "incomplete", "missing ids")

    def test_prompt_mismatch_is_not_output_identity(self):
        base, candidate = model_run(scores=False), model_run(scores=False)
        candidate["generation"]["sequences"][0]["prompt_ids"] = [99]
        result = self.assert_status(base, candidate, "incomparable", "input prefix differs")
        self.assertEqual(result["generation"]["identical"], 1)
        self.assertIn("diagnostic only", behaviour.format_comparison(result, "a", "b"))

    def test_fixed_length_is_validated_only_when_requested(self):
        base, candidate = model_run(scores=False), model_run(scores=False)
        for run in (base, candidate):
            run["generation"]["sequences"][0]["output_ids"].pop()
            run["generation"]["sequences"][0]["finish_reason"] = "stop"
        self.assert_status(base, candidate, "incomplete", "fixed length")
        for run in (base, candidate):
            run["workload"]["fixed_length"] = False
        self.assertEqual(behaviour.compare(base, candidate)["status"], "complete")

    def test_malformed_token_records_do_not_crash_or_pass(self):
        for field, value in (("prompt_ids", []), ("prompt_ids", None),
                             ("output_ids", None), ("output_ids", [True]),
                             ("output_ids", ["30"])):
            with self.subTest(field=field, value=value):
                base, candidate = model_run(scores=False), model_run(scores=False)
                candidate["generation"]["sequences"][0][field] = value
                self.assert_status(base, candidate, "incomplete", "malformed token ids")

    def test_workload_mismatch_including_zero_steering_is_incomparable(self):
        changes = {"prompts_sha256": "other", "concurrency": 1, "max_tokens": 3,
                   "top": 1, "extra_body": {"steering_scale": 0}}
        for key, value in changes.items():
            with self.subTest(key=key):
                base, candidate = model_run(), model_run()
                candidate["workload"][key] = value
                # A changed max_tokens with fixed_length would also be incomplete.
                if key == "max_tokens":
                    base["workload"]["fixed_length"] = candidate["workload"]["fixed_length"] = False
                self.assert_status(base, candidate, "incomparable", key)

    def test_same_arm_steering_state_mismatch_cannot_hide_in_equal_score_metadata(self):
        base = model_run()
        base["workload"]["extra_body"] = {"steering_profile": "fixture", "steering_scale": 1}
        result = self.assert_status(base, copy.deepcopy(base), "incomparable", "state differs from generation")
        self.assertEqual(result["scores"]["identical_fraction"], 1.0)

    def test_same_arm_boolean_steering_is_not_numeric_identity(self):
        for numeric, boolean in ((0, False), (1, True)):
            for nested in (False, True):
                with self.subTest(numeric=numeric, nested=nested):
                    base = model_run()
                    generated, scored = {"steering_scale": numeric}, {"steering_scale": boolean}
                    if nested:
                        generated, scored = {"vllm_xargs": generated}, {"vllm_xargs": scored}
                    base["workload"]["extra_body"] = generated
                    base["workload"]["score_extra_body"] = scored
                    self.assert_status(base, copy.deepcopy(base), "incomparable", "state differs from generation")

    def test_cross_arm_boolean_steering_is_not_numeric_identity(self):
        for numeric, boolean in ((0, False), (1, True)):
            for nested in (False, True):
                with self.subTest(numeric=numeric, nested=nested):
                    base, candidate = model_run(), model_run()
                    for run, value in ((base, numeric), (candidate, boolean)):
                        state = {"steering_scale": value}
                        if nested:
                            state = {"vllm_xargs": state}
                        run["workload"]["extra_body"] = copy.deepcopy(state)
                        run["workload"]["score_extra_body"] = copy.deepcopy(state)
                    self.assert_status(base, candidate, "incomparable", "extra_body")

    def test_numeric_metadata_equivalence_is_preserved(self):
        base, candidate = model_run(), model_run()
        for run, value in ((base, 0), (candidate, 0.0)):
            run["workload"]["extra_body"] = {"vllm_xargs": {"steering_scale": value}}
            run["workload"]["score_extra_body"] = {"vllm_xargs": {"steering_scale": 0.0}}
        self.assertEqual(behaviour.compare(base, candidate)["status"], "complete")
        self.assertTrue(behaviour._same_request_value({"values": [0, {"x": 1}]},
                                                     {"values": [0.0, {"x": 1.0}]}))
        self.assertFalse(behaviour._same_request_value({"values": [0]}, {"values": [False]}))

    def test_supported_zero_state_is_compared_without_chat_fields(self):
        base = model_run()
        base["workload"]["extra_body"] = {
            "chat_template_kwargs": {"enable_thinking": False},
            "vllm_xargs": {"steering_profile": "fixture", "steering_scale": 0, "other": "ignored"},
        }
        base["workload"]["score_extra_body"] = {
            "vllm_xargs": {"steering_profile": "fixture", "steering_scale": 0},
        }
        self.assertEqual(behaviour.compare(base, copy.deepcopy(base))["status"], "complete")

    def test_malformed_request_state_is_incomparable_not_a_comparison_crash(self):
        base = model_run()
        base["workload"]["extra_body"] = {"vllm_xargs": []}
        self.assert_status(base, copy.deepcopy(base), "incomparable", "invalid generation request state")

    def test_score_rows_missing_duplicate_and_failures_do_not_disappear(self):
        for arm in ("base", "candidate"):
            for defect in ("missing", "duplicate", "error"):
                with self.subTest(arm=arm, defect=defect):
                    base, candidate = model_run(), model_run()
                    rows = (base if arm == "base" else candidate)["scores"]
                    if defect == "missing":
                        rows.pop()
                    elif defect == "duplicate":
                        rows.append(copy.deepcopy(rows[0]))
                    else:
                        rows[0] = {"id": 0, "error": "fixture rejected"}
                    self.assertEqual(behaviour.compare(base, candidate)["status"], "incomplete")

    def test_score_positions_require_full_well_formed_coverage(self):
        defects = [(key, []) for key in ("logprob", "rank", "top_ids", "top_logprobs")]
        defects += [("logprob", [float("nan"), -0.3]), ("rank", [None, 1]),
                    ("top_ids", [[30, 30], [40, 41]]),
                    ("top_logprobs", [[-0.2], [-0.3, -2.1]])]
        for field, value in defects:
            with self.subTest(field=field, value=value):
                base, candidate = model_run(), model_run()
                candidate["scores"][0][field] = value
                result = behaviour.compare(base, candidate)
                self.assertEqual(result["status"], "incomplete")
                self.assertEqual(result["scores"]["positions"], 2)
                self.assertIn("malformed or incomplete positions", "\n".join(result["reasons"]))

    def test_scored_prefix_or_corpus_mismatch_blocks_inference(self):
        base, candidate = model_run(), model_run()
        candidate["scores"][0]["prompt_ids"] = [999]
        self.assert_status(base, candidate, "incomparable", "does not match the frozen corpus")
        candidate = model_run()
        candidate["corpus"]["sha256"] = "different"
        self.assert_status(base, candidate, "incomparable", "different corpora")

    def test_candidate_can_score_base_corpus_after_its_own_output_changes(self):
        base, candidate = model_run(), model_run()
        candidate["generation"]["sequences"][0]["output_ids"] = [88, 89]
        candidate["corpus"]["source"] = "base.json.gz"
        result = behaviour.compare(base, candidate)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["generation"]["identical"], 1)
        self.assertEqual(result["scores"]["identical_fraction"], 1.0)

    def test_legacy_state_stays_unverified_with_numbers_preserved(self):
        base, candidate = model_run(), model_run()
        for run in (base, candidate):
            run["workload"].pop("score_extra_body")
            for row in run["scores"]:
                row.pop("prompt_ids")
        result = self.assert_status(base, candidate, "incomparable", "scoring request state is unverified")
        self.assertEqual(result["scores"]["identical_fraction"], 1.0)
        self.assertEqual(result["scores"]["positions"], 4)
        self.assertEqual(result["warnings"], [])

    def test_unknown_external_prefix_never_inferred_from_output_tokens(self):
        base, candidate = model_run(), model_run()
        for run in (base, candidate):
            run["corpus"]["sha256"] = "external"
            for row in run["scores"]:
                row.pop("prompt_ids")
        self.assert_status(base, candidate, "incomparable", "input prefix is unavailable")

    def test_self_consistent_external_corpus_is_supported_with_full_request_records(self):
        base, candidate = model_run(), model_run()
        for run in (base, candidate):
            corpus = []
            for row in run["scores"]:
                row["prompt_ids"] = [80 + row["id"]]
                corpus.append({"id": row["id"], "prompt_ids": row["prompt_ids"], "output_ids": row["tokens"]})
            run["corpus"]["sha256"] = behaviour.corpus_digest(corpus)
            run["corpus"]["source"] = "external.json.gz"
        self.assertEqual(behaviour.compare(base, candidate)["status"], "complete")

    def test_both_sides_missing_scores_do_not_appear_complete(self):
        base = model_run()
        base["scores"].pop()
        self.assert_status(base, copy.deepcopy(base), "incomplete", "missing ids [1]")

    def test_external_corpus_cannot_hide_unexpected_ids_in_its_digest(self):
        base = model_run()
        base["scores"][0]["id"] = 9
        corpus = [{"id": row["id"], "prompt_ids": row["prompt_ids"], "output_ids": row["tokens"]}
                  for row in base["scores"]]
        base["corpus"]["sha256"] = behaviour.corpus_digest(corpus)
        self.assert_status(base, copy.deepcopy(base), "incomplete", "unexpected ids [9]")

    def test_empty_outputs_are_unscorable_not_request_errors(self):
        for empty_ids in ((0,), (0, 1)):
            with self.subTest(empty_ids=empty_ids):
                base = model_run()
                base["workload"]["fixed_length"] = False
                for row in base["generation"]["sequences"]:
                    if row["id"] in empty_ids:
                        row["output_ids"] = []
                        row["finish_reason"] = "stop"
                base["corpus"]["sha256"] = behaviour.corpus_digest(base["generation"]["sequences"])
                base["scores"] = [row for row in base["scores"] if row["id"] not in empty_ids]
                result = self.assert_status(base, copy.deepcopy(base), "incomparable", "no output tokens")
                self.assertNotIn("missing ids", "\n".join(result["reasons"]))
                self.assertNotIn("failed", "\n".join(result["reasons"]))
                self.assertEqual(result["scores"]["positions"], 2 * (2 - len(empty_ids)))

    def test_standalone_zero_position_score_is_unscorable(self):
        row = {"id": 0, "prompt_ids": [10], "tokens": [], "logprob": [], "rank": [],
               "top_ids": [], "top_logprobs": []}
        result = behaviour.compare_scores([row], [row])
        self.assertEqual(result["status"], "incomparable")
        self.assertEqual(result["positions"], 0)

    def test_scoring_present_only_on_one_arm_is_incomplete(self):
        self.assert_status(model_run(), model_run(scores=False), "incomplete", "missing ids")

    def test_declared_scoring_with_both_null_or_missing_score_collections_is_incomplete(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                base = model_run()
                if missing:
                    del base["scores"]
                else:
                    base["scores"] = None
                self.assert_status(base, copy.deepcopy(base), "incomplete", "score records are unavailable")

    def test_declared_scoring_requires_collection_even_when_no_positions_are_scorable(self):
        base = model_run()
        base["workload"]["fixed_length"] = False
        for row in base["generation"]["sequences"]:
            row["output_ids"] = []
        base["corpus"]["sha256"] = behaviour.corpus_digest(base["generation"]["sequences"])
        base["scores"] = None
        self.assert_status(base, copy.deepcopy(base), "incomplete", "score records are unavailable")
        base["scores"] = []
        self.assert_status(base, copy.deepcopy(base), "incomparable", "no scorable output positions")

    def test_no_score_remains_a_complete_generation_only_report(self):
        base = model_run(scores=False)
        result = behaviour.compare(base, copy.deepcopy(base))
        self.assertEqual(result["status"], "complete")
        self.assertNotIn("scores", result)
        json.dumps(result, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
