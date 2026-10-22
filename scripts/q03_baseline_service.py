#!/usr/bin/env python3
"""Finite schema-6 baseline bridge to the existing owned HTTP lifecycle.

This is a selected-case pilot with an optional fixed-state C1/C4 batch, not a
second controller, owner qualification, full 18-state matrix, or replay of the
historical same-boot scale sequence.
Planning is inert. Existing journals, epochs, watchdog, guards, logs and positive
four-rank drain remain owned by q03_http_service / q03_owner_model.
"""
from __future__ import annotations
import copy
import os
from pathlib import Path
import re
import types
import sys

import q03_owner_model as owner
from q03_held_window import read_owned_bytes

require, canonical, digest, json_bytes = owner.require, owner.canonical, owner.digest, owner.json_bytes
PREPARE = owner.PREPARE
PROFILE_SHA256 = 'e935b492d1f76eeae09fe5a57b094c3bfbf8e63e83b37fe1be0e42fa46170a36'
# Independently frozen expected configuration, not a first-worker observation.
# The scale is a request scalar, so loaded0 through loaded4 have identical config.
EFFECTIVE_SHA256 = 'a144ea7b1dbb12c50ca4cf0862d1ec9e83f34d87a2938ea441f8c57f181914c8'
IDENTITY_SOURCE_SHA256 = 'cafd0342e551b55a076ad49fb7cb3bc6d0325dcbdf9d8eec2101d796796d764f'
LEGACY_SOURCE_SHA256 = '75f7acc434897196b90276baec1540d63cd1a43544882baaec6750f0e059ced5'
BATCH_SOURCE_SHA256 = 'c71e9b214ec541c7a2fd8c26f010f14730577c0764ad179f53726b3249f64eb6'
SOURCE_FILES = tuple(dict.fromkeys((*owner.SOURCE_FILES, 'q03_held_window.py',
    'q03_http_service.py', 'q03_http_client.py', 'q03_http_toolbox.json',
    'q03_baseline_service.py', 'q03_baseline_identity.py', 'q03_baseline_worker.py',
    'q03_legacy_case_replay.py', 'q03_baseline_batch.py', 'q03_quality_pilot.py', 'q03_quality_capture.py', 'q03_qualification.py', 'q03_git_toolchain.py')))
CANDIDATE_FIELDS = {'image', 'component_head', 'runtime_manifest_path',
    'runtime_manifest_sha256', 'pytest_base_pythonpath', 'cache_path', 'output_path'}


def _module(name, expected):
    path = Path(__file__).with_name(name + '.py')
    raw = read_owned_bytes(path)
    require(digest(raw) == expected, 'baseline dependency source pin differs: ' + name)
    module = types.ModuleType('_source_' + name)
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def identity_module():
    return _module('q03_baseline_identity', IDENTITY_SOURCE_SHA256)


def legacy_module():
    return _module('q03_legacy_case_replay', LEGACY_SOURCE_SHA256)


QUALITY_SOURCE_SHA256 = 'e48f4a50d0cb90aa104cdc653f995368c3f0c2b1b873abea1ed4f96b0c511d14'
CAPTURE_SOURCE_SHA256 = '6aceb09a34cacf59595b8e7f3148e49a7c2051292291d4eba9f23c2dfce3a38e'


def quality_pilot_module():
    # Both dependencies are exact source bytes, never cached .pyc or sys.path.
    dependencies = {'q03_quality_capture': _module('q03_quality_capture', CAPTURE_SOURCE_SHA256),
                    'q03_legacy_case_replay': legacy_module()}
    previous = {name: sys.modules.get(name) for name in dependencies}
    sys.modules.update(dependencies)
    try:
        return _module('q03_quality_pilot', QUALITY_SOURCE_SHA256)
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def workload(value):
    if value.get('schema_version') == 7:
        return 'quality_pilot', quality_pilot_module()
    return 'legacy_case', legacy_module()


def batch_module():
    return _module('q03_baseline_batch', BATCH_SOURCE_SHA256)


