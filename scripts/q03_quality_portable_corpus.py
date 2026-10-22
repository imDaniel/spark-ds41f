#!/usr/bin/env python3
"""Real CPU-only sixty-prompt preparation, followed by separate full site binding.

Cloud preparation is NOT_REPLAY_ADMISSIBLE, not native ARM/runtime evidence.
No model, candidate/reference code, generator, endpoint or weight shard is run/read.
"""
from __future__ import annotations
import argparse
import copy
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import platform
import sys

# Install exact source selection before the FIRST task-helper import. -B alone
# does not prevent cache reads, package shadows or native-extension selection.
BYTECODE_PREFIX = '/__q03_cpu_export_unused_bytecode__'
HELPER_NAMES = frozenset({'q03_legacy_portable_corpus', 'q03_legacy_corpus_export',
    'q03_native_corpus_export', 'q03_legacy_case_replay', 'q03_qualification',
    'q03_owner_model', 'q03_held_window'})
if Path(BYTECODE_PREFIX).exists():
    raise RuntimeError('isolated unused bytecode prefix already exists')
if any(name in sys.modules for name in HELPER_NAMES):
    raise RuntimeError('use a fresh interpreter without preloaded task helpers')


class HelperSourceFinder:
    def find_spec(self, fullname, path=None, target=None):
        if fullname in HELPER_NAMES:
            source = Path(__file__).resolve().with_name(fullname + '.py')
            loader = importlib.machinery.SourceFileLoader(fullname, str(source))
            return importlib.util.spec_from_file_location(fullname, source, loader=loader)
        return None


_before_bytecode = sys.dont_write_bytecode, sys.pycache_prefix
sys.dont_write_bytecode, sys.pycache_prefix = True, BYTECODE_PREFIX
_finder = HelperSourceFinder()
sys.meta_path.insert(0, _finder)
try:
    import q03_legacy_portable_corpus as P
finally:
    sys.meta_path.remove(_finder)
    if __name__ != '__main__':
        sys.dont_write_bytecode, sys.pycache_prefix = _before_bytecode

E, N, Q, L = P.E, P.N, P.Q, P.L
require = Q.require
WORKLOAD = 'quality_pilot60_v1'
KIND = 'quality_pilot60_portable_preparation'
MATERIAL_SHA256 = 'c3db307e1f145026c33252c36cda819ffefda1d189400d6e06d316454f3bf580'
PROMPTS_SHA256 = '8cdcf6143d80d4776c052166a9764d36d8b5020038ad24118e352f3493cf4ca0'
CASES_SHA256 = '0b124c8a98c6f3b2e57fa7171ed2fde140b514e0bbb657a05701e5ea29f9a715'
CASE_INDEX_SHA256 = 'd2a29f5730371fd0e0d926b2114766b8078a058457902248ea30c1b17e27ff97'
DEFAULT_RENDERING = {'thinking': True, 'reasoning_effort': 'high',
                     'add_generation_prompt': True, 'overrides': {}}
CASE_FIELDS = {'id', 'category', 'language', 'prompt_sha256', 'prompt_utf8_bytes',
    'rendered_text', 'rendered_sha256', 'rendered_utf8_bytes',
    'input_ids', 'input_ids_sha256', 'input_token_count'}


def isolate():
    P.isolate()
    for name in HELPER_NAMES:
        module = sys.modules[name]
        source = Path(__file__).resolve().with_name(name + '.py')
        require(Path(module.__file__).resolve() == source
            and isinstance(module.__loader__, importlib.machinery.SourceFileLoader)
            and module.__spec__.cached.startswith(BYTECODE_PREFIX + '/'),
            'task helper was not loaded from exact isolated source: ' + name)


def support_sources():
    value = P.support_sources()
    path = Path(__file__).resolve()
    value[path.name] = {'path': str(path), 'sha256': Q.digest(Q.read_owned_bytes(path))}
    return value


