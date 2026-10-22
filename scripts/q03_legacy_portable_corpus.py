#!/usr/bin/env python3
"""Explicit tokenizer-only cloud preparation, then separate site binding.

Preparation is NOT_REPLAY_ADMISSIBLE. It carries no site paths or runtime claims.
Binding verifies the unchanged full site inputs and compares frozen token vectors;
it never calls the document generator or rewrites the portable preparation.
"""
from __future__ import annotations
import argparse
import copy
import importlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import platform
import sys
import sysconfig

# -B disables cache writes, not cache reads. Install the absent source-only
# prefix before the very first task-helper import, including the exporter.
BYTECODE_PREFIX = '/__q03_cpu_export_unused_bytecode__'
if Path(BYTECODE_PREFIX).exists():
    raise RuntimeError('isolated unused bytecode prefix already exists')
HELPER_NAMES = frozenset({'q03_legacy_corpus_export', 'q03_native_corpus_export',
                          'q03_legacy_case_replay', 'q03_qualification',
                          'q03_owner_model', 'q03_held_window'})
_preimported_helpers = any(name in sys.modules for name in HELPER_NAMES)


class HelperSourceFinder:
    # The prefix excludes .pyc; the explicit source loader also excludes ambient
    # native-extension/sourceless module selection for this finite closure.
    def find_spec(self, fullname, path=None, target=None):
        if fullname in HELPER_NAMES:
            source = Path(__file__).resolve().with_name(fullname + '.py')
            loader = importlib.machinery.SourceFileLoader(fullname, str(source))
            return importlib.util.spec_from_file_location(fullname, source, loader=loader)
        return None


_before_bytecode = sys.dont_write_bytecode, sys.pycache_prefix
sys.dont_write_bytecode, sys.pycache_prefix = True, BYTECODE_PREFIX
_helper_source_finder = HelperSourceFinder()
sys.meta_path.insert(0, _helper_source_finder)
try:
    import q03_legacy_corpus_export as E
finally:
    sys.meta_path.remove(_helper_source_finder)
    if __name__ != '__main__':
        sys.dont_write_bytecode, sys.pycache_prefix = _before_bytecode

N, Q, L = E.N, E.Q, E.L
require = Q.require
KIND = 'legacy_warm4k_portable_preparation'
ADMISSIBILITY = 'NOT_REPLAY_ADMISSIBLE'
REGISTERED_ASSETS = {
    'config.json': '8be45ce0476004a3f529fd896115a4a2e800a129ad2d3ec05b16050f52e21879',
    'tokenizer.json': 'c90dfa01249db1be4245780a052ede752e1361c612ac6d08e2bdada7d599476b',
    'tokenizer_config.json': '6ac8c8dc065ed118161d02dd532749ae3f52c243deac27872134fae2f50d8547',
}
RAW_OPTIONS = {'add_special_tokens': True, 'truncation': False}
CHAT_OPTIONS = {**E.CHAT_OPTIONS, 'add_special_tokens': False, 'truncation': False,
                'thinking_override': 'ABSENT', 'enable_thinking_override': 'ABSENT',
                'reasoning_effort_override': 'ABSENT'}


def isolate():
    require(not _preimported_helpers, 'use a fresh interpreter without preloaded task helpers')
    E.isolate()
    cached = getattr(getattr(E, '__spec__', None), 'cached', None)
    require(isinstance(cached, str) and cached.startswith(BYTECODE_PREFIX + '/'),
            'initial exporter loaded before source-only isolation; use a fresh interpreter')


def support_sources():
    value = E.preparation_sources()
    path = Path(__file__).resolve()
    value[path.name] = {'path': str(path), 'sha256': Q.digest(Q.read_owned_bytes(path))}
    return value


