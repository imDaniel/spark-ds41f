"""Run the published 1126 encoder suite and the candidate's tool argument tests."""

import os
import runpy
from pathlib import Path

import pytest
from transformers import TokenizersBackend


experiments = Path(__file__).resolve().parents[2]
parent = runpy.run_path(str(experiments / "2026-11-28-1126-attention/tools/test_encoder_offline.py"))
encoder = parent["encoder"]
test_control_spellings = parent["test_control_spellings"]
test_upstream_encoder_regressions = parent["test_upstream_encoder_regressions"]
test_reference_prompts = parent["test_reference_prompts"]
test_renderer_tokenization_entry = parent["test_renderer_tokenization_entry"]
test_image_parts_and_literal_placeholder = parent["test_image_parts_and_literal_placeholder"]


@pytest.fixture(params=["byte_fixture", "checkpoint_tokenizer"])
def argument_tests(encoder, monkeypatch, request):
    tokenizer, tests = encoder
    if request.param == "checkpoint_tokenizer":
        checkpoint = Path(os.environ["STEERING_MODEL_IDENTITY"]) / "tokenizer.json"
        wrapped = tokenizer["get_deepseek_v41_tokenizer"](
            TokenizersBackend(tokenizer_file=str(checkpoint))
        )
        monkeypatch.setitem(tests, "byte_level_tokenizer", lambda: wrapped)
    return tests


@pytest.mark.parametrize("encoding_depth", [0, 1, 2])
@pytest.mark.parametrize("thinking", [False, True])
def test_tool_argument_types(argument_tests, encoding_depth, thinking):
    argument_tests["test_tool_argument_types_survive_control_protection"](encoding_depth, thinking)


def test_literal_image_preserves_json_argument_strings(argument_tests):
    argument_tests["test_literal_image_preserves_json_argument_strings"]()


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", '"plain"'])
def test_non_object_tool_arguments_keep_raw_string(encoder, raw):
    encoder[1]["test_non_object_tool_arguments_keep_raw_string"](raw)
