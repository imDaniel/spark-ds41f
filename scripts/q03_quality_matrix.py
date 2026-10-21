#!/usr/bin/env python3
"""Stage 1b only: finite 16-token screen, no scoring/capacity/performance claim."""
from __future__ import annotations
import copy
import os
from pathlib import Path

import q03_qualification as Q

require, canonical, digest, json_bytes = Q.require, Q.canonical, Q.digest, Q.json_bytes
read_owned_bytes, exclusive_json = Q.read_owned_bytes, Q.exclusive_json
WORKLOAD = 'lean_tokens_stage1b_v1'
STAGE1A_HEAD = '4d3f478e7ef8221cb45a3c19fd81bf17aaa14c02'
STAGE1A_TREE = 'b4b6d918f8b0c6d40f808f1a330c5a72aa584749'
# Historical evidence allowlist only; never used as recovery/execution authority.
STAGE1A_SOURCES = {'../bin/spark': 'd7744fd381e6a61c4a571ba27beec6a57ea682799abefb4dbfbdea27fb63a73f',
 'kernel_backend.py': '80ee6b7b1696da04b1d90273026b6c26002ab050af914f742dc66ec3e0a5137e',
 'kernel_readiness.py': '7219d260d766c9ddec292a13a88309ffd88c5cbe3d6d453aa566406b70ced30c',
 'memguard-start.sh': 'a1c0cb8e3d78eb3b22c457bb7fba8087f79e9ac31342dc38106f7cde65bd5f15',
 'memguard.sh': 'c70a379efec2e48858e86c77ab8eb90cc748700f702afe663480d92fa5c1ab09',
 'mesh_fabric.py': '0d322510ca43655ad641236c5d600aeca29f92b900e2023bf90a778627644a13',
 'model_layout.py': '8b7688837b37021994118aa99d46da01e8e696278683f330cbae585522d6d4b5',
 'q03_build_adapter.py': '11bf6771adebf72c580670a56fcb93dcbd26471269312c01fad8ad32b14248b9',
 'q03_buildkit_plan.py': 'c504b8eddf9af157b6223108123d90c14c14a0d0ed57e1a0d75d7c158b85381a',
 'q03_git_toolchain.py': 'ad1cfb9b6abf2e29faa1e885903413f8bd59bfc9aa3f9d3acdac68b717b1d076',
 'q03_held_window.py': '76b3c000796dce3e7b54f958531ddf3a99a145040a7cdbf61b7ae2f7fa517a21',
 'q03_http_client.py': 'ff60dc0b5780ed1c8e697221dd8ea51f2b38057d0ab8cfdad392fc49b4f1b5e9',
 'q03_http_service.py': '1541e1ed8a37f4b0e49d245c58e1a58ace4b25af1c6e0c496c970ab50acb49a6',
 'q03_http_toolbox.json': '44f8494c4ddcf45103c6dda5148448cc33cbb0a1c9eb5188d6227cacca36207a',
 'q03_http_worker.py': '3b1a358ad47051dd3bed3628d6d34d8433241de6271f764a522026534ba381cf',
 'q03_image_preparation.py': '004aa1e7b4e2686193f9eab48daf019c4c477d8b82b5ce42e576df07bf25d79c',
 'q03_original_spark_adapter.py': '63c3761b652acdd27bf379ad1a34f2fabe3115a1065c89bd553a9fcd2f2d095a',
 'q03_owner_model.py': '9f61fade2fc5eb26e476e58ddd5d51a7d889523b4674b52b806e642d9247952f',
 'q03_qualification.py': 'f41e9fac23ee88defa71d6aed6dddaa6b0630b8c5e369f14b02c17ee2d242c1e',
 'q03_rank_logs.py': '3526d76386f735243cfbc457f1f1aec7eb5a4a3d144628f9cb42c5f01af4a9a6',
 'q03_remote_operation.py': '7271c816f06c6f1bb600649b31c7e74941a075a502d958d0bf945e2b38ebd36e',
 'q03_window.py': '128272ef13773348bafad8b35fcce747de6f5c3c1ca6e98d3097255c99194d03',
 'q03_window_build.py': '3c63b544b0e288cb816bfa1354e3c1a8ac1f328ea5c64287134418a86795d352',
 'q03_window_preflight.py': 'a86e346bd891d62de27438b9efb997158793ac60495bfa6f90cd9e0e7f78a1c0',
 'q03_window_watchdog.py': '50877b790c637038efb4428ee4f80611b1e669a8c67f1ec57bbd0f0b033993b5',
 'recipe.py': 'b30468db222dbc1933e479733a53b6e1c51e4e5d6400f3f97c312c480958c4cb',
 'topology.py': '794b9fbfe245b32cee4231d1ddb75ab460646664faff6d61461da13e851f18e2',
 'transport_profiles.py': 'b6d25ecfd0544ce7129082bffe51438a941cb053b5315c50930daf822c2c5361'}