def validate_cloud_bindings(value):
    require(isinstance(value, dict) and set(value) == {'schema_version', 'mode', 'model_repository',
            'model_revision', 'tokenizer_revision', 'model_root', 'native_sources',
            'weights_manifest', 'toolbox_root'} and type(value['schema_version']) is int
            and value['schema_version'] == 1 and value['mode'] == 'TOKENIZER_ONLY_CLOUD',
            'explicit tokenizer-only cloud bindings required')
    require(value['model_repository'] == N.MODEL_REPOSITORY
            and value['model_revision'] == value['tokenizer_revision'] == N.MODEL_REVISION,
            'registered 1017 model/tokenizer revision differs')
    root = E.canonical_root(value['model_root'], 'tokenizer-only model root')
    observed = {p.name for p in root.iterdir() if p.is_file() and
                (p.name.startswith(('tokenizer', 'special_tokens', 'added_tokens', 'chat_template'))
                 or p.suffix in {'.jinja', '.model'} or p.name in {'vocab.json', 'vocab.txt', 'merges.txt'})}
    require(observed == {'tokenizer.json', 'tokenizer_config.json'}, 'registered tokenizer asset closure differs')
    for name, expected in REGISTERED_ASSETS.items():
        require(N.sha_file(root / name) == expected, 'registered cloud asset drift: ' + name)
    pin = value['weights_manifest']
    require(isinstance(pin, dict) and set(pin) == {'path', 'sha256'} and L.sha(pin['sha256'])
            and isinstance(pin['path'], str) and Path(pin['path']).is_absolute()
            and Path(pin['path']).resolve() == Path(pin['path']), 'retained raw manifest pin required')
    raw = Q.read_owned_bytes(pin['path'])
    require(Q.digest(raw) == pin['sha256'], 'retained raw manifest hash differs')
    inventory = N.weight_inventory(raw)
    for name, expected in REGISTERED_ASSETS.items():
        entry = inventory.get(name)
        require((entry.get('sha256') if isinstance(entry, dict) else entry) == expected,
                'registered asset differs from retained 83-file manifest: ' + name)
    # No weight existence claim or access is made. Rich replica metadata is not
    # rewritten, normalized or interpreted as portable content identity.
    identity = {'model_revision': N.MODEL_REVISION, 'tokenizer_revision': N.MODEL_REVISION,
                'model_config_sha256': REGISTERED_ASSETS['config.json'],
                'tokenizer_assets_sha256': {k: v for k, v in REGISTERED_ASSETS.items() if k != 'config.json'},
                'weights_manifest_sha256': pin['sha256'],
                'encoding_source_sha256': Q.NATIVE_SOURCES['deepseek_v41_encoding.py'],
                'chat_template_sha256': Q.TEMPLATE_SHA,
                'special_tokens_sha256': REGISTERED_ASSETS['tokenizer_config.json']}
    sources = E.read_native_sources(value['native_sources'])
    toolbox = E.read_toolbox(value['toolbox_root'])
    return identity, sources, toolbox, raw



