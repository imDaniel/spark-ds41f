#!/usr/bin/env python3
"""Finite unloaded lean token screen; never an argv, service or scoring executor.

Preparation normalizes an export from the ACTUAL deepseek_v41 tokenizer. It does
not tokenize, approximate lengths, fetch assets or claim field validation. The
HTTP response must subsequently echo every independently frozen input token ID.
"""
from __future__ import annotations
import argparse
import copy
import os
from pathlib import Path
import re

import q03_owner_model as owner
from q03_held_window import exclusive_json, read_owned_bytes

require, canonical, digest, json_bytes = owner.require, owner.canonical, owner.digest, owner.json_bytes
WORKLOAD = 'lean_tokens_c1'
MODEL = 'deepseek-v4.1-flash'
NATIVE_SOURCES = {
    'deepseek_v41.py': '8450587010152846a2e3f6269c661b464615b7e46c312f4e38a397c44218a6e5',
    'deepseek_v41_encoding.py': '8ee7a8010c3ed257a0f1caa4dfbeb3ec68df0b03e36fcd20dad044ec7cf114fe'}
TEMPLATE_SHA = digest(canonical(NATIVE_SOURCES))
EXTRA = {'temperature': 0, 'seed': 0, 'chat_template_kwargs': {'thinking': False}}
IDENTITY_FIELDS = {'model_repository', 'model_revision', 'model_config_sha256',
                   'weights_manifest_sha256', 'tokenizer_revision', 'tokenizer_assets_sha256',
                   'encoding_source_sha256', 'chat_template_sha256', 'special_tokens_sha256',
                   'transformers_version', 'tokenizers_version'}


def token_ids(value, length):
    return (isinstance(value, list) and len(value) == length
            and all(type(x) is int and x >= 0 for x in value))


def validate_identity(value):
    require(isinstance(value, dict) and set(value) == IDENTITY_FIELDS, 'complete model/tokenizer identity required')
    for key in ('model_revision', 'tokenizer_revision'):
        require(isinstance(value[key], str) and re.fullmatch('[0-9a-f]{40}', value[key]), 'exact revision required: ' + key)
    for key in ('model_config_sha256', 'weights_manifest_sha256', 'encoding_source_sha256',
                'chat_template_sha256', 'special_tokens_sha256'):
        require(isinstance(value[key], str) and re.fullmatch('[0-9a-f]{64}', value[key]), 'exact asset hash required: ' + key)
    for key in ('model_repository', 'transformers_version', 'tokenizers_version'):
        require(isinstance(value[key], str) and value[key].strip() and '<' not in value[key], 'unbound identity: ' + key)
    require(value['encoding_source_sha256'] == NATIVE_SOURCES['deepseek_v41_encoding.py']
            and value['chat_template_sha256'] == TEMPLATE_SHA, 'native197 active Python encoder/template differs')
    assets = value['tokenizer_assets_sha256']
    require(isinstance(assets, dict) and assets, 'tokenizer asset inventory required')
    for name, sha in assets.items():
        require(isinstance(name, str) and name and not Path(name).is_absolute()
                and '..' not in Path(name).parts and isinstance(sha, str)
                and re.fullmatch('[0-9a-f]{64}', sha), 'invalid tokenizer asset identity')


def validate_export(value):
    require(isinstance(value, dict) and set(value) == {'schema_version', 'tokenizer_mode', 'identity', 'artifacts', 'request', 'rows'}
            and type(value['schema_version']) is int and value['schema_version'] == 1
            and value['tokenizer_mode'] == 'deepseek_v41', 'actual tokenizer export schema differs')
    validate_identity(value['identity'])
    artifact_files(value)  # Shape and declared hashes must match, even without I/O.
    require(canonical(value['request']) == canonical(EXTRA), 'frozen generation/template request differs')
    rows = value['rows']
    require(isinstance(rows, list) and len(rows) == 16, 'exactly 16 prompts required')
    texts, inputs = set(), set()
    for index, row in enumerate(rows):
        require(isinstance(row, dict) and set(row) == {'id', 'text', 'prompt_ids'}
                and type(row['id']) is int and row['id'] == index
                and isinstance(row['text'], str) and row['text']
                and token_ids(row['prompt_ids'], 2048), 'ordered full 2048-token prompt required')
        texts.add(row['text']); inputs.add(tuple(row['prompt_ids']))
    require(len(texts) == len(inputs) == 16, 'all 16 prompts and input-ID vectors must be distinct')
    return value