SELECTIONS = tuple({'mode': mode, 'steering': steering}
                   for steering in ('off', 'loaded_zero', 'active') for mode in ('off', 'dual'))
SCOPE = '2048-input/c1,c8,c16/16-output token-ID screen only; no distribution/capacity/performance acceptance'


def runs(selection):
    Q.owner.validate_selection(selection)
    if selection['steering'] == 'off':
        return [{'label': selection['mode'] + '_c' + str(c), 'concurrency': c} for c in (8, 16)]
    if selection['mode'] == 'off':
        return [{'label': 'off_c1_control1', 'concurrency': 1},
                {'label': 'off_c1_control2', 'concurrency': 1},
                {'label': 'off_c8', 'concurrency': 8}, {'label': 'off_c16', 'concurrency': 16}]
    return [{'label': 'dual_c' + str(c), 'concurrency': c} for c in (1, 8, 16)]


def extra_body(selection):
    Q.owner.validate_selection(selection)
    extra = copy.deepcopy(Q.EXTRA)
    if selection['steering'] != 'off':
        extra['vllm_xargs'] = {'steering_profile': 'heretic-1017-attn-v1',
                               'steering_scale': 0 if selection['steering'] == 'loaded_zero' else 1}
    return extra


def read_drained(directory, *, historical=False):
    """Recheck full ordinary chain; historical inputs have one literal source allowlist."""
    from q03_window import Session, RuntimeOps
    from q03_held_window import verify_outer, verify_history
    from q03_http_service import verify_plan_identity
    directory = Path(directory)
    root = directory.parent.parent
    raw_capsule = read_owned_bytes(root / 'capsule.json')
    capsule = json_bytes(raw_capsule)
    ops = RuntimeOps()
    if historical:
        require(capsule['recovery_source_sha256'] == STAGE1A_SOURCES,
                'unsupported stage1a source inventory; only frozen PR21 is recognized')
        from q03_window_preflight import _validate_publication
        publication = capsule['manifest']['preflight'].get('publication', {})
        require(publication.get('sha') == STAGE1A_HEAD, 'historical publication is not frozen PR21')
        _validate_publication(publication)
        checkout = Path(capsule['manifest']['preflight']['independent_checkout'])
        require(checkout.is_absolute() and checkout.resolve() == checkout and '..' not in checkout.parts,
                'historical checkout must be canonical and owner-controlled')
        # No imports, subprocesses, Git hooks or execution from the historical checkout.
        measured = {name: digest(read_owned_bytes(checkout / ('bin/spark' if name == '../bin/spark'
                    else 'scripts/' + name))) for name in STAGE1A_SOURCES}
        require(measured == STAGE1A_SOURCES, 'archived PR21 source bytes changed')
        class HistoricalEvidenceOps(RuntimeOps):
            def recovery_sources(self):
                return measured
        ops = HistoricalEvidenceOps()
    session = Session(root, ops)
    verify_outer(session, require_hold=False)
    verify_history(session)
    require(directory.name in session.state.get('attempt_receipt_sha256', {}), 'attempt absent from retained history')
    plan = json_bytes(read_owned_bytes(directory / 'http' / 'plan.json'))
    verify_plan_identity(session, plan)
    if historical:
        inventory = json_bytes(read_owned_bytes(checkout / 'scripts/q03_http_toolbox.json'))
        require(plan['client']['qualification_sources'] == {name: STAGE1A_SOURCES[name + '.py']
                for name in ('q03_qualification', 'q03_owner_model', 'q03_held_window')}
                and plan['client']['toolbox_files'] == inventory['files']
                and plan['client']['toolbox_head'] == inventory['head'],
                'stage1a executed producer/source/toolbox closure differs')
        require(canonical(plan['client']['qualification']['state_identity'])
                == canonical(state_identity(plan['binding'], 'off')),
                'stage1a decoded configuration identity differs')
        require(canonical(plan['binding']['http']) == canonical(session.manifest['http_service'])
                and canonical(plan['client']['qualification']) == canonical(Q.client_plan(plan['binding'], plan['selection'])),
                'stage1a plan differs from its admitted finite corpus/state binding')
    descriptor = json_bytes(read_owned_bytes(directory / 'descriptor.json'))
    require(descriptor['jobs'] == [dict(session.manifest['jobs'][0], selection=plan['selection'])],
            'descriptor job differs from finite capsule')
    terminal = json_bytes(read_owned_bytes(directory / 'terminal.json'))
    require(descriptor['capsule_sha256'] == digest(raw_capsule)
            and capsule['window_id'] == plan['window_id'], 'retained capsule identity differs')
    require(terminal.get('outcome') == 'passed' and terminal.get('passed_gates') == ['http_service']
            and terminal['descriptor_sha256'] == digest(canonical(descriptor))
            and terminal.get('command_plan_sha256') == descriptor['command_plan_sha256']
            and terminal['quiescence'].get('attempt_id') == plan['attempt_id']
            and terminal['quiescence'].get('phase') in ('stopped', 'restore_drained')
            and terminal['attempt_id'] == descriptor['attempt_id'] == plan['attempt_id']
            and terminal['window_id'] == descriptor['window_id'] == plan['window_id']
            and terminal['quiescence'].get('quiescent') is True
            and type(terminal['quiescence'].get('node_count')) is int and terminal['quiescence']['node_count'] == 4,
            'comparison requires passed identity-bound all-four drained attempt')
    path = Path(plan['client']['output']); raw = read_owned_bytes(path)
    receipt = json_bytes(read_owned_bytes(path.with_name('client-receipt.json')))
    require(receipt.get('window_id') == plan['window_id'] and receipt.get('attempt_id') == plan['attempt_id']
            and receipt.get('plan_sha256') == plan['plan_sha256'] and type(receipt.get('exit_code')) is int
            and receipt['exit_code'] == 0 and receipt.get('error') is None
            and receipt.get('output_sha256') == digest(raw), 'client completion/hash differs')
    value = json_bytes(raw)
    if not historical:
        names = ('q03_qualification', 'q03_owner_model', 'q03_held_window', 'q03_quality_matrix')
        inventory = json_bytes(read_owned_bytes(Path(__file__).with_name('q03_http_toolbox.json')))
        require(plan['client']['qualification_sources'] == {name: digest(read_owned_bytes(
                    Path(__file__).with_name(name + '.py'))) for name in names}
                and plan['client']['toolbox_files'] == inventory['files']
                and plan['client']['toolbox_head'] == inventory['head'], 'stage1b producer source closure differs')
        require(plan['binding']['http'] == session.manifest['http_service']
                and canonical(plan['client']['qualification']) == canonical(client_plan(plan['binding'], plan['selection'], session)),
                'stage1b reference/corpus/runtime/state binding differs')
    (Q.validate_output if historical else validate_output)(value, plan)
    for run in value['runs']:
        retained = read_owned_bytes(path.with_name(run['label'] + '.json'))
        require(digest(retained) == run['sha256'] and json_bytes(retained) == run['value'], 'retained raw generation changed')
    return plan, value, session


