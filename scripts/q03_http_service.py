#!/usr/bin/env python3
"""Fixed HTTP/service-and-client lane in a NEW immutable Q03 held capsule.

Uses bin/spark's resolved serving profile/renderer, existing exact-ID guards,
remote operation epochs and independent window watchdog. Never changes the
closed owner_model contract or grants caller-supplied argv authority.
"""
from __future__ import annotations
import argparse
import copy
import fcntl
import json
import math
import types
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request
import uuid

import q03_owner_model as owner
from q03_held_window import exclusive_json, read_owned_bytes

require, canonical, digest, json_bytes = owner.require, owner.canonical, owner.digest, owner.json_bytes
IMAGE, SOURCE_HEAD, RUNTIME_SHA = owner.IMAGE, owner.SOURCE_HEAD, owner.RUNTIME_SHA
PHASES = ('admitted', 'prepared4', 'created4', 'startup_guards4', 'started4',
          'config_ready', 'steady_guards4', 'client_running', 'client_terminal', 'drained')
SOURCE_FILES = ('q03_http_service.py', 'q03_http_worker.py', 'q03_http_client.py', 'q03_http_toolbox.json', 'q03_qualification.py', 'q03_quality_matrix.py', 'q03_performance.py', 'q03_baseline_service.py', 'q03_baseline_identity.py', 'q03_baseline_worker.py', 'q03_legacy_case_replay.py')
BOOT_MODES = ('off', 'off', 'dual')
QUALIFICATION_SOURCE_SHA256 = 'f41e9fac23ee88defa71d6aed6dddaa6b0630b8c5e369f14b02c17ee2d242c1e'


QUALITY_MATRIX_SOURCE_SHA256 = '0192cef0b2cf1765824231998976708893b968c64c5f18a12cf3f3de90722959'


def qualification_module(schema_version=2):
    # A new module must not bypass its source pin via ignored timestamp .pyc.
    require(type(schema_version) is int and schema_version in (2, 3), 'qualification schema differs')
    name, expected = ('q03_qualification.py', QUALIFICATION_SOURCE_SHA256) if schema_version == 2 else (
        'q03_quality_matrix.py', QUALITY_MATRIX_SOURCE_SHA256)
    path = Path(__file__).with_name(name)
    raw = read_owned_bytes(path)
    require(digest(raw) == expected, 'qualification source pin differs')
    module = types.ModuleType('_q03_qualification_source')
    module.__file__ = str(path)
    if schema_version == 2:
        exec(compile(raw, str(path), 'exec'), module.__dict__)
    else:
        # Bind the matrix dependency to the same source-only legacy module, not .pyc.
        previous = sys.modules.get('q03_qualification')
        sys.modules['q03_qualification'] = qualification_module(2)
        try:
            exec(compile(raw, str(path), 'exec'), module.__dict__)
        finally:
            if previous is None:
                del sys.modules['q03_qualification']
            else:
                sys.modules['q03_qualification'] = previous
    return module


def quality_module():
    """Fixed source-pinned Stage 1b reader for separately admitted successors."""
    return qualification_module(3)


PERFORMANCE_SOURCE_SHA256 = '53bcbf7cd715c11ae23bf31354a61943c1fd0b11b3649ef72139a14c37dc0ebf'


def performance_module():
    path = Path(__file__).with_name('q03_performance.py')
    raw = read_owned_bytes(path)
    require(digest(raw) == PERFORMANCE_SOURCE_SHA256, 'performance source pin differs')
    module = types.ModuleType('_q03_performance_source'); module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def is_baseline(value):
    http = value.get('http_service', value.get('http', {}))
    return type(http.get('schema_version')) is int and http['schema_version'] == 6


BASELINE_SOURCE_SHA256 = '8d69b4546f6000e2eed935a4aedc73ce9aac3d42b5fa758a34b124e0426cdceb'

def baseline_module():
    # This new finite lane is pinned with the complete controller recovery inventory.
    # It never changes the identity or meaning of any pre-existing schema.
    path = Path(__file__).with_name('q03_baseline_service.py')
    raw = read_owned_bytes(path)
    require(digest(raw) == BASELINE_SOURCE_SHA256, 'baseline bridge source pin differs')
    module = types.ModuleType('_q03_baseline_service_source')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def plan_image(plan):
    if is_baseline(plan.get('binding', {})):
        expected = baseline_module().identity_module().checked_identity(plan['selection']['arm'])
        require(plan.get('image') == expected['image'], 'baseline plan image differs')
        return expected['image']
    return IMAGE


def finite_corpus(value, binding=None):
    if value.get('schema_version') == 4:
        config, module = value['performance_screen'], performance_module()
    else:
        config, module = value['qualification'], qualification_module(value['schema_version'])
    return config, module.validate_binding(config, binding)


def is_http(manifest):
    return any(j.get('kind') == 'http_service' for j in manifest.get('jobs', []))


def validate_jobs(manifest):
    if is_baseline(manifest):
        return baseline_module().validate_jobs(manifest)
    jobs = manifest.get('jobs')
    require(isinstance(jobs, list) and len(jobs) == 1, 'HTTP attempt has one bounded job')
    job = jobs[0]
    require(isinstance(job, dict) and set(job) == {'kind', 'timeout_seconds', 'selection'}
            and job['kind'] == 'http_service' and type(job['timeout_seconds']) is int
            and 1 <= job['timeout_seconds'] <= 7200, 'invalid bounded HTTP job')
    owner.validate_selection(job['selection'])
    require(type(manifest.get('window_seconds')) is int and manifest['window_seconds'] == 10800,
            'HTTP outer lease must remain 10800 seconds')
    require(manifest['preflight'].get('original_service_disposition') == 'keep-stopped',
            'HTTP lane forbids original START')


def next_selection(session):
    if is_baseline(session.manifest):
        return baseline_module().next_selection(session)
    if session.manifest.get('http_service', {}).get('schema_version') == 4:
        return performance_module().next_selection(session)
    if session.manifest.get('http_service', {}).get('schema_version') in (2, 3):
        return qualification_module(session.manifest['http_service']['schema_version']).next_selection(session)
    passed = [r for r in session.state.get('attempt_results', [])
              if r.get('outcome') == 'passed' and r.get('passed_gates') == ['http_service']]
    require(len(passed) < 3, 'OFF/OFF/DUAL HTTP smoke matrix is complete')
    steering = session.manifest['jobs'][0]['selection']['steering']
    for index, row in enumerate(passed):
        path = session.directory / 'attempts' / row['attempt_id'] / 'descriptor.json'
        job = json_bytes(read_owned_bytes(path))['jobs'][0]
        require(job['selection'] == {'mode': BOOT_MODES[index], 'steering': steering},
                'HTTP smoke matrix predecessor differs')
    return {'mode': BOOT_MODES[len(passed)], 'steering': steering}


def owner_manifest(manifest):
    value = copy.deepcopy(manifest)
    value['jobs'][0]['kind'] = 'owner_model'
    value['owner_model'] = value['http_service']['owner_inputs']
    return value


