#!/usr/bin/env python3
"""CPU-only real-checkpoint tokenizer, registered parser and profile binding gate."""
import argparse
import hashlib
import json
from pathlib import Path

from tests.parser.engine.test_deepseek_v41_token_provenance import EOS, NAMED, TOOLS, VALUE, output_for
from tests.parser.engine.replay_harness import _test_request
from vllm.parser.parser_manager import ParserManager
from vllm.tokenizers.deepseek_v41 import DeepseekV41Tokenizer, _plain_text_encoder
from vllm.models.deepseek_v4_1.steering.profile import load_profile, model_identity, template_identity

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--model", type=Path, required=True)
p.add_argument("--profile", type=Path, required=True)
a = p.parse_args()
tokenizer = DeepseekV41Tokenizer.from_pretrained(str(a.model), local_files_only=True)
assert EOS in tokenizer.all_special_tokens, "Load the checkpoint's special-token configuration"
plain = _plain_text_encoder(tokenizer)
vocab = tokenizer.get_vocab()
literal_ids = plain([EOS])[0]
assert vocab[EOS] not in literal_ids
rows = []
for thinking in (False, True):
    for json_tools in (False, True):
        for choice in ("named", "required", "auto", "none"):
            syntax = "auto" if choice in ("named", "required") and not json_tools else choice
            text = output_for(syntax)
            before, after = text.split(EOS)
            ids = tokenizer.encode(before, add_special_tokens=False) + literal_ids + tokenizer.encode(after, add_special_tokens=False)
            if thinking:
                prefix = tokenizer.encode("Plan.", add_special_tokens=False) + [vocab["</think>"]]
                ids = prefix + ids; text = "Plan.</think>" + text
            ids += [vocab[EOS]]; text += EOS
            assert tokenizer.decode(ids, skip_special_tokens=False) == text
            cls = ParserManager.get_parser(tool_parser_name="deepseek_v41", reasoning_parser_name="deepseek_v41", enable_auto_tools=True)
            parser = cls(tokenizer, chat_template_kwargs={"thinking": thinking})
            parser.tool_parser.supports_required_and_named = json_tools
            base = _test_request(tools=TOOLS)
            request = type(base).model_validate({**base.model_dump(), "tool_choice": NAMED if choice == "named" else choice})
            reasoning, content, calls = parser.parse(text, request, enable_auto_tools=True, model_output_token_ids=ids)
            assert (reasoning or "") == ("Plan." if thinking else "")
            if choice == "none":
                assert not calls and json.loads(content) == VALUE
            else:
                assert len(calls) == 1 and calls[0].name == "record_value"
                assert json.loads(calls[0].arguments) == VALUE
            rows.append({"thinking": thinking, "json_tools": json_tools, "choice": choice, "passed": True})
model = model_identity(a.model); template = template_identity(a.model)
assert model == "b35c43fda128481f17b7ee878df2c2f93fd6db416df451b080cc8d00e166f7d0"
assert template == "e27cffc1791e4da15a669f16223dd4b7b7745e0fed48de8d61f958dea939c8cb"
profile_sha = hashlib.sha256(a.profile.read_bytes()).hexdigest()
assert profile_sha == "e06b8cb60f3f382157ef6632a93efdd90185beafa2251a8c29575f09c45cd45b"
profile = load_profile(a.profile, profile_sha, expected_model_identity=model, expected_template_identity=template)
payload = hashlib.sha256(profile.directions.tobytes()).hexdigest()
assert payload == "f11e3762252448f547183d4b2daaf5b58c2d9023cd5b23365bc026a402c1a8e6"
assert profile.layers == tuple(range(2, 40)) and all(alpha == 2.5 for alpha in profile.alphas)
print(json.dumps({"tokenizer_sha256": hashlib.sha256((a.model / "tokenizer.json").read_bytes()).hexdigest(),
    "literal_token_ids": literal_ids, "real_eos_id": vocab[EOS], "parser_cases": rows,
    "model_identity": model, "template_identity": template, "profile_sha256": profile_sha,
    "directions_sha256": payload, "profile_rebinding_required": False}, indent=2))
