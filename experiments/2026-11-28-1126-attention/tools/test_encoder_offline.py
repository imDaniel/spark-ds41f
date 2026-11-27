"""Run the established encoder matrix against the 1126 candidate and image contract."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / "2026-10-23-1022-attention-review/tools"
spec = spec_from_file_location("encoder_1022", TOOLS / "test_encoder_offline.py")
previous = module_from_spec(spec)
spec.loader.exec_module(previous)
encoder = previous.encoder
test_control_spellings = previous.test_control_spellings
test_upstream_encoder_regressions = previous.test_upstream_encoder_regressions
test_reference_prompts = previous.test_reference_prompts
test_renderer_tokenization_entry = previous.test_renderer_tokenization_entry


def test_image_parts_and_literal_placeholder(encoder):
    tokenizer, tests = encoder
    wrapped = tests["byte_level_tokenizer"]()
    marker = tokenizer["IMAGE_PLACEHOLDER"]
    text_marker = tokenizer["IMAGE_PLACEHOLDER_TEXT"]
    messages = [{"role": "user", "content": [
        {"type": "text", "text": marker},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,fixture"}},
    ]}]
    ids = wrapped.apply_chat_template(messages)
    assert ids.count(wrapped.get_added_vocab()[marker]) == 1
    prompt = wrapped.apply_chat_template(messages, tokenize=False)
    assert prompt.count(marker) == 1
    assert text_marker in prompt
