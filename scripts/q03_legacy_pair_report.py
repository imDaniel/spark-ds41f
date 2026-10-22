#!/usr/bin/env python3
"""Read-only descriptive pairing of complete fixed original warm4K cases.

No execution, scheduler, retry selection, quality judge, or noninferiority test.
The existing owned lifecycle and frozen PR8 reporter remain authoritative.
"""
from __future__ import annotations
import argparse
import copy
import contextlib
import functools
import importlib.abc
import importlib.machinery
import importlib.util
from datetime import datetime
import math
import os
import re
from pathlib import Path
import sys
from types import SimpleNamespace, ModuleType

# -B prevents writes, not reads of timestamp-valid or unchecked-hash caches.
# Pin helper resolution to sibling source, including late imports from bin/spark.
BYTECODE_PREFIX = '/__q03_legacy_pair_unused_bytecode__'
_SOURCE_ROOT = Path(__file__).resolve().parent
_SPARK_HELPERS = frozenset({'mesh_fabric', 'kernel_backend', 'kernel_readiness',
                           'topology', 'transport_profiles', 'model_layout', 'recipe'})
_loaded_helpers = {}


def _owned_helper(name):
    return name != __name__ and (name.startswith('q03_') or name in _SPARK_HELPERS)


class _SourceLoader(importlib.machinery.SourceFileLoader):
    def get_code(self, fullname):
        # Never consult .pyc, extension modules, packages, or an import-path shadow.
        return self.source_to_code(self.get_data(self.path), self.path)

    def exec_module(self, module):
        super().exec_module(module)
        _loaded_helpers[self.name] = (module, self)


class _SourceFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if not _owned_helper(fullname):
            return None
        source = _SOURCE_ROOT / (fullname + '.py')
        if '.' in fullname or not source.is_file() or source.resolve() != source:
            raise RuntimeError('owned helper source is missing or aliased: ' + fullname)
        return importlib.util.spec_from_file_location(fullname, source,
            loader=_SourceLoader(fullname, str(source)))


def _check_helpers():
    for name, module in tuple(sys.modules.items()):
        if _owned_helper(name):
            known = _loaded_helpers.get(name)
            if known is None or known[0] is not module or known[1] is not module.__loader__:
                raise RuntimeError('use a fresh interpreter without preloaded or replaced task helpers')


@contextlib.contextmanager
def _source_imports(*, check=True):
    if os.path.lexists(BYTECODE_PREFIX):
        raise RuntimeError('isolated unused bytecode prefix already exists')
    if check:
        _check_helpers()
    before = sys.dont_write_bytecode, sys.pycache_prefix
    meta, paths = sys.meta_path[:], sys.path[:]
    sys.dont_write_bytecode, sys.pycache_prefix = True, BYTECODE_PREFIX
    sys.meta_path.insert(0, _SourceFinder())
    try:
        yield
    finally:
        sys.dont_write_bytecode, sys.pycache_prefix = before
        sys.meta_path[:], sys.path[:] = meta, paths