def validate_content(value):
    expected = {'schema_version', 'workload', 'model', 'tokenizer_identity', 'generator', 'prompts',
                'prompts_sha256', 'documents', 'historical_document_identity'}
    require(isinstance(value, dict) and set(value) == expected and type(value['schema_version']) is int
            and value['schema_version'] == 1 and value['workload'] == L.WORKLOAD and value['model'] == L.MODEL,
            'finite warm4K corpus schema differs')
    L.validate_tokenizer_identity(value['tokenizer_identity'])
    identity = value['tokenizer_identity']
    require(identity['model_revision'] == identity['tokenizer_revision'] == N.MODEL_REVISION
            and identity['model_config_sha256'] == REGISTERED_ASSETS['config.json']
            and identity['tokenizer_assets_sha256'] == {k: v for k, v in REGISTERED_ASSETS.items() if k != 'config.json'}
            and identity['special_tokens_sha256'] == REGISTERED_ASSETS['tokenizer_config.json']
            and identity['encoding_source_sha256'] == Q.NATIVE_SOURCES['deepseek_v41_encoding.py']
            and identity['chat_template_sha256'] == Q.TEMPLATE_SHA, 'registered portable tokenizer identity differs')
    require(value['historical_document_identity'] == 'UNAVAILABLE_NEW_SHARED_CORPUS',
            'historical document bytes were not retained; never claim exact historical replay')
    prompts = value['prompts']
    require(isinstance(prompts, list) and len(prompts) == 16
            and all(set(p) == {'id', 'text'} and p['id'] == f'd{i:03d}' and isinstance(p['text'], str)
                    for i, p in enumerate(prompts))
            and Q.digest(json.dumps(prompts, sort_keys=True).encode()) == value['prompts_sha256'] == L.PROMPTS_SHA256,
            'original sixteen question bytes differ')
    gen = value['generator']
    require(isinstance(gen, dict) and set(gen) == {'toolbox_head', 'toolbox_files', 'driver_sha256',
            'context_tokens', 'seed', 'python', 'stdlib_sources'} and gen['toolbox_head'] == L.TOOLBOX_HEAD
            and gen['toolbox_files'] == L.toolbox_inventory()['files'] and gen['driver_sha256'] == L.ORIGINAL_DRIVER_SHA256
            and type(gen['context_tokens']) is int and gen['context_tokens'] == 4096
            and type(gen['seed']) is int and gen['seed'] == 2048, 'original document generator identity differs')
    require(isinstance(gen['python'], dict) and set(gen['python']) == {'implementation', 'version', 'executable_sha256'}
            and all(isinstance(gen['python'][k], str) and gen['python'][k] for k in ('implementation', 'version'))
            and L.sha(gen['python']['executable_sha256']), 'Python generator environment identity required')
    sources = gen['stdlib_sources']
    require(isinstance(sources, list) and sources and all(isinstance(s, dict) and set(s) == {'name', 'size', 'sha256'}
            and isinstance(s['name'], str) and Path(s['name']).name == s['name'] and s['name'].endswith('.py')
            and type(s['size']) is int and s['size'] > 4096 and L.sha(s['sha256']) for s in sources)
            and [s['name'] for s in sources] == sorted(set(s['name'] for s in sources)), 'stdlib source inventory differs')
    docs = value['documents']
    require(isinstance(docs, list) and len(docs) == 4, 'all four real shared documents required, including C1')
    for slot, doc in enumerate(docs):
        require(isinstance(doc, dict) and set(doc) == {'slot', 'text', 'text_sha256', 'raw_ids', 'calibration', 'chat_inputs'}
                and type(doc['slot']) is int and doc['slot'] == slot
                and isinstance(doc['text'], str) and doc['text'].startswith(f'Document {2048000 + slot}:\n')
                and Q.digest(doc['text'].encode()) == doc['text_sha256'] and L.ids(doc['raw_ids']), 'real frozen document differs')
        calibration = doc['calibration']
        require(isinstance(calibration, list) and len(calibration) == 2, 'original two-step tokenizer rescaling required')
        for item in calibration:
            require(isinstance(item, dict) and set(item) == {'text', 'response'} and isinstance(item['text'], str)
                    and isinstance(item['response'], dict) and set(item['response']) == {'tokens', 'count'}
                    and L.ids(item['response'].get('tokens'))
                    and type(item['response'].get('count')) is int
                    and item['response']['count'] == len(item['response']['tokens']), 'complete calibration text/IDs required')
        require(calibration[1]['text'] == doc['text'] and calibration[1]['response']['tokens'] == doc['raw_ids'],
                'final calibrated bytes/IDs differ')
        # The original generator always truncates character count, not token IDs.
        prefix = f'Document {2048000 + slot}:\n'
        require(len(calibration[0]['text']) == len(prefix) + int(4096 * 3.8)
                and len(doc['text']) == len(prefix) + int(4096 * (len(calibration[0]['text']) /
                                                       calibration[0]['response']['count'])),
                'original one-step character rescaling differs')
        rows = doc['chat_inputs']
        require(isinstance(rows, list) and len(rows) == 16, 'all slot/question template IDs required')
        for prompt, row in zip(prompts, rows):
            require(isinstance(row, dict) and set(row) == {'prompt_id', 'prompt_ids'}
                    and row['prompt_id'] == prompt['id'] and L.ids(row['prompt_ids']), 'complete template input IDs required')
    require(len({doc['text_sha256'] for doc in docs}) == 4, 'four distinct documents required')
    return value


def stdlib_snapshot(toolbox):
    return {path.name: path.read_bytes()
            for path in E.project_generator(toolbox).standard_library_sources()}


def snapshot_inventory(snapshot):
    return [{'name': name, 'size': len(raw), 'sha256': Q.digest(raw)}
            for name, raw in sorted(snapshot.items())]


def new_output(value, immutable):
    directory = Path(value)
    require(directory.is_absolute() and directory.resolve() == directory and not directory.exists(),
            'new canonical absolute output directory required')
    for path in immutable:
        path = Path(path).resolve()
        require(not (directory == path or directory in path.parents or path in directory.parents),
                'output directory overlaps immutable inputs')
    return directory


def packages():
    modules = {name: importlib.import_module(name) for name in ('transformers', 'tokenizers')}
    evidence = {name: N.package_evidence(name, module) for name, module in modules.items()}
    return modules, evidence


def recheck_packages(modules, evidence):
    for name, module in modules.items():
        require(N.package_evidence(name, module) == evidence[name], name + ' package bytes changed')