def validate_owner_matrix(binding, candidate):
    """All six actual owner cells and positive drain precede HTTP admission."""
    matrix = binding['owner_matrix']
    require(isinstance(matrix, dict) and set(matrix) == {'capsule', 'terminals'}
            and isinstance(matrix['terminals'], list) and len(matrix['terminals']) == 6,
            'actual six-cell owner matrix required before HTTP')
    capsule = owner.pinned_json(matrix['capsule'])
    old = capsule['manifest']['candidate']
    require(all(old[k] == candidate[k] for k in ('image', 'component_head', 'runtime_manifest_sha256')),
            'owner matrix native identity differs')
    prior = capsule['manifest'].get('owner_model')
    require(isinstance(prior, dict) and isinstance(prior.get('tests'), dict)
            and prior['tests'].get('tree') == owner.TEST_TREE
            and prior['tests'].get('fixture_sha256') == owner.TEST_SHA,
            'owner matrix qualification fixture differs')
    # Compare semantic input hashes, not their relocatable private paths. The
    # expected pins come from the admitted binding, never an observed receipt.
    for key in ('profile', 'effective_config', 'nodes'):
        require(isinstance(prior.get(key), dict)
                and prior[key].get('sha256') == binding['owner_inputs'][key]['sha256'],
                'owner matrix qualification ' + key + ' differs')
    seen = set()
    for bindings in matrix['terminals']:
        require(set(bindings) == {'terminal', 'descriptor'}, 'owner matrix cell bindings differ')
        terminal, descriptor = (owner.pinned_json(bindings[k]) for k in ('terminal', 'descriptor'))
        require(terminal.get('outcome') == 'passed' and terminal.get('passed_gates') == ['owner_model']
                and terminal.get('quiescence', {}).get('quiescent') is True
                and type(terminal['quiescence'].get('node_count')) is int
                and terminal['quiescence']['node_count'] == 4
                and terminal['attempt_id'] == descriptor['attempt_id']
                and terminal['window_id'] == descriptor['window_id'] == capsule['window_id']
                and terminal['descriptor_sha256'] == digest(canonical(descriptor))
                and descriptor['capsule_sha256'] == matrix['capsule']['sha256'],
                'owner matrix cell not passed, drained or identity bound')
        jobs = descriptor['jobs']
        require(len(jobs) == 1 and jobs[0]['kind'] == 'owner_model', 'foreign owner matrix job')
        owner.validate_selection(jobs[0]['selection'])
        cell = tuple(jobs[0]['selection'][k] for k in ('mode', 'steering'))
        require(cell not in seen, 'duplicate owner matrix cell')
        seen.add(cell)
    require(seen == {(m, s) for m in owner.MODES for s in owner.STEERING}, 'owner matrix incomplete')


def validate_binding(manifest, ops, *, remote=True):
    if is_baseline(manifest):
        return baseline_module().validate_binding(manifest, ops, remote=remote)
    validate_jobs(manifest)
    value = manifest.get('http_service')
    fields = {'schema_version', 'owner_inputs', 'owner_matrix', 'toolbox', 'api_port',
              'startup_seconds', 'client_seconds'}
    require(isinstance(value, dict) and type(value.get('schema_version')) is int
            and value['schema_version'] in (1, 2, 3, 4), 'HTTP schema differs')
    require(set(value) == (fields if value['schema_version'] == 1 else fields |
            ({'performance_screen'} if value['schema_version'] == 4 else {'qualification'})),
            'HTTP binding fields differ')
    if value['schema_version'] in (2, 3):
        validate_qualification = qualification_module(value['schema_version']).validate_binding
        validate_qualification(value['qualification'])
        if value['schema_version'] == 2:
            require(manifest['jobs'][0]['selection']['steering'] == 'off',
                    'first lean qualification slice is unloaded only')
    if value['schema_version'] == 4:
        performance_module().validate_binding(value['performance_screen'])
        require(manifest['jobs'][0]['selection']['steering'] == 'active',
                'performance is active1 only; owned sequence determines OFF/DUAL')
    for key, low, high in (('api_port', 1024, 65535), ('startup_seconds', 1, 3600), ('client_seconds', 1, 600)):
        require(type(value[key]) is int and low <= value[key] <= high, 'invalid HTTP bound: ' + key)
    require(value['api_port'] != value['owner_inputs']['master_port'], 'HTTP and rendezvous ports collide')
    binding = owner.validate_owner_binding(owner_manifest(manifest), ops)
    validate_owner_matrix(value, manifest['candidate'])
    toolbox = value['toolbox']
    inventory = json_bytes(read_owned_bytes(Path(__file__).with_name('q03_http_toolbox.json')))
    require(isinstance(toolbox, dict) and set(toolbox) == {'path', 'head', 'tree', 'git_config_sha256'}
            and toolbox['head'] == inventory['head'] and toolbox['tree'] == inventory['tree'],
            'toolbox must be separate exact c6342 checkout')
    from q03_git_toolchain import validate_git_metadata
    validate_git_metadata(toolbox['path'], toolbox['git_config_sha256'])
    git = ['git', '--no-replace-objects', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null',
           '-C', toolbox['path']]
    require(Path(toolbox['path'], '.git').is_dir()
            and ops.run([*git, 'rev-parse', 'HEAD']).stdout.strip() == toolbox['head']
            and ops.run([*git, 'rev-parse', 'HEAD^{tree}']).stdout.strip() == toolbox['tree']
            and not ops.run([*git, 'status', '--porcelain']).stdout.strip(), 'toolbox checkout changed')
    for relative, expected in inventory['files'].items():
        require(digest(read_owned_bytes(Path(toolbox['path']) / relative)) == expected, 'toolbox bytes differ')
    binding.update(http=copy.deepcopy(value), toolbox_inventory=inventory)
    if value['schema_version'] in (2, 3):
        validate_qualification(value['qualification'], binding)
    if value['schema_version'] == 4:
        performance_module().validate_binding(value['performance_screen'], binding)
    if remote:
        validate_remote_inputs(manifest, binding, ops)
    return binding