def stage1a(binding):
    pin = binding['stage1a']
    require(isinstance(pin, dict) and set(pin) == {'source_head', 'source_tree', 'capsule', 'off_attempt', 'dual_attempt'}
            and pin['source_head'] == STAGE1A_HEAD and pin['source_tree'] == STAGE1A_TREE,
            'stage1a requires explicit frozen PR21 publication identity')
    capsule = Q.owner.pinned_json(pin['capsule'])
    root = Path(pin['capsule']['path']).parent
    require(Path(pin['capsule']['path']).name == 'capsule.json' and capsule['session_dir'] == str(root)
            and capsule['manifest']['http_service']['schema_version'] == 2,
            'stage1a capsule/path/schema differs')
    plans, values = [], []
    for arm in ('off', 'dual'):
        directory = Path(pin[arm + '_attempt'])
        require(directory.parent == root / 'attempts', 'stage1a attempt escaped frozen capsule')
        plan, value, session = read_drained(directory, historical=True)
        require(value['selection'] == {'mode': arm, 'steering': 'off'}, 'stage1a arm differs')
        plans.append(plan); values.append(value)
    records = session.state['attempt_results']
    require([r['attempt_id'] for r in records] == [v['attempt_id'] for v in values]
            and all(r['outcome'] == 'passed' for r in records),
            'stage1a is incomplete or has retained failure lineage; no expansion')
    for key in ('state_identity', 'corpus_sha256', 'toolbox_head', 'workload'):
        require(canonical(values[0][key]) == canonical(values[1][key]), 'stage1a identity differs: ' + key)
    require(values[0]['workload'] == Q.WORKLOAD and values[0]['corpus_sha256'] == binding['corpus']['sha256'],
            'stage1a corpus/workload differs')
    rows = [Q.validate_generation(r['value'], p, r['label']) for p, v in zip(plans, values) for r in v['runs']]
    require(len(rows) == 3 and all(Q.first_divergence(rows[0], row) is None for row in rows[1:]),
            'stage1a control unstable or candidate mismatched; no expansion')
    return {'rows': rows[0], 'state_identity': values[0]['state_identity'],
            'toolbox_head': values[0]['toolbox_head'], 'capsule_sha256': pin['capsule']['sha256'],
            'plan_sha256': [p['plan_sha256'] for p in plans]}