def validate_jobs(manifest):
    jobs = manifest.get('jobs')
    require(isinstance(jobs, list) and len(jobs) == 1, 'baseline attempt has one bounded HTTP case')
    job = jobs[0]
    require(isinstance(job, dict) and set(job) == {'kind', 'timeout_seconds', 'selection'}
        and job['kind'] == 'http_service' and type(job['timeout_seconds']) is int
        and 1 <= job['timeout_seconds'] <= 7200, 'invalid bounded baseline HTTP job')
    identity_module().validate_selection(job['selection'])
    require(type(manifest.get('window_seconds')) is int and manifest['window_seconds'] == 10800,
        'baseline outer lease must remain 10800 seconds')
    require(manifest['preflight'].get('original_service_disposition') == 'keep-stopped',
        'baseline forbids original START')
    require('image_preparation' not in manifest, 'baseline image preparation is not supported')


def next_selection(session):
    validate_jobs(session.manifest)
    if session.manifest.get('http_service', {}).get('schema_version') == 7:
        require(not session.state.get('attempt_results'), 'quality primary attempt already retained; no retry in capsule')
    require(not any(r.get('outcome') == 'passed' and r.get('passed_gates') == ['http_service']
        for r in session.state.get('attempt_results', [])), 'selected baseline case is complete')
    return copy.deepcopy(session.manifest['jobs'][0]['selection'])


def _toolbox(value, ops):
    inventory = json_bytes(read_owned_bytes(Path(__file__).with_name('q03_http_toolbox.json')))
    require(isinstance(value, dict) and set(value) == {'path', 'head', 'tree', 'git_config_sha256'}
        and value['head'] == inventory['head'] and value['tree'] == inventory['tree'],
        'baseline toolbox must be exact reviewed PR8 checkout')
    from q03_git_toolchain import validate_git_metadata
    validate_git_metadata(value['path'], value['git_config_sha256'])
    git = ['git', '--no-replace-objects', '-c', 'core.fsmonitor=false',
           '-c', 'core.hooksPath=/dev/null', '-C', value['path']]
    require(Path(value['path'], '.git').is_dir()
        and ops.run([*git, 'rev-parse', 'HEAD']).stdout.strip() == value['head']
        and ops.run([*git, 'rev-parse', 'HEAD^{tree}']).stdout.strip() == value['tree']
        and not ops.run([*git, 'status', '--porcelain']).stdout.strip(), 'baseline toolbox checkout changed')
    for name, sha in inventory['files'].items():
        require(digest(read_owned_bytes(Path(value['path']) / name)) == sha, 'baseline toolbox source differs')
    return inventory