def _source_only(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        with _source_imports():
            return function(*args, **kwargs)
    return wrapped


with _source_imports(check=__name__ == '__main__'):
    import q03_http_service as H
    from q03_held_window import read_owned_bytes, verify_outer, verify_history, derive_attempt_manifest

require, canonical, digest, json_bytes = H.require, H.canonical, H.digest, H.json_bytes
SCHEMA = 'legacy-baseline-pair-report/1'
BATCH_SCHEMA = 'legacy-baseline-batch-pair-report/1'
REVISED_SCHEMA = 'r6x-baseline-pair-report/1'
REVISED_BATCH_SCHEMA = 'r6x-baseline-batch-pair-report/1'
SOURCE_INVENTORY = 'owned-http-finite-batch/2'


def read_json(path):
    return json_bytes(read_owned_bytes(Path(path)))


def _closed_session(directory):
    from q03_window import Session, RuntimeOps
    ops = RuntimeOps()
    # Source inventories are whole, versioned contracts, never optional files.
    # Pre-batch capsules must be read with their matching archived reader/source
    # checkout; deleting the new entry cannot make older hashes compatible.
    sources = read_json(Path(directory) / 'capsule.json').get('recovery_source_sha256', {})
    expected = ops.recovery_sources()
    if set(sources) == set(expected) - {'q03_baseline_batch.py'}:
        raise RuntimeError('pre-batch source inventory owned-http-single/1 requires its matching archived source checkout')
    require(set(sources) == set(expected), 'unsupported recovery source inventory; expected ' + SOURCE_INVENTORY)
    session = Session(directory, ops)  # Constructor reads files/source hashes only.
    verify_outer(session, require_hold=False)
    verify_history(session)
    state = session.state
    require(state.get('phase') == 'closed-stopped' and state.get('original_kept_stopped') is True
            and state.get('restored') is False and state.get('hold_released') is True
            and state.get('active_attempt') is None and state.get('active_process') is None
            and state.get('active_gate') is None, 'complete keep-stopped closure required')
    rows = state.get('attempt_results', [])
    require(len(rows) == 1 and rows[0].get('outcome') == 'passed'
            and rows[0].get('passed_gates') == ['http_service'] and rows[0].get('failure') is None,
            'exactly one passed selected-case attempt required; no retry selection')
    return session


def _closure_evidence(session, ended_utc):
    from q03_window_preflight import validate_preflight
    captures = sorted(session.directory.glob('preflight-restore_drained-*.json'))
    require(captures, 'retained all-four final GPU drain observation required')
    hashes, times = [], []
    for path in captures:
        value = read_json(path)
        require(value.get('phase') == 'restore_drained' and type(value.get('node_count')) is int
                and value['node_count'] == 4 and value.get('gpus_empty') is True,
                'final all-four GPU drain is missing or incomplete')
        observed = copy.deepcopy(value)
        observed['now'] = datetime.fromisoformat(value['now'].replace('Z', '+00:00'))
        require(observed['now'].tzinfo is not None and observed['now'].timestamp() >= ended_utc,
                'final GPU drain predates client completion')
        expected = dict(session.manifest['preflight'], window_id=session.capsule['window_id'])
        validate_preflight(expected, observed)
        hashes.append(digest(read_owned_bytes(path)))
        times.append(observed['now'].timestamp())
    return dict(sha256=hashes, observed_utc=times)


def _attempt_manifest(plan, session, descriptor, bridge):
    """Replay the typed first-attempt derivation without reopening admission.

    build_attempt is intentionally not called: a closed session already has its
    terminal predecessor and attempt directory. Its admission-only guards must
    not be disabled or replayed against invented pre-admission session state.
    """
    keys = {'schema_version', 'window_id', 'capsule_sha256', 'attempt_id',
            'previous_receipt_sha256', 'candidate_updates', 'jobs', 'command_plan_sha256'}
    require(isinstance(descriptor, dict) and set(descriptor) == keys
            and type(descriptor['schema_version']) is int and descriptor['schema_version'] == 1,
            'selected-case typed descriptor fields/schema differ')
    attempt_id = descriptor['attempt_id']
    require(type(attempt_id) is str and re.fullmatch(r'[0-9a-f]{32}', attempt_id)
            and attempt_id == plan['attempt_id']
            and descriptor['window_id'] == plan['window_id'] == session.capsule['window_id']
            and descriptor['capsule_sha256'] == digest(read_owned_bytes(session.directory / 'capsule.json')),
            'selected-case descriptor window/attempt/capsule differs')
    bridge.validate_jobs(dict(session.manifest, jobs=descriptor['jobs']))
    # read_drained admits exactly one retained attempt, not a retry selection.
    require(descriptor['previous_receipt_sha256'] is None
            and isinstance(descriptor['candidate_updates'], dict) and not descriptor['candidate_updates']
            and descriptor['jobs'] == session.manifest['jobs'],
            'selected-case descriptor predecessor/jobs/updates differ')
    require(descriptor['command_plan_sha256'] == digest(canonical([['http_service', plan]])),
            'selected-case typed command plan differs')
    return derive_attempt_manifest(session, attempt_id, descriptor['jobs'], descriptor['candidate_updates'])


def _fixed_plan(plan, session, bridge, descriptor):
    require(plan.get('schema_version') == 6 and plan.get('kind') == 'http_service',
            'only the selected original-case schema6 is comparable')
    binding = plan['binding']
    bridge.validate_jobs(session.manifest)
    manifest = _attempt_manifest(plan, session, descriptor, bridge)
    require(binding['http'] == manifest['http_service']
            and binding['candidate'] == manifest['candidate']
            and binding['selection'] == plan['selection'] == manifest['jobs'][0]['selection'],
            'plan/derived attempt selected arm or candidate differs')
    fixed = binding['http']['baseline_inputs']
    replay_binding = copy.deepcopy(binding)
    for key in ('profile', 'nodes', 'effective_config'):
        require(binding[key] == fixed[key], 'fixed resource binding differs: ' + key)
        value = H.owner.pinned_json(binding[key])
        projection = {'profile': 'profile_value', 'nodes': 'nodes_value', 'effective_config': 'effective_value'}[key]
        require(value == binding[projection], 'resolved fixed resource bytes differ: ' + key)
        # Saved plans are canonical JSON, but the original renderer preserves
        # nested mapping order inside string-valued environment/argv fields.
        # Reuse only hash-verified original inputs after semantic binding checks.
        replay_binding[projection] = value
    identity_module = bridge.identity_module()
    revised = plan['selection']['arm'] in ('r6x-unpatched', 'r6x-steer')
    freeze = identity_module.validate_revised_freeze() if revised else None
    profile_sha = freeze['profile_sha256'][plan['selection']['arm']] if revised else bridge.PROFILE_SHA256
    effective_sha = freeze['effective_sha256'] if revised else bridge.EFFECTIVE_SHA256
    require(binding['profile']['sha256'] == profile_sha
            and binding['effective_config']['sha256'] == effective_sha,
            'reviewed normal profile/effective configuration differs')
    if revised:
        require(digest(canonical(bridge.common_recipe(binding['profile_value'])))
                == freeze['common_recipe_sha256'], 'revised common serving recipe differs')
    identity = identity_module.validate_binding(binding['http']['baseline_identity'], binding['candidate'])
    require(all(binding['baseline_identity'][k] == v for k, v in identity.items()),
            'static provenance differs from retained plan')
    regenerated = bridge.http_plan(replay_binding, plan['window_id'], plan['attempt_id'], plan['selection'])
    require(canonical(regenerated) == canonical(plan), 'finite selected-case plan does not reproduce')


def _journal_chain(directory, plan, context, ready_raw, *, batch_runs=None):
    from q03_rank_logs import verified_capture, binding as log_binding
    from q03_window import held_container_projection
    journal_dir = directory / 'http'
    state = read_json(journal_dir / 'state.json')
    rows = state['ranks']
    require(state.get('phase') == 'drained' and len(rows) == 4
            and [r.get('rank') for r in rows] == list(range(4))
            and all(r.get('removed') is True for r in rows), 'all-four HTTP retirement required')
    contexts = [context] if batch_runs is None else [run['context'] for run in batch_runs]
    require(all(c['ranks'] == [{k: r[k] for k in ('rank', 'container_id', 'config_sha256')} for r in rows]
            and c['ready_sha256'] == digest(ready_raw) for c in contexts),
            'owned context/readiness/container linkage differs')
    sequence = state.get('sequence')
    require(type(sequence) is int and sequence > 0, 'complete HTTP journal sequence required')
    paths = sorted(journal_dir.glob('[0-9][0-9][0-9][0-9][0-9][0-9].json'))
    require([p.name for p in paths] == [f'{i:06d}.json' for i in range(1, sequence + 1)],
            'HTTP journal missing or extra events')
    events = [read_json(p) for p in paths]
    require(all(e.get('sequence') == i and all(e.get(k) == plan[k] for k in ('window_id', 'attempt_id'))
                for i, e in enumerate(events, 1)), 'foreign HTTP journal event')
    pins = [e for e in events if e.get('kind') == 'baseline-runtime-context-pinned']
    if batch_runs is None:
        require(len(pins) == 1 and pins[0].get('context_sha256') == digest(canonical(context)),
                'controller-owned admission context pin differs')
    else:
        _batch_journal(plan, state, events, batch_runs)
    require(events[-1].get('kind') == 'phase' and events[-1].get('phase') == 'drained',
            'HTTP journal has not reached positive drain')
    index_raw = read_owned_bytes(journal_dir / 'rank-logs-index.json')
    index, drained = json_bytes(index_raw), read_json(journal_dir / 'drained.json')
    require(index == dict(schema_version=1, window_id=plan['window_id'], attempt_id=plan['attempt_id'],
        plan_sha256=plan['plan_sha256'], node_count=4, logs_complete=True,
        ranks=[dict(log_binding(plan, r), capture=r.get('log_capture'), not_created=False) for r in rows]),
        'full exact-container rank log index differs')
    require(drained == dict(window_id=plan['window_id'], attempt_id=plan['attempt_id'], node_count=4,
        daemon_quiescent=True, gpu_drain_required_by_caller=True, plan_sha256=plan['plan_sha256'],
        logs_complete=True, log_index_sha256=digest(index_raw)), 'positive daemon/log drain receipt differs')
    journal = SimpleNamespace(directory=journal_dir, plan=plan)
    resource_configs = []
    for row in rows:
        stopped = verified_capture(journal, row, H.validate_container)
        # Recheck the original fixed resource/command/environment admission, not
        # merely a self-declared config hash. This validator performs no actions.
        H.validate_container(stopped, plan, row, initial=True)
        # Bind source paths necessarily differ by boot/runtime. Actual mounts were
        # just validated against the immutable plan; compare all other resource fields.
        resource_configs.append({k: copy.deepcopy(v) for k, v in held_container_projection(stopped)['HostConfig'].items()
                                 if k not in ('Binds', 'Mounts')})
    return resource_configs


@_source_only
def read_drained(directory):
    """Consume actual retained paths; never rewrite or relocate hash-bound inputs."""
    directory = Path(directory)
    require(directory.is_absolute() and directory.resolve() == directory
            and directory.parent.name == 'attempts', 'canonical retained attempt directory required')
    bridge = H.baseline_module()
    legacy = bridge.legacy_module()
    session = _closed_session(directory.parent.parent)
    require(session.state['attempt_results'][0]['attempt_id'] == directory.name,
            'selected attempt is not the complete session history')
    plan = read_json(directory / 'http' / 'plan.json')
    H.verify_plan_identity(session, plan)
    descriptor = read_json(directory / 'descriptor.json')
    _fixed_plan(plan, session, bridge, descriptor)
    terminal_raw = read_owned_bytes(directory / 'terminal.json')
    terminal = json_bytes(terminal_raw)
    require(plan['attempt_id'] == directory.name and descriptor['jobs'] == session.manifest['jobs']
            and terminal['descriptor_sha256'] == digest(canonical(descriptor))
            and terminal['command_plan_sha256'] == descriptor['command_plan_sha256']
            and digest(terminal_raw) == session.state['attempt_receipt_sha256'][directory.name],
            'selected-case descriptor/terminal linkage differs')
    drain = terminal.get('quiescence', {})
    require(drain.get('quiescent') is True and type(drain.get('node_count')) is int and drain['node_count'] == 4
            and drain.get('attempt_id') == plan['attempt_id'] and drain.get('phase') in ('stopped', 'restore_drained'),
            'positive all-four attempt drain required')
    if 'case_sequence' in plan['binding']['http']:
        return _read_batch(directory, plan, session, descriptor, terminal_raw, bridge, legacy)
    run = _read_case(plan, directory / 'http', legacy)
    require(read_json(directory / 'http_service-result.json') == run.pop('controller_result'),
            'passed controller result differs from complete client')
    ready_raw = read_owned_bytes(directory / 'http' / 'ready-observed.json')
    run['ready'] = bridge.validate_ready(ready_raw, plan)
    run['resources'] = _journal_chain(directory, plan, run['context'], ready_raw)
    closure = _closure_evidence(session, run['evidence']['ended_utc'])
    run['lineage'].update(capsule_sha256=descriptor['capsule_sha256'], terminal_sha256=digest(terminal_raw),
        closure_sha256=closure['sha256'], closure_observed_utc=closure['observed_utc'])
    return run


def _read_case(plan, directory, legacy):
    path = Path(plan['client']['output'])
    output_raw = read_owned_bytes(path)
    output = json_bytes(output_raw)
    receipt = read_json(path.with_name('client-receipt.json'))
    require(all(receipt.get(k) == plan[k] for k in ('window_id', 'attempt_id', 'plan_sha256'))
            and type(receipt.get('exit_code')) is int and receipt['exit_code'] == 0
            and receipt.get('error') is None and receipt.get('output_sha256') == digest(output_raw),
            'complete supervised-client receipt differs')
    result = legacy.validate_output(output, plan)
    expected_result = dict(result='passed', window_id=plan['window_id'], attempt_id=plan['attempt_id'],
        image=plan['image'], source_head=plan['source_head'], plan_sha256=plan['plan_sha256'],
        result_sha256=digest(output_raw), toolbox_head=plan['client']['toolbox_head'], **result)
    require(canonical(read_json(directory / 'result.json')) == canonical(expected_result),
            'passed controller result differs from complete client')
    context_raw = read_owned_bytes(directory / 'baseline-runtime-context.json')
    require(context_raw == read_owned_bytes(plan['client']['runtime_context']),
            'client context differs from controller-owned bytes')
    evidence = read_json(path.with_name('legacy-evidence.json'))
    saved_run = read_json(path.with_name('legacy-run.json'))
    return dict(plan=plan, receipt=output, context=json_bytes(context_raw), controller_result=expected_result,
        raw=evidence['raw_run'], summary=saved_run['summary'], evidence=evidence,
        lineage=dict(result_sha256=digest(output_raw), raw_sha256=output['raw_sha256'],
                     evidence_sha256=output['evidence_sha256']))


def _case_scopes(plan, directory):
    # Independently derive the fixed projections, not a caller-selected scope or
    # producer helper result. Retained nested JSON order stays untouched.
    require(plan['binding']['http'].get('case_sequence') == ['warm4k_c1', 'warm4k_c4']
            and type(plan['binding']['http']['case_sequence']) is list
            and plan['binding']['http']['legacy_case']['case_id'] == 'warm4k_c1'
            and plan['client']['legacy_case']['case_id'] == 'warm4k_c1'
            and plan['selection'] == plan['binding']['selection'] == plan['client']['legacy_case']['selection']
            and 'batch_case' not in plan, 'fixed same-state C1 then C4 parent required')
    scopes = []
    root = Path(plan['client']['output']).parent
    for index, case in enumerate(('warm4k_c1', 'warm4k_c4')):
        child = copy.deepcopy(plan)
        child['batch_case'] = dict(parent_plan_sha256=plan['plan_sha256'], index=index)
        child['client']['legacy_case']['case_id'] = case
        child['client']['output'] = str(root / case / 'legacy-case.json')
        child['client']['runtime_context'] = str(root / case / 'runtime-context.json')
        del child['plan_sha256']
        child['plan_sha256'] = digest(canonical(child))
        scope = directory / ('case-' + case)
        for path in (scope, Path(child['client']['output']), Path(child['client']['runtime_context'])):
            require(path.is_absolute() and path.resolve() == path and '..' not in path.parts,
                    'batch case path alias is not allowed')
        scopes.append((child, scope))
    require({p.name for p in directory.glob('case-*')} == {'case-warm4k_c1', 'case-warm4k_c4'}
            and {p.name for p in root.iterdir()} == {'warm4k_c1', 'warm4k_c4'},
            'missing or extra batch case paths')
    return scopes


def _successful_client(value, plan, operation, *, sealed=False):
    require(value.get('window') == plan['window_id'] and value.get('operation') == operation
            and value.get('status') == 'terminal' and value.get('action_succeeded') is True
            and value.get('group_quiescent') is True and value.get('cancelled') is False
            and value.get('timed_out') is False and type(value.get('exit_code')) is int
            and value['exit_code'] == 0
            and (not sealed or value.get('epoch_sealed') is True and value.get('safe_to_overlap') is True),
            'batch client failed, cancelled, timed out or UNKNOWN')


def _batch_journal(plan, state, events, runs):
    from q03_remote_operation import bound_command
    cases = ('warm4k_c1', 'warm4k_c4')
    phases = [e for e in events if e.get('kind') == 'phase']
    require(not any(e.get('kind') == 'failure' for e in events)
            and [e.get('phase') for e in phases[:8]] == list(H.PHASES[1:-1])
            and phases[8:] and all(e.get('phase') == 'drained' for e in phases[8:]),
            'batch parent lifecycle is failed, partial or out of order')
    relevant = [e for e in events if e.get('kind') in (
        'baseline-case-intent', 'baseline-prefix-reset', 'baseline-runtime-context-pinned', 'baseline-case-complete')]
    require(len(relevant) == 8 and phases[6]['sequence'] < relevant[0]['sequence']
            and relevant[-1]['sequence'] < phases[7]['sequence'], 'missing or extra batch case event')
    epochs = state.get('epochs', [])
    clients = [e for e in epochs if e.get('action_kind') == 'http_client']
    require(len(clients) == 2 and len({e['epoch'] for e in epochs}) == len(epochs)
            and len({e['operation'] for e in epochs}) == len(epochs),
            'missing, extra or duplicate batch client epochs')
    client_intents = [e for e in events if e.get('kind') == 'remote-intent' and e.get('action_kind') == 'http_client']
    seals = [e for e in events if e.get('kind') == 'http-client-epoch-sealed']
    require(len(client_intents) == 2 and len(seals) == 8, 'missing or extra batch client epoch events')
    previous = previous_finished = None
    for index, (case, run, epoch) in enumerate(zip(cases, runs, clients)):
        child = run['plan']
        intent, reset_event, pin, complete = relevant[4 * index:4 * index + 4]
        result_sha = digest(canonical(run['controller_result']))
        expected_events = [dict(kind='baseline-case-intent', case_index=index, case_id=case,
            case_plan_sha256=child['plan_sha256'], previous_result_sha256=previous),
            dict(kind='baseline-runtime-context-pinned', case_index=index,
                 context_sha256=digest(canonical(run['context']))),
            dict(kind='baseline-case-complete', case_index=index, case_id=case,
                 case_plan_sha256=child['plan_sha256'], result_sha256=result_sha)]
        for event, expected in zip((intent, pin, complete), expected_events):
            require(all(type(event.get(k)) is type(v) and event[k] == v for k, v in expected.items()),
                    'batch case order/identity/context/result event differs')
        reset = run['context']['admission']['prefix_reset']
        require(reset_event.get('kind') == 'baseline-prefix-reset'
                and canonical(reset_event.get('receipt')) == canonical(reset)
                and intent['sequence'] < reset_event['sequence'] < pin['sequence'],
                'batch case reset event missing, duplicated, reordered or receipt differs')
        command = bound_command(['python3', '-I', '-B',
            str(Path(plan['binding']['checkout']) / 'scripts/q03_http_client.py'), '--plan',
            str(Path(run['controller_directory']) / 'client-plan.json'), '--sha256', child['plan_sha256']])
        require(set(epoch) == {'rank', 'action_kind', 'epoch', 'operation', 'argv_sha256'}
                and type(epoch['rank']) is int and epoch['rank'] == 0
                and all(type(epoch[k]) is str and re.fullmatch(r'[0-9a-f]{32}', epoch[k])
                        for k in ('epoch', 'operation'))
                and epoch['argv_sha256'] == digest(canonical(command))
                and state.get('sealed_epochs', []).count(epoch['epoch']) == 1,
                'batch client command/epoch binding differs')
        remote = client_intents[index]
        require(all(type(remote.get(k)) is type(v) and remote[k] == v for k, v in epoch.items())
                and pin['sequence'] < remote['sequence'] < complete['sequence'],
                'batch client intent differs or is out of order')
        terminals = [e for e in events if e.get('kind') == 'remote-terminal'
                     and e.get('operation') == epoch['operation']]
        require(len(terminals) == 1 and type(terminals[0].get('rank')) is int
                and terminals[0]['rank'] == 0
                and remote['sequence'] < terminals[0]['sequence'] < complete['sequence'],
                'batch client terminal missing, extra or out of order')
        _successful_client(terminals[0]['receipt'], plan, epoch['operation'])
        operation_dir = Path(plan['binding']['independent_root']) / 'remote-operations' / plan['window_id'] / epoch['operation']
        manifest_raw = read_owned_bytes(operation_dir / 'manifest.json')
        manifest = json_bytes(manifest_raw)
        require(read_owned_bytes(operation_dir / 'manifest.sha256').decode().strip() == digest(manifest_raw)
                == terminals[0]['receipt'].get('manifest_sha256')
                and set(manifest) == {'root', 'window', 'epoch', 'operation', 'argv', 'cwd',
                                     'timeout_seconds', 'created_epoch', 'helper_sha256'}
                and manifest['root'] == str(operation_dir.parent.parent)
                and manifest['window'] == plan['window_id'] and manifest['epoch'] == epoch['epoch']
                and manifest['operation'] == epoch['operation'] and manifest['argv'] == command
                and manifest['cwd'] == plan['binding']['independent_root']
                and manifest['helper_sha256'] == digest(read_owned_bytes(_SOURCE_ROOT / 'q03_remote_operation.py'))
                and type(manifest['timeout_seconds']) in (int, float)
                and 0 < manifest['timeout_seconds'] <= child['client']['timeout_seconds']
                and type(manifest['created_epoch']) in (int, float) and math.isfinite(manifest['created_epoch']),
                'batch operation manifest does not bind the exact fixed client')
        # All of these are wall-clock Unix timestamps from the owned head.
        # Never compare them to the client's accelerated/monotonic clock.
        samples = run['context']['admission']['thermal']['samples']
        require(all((previous_finished is None or previous_finished <= sample['sampled_utc'])
                    and sample['sampled_utc'] <= reset['completed_utc'] <= manifest['created_epoch']
                    for sample in samples),
                'batch final thermal/reset admission must follow prior client completion and precede launch')
        receipt = read_json(operation_dir / 'receipt.json')
        require(set(receipt) == {'window', 'operation', 'manifest_sha256', 'group_quiescent',
                                'exit_code', 'cancelled', 'timed_out', 'finished_epoch'}
                and all(type(terminals[0]['receipt'].get(k)) is type(v)
                        and terminals[0]['receipt'][k] == v for k, v in receipt.items())
                and type(receipt['finished_epoch']) in (int, float) and math.isfinite(receipt['finished_epoch'])
                and manifest['created_epoch'] <= run['evidence']['started_utc']
                <= run['evidence']['ended_utc'] <= receipt['finished_epoch']
                and (index == 1 or receipt['finished_epoch'] <= runs[1]['evidence']['started_utc']),
                'batch operation receipt/client chronology differs')
        operation_receipt = receipt
        epoch_seals = [e for e in seals if e.get('epoch') == epoch['epoch']]
        require([e.get('rank') for e in epoch_seals] == list(range(4)), 'all-four batch client seal required')
        for rank, event in enumerate(epoch_seals):
            receipt = event['receipt']
            require(type(event['rank']) is int and terminals[0]['sequence'] < event['sequence'] < complete['sequence']
                    and receipt.get('window') == plan['window_id'] and receipt.get('epoch') == epoch['epoch']
                    and receipt.get('sealed') is True and receipt.get('safe_to_overlap') is True
                    and receipt.get('status') == 'quiescent'
                    and len(receipt.get('operations', [])) == (1 if rank == 0 else 0),
                    'batch client all-four seal is incomplete/UNKNOWN or out of order')
            if rank == 0:
                _successful_client(receipt['operations'][0], plan, epoch['operation'], sealed=True)
                require(all(type(receipt['operations'][0].get(k)) is type(v)
                            and receipt['operations'][0][k] == v for k, v in operation_receipt.items()),
                        'batch terminal/seal operation receipt differs')
        previous, previous_finished = result_sha, operation_receipt['finished_epoch']
    require(runs[0]['evidence']['ended_utc'] <= runs[1]['evidence']['started_utc'],
            'same-boot batch client chronology overlaps or reverses')


def _read_batch(directory, plan, session, descriptor, terminal_raw, bridge, legacy):
    journal = directory / 'http'
    runs = []
    for child, scope in _case_scopes(plan, journal):
        require(read_owned_bytes(scope / 'client-plan.json') == canonical(child),
                'retained batch client plan differs from independent projection')
        run = _read_case(child, scope, legacy)
        run['controller_directory'] = str(scope)
        runs.append(run)
    expected = dict(result='passed', window_id=plan['window_id'], attempt_id=plan['attempt_id'],
        plan_sha256=plan['plan_sha256'], selection=copy.deepcopy(plan['selection']),
        cases=[dict(case_id=run['plan']['client']['legacy_case']['case_id'],
                    case_plan_sha256=run['plan']['plan_sha256'],
                    result_sha256=digest(canonical(run['controller_result'])), result=run['controller_result'])
               for run in runs], lifecycle='REQUIRES_OWNED_FOUR_RANK_DRAIN',
        performance='NOT_EVALUATED', quality='NOT_EVALUATED', capacity='NOT_EVALUATED')
    require(canonical(read_json(journal / 'result.json')) == canonical(expected)
            and canonical(read_json(directory / 'http_service-result.json')) == canonical(expected),
            'whole batch controller result differs; one case is not a complete batch')
    ready_raw = read_owned_bytes(journal / 'ready-observed.json')
    ready = bridge.validate_ready(ready_raw, plan)
    resources = _journal_chain(directory, plan, None, ready_raw, batch_runs=runs)
    closure = _closure_evidence(session, max(run['evidence']['ended_utc'] for run in runs))
    lineage = dict(capsule_sha256=descriptor['capsule_sha256'], terminal_sha256=digest(terminal_raw),
        parent_plan_sha256=plan['plan_sha256'], closure_sha256=closure['sha256'],
        closure_observed_utc=closure['observed_utc'], source_inventory=SOURCE_INVENTORY)
    for run in runs:
        run['ready'], run['resources'] = ready, resources
        run['lineage'].update(lineage)
        run.pop('controller_result'); run.pop('controller_directory')
    return dict(scope='FIXED_C1_THEN_C4_SAME_BOOT', plan=plan, cases=runs, lineage=lineage)


def _stats(plan):
    """Exact PR8 nearest-rank definition; p95 is an explicitly added view."""
    legacy = H.baseline_module().legacy_module()
    legacy.verify_toolbox(plan['client']['toolbox_path'], plan['client']['toolbox_files'])
    module = ModuleType('_legacy_pair_frozen_stats')
    path = Path(plan['client']['toolbox_path']) / 'servebench/stats.py'
    exec(compile(read_owned_bytes(path), str(path), 'exec'), module.__dict__)
    return module


@_source_only
def absolute(run):
    summary, raw = copy.deepcopy(run['summary']), run['raw']
    stats = _stats(run['plan'])
    w0, w1 = raw['window']
    rows = [r for r in raw['records'] if w0 <= r['sent'] < w1]
    # The producer rejected errors, approximate timing and incomplete records.
    p95 = {kind: stats.percentiles(values, points=(95,)) for kind, values in (
        ('ttft_s', [r['first'] - r['sent'] for r in rows]),
        ('tpot_ms', [1000 * (r['end'] - r['first']) / (r['completion_tokens'] - 1)
                     for r in rows if r['completion_tokens'] > 1 and r['end'] > r['first']]),
        ('e2e_s', [r['end'] - r['sent'] for r in rows]))}
    require(all(p.get('n', 0) > 0 for p in p95.values()), 'missing measured-window latency samples')
    return dict(original_pr8_summary=summary, additional_p95=p95,
        p95_scope='Same PR8 sent-in-[w0,w1) request subset; nearest-rank, unrounded raw times',
        within_run_statistics='Six subwindows only; not six independent boots',
        thermal_admission=run['context']['admission']['thermal'],
        prefix_reset=run['context']['admission']['prefix_reset'],
        run_scope=dict(warmup_s=15, duration_s=90, within_run_subwindows=6,
                       latency_requests=len(rows), latency_window='request sent in [w0,w1)',
                       throughput_window='token events in the six measured subwindows'))


def _row(name, a, b, unit, scope, *, margin=None, higher=False):
    valid = all(type(v) in (int, float) and math.isfinite(v) for v in (a, b))
    delta = b - a if valid else None
    change = 100 * (b / a - 1) if valid and a != 0 else None
    status = 'DEFINED' if change is not None else ('UNDEFINED_ZERO_BASELINE' if valid else 'UNAVAILABLE')
    flag = None
    if margin is not None:
        flag = ('NOT_ASSESSABLE' if change is None else
                'INVESTIGATE_POINT_ESTIMATE' if (change < -margin if higher else change > margin)
                else 'NO_POINT_ESTIMATE_FLAG_NOT_ACCEPTANCE')
    return dict(metric=name, a188=a, b197_off=b, absolute_change=delta, change_percent=change,
                relative_status=status, unit=unit, scope=scope, descriptive_margin_pct=margin,
                descriptive_flag=flag, paired_boot_ci95=None)


@_source_only
def compare_pair(a_attempt, b_attempt):
    """One historical or revised pair. No formal performance/quality claim."""
    a, b = read_drained(a_attempt), read_drained(b_attempt)
    if 'cases' in a or 'cases' in b:
        require(a.get('scope') == b.get('scope') == 'FIXED_C1_THEN_C4_SAME_BOOT'
                and len(a.get('cases', [])) == len(b.get('cases', [])) == 2,
                'both arms require a complete same-boot batch; no mixed/partial intake')
        results = [_compare_runs(left, right) for left, right in zip(a['cases'], b['cases'])]
        require([r['case_id'] for r in results] == ['warm4k_c1', 'warm4k_c4'], 'batch paired case order differs')
        require(len({r['comparison'] for r in results}) == 1, 'batch pair identity differs between cases')
        return dict(schema=REVISED_BATCH_SCHEMA if results[0]['schema'] == REVISED_SCHEMA else BATCH_SCHEMA,
            status='DESCRIPTIVE_BATCH_PAIR_COMPLETE_NOT_ACCEPTANCE',
            comparison=results[0]['comparison'],
            performance='INCONCLUSIVE', quality='NOT_EVALUATED', capacity='NOT_EVALUATED',
            independent_boot_pairs=1, same_boot_cases=2, paired_boot_ci95=None,
            case_sequence=['warm4k_c1', 'warm4k_c4'], cases=results,
            lineage={name: run['lineage'] for name, run in zip(results[0]['arms'], (a, b))},
            limitations=['C1 and C4 reuse one boot per arm, not two independent paired repetitions.',
                'Per-case reports share that one boot pair; do not pool their subwindows or claim acceptance.',
                'One selected steering state, not the complete matrix; quality and capacity are not evaluated.'])
    return _compare_runs(a, b)


def _compare_runs(a, b):
    pa, pb = a['plan'], b['plan']
    arms = (pa['selection']['arm'], pb['selection']['arm'])
    revised = arms == ('r6x-unpatched', 'r6x-steer')
    require(revised or arms == ('r6x188', 'native197-off'),
            'ordered historical A188/B197-OFF or revised unpatched/steer pair required')
    names = ('R6X_UNPATCHED', 'R6X_STEER') if revised else ('A188', 'B197_OFF')
    comparison = ('REVISED_R6X_UNPATCHED_VS_STEER_NOT_Q03_CAUSAL' if revised else
                  'VERSION_PACKAGE_A188_VS_B197_OFF_NOT_Q03_CAUSAL')
    if revised:
        bridge = H.baseline_module()
        identity = bridge.identity_module()
        freeze = identity.validate_revised_freeze()
        for plan in (pa, pb):
            identity.validate_selection(plan['selection'])
            require(plan['spec']['baseline_identity']['identity'] ==
                    identity.checked_identity(plan['selection']['arm']), 'revised pair actual arm identity differs')
            require(digest(canonical(bridge.common_recipe(plan['binding']['profile_value']))) ==
                    freeze['common_recipe_sha256'], 'revised pair common serving recipe differs')
            require(identity.common_runtime_sha256(plan['spec']['baseline_identity']['runtime']) ==
                    freeze['common_runtime_sha256'], 'revised pair common runtime differs')
        require(pa['selection']['steering'] == 'absent', 'unpatched pair state must be absent')
    else:
        require(pa['selection']['steering'] == pb['selection']['steering'], 'steering states differ')
    qa, qb = pa['client']['legacy_case'], pb['client']['legacy_case']
    for key in ('workload', 'case_id', 'corpus', 'corpus_sha256', 'metric_labels'):
        require(canonical(qa[key]) == canonical(qb[key]), 'case/corpus/semantic identity differs: ' + key)
    for key in (('nodes_value', 'effective_value') if revised else
                ('profile_value', 'nodes_value', 'effective_value')):
        require(canonical(pa['binding'][key]) == canonical(pb['binding'][key]), 'matched resource config differs: ' + key)
    configs = [copy.deepcopy(p['spec']['expected_effective_config']) for p in (pa, pb)]
    if revised:
        absent = {'present': False, 'value': None}
        loaded = {'present': True, 'value': H.owner.FIXED_ENGINE_KWARGS['additional_config']['ds41_steering']}
        require(configs[0].get('additional_config.ds41_steering') == absent,
                'unpatched pair has steering configuration')
        expected_steering = absent if pb['selection']['steering'] == 'unloaded' else loaded
        require(configs[1].get('additional_config.ds41_steering') == expected_steering,
                'revised steering configuration differs from selected state')
        for plan, config in zip((pa, pb), configs):
            mode = 'off' if plan['selection']['steering'] in ('absent', 'unloaded') else 'loaded_zero'
            require(config == plan['binding']['effective_value'][mode],
                    'revised pair selected frozen effective configuration differs')
        for config in configs:
            config.pop('additional_config.ds41_steering', None)
    require(configs[0] == configs[1]
            and a['resources'] == b['resources'] and pa['docker_args'] == pb['docker_args'],
            'actual per-rank resource configuration differs')
    if not revised:
        require([r['steering_profile'] for r in a['ready']['reports']] ==
                [r['steering_profile'] for r in b['ready']['reports']], 'actual shared steering assets differ')
    require(pa['window_id'] != pb['window_id'] and pa['attempt_id'] != pb['attempt_id']
            and not ({r['container_id'] for r in a['context']['ranks']} &
                     {r['container_id'] for r in b['context']['ranks']}), 'pair reuses session/attempt/container boot identity')
    require(a['evidence']['ended_utc'] <= b['evidence']['started_utc']
            and max(a['lineage']['closure_observed_utc']) <= b['evidence']['started_utc'],
            'first pilot A drain/B run chronology overlaps or reverses')
    aa, ab = absolute(a), absolute(b)
    sa, sb = aa['original_pr8_summary'], ab['original_pr8_summary']
    rows = [_row('output_tps', sa['output_tps']['mean'], sb['output_tps']['mean'], 'output tokens/s',
                 'PR8 six measured subwindow mean', margin=3, higher=True)]
    for name, unit in (('ttft_s', 's'), ('tpot_ms', 'ms/token'), ('e2e_s', 's'), ('per_user_tps', 'tokens/s')):
        for p in ('mean', 'p50', 'p90', 'p99'):
            rows.append(_row(name + '.' + p, sa['latency'][name].get(p), sb['latency'][name].get(p), unit,
                             'PR8 requests sent in the measured window'))
    for name, unit in (('ttft_s', 's'), ('tpot_ms', 'ms/token'), ('e2e_s', 's')):
        rows.append(_row(name + '.p95', aa['additional_p95'][name]['p95'], ab['additional_p95'][name]['p95'], unit,
                         'Additional exact-PR8 nearest-rank view', margin=5 if name != 'e2e_s' else None))
    for name in ('failed', 'rejected', 'foreign', 'approximate_token_times'):
        rows.append(_row('requests.' + name, sa['requests'][name], sb['requests'][name], 'requests',
                         'Original run request accounting, including warmup'))
    units = {'prefix_cache_hit_rate': 'fraction', 'generation_tps': 'tokens/s', 'prompt_tps': 'tokens/s',
             'steps_per_s': 'steps/s', 'output_tokens_per_step': 'tokens/step', 'scheduled_tokens_per_step': 'tokens/step',
             'accepted_per_draft': 'tokens/draft', 'draft_tokens_per_draft': 'tokens/draft', 'preemptions': 'preemptions/subwindow'}
    for name, unit in units.items():
        left, right = sa['server'].get(name, {}), sb['server'].get(name, {})
        rows.append(_row('server.' + name, left.get('mean'), right.get('mean'), unit,
                         'PR8 server subwindow mean; undefined denominator stays unavailable'))
    if revised:
        for row in rows:
            row['r6x_unpatched'] = row.pop('a188')
            row['r6x_steer'] = row.pop('b197_off')
    return dict(schema=REVISED_SCHEMA if revised else SCHEMA, status='DESCRIPTIVE_PAIR_COMPLETE_NOT_ACCEPTANCE',
        comparison=comparison,
        performance='INCONCLUSIVE', quality='NOT_EVALUATED', capacity='NOT_EVALUATED',
        independent_boot_pairs=1, statistical_power='UNKNOWN', paired_boot_ci95=None,
        case_id=qa['case_id'], steering=(dict(unpatched='absent', steer=pb['selection']['steering'])
            if revised else pa['selection']['steering']), corpus_sha256=qa['corpus_sha256'],
        arms={name: dict(identity=run['plan']['spec']['baseline_identity']['identity'],
                        runtime_sha256=run['plan']['runtime_sha256'], lineage=run['lineage'], absolute=summary)
              for name, run, summary in ((names[0], a, aa), (names[1], b, ab))},
        paired_changes=rows, proposed_flags='3% throughput / 5% p95 latency point estimates only; not a statistical test',
        limitations=['One selected case and steering state, not the full matrix.',
          'Six within-run subwindows are not independent boot repetitions; no between-boot confidence interval.',
          'Tail percentiles are descriptive; sample count does not establish tail stability or statistical power.',
          'Thermal admission passed independently; matching thermal trajectories and physical boot independence need field review.',
          'No semantic/capability artifact was evaluated; completed output is not quality evidence.',
          'Fresh shared corpus, not byte-identical historical188 document replay.',
          'Hashes establish retained byte lineage, not independent attestation of physical observations.'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline_attempt', type=Path)
    parser.add_argument('candidate_attempt', type=Path)
    args = parser.parse_args(argv)
    try:
        result = compare_pair(args.baseline_attempt, args.candidate_attempt)
    except (RuntimeError, ValueError, KeyError, TypeError, OSError, OverflowError, IndexError, AttributeError) as error:
        result = dict(schema=SCHEMA, status='REJECTED_INCOMPLETE_OR_INCOMPARABLE', performance='INCONCLUSIVE',
                      quality='NOT_EVALUATED', capacity='NOT_EVALUATED', reason=str(error))
        print(canonical(result).decode())
        return 1
    print(canonical(result).decode())
    return 0


if __name__ == '__main__':
    sys.exit(main())
