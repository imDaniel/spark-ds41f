"""Run the maintenance encoder matrix against the prepared RC2 source."""

import runpy
from pathlib import Path


tools = Path(__file__).resolve().parents[2] / "2026-11-28-1126-attention-fixes/tools"
previous = runpy.run_path(str(tools / "test_encoder_offline.py"))
encoder = previous["encoder"]
argument_tests = previous["argument_tests"]
test_control_spellings = previous["test_control_spellings"]
test_upstream_encoder_regressions = previous["test_upstream_encoder_regressions"]
test_reference_prompts = previous["test_reference_prompts"]
test_renderer_tokenization_entry = previous["test_renderer_tokenization_entry"]
test_image_parts_and_literal_placeholder = previous["test_image_parts_and_literal_placeholder"]
test_tool_argument_types = previous["test_tool_argument_types"]
test_literal_image_preserves_json_argument_strings = previous[
    "test_literal_image_preserves_json_argument_strings"
]
test_non_object_tool_arguments_keep_raw_string = previous[
    "test_non_object_tool_arguments_keep_raw_string"
]


def test_deep_tool_argument_json_keeps_renderer_depth(argument_tests):
    argument_tests["test_deep_tool_argument_json_keeps_renderer_depth"]()


def test_repeated_control_spellings_preserve_tool_call_identity(argument_tests):
    argument_tests["test_repeated_control_spellings_preserve_tool_call_identity"]()