def validate_binding(manifest, ops, *, remote=True):
    validate_jobs(manifest)
    value = manifest.get('http_service')
    fields = {'schema_version', 'baseline_inputs', 'baseline_identity', 'toolbox',
              'api_port', 'startup_seconds', 'client_seconds'}
    work_key, module = workload(value) if isinstance(value, dict) else ('legacy_case', None)
    fields.add(work_key)
    if isinstance(value, dict) and 'case_sequence' in value:
        fields.add('case_sequence')
        batch_module().validate_sequence(value)
    require(isinstance(value, dict) and set(value) == fields
        and type(value['schema_version']) is int and value['schema_version'] in (6, 7),
        'baseline HTTP binding fields differ')
    require(isinstance(manifest.get('candidate'), dict)
        and set(manifest['candidate']) == CANDIDATE_FIELDS, 'baseline candidate fields differ')
    selection = manifest['jobs'][0]['selection']
    require(value['baseline_identity']['arm'] == selection['arm'], 'baseline capsule arm differs')
    fixed = value['baseline_inputs']
    require(isinstance(fixed, dict) and set(fixed) == {'schema_version', 'profile', 'nodes',
        'effective_config', 'master_port', 'handoff_seconds'} and type(fixed['schema_version']) is int
        and fixed['schema_version'] == 1, 'baseline input fields differ')
    for source, key, low, high in ((fixed, 'master_port', 1024, 65535),
        (fixed, 'handoff_seconds', 1, 600), (value, 'api_port', 1024, 65535),
        (value, 'startup_seconds', 1, 3600), (value, 'client_seconds', 106, 600)):
        require(type(source[key]) is int and low <= source[key] <= high, 'invalid baseline bound: ' + key)
    require(value['api_port'] != fixed['master_port'], 'baseline HTTP/rendezvous ports collide')
    revised = selection['arm'] in ('r6x-unpatched', 'r6x-steer')
    frozen = identity_module().validate_revised_freeze() if revised else None
    profile_sha = frozen['profile_sha256'][selection['arm']] if revised else PROFILE_SHA256
    require(fixed['profile']['sha256'] == profile_sha, 'baseline normal profile pin differs')
    profile = owner.pinned_json(fixed['profile'])
    require(profile['shape'] == 'tp4-ring' and profile['fabric']['transport'] == 'oneshot-ring4',
        'baseline requires reviewed TP4 ring profile')
    if revised:
        require(digest(canonical(common_recipe(profile))) == frozen['common_recipe_sha256'],
                'revised pair common serving recipe differs')
        if selection['arm'] == 'r6x-unpatched':
            require('--additional-config' not in profile['serve_args']
                    and not any(d == '/steering' for _, d, _ in profile['container']['mounts']),
                    'unpatched profile cannot require steering configuration or assets')
    require(fixed['effective_config']['sha256'] == (frozen['effective_sha256'] if revised else EFFECTIVE_SHA256),
        'baseline expected effective config must be independently frozen')
    effective = owner.pinned_json(fixed['effective_config'])
    require(set(effective) == {'off', 'loaded_zero', 'active'}
        and all(set(v) == owner.EFFECTIVE_KEYS for v in effective.values())
        and canonical(effective['loaded_zero']) == canonical(effective['active']),
        'baseline fixed effective config projection differs')
    nodes = owner.pinned_json(fixed['nodes'])
    ranks = nodes.get('nodes')
    require(isinstance(ranks, list) and len(ranks) == 4
        and all(type(n.get('rank')) is int for n in ranks)
        and {n['rank'] for n in ranks} == set(range(4))
        and [n['rank'] for n in ranks if n.get('head') is True] == [0],
        'baseline needs logical head0 and followers1/2/3')
    spark = owner.load_spark()
    problems = spark.topology.problems(profile, nodes)
    require(not problems, 'invalid baseline topology: ' + '; '.join(problems))
    from q03_git_toolchain import validate_git_metadata
    checkout = Path(manifest['preflight']['independent_checkout'])
    config_sha = digest(read_owned_bytes(checkout / '.git/config'))
    validate_git_metadata(checkout, config_sha)
    checked = identity_module().validate_host(value['baseline_identity'], manifest['candidate'], ops)
    binding = copy.deepcopy(fixed)
    binding.update(candidate=copy.deepcopy(manifest['candidate']), independent_root=manifest['independent_root'],
        profile_value=profile, nodes_value=nodes, effective_value=effective,
        checkout=manifest['preflight']['independent_checkout'], preflight=copy.deepcopy(manifest['preflight']),
        http=copy.deepcopy(value), baseline_identity=checked, selection=copy.deepcopy(selection),
        toolbox_inventory=_toolbox(value['toolbox'], ops), deployment_git_config_sha256=config_sha)
    owner.remote_nodes(binding)  # Reuse exact physical inventory/endpoint mapping.
    corpus = module.validate_binding(value[work_key], binding)
    if revised:
        require(digest(canonical(corpus['tokenizer_identity'])) == frozen['model_tokenizer_sha256'],
                'revised pair model/tokenizer identity differs')
        sources = identity_module().source_hashes(selection['arm'])
        native = {name: sources['tokenizers/' + name] for name in
                  ('deepseek_v41.py', 'deepseek_v41_encoding.py')}
        require(corpus['tokenizer_identity']['encoding_source_sha256'] == native['deepseek_v41_encoding.py']
                and corpus['tokenizer_identity']['chat_template_sha256'] == digest(canonical(native)),
                'revised corpus tokenizer proof is not from this actual native source')
    model_roots = [s.format(home=profile['host']['home']) for s, d, mode in profile['container']['mounts']
        if d == '/models' and mode == 'ro']
    require(model_roots == [corpus['artifacts']['model_root']],
        'baseline corpus differs from the exact registered model mount')
    if remote:
        validate_remote_inputs(manifest, binding, ops)
    return binding