def validate_paths(manifest, directory):
    if is_baseline(manifest):
        return baseline_module().validate_paths(manifest, directory)
    owner.validate_paths(owner_manifest(manifest), directory)
    from q03_original_spark_adapter import evidence_path
    resource = evidence_path(str(Path(manifest['independent_root']) / 'http-resources'), manifest)
    toolbox = Path(manifest['http_service']['toolbox']['path'])
    immutable = [toolbox, Path(manifest['preflight']['independent_checkout'])]
    immutable += [Path(manifest['candidate'][k]) for k in ('tests_path', 'runtime_manifest_path',
        'tblib_path', 'build_receipt_path', 'build_inputs_path', 'git_toolchain_path')]
    immutable.append(Path(manifest['http_service']['owner_inputs']['tests']['path']))
    matrix = manifest['http_service']['owner_matrix']
    immutable.append(Path(matrix['capsule']['path']))
    immutable += [Path(cell[key]['path']) for cell in matrix['terminals'] for key in ('terminal', 'descriptor')]
    for key in ('profile', 'nodes', 'effective_config'):
        immutable.append(Path(manifest['http_service']['owner_inputs'][key]['path']))
    if manifest['http_service'].get('schema_version') in (2, 3, 4):
        qualification, corpus = finite_corpus(manifest['http_service'])
        immutable += [Path(qualification['corpus']['path']), Path(corpus['prompts']['path']),
                      Path(corpus['artifacts']['model_root'])]
        immutable += [Path(pin['path']) for pin in [corpus['artifacts']['weights_manifest'],
                      corpus['artifacts']['runtime_metadata'], *corpus['artifacts']['native_sources'].values()]]
    if manifest['http_service'].get('schema_version') == 3:
        prior = manifest['http_service']['qualification']['stage1a']['capsule']
        immutable += [Path(prior['path']).parent,
                      Path(owner.pinned_json(prior)['manifest']['preflight']['independent_checkout'])]
    if manifest['http_service'].get('schema_version') == 4:
        stage1 = manifest['http_service']['performance_screen']['stage1']
        immutable.append(Path(stage1['session']))
        prior = owner.pinned_json(stage1['capsule'])['manifest']['http_service']['qualification']['stage1a']['capsule']
        immutable += [Path(prior['path']).parent,
                      Path(owner.pinned_json(prior)['manifest']['preflight']['independent_checkout'])]
    writable = [resource, evidence_path(directory, manifest)]
    writable += [evidence_path(manifest['candidate'][k], manifest) for k in ('cache_path', 'output_path')]
    for target in writable:
        require(not any(target == p or target in p.parents or p in target.parents for p in immutable),
                'HTTP writable path overlaps immutable input')
    for index, left in enumerate(writable):
        require(not any(left == right or left in right.parents or right in left.parents
                        for right in writable[index+1:]), 'HTTP writable roots overlap')


def replace_flag(args, key, value):
    require(args.count(key) == 1, 'normal profile must name exactly one ' + key)
    index = args.index(key)
    args[index + 1] = str(value)


def http_plan(binding, window_id, attempt_id, selection, *, session=None):
    if is_baseline(binding):
        return baseline_module().http_plan(binding, window_id, attempt_id, selection, session=session)
    # The owner plan supplies pinned identity/expected resolved projection only.
    # Serving argv is rendered from the ACTUAL normal profile, not EngineArgs.
    plan = owner.owner_plan(binding, window_id, attempt_id, selection)
    profile = copy.deepcopy(binding['profile_value'])
    args = profile['serve_args']
    require(args[0] == '/models', 'HTTP uses the exact local checkpoint')
    for flag, value in (('--max-model-len', 1048576), ('--max-num-seqs', 16),
                        ('--max-num-batched-tokens', 8192), ('--kv-cache-memory-bytes', 11274289152),
                        ('--tensor-parallel-size', 4), ('--nnodes', 4)):
        require(args.count(flag) == 1 and args[args.index(flag) + 1] == str(value),
                'normal profile changes fixed capacity/topology: ' + flag)
    replace_flag(args, '--host', '127.0.0.1')
    replace_flag(args, '--port', binding['http']['api_port'])
    if '--additional-config' in args:
        index = args.index('--additional-config')
        require(args.count('--additional-config') == 1, 'duplicate additional config')
        additional = json_bytes(args[index + 1])
        require(set(additional) == {'ds41_steering'}, 'unknown normal profile additional config')
        if selection['steering'] == 'off':
            del args[index:index + 2]
    elif selection['steering'] != 'off':
        raise RuntimeError('loaded steering requires normal profile additional config')
    require('--worker-extension-cls' not in args, 'unreviewed worker extension in profile')
    args += ['--worker-extension-cls', 'q03_http_worker.HttpObservation']
    profile['distributed']['master_port'] = str(binding['master_port'])
    profile['container']['image'] = IMAGE
    profile['container']['expected_labels'] = json_bytes(read_owned_bytes(
        binding['candidate']['build_receipt_path']))['image_labels']
    spark = owner.load_spark()
    for row in plan['ranks']:
        rank = row['rank']
        row['name'] = f'q03-http-{window_id}-{attempt_id}-{rank}'
        row['root'] = str(Path(binding['independent_root']) / 'http-resources' / window_id / attempt_id / str(rank))
        row['environment'] = spark.expected_environment(profile, row['node'])
        require(row['environment'].pop('PYTHONPATH') == binding['candidate']['pytest_base_pythonpath'],
                'HTTP normal profile changes baked Python path')
        row['environment'].update(VLLM_SERVER_DEV_MODE='1', PYTHONDONTWRITEBYTECODE='1',
                                 VLLM_ALLOW_INSECURE_SERIALIZATION='0',
                                 Q03_HTTP_WORKER_SHA256=digest(read_owned_bytes(
                                     Path(binding['checkout']) / 'scripts/q03_http_worker.py')),
                                 VLLM_DS41_Q03_MHC_MXFP8='1' if selection['mode'] == 'dual' else '0',
                                 Q03_HTTP_WINDOW=window_id, Q03_HTTP_ATTEMPT=attempt_id,
                                 Q03_HTTP_SPEC_SHA256=plan['spec_sha256'])
        row['mounts'] = [[s.format(home=profile['host']['home']), d, m]
                          for s, d, m in profile['container']['mounts'] if d != '/cache']
        require(all(m == 'ro' for _, _, m in row['mounts']), 'unapproved profile writable mount')
        row['mounts'] += [[binding['tests']['path'] + '/tests', '/validation/tests', 'ro'],
            [binding['candidate']['runtime_manifest_path'], '/runtime/runtime-manifest.json', 'ro'],
            [row['root'] + '/spec.json', '/runtime/http-spec.json', 'ro'],
            [str(Path(binding['checkout']) / 'scripts/q03_http_worker.py'), '/q03-http/q03_http_worker.py', 'ro'],
            [row['root'] + '/cache', '/cache', 'rw'], [row['root'] + '/results', '/results', 'rw']]
        row['python_argv'] = ['-B', '/q03-http/q03_http_worker.py', 'serve', *spark.expected_command(profile, row['node'])]
        # Invoke the normal renderer for resource/environment/mount derivation,
        # then transform detached run into inert create before exact-ID start.
        per_rank = copy.deepcopy(profile)
        per_rank['container']['name'] = row['name']
        per_rank['container']['mounts'] = row['mounts']
        normal = spark.rendered_docker_command(per_rank, row['node'])
        require(normal[:5] == ['docker', 'run', '-d', '--name', row['name']], 'normal renderer contract changed')
        # Replace the renderer's environment with the closed approved delta.
        image_index = normal.index(IMAGE)
        options = normal[3:image_index]
        clean, index = [], 0
        while index < len(options):
            if options[index] == '--env':
                index += 2
            else:
                clean.append(options[index]); index += 1
        create = ['docker', 'create', *clean, '--workdir', '/validation']
        for key, val in sorted(row['environment'].items()):
            create += ['--env', key + '=' + str(val)]
        for key, val in {'q03.window': window_id, 'q03.attempt': attempt_id,
                         'q03.job': 'http_service', 'q03.rank': str(rank)}.items():
            create += ['--label', key + '=' + val]
        row['create_prefix'] = create
    plan.update(url=f"http://127.0.0.1:{binding['http']['api_port']}", kind='http_service',
                client={'toolbox_path': binding['http']['toolbox']['path'],
                        'toolbox_head': binding['toolbox_inventory']['head'],
                        'toolbox_files': binding['toolbox_inventory']['files'],
                        'prompts': binding['toolbox_inventory']['prompts'],
                        'output': plan['ranks'][0]['root'] + '/results/smoke.json',
                        'timeout_seconds': binding['http']['client_seconds']})
    if binding['http'].get('schema_version') in (2, 3):
        version = binding['http']['schema_version']
        client_plan = qualification_module(version).client_plan
        plan['client']['qualification'] = (client_plan(binding, selection) if version == 2
                                           else client_plan(binding, selection, session))
        plan['client']['qualification_sources'] = {name: digest(read_owned_bytes(
            Path(binding['checkout']) / 'scripts' / (name + '.py')))
            for name in (('q03_qualification', 'q03_owner_model', 'q03_held_window')
                         + (('q03_quality_matrix',) if version == 3 else ()))}
        plan['client']['output'] = plan['ranks'][0]['root'] + '/results/lean.json'
    if binding['http'].get('schema_version') == 4:
        plan['client']['performance_screen'] = performance_module().client_plan(binding, selection)
        plan['client']['performance_sources'] = {name: digest(read_owned_bytes(
            Path(binding['checkout']) / 'scripts' / (name + '.py')))
            for name in ('q03_performance', 'q03_owner_model', 'q03_held_window')}
        plan['client']['output'] = plan['ranks'][0]['root'] + '/results/performance-screen.json'
        plan['client']['runtime_context'] = plan['ranks'][0]['root'] + '/results/runtime-context.json'
    plan.pop('plan_sha256')
    plan['plan_sha256'] = digest(canonical(plan))
    return plan


