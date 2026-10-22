#!/usr/bin/env python3
"""Offline CPU producer for the finite original warm4K A188/B197-OFF corpus.

Uses the actual local tokenizer assets/backend and hash-verified unchanged native
and PR8 generator function bodies. No endpoint, model, GPU, or token-ID padding,
concatenation, slicing, or truncation participates in corpus construction.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import platform
import random
import sys
import sysconfig
from types import SimpleNamespace
import uuid

# The supporting exporter must see every helper for the first time under its
# source-only prefix, including on Python versions with lazy spec.cached.
BYTECODE_PREFIX = '/__q03_cpu_export_unused_bytecode__'
_preimported_helpers = any(name in sys.modules for name in (
    'q03_native_corpus_export', 'q03_legacy_case_replay', 'q03_qualification',
    'q03_owner_model', 'q03_held_window'))
_before_bytecode = sys.dont_write_bytecode, sys.pycache_prefix
sys.dont_write_bytecode, sys.pycache_prefix = True, BYTECODE_PREFIX
try:
    import q03_native_corpus_export as N
    import q03_legacy_case_replay as L
finally:
    if __name__ != '__main__':
        sys.dont_write_bytecode, sys.pycache_prefix = _before_bytecode

Q = N.Q
require = Q.require
NATIVE_HEADS = {
    'r6x188': 'ebb44766d6f04d3a9272b712778d7f2fa2cec1fd',
    'native197-off': '154159dc3e38ca8b2321c01a153a1b2ccaf8663b',
}
SOURCE_FILES = {
    **N.SOURCE_FILES,
    'vllm/entrypoints/serve/tokenize/protocol.py': '0bb4ddf2c0b1412e88c9d853e2ef7163f62665df0b8bbef86df5bdbdd9a76e44',
    'vllm/entrypoints/serve/tokenize/serving.py': 'fb9fd00cc71df5c052f420a959e39434ce5fd3c84556275eb9dc25d43ff59750',
    'vllm/entrypoints/openai/chat_completion/protocol.py': 'f6a21a3b6ae07967b6e4994e14bcf9b39117d8f7dfc57bbc8c2979687784f95f',
}
CHAT_OPTIONS = {'add_generation_prompt': True, 'continue_final_message': False}


def isolate():
    require(not _preimported_helpers, 'use a fresh interpreter without preloaded task helpers')
    N.isolate()
    for module in (N, L):
        cached = getattr(getattr(module, '__spec__', None), 'cached', None)
        require(isinstance(cached, str) and cached.startswith(BYTECODE_PREFIX + '/'),
                'legacy helper loaded before source-only isolation; use a fresh interpreter')
    # Refuse the unfixed PR25 implementation before importing Transformers.
    require(all(name in sys.modules and sys.modules[name] is None for name in N.FORBIDDEN_IMPORTS),
            'fixed native CPU exporter negative import-cache isolation is required')


def preparation_sources():
    result = N.preparation_sources()
    root = Path(__file__).resolve().parent
    for module, name in ((sys.modules[__name__], 'q03_legacy_corpus_export.py'),
                         (L, 'q03_legacy_case_replay.py')):
        path = Path(module.__file__).resolve()
        require(path == root / name, 'legacy preparation helper import origin differs: ' + name)
        result[name] = {'path': str(path), 'sha256': Q.digest(Q.read_owned_bytes(path))}
    path = root / 'q03_http_toolbox.json'
    result[path.name] = {'path': str(path), 'sha256': Q.digest(Q.read_owned_bytes(path))}
    require(result[path.name]['sha256'] == L.TOOLBOX_INVENTORY_SHA256, 'PR8 inventory drift')
    return result


def canonical_root(value, label):
    require(isinstance(value, str), label + ' must be a canonical absolute path')
    path = Path(value)
    require(path.is_absolute() and str(path) == value and path.resolve() == path and path.is_dir(),
            label + ' must be an existing canonical absolute directory')
    return path


def read_native_sources(bindings):
    require(isinstance(bindings, dict) and set(bindings) == set(NATIVE_HEADS),
            'both fixed native source selections required')
    output = {}
    for arm, expected_head in NATIVE_HEADS.items():
        binding = bindings[arm]
        require(isinstance(binding, dict) and set(binding) == {'head', 'root'}
                and binding['head'] == expected_head, 'fixed native source head differs: ' + arm)
        root = canonical_root(binding['root'], arm + ' native source root')
        files = {}
        for name, expected in SOURCE_FILES.items():
            raw = Q.read_owned_bytes(root / name)
            require(Q.digest(raw) == expected, 'native tokenizer/protocol source drift: ' + arm + ':' + name)
            files[name] = raw
        output[arm] = files
    require(output['r6x188'] == output['native197-off'], 'native A/B tokenizer/protocol bytes differ')
    return output


def read_toolbox(root):
    root = canonical_root(root, 'PR8 toolbox root')
    inventory = L.toolbox_inventory()['files']
    L.verify_toolbox(root, inventory)
    return {name: Q.read_owned_bytes(root / name) for name in inventory}


def validate_bindings(value):
    require(isinstance(value, dict) and set(value) == {
        'schema_version', 'native_cpu_bindings', 'native_sources', 'toolbox_root'}
        and type(value['schema_version']) is int and value['schema_version'] == 1,
        'complete legacy CPU corpus bindings required')
    native = Q.owner.pinned_json(value['native_cpu_bindings'])
    export, loader_sources, weights = N.validate_bindings(native)
    sources = read_native_sources(value['native_sources'])
    require(native['native_root'] == value['native_sources']['native197-off']['root'],
            'actual tokenizer loader source root differs from fixed native197 input')
    require(loader_sources == {key: sources['native197-off'][key] for key in N.SOURCE_FILES},
            'actual tokenizer loader source bytes differ')
    toolbox = read_toolbox(value['toolbox_root'])
    return native, export, sources, toolbox, weights


def project_generator(toolbox):
    """Execute unchanged hash-verified PR8 source bodies with stdlib dependencies."""
    name = 'servebench/prompts.py'
    require(Q.digest(toolbox[name]) == L.toolbox_inventory()['files'][name], 'PR8 prompt source drift')
    namespace = {'__name__': '_q03_legacy_prompts_projection', 'hashlib': hashlib,
                 'json': json, 'random': random, 'sysconfig': sysconfig, 'Path': Path}
    N.execute_projection(toolbox[name], name, namespace)
    return SimpleNamespace(**namespace)


def python_sources(prompts):
    paths = prompts.standard_library_sources()
    require(paths, 'Python standard-library generator source inventory is empty')
    return [{'name': path.name, 'size': path.stat().st_size, 'sha256': N.sha_file(path)} for path in paths]


def python_identity():
    return {'implementation': platform.python_implementation(), 'version': platform.python_version(),
            'executable_sha256': N.sha_file(Path(sys.executable).resolve())}


def raw_ids(tokenizer, text):
    # PR8 /tokenize(prompt=...) has add_special_tokens=True by default.
    result = tokenizer.encode(text, add_special_tokens=True, truncation=False)
    require(L.ids(result), 'native raw calibration must return complete integer IDs')
    return list(result)


def chat_ids(tokenizer, text):
    messages = [{'role': 'user', 'content': text}]
    # Do not set thinking, enable_thinking or reasoning_effort. Native default
    # thinking=True/high is part of the old request, not the lean-screen setting.
    rendered = tokenizer.apply_chat_template(messages, tokenize=False, **CHAT_OPTIONS)
    require(isinstance(rendered, str), 'native chat renderer did not return text')
    result = tokenizer.encode(rendered, add_special_tokens=False, truncation=False)
    require(L.ids(result), 'native chat encoding must return complete integer IDs')
    direct = tokenizer.apply_chat_template(messages, tokenize=True, truncation=False, **CHAT_OPTIONS)
    require(result == direct, 'native rendered/direct full template IDs differ')
    return rendered, list(result)


def construct_corpus(tokenizer, export, toolbox, *, content_only=False):
    """Exactly four documents, eight raw calibrations, and 64 full chat vectors."""
    prompts = project_generator(toolbox)
    sources = python_sources(prompts)
    environment = python_identity()
    captured = []
    def token_count(url, model, text):
        require(url == 'offline-cpu-only' and model == L.MODEL, 'offline calibration target differs')
        result = raw_ids(tokenizer, text)
        captured.append({'text': text, 'response': {'tokens': result, 'count': len(result)}})
        return len(result)
    namespace = {'__name__': '_q03_legacy_documents_projection', 'Load': SimpleNamespace,
                 'prompt_sets': prompts, 'token_count': token_count, 'uuid': uuid}
    name = 'servebench/load.py'
    require(Q.digest(toolbox[name]) == L.toolbox_inventory()['files'][name], 'PR8 load source drift')
    N.execute_projection(toolbox[name], name, namespace, function='documents')
    docs = namespace['documents']('offline-cpu-only', L.MODEL,
                                 SimpleNamespace(concurrency=4, context_tokens=4096, seed=2048), True)
    require(len(docs) == 4 and len(captured) == 8, 'exact PR8 two-step calibration count differs')
    questions = prompts.default_prompts()[:16]
    frozen, rendered_rows = [], []
    for slot, doc in enumerate(docs):
        rows = []
        for question in questions:
            text = doc['text'] + '\n\n' + question['text']
            rendered, result = chat_ids(tokenizer, text)
            rows.append({'prompt_id': question['id'], 'prompt_ids': result})
            rendered_rows.append({'slot': slot, 'prompt_id': question['id'], 'rendered_text': rendered,
                                  'rendered_sha256': Q.digest(rendered.encode()), 'token_count': len(result)})
        frozen.append({'slot': slot, 'text': doc['text'], 'text_sha256': Q.digest(doc['text'].encode()),
                       'raw_ids': captured[2 * slot + 1]['response']['tokens'],
                       'calibration': captured[2 * slot:2 * slot + 2], 'chat_inputs': rows})
    value = {'schema_version': 1, 'workload': L.WORKLOAD, 'model': L.MODEL,
             'tokenizer_identity': {key: copy.deepcopy(export['identity'][key]) for key in L.TOKENIZER_FIELDS},
             'generator': {'toolbox_head': L.TOOLBOX_HEAD, 'toolbox_files': L.toolbox_inventory()['files'],
                           'driver_sha256': L.ORIGINAL_DRIVER_SHA256, 'context_tokens': 4096, 'seed': 2048,
                           'python': environment, 'stdlib_sources': sources},
             'prompts': questions, 'prompts_sha256': L.PROMPTS_SHA256, 'documents': frozen,
             'historical_document_identity': 'UNAVAILABLE_NEW_SHARED_CORPUS'}
    require(python_sources(prompts) == sources and python_identity() == environment,
            'Python generator environment changed during corpus construction')
    if content_only:
        return value, rendered_rows
    value['artifacts'] = copy.deepcopy(export['artifacts'])
    return L.validate_corpus(value), rendered_rows


def verify_corpus(tokenizer, corpus, rendered_rows):
    require(len(rendered_rows) == 64, 'all 64 native renderings must be retained')
    index = 0
    for doc in corpus['documents']:
        for calibration in doc['calibration']:
            require(raw_ids(tokenizer, calibration['text']) == calibration['response']['tokens'],
                    'raw calibration token IDs changed before publication')
        for question, row in zip(corpus['prompts'], doc['chat_inputs']):
            rendered, result = chat_ids(tokenizer, doc['text'] + '\n\n' + question['text'])
            retained = rendered_rows[index]
            require(retained['slot'] == doc['slot'] and retained['prompt_id'] == question['id']
                    and retained['rendered_text'] == rendered and retained['rendered_sha256'] == Q.digest(rendered.encode())
                    and result == row['prompt_ids'] and retained['token_count'] == len(result),
                    'native full template rendering or IDs changed before publication')
            index += 1


def run(bindings_path, output_directory):
    isolate()
    support = preparation_sources()
    raw = Q.read_owned_bytes(bindings_path)
    bindings = Q.json_bytes(raw)
    native, export, sources, toolbox, weights = validate_bindings(bindings)
    directory = Path(output_directory)
    require(directory.is_absolute() and directory.resolve() == directory and not directory.exists(),
            'new canonical absolute output directory required')
    immutable = [Path(bindings_path), Path(bindings['native_cpu_bindings']['path']),
                 Path(native['model_root']), Path(native['weights_manifest']['path']),
                 Path(native['runtime_metadata']['path']), Path(bindings['toolbox_root']),
                 *(Path(item['root']) for item in bindings['native_sources'].values()),
                 *(Path(pin['path']) for pin in support.values())]
    require(not any(directory == path or directory in path.parents or path in directory.parents for path in immutable),
            'output directory overlaps immutable model/source/provenance inputs')
    transformers = importlib.import_module('transformers')
    tokenizers = importlib.import_module('tokenizers')
    packages = {name: N.package_evidence(name, module) for name, module in (
        ('transformers', transformers), ('tokenizers', tokenizers))}
    for name, evidence in packages.items():
        require(evidence['version'] == export['identity'][name + '_version'],
                name + ' CPU/native runtime version drift')
    tokenizer = N.native_tokenizer(sources['native197-off'], native['model_root'], transformers.TokenizersBackend)
    corpus, rendered_rows = construct_corpus(tokenizer, export, toolbox)
    verify_corpus(tokenizer, corpus, rendered_rows)
    final_native, final_export, final_sources, final_toolbox, final_weights = validate_bindings(bindings)
    require((final_native, final_export, final_sources, final_toolbox, final_weights)
            == (native, export, sources, toolbox, weights), 'retained CPU inputs changed during export')
    require(python_sources(project_generator(toolbox)) == corpus['generator']['stdlib_sources']
            and python_identity() == corpus['generator']['python'], 'Python generator inputs changed during export')
    for name, module in (('transformers', transformers), ('tokenizers', tokenizers)):
        require(N.package_evidence(name, module) == packages[name], name + ' package bytes changed during export')
    require(Q.read_owned_bytes(bindings_path) == raw and preparation_sources() == support,
            'preparation source/bindings changed during export')
    directory.mkdir(mode=0o700, exist_ok=False)
    bindings_pin = {'path': str(directory / 'bindings.json'),
                    'sha256': Q.exclusive_json(directory / 'bindings.json', bindings)}
    evidence = {'schema_version': 1, 'scope': 'CPU_PREPARATION_ONLY_NOT_FIELD_QUALIFICATION',
                'bindings': bindings_pin, 'input_bindings_sha256': Q.digest(raw),
                'native_cpu_bindings': bindings['native_cpu_bindings'],
                'native_runtime_metadata': native['runtime_metadata'],
                'native_sources': {arm: {'head': NATIVE_HEADS[arm], 'root': selection['root'],
                    'files_sha256': SOURCE_FILES} for arm, selection in bindings['native_sources'].items()},
                'source_scope': 'verified_selected_source_bytes_not_full_git_tree_or_executing_image',
                'projection': 'unchanged_native_and_PR8_AST_bodies_with_explicit_CPU_dependencies',
                'preparation_sources': support, 'packages': packages,
                'tokenizer_identity': export['identity'], 'artifacts': export['artifacts'],
                'raw_tokenization': {'add_special_tokens': True, 'truncation': False},
                'chat_tokenization': {**CHAT_OPTIONS, 'add_special_tokens': False, 'truncation': False,
                                      'thinking_override': 'ABSENT', 'reasoning_effort_override': 'ABSENT'},
                'weights': {'manifest': native['weights_manifest'], 'file_count': len(weights),
                            'weight_bytes_rehashed': False},
                'environment': {key: os.environ.get(key) for key in N.ENVIRONMENT},
                'python_version': sys.version, 'bytecode_prefix': sys.pycache_prefix,
                'stdlib_root': str(Path(sysconfig.get_paths()['stdlib']).resolve()),
                'blocked_import_roots': sorted(N.FORBIDDEN_IMPORTS), 'rendered_rows': rendered_rows}
    evidence_pin = {'path': str(directory / 'cpu-evidence.json'),
                    'sha256': Q.exclusive_json(directory / 'cpu-evidence.json', evidence)}
    corpus_pin = {'path': str(directory / 'corpus.json'),
                  'sha256': Q.exclusive_json(directory / 'corpus.json', corpus)}
    result = {'corpus': corpus_pin, 'cpu_evidence': evidence_pin, 'bindings': bindings_pin}
    result['receipt_sha256'] = Q.exclusive_json(directory / 'receipt.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bindings', required=True, help='retained CPU/asset/native-source bindings JSON')
    parser.add_argument('--output-directory', required=True, help='new canonical evidence output directory')
    args = parser.parse_args(argv)
    try:
        print(json.dumps(run(args.bindings, args.output_directory), sort_keys=True))
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