def validate_host(manifest, ops, *, prepare_images=False):
    require(not prepare_images, 'baseline image preparation is not supported')
    checkout = Path(manifest['preflight']['independent_checkout'])
    require(Path(__file__).resolve() == checkout / 'scripts/q03_baseline_service.py',
        'baseline controller is not from the pinned independent checkout')
    root = Path(manifest['independent_root'])
    require(root.is_absolute() and root.resolve() == root and root.is_dir(),
        'baseline independent root must already exist without aliases')
    for parent in (root, *root.parents):
        stat = parent.stat()
        require(stat.st_uid in (0, os.getuid()) and not stat.st_mode & 0o022,
            'baseline independent root ancestry is not owner-controlled')
    for key in ('cache_path', 'output_path'):
        require(Path(manifest['candidate'][key]).is_dir(), 'baseline candidate directory missing: ' + key)
    return validate_binding(manifest, ops)


def _immutable(manifest):
    value = manifest['http_service']
    key, module = workload(value)
    corpus = module.validate_binding(value[key])
    provenance = _provenance_files(corpus, module)
    return [*[Path(p['path']) for p in provenance], Path(corpus['artifacts']['model_root']),
        Path(manifest['preflight']['independent_checkout']), Path(value['toolbox']['path']),
        Path(value['baseline_identity']['provenance']['path']), Path(value[key]['corpus']['path']),
        *[Path(value['baseline_inputs'][k]['path']) for k in ('profile', 'nodes', 'effective_config')]]


def validate_paths(manifest, directory):
    from q03_original_spark_adapter import evidence_path
    immutable = _immutable(manifest)
    for path in immutable:
        require(path.is_absolute() and path.resolve() == path and '..' not in path.parts,
            'baseline immutable input aliases another path')
    writable = [evidence_path(str(Path(manifest['independent_root']) / 'http-resources'), manifest),
        evidence_path(directory, manifest)]
    writable += [evidence_path(manifest['candidate'][k], manifest) for k in ('cache_path', 'output_path')]
    for target in writable:
        require(not any(target == p or target in p.parents or p in target.parents for p in immutable),
            'baseline writable path overlaps immutable input')
    require(not any(a == b or a in b.parents or b in a.parents
        for i, a in enumerate(writable) for b in writable[i + 1:]), 'baseline writable roots overlap')


def _provenance_files(corpus, module=None):
    legacy = module or legacy_module()
    mounted = {p['path'] for p in legacy.mounted_asset_files(corpus)}
    return [p for p in legacy.artifact_files(corpus) if p['path'] not in mounted]


def input_contract(binding, *, include_toolbox=True):
    value = identity_module().input_contract(binding['http']['baseline_identity'])
    value['files'] += [binding[k] for k in ('profile', 'nodes', 'effective_config')]
    for name in SOURCE_FILES:
        path = (Path(binding['checkout']) / 'scripts' / name).resolve()
        value['files'].append({'path': str(path), 'sha256': digest(read_owned_bytes(path))})
    profile = binding['profile_value']
    value['directories'] = [s.format(home=profile['host']['home'])
        for s, _, m in profile['container']['mounts'] if m == 'ro']
    checked = binding['baseline_identity']
    value['identity_source'] = {'path': str(Path(binding['checkout']) / 'scripts/q03_baseline_identity.py'), 'sha256': IDENTITY_SOURCE_SHA256}
    value['image_observation'] = copy.deepcopy(checked['image_observation'])
    value['image'] = checked['identity']['image']
    value['image_labels'] = checked['image_observation']['image_labels']
    value['pythonpath'] = binding['candidate']['pytest_base_pythonpath']
    publication = binding['preflight']['publication']
    value['deployment'] = dict(path=binding['checkout'], head=publication['sha'], repository=publication['url'])
    value['files'].append({'path': str(Path(binding['checkout']) / '.git/config'),
        'sha256': binding['deployment_git_config_sha256']})
    if include_toolbox:
        toolbox = binding['http']['toolbox']
        key, module = workload(binding['http'])
        value['files'].append(binding['http'][key]['corpus'])
        corpus = module.validate_binding(binding['http'][key], binding)
        value['files'] += _provenance_files(corpus, module)
        value['files'] += [{'path': str(Path(toolbox['path']) / name), 'sha256': sha}
            for name, sha in binding['toolbox_inventory']['files'].items()]
        value['files'].append({'path': str(Path(toolbox['path']) / '.git/config'),
            'sha256': toolbox['git_config_sha256']})
        value['directories'].append(toolbox['path'])
    return value