def prepare(export_path, directory):
    """Deterministic bytes from a frozen real-tokenizer export; no network or GPU."""
    raw = read_owned_bytes(export_path)
    value = validate_export(json_bytes(raw))
    verify_artifacts(value)
    directory = Path(directory)
    directory.mkdir(mode=0o700, exist_ok=False)
    prompts = [{'id': row['id'], 'text': row['text']} for row in value['rows']]
    prompt_raw = b''.join(canonical(row) + b'\n' for row in prompts)
    prompt_path = directory / 'prompts.jsonl'
    with prompt_path.open('xb') as stream:
        stream.write(prompt_raw); stream.flush(); os.fsync(stream.fileno())
    corpus = dict(value, source_export_sha256=digest(raw),
                  prompts={'path': str(prompt_path), 'sha256': digest(prompt_raw)},
                  # Exact PR8 servebench.prompts.digest encoding, not our canonical encoding.
                  prompts_sha256=servebench_prompt_digest(prompts))
    path = directory / 'corpus.json'
    sha = exclusive_json(path, corpus)
    return {'path': str(path), 'sha256': sha}


def servebench_prompt_digest(prompts):
    import json
    return digest(json.dumps(prompts, sort_keys=True).encode())


def validate_corpus(value):
    require(isinstance(value, dict) and set(value) == {'schema_version', 'tokenizer_mode', 'identity',
            'artifacts', 'request', 'rows', 'source_export_sha256', 'prompts', 'prompts_sha256'}, 'corpus fields differ')
    validate_export({key: value[key] for key in ('schema_version', 'tokenizer_mode', 'identity', 'artifacts', 'request', 'rows')})
    require(isinstance(value['source_export_sha256'], str)
            and re.fullmatch('[0-9a-f]{64}', value['source_export_sha256']), 'missing source export identity')
    pin = value['prompts']
    require(isinstance(pin, dict) and set(pin) == {'path', 'sha256'}, 'prompt JSONL pin required')
    raw = read_owned_bytes(pin['path'])
    prompts = [json_bytes(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
    expected = [{'id': row['id'], 'text': row['text']} for row in value['rows']]
    require(digest(raw) == pin['sha256'] and canonical(prompts) == canonical(expected)
            and servebench_prompt_digest(prompts) == value['prompts_sha256'], 'raw/parsed prompt identity differs')
    return value


def artifact_files(value):
    """Existing model data only; no code import, chmod, download or weight scan."""
    identity, artifacts = value['identity'], value['artifacts']
    require(isinstance(artifacts, dict) and set(artifacts) == {
        'model_root', 'weights_manifest', 'runtime_metadata', 'native_sources', 'special_tokens_file'},
        'actual artifact file bindings required')
    root = Path(artifacts['model_root'])
    require(root.is_absolute() and root.resolve() == root and '..' not in root.parts,
            'model root must be the registered canonical mount')
    special = artifacts['special_tokens_file']
    require(special in identity['tokenizer_assets_sha256']
            and identity['tokenizer_assets_sha256'][special] == identity['special_tokens_sha256'],
            'special tokens must identify actual inventoried asset bytes')
    files = [{'path': str(root / 'config.json'), 'sha256': identity['model_config_sha256']}]
    files += [{'path': str(root / name), 'sha256': sha}
              for name, sha in sorted(identity['tokenizer_assets_sha256'].items())]
    native = artifacts['native_sources']
    require(isinstance(native, dict) and set(native) == set(NATIVE_SOURCES), 'exact native source artifact closure required')
    for name, expected in NATIVE_SOURCES.items():
        pin = native[name]
        require(isinstance(pin, dict) and set(pin) == {'path', 'sha256'}
                and pin['sha256'] == expected, 'native source artifact differs')
    for key in ('weights_manifest', 'runtime_metadata'):
        pin = artifacts[key]
        require(isinstance(pin, dict) and set(pin) == {'path', 'sha256'}
                and isinstance(pin['sha256'], str) and re.fullmatch('[0-9a-f]{64}', pin['sha256']),
                'immutable artifact pin required: ' + key)
    require(artifacts['weights_manifest']['sha256'] == identity['weights_manifest_sha256'], 'weight manifest identity differs')
    return files


# Read-only all-four mounted config/tokenizer byte check. Registered model data
# retains its existing owner/group modes; never repurpose the code-boundary rule.
ASSET_PROBE = r"""
import hashlib,json,sys
files=json.loads(sys.argv[1])
for pin in files:
 with open(pin['path'],'rb') as stream:
  actual=hashlib.file_digest(stream,'sha256').hexdigest()
 if actual!=pin['sha256']: raise RuntimeError('mounted model/tokenizer asset changed: '+pin['path'])
print(json.dumps({'verified_files':len(files)}))
"""


def verify_artifacts(value, binding=None):
    import hashlib
    files = artifact_files(value)
    artifacts, identity = value['artifacts'], value['identity']
    if binding is not None:
        profile = binding['profile_value']
        roots = [s.format(home=profile['host']['home']) for s, d, mode in profile['container']['mounts']
                 if d == '/models' and mode == 'ro']
        require(roots == [artifacts['model_root']], 'corpus is not bound to the registered read-only model mount')
    for pin in files:
        with open(pin['path'], 'rb') as stream:
            require(hashlib.file_digest(stream, 'sha256').hexdigest() == pin['sha256'], 'actual model/tokenizer artifact changed')
    for pin in [artifacts['weights_manifest'], *artifacts['native_sources'].values()]:
        require(digest(read_owned_bytes(pin['path'])) == pin['sha256'], 'actual provenance artifact changed')
    metadata = owner.pinned_json(artifacts['runtime_metadata'])
    expected = {key: identity[key] for key in ('model_repository', 'model_revision', 'tokenizer_revision',
                                               'transformers_version', 'tokenizers_version')}
    expected.update(image=owner.IMAGE, source_head=owner.SOURCE_HEAD,
                    runtime_manifest_sha256=owner.RUNTIME_SHA, model_root=artifacts['model_root'])
    require(canonical(metadata) == canonical(expected), 'native runtime/model provenance record differs')
    return files


def validate_binding(value, binding=None):
    require(isinstance(value, dict) and set(value) == {'workload', 'corpus'}
            and value['workload'] == WORKLOAD, 'only the finite unloaded lean c1 workload is supported')
    corpus = validate_corpus(owner.pinned_json(value['corpus']))
    require(digest(canonical(corpus)) == value['corpus']['sha256'], 'corpus must be the exact canonical preparation output')
    verify_artifacts(corpus, binding)
    return corpus


def next_selection(session):
    passed = [r for r in session.state.get('attempt_results', [])
              if r.get('outcome') == 'passed' and r.get('passed_gates') == ['http_service']]
    require(len(passed) < 2, 'lean OFF-controls/DUAL execution is complete')
    if passed:
        directory = session.directory / 'attempts' / passed[0]['attempt_id']
        plan, value, _ = read_drained(directory)
        require(plan['selection'] == {'mode': 'off', 'steering': 'off'}, 'lean predecessor state differs')
        require(validate_output(value, plan)['qualification_status'] == 'CONTROL_STABLE',
                'unstable or incomplete OFF control; no expansion')
    return {'mode': 'dual' if passed else 'off', 'steering': 'off'}


def client_plan(binding, selection):
    require(selection in ({'mode': 'off', 'steering': 'off'}, {'mode': 'dual', 'steering': 'off'}),
            'first lean slice is unloaded only')
    corpus = validate_binding(binding['http']['qualification'], binding)
    return {'workload': WORKLOAD, 'corpus': corpus,
            'corpus_sha256': binding['http']['qualification']['corpus']['sha256'],
            'runs': ['off_control1', 'off_control2'] if selection['mode'] == 'off' else ['dual'],
            'state_identity': {'image': owner.IMAGE, 'source_head': owner.SOURCE_HEAD,
                'runtime_manifest_sha256': owner.RUNTIME_SHA,
                'profile_sha256': binding['profile']['sha256'],
                'effective_config_sha256': digest(canonical(binding['effective_value']['off'])),
                'steering': 'off', 'model': MODEL}}


def argv(plan, label, output):
    q = plan['client']['qualification']
    require(label in q['runs'], 'unknown lean run')
    return ['model', plan['url'], '--model', MODEL, '--prompts', q['corpus']['prompts']['path'],
            '--limit', '16', '--concurrency', '1', '--max-tokens', '16', '--fixed-length',
            '--no-score', '--extra-body', canonical(EXTRA).decode(), '--label', label,
            '--output', str(output)]


def validate_generation(value, plan, label):
    q = plan['client']['qualification']; corpus = validate_corpus(q['corpus'])
    require(value.get('schema') == 'servebench-model/1' and value.get('label') == label
            and value.get('server') == {'url': plan['url'], 'model': MODEL}
            and value.get('scores') is None and value.get('score_seconds') is None
            and value.get('self_agreement') is None, 'lean generation/scoring/server identity differs')
    expected = {'prompts': corpus['prompts']['path'], 'prompt_count': 16,
                'prompts_sha256': corpus['prompts_sha256'], 'concurrency': 1, 'max_tokens': 16,
                'fixed_length': True, 'top': 20, 'extra_body': EXTRA}
    require(canonical(value.get('workload')) == canonical(expected), 'lean workload/request identity differs')
    rows = value.get('generation', {}).get('sequences')
    require(isinstance(rows, list) and len(rows) == 16, 'incomplete generation records')
    by_id = {}
    for row in rows:
        require(isinstance(row, dict) and set(row) == {'id', 'prompt_ids', 'output_ids', 'finish_reason'}
                and type(row['id']) is int and 0 <= row['id'] < 16 and row['id'] not in by_id,
                'errored, duplicate or foreign generation record')
        require(row['prompt_ids'] == corpus['rows'][row['id']]['prompt_ids']
                and token_ids(row['prompt_ids'], 2048) and token_ids(row['output_ids'], 16)
                and row['finish_reason'] == 'length', 'inexact input/output token IDs or finish')
        by_id[row['id']] = row
    require(set(by_id) == set(range(16)), 'missing generation IDs')
    return [by_id[i] for i in range(16)]


def first_divergence(left, right):
    for a, b in zip(left, right):
        for offset, (x, y) in enumerate(zip(a['output_ids'], b['output_ids'])):
            if x != y:
                return {'id': a['id'], 'output_offset': offset, 'left': x, 'right': y}
    return None


def run_client(plan, main):
    """Called only by the existing hash-bound, descendant-supervised HTTP client."""
    q = plan['client']['qualification']; validate_corpus(q['corpus'])
    require(digest(canonical(q['corpus'])) == q['corpus_sha256'], 'embedded corpus hash differs')
    output = Path(plan['client']['output'])
    expected = ['off_control1', 'off_control2'] if plan['selection'] == {'mode': 'off', 'steering': 'off'} else ['dual']
    require(plan['selection'] in ({'mode': 'off', 'steering': 'off'}, {'mode': 'dual', 'steering': 'off'})
            and q['workload'] == WORKLOAD and q['runs'] == expected, 'finite client selection differs')
    results = []
    for label in q['runs']:
        path = output.with_name(label + '.json')
        require(not path.exists(), 'lean raw output already exists')
        code = main(argv(plan, label, path))
        raw = read_owned_bytes(path)
        with path.open('rb') as stream:
            os.fsync(stream.fileno())
        # Retain unsuccessful raw output and actual exit; never retry-to-green.
        results.append({'label': label, 'exit_code': code, 'sha256': digest(raw), 'value': json_bytes(raw)})
        if code != 0:
            break
    result = {'workload': WORKLOAD, 'window_id': plan['window_id'], 'attempt_id': plan['attempt_id'],
              'plan_sha256': plan['plan_sha256'], 'selection': plan['selection'],
              'state_identity': q['state_identity'], 'corpus_sha256': q['corpus_sha256'],
              'toolbox_head': plan['client']['toolbox_head'], 'runs': results}
    exclusive_json(output, result)
    return 0 if len(results) == len(q['runs']) and all(r['exit_code'] == 0 for r in results) else 1


def validate_output(value, plan):
    q = plan['client']['qualification']
    require(digest(canonical(q['corpus'])) == q['corpus_sha256'], 'embedded corpus hash differs')
    require(plan['selection'] in ({'mode': 'off', 'steering': 'off'}, {'mode': 'dual', 'steering': 'off'})
            and q['workload'] == WORKLOAD
            and q['runs'] == (['off_control1', 'off_control2'] if plan['selection']['mode'] == 'off' else ['dual']),
            'finite result selection differs')
    for key, expected in {'workload': WORKLOAD, 'window_id': plan['window_id'], 'attempt_id': plan['attempt_id'],
                          'plan_sha256': plan['plan_sha256'], 'selection': plan['selection'],
                          'state_identity': q['state_identity'], 'corpus_sha256': q['corpus_sha256'],
                          'toolbox_head': plan['client']['toolbox_head']}.items():
        require(canonical(value.get(key)) == canonical(expected), 'lean result binding differs: ' + key)
    rows = value.get('runs')
    require(isinstance(rows, list) and len(rows) == len(q['runs']), 'lean run inventory incomplete')
    sequences = []
    for row, label in zip(rows, q['runs']):
        require(set(row) == {'label', 'exit_code', 'sha256', 'value'} and row['label'] == label
                and type(row['exit_code']) is int and row['exit_code'] == 0,
                'lean CLI execution failed or run order differs')
        # The retained original servebench JSON is also checked by collect_result.
        sequences.append(validate_generation(row['value'], plan, label))
    divergence = first_divergence(*sequences) if len(sequences) == 2 else None
    return {'qualification_status': ('INCONCLUSIVE_UNSTABLE_CONTROL' if divergence else 'CONTROL_STABLE')
            if len(sequences) == 2 else 'CANDIDATE_COMPLETE_REQUIRES_COMPARISON',
            'first_divergence': divergence,
            'qualification': 'unloaded 2048-input/c1/16-output token-ID screen only; no distribution/capacity/performance acceptance'}


def read_drained(directory):
    """Offline comparison consumes complete immutable attempts, not loose JSON."""
    directory = Path(directory)
    from q03_window import Session, RuntimeOps
    from q03_held_window import verify_outer, verify_history
    from q03_http_service import verify_plan_identity
    session = Session(directory.parent.parent, RuntimeOps())
    verify_outer(session, require_hold=False)
    verify_history(session)
    require(directory.name in session.state.get('attempt_receipt_sha256', {}), 'attempt absent from retained history')
    plan = json_bytes(read_owned_bytes(directory / 'http' / 'plan.json'))
    verify_plan_identity(session, plan)
    descriptor = json_bytes(read_owned_bytes(directory / 'descriptor.json'))
    require(descriptor['jobs'] == [dict(session.manifest['jobs'][0], selection=plan['selection'])],
            'descriptor job differs from finite capsule')
    terminal = json_bytes(read_owned_bytes(directory / 'terminal.json'))
    capsule_raw = read_owned_bytes(directory.parent.parent / 'capsule.json')
    capsule = json_bytes(capsule_raw)
    require(descriptor['capsule_sha256'] == digest(capsule_raw)
            and capsule['window_id'] == plan['window_id'], 'retained capsule identity differs')
    wanted = copy.deepcopy(plan); sha = wanted.pop('plan_sha256')
    require(digest(canonical(wanted)) == sha and descriptor['command_plan_sha256'] == digest(canonical([['http_service', plan]]))
            and terminal.get('outcome') == 'passed' and terminal.get('passed_gates') == ['http_service']
            and terminal['descriptor_sha256'] == digest(canonical(descriptor))
            and terminal.get('command_plan_sha256') == descriptor['command_plan_sha256']
            and terminal['quiescence'].get('attempt_id') == plan['attempt_id']
            and terminal['quiescence'].get('phase') in ('stopped', 'restore_drained')
            and terminal['attempt_id'] == descriptor['attempt_id'] == plan['attempt_id']
            and terminal['window_id'] == descriptor['window_id'] == plan['window_id']
            and terminal.get('quiescence', {}).get('quiescent') is True
            and type(terminal['quiescence'].get('node_count')) is int and terminal['quiescence']['node_count'] == 4,
            'comparison requires passed identity-bound all-four drained attempt')
    path = Path(plan['client']['output']); raw = read_owned_bytes(path)
    receipt = json_bytes(read_owned_bytes(path.with_name('client-receipt.json')))
    require(receipt.get('window_id') == plan['window_id'] and receipt.get('attempt_id') == plan['attempt_id']
            and receipt.get('plan_sha256') == sha and type(receipt.get('exit_code')) is int
            and receipt['exit_code'] == 0 and receipt.get('error') is None
            and receipt.get('output_sha256') == digest(raw), 'client completion/hash differs')
    value = json_bytes(raw); validate_output(value, plan)
    for run in value['runs']:
        retained = read_owned_bytes(path.with_name(run['label'] + '.json'))
        require(digest(retained) == run['sha256'] and json_bytes(retained) == run['value'], 'retained raw generation changed')
    return plan, value, descriptor['capsule_sha256']


def compare_drained(off_directory, dual_directory):
    off_plan, off, off_capsule = read_drained(off_directory)
    dual_plan, dual, dual_capsule = read_drained(dual_directory)
    require(off['selection'] == {'mode': 'off', 'steering': 'off'}
            and dual['selection'] == {'mode': 'dual', 'steering': 'off'}
            and off['attempt_id'] != dual['attempt_id'] and off['window_id'] == dual['window_id']
            and off_capsule == dual_capsule, 'OFF/DUAL capsule/boot selection differs')
    state = json_bytes(read_owned_bytes(Path(off_directory).parent.parent / 'state.json'))
    passed = [row['attempt_id'] for row in state['attempt_results']
              if row.get('outcome') == 'passed' and row.get('passed_gates') == ['http_service']]
    require(passed == [off['attempt_id'], dual['attempt_id']],
            'comparison requires the retained OFF-controls then DUAL attempt order')
    for key in ('state_identity', 'corpus_sha256', 'toolbox_head', 'workload'):
        require(canonical(off[key]) == canonical(dual[key]), 'incomparable identity: ' + key)
    controls = [validate_generation(r['value'], off_plan, r['label']) for r in off['runs']]
    candidate = validate_generation(dual['runs'][0]['value'], dual_plan, 'dual')
    divergence = first_divergence(*controls)
    status = 'INCONCLUSIVE_UNSTABLE_CONTROL' if divergence else 'TOKEN_ID_MATCH'
    if divergence is None:
        divergence = first_divergence(controls[0], candidate)
        if divergence is not None:
            status = 'TOKEN_ID_MISMATCH'
    failures = [{'attempt_id': row['attempt_id'], 'failure': row.get('failure'),
                 'passed_gates': row.get('passed_gates'),
                 'terminal_sha256': state['attempt_receipt_sha256'][row['attempt_id']]}
                for row in state['attempt_results'] if row.get('outcome') != 'passed']
    # Preserve operational retries, but never select only later green evidence.
    # A future reviewed classifier may prove an old failure infrastructure-only;
    # this finite comparator deliberately makes no such unsupported inference.
    return {'status': 'INCONCLUSIVE_PRIOR_FAILED_ATTEMPTS' if failures else status,
            'selected_complete_attempt_status': status, 'failure_lineage': failures,
            'first_divergence': divergence,
            'off_attempt_id': off['attempt_id'], 'dual_attempt_id': dual['attempt_id'],
            'corpus_sha256': off['corpus_sha256'], 'matched_scope': 'unloaded/2048-input/c1/16-output only',
            'distribution_quality': 'NOT_EVALUATED', 'capacity': 'NOT_EVALUATED', 'performance': 'NOT_EVALUATED'}


def main():
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    prepare_parser = sub.add_parser('prepare')
    prepare_parser.add_argument('--tokenizer-export', required=True)
    prepare_parser.add_argument('--output-directory', required=True)
    compare_parser = sub.add_parser('compare')
    compare_parser.add_argument('--off-attempt', required=True)
    compare_parser.add_argument('--dual-attempt', required=True)
    args = parser.parse_args()
    value = prepare(args.tokenizer_export, args.output_directory) if args.action == 'prepare' else compare_drained(args.off_attempt, args.dual_attempt)
    print(json.dumps(value, sort_keys=True))


if __name__ == '__main__':
    main()