def frozen_vectors(tokenizer, content):
    """Re-encode only frozen texts: no generation, rescaling or stdlib lookup."""
    rows = []
    for doc in content['documents']:
        for calibration in doc['calibration']:
            require(E.raw_ids(tokenizer, calibration['text']) == calibration['response']['tokens'],
                    'frozen raw calibration IDs differ')
        for question, row in zip(content['prompts'], doc['chat_inputs']):
            rendered, ids = E.chat_ids(tokenizer, doc['text'] + '\n\n' + question['text'])
            require(ids == row['prompt_ids'], 'frozen full chat IDs differ')
            rows.append({'slot': doc['slot'], 'prompt_id': question['id'],
                         'rendered_sha256': Q.digest(rendered.encode()), 'token_count': len(ids)})
    require(len(rows) == 64, 'all 64 frozen chat vectors required')
    return rows


def read_preparation(path, expected_sha256):
    require(L.sha(expected_sha256), 'expected portable preparation hash required')
    raw = Q.read_owned_bytes(path)
    require(Q.digest(raw) == expected_sha256, 'portable preparation file hash differs')
    value = Q.json_bytes(raw)
    require(Q.canonical(value) == raw, 'canonical portable preparation bytes required')
    require(isinstance(value, dict) and set(value) == {'schema_version', 'kind', 'admissibility',
            'content_sha256', 'content'} and type(value['schema_version']) is int
            and value['schema_version'] == 1 and value['kind'] == KIND
            and value['admissibility'] == ADMISSIBILITY, 'non-admissible portable preparation schema differs')
    validate_content(value['content'])
    require(Q.digest(Q.canonical(value['content'])) == value['content_sha256'], 'portable content hash differs')
    return raw, value


