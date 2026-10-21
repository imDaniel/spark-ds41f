#!/usr/bin/env python3
"""Opt-in offline CPU corpus for only four 64,512-input-ID capacity requests.

Preparation only. No field execution, generation, endpoint or arbitrary target.
The unchanged PR25 exporter owns native projection and provenance checks; the
reviewed finite capacity producer owns the output contract and preparation.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import re
import sys

# Isolate all helper imports, including the capacity consumer, before loading.
BYTECODE_PREFIX = '/__q03_cpu_export_unused_bytecode__'
if Path(BYTECODE_PREFIX).exists():
    raise RuntimeError('isolated unused bytecode prefix already exists')
_preimported_helpers = any(name in sys.modules for name in (
    'q03_native_corpus_export', 'q03_capacity', 'q03_qualification',
    'q03_owner_model', 'q03_held_window'))
_caller_bytecode_settings = sys.dont_write_bytecode, sys.pycache_prefix
sys.dont_write_bytecode, sys.pycache_prefix = True, BYTECODE_PREFIX
try:
    import q03_native_corpus_export as E
    import q03_capacity as C
finally:
    if __name__ != '__main__':
        sys.dont_write_bytecode, sys.pycache_prefix = _caller_bytecode_settings

Q, require = E.Q, E.require
TARGET_LENGTH, ROW_COUNT = 64512, 4
MAX_CANDIDATES_PER_ROW = 512
MAX_TEXT_BYTES = 2 * 1024 * 1024
NOTE_BLOCKS = (128, 32, 8, 1)
RECIPE = 'native-full-template-first-capacity-bounded-text-v1'


def isolate():
    require(not _preimported_helpers, 'task helper pre-imported; use a fresh interpreter')
    E.isolate()
    for module in (E, C):
        cached = getattr(getattr(module, '__spec__', None), 'cached', None)
        require(isinstance(cached, str) and cached.startswith(BYTECODE_PREFIX + '/'),
                'capacity helper loaded before source-only isolation; use a fresh interpreter')


def preparation_sources():
    result = E.preparation_sources()
    root = Path(__file__).resolve().parent
    require(all(Path(pin['path']) == root / name for name, pin in result.items()),
            'capacity preparation helper closure uses a different source directory')
    for name, module in [('q03_capacity_corpus_export.py', sys.modules[__name__]),
                         ('q03_capacity.py', C)]:
        path = Path(module.__file__).resolve()
        require(path == root / name, 'capacity preparation helper import origin differs: ' + name)
        result[name] = {'path': str(path), 'sha256': Q.digest(Q.read_owned_bytes(path))}
    return result


def validate_bindings(value):
    # Fail before any backend import if required retained records are missing.
    require(isinstance(value, dict) and set(value) == E.BINDING_FIELDS,
            'complete native CPU export bindings required')
    for name in ('weights_manifest', 'runtime_metadata'):
        pin = value[name]
        require(isinstance(pin, dict) and set(pin) == {'path', 'sha256'}
                and isinstance(pin['path'], str) and bool(pin['path'])
                and isinstance(pin['sha256'], str) and re.fullmatch('[0-9a-f]{64}', pin['sha256']),
                'retained actual provenance pin required: ' + name)
    return E.validate_bindings(value)


def verify_exact(tokenizer, text):
    rendered, untruncated = E.encode_text(tokenizer, text)
    http = tokenizer(rendered, add_special_tokens=False, truncation=True,
                     max_length=E.HTTP_MAX_LENGTH)['input_ids']
    native = tokenizer.apply_chat_template([{'role': 'user', 'content': text}],
                                           thinking=False, tokenize=True)
    require(http == untruncated == native, 'HTTP/native tokenizer drift or truncation')
    require(Q.token_ids(http, TARGET_LENGTH),
            'fully templated capacity prompt must be exactly 64512 IDs without truncation')
    return rendered, list(http)


def construct_row(tokenizer, index):
    require(type(index) is int and 0 <= index < ROW_COUNT, 'only four capacity prompt rows supported')
    topic, instruction = E.TOPICS[index]
    text = (instruction + '\nUse the planning notes below as reference material. '
            'Summarize the three most useful practical recommendations in a short answer.\n'
            'Planning record: ' + topic + '.\n')
    calls, note_index = 0, index

    def measure(candidate):
        nonlocal calls
        calls += 1
        require(calls <= MAX_CANDIDATES_PER_ROW, 'bounded capacity construction exhausted')
        require(len(candidate.encode('utf-8')) <= MAX_TEXT_BYTES, 'bounded capacity text size exceeded')
        return E.encode_text(tokenizer, candidate)[1]

    ids = measure(text)
    require(len(ids) < TARGET_LENGTH, 'initial capacity prompt exceeds exact-length target')
    # Fixed text-block schedule, not a token-count estimate or monotonic search.
    # Every complete candidate is freshly rendered and measured. Overshoots or
    # nonpositive BPE changes are rejected, then a smaller written block is tried.
    for block_size in NOTE_BLOCKS:
        while len(ids) < TARGET_LENGTH:
            block = ''.join(E.NOTES[(note_index + n) % len(E.NOTES)] + '\n'
                            for n in range(block_size))
            candidate = text + block
            trial = measure(candidate)
            if not len(ids) < len(trial) <= TARGET_LENGTH:
                break
            text, ids = candidate, trial
            note_index += block_size
    if len(ids) < TARGET_LENGTH:
        candidate = text + '\nReference keywords:'
        trial = measure(candidate)
        if len(ids) < len(trial) <= TARGET_LENGTH:
            text, ids = candidate, trial
    while len(ids) < TARGET_LENGTH:
        for word in E.KEYWORDS:
            candidate = text + word
            trial = measure(candidate)
            if len(ids) < len(trial) <= TARGET_LENGTH:
                text, ids = candidate, trial
                break
        else:
            raise RuntimeError('no bounded exact-length capacity text extension; no truncation fallback')
    rendered, final_ids = verify_exact(tokenizer, text)
    require(final_ids == ids, 'tokenizer changed during capacity construction')
    return {'id': index, 'text': text, 'prompt_ids': final_ids}, {
        'id': index, 'rendered_text': rendered, 'rendered_sha256': Q.digest(rendered.encode()),
        'candidate_calls': calls, 'untruncated_length': len(final_ids)}


def run(bindings_path, output_directory):
    isolate()
    require((C.WORKLOAD, C.INPUT_TOKENS, C.REQUESTS, C.OUTPUT_TOKENS)
            == ('capacity_64k_c4', TARGET_LENGTH, ROW_COUNT, 1024), 'finite capacity consumer contract differs')
    support_sources = preparation_sources()
    raw = Q.read_owned_bytes(bindings_path)
    bindings = Q.json_bytes(raw)
    export, sources, weights = validate_bindings(bindings)
    export['workload'] = C.WORKLOAD
    directory = Path(output_directory)
    require(directory.is_absolute() and directory.resolve() == directory and not directory.exists(),
            'new canonical absolute output directory required')
    immutable = [Path(bindings['model_root']), Path(bindings['native_root']), Path(bindings_path),
                 Path(bindings['weights_manifest']['path']), Path(bindings['runtime_metadata']['path'])]
    require(not any(directory == p or directory in p.parents or p in directory.parents for p in immutable),
            'output directory overlaps immutable model/source/provenance inputs')
    transformers = importlib.import_module('transformers')
    tokenizers = importlib.import_module('tokenizers')
    packages = {name: E.package_evidence(name, module) for name, module in
                [('transformers', transformers), ('tokenizers', tokenizers)]}
    for name, evidence in packages.items():
        require(evidence['version'] == export['identity'][name + '_version'],
                name + ' CPU/native runtime version drift')
    tokenizer = E.native_tokenizer(sources, bindings['model_root'], transformers.TokenizersBackend)
    rendered_rows = []
    for index in range(ROW_COUNT):
        row, rendered = construct_row(tokenizer, index)
        export['rows'].append(row)
        rendered_rows.append(rendered)
    C.validate_export(export)
    second, final_sources, final_weights = validate_bindings(bindings)
    require(final_sources == sources and final_weights == weights
            and second['identity'] == export['identity'], 'input provenance changed during export')
    for row, rendered in zip(export['rows'], rendered_rows):
        text, ids = verify_exact(tokenizer, row['text'])
        require(text == rendered['rendered_text'] and ids == row['prompt_ids'],
                'tokenizer drift during final capacity corpus verification')
    for name, module in [('transformers', transformers), ('tokenizers', tokenizers)]:
        require(E.package_evidence(name, module) == packages[name], name + ' package bytes changed during export')
    require(Q.read_owned_bytes(bindings_path) == raw, 'bindings changed during export')
    require(preparation_sources() == support_sources, 'preparation source changed during export')
    directory.mkdir(mode=0o700, exist_ok=False)
    bindings_pin = {'path': str(directory / 'bindings.json'),
                    'sha256': Q.exclusive_json(directory / 'bindings.json', bindings)}
    evidence = {'schema_version': 1, 'scope': 'CPU_PREPARATION_ONLY_NOT_FIELD_CAPACITY',
                'workload': C.WORKLOAD, 'input_tokens': TARGET_LENGTH, 'requests': ROW_COUNT,
                'bindings': bindings_pin, 'input_bindings_sha256': Q.digest(raw),
                'native_runtime_metadata': bindings['runtime_metadata'],
                'projection': 'unchanged_native197_AST_bodies_with_explicit_CPU_dependencies',
                'source_files_sha256': E.SOURCE_FILES, 'preparation_sources': support_sources,
                'construction_recipe': RECIPE, 'packages': packages,
                'construction_limits': {'candidate_calls_per_row': MAX_CANDIDATES_PER_ROW,
                                        'text_utf8_bytes_per_row': MAX_TEXT_BYTES,
                                        'note_block_sizes': list(NOTE_BLOCKS)},
                'weights': {'manifest': bindings['weights_manifest'], 'file_count': len(weights),
                            'weight_bytes_rehashed': False},
                'environment': {key: os.environ.get(key) for key in E.ENVIRONMENT},
                'python_version': sys.version, 'bytecode_prefix': sys.pycache_prefix,
                'blocked_import_roots': sorted(E.FORBIDDEN_IMPORTS), 'rendered_rows': rendered_rows}
    evidence_pin = {'path': str(directory / 'cpu-evidence.json'),
                    'sha256': Q.exclusive_json(directory / 'cpu-evidence.json', evidence)}
    export_pin = {'path': str(directory / 'tokenizer-export.json'),
                  'sha256': Q.exclusive_json(directory / 'tokenizer-export.json', export)}
    corpus_pin = C.prepare(export_pin['path'], directory / 'prepared')
    C.validate_corpus(Q.owner.pinned_json(corpus_pin))
    result = {'tokenizer_export': export_pin, 'cpu_evidence': evidence_pin, 'corpus': corpus_pin}
    result['receipt_sha256'] = Q.exclusive_json(directory / 'receipt.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bindings', required=True, help='retained actual PR25 asset/runtime bindings')
    parser.add_argument('--output-directory', required=True, help='new owner-controlled capacity evidence directory')
    args = parser.parse_args(argv)
    try:
        result = run(args.bindings, args.output_directory)
    except (RuntimeError, ValueError, OSError, ImportError) as error:
        parser.exit(1, 'capacity CPU export blocked: ' + str(error) + '\n')
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
