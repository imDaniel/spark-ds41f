"""Run 1022's real encoder/tokenizer bodies without importing CUDA vLLM.

Only package-boundary imports are replaced. The renderer check covers its
tokenization entry point, not the complete online multimedia/API pipeline.
"""

import ast
import os
from pathlib import Path

import pytest
from transformers import TokenizersBackend

SOURCE = Path(os.environ["STEERING_VLLM_SOURCE"])
MODEL = Path(os.environ["STEERING_MODEL_IDENTITY"])


def load(relative, namespace=None):
    path = SOURCE / relative
    namespace = dict(namespace or {}, __file__=str(path), __name__="offline_encoder")
    tree = ast.parse(path.read_text())
    # Keep every production statement; bypass only imports of the CUDA package.
    tree.body = [
        node for node in tree.body
        if not isinstance(node, ast.ImportFrom)
        or not (node.level or (node.module or "").startswith(("vllm", "tests")))
    ]
    tree.body.insert(0, ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0
    ))
    exec(compile(ast.fix_missing_locations(tree), str(path), "exec"), namespace)
    return namespace


@pytest.fixture(scope="module")
def encoder():
    names = load("vllm/utils/tool_names.py")
    encoding = load("vllm/tokenizers/deepseek_v41_encoding.py", names)
    tokenizer = load("vllm/tokenizers/deepseek_v41.py", {
        **encoding, "HfTokenizer": TokenizersBackend, "TokenizerLike": object,
        "get_cached_tokenizer": lambda value: value,
    })
    upstream_fake = ast.parse(
        (SOURCE / "tests/tokenizers_/test_deepseek_v4.py").read_text()
    )
    fake = next(n for n in upstream_fake.body if isinstance(n, ast.ClassDef) and n.name == "FakeHfTokenizer")
    ns = {}
    exec(compile(ast.Module(body=[fake], type_ignores=[]), "<upstream-fake-tokenizer>", "exec"), ns)
    tests = load("tests/tokenizers_/test_deepseek_v41.py", {
        **encoding, **tokenizer, "FakeHfTokenizer": ns["FakeHfTokenizer"],
    })
    return tokenizer, tests


@pytest.mark.parametrize("backend", ["byte_fixture", "checkpoint_tokenizer"])
@pytest.mark.parametrize("thinking", [True, False])
@pytest.mark.parametrize("location", [
    "user", "text_part", "system", "reasoning", "tool_result",
    "arguments_escaped", "tool_description",
])
def test_control_spellings(encoder, monkeypatch, backend, thinking, location):
    tokenizer, tests = encoder
    if backend == "checkpoint_tokenizer":
        monkeypatch.setitem(tests, "byte_level_tokenizer", lambda:
            tokenizer["get_deepseek_v41_tokenizer"](
                TokenizersBackend(tokenizer_file=str(MODEL / "tokenizer.json"))
            )
        )
    tests["test_control_spellings_in_request_text_stay_plain_text"](location, thinking)


@pytest.mark.parametrize("name", [
    "test_requests_without_control_spellings_encode_the_prompt_unchanged",
    "test_control_spellings_truncate_like_encode",
    "test_text_form_keeps_control_spellings_verbatim",
    "test_sentinels_avoid_private_use_characters_in_the_request",
])
def test_upstream_encoder_regressions(encoder, name):
    encoder[1][name]()


@pytest.mark.parametrize("case", [1, 2])
def test_reference_prompts(encoder, case):
    encoder[1]["test_reference_encoder_fixtures"](case)


def test_renderer_tokenization_entry(encoder):
    tokenizer, tests = encoder
    path = SOURCE / "vllm/renderers/deepseek_v4.py"
    base_tree = ast.parse(path.read_text())
    base = next(n for n in base_tree.body if isinstance(n, ast.ClassDef) and n.name == "DeepseekV4Renderer")
    method = next(n for n in base.body if getattr(n, "name", None) == "_apply_chat_template")
    ns = {}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), ns)
    base_class = type("RendererBoundary", (), {
        "_apply_chat_template": ns["_apply_chat_template"],
        "get_tokenizer": lambda self: self.tokenizer,
    })
    renderer = load("vllm/renderers/deepseek_v41.py", {
        "DeepseekV4Renderer": base_class,
        "order_tool_results": tokenizer["order_tool_results"],
    })["DeepseekV41Renderer"]()
    renderer.tokenizer = tests["byte_level_tokenizer"]()
    messages = [{"role": "user", "content": tests["INJECTED"]}]
    actual = renderer._apply_chat_template(messages=messages, tokenize=False)
    assert isinstance(actual, list)
    assert actual == renderer.tokenizer.apply_chat_template(messages)


def test_image_parts_remain_images_and_typed_placeholder_is_rejected(encoder):
    tokenizer, tests = encoder
    wrapped = tests["byte_level_tokenizer"]()
    marker = tokenizer["IMAGE_PLACEHOLDER"]
    messages = [{"role": "user", "content": [
        {"type": "text", "text": tests["INJECTED"]},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,fixture"}},
    ]}]
    ids = wrapped.apply_chat_template(messages)
    assert ids.count(wrapped.get_added_vocab()[marker]) == 1
    with pytest.raises(ValueError, match="image special token"):
        wrapped.apply_chat_template([{"role": "user", "content": marker}])