def prompt_rows(raw):
    require(Q.digest(raw) == PROMPTS_SHA256, 'exact generation-only prompt bytes differ')
    rows = [Q.json_bytes(line) for line in raw.splitlines()]
    require(len(rows) == 60 and all(isinstance(p, dict) and set(p) == {'id', 'text'}
        and isinstance(p['id'], str) and isinstance(p['text'], str) for p in rows)
        and len({p['id'] for p in rows}) == 60, 'sixty exact unique prompts required')
    return rows


def read_materials(directory):
    root = E.canonical_root(directory, 'reviewed material directory')
    sums = Q.read_owned_bytes(root / 'SHA256SUMS')
    require(Q.digest(sums) == MATERIAL_SHA256, 'reviewed material snapshot differs')
    snapshot = {'SHA256SUMS': sums}
    for line in sums.decode('utf-8').splitlines():
        digest, relative = line.split('  ', 1)
        require(relative.startswith('./') and N.plain_relative(relative[2:])
            and L.sha(digest) and relative[2:] not in snapshot, 'material inventory entry differs')
        raw = Q.read_owned_bytes(root / relative[2:])
        require(Q.digest(raw) == digest, 'material file drift: ' + relative)
        snapshot[relative[2:]] = raw
    require(len(snapshot) == 35 and Q.digest(snapshot['cases-with-gold.json']) == CASES_SHA256,
            'complete original material inventory required')
    prompts = prompt_rows(snapshot['prompts.jsonl'])
    # Read metadata only as JSON data; gold/reference source is never imported.
    cases = Q.json_bytes(snapshot['cases-with-gold.json'])
    require(isinstance(cases, list) and len(cases) == 60, 'material case count differs')
    metadata = [{k: c[k] for k in ('id', 'category', 'language')} for c in cases]
    require(Q.digest(Q.canonical(metadata)) == CASE_INDEX_SHA256, 'material case index differs')
    for prompt, case in zip(prompts, cases):
        require(prompt['id'] == case['id'] and prompt['text'] == case['prompt_text']
            and Q.digest(prompt['text'].encode()) == case['prompt_utf8_sha256']
            and len(prompt['text'].encode()) == case['prompt_utf8_bytes'], 'material prompt bytes differ')
    return root, snapshot, prompts, metadata


def validate_content(value):
    require(isinstance(value, dict) and set(value) == {'schema_version', 'workload', 'model',
        'material_sha256', 'prompts_sha256', 'prompts_jsonl', 'tokenizer_identity',
        'default_rendering', 'chat_tokenization', 'cases'}, 'portable quality content fields differ')
    require(type(value['schema_version']) is int and value['schema_version'] == 1
        and value['workload'] == WORKLOAD and value['model'] == L.MODEL
        and value['material_sha256'] == MATERIAL_SHA256 and value['prompts_sha256'] == PROMPTS_SHA256,
        'portable quality identity differs')
    require(Q.canonical(value['default_rendering']) == Q.canonical(DEFAULT_RENDERING)
        and Q.canonical(value['chat_tokenization']) == Q.canonical(P.CHAT_OPTIONS),
        'unmodified default thinking/high full rendering required')
    L.validate_tokenizer_identity(value['tokenizer_identity'])
    identity = value['tokenizer_identity']
    require(identity['model_revision'] == identity['tokenizer_revision'] == N.MODEL_REVISION
        and identity['model_config_sha256'] == P.REGISTERED_ASSETS['config.json']
        and identity['tokenizer_assets_sha256'] == {k: v for k, v in P.REGISTERED_ASSETS.items() if k != 'config.json'}
        and identity['special_tokens_sha256'] == P.REGISTERED_ASSETS['tokenizer_config.json']
        and identity['encoding_source_sha256'] == Q.NATIVE_SOURCES['deepseek_v41_encoding.py']
        and identity['chat_template_sha256'] == Q.TEMPLATE_SHA, 'registered tokenizer identity differs')
    require(isinstance(value['prompts_jsonl'], str), 'complete original prompt JSONL required')
    prompts = prompt_rows(value['prompts_jsonl'].encode('utf-8'))
    cases = value['cases']
    require(isinstance(cases, list) and len(cases) == 60, 'all sixty complete vectors required')
    metadata = []
    for prompt, case in zip(prompts, cases):
        require(isinstance(case, dict) and set(case) == CASE_FIELDS and case['id'] == prompt['id']
            and case['prompt_sha256'] == Q.digest(prompt['text'].encode())
            and type(case['prompt_utf8_bytes']) is int and case['prompt_utf8_bytes'] == len(prompt['text'].encode())
            and isinstance(case['rendered_text'], str) and case['rendered_text']
            and case['rendered_sha256'] == Q.digest(case['rendered_text'].encode())
            and type(case['rendered_utf8_bytes']) is int and case['rendered_utf8_bytes'] == len(case['rendered_text'].encode())
            and L.ids(case['input_ids']) and type(case['input_token_count']) is int
            and case['input_token_count'] == len(case['input_ids'])
            and case['input_ids_sha256'] == Q.digest(Q.canonical(case['input_ids'])),
            'portable case prompt/rendering/vector evidence differs')
        metadata.append({k: case[k] for k in ('id', 'category', 'language')})
    require(Q.digest(Q.canonical(metadata)) == CASE_INDEX_SHA256, 'portable case index differs')
    return value


