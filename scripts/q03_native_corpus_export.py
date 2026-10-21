#!/usr/bin/env python3
"""Offline CPU preparation for schema-2 lean qualification, never generation.

The exact native197 callable source is projected without vLLM's model imports.
Actual TokenizersBackend calls, not fixture IDs, supply every exported token.
The retained native-runtime record and this CPU process's measurements are
separate evidence: a source pin is not proof of an executing container image.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import copy
import hashlib
import importlib
import importlib.abc
import importlib.metadata
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Union

# Set before importing any task-owned helpers: -B alone still reads old .pyc.
BYTECODE_PREFIX = '/__q03_cpu_export_unused_bytecode__'
if Path(BYTECODE_PREFIX).exists():
    raise RuntimeError('isolated unused bytecode prefix already exists')
_caller_bytecode_settings = sys.dont_write_bytecode, sys.pycache_prefix
sys.dont_write_bytecode = True
sys.pycache_prefix = BYTECODE_PREFIX
try:
    import q03_qualification as Q
finally:
    # Library imports must not change unrelated callers' compile/cache behavior.
    if __name__ != '__main__':
        sys.dont_write_bytecode, sys.pycache_prefix = _caller_bytecode_settings

require = Q.require
MODEL_REPOSITORY = 'deepseek-ai/DeepSeek-V4.1-Flash-1017'
MODEL_REVISION = '1bae0e1088993b9b82de08f59de5be5a34e42170'
HTTP_MAX_LENGTH = 1048561
TARGET_LENGTH = 2048
SOURCE_FILES = {
    'vllm/tokenizers/deepseek_v41.py': Q.NATIVE_SOURCES['deepseek_v41.py'],
    'vllm/tokenizers/deepseek_v41_encoding.py': Q.NATIVE_SOURCES['deepseek_v41_encoding.py'],
    'vllm/tokenizers/hf.py': 'fa96702c3b78dea6c70e90d3eaa6054315b953aec647a7ad1ac389931fdba85d',
    'vllm/tokenizers/protocol.py': '6eb7b47b5e0c21b0a51a8307d888cc075742dcee440eac2f6b8bb756a30876dd',
    'vllm/utils/tool_names.py': 'd2cc3e771d2e2072f02d43879b4012a0eb97a66b1cd1ff206e851abf3a989e67',
}
BINDING_FIELDS = {'schema_version', 'model_repository', 'model_revision', 'tokenizer_revision',
                  'model_root', 'model_config_sha256', 'tokenizer_assets_sha256',
                  'special_tokens_file', 'weights_manifest', 'runtime_metadata', 'native_root'}
ENVIRONMENT = {'CUDA_VISIBLE_DEVICES': '', 'NVIDIA_VISIBLE_DEVICES': 'void',
               'HIP_VISIBLE_DEVICES': '', 'ROCR_VISIBLE_DEVICES': '', 'USE_TORCH': '0',
               'USE_TF': '0', 'USE_FLAX': '0', 'HF_HUB_OFFLINE': '1',
               'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1',
               'TOKENIZERS_PARALLELISM': 'false', 'PYTHONDONTWRITEBYTECODE': '1'}
FORBIDDEN_IMPORTS = frozenset({'torch', 'tensorflow', 'jax', 'cupy', 'triton', 'vllm'})


class PreparationIsolation(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        require(fullname.split('.')[0] not in FORBIDDEN_IMPORTS,
                'CPU-only preparation refuses runtime/GPU import: ' + fullname)
        return None


def offline_audit(event, args):
    # This is defense in depth for a dedicated one-shot process, not an OS sandbox.
    # Also run with the operator's network/device isolation when available.
    require(not event.startswith('socket.') and event not in {
        'subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.exec', 'os.fork', 'os.forkpty'},
        'offline CPU preparation refuses network/process action: ' + event)
    if event == 'open':
        path = args[0]
        if isinstance(path, (str, bytes)):
            value = os.fsdecode(path)
            require(not value.startswith(('/dev/nvidia', '/dev/dri', '/dev/kfd')),
                    'CPU-only preparation refuses GPU device access')


def isolate():
    require(not any(name.split('.')[0] in FORBIDDEN_IMPORTS for name in sys.modules),
            'use a fresh interpreter without model/GPU modules')
    os.environ.update(ENVIRONMENT)
    sys.dont_write_bytecode = True
    require(not Path(BYTECODE_PREFIX).exists(), 'isolated unused bytecode prefix already exists')
    sys.pycache_prefix = BYTECODE_PREFIX
    os.environ['PYTHONPYCACHEPREFIX'] = BYTECODE_PREFIX
    for module in (Q, Q.owner, sys.modules['q03_held_window']):
        require(str(getattr(module, '__cached__', '')).startswith(BYTECODE_PREFIX + '/'),
                'task helper loaded before source-only isolation; use a fresh interpreter')
    sys.meta_path.insert(0, PreparationIsolation())
    sys.addaudithook(offline_audit)


def preparation_sources():
    root = Path(__file__).resolve().parent
    result = {}
    for name, module in [('q03_native_corpus_export.py', sys.modules[__name__]),
                         ('q03_qualification.py', Q), ('q03_owner_model.py', Q.owner),
                         ('q03_held_window.py', sys.modules['q03_held_window'])]:
        path = Path(module.__file__).resolve()
        require(path == root / name, 'preparation helper import origin differs: ' + name)
        result[name] = {'path': str(path), 'sha256': Q.digest(Q.read_owned_bytes(path))}
    return result


def sha_file(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def plain_relative(name):
    return (isinstance(name, str) and name and not Path(name).is_absolute()
            and '..' not in Path(name).parts and str(Path(name)) == name)


def weight_inventory(raw):
    """Read the existing manifest, never open or rehash any weight shard."""
    text = raw.decode('utf-8')
    if text.lstrip().startswith('{'):
        value = Q.json_bytes(raw)
        # Explicit supported existing JSON form: {"files": {relative: sha256}}.
        require(isinstance(value, dict) and set(value) == {'files'}
                and isinstance(value['files'], dict), 'unsupported existing weights manifest JSON')
        pairs = list(value['files'].items())
    else:
        pairs = []
        for line in text.splitlines():
            match = re.fullmatch(r'([0-9a-f]{64}) [ *](.+)', line)
            require(match is not None, 'existing weights manifest must be SHA256SUMS or files JSON')
            pairs.append((match[2], match[1]))
    require(len(pairs) == 83 and len({name for name, _ in pairs}) == 83,
            'existing weights manifest must retain exactly 83 distinct files')
    require(all(plain_relative(name) and isinstance(sha, str)
                and re.fullmatch('[0-9a-f]{64}', sha) for name, sha in pairs),
            'invalid existing weight manifest inventory')
    return dict(pairs)


def read_sources(native_root):
    root = Path(native_root)
    require(root.is_absolute() and root.resolve() == root, 'canonical native source root required')
    sources = {}
    for name, expected in SOURCE_FILES.items():
        raw = Q.read_owned_bytes(root / name)
        require(Q.digest(raw) == expected, 'native197 source drift: ' + name)
        sources[name] = raw
    return sources


def validate_bindings(value):
    require(isinstance(value, dict) and set(value) == BINDING_FIELDS
            and type(value['schema_version']) is int and value['schema_version'] == 1,
            'complete native CPU export bindings required')
    require(value['model_repository'] == MODEL_REPOSITORY
            and value['model_revision'] == value['tokenizer_revision'] == MODEL_REVISION,
            'registered 1017 model/tokenizer revision differs')
    root = Path(value['model_root'])
    require(root.is_absolute() and root.resolve() == root and root.is_dir(),
            'registered canonical local model snapshot required')
    assets = value['tokenizer_assets_sha256']
    require(isinstance(assets, dict) and {'tokenizer.json', 'tokenizer_config.json'} <= set(assets),
            'complete tokenizer.json and tokenizer_config.json pins required')
    require(all(plain_relative(name) and isinstance(sha, str) and re.fullmatch('[0-9a-f]{64}', sha)
                for name, sha in assets.items()), 'invalid tokenizer asset pins')
    # The native backend is a single-file tokenizer, with config and optional
    # special-token/added-token/Jinja sidecars. Never silently ignore a sidecar.
    observed = {p.name for p in root.iterdir() if p.is_file() and
                (p.name.startswith(('tokenizer', 'special_tokens', 'added_tokens', 'chat_template'))
                 or p.suffix in {'.jinja', '.model'} or p.name in {'vocab.json', 'vocab.txt', 'merges.txt'})}
    require(observed == set(assets), 'tokenizer asset closure differs from snapshot')
    require(not any(name.endswith(('.model', '.py')) or name in {'vocab.json', 'vocab.txt', 'merges.txt'}
                    for name in assets), 'native TokenizersBackend single-file asset closure differs')
    for name, expected in [('config.json', value['model_config_sha256']), *sorted(assets.items())]:
        require(isinstance(expected, str) and re.fullmatch('[0-9a-f]{64}', expected)
                and sha_file(root / name) == expected, 'model/tokenizer asset drift: ' + name)
    tokenizer_config = Q.json_bytes((root / 'tokenizer_config.json').read_bytes())
    redirects = {'auto_map', 'fast_tokenizer_files', 'tokenizer_file', 'tokenizer_files',
                 'vocab_file', 'vocab_files', 'merges_file', 'tokenizer_config_file',
                 'special_tokens_map_file', 'added_tokens_file', 'chat_template_file'}
    require(isinstance(tokenizer_config, dict)
            and not any(tokenizer_config.get(key) for key in redirects),
            'remote code or alternate tokenizer assets are outside native closure')
    require(value['special_tokens_file'] in assets, 'inventoried special tokens asset required')
    sources = read_sources(value['native_root'])
    runtime = Q.owner.pinned_json(value['runtime_metadata'])
    identity = {key: value[key] for key in ('model_repository', 'model_revision', 'tokenizer_revision',
                'model_config_sha256', 'tokenizer_assets_sha256')}
    identity.update(weights_manifest_sha256=value['weights_manifest']['sha256'],
                    encoding_source_sha256=Q.NATIVE_SOURCES['deepseek_v41_encoding.py'],
                    chat_template_sha256=Q.TEMPLATE_SHA,
                    special_tokens_sha256=assets[value['special_tokens_file']],
                    transformers_version=runtime.get('transformers_version'),
                    tokenizers_version=runtime.get('tokenizers_version'))
    artifacts = {'model_root': str(root), 'weights_manifest': value['weights_manifest'],
                 'runtime_metadata': value['runtime_metadata'], 'special_tokens_file': value['special_tokens_file'],
                 'native_sources': {name: {'path': str(Path(value['native_root']) / 'vllm/tokenizers' / name),
                                            'sha256': sha} for name, sha in Q.NATIVE_SOURCES.items()}}
    export = {'schema_version': 1, 'tokenizer_mode': 'deepseek_v41', 'identity': identity,
              'artifacts': artifacts, 'request': copy.deepcopy(Q.EXTRA), 'rows': []}
    Q.validate_identity(identity)
    Q.verify_artifacts(export)
    inventory = weight_inventory(Q.read_owned_bytes(value['weights_manifest']['path']))
    require(all((root / name).is_file() for name in inventory), 'retained manifest file absent in snapshot')
    return export, sources, inventory


def execute_projection(raw, filename, namespace, *, function=None):
    """Compile unchanged AST bodies from a hash-verified complete source file."""
    tree = ast.parse(raw, filename=filename)
    if function:
        selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function]
        require(len(selected) == 1, 'native helper projection differs')
    else:
        # Only top-level imports are removed. Their actual stdlib/backend/native
        # dependencies are supplied explicitly below; all executable bodies stay.
        selected = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    module = ast.Module(body=selected, type_ignores=[])
    exec(compile(module, filename, 'exec'), namespace)


def native_tokenizer(sources, model_root, backend):
    ns = {'__name__': '_q03_native_cpu_projection', 'copy': copy, 'json': json,
          'contextlib': contextlib, 'Any': Any, 'Dict': Dict, 'List': List,
          'Optional': Optional, 'Union': Union, 'TokenizersBackend': backend,
          'HfTokenizer': backend, 'ChatCompletionMessageParam': dict}
    # Protocol is stdlib-only; its TYPE_CHECKING branch is not executed.
    name = 'vllm/tokenizers/protocol.py'
    exec(compile(sources[name], name, 'exec'), ns)
    for name, function in [('vllm/utils/tool_names.py', None),
                           ('vllm/tokenizers/hf.py', 'get_cached_tokenizer'),
                           ('vllm/tokenizers/deepseek_v41_encoding.py', None),
                           ('vllm/tokenizers/deepseek_v41.py', None)]:
        execute_projection(sources[name], name, ns, function=function)
    return ns['DeepseekV41Tokenizer'].from_pretrained(str(model_root), local_files_only=True)


def package_evidence(name, module):
    """Measure installed distribution bytes, not expected/fabricated versions."""
    dist = importlib.metadata.distribution(name)
    version = dist.version
    require(version == getattr(module, '__version__', None), name + ' loaded/installed version drift')
    require(dist.files is not None, name + ' installed file inventory is missing')
    files = {}
    for item in dist.files:
        relative = str(item)
        if relative.endswith('.pyc') or '__pycache__' in Path(relative).parts:
            continue
        # Include this package and its dist-info only, not unrelated bin scripts.
        if Path(relative).parts[0] != name and not Path(relative).parts[0].startswith(name + '-'):
            continue
        path = Path(dist.locate_file(item)).resolve()
        require(path.is_file(), name + ' installed package file is missing: ' + relative)
        files[relative] = {'path': str(path), 'sha256': sha_file(path)}
    imported = str(Path(module.__file__).resolve())
    require(files and imported in {pin['path'] for pin in files.values()}, name + ' import origin outside distribution')
    return {'version': version, 'module_path': imported, 'files': files,
            'inventory_sha256': Q.digest(Q.canonical(files))}


TOPICS = (
    ('library', 'Plan a quiet public library reading room with accessible desks and clear book returns.'),
    ('garden', 'Plan a community garden with shared tools, watering duties, and shaded gathering space.'),
    ('museum', 'Explain how a small museum can label exhibits clearly and welcome first-time visitors.'),
    ('bakery', 'Organize a bakery morning shift with a clear production order and less wasted packaging.'),
    ('bicycle', 'Describe a neighborhood bicycle repair workshop and a practical volunteer work schedule.'),
    ('school', 'Plan an after-school reading club with varied activities and a simple lending process.'),
    ('market', 'Lay out a weekend craft market with clear pedestrian routes and straightforward signs.'),
    ('archive', 'Design a cataloguing workflow for a local history archive of photographs and letters.'),
    ('park', 'Suggest a maintenance plan for a small park with paths, benches, and seasonal planting.'),
    ('theater', 'Prepare a community theater rehearsal plan that keeps scenery and props organized.'),
    ('kitchen', 'Organize a shared teaching kitchen with labeled storage and an orderly cleanup routine.'),
    ('station', 'Improve wayfinding in a small railway station using consistent names and visible signs.'),
    ('studio', 'Plan a shared pottery studio with separate work areas and a fair booking procedure.'),
    ('trail', 'Write a visitor guide for a nature trail that explains route choices and rest areas.'),
    ('festival', 'Create a volunteer briefing for a neighborhood music festival with clear handover notes.'),
    ('office', 'Describe a shared office layout that balances quiet work and small collaborative meetings.'),
)
NOTES = (
    'Visitors should be able to find the main entrance without asking for directions.',
    'Use plain language on signs and keep the same names in printed and spoken instructions.',
    'Keep frequently used supplies close to the area where people actually need them.',
    'Assign a named coordinator to each activity and record the handover at the end of the day.',
    'Leave enough room around furniture for people to move comfortably in either direction.',
    'Invite volunteers to report confusing instructions before the next session begins.',
    'Store reusable materials neatly and identify a sensible place for items awaiting repair.',
    'A short checklist can help the next person understand which routine tasks remain unfinished.',
    'Allow time for setup, quiet breaks, and cleanup rather than scheduling every minute.',
    'Review feedback regularly and distinguish urgent maintenance from optional future improvements.',
    'Explain how visitors can ask a question without interrupting an activity already in progress.',
    'Keep a readable written summary so that a new volunteer can participate with confidence.',
)
KEYWORDS = (' access', ' signs', ' storage', ' timing', ' care', ' notes', ' order', ' people',
            ' review', ' space', ' plan', ' help', ' work', ' use', ' a', '.', '\n')
MAX_CANDIDATES_PER_ROW = 4096


def encode_text(tokenizer, text):
    messages = [{'role': 'user', 'content': text}]
    rendered = tokenizer.apply_chat_template(messages, thinking=False, tokenize=False)
    require(isinstance(rendered, str), 'native renderer did not return text')
    ids = tokenizer(rendered, add_special_tokens=False, truncation=False)['input_ids']
    require(isinstance(ids, list) and all(type(i) is int and i >= 0 for i in ids),
            'native tokenizer did not return full integer IDs')
    return rendered, ids


def verify_exact(tokenizer, text):
    rendered, untruncated = encode_text(tokenizer, text)
    http = tokenizer(rendered, add_special_tokens=False, truncation=True,
                     max_length=HTTP_MAX_LENGTH)['input_ids']
    native = tokenizer.apply_chat_template([{'role': 'user', 'content': text}], thinking=False, tokenize=True)
    require(http == untruncated == native, 'HTTP/native tokenizer drift or truncation')
    require(Q.token_ids(http, TARGET_LENGTH), 'fully templated prompt must be exactly 2048 IDs without truncation')
    return rendered, list(http)


def construct_row(tokenizer, index):
    topic, instruction = TOPICS[index]
    text = (instruction + '\nUse the planning notes below as reference material. '
            'Summarize the three most useful practical recommendations in a short answer.\n'
            'Planning record: ' + topic + '.\n')
    calls = 0
    def measure(candidate):
        nonlocal calls
        calls += 1
        require(calls <= MAX_CANDIDATES_PER_ROW, 'bounded real-tokenizer construction exhausted')
        return encode_text(tokenizer, candidate)[1]
    ids = measure(text)
    require(len(ids) < TARGET_LENGTH, 'initial real prompt exceeds exact-length target')
    # Only accept complete, freshly measured candidates. No additive token-count
    # assumptions, binary-search monotonicity, ID slices, or encode/decode seeds.
    for step in range(256):
        candidate = text + NOTES[(step + index) % len(NOTES)] + '\n'
        trial = measure(candidate)
        if len(ids) < len(trial) <= TARGET_LENGTH:
            text, ids = candidate, trial
        else:
            break
        if len(ids) == TARGET_LENGTH:
            break
    if len(ids) < TARGET_LENGTH:
        candidate = text + '\nReference keywords:'
        trial = measure(candidate)
        if len(ids) < len(trial) <= TARGET_LENGTH:
            text, ids = candidate, trial
    while len(ids) < TARGET_LENGTH:
        for word in KEYWORDS:
            candidate = text + word
            trial = measure(candidate)
            if len(ids) < len(trial) <= TARGET_LENGTH:
                text, ids = candidate, trial
                break
        else:
            raise RuntimeError('no bounded exact-length text extension; no truncation fallback')
    rendered, final_ids = verify_exact(tokenizer, text)
    require(final_ids == ids, 'tokenizer changed during construction')
    return {'id': index, 'text': text, 'prompt_ids': final_ids}, {
        'id': index, 'rendered_text': rendered, 'rendered_sha256': Q.digest(rendered.encode()),
        'candidate_calls': calls, 'untruncated_length': len(final_ids)}


def run(bindings_path, output_directory):
    isolate()
    support_sources = preparation_sources()
    raw = Q.read_owned_bytes(bindings_path)
    bindings = Q.json_bytes(raw)
    export, sources, weights = validate_bindings(bindings)
    directory = Path(output_directory)
    require(directory.is_absolute() and directory.resolve() == directory and not directory.exists(),
            'new canonical absolute output directory required')
    immutable = [Path(bindings['model_root']), Path(bindings['native_root']), Path(bindings_path),
                 Path(bindings['weights_manifest']['path']), Path(bindings['runtime_metadata']['path'])]
    require(not any(directory == p or directory in p.parents or p in directory.parents for p in immutable),
            'output directory overlaps immutable model/source/provenance inputs')
    # No AutoTokenizer or model import: this is the real installed CPU backend.
    transformers = importlib.import_module('transformers')
    tokenizers = importlib.import_module('tokenizers')
    packages = {name: package_evidence(name, module) for name, module in
                [('transformers', transformers), ('tokenizers', tokenizers)]}
    for name, evidence in packages.items():
        require(evidence['version'] == export['identity'][name + '_version'],
                name + ' CPU/native runtime version drift')
    tokenizer = native_tokenizer(sources, bindings['model_root'], transformers.TokenizersBackend)
    rendered_rows = []
    for index in range(16):
        row, rendered = construct_row(tokenizer, index)
        export['rows'].append(row)
        rendered_rows.append(rendered)
    Q.validate_export(export)
    # Catch source, asset, metadata, package, and tokenizer changes before publish.
    second, final_sources, final_weights = validate_bindings(bindings)
    require(final_sources == sources and final_weights == weights
            and second['identity'] == export['identity'], 'input provenance changed during export')
    for row, rendered in zip(export['rows'], rendered_rows):
        text, ids = verify_exact(tokenizer, row['text'])
        require(text == rendered['rendered_text'] and ids == row['prompt_ids'],
                'tokenizer drift during final corpus verification')
    for name, module in [('transformers', transformers), ('tokenizers', tokenizers)]:
        require(package_evidence(name, module) == packages[name], name + ' package bytes changed during export')
    require(Q.read_owned_bytes(bindings_path) == raw, 'bindings changed during export')
    require(preparation_sources() == support_sources, 'preparation source changed during export')
    directory.mkdir(mode=0o700, exist_ok=False)
    bindings_pin = {'path': str(directory / 'bindings.json'),
                    'sha256': Q.exclusive_json(directory / 'bindings.json', bindings)}
    evidence = {'schema_version': 1, 'scope': 'CPU_PREPARATION_ONLY_NOT_FIELD_QUALIFICATION',
                'bindings': bindings_pin, 'input_bindings_sha256': Q.digest(raw),
                'native_runtime_metadata': bindings['runtime_metadata'],
                'projection': 'unchanged_native197_AST_bodies_with_explicit_CPU_dependencies',
                'source_files_sha256': SOURCE_FILES, 'preparation_sources': support_sources,
                'construction_recipe': 'native-full-template-bounded-text-v1', 'packages': packages,
                'weights': {'manifest': bindings['weights_manifest'], 'file_count': len(weights),
                            'weight_bytes_rehashed': False},
                'environment': {key: os.environ.get(key) for key in ENVIRONMENT},
                'python_version': sys.version, 'bytecode_prefix': sys.pycache_prefix,
                'rendered_rows': rendered_rows}
    evidence_pin = {'path': str(directory / 'cpu-evidence.json'),
                    'sha256': Q.exclusive_json(directory / 'cpu-evidence.json', evidence)}
    export_pin = {'path': str(directory / 'tokenizer-export.json'),
                  'sha256': Q.exclusive_json(directory / 'tokenizer-export.json', export)}
    corpus_pin = Q.prepare(export_pin['path'], directory / 'prepared')
    result = {'tokenizer_export': export_pin, 'cpu_evidence': evidence_pin, 'corpus': corpus_pin}
    result['receipt_sha256'] = Q.exclusive_json(directory / 'receipt.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bindings', required=True, help='retained actual asset/runtime pin JSON')
    parser.add_argument('--output-directory', required=True, help='new owner-controlled evidence directory')
    args = parser.parse_args(argv)
    try:
        result = run(args.bindings, args.output_directory)
    except (RuntimeError, ValueError, OSError, ImportError) as error:
        parser.exit(1, 'native CPU export blocked: ' + str(error) + '\n')
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
