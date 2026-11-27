"""Replay the 91-token 1022 failure through the candidate's registered parser."""

import json
import os
from pathlib import Path

import pytest
from tests.parser.engine.replay_harness import _test_request, collect_output
from tests.parser.engine.test_deepseek_v41 import parser_for
from tests.parser.engine.test_deepseek_v41_token_provenance import NAMED, TOOLS, VALUE
from tokenizers.decoders import DecodeStream
from vllm.tokenizers.deepseek_v41 import DeepseekV41Tokenizer

EXPERIMENTS = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def checkpoint():
    tokenizer = DeepseekV41Tokenizer.from_pretrained(
        os.environ["STEERING_MODEL_IDENTITY"], local_files_only=True
    )
    record = EXPERIMENTS / "2026-10-23-1022-parser-byte-alignment/runs/attempt-3/requests-failed.json"
    ids = json.loads(record.read_text())["rows"][0]["answer"]["choices"][0]["token_ids"]
    assert len(ids) == 91 and ids[-1] == tokenizer.eos_token_id
    return tokenizer, ids


@pytest.mark.parametrize("thinking", [False, True])
@pytest.mark.parametrize("choice", ["named", "required", "auto", "none"])
@pytest.mark.parametrize("chunk", [0, 1, 7, 91])
def test_recorded_tool_output(checkpoint, thinking, choice, chunk):
    tokenizer, ids = checkpoint
    prefix = tokenizer.encode("Plan.</think>", add_special_tokens=False) if thinking else []
    ids = prefix + ids
    parser = parser_for(tokenizer, {"thinking": thinking})
    base = _test_request(tools=TOOLS)
    request = type(base).model_validate({
        **base.model_dump(), "tool_choice": NAMED if choice == "named" else choice,
    })
    prompt = [tokenizer.convert_tokens_to_ids("<think>" if thinking else "</think>")]
    parser.set_prompt_token_ids(prompt)
    text = tokenizer.decode(ids[:-1], skip_special_tokens=False)
    if chunk:
        decoder = DecodeStream(skip_special_tokens=False)
        pieces = [decoder.step(tokenizer.backend_tokenizer, tid) or "" for tid in ids[:-1]] + [""]
        assert "".join(pieces) == text
        deltas = []
        for start in range(0, len(ids), chunk):
            end = min(start + chunk, len(ids))
            deltas.append(parser.parse_delta(
                "".join(pieces[start:end]), ids[start:end], request,
                prompt_token_ids=prompt if start == 0 else None, finished=end == len(ids),
            ))
        output = collect_output(deltas)
        reasoning, content, calls = output.reasoning, output.content, output.tool_calls
    else:
        reasoning, content, parsed = parser.parse(text, request, True, ids)
        calls = [{"name": call.name, "arguments": call.arguments} for call in parsed or []]
    assert (reasoning or "").strip() == ("Plan." if thinking else "")
    if choice == "none":
        assert not calls and "<｜end▁of▁sentence｜>" in content
    else:
        assert not (content or "").strip()
        assert len(calls) == 1 and calls[0]["name"] == "record_value"
        assert json.loads(calls[0]["arguments"]) == VALUE