def validate_ready(raw, plan):
    if is_baseline(plan.get("binding", {})):
        return baseline_module().validate_ready(raw, plan)
    value = json_bytes(raw)
    reports = value.get('reports')
    require(isinstance(reports, list) and len(reports) == 4, 'HTTP readiness needs all four worker reports')
    require(all(r.get('http_runtime_sha256') == RUNTIME_SHA and r.get('http_fixture_sha256') == owner.TEST_SHA
                and r.get('http_window_id') == plan['window_id']
                and r.get('http_attempt_id') == plan['attempt_id']
                and r.get('http_spec_sha256') == plan['spec_sha256']
                for r in reports), 'HTTP native/fixture/attempt identity differs')
    return owner.validate_ready(raw, plan)


def input_contract(binding, *, include_toolbox=True):
    if is_baseline(binding):
        return baseline_module().input_contract(binding, include_toolbox=include_toolbox)
    value = owner.input_contract(binding)
    for name in SOURCE_FILES:
        path = Path(binding['checkout']) / 'scripts' / name
        value['files'].append({'path': str(path), 'sha256': digest(read_owned_bytes(path))})
    if not include_toolbox:
        return value
    if binding['http'].get('schema_version') in (2, 3, 4):
        qualification, corpus = finite_corpus(binding['http'])
        value['files'] += [qualification['corpus'], corpus['prompts'],
                           corpus['artifacts']['weights_manifest'], corpus['artifacts']['runtime_metadata'],
                           *corpus['artifacts']['native_sources'].values()]
    toolbox = binding['http']['toolbox']
    value['files'] += [{'path': str(Path(toolbox['path']) / relative), 'sha256': expected}
                       for relative, expected in binding['toolbox_inventory']['files'].items()]
    value['files'].append({'path': str(Path(toolbox['path']) / '.git/config'), 'sha256': toolbox['git_config_sha256']})
    value['directories'].append(toolbox['path'])
    return value


def boundary_contract(binding, node, path):
    if is_baseline(binding):
        return baseline_module().boundary_contract(binding, node, path)
    value = owner.boundary_contract(binding, node, path)
    include_toolbox = node['rank'] == 1  # physical head is rank1; logical head is rank0
    value['input_files'] = input_contract(binding, include_toolbox=include_toolbox)['files']
    toolbox = binding['http']['toolbox']['path']
    value['immutable_paths'].append(toolbox)
    if binding['http'].get('schema_version') in (2, 3, 4):
        qualification, corpus = finite_corpus(binding['http'])
        value['immutable_paths'] += [qualification['corpus']['path'], corpus['prompts']['path']]
    if include_toolbox:
        value['input_directories'].append(toolbox)
        value['strict_input_directories'].append(toolbox)
    return value


def validate_remote_inputs(manifest, binding, ops):
    if is_baseline(manifest):
        return baseline_module().validate_remote_inputs(manifest, binding, ops)
    from q03_window_preflight import _node_argv
    from q03_remote_operation import bound_command
    for node in owner.remote_nodes(binding):
        contract = boundary_contract(binding, node, str(Path(binding['independent_root']) / 'http-resources'))
        result = ops.run(_node_argv(manifest['preflight'], node, bound_command(
            ['python3', '-I', '-B', '-c', owner.REMOTE_BOUNDARY, canonical(contract).decode()])), timeout=90)
        require(json_bytes(result.stdout).get('paths_safe') is True, 'HTTP all-four boundary not proven')
        result = ops.run(_node_argv(manifest['preflight'], node, bound_command(
            ['python3', '-I', '-B', '-c', owner.INPUT_PROBE, canonical(input_contract(binding, include_toolbox=node['rank'] == 1)).decode()])), timeout=90)
        report = json_bytes(result.stdout)
        require(report.get('image') == IMAGE and report.get('test_tree') == owner.TEST_TREE,
                'HTTP all-four input/image identity differs')
        if binding['http'].get('schema_version') in (2, 3, 4):
            legacy = qualification_module(2)
            _, corpus = finite_corpus(binding['http'], binding)
            files = legacy.artifact_files(corpus)
            result = ops.run(_node_argv(manifest['preflight'], node, bound_command(
                ['python3', '-I', '-B', '-c', legacy.ASSET_PROBE, canonical(files).decode()])), timeout=90)
            require(result.returncode == 0 and json_bytes(result.stdout) == {'verified_files': len(files)},
                    'all-four mounted model/tokenizer asset identity differs')


class Journal:
    """One fenced writer. Intent survives process death independently of core state."""
    def __init__(self, session, plan, *, create=False):
        self.session, self.plan = session, plan
        self.directory = session.directory / "attempts" / plan["attempt_id"] / "http"
        if create:
            self.directory.mkdir(mode=0o700, exist_ok=False)
            exclusive_json(self.directory / "plan.json", plan)
            exclusive_json(self.directory / "state.json", dict(
                phase="admitted", sequence=0, epochs=[], ranks=[
                    dict(rank=i, container_id=None, config_sha256=None, guard_intent=False,
                         guard_phase=None, removed=False) for i in range(4)]))
        raw = read_owned_bytes(self.directory / "plan.json")
        require(json_bytes(raw) == plan, "retained HTTP plan changed")
        self.state = json_bytes(read_owned_bytes(self.directory / "state.json"))
        self.lock = open(self.directory / "http.lock", "a")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def close(self):
        self.lock.close()

    def save(self):
        from q03_window import atomic
        atomic(self.directory / "state.json", self.state)

    def event(self, kind, **fields):
        sequence = self.state["sequence"] + 1
        exclusive_json(self.directory / (f"{sequence:06d}.json"), dict(
            sequence=sequence, kind=kind, window_id=self.plan["window_id"],
            attempt_id=self.plan["attempt_id"], **fields))
        self.state["sequence"] = sequence
        self.save()

    def phase(self, phase):
        require(phase in PHASES, "unknown owner phase")
        self.event("phase", phase=phase)
        self.state["phase"] = phase
        self.save()