def construct_content(tokenizer, identity, snapshot, prompts, metadata):
    rows = []
    for prompt, info in zip(prompts, metadata):
        rendered, ids = E.chat_ids(tokenizer, prompt['text'])
        rows.append({**info, 'prompt_sha256': Q.digest(prompt['text'].encode()),
            'prompt_utf8_bytes': len(prompt['text'].encode()), 'rendered_text': rendered,
            'rendered_sha256': Q.digest(rendered.encode()), 'rendered_utf8_bytes': len(rendered.encode()),
            'input_ids': ids, 'input_ids_sha256': Q.digest(Q.canonical(ids)), 'input_token_count': len(ids)})
    return validate_content(dict(schema_version=1, workload=WORKLOAD, model=L.MODEL,
        material_sha256=MATERIAL_SHA256, prompts_sha256=PROMPTS_SHA256,
        prompts_jsonl=snapshot['prompts.jsonl'].decode('utf-8'), tokenizer_identity=identity,
        default_rendering=copy.deepcopy(DEFAULT_RENDERING), chat_tokenization=copy.deepcopy(P.CHAT_OPTIONS), cases=rows))


def frozen_vectors(tokenizer, content):
    rows = []
    for prompt, case in zip(prompt_rows(content['prompts_jsonl'].encode()), content['cases']):
        rendered, ids = E.chat_ids(tokenizer, prompt['text'])
        require(rendered == case['rendered_text'] and ids == case['input_ids'],
                'frozen full rendered text or complete IDs differ: ' + case['id'])
        rows.append({k: case[k] for k in ('id', 'rendered_sha256', 'input_ids_sha256', 'input_token_count')})
    require(len(rows) == 60, 'all sixty vectors must compare')
    return rows


def read_preparation(path, expected_sha256):
    require(L.sha(expected_sha256), 'portable preparation hash required')
    raw = Q.read_owned_bytes(path)
    require(Q.digest(raw) == expected_sha256, 'portable preparation file hash differs')
    value = Q.json_bytes(raw)
    require(Q.canonical(value) == raw and isinstance(value, dict)
        and set(value) == {'schema_version', 'kind', 'admissibility', 'content_sha256', 'content'}
        and type(value['schema_version']) is int and value['schema_version'] == 1
        and value['kind'] == KIND and value['admissibility'] == P.ADMISSIBILITY,
        'non-admissible portable preparation schema differs')
    validate_content(value['content'])
    require(Q.digest(Q.canonical(value['content'])) == value['content_sha256'], 'portable content hash differs')
    return raw, value


def write_bytes(path, raw):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())