def validate_binding(value, binding=None):
    require(isinstance(value, dict) and set(value) == {'workload', 'corpus', 'stage1a'}
            and value['workload'] == WORKLOAD, 'only finite stage1b v1 qualification is supported')
    corpus = Q.validate_binding({'workload': Q.WORKLOAD, 'corpus': value['corpus']}, binding)
    prior = stage1a(value)
    if binding is not None:
        expected = state_identity(binding, 'off')
        require(canonical(prior['state_identity']) == canonical(expected)
                and prior['toolbox_head'] == binding['toolbox_inventory']['head'],
                'stage1a native/profile/corpus/toolbox identity differs from current binding')
    return corpus


def state_identity(binding, steering):
    # The decoded plan dictionaries are not their own configuration authority.
    inputs = binding['http']['owner_inputs']
    for key, decoded in (('profile', 'profile_value'), ('effective_config', 'effective_value')):
        require(binding[key] == inputs[key]
                and canonical(binding[decoded]) == canonical(Q.owner.pinned_json(inputs[key])),
                'decoded state differs from pinned owner input: ' + key)
    return {'image': Q.owner.IMAGE, 'source_head': Q.owner.SOURCE_HEAD,
            'runtime_manifest_sha256': Q.owner.RUNTIME_SHA,
            'profile_sha256': binding['profile']['sha256'],
            'effective_config_sha256': digest(canonical(binding['effective_value'][steering])),
            'steering': steering, 'model': Q.MODEL}


def next_selection(session):
    records = session.state.get('attempt_results', [])
    require(all(r.get('outcome') == 'passed' and r.get('passed_gates') == ['http_service'] for r in records),
            'retained stage1b failure stops progression; no retry-to-green')
    require(len(records) < len(SELECTIONS), 'stage1b finite inventory is complete')
    for index, row in enumerate(records):
        plan, value, _ = read_drained(session.directory / 'attempts' / row['attempt_id'])
        require(plan['selection'] == SELECTIONS[index]
                and validate_output(value, plan)['qualification_status'] == 'TOKEN_ID_MATCH',
                'stage1b predecessor incomplete, unstable or mismatched')
    return copy.deepcopy(SELECTIONS[len(records)])


def client_plan(binding, selection, session=None):
    corpus = validate_binding(binding['http']['qualification'], binding)
    prior = stage1a(binding['http']['qualification'])
    reference, origin = prior['rows'], {'stage1a_capsule_sha256': prior['capsule_sha256'],
                                      'stage1a_plan_sha256': prior['plan_sha256']}
    if selection['steering'] == 'active':
        reference, origin = None, None
        if selection['mode'] == 'dual':
            require(session is not None and len(session.state['attempt_results']) >= 5,
                    'active DUAL needs the drained same-state OFF predecessor')
            row = session.state['attempt_results'][4]
            plan, value, _ = read_drained(session.directory / 'attempts' / row['attempt_id'])
            require(plan['selection'] == {'mode': 'off', 'steering': 'active'}
                    and canonical(value['state_identity']) == canonical(state_identity(binding, 'active')),
                    'active OFF reference state identity differs')
            reference = validate_generation(value['runs'][0]['value'], plan, runs(plan['selection'])[0])
            origin = {'plan_sha256': plan['plan_sha256'], 'result_sha256': digest(canonical(value))}
    return {'workload': WORKLOAD, 'corpus': corpus,
            'corpus_sha256': binding['http']['qualification']['corpus']['sha256'],
            'runs': runs(selection), 'state_identity': state_identity(binding, selection['steering']),
            'reference_rows': reference, 'reference_origin': origin}


