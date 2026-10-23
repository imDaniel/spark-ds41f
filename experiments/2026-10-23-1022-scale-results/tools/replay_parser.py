#!/usr/bin/env python3
"""CPU replay of unchanged candidate parser code, with a character-token fixture.

The fixture supplies the checkpoint's actual special-token table. The replay compares
ordinary character IDs, a special EOS ID, and text without token IDs.
Only package boundaries and unused high-level parser/tool adapters are stubbed;
the production config, scanner, lexer, state machine and converter are unchanged.
This is synthetic parser evidence, not the unavailable raw GPU token trace.
"""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import types

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--source', type=Path, required=True)
p.add_argument('--tokenizer', type=Path, required=True)
a = p.parse_args(); source = a.source.resolve()
expected = 'ab7b6505447360a926fee000df5dc59b2ff770ff'
assert subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD^{tree}'], text=True).strip() == expected
assert not subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain', '--untracked-files=no'], text=True).strip()
for name, relative in [('vllm', 'vllm'), ('vllm.parser', 'vllm/parser'),
                       ('vllm.parser.engine', 'vllm/parser/engine'), ('vllm.tool_parsers', 'vllm/tool_parsers')]:
    module = types.ModuleType(name); module.__path__ = [str(source / relative)]; sys.modules[name] = module
module = types.ModuleType('vllm.parser.engine.parser_engine'); module.ParserEngine = object
sys.modules[module.__name__] = module
module = types.ModuleType('vllm.tool_parsers.utils')
def unused(*args, **kwargs):
    raise AssertionError('Unused adapter was called')
module.find_tool_properties = unused; sys.modules[module.__name__] = module
from vllm.parser.deepseek_v41 import deepseek_v41_config
from vllm.parser.engine.streaming_parser_engine import StreamingParserEngine
from vllm.parser.engine.events import EventType

spec = json.loads(a.tokenizer.read_text())
special = {v['content']: v['id'] for v in spec['added_tokens'] if v.get('special')}
class CharacterTokenizer:
    all_special_tokens = list(special)
    all_special_ids = list(special.values())
    def get_vocab(self): return special
    def decode(self, ids):
        inverse = {v:k for k,v in special.items()}
        return ''.join(inverse[i] if i in inverse else chr(i - 1000000) for i in ids)

typed = '<｜end▁of▁sentence｜>'
config = deepseek_v41_config(thinking=False)
assert typed in special and typed not in config.preserve_tokens
rows = []
for case, value, escaped, mode in [('ordinary', 'literal text', False, 'characters'),
    ('literal_ordinary_ids', typed, False, 'characters'),
    ('literal_special_id', typed, False, 'special'),
    ('literal_text_only', typed, False, 'no_ids'),
    ('unicode_escaped_literal', typed, True, 'characters')]:
    nested = json.dumps({'quote"key': value}, ensure_ascii=escaped)
    text = ('<｜DSML｜ calls><｜DSML｜ invoke name="record_value">'
            '<｜DSML｜ parameter name="role" string="true">literal user value</｜DSML｜ parameter>'
            '<｜DSML｜ parameter name="type" string="true">literal string value</｜DSML｜ parameter>'
            '<｜DSML｜ parameter name="nested" string="false">' + nested + '</｜DSML｜ parameter>'
            '</｜DSML｜ invoke></｜DSML｜ calls>')
    ids = [1000000 + ord(c) for c in text]
    if mode == 'special':
        left, right = text.split(typed)
        ids = [1000000 + ord(c) for c in left] + [special[typed]] + [1000000 + ord(c) for c in right]
    elif mode == 'no_ids':
        ids = []
    special_count = sum(i in special.values() for i in ids)
    engine = StreamingParserEngine(config, CharacterTokenizer())
    events = engine.feed(text, ids) + engine.finish()
    raw = ''.join(e.value for e in events if e.type == EventType.ARG_VALUE_CHUNK)
    actual = json.loads(config.arg_converter(raw, False))
    expected_value = '' if mode in ('special', 'no_ids') else value
    assert actual == {'role':'literal user value', 'type':'literal string value', 'nested':{'quote"key':expected_value}}, (case, actual, raw)
    rows.append({'case':case,'input_value':value,'output_arguments':actual,
                 'special_token_ids_in_input':special_count, 'token_id_mode':mode,'literal_preserved':actual['nested']['quote"key']==value})
files = ['parser/deepseek_v4.py','parser/deepseek_v41.py','parser/engine/streaming_parser_engine.py',
         'parser/engine/parser_engine_config.py','parser/engine/token_id_scanner.py','parser/engine/incremental_lexer.py','parser/engine/events.py']
print(json.dumps({'source_tree':expected,'fixture':'ordinary character IDs plus actual checkpoint special-token table',
    'tokenizer_sha256':hashlib.sha256(a.tokenizer.read_bytes()).hexdigest(),
    'source_file_sha256':{x:hashlib.sha256((source/'vllm'/x).read_bytes()).hexdigest() for x in files},
    'cases':rows,'conclusion':'Parser behavior depends on token provenance. This CPU replay does not establish the unrecorded runtime token sequence or attribute the API failure to steering.'},ensure_ascii=False,indent=2))
