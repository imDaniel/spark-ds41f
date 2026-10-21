"""CPU request fixtures for scoring in generation's steering state, not GPU proof."""

import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from servebench import __main__ as cli  # noqa: E402
from servebench import behaviour, suite  # noqa: E402


SEQUENCE = {"id": 0, "prompt_ids": [11, 12], "output_ids": [13, 14], "finish_reason": "length"}
STATES = {
    "off": {},
    "loaded_zero": {"steering_profile": "fixture", "steering_scale": 0},
    "active": {"steering_profile": "fixture", "steering_scale": 1.25},
}


def score_reply(tokens):
    return {"choices": [{"prompt_token_ids": list(tokens), "prompt_logprobs": [
        None if index == 0 else {str(token): {"logprob": -0.25, "rank": 1}}
        for index, token in enumerate(tokens)
    ]}]}


class SameStateScoringTest(unittest.TestCase):
    def capture_score(self, extra=None, sequences=None):
        requests = []

        def post(url, body, timeout):
            requests.append((url, copy.deepcopy(body), timeout))
            return score_reply(body["prompt"])

        with patch.object(behaviour, "_post", side_effect=post):
            scores = behaviour.score("http://fixture/", "fixed-model", sequences or [SEQUENCE],
                                     top=3, concurrency=1, timeout=19, extra_body=extra)
        return requests, scores

    def test_off_zero_and_active_preserve_fixed_scoring_request(self):
        for state, extra in STATES.items():
            with self.subTest(state=state):
                requests, scores = self.capture_score(extra)
                url, body, timeout = requests[0]
                salt = body.pop("cache_salt")
                self.assertEqual((url, timeout), ("http://fixture/v1/completions", 19))
                self.assertEqual(len(salt), 32)
                self.assertEqual(body, {"model": "fixed-model", "prompt": [11, 12, 13, 14],
                                        "max_tokens": 1, "temperature": 0, "prompt_logprobs": 3,
                                        **extra})
                self.assertEqual(scores[0]["tokens"], [13, 14])
                self.assertEqual(scores[0]["prompt_ids"], [11, 12])
                self.assertIsNot(scores[0]["prompt_ids"], SEQUENCE["prompt_ids"])
                self.assertEqual(scores[0]["logprob"], [-0.25, -0.25])
                self.assertNotIn("error", scores[0])

    def test_absent_explicit_zero_null_and_profile_default_are_not_coerced(self):
        for extra in (None, {}, {"steering_scale": 0}, {"steering_scale": -0.0},
                      {"steering_profile": "fixture"}, {"steering_profile": None},
                      {"steering_profile": "fixture", "steering_scale": None},
                      {"vllm_xargs": {"steering_scale": 0}},
                      {"vllm_xargs": {"steering_profile": "fixture", "steering_scale": 1.25}},
                      {"vllm_xargs": {"steering_profile": None, "steering_scale": None}}):
            with self.subTest(extra=extra):
                before = copy.deepcopy(extra)
                selected = behaviour.score_extra_body(extra)
                self.assertEqual(selected, extra or {})
                self.assertEqual(extra, before)
                requests, _ = self.capture_score(extra)
                for key in ("steering_profile", "steering_scale", "vllm_xargs"):
                    self.assertEqual(key in requests[0][1], key in selected)
                    if key in selected:
                        self.assertEqual(requests[0][1][key], selected[key])

    def test_both_supported_locations_are_preserved_without_mutation(self):
        extra = {"steering_profile": "fixture", "steering_scale": 0,
                 "vllm_xargs": {"steering_profile": "fixture", "steering_scale": 0,
                                "unrelated_extension": "generation-only"}}
        before = copy.deepcopy(extra)
        selected = behaviour.score_extra_body(extra)
        self.assertEqual(selected, {"steering_profile": "fixture", "steering_scale": 0,
                                    "vllm_xargs": {"steering_profile": "fixture", "steering_scale": 0}})
        self.assertEqual(extra, before)
        self.assertIsNot(selected["vllm_xargs"], extra["vllm_xargs"])

    def test_chat_and_generation_options_cannot_replace_scoring_semantics(self):
        extra = {"model": "not-the-scoring-model", "prompt": [999], "messages": [],
                 "max_tokens": 999, "temperature": 9, "prompt_logprobs": 999,
                 "cache_salt": "not-cold", "return_token_ids": False,
                 "chat_template_kwargs": {"thinking": False}, "ignore_eos": True,
                 "min_tokens": 999, **STATES["active"]}
        requests, _ = self.capture_score(extra)
        body = requests[0][1]
        self.assertEqual(body["model"], "fixed-model")
        self.assertEqual(body["prompt"], [11, 12, 13, 14])
        self.assertEqual((body["max_tokens"], body["temperature"], body["prompt_logprobs"]), (1, 0, 3))
        self.assertNotEqual(body["cache_salt"], "not-cold")
        for key in ("messages", "chat_template_kwargs", "ignore_eos", "min_tokens"):
            self.assertNotIn(key, body)

    def test_each_scoring_request_retains_a_fresh_caller_salt(self):
        sequences = [dict(SEQUENCE, id=identifier) for identifier in range(3)]
        requests, _ = self.capture_score(STATES["active"], sequences)
        self.assertEqual(len({body["cache_salt"] for _, body, _ in requests}), 3)

    def test_invalid_envelopes_are_not_silently_dropped(self):
        for extra in ([], False, 0, "bad", {"vllm_xargs": []}, {"vllm_xargs": False},
                      {"vllm_xargs": 0}, {"vllm_xargs": "bad"}):
            with self.subTest(extra=extra), patch.object(behaviour, "_post") as post:
                with self.assertRaises(ValueError):
                    behaviour.score("http://fixture", "m", [SEQUENCE], extra_body=extra)
                post.assert_not_called()

    def test_null_empty_and_unrelated_xargs_do_not_invent_steering_controls(self):
        for extra in ({"vllm_xargs": None}, {"vllm_xargs": {}},
                      {"vllm_xargs": {"unrelated_extension": "generation-only"}}):
            with self.subTest(extra=extra):
                self.assertEqual(behaviour.score_extra_body(extra), {})

    def test_model_rejects_invalid_projection_envelope_before_generation(self):
        for extra in ([], {"vllm_xargs": []}):
            with self.subTest(extra=extra), patch.object(behaviour, "generate") as generate:
                with self.assertRaises(ValueError):
                    cli.main(["model", "http://fixture", "--model", "m", "--limit", "1",
                              "--extra-body", json.dumps(extra)])
                generate.assert_not_called()

    def test_server_rejections_preserve_conflicts_and_invalid_steering_values(self):
        # Transport tests only: a rejection is a fixture, not an imported native
        # protocol validator or proof of target-server admission.
        invalid = [
            {"steering_profile": "fixture", "vllm_xargs": {"steering_profile": "other"}},
            {"steering_scale": 0, "vllm_xargs": {"steering_scale": 1}},
            {"steering_profile": "unknown", "steering_scale": 0},
            {"steering_scale": 1},
            {"steering_profile": "fixture", "steering_scale": True},
            {"steering_profile": "fixture", "steering_scale": "0"},
            {"steering_profile": "fixture", "steering_scale": 4.01},
            {"steering_profile": "fixture", "steering_scale": -1},
        ]
        for extra in invalid:
            with self.subTest(extra=extra):
                def rejected(url, body, timeout):
                    self.assertEqual({key: body[key] for key in extra}, extra)
                    raise ValueError("fixture server rejected steering request")

                with patch.object(behaviour, "_post", side_effect=rejected):
                    result = behaviour.score("http://fixture", "m", [SEQUENCE], extra_body=extra)
                self.assertEqual(result, [{"id": 0, "error":
                                          "ValueError: fixture server rejected steering request"}])

    def test_malformed_score_responses_are_per_sequence_errors(self):
        valid = score_reply([11, 12, 13, 14])
        malformed = [
            {}, {"choices": []}, {"choices": [{}]},
            {"choices": [{"prompt_logprobs": None}]},
            {"choices": [{"prompt_logprobs": []}]},
            {"choices": [{"prompt_logprobs": valid["choices"][0]["prompt_logprobs"][:-1]}]},
            {"choices": [{"prompt_logprobs": valid["choices"][0]["prompt_logprobs"] + [None]}]},
            {"choices": [{"prompt_logprobs": [None, None, 3, None]}]},
            {"choices": [{"prompt_logprobs": [None, None, {"13": 7}, None]}]},
        ]
        for response in malformed:
            with self.subTest(response=response), patch.object(behaviour, "_post", return_value=response):
                result = behaviour.score("http://fixture", "m", [SEQUENCE])
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0]["id"], 0)
                self.assertIn("error", result[0])

    def test_one_malformed_response_does_not_discard_other_sequence_results(self):
        sequences = [dict(SEQUENCE, id=identifier) for identifier in range(2)]
        with patch.object(behaviour, "_post", side_effect=[
            {"choices": [{"prompt_logprobs": []}]}, score_reply([11, 12, 13, 14]),
        ]):
            result = behaviour.score("http://fixture", "m", sequences, concurrency=1)
        self.assertEqual([row["id"] for row in result], [0, 1])
        self.assertIn("error", result[0])
        self.assertEqual(result[1]["prompt_ids"], [11, 12])
        self.assertNotIn("error", result[1])

    def test_model_and_suite_arguments_forward_current_state_not_corpus_state(self):
        for state, extra in STATES.items():
            for use_corpus in (False, True):
                with self.subTest(state=state, corpus=use_corpus), tempfile.TemporaryDirectory() as tmp:
                    requests = []
                    corpus_sequence = dict(SEQUENCE, prompt_ids=[21, 22], output_ids=[23, 24])
                    corpus_path = str(Path(tmp) / "corpus.json")
                    cli.write_json(corpus_path, {"workload": {"extra_body": {"steering_profile": "other"}},
                                                 "generation": {"sequences": [corpus_sequence]}})
                    extra = {**extra, "chat_template_kwargs": {"thinking": False}}
                    step = {"id": "model", "command": "model", "args": {
                        "limit": 1, "max_tokens": 2, "fixed_length": True,
                        "model": "fixed-model", "top": 3, "extra_body": extra,
                    }}
                    output = str(Path(tmp) / "model.json")
                    argv = suite.arguments(step, "http://fixture", output, state,
                                           corpus_path if use_corpus else None)

                    def post(url, body, timeout):
                        requests.append((url, copy.deepcopy(body)))
                        if url.endswith("/chat/completions"):
                            return {"prompt_token_ids": SEQUENCE["prompt_ids"], "choices": [{
                                "token_ids": SEQUENCE["output_ids"], "finish_reason": "length"}]}
                        return score_reply(body["prompt"])

                    with contextlib.ExitStack() as stack:
                        stack.enter_context(patch.object(behaviour, "_post", side_effect=post))
                        stack.enter_context(patch.object(behaviour.prom, "require_idle"))
                        stack.enter_context(patch.object(behaviour.prom, "scrape", return_value=None))
                        stack.enter_context(patch.object(behaviour, "_positions", return_value=None))
                        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                        self.assertEqual(cli.main(argv), 0)
                    self.assertEqual(len(requests), 2)
                    generated, scored = [body for _, body in requests]
                    selected = behaviour.score_extra_body(extra)
                    for key in ("steering_profile", "steering_scale", "vllm_xargs"):
                        self.assertEqual(key in scored, key in selected)
                        if key in selected:
                            self.assertEqual(scored[key], generated[key])
                    expected = corpus_sequence if use_corpus else SEQUENCE
                    self.assertEqual(scored["prompt"], expected["prompt_ids"] + expected["output_ids"])
                    self.assertNotIn("chat_template_kwargs", scored)
                    result = cli.read_json(output)
                    self.assertEqual(result["workload"]["extra_body"], extra)
                    self.assertEqual(result["workload"]["score_extra_body"], selected)
                    self.assertEqual(result["scores"][0]["prompt_ids"], expected["prompt_ids"])

    def test_no_score_never_calls_completion_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(behaviour, "generate", return_value={
            "sequences": [SEQUENCE], "seconds": 0.0, "acceptance": {},
        }), patch.object(behaviour, "score") as score, contextlib.redirect_stdout(io.StringIO()):
            output = str(Path(tmp) / "model.json")
            self.assertEqual(cli.main(["model", "http://fixture", "--model", "m", "--limit", "1",
                                       "--extra-body", json.dumps(STATES["active"]), "--no-score",
                                       "--output", output]), 0)
            result = cli.read_json(output)
            self.assertNotIn("score_extra_body", result["workload"])
            self.assertIsNone(result["scores"])
        score.assert_not_called()


if __name__ == "__main__":
    unittest.main()