def prepare(bindings_path, materials_directory, output_directory):
    isolate()
    support = support_sources()
    raw = Q.read_owned_bytes(bindings_path)
    bindings = Q.json_bytes(raw)
    identity, sources, toolbox, manifest = P.validate_cloud_bindings(bindings)
    material_root, snapshot, prompts, metadata = read_materials(materials_directory)
    immutable = [bindings_path, material_root, bindings['model_root'], bindings['weights_manifest']['path'],
        bindings['toolbox_root'], *(x['root'] for x in bindings['native_sources'].values()),
        *(x['path'] for x in support.values())]
    directory = P.new_output(output_directory, immutable)
    modules, packages = P.packages()
    tokenizer = N.native_tokenizer(sources['native197-off'], bindings['model_root'], modules['transformers'].TokenizersBackend)
    content = construct_content(tokenizer, identity, snapshot, prompts, metadata)
    require(P.validate_cloud_bindings(bindings) == (identity, sources, toolbox, manifest)
        and read_materials(materials_directory) == (material_root, snapshot, prompts, metadata),
        'immutable cloud inputs changed')
    P.recheck_packages(modules, packages)
    require(Q.read_owned_bytes(bindings_path) == raw and support_sources() == support,
            'cloud bindings/source changed')
    preparation = dict(schema_version=1, kind=KIND, admissibility=P.ADMISSIBILITY,
                       content_sha256=Q.digest(Q.canonical(content)), content=content)
    directory.mkdir(mode=0o700, exist_ok=False)
    for relative, data in snapshot.items():
        write_bytes(directory / 'materials' / relative, data)
    write_bytes(directory / 'raw-inputs' / 'cloud-bindings.json', raw)
    write_bytes(directory / 'raw-inputs' / 'weights-manifest.json', manifest)
    for name, pin in support.items():
        data = Q.read_owned_bytes(pin['path'])
        require(Q.digest(data) == pin['sha256'], 'retained helper source changed')
        write_bytes(directory / 'preparation-sources' / name, data)
    for arm, files in sources.items():
        for name, data in files.items():
            write_bytes(directory / 'native-sources' / arm / name, data)
    prep_pin = {'path': str(directory / 'portable-preparation.json'),
                'sha256': Q.exclusive_json(directory / 'portable-preparation.json', preparation)}
    evidence = dict(schema_version=1, scope='TOKENIZER_ONLY_CLOUD_NOT_FIELD_QUALIFICATION',
        admissibility=P.ADMISSIBILITY, preparation=prep_pin, content_sha256=preparation['content_sha256'],
        material_sha256=MATERIAL_SHA256, bindings=bindings, input_bindings_sha256=Q.digest(raw),
        preparation_sources=support, native_source_files_sha256=E.SOURCE_FILES,
        source_scope='verified_selected_source_bytes_not_full_git_tree_or_executing_image',
        packages=packages, environment={key: os.environ.get(key) for key in N.ENVIRONMENT},
        python=E.python_identity(), python_version=sys.version, architecture=platform.machine(),
        native_arm_execution=False, bytecode_prefix=sys.pycache_prefix,
        blocked_import_roots=sorted(N.FORBIDDEN_IMPORTS),
        weights=dict(manifest=bindings['weights_manifest'], raw_manifest_sha256=Q.digest(manifest),
            file_count=83, weights_present_verified=False, weight_bytes_rehashed=False),
        chat_tokenization=P.CHAT_OPTIONS, generated_prompt_vectors=60,
        model_generation_performed=False, candidate_or_reference_execution=False)
    evidence_pin = {'path': str(directory / 'cloud-provenance.json'),
        'sha256': Q.exclusive_json(directory / 'cloud-provenance.json', evidence)}
    result = dict(preparation=prep_pin, content_sha256=preparation['content_sha256'],
                  cloud_provenance=evidence_pin, admissibility=P.ADMISSIBILITY)
    result['receipt_sha256'] = Q.exclusive_json(directory / 'receipt.json', result)
    return result