def prepare(bindings_path, output_directory):
    isolate()
    support = support_sources()
    raw = Q.read_owned_bytes(bindings_path)
    bindings = Q.json_bytes(raw)
    identity, sources, toolbox, manifest_raw = validate_cloud_bindings(bindings)
    immutable = [bindings_path, bindings['model_root'], bindings['weights_manifest']['path'],
                 bindings['toolbox_root'], *(x['root'] for x in bindings['native_sources'].values()),
                 *(x['path'] for x in support.values()), sysconfig.get_paths()['stdlib']]
    directory = new_output(output_directory, immutable)
    snapshot = stdlib_snapshot(toolbox)
    modules, package_evidence = packages()
    tokenizer = N.native_tokenizer(sources['native197-off'], bindings['model_root'],
                                   modules['transformers'].TokenizersBackend)
    content, rendered = E.construct_corpus(tokenizer, {'identity': identity}, toolbox, content_only=True)
    validate_content(content)
    E.verify_corpus(tokenizer, content, rendered)
    require(snapshot_inventory(snapshot) == content['generator']['stdlib_sources']
            and stdlib_snapshot(toolbox) == snapshot, 'frozen stdlib source proof differs')
    require(validate_cloud_bindings(bindings) == (identity, sources, toolbox, manifest_raw),
            'cloud preparation inputs changed')
    recheck_packages(modules, package_evidence)
    require(Q.read_owned_bytes(bindings_path) == raw and support_sources() == support,
            'cloud preparation bindings/source changed')
    preparation = {'schema_version': 1, 'kind': KIND, 'admissibility': ADMISSIBILITY,
                   'content_sha256': Q.digest(Q.canonical(content)), 'content': content}
    directory.mkdir(mode=0o700, exist_ok=False)
    # Retain exact stdlib bytes, not a later site's potentially different Python.
    source_root = directory / 'stdlib-sources'
    source_root.mkdir(mode=0o700)
    for name, data in snapshot.items():
        fd = os.open(source_root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
    prep_pin = {'path': str(directory / 'portable-preparation.json'),
                'sha256': Q.exclusive_json(directory / 'portable-preparation.json', preparation)}
    evidence = {'schema_version': 1, 'scope': 'TOKENIZER_ONLY_CLOUD_NOT_FIELD_QUALIFICATION',
                'admissibility': ADMISSIBILITY, 'preparation': prep_pin,
                'content_sha256': preparation['content_sha256'], 'bindings': bindings,
                'input_bindings_sha256': Q.digest(raw), 'preparation_sources': support,
                'native_source_files_sha256': E.SOURCE_FILES,
                'source_scope': 'verified_selected_source_bytes_not_full_git_tree_or_executing_image',
                'packages': package_evidence,
                'environment': {key: os.environ.get(key) for key in N.ENVIRONMENT},
                'python_version': sys.version, 'architecture': platform.machine(),
                'bytecode_prefix': sys.pycache_prefix, 'blocked_import_roots': sorted(N.FORBIDDEN_IMPORTS),
                'weights': {'manifest': bindings['weights_manifest'], 'raw_manifest_sha256': Q.digest(manifest_raw),
                            'file_count': 83, 'weights_present_verified': False, 'weight_bytes_rehashed': False},
                'stdlib_sources_root': str(source_root), 'stdlib_sources': snapshot_inventory(snapshot),
                'raw_tokenization': RAW_OPTIONS, 'chat_tokenization': CHAT_OPTIONS,
                'rendered_rows': rendered}
    evidence_pin = {'path': str(directory / 'cloud-provenance.json'),
                    'sha256': Q.exclusive_json(directory / 'cloud-provenance.json', evidence)}
    result = {'preparation': prep_pin, 'content_sha256': preparation['content_sha256'],
              'cloud_provenance': evidence_pin, 'admissibility': ADMISSIBILITY}
    result['receipt_sha256'] = Q.exclusive_json(directory / 'receipt.json', result)
    return result


def bind_site(preparation_path, preparation_sha256, bindings_path, output_directory):
    isolate()
    support = support_sources()
    prep_raw, preparation = read_preparation(preparation_path, preparation_sha256)
    raw = Q.read_owned_bytes(bindings_path)
    bindings = Q.json_bytes(raw)
    # Do not weaken the original full snapshot/runtime/native/source binding.
    native, export, sources, toolbox, weights = E.validate_bindings(bindings)
    content = preparation['content']
    require(content['tokenizer_identity'] == {k: export['identity'][k] for k in L.TOKENIZER_FIELDS},
            'portable content/site tokenizer identity differs')
    immutable = [preparation_path, bindings_path, bindings['native_cpu_bindings']['path'],
                 native['model_root'], native['weights_manifest']['path'], native['runtime_metadata']['path'],
                 bindings['toolbox_root'], *(x['root'] for x in bindings['native_sources'].values()),
                 *(x['path'] for x in support.values())]
    directory = new_output(output_directory, immutable)
    modules, package_evidence = packages()
    for name, evidence in package_evidence.items():
        require(evidence['version'] == export['identity'][name + '_version'],
                name + ' CPU/native runtime version drift')
    tokenizer = N.native_tokenizer(sources['native197-off'], native['model_root'],
                                   modules['transformers'].TokenizersBackend)
    rows = frozen_vectors(tokenizer, content)
    corpus = copy.deepcopy(content)
    corpus['artifacts'] = copy.deepcopy(export['artifacts'])
    L.validate_corpus(corpus)
    require(E.validate_bindings(bindings) == (native, export, sources, toolbox, weights),
            'site inputs changed during binding')
    recheck_packages(modules, package_evidence)
    require(Q.read_owned_bytes(preparation_path) == prep_raw and Q.read_owned_bytes(bindings_path) == raw
            and support_sources() == support, 'immutable preparation/site bindings/source changed')
    directory.mkdir(mode=0o700, exist_ok=False)
    corpus_pin = {'path': str(directory / 'corpus.json'),
                  'sha256': Q.exclusive_json(directory / 'corpus.json', corpus)}
    receipt = {'schema_version': 1, 'scope': 'SITE_CPU_INPUT_BINDING_NOT_FIELD_QUALIFICATION',
               'preparation_sha256': Q.digest(prep_raw), 'content_sha256': preparation['content_sha256'],
               'site_bindings_sha256': Q.digest(raw), 'site_bindings': bindings,
               'site_artifacts': export['artifacts'], 'final_corpus': corpus_pin,
               'comparison': {'raw_calibration_vectors': 8, 'chat_vectors': 64, 'complete_ids_equal': True,
                              'regenerated_documents': False},
               'packages': package_evidence, 'preparation_sources': support,
               'raw_tokenization': RAW_OPTIONS, 'chat_tokenization': CHAT_OPTIONS, 'rendered_rows': rows}
    receipt_pin = {'path': str(directory / 'site-link-receipt.json'),
                   'sha256': Q.exclusive_json(directory / 'site-link-receipt.json', receipt)}
    return {'corpus': corpus_pin, 'site_link_receipt': receipt_pin}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prepare_parser = sub.add_parser('prepare-tokenizer-only')
    bind_parser = sub.add_parser('bind-site')
    for item in (prepare_parser, bind_parser):
        item.add_argument('--bindings', required=True)
        item.add_argument('--output-directory', required=True)
    bind_parser.add_argument('--preparation', required=True)
    bind_parser.add_argument('--preparation-sha256', required=True)
    args = parser.parse_args(argv)
    try:
        result = (prepare(args.bindings, args.output_directory) if args.command == 'prepare-tokenizer-only'
                  else bind_site(args.preparation, args.preparation_sha256, args.bindings, args.output_directory))
        print(json.dumps(result, sort_keys=True))
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, ImportError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