def validate_container(item, plan, record, *, initial=False):
    from q03_window import held_container_sha256
    rank = record["rank"]
    wanted = plan["ranks"][rank]
    require(isinstance(item, dict) and re.fullmatch(r"[0-9a-f]{64}", item.get("Id", ""))
            and item["Id"] == record["container_id"] and item.get("Image") == plan_image(plan),
            "owner exact container/image identity differs")
    config, host = item["Config"], item["HostConfig"]
    labels = config.get("Labels", {})
    require(all(labels.get(k) == v for k, v in {
        "q03.window": plan["window_id"], "q03.attempt": plan["attempt_id"], "q03.job": "http_service",
        "q03.rank": str(rank), "q03.plan": plan["plan_sha256"]}.items()), "owner container labels differ")
    if initial:
        require("OomKillDisable" in host and
                (host["OomKillDisable"] is None or host["OomKillDisable"] is False),
                "owner OOM killing must remain enabled")
        require(config.get("Entrypoint") == ["python3"] and config.get("Cmd") == wanted["python_argv"]
                and config.get("WorkingDir") == "/validation" and host.get("NetworkMode") == "host"
                and host.get("IpcMode") == "host" and host.get("Privileged") is False
                and host.get("AutoRemove") is False and host.get("RestartPolicy", {}).get("Name") in ("", "no")
                and host.get("Memory") == 112 * 1024**3 and host.get("MemorySwap") == 112 * 1024**3
                and host.get("ShmSize") == 16 * 1024**3 and host.get("CpusetCpus") == "5-9,15-19",
                "owner transport/resource/command configuration differs")
        expected_env = dict(wanted["environment"], PYTHONPATH=plan["binding"]["candidate"]["pytest_base_pythonpath"])
        environment = config.get("Env", [])
        require(all([v for v in environment if v.startswith(k + "=")] == [k + "=" + str(val)]
                    for k, val in expected_env.items()), "owner per-node environment differs")
        actual = sorted((m.get("Source"), m.get("Destination"), m.get("RW")) for m in item.get("Mounts", []))
        # The one DRM mount is part of the fixed existing profile's Docker arguments.
        expected_mounts = [(s, d, m == "rw") for s, d, m in wanted["mounts"]]
        expected_mounts.append(("/dev/dri/by-path/pci-000f:01:00.0-card", "/dev/dri/card0", True))
        require(actual == sorted(expected_mounts), "owner mount plan differs")
        requests = host.get("DeviceRequests")
        require(isinstance(requests, list) and len(requests) == 1 and requests[0].get("Count") == -1
                and requests[0].get("Capabilities") == [["gpu"]], "owner GPU request differs")
        devices = host.get("Devices")
        require(devices == [{"PathOnHost": "/dev/infiniband", "PathInContainer": "/dev/infiniband",
                             "CgroupPermissions": "rwm"}]
                # Docker may return the canonical CAP_ spelling in inspect.
                # Accept only this single capability, with no extras/duplicates.
                and host.get("CapAdd") in (["IPC_LOCK"], ["CAP_IPC_LOCK"])
                and sorted(host.get("SecurityOpt") or []) == ["label=disable", "seccomp=unconfined"]
                and host.get("DeviceCgroupRules") == ["c 226:* rw"],
                "owner RDMA/device/capability/security profile differs")
        limits = {v["Name"]: (v["Soft"], v["Hard"]) for v in host.get("Ulimits", [])}
        require(len(limits) == len(host.get("Ulimits", [])) and
                all(limits.get(k) == v for k, v in {"memlock": (-1, -1),
                    "stack": (67108864, 67108864), "core": (0, 0)}.items()),
                "owner memlock/stack/core limits differ")
        require(item["State"].get("Running") is False, "owner container must be inert before start")
    else:
        require(held_container_sha256(item) == record["config_sha256"], "owner immutable config drift")
    return held_container_sha256(item)


def require_guard(state, plan, record, phase):
    require(isinstance(state, dict) and state.get("owned") is True
            and state.get("active") is True and state.get("phase") == phase
            and state.get("container_id") == record["container_id"],
            "owned exact-ID " + phase + " guard not proven active")