def boundary_contract(binding, node, resource_path):
    pre = binding['preflight']
    protected = pre['protected_roots'] + [pre[k] for k in (
        'hold_path', 'request_path', 'legacy_hold_path', 'legacy_request_path', 'policy_path')]
    protected.append(pre['registered_policy']['repository'])
    inputs = input_contract(binding, include_toolbox=node['rank'] == 1)
    site = [s.format(home=binding['profile_value']['host']['home'])
        for s, d, m in binding['profile_value']['container']['mounts'] if m == 'ro' and d in ('/models', '/steering')]
    immutable = [binding['checkout'], binding['http']['toolbox']['path']]
    immutable += [p['path'] for p in inputs['files']]
    return dict(independent_root=binding['independent_root'], resource_path=resource_path,
        input_files=inputs['files'], input_directories=inputs['directories'], site_readonly_roots=site,
        strict_input_directories=[p for p in inputs['directories'] if p not in site],
        hostname=node['expected_hostname'], authority_path=pre['remote_authority_path'],
        authority_sha256=pre['remote_authority_sha256'], authority_protected_roots=pre['protected_roots'],
        protected_roots=protected, immutable_paths=immutable)


def validate_remote_inputs(manifest, binding, ops):
    from q03_window_preflight import _node_argv
    from q03_remote_operation import bound_command
    identity = identity_module()
    key, module = workload(binding['http'])
    corpus = module.validate_binding(binding['http'][key], binding)
    assets = module.mounted_asset_files(corpus)
    # Reuse only the generic mounted-data byte probe, never its197 corpus gate.
    from q03_http_service import qualification_module
    asset_probe = qualification_module(2).ASSET_PROBE
    for node in owner.remote_nodes(binding):
        boundary = boundary_contract(binding, node, str(Path(binding['independent_root']) / 'http-resources'))
        for program, contract, label in ((owner.REMOTE_BOUNDARY, boundary, 'boundary'),
            (identity.INPUT_PROBE, input_contract(binding, include_toolbox=node['rank'] == 1), 'identity')):
            result = ops.run(_node_argv(manifest['preflight'], node, bound_command(
                ['python3', '-I', '-B', '-c', program, canonical(contract).decode()])), timeout=90)
            require(result.returncode == 0, 'baseline all-four ' + label + ' validation failed')
            report = json_bytes(result.stdout)
            if label == 'boundary':
                require(report.get('paths_safe') is True, 'baseline all-four boundary not proven')
            else:
                require(report.get('identity') == binding['baseline_identity']['identity']
                    and report.get('deployment_head') == manifest['preflight']['publication']['sha'],
                    'baseline all-four image/deployment identity differs')
        result = ops.run(_node_argv(manifest['preflight'], node, bound_command(
            ['python3', '-I', '-B', '-c', asset_probe, canonical(assets).decode()])), timeout=90)
        require(result.returncode == 0 and json_bytes(result.stdout) == {'verified_files': len(assets)},
            'baseline all-four mounted model/tokenizer bytes differ')


def common_recipe(profile):
    """Only image/source labels and the declared steer delta may differ."""
    value = copy.deepcopy(profile)
    # Source/build references are arm identity, not a serving-recipe difference.
    # Each entire profile is separately frozen; never relabel U as the S baseline.
    value.pop('upstreams_config', None)
    value.pop('promoted_baseline', None)
    container = value['container']
    container.pop('image', None)
    container.pop('expected_labels', None)
    container['mounts'] = [m for m in container['mounts'] if m[1] != '/steering']
    args = value['serve_args']
    if '--additional-config' in args:
        require(args.count('--additional-config') == 1, 'duplicated steering profile flag')
        index = args.index('--additional-config')
        require(index + 1 < len(args) and json_bytes(args[index + 1]) ==
                owner.FIXED_ENGINE_KWARGS['additional_config'], 'non-steering recipe delta')
        del args[index:index + 2]
    return value