def validate_plan(plan):
    q = plan['client']['qualification']
    Q.validate_corpus(q['corpus'])
    require(q['workload'] == WORKLOAD and canonical(q['runs']) == canonical(runs(plan['selection']))
            and digest(canonical(q['corpus'])) == q['corpus_sha256'], 'stage1b finite plan/corpus differs')
    require(q['state_identity']['steering'] == plan['selection']['steering'], 'stage1b steering identity differs')
    reference = q['reference_rows']
    if plan['selection'] == {'mode': 'off', 'steering': 'active'}:
        require(reference is None and q['reference_origin'] is None, 'active OFF must establish its own reference')
    else:
        require(isinstance(reference, list) and len(reference) == 16 and isinstance(q['reference_origin'], dict),
                'complete same-state or no-op reference required')
        for index, row in enumerate(reference):
            require(set(row) == {'id', 'prompt_ids', 'output_ids', 'finish_reason'}
                    and type(row['id']) is int and row['id'] == index
                    and row['prompt_ids'] == q['corpus']['rows'][index]['prompt_ids']
                    and Q.token_ids(row['output_ids'], 16) and row['finish_reason'] == 'length',
                    'reference token inventory differs')
    return q


def argv(plan, run, output):
    q = validate_plan(plan)
    require(run in q['runs'], 'unknown finite stage1b run')
    return ['model', plan['url'], '--model', Q.MODEL, '--prompts', q['corpus']['prompts']['path'],
            '--limit', '16', '--concurrency', str(run['concurrency']), '--max-tokens', '16', '--fixed-length',
            '--no-score', '--extra-body', canonical(extra_body(plan['selection'])).decode(),
            '--label', run['label'], '--output', str(output)]


def validate_generation(value, plan, run):
    q = validate_plan(plan)
    # Reuse the exact legacy token-map validator after validating the only two deltas.
    require(value.get('workload', {}).get('concurrency') == run['concurrency']
            and type(value['workload']['concurrency']) is int
            and canonical(value['workload'].get('extra_body')) == canonical(extra_body(plan['selection'])),
            'stage1b concurrency or request steering differs')
    legacy = copy.deepcopy(value)
    legacy['workload'].update(concurrency=1, extra_body=Q.EXTRA)
    return Q.validate_generation(legacy, plan, run['label'])


def run_client(plan, main):
    q = validate_plan(plan); output = Path(plan['client']['output'])
    results, stopped, reference = [], None, q['reference_rows']
    for run in q['runs']:
        path = output.with_name(run['label'] + '.json')
        require(not path.exists(), 'stage1b raw output already exists')
        code = main(argv(plan, run, path))
        raw = read_owned_bytes(path)
        with path.open('rb') as stream:
            os.fsync(stream.fileno())
        value = json_bytes(raw)
        results.append({'label': run['label'], 'exit_code': code, 'sha256': digest(raw), 'value': value})
        try:
            require(type(code) is int and code == 0, 'generation CLI failed')
            rows = validate_generation(value, plan, run)
            if reference is None:
                reference = rows
            divergence = Q.first_divergence(reference, rows)
            if divergence is not None:
                reason = ('INCONCLUSIVE_UNSTABLE_CONTROL' if run['label'] == 'off_c1_control2'
                          else 'TOKEN_ID_MISMATCH')
                stopped = {'label': run['label'], 'reason': reason, 'first_divergence': divergence}
        except (RuntimeError, ValueError, KeyError, TypeError) as exc:
            stopped = {'label': run['label'], 'reason': str(exc), 'first_divergence': None}
        if stopped is not None:
            break
    value = {'workload': WORKLOAD, 'window_id': plan['window_id'], 'attempt_id': plan['attempt_id'],
             'plan_sha256': plan['plan_sha256'], 'selection': plan['selection'],
             'state_identity': q['state_identity'], 'corpus_sha256': q['corpus_sha256'],
             'toolbox_head': plan['client']['toolbox_head'], 'reference_origin': q['reference_origin'],
             'runs': results, 'stopped': stopped}
    exclusive_json(output, value)
    return 1 if stopped else 0