def bind_site(preparation_path, preparation_sha256, bindings_path, materials_directory, output_directory):
    isolate()
    support = support_sources()
    prep_raw, preparation = read_preparation(preparation_path, preparation_sha256)
    raw = Q.read_owned_bytes(bindings_path)
    bindings = Q.json_bytes(raw)
    # Unchanged complete site proof, including 83-file existence and actual
    # runtime version pins. Cloud assets are insufficient; never relax this.
    native, export, sources, toolbox, weights = E.validate_bindings(bindings)
    material_root, snapshot, prompts, metadata = read_materials(materials_directory)
    content = preparation['content']
    require(content['tokenizer_identity'] == {k: export['identity'][k] for k in L.TOKENIZER_FIELDS}
        and content['prompts_jsonl'].encode() == snapshot['prompts.jsonl'], 'portable/site input identity differs')
    immutable = [preparation_path, bindings_path, material_root, bindings['native_cpu_bindings']['path'],
        native['model_root'], native['weights_manifest']['path'], native['runtime_metadata']['path'],
        bindings['toolbox_root'], *(x['root'] for x in bindings['native_sources'].values()),
        *(x['path'] for x in support.values())]
    directory = P.new_output(output_directory, immutable)
    modules, packages = P.packages()
    for name, evidence in packages.items():
        require(evidence['version'] == export['identity'][name + '_version'], name + ' CPU/native runtime version drift')
    tokenizer = N.native_tokenizer(sources['native197-off'], native['model_root'], modules['transformers'].TokenizersBackend)
    rows = frozen_vectors(tokenizer, content)
    corpus = dict(schema_version=1, workload=WORKLOAD, model=L.MODEL, material_sha256=MATERIAL_SHA256,
        prompts={'path': str(material_root / 'prompts.jsonl'), 'sha256': PROMPTS_SHA256},
        tokenizer_identity=copy.deepcopy(content['tokenizer_identity']), artifacts=copy.deepcopy(export['artifacts']),
        cases=[{k: copy.deepcopy(c[k]) for k in ('id', 'category', 'language', 'prompt_sha256', 'input_ids')}
               for c in content['cases']], default_rendering=copy.deepcopy(DEFAULT_RENDERING))
    L.validate_tokenizer_identity(corpus['tokenizer_identity']); L.artifact_files(corpus)
    require(E.validate_bindings(bindings) == (native, export, sources, toolbox, weights)
        and read_materials(materials_directory) == (material_root, snapshot, prompts, metadata), 'site inputs changed')
    P.recheck_packages(modules, packages)
    require(Q.read_owned_bytes(preparation_path) == prep_raw and Q.read_owned_bytes(bindings_path) == raw
        and support_sources() == support, 'immutable preparation/site bindings/source changed')
    directory.mkdir(mode=0o700, exist_ok=False)
    corpus_pin = {'path': str(directory / 'corpus.json'), 'sha256': Q.exclusive_json(directory / 'corpus.json', corpus)}
    receipt = dict(schema_version=1, scope='SITE_CPU_INPUT_BINDING_NOT_FIELD_QUALIFICATION',
        preparation_sha256=Q.digest(prep_raw), content_sha256=preparation['content_sha256'],
        material_sha256=MATERIAL_SHA256, site_bindings_sha256=Q.digest(raw), site_bindings=bindings,
        site_artifacts=export['artifacts'], final_corpus=corpus_pin,
        comparison=dict(chat_vectors=60, complete_ids_equal=True, complete_rendered_text_equal=True,
                        regenerated_prompts=False), packages=packages, preparation_sources=support,
        chat_tokenization=P.CHAT_OPTIONS, compared_rows=rows)
    receipt_pin = {'path': str(directory / 'site-link-receipt.json'),
        'sha256': Q.exclusive_json(directory / 'site-link-receipt.json', receipt)}
    return dict(corpus=corpus_pin, site_link_receipt=receipt_pin)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prepare_parser = sub.add_parser('prepare-tokenizer-only')
    bind_parser = sub.add_parser('bind-site')
    for item in (prepare_parser, bind_parser):
        item.add_argument('--bindings', required=True)
        item.add_argument('--materials', required=True)
        item.add_argument('--output-directory', required=True)
    bind_parser.add_argument('--preparation', required=True)
    bind_parser.add_argument('--preparation-sha256', required=True)
    args = parser.parse_args(argv)
    try:
        result = (prepare(args.bindings, args.materials, args.output_directory)
            if args.command == 'prepare-tokenizer-only' else bind_site(args.preparation,
                args.preparation_sha256, args.bindings, args.materials, args.output_directory))
        print(json.dumps(result, sort_keys=True))
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, ImportError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