def _replace(args, flag, value):
    require(args.count(flag) == 1 and args.index(flag) + 1 < len(args), 'baseline normal flag differs: ' + flag)
    args[args.index(flag) + 1] = str(value)


def http_plan(binding, window_id, attempt_id, selection, *, session=None):
    identity = identity_module()
    identity.validate_selection(selection)
    require(selection == binding['selection'], 'baseline capsule selection is immutable')
    for value in (window_id, attempt_id):
        require(type(value) is str and re.fullmatch(r'[0-9a-f]{32}', value), 'canonical baseline UUID required')
    checked = identity.validate_binding(binding['http']['baseline_identity'], binding['candidate'])
    require(checked == {k: binding['baseline_identity'][k] for k in checked}, 'baseline identity changed before planning')
    profile = copy.deepcopy(binding['profile_value'])
    args = profile['serve_args']
    require(args[0] == '/models', 'baseline uses exact local checkpoint')
    for flag, value in (('--max-model-len', 1048576), ('--max-num-seqs', 16),
        ('--max-num-batched-tokens', 8192), ('--kv-cache-memory-bytes', 11274289152),
        ('--tensor-parallel-size', 4), ('--nnodes', 4)):
        require(args.count(flag) == 1 and args[args.index(flag) + 1] == str(value),
            'baseline normal profile changes fixed capacity/topology: ' + flag)
    _replace(args, '--host', '127.0.0.1')
    _replace(args, '--port', binding['http']['api_port'])
    if selection['arm'] == 'r6x-unpatched':
        require(selection['steering'] == 'absent' and '--additional-config' not in args
                and not any(d == '/steering' for _, d, _ in profile['container']['mounts']),
                'unpatched profile contains steering configuration or assets')
    else:
        require(args.count('--additional-config') == 1, 'baseline normal steering profile missing/duplicated')
        index = args.index('--additional-config')
        additional = json_bytes(args[index + 1])
        require(additional == owner.FIXED_ENGINE_KWARGS['additional_config'], 'baseline steering configuration differs')
        if selection['steering'] == 'unloaded':
            del args[index:index + 2]
    require('--worker-extension-cls' not in args, 'unreviewed baseline worker extension')
    args += ['--worker-extension-cls', 'q03_baseline_worker.HttpObservation']
    profile['distributed']['master_port'] = str(binding['master_port'])
    profile['container']['image'] = image = checked['identity']['image']
    profile['container']['expected_labels'] = copy.deepcopy(binding['baseline_identity']['image_observation']['image_labels'])
    expected = binding['effective_value']['off' if selection['steering'] in ('absent', 'unloaded') else 'loaded_zero']
    spec = dict(baseline_identity=checked, expected_effective_config=copy.deepcopy(expected),
        selection=copy.deepcopy(selection), handshake=dict(window_id=window_id, attempt_id=attempt_id,
            timeout_seconds=binding['handoff_seconds']))
    spec_sha = digest(canonical(spec))
    spark, ranks = owner.load_spark(), []
    for node in sorted(binding['nodes_value']['nodes'], key=lambda n: n['rank']):
        rank = node['rank']
        name = f'q03-http-{window_id}-{attempt_id}-{rank}'
        root = str(Path(binding['independent_root']) / 'http-resources' / window_id / attempt_id / str(rank))
        env = spark.expected_environment(profile, node)
        require(env.pop('PYTHONPATH') == binding['candidate']['pytest_base_pythonpath'],
            'baseline normal profile changes baked Python path')
        env.update(VLLM_SERVER_DEV_MODE='1', PYTHONDONTWRITEBYTECODE='1', VLLM_ALLOW_INSECURE_SERIALIZATION='0',
            PYTHONPYCACHEPREFIX='/cache/baseline-bytecode',
            VLLM_DS41_Q03_MHC_MXFP8='0', Q03_HTTP_WINDOW=window_id, Q03_HTTP_ATTEMPT=attempt_id,
            Q03_HTTP_SPEC_SHA256=spec_sha,
            Q03_HTTP_WORKER_SHA256=digest(read_owned_bytes(Path(binding['checkout']) / 'scripts/q03_baseline_worker.py')),
            Q03_BASELINE_IDENTITY_SHA256=IDENTITY_SOURCE_SHA256)
        if selection['arm'] in ('r6x-unpatched', 'r6x-steer'):
            require('VLLM_DS41_Q03_MHC_MXFP8' not in profile['environment'],
                    'revised pair profile contains a Q03 runtime switch')
            del env['VLLM_DS41_Q03_MHC_MXFP8']
        mounts = [[s.format(home=profile['host']['home']), d, m]
            for s, d, m in profile['container']['mounts'] if d != '/cache']
        require(all(m == 'ro' for _, _, m in mounts), 'unapproved baseline profile writable mount')
        mounts += [[binding['candidate']['runtime_manifest_path'], '/runtime/runtime-manifest.json', 'ro'],
            [root + '/spec.json', '/runtime/http-spec.json', 'ro'],
            [str(Path(binding['checkout']) / 'scripts/q03_baseline_worker.py'), '/q03-http/q03_baseline_worker.py', 'ro'],
            [str(Path(binding['checkout']) / 'scripts/q03_baseline_identity.py'), '/q03-http/q03_baseline_identity.py', 'ro'],
            [root + '/cache', '/cache', 'rw'], [root + '/results', '/results', 'rw']]
        python = ['-B', '/q03-http/q03_baseline_worker.py', 'serve', *spark.expected_command(profile, node)]
        per_rank = copy.deepcopy(profile)
        per_rank['container'].update(name=name, mounts=mounts)
        normal = spark.rendered_docker_command(per_rank, node)
        require(normal[:5] == ['docker', 'run', '-d', '--name', name], 'baseline normal renderer changed')
        options, clean, index = normal[3:normal.index(image)], [], 0
        while index < len(options):
            if options[index] == '--env':
                index += 2
            else:
                clean.append(options[index]); index += 1
        create = ['docker', 'create', *clean, '--workdir', '/validation']
        for key, val in sorted(env.items()):
            create += ['--env', key + '=' + str(val)]
        for key, val in {'q03.window': window_id, 'q03.attempt': attempt_id, 'q03.job': 'http_service', 'q03.rank': str(rank)}.items():
            create += ['--label', key + '=' + val]
        ranks.append(dict(rank=rank, node=node, name=name, root=root, environment=env,
            mounts=mounts, python_argv=python, create_prefix=create))
    plan = dict(schema_version=binding['http']['schema_version'], kind='http_service', window_id=window_id, attempt_id=attempt_id,
        selection=copy.deepcopy(selection), image=image, source_head=checked['identity']['source_head'],
        runtime_sha256=checked['provenance']['sha256'], spec=spec, spec_sha256=spec_sha,
        ranks=ranks, docker_args=profile['container']['docker_run_args'], binding=copy.deepcopy(binding),
        url=f"http://127.0.0.1:{binding['http']['api_port']}")
    key, module = workload(binding['http'])
    quality = key == 'quality_pilot'
    plan['client'] = dict(toolbox_path=binding['http']['toolbox']['path'],
        toolbox_head=binding['toolbox_inventory']['head'], toolbox_files=binding['toolbox_inventory']['files'],
        prompts=binding['toolbox_inventory']['prompts'], timeout_seconds=binding['http']['client_seconds'],
        output=ranks[0]['root'] + ('/results/quality-pr8.json' if quality else '/results/legacy-case.json'),
        runtime_context=ranks[0]['root'] + '/results/runtime-context.json')
    plan['client'][key] = module.client_plan(binding, selection)
    names = module.SOURCES if quality else (
        'q03_legacy_case_replay', 'q03_baseline_identity', 'q03_owner_model', 'q03_held_window')
    plan['client']['quality_sources' if quality else 'legacy_sources'] = {
        name: digest(read_owned_bytes(Path(binding['checkout']) / 'scripts' / (name + '.py'))) for name in names}
    if quality:
        plan['lane_id'] = selection['arm'] + '/' + selection['steering']
    plan['plan_sha256'] = digest(canonical(plan))
    return plan


def validate_ready(raw, plan):
    return identity_module().validate_ready(raw, plan)