def validate_output(value, plan):
    q = validate_plan(plan)
    expected = {'workload': WORKLOAD, 'window_id': plan['window_id'], 'attempt_id': plan['attempt_id'],
                'plan_sha256': plan['plan_sha256'], 'selection': plan['selection'],
                'state_identity': q['state_identity'], 'corpus_sha256': q['corpus_sha256'],
                'toolbox_head': plan['client']['toolbox_head'], 'reference_origin': q['reference_origin']}
    for key, wanted in expected.items():
        require(canonical(value.get(key)) == canonical(wanted), 'stage1b result binding differs: ' + key)
    require(value.get('stopped') is None and isinstance(value.get('runs'), list)
            and len(value['runs']) == len(q['runs']), 'stage1b incomplete or short-circuited run inventory')
    reference = q['reference_rows']
    for row, run in zip(value['runs'], q['runs']):
        require(set(row) == {'label', 'exit_code', 'sha256', 'value'} and row['label'] == run['label']
                and type(row['exit_code']) is int and row['exit_code'] == 0, 'stage1b CLI failed or order differs')
        rows = validate_generation(row['value'], plan, run)
        if reference is None:
            reference = rows
        require(Q.first_divergence(reference, rows) is None, 'stage1b token-ID mismatch')
    return {'qualification_status': 'TOKEN_ID_MATCH', 'first_divergence': None, 'qualification': SCOPE,
            'completed_suite_calls': len(q['runs']), 'completed_requests': len(q['runs']) * 16,
            'recorded_output_tokens': len(q['runs']) * 256}



def compare_session(directory):
    """Offline complete Stage 1 screen. Never promotes quality or schedules work."""
    from q03_window import Session, RuntimeOps
    from q03_held_window import verify_outer, verify_history
    session = Session(directory, RuntimeOps())
    verify_outer(session, require_hold=False); verify_history(session)
    require(session.manifest['http_service']['schema_version'] == 3, 'stage1b session schema differs')
    prior = stage1a(session.manifest['http_service']['qualification'])
    records = session.state.get('attempt_results', [])
    require(len(records) == 6 and all(r.get('outcome') == 'passed' and r.get('passed_gates') == ['http_service']
                                    for r in records), 'complete six-attempt inventory without retained failures required')
    count, identities = 0, {}
    for selection, record in zip(SELECTIONS, records):
        plan, value, _ = read_drained(session.directory / 'attempts' / record['attempt_id'])
        require(plan['selection'] == selection, 'stage1b retained attempt order differs')
        expected = client_plan(plan['binding'], selection, session)
        require(canonical(plan['client']['qualification']) == canonical(expected),
                'stage1b reference/corpus/runtime/state drift')
        require(plan['binding']['http'] == session.manifest['http_service']
                and value['toolbox_head'] == prior['toolbox_head'], 'stage1b admission/toolbox differs')
        steering = selection['steering']
        if steering in identities:
            require(canonical(identities[steering]) == canonical(expected['state_identity']),
                    'same-state OFF/DUAL configuration identity differs')
        identities[steering] = expected['state_identity']
        count += validate_output(value, plan)['completed_suite_calls']
    require(count == 18, 'stage1b full call inventory differs')
    return {'status': 'TOKEN_ID_MATCH', 'qualification': SCOPE, 'reused_stage1a_suite_calls': 3,
            'new_stage1b_suite_calls': count, 'total_suite_calls': 21, 'total_requests': 336,
            'total_recorded_output_tokens': 5376,
            'corpus_sha256': session.manifest['http_service']['qualification']['corpus']['sha256'],
            'stage1a_capsule_sha256': prior['capsule_sha256'],
            'stage1b_capsule_sha256': digest(read_owned_bytes(session.directory / 'capsule.json')),
            'state_identities': identities,
            'distribution_quality': 'NOT_EVALUATED', 'capacity': 'NOT_EVALUATED', 'performance': 'NOT_EVALUATED'}


if __name__ == '__main__':
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session-dir', required=True)
    args = parser.parse_args()
    print(json.dumps(compare_session(args.session_dir), sort_keys=True))