def run_http(session, plan, poll=None, ops=None):
    journal = Journal(session, plan, create=True)
    ops = HttpOps(session, plan, journal) if ops is None else ops
    ops.journal = journal
    records = journal.state['ranks']
    def check():
        if poll is not None:
            poll()
        ops.check_authority()
    def running(phase):
        for row in records:
            item = ops.inspect(row['rank'], row)
            validate_container(item, plan, row)
            require(item['State'].get('Running') is True, 'HTTP rank not running')
            require_guard(ops.guard_state(row['rank'], row), plan, row, phase)
    try:
        check()
        for row in records:
            check()
            journal.event('prepare-intent', rank=row['rank'])
            ops.action(row['rank'], 'prepare', row)
        ops.seal_all()
        journal.phase('prepared4')
        for row in records:
            check()
            journal.event('create-intent', rank=row['rank'])
            ident = ops.action(row['rank'], 'create', row)
            require(isinstance(ident, str) and re.fullmatch(r'[0-9a-f]{64}', ident),
                    'HTTP create has no exact ID: UNKNOWN')
            row['container_id'] = ident
            journal.save()
            item = ops.inspect(row['rank'], row)
            from q03_window import held_container_sha256
            row['config_sha256'] = held_container_sha256(item)
            journal.save()
            validate_container(item, plan, row, initial=True)
        ops.seal_all()
        journal.phase('created4')
        for row in records:
            check()
            require(ops.guard_state(row['rank'], row).get('absent') is True, 'existing HTTP guard refused')
            row['guard_intent'] = True
            journal.save()
            journal.event('startup-guard-intent', rank=row['rank'], container_id=row['container_id'])
            ops.action(row['rank'], 'startup_guard', row)
            require_guard(ops.guard_state(row['rank'], row), plan, row, 'startup')
            row['guard_phase'] = 'startup'
            journal.save()
        ops.seal_all()
        journal.phase('startup_guards4')
        for row in records:
            check()
            require_guard(ops.guard_state(row['rank'], row), plan, row, 'startup')
            journal.event('start-intent', rank=row['rank'], container_id=row['container_id'])
            ops.action(row['rank'], 'start', row)
        ops.seal_all()
        journal.phase('started4')
        deadline = time.monotonic() + plan['binding']['http']['startup_seconds']
        while True:
            check()
            require(time.monotonic() < deadline, 'bounded HTTP startup deadline reached')
            running('startup')
            raw = ops.read_ready()
            if raw is not None:
                ready = validate_ready(raw, plan)
                exclusive_json(journal.directory / 'ready-observed.json', ready)
                break
            ops.sleep(.2)
        journal.phase('config_ready')
        for row in records:
            check()
            validate_container(ops.inspect(row['rank'], row), plan, row)
            require_guard(ops.guard_state(row['rank'], row), plan, row, 'startup')
            journal.event('steady-guard-intent', rank=row['rank'])
            ops.action(row['rank'], 'steady_guard', row)
            require_guard(ops.guard_state(row['rank'], row), plan, row, 'steady')
            row['guard_phase'] = 'steady'
            journal.save()
        ops.seal_all()
        running('steady')
        journal.phase('steady_guards4')
        check()
        journal.phase('client_running')
        ops.run_client()
        ops.seal_all()  # No client descendants, late dispatch or unfinished RPC client.
        check()
        running('steady')
        final_ready = ops.read_ready()
        validate_ready(final_ready, plan)
        require(json_bytes(final_ready)['reports'] == ready['reports'], 'HTTP workers changed during client')
        result = ops.collect_result()
        require(result.get('result') == 'passed', 'HTTP client result did not pass')
        journal.phase('client_terminal')
        exclusive_json(journal.directory / 'result.json', result)
        return result
    except BaseException as exc:
        journal.event('failure', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        journal.close()


def verify_plan_identity(session, plan):
    directory = session.directory / 'attempts' / plan['attempt_id']
    descriptor = json_bytes(read_owned_bytes(directory / 'descriptor.json'))
    admitted = json_bytes(read_owned_bytes(directory / 'admitted.json'))
    require(descriptor['window_id'] == plan['window_id'] == session.capsule['window_id']
            and descriptor['capsule_sha256'] == digest(read_owned_bytes(session.directory / 'capsule.json'))
            and admitted['descriptor_sha256'] == digest(canonical(descriptor))
            and descriptor['command_plan_sha256'] == digest(canonical([['http_service', plan]]))
            and admitted['command_plan_sha256'] == descriptor['command_plan_sha256'],
            'HTTP plan not bound to immutable admitted attempt')
    expected = dict(plan)
    wanted = expected.pop('plan_sha256')
    require(digest(canonical(expected)) == wanted, 'HTTP plan digest differs')


class HttpOps(owner.OwnerOps):
    """Reuse existing fixed guard/inspect/remote supervision, not an argv service."""
    def __init__(self, session, plan, journal, *, recovering=False):
        self.session, self.plan, self.journal, self.recovering = session, plan, journal, recovering
        self.binding = plan['binding']
        self.nodes = owner.remote_nodes(self.binding)
        self.pre = session.manifest['preflight']
        from q03_original_spark_adapter import load_remote_helper
        self.helper = load_remote_helper(self.pre['remote_helper_sha256'])
        require(self.pre['remote_helper_sha256'] == session.capsule['recovery_source_sha256']['q03_remote_operation.py'],
                'HTTP helper differs from capsule')
        if recovering:
            state = session.ops.read_json(session.directory / 'state.json')
            self.end = state.get('restoring_started_epoch', time.time()) + session.capsule['recovery_timeout_s']
        else:
            admitted = json_bytes(read_owned_bytes(session.directory / 'attempts' / plan['attempt_id'] / 'admitted.json'))
            self.end = min(session.capsule['deadline_epoch'], admitted['http_deadline_epoch'])
        for rank in range(4):
            self.verify_boundary(rank)

    def verify_boundary(self, rank):
        contract = boundary_contract(self.binding, self.nodes[rank], self.plan['ranks'][rank]['root'])
        result = self.transport(rank, self.helper.bound_command(
            ['python3', '-I', '-B', '-c', owner.REMOTE_BOUNDARY, canonical(contract).decode()]))
        require(result.returncode == 0 and json_bytes(result.stdout) == {
            'hostname': contract['hostname'], 'authority_sha256': contract['authority_sha256'],
            'resource_path': contract['resource_path'], 'paths_safe': True}, 'HTTP remote boundary UNKNOWN')

    def check_authority(self):
        require(self.session.owns_hold(), 'HTTP adapter lost hold')
        require(time.time() < self.end, 'bounded HTTP/recovery deadline reached')
        require(self.session.capsule['recovery_source_sha256'] == self.session.ops.recovery_sources(),
                'HTTP immutable source/helper inventory changed')
        require(json_bytes(read_owned_bytes(self.journal.directory / 'plan.json')) == self.plan,
                'HTTP plan changed')
        verify_plan_identity(self.session, self.plan)
        if not self.recovering:
            from q03_window_watchdog import process_alive
            require(process_alive(self.session.capsule['core_process']), 'HTTP core died or PID reused')
            require(not self.session.requested(), 'cluster requested during HTTP job')
            core = self.session.ops.read_json(self.session.directory / 'state.json')
            require(core.get('active_attempt', {}).get('attempt_id') == self.plan['attempt_id']
                    and core.get('active_gate') == 'http_service', 'HTTP attempt lost core authority')
            control = self.session.directory / 'controls' / (f"{core['held_sequence'] + 1:08d}.request.json")
            require(not control.exists(), 'pending operator control; stop HTTP progress')
            if (self.journal.state['phase'] == 'client_running'
                    and time.monotonic() >= getattr(self, '_next_service_check', 0)):
                self._next_service_check = time.monotonic() + 5
                for row in self.journal.state['ranks']:
                    item = self.inspect(row['rank'], row)
                    validate_container(item, self.plan, row)
                    require(item['State'].get('Running') is True, 'HTTP rank lost during client')
                    require_guard(self.guard_state(row['rank'], row), self.plan, row, 'steady')

    def guard_input_contract(self):
        return input_contract(self.binding)

    def action(self, rank, kind, record):
        row = self.plan['ranks'][rank]
        if kind == 'prepare':
            contract = input_contract(self.binding, include_toolbox=rank == 0)
            contract.update(root=row['root'], independent_root=self.binding['independent_root'],
                            spec=canonical(self.plan['spec']).decode(), spec_sha256=self.plan['spec_sha256'])
            prepare = baseline_module().PREPARE if is_baseline(self.binding) else owner.PREPARE
            return self.mutate(rank, kind, ['python3', '-I', '-B', '-c', prepare, canonical(contract).decode()], 60)
        if kind == 'create':
            argv = [*row['create_prefix'], '--label', 'q03.plan=' + self.plan['plan_sha256'],
                    '--entrypoint', 'python3', plan_image(self.plan), *row['python_argv']]
            return self.mutate(rank, kind, argv, 90).strip()
        return super().action(rank, kind, record)

    def read_ready(self):
        self.check_authority()
        request = urllib.request.Request(self.plan['url'] + '/collective_rpc',
            data=canonical({'method': 'q03_http_observe', 'args': [], 'timeout': 15}),
            headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with urllib.request.urlopen(request, timeout=min(20, max(.1, self.end - time.time()))) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
        except urllib.error.HTTPError:
            raise  # A serving wrong/missing endpoint is not readiness.
        except (urllib.error.URLError, TimeoutError):
            return None
        require(len(raw) <= 4 * 1024 * 1024, 'HTTP observation response too large')
        reports = json_bytes(raw)['results']
        return canonical({'schema_version': 1, 'window_id': self.plan['window_id'],
            'attempt_id': self.plan['attempt_id'], 'spec_sha256': self.plan['spec_sha256'],
            'reports': reports, 'reports_sha256': digest(canonical(reports))})

    def legacy_runtime_context(self):
        """Original bounded thermal admission and reset, outside client measurement."""
        started = time.monotonic()
        while True:
            self.check_authority()
            samples = []
            for record in self.journal.state['ranks']:
                rank = record['rank']
                self.check_authority()
                item = self.inspect(rank, record)
                validate_container(item, self.plan, record)
                require(item['State'].get('Running') is True, 'baseline rank stopped during thermal admission')
                require_guard(self.guard_state(rank, record), self.plan, record, 'steady')
                sampled = time.time()
                command = ['nvidia-smi', '--query-gpu=uuid,temperature.gpu', '--format=csv,noheader,nounits']
                result = self.transport(rank, self.helper.bound_command(command))
                self.journal.event('baseline-thermal-observation', rank=rank,
                    container_id=record['container_id'], sampled_utc=sampled,
                    stdout=result.stdout, stderr=result.stderr, exit_code=result.returncode)
                lines = result.stdout.strip().splitlines()
                require(result.returncode == 0 and len(lines) == 1, 'single native GPU thermal observation required')
                fields = [v.strip() for v in lines[0].split(',')]
                require(len(fields) == 2 and re.fullmatch(r'GPU-[0-9a-fA-F-]+', fields[0]),
                        'thermal GPU identity/temperature output malformed')
                try:
                    temperature = float(fields[1])
                except ValueError:
                    raise RuntimeError('GPU thermal value unavailable')
                require(math.isfinite(temperature) and temperature >= 0, 'GPU thermal value invalid')
                samples.append(dict(rank=rank, container_id=record['container_id'],
                    sampled_utc=sampled, hottest_c=temperature))
            elapsed = time.monotonic() - started
            require(elapsed <= 600, 'original bounded thermal wait expired')
            if all(row['hottest_c'] < 55 and 0 <= time.time() - row['sampled_utc'] < 20 for row in samples):
                break
            self.sleep(min(2, max(0, 600 - elapsed)))
        self.check_authority()
        request = urllib.request.Request(self.plan['url'] + '/reset_prefix_cache', data=b'', method='POST')
        with urllib.request.urlopen(request, timeout=min(15, max(.1, self.end - time.time()))) as response:
            raw, status = response.read(1024 * 1024 + 1), response.status
        require(status == 200 and len(raw) <= 1024 * 1024, 'baseline prefix reset failed or oversized')
        reset_result = json_bytes(raw)
        require(isinstance(reset_result, dict) and reset_result.get('success') is True,
                'baseline prefix reset lacks positive native success')
        reset = {k: self.plan[k] for k in ('window_id', 'attempt_id', 'plan_sha256')}
        reset.update(url=self.plan['url'] + '/reset_prefix_cache', method='POST',
            completed_utc=time.time(), status=status, response_utf8=raw.decode('utf-8'), response_sha256=digest(raw))
        self.journal.event('baseline-prefix-reset', receipt=reset)
        context = {k: self.plan[k] for k in ('window_id', 'attempt_id', 'plan_sha256', 'url')}
        context.update(ranks=[{k: row[k] for k in ('rank', 'container_id', 'config_sha256')}
                             for row in self.journal.state['ranks']],
            ready_sha256=digest(read_owned_bytes(self.journal.directory / 'ready-observed.json')),
            admission=dict(thermal=dict(wait_seconds=elapsed, fan_policy='UNCHANGED', samples=samples), prefix_reset=reset))
        baseline_module().legacy_module().validate_runtime_context(context, self.plan)
        self.check_authority()
        return context

    def run_client(self):
        self.check_authority()
        # Host-side fixed Python client, NOT docker exec: supervisor owns the
        # actual HTTP-client process and every descendant rather than a CLI proxy.
        path = self.journal.directory / 'client-plan.json'
        if 'legacy_case' in self.plan['client']:
            context = self.legacy_runtime_context()
            sha = exclusive_json(self.journal.directory / 'baseline-runtime-context.json', context)
            self.journal.event('baseline-runtime-context-pinned', context_sha256=sha)
            exclusive_json(Path(self.plan['client']['runtime_context']), context)
        if 'performance_screen' in self.plan['client']:
            context = {k: self.plan[k] for k in ('window_id', 'attempt_id', 'plan_sha256', 'url')}
            context.update(ranks=[{k: r[k] for k in ('rank', 'container_id', 'config_sha256')}
                                  for r in self.journal.state['ranks']],
                           ready_sha256=digest(read_owned_bytes(self.journal.directory / 'ready-observed.json')))
            performance_module().validate_context(context, self.plan)
            exclusive_json(Path(self.plan['client']['runtime_context']), context)
        sha = exclusive_json(path, self.plan)
        script = str(Path(self.binding['checkout']) / 'scripts/q03_http_client.py')
        self.mutate(0, 'http_client', ['python3', '-I', '-B', script, '--plan', str(path), '--sha256', sha],
                    self.plan['client']['timeout_seconds'])

    def seal_epoch(self, epoch):
        rows = [r for r in self.journal.state['epochs'] if r['epoch'] == epoch]
        if len(rows) != 1 or rows[0]['action_kind'] != 'http_client':
            return super().seal_epoch(epoch)
        # A timed-out HTTP request can still be running in the service. This
        # proves ONLY the fixed client's descendants empty; service removal and
        # all-four GPU drain remain mandatory before any next boot/release.
        record = rows[0]
        path = self.journal.directory / 'client-plan.json'
        require(json_bytes(read_owned_bytes(path)) == self.plan, 'frozen HTTP client plan changed')
        script = str(Path(self.binding['checkout']) / 'scripts/q03_http_client.py')
        expected = self.helper.bound_command(['python3', '-I', '-B', script, '--plan', str(path),
                                              '--sha256', digest(canonical(self.plan))])
        require(record['rank'] == 0 and record['argv_sha256'] == digest(canonical(expected)),
                'client epoch is not the fixed admitted command')
        for rank in range(4):
            value = self.helper_call(rank, 'seal', epoch)
            require(value.get('sealed') is True, 'HTTP client epoch not sealed')
            while value.get('safe_to_overlap') is not True:
                pending = value.get('operations', [])
                require(rank == record['rank'] and len(pending) == 1
                        and pending[0]['operation'] == record['operation'], 'foreign HTTP client epoch')
                status = pending[0]
                if status.get('status') == 'running':
                    self.helper_call(rank, 'cancel', epoch, record['operation'])
                    self.sleep(.1)
                    value = self.helper_call(rank, 'seal', epoch)
                    continue
                require(status.get('status') == 'terminal' and status.get('group_quiescent') is True
                        and status.get('epoch_sealed') is True, 'HTTP client descendants UNKNOWN')
                break
            self.journal.event('http-client-epoch-sealed', rank=rank, epoch=epoch, receipt=value)
        sealed = self.journal.state.setdefault('sealed_epochs', [])
        if epoch not in sealed:
            sealed.append(epoch)
            self.journal.save()

    def collect_result(self):
        path = Path(self.plan['client']['output'])
        raw = read_owned_bytes(path)
        receipt = json_bytes(read_owned_bytes(path.with_name('client-receipt.json')))
        require(receipt.get('window_id') == self.plan['window_id']
                and receipt.get('attempt_id') == self.plan['attempt_id']
                and receipt.get('plan_sha256') == self.plan['plan_sha256']
                and type(receipt.get('exit_code')) is int and receipt['exit_code'] == 0
                and receipt.get('error') is None and receipt.get('output_sha256') == digest(raw),
                'HTTP client receipt failed or differs')
        value = json_bytes(raw)
        if 'legacy_case' in self.plan['client']:
            context_raw = read_owned_bytes(self.journal.directory / 'baseline-runtime-context.json')
            require(context_raw == read_owned_bytes(self.plan['client']['runtime_context']),
                    'baseline client context differs from controller-owned capture')
            context = json_bytes(context_raw)
            pins = [json_bytes(read_owned_bytes(path))
                    for path in sorted(self.journal.directory.glob('[0-9][0-9][0-9][0-9][0-9][0-9].json'))]
            pins = [event for event in pins if event.get('kind') == 'baseline-runtime-context-pinned']
            require(len(pins) == 1 and pins[0].get('context_sha256') == digest(context_raw)
                    and pins[0].get('window_id') == self.plan['window_id']
                    and pins[0].get('attempt_id') == self.plan['attempt_id'],
                    'baseline admission context differs from durable controller event')
            require(context['ranks'] == [{k: row[k] for k in ('rank', 'container_id', 'config_sha256')}
                                        for row in self.journal.state['ranks']]
                    and context['ready_sha256'] == digest(read_owned_bytes(self.journal.directory / 'ready-observed.json')),
                    'baseline context container/config/readiness differs from owned journal')
            result = baseline_module().legacy_module().validate_output(value, self.plan)
            return dict(result='passed', window_id=self.plan['window_id'], attempt_id=self.plan['attempt_id'],
                image=plan_image(self.plan), source_head=self.plan['source_head'], plan_sha256=self.plan['plan_sha256'],
                result_sha256=digest(raw), toolbox_head=self.plan['client']['toolbox_head'], **result)
        if 'performance_screen' in self.plan['client']:
            module = performance_module()
            module.verify_runtime_context(self.journal.directory.parent, self.plan)
            result = module.validate_output(value, self.plan)
            return dict(result='passed', window_id=self.plan['window_id'], attempt_id=self.plan['attempt_id'],
                        image=IMAGE, source_head=SOURCE_HEAD, plan_sha256=self.plan['plan_sha256'],
                        result_sha256=digest(raw), toolbox_head=self.plan['client']['toolbox_head'], **result)
        if 'qualification' in self.plan['client']:
            validate_output = qualification_module(self.binding['http']['schema_version']).validate_output
            qualification = validate_output(value, self.plan)
            for run in value['runs']:
                retained = read_owned_bytes(path.with_name(run['label'] + '.json'))
                require(digest(retained) == run['sha256'] and json_bytes(retained) == run['value'],
                        'retained lean generation changed')
            return dict(result='passed', window_id=self.plan['window_id'], attempt_id=self.plan['attempt_id'],
                        image=IMAGE, source_head=SOURCE_HEAD, plan_sha256=self.plan['plan_sha256'],
                        result_sha256=digest(raw), toolbox_head=self.plan['client']['toolbox_head'], **qualification)
        work, summary = value['workload'], value['summary']
        extra = {} if self.plan['selection']['steering'] == 'off' else {'vllm_xargs': {
            'steering_profile': 'heretic-1017-attn-v1',
            'steering_scale': 0 if self.plan['selection']['steering'] == 'loaded_zero' else 1}}
        prompts = self.plan['client']['prompts']
        expected_workload = {'mode': 'burst', 'concurrency': 1, 'rate': 1.0,
            'warmup_s': 0.0, 'duration_s': 1.0, 'batches': 1, 'max_tokens': 16,
            'fixed_length': True, 'seed': 0, 'max_inflight': 512, 'context_tokens': 0,
            'prompts': prompts['name'], 'prompt_count': prompts['count'],
            'prompts_sha256': prompts['sha256'], 'extra_body': extra}
        require(canonical(work) == canonical(expected_workload), 'HTTP frozen workload/steering identity differs')
        require(value['label'] == self.plan['attempt_id'] and value['server']['url'] == self.plan['url']
                and value['server']['model'] == 'deepseek-v4.1-flash'
                and work['mode'] == 'burst' and work['concurrency'] == 1 and work['batches'] == 1
                and work['max_tokens'] == 16 and work['seed'] == 0
                and all(type(summary['requests'].get(k)) is int and summary['requests'][k] == v
                        for k, v in (('failed', 0), ('foreign', 0), ('sent', 1), ('rejected', 0)))
                and len(value.get('requests', [])) == 1
                and value['requests'][0].get('error') is None
                and type(value['requests'][0].get('completion_tokens')) is int
                and value['requests'][0]['completion_tokens'] == 16,  'HTTP smoke output contract differs')
        return dict(result='passed', window_id=self.plan['window_id'], attempt_id=self.plan['attempt_id'],
                    image=IMAGE, source_head=SOURCE_HEAD, plan_sha256=self.plan['plan_sha256'],
                    result_sha256=digest(raw), toolbox_head=self.plan['client']['toolbox_head'],
                    qualification='bounded HTTP smoke only; no performance or numerical acceptance')


def quiesce_http(session, attempt_id, ops=None):
    """Same fenced daemon drain for normal completion, failure and watchdog."""
    path = session.directory / "attempts" / attempt_id / "http" / "plan.json"
    if not path.exists():
        require(not path.parent.exists(), "partial owner journal: UNKNOWN")
        return dict(attempt_id=attempt_id, node_count=4, quiescent=True, no_owner_intent=True)
    plan = json_bytes(read_owned_bytes(path))
    require(plan["attempt_id"] == attempt_id and plan["window_id"] == session.capsule["window_id"],
            "foreign owner journal")
    expected = dict(plan)
    wanted = expected.pop("plan_sha256")
    require(digest(canonical(expected)) == wanted, "owner retained plan digest differs")
    verify_plan_identity(session, plan)
    journal = Journal(session, plan)
    ops = HttpOps(session, plan, journal, recovering=True) if ops is None else ops
    ops.journal = journal
    try:
        from q03_rank_logs import quiesce_with_logs
        return quiesce_with_logs(journal, ops, validate_container)
    finally:
        journal.close()




def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('run', 'quiesce'))
    parser.add_argument('--session-dir', required=True)
    parser.add_argument('--attempt-id', required=True)
    args = parser.parse_args()
    from q03_window import Session, RuntimeOps
    session = Session(args.session_dir, RuntimeOps())
    require(re.fullmatch(r'[0-9a-f]{32}', args.attempt_id), 'canonical attempt UUID required')
    if args.action == 'quiesce':
        value = quiesce_http(session, args.attempt_id)
    else:
        path = session.directory / 'attempts' / args.attempt_id / 'http-plan.json'
        plan = json_bytes(read_owned_bytes(path))
        verify_plan_identity(session, plan)
        value = run_http(session, plan)
    print(json.dumps(value, sort_keys=True))


if __name__ == '__main__':
    main()
