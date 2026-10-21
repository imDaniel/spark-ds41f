"""Independent Q03 owner-adapter CPU tests, never site qualification.

Load the actual selected module via Q03_OWNER_MODEL_ROOT. Only external inputs
and command boundaries are faked; no native/model/GPU imports or processes run.
The prior fixture-ready helper suite is deliberately not repeated here.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
OWNER_ROOT = Path(os.environ.get('Q03_OWNER_MODEL_ROOT', ROOT))
sys.path.insert(0, str(OWNER_ROOT / 'scripts'))


def load_owner():
    spec = importlib.util.spec_from_file_location(
        'q03_owner_model_actual_test', OWNER_ROOT / 'scripts/q03_owner_model.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NoExternalOperations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.owner = load_owner()

    def setUp(self):
        for name in ('Popen', 'run', 'check_call', 'check_output'):
            guard = mock.patch.object(subprocess, name,
                side_effect=AssertionError('real process forbidden in CPU fault test'))
            guard.start()
            self.addCleanup(guard.stop)
        guard = mock.patch.object(socket, 'create_connection',
            side_effect=AssertionError('network forbidden in CPU fault test'))
        guard.start()
        self.addCleanup(guard.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name).resolve()


class OwnerAdmissionTests(NoExternalOperations):
    def valid_manifest(self):
        return {'window_seconds': 10800,
                'jobs': [{'kind': 'owner_model', 'timeout_seconds': 120,
                          'selection': {'mode': 'off', 'steering': 'off'}}]}

    def test_closed_job_selection_rejects_argv_unknown_and_ambiguous_values(self):
        valid = self.valid_manifest()
        self.owner.validate_jobs(valid)
        variants = []
        for mode in ('external_launcher', None, True, 'OFF', ''):
            item = copy.deepcopy(valid)
            item['jobs'][0]['selection']['mode'] = mode
            variants.append(item)
        for steering in ('default', None, True, 'active_zero'):
            item = copy.deepcopy(valid)
            item['jobs'][0]['selection']['steering'] = steering
            variants.append(item)
        for timeout in (0, 7201, True, 1.5, '120'):
            item = copy.deepcopy(valid)
            item['jobs'][0]['timeout_seconds'] = timeout
            variants.append(item)
        item = copy.deepcopy(valid)
        item['jobs'][0]['argv'] = ['unreviewed']
        variants.append(item)
        item = copy.deepcopy(valid)
        item['jobs'].append(copy.deepcopy(item['jobs'][0]))
        variants.append(item)
        for value in variants:
            with self.subTest(value=value), self.assertRaises((RuntimeError, ValueError)):
                self.owner.validate_jobs(value)

    def test_outer_lease_is_strict_integer_10800_not_numeric_equivalence(self):
        valid = self.valid_manifest()
        self.owner.validate_jobs(valid)
        for value in (10800.0, True, False, '10800', None, 10799, 10801):
            altered = copy.deepcopy(valid)
            altered['window_seconds'] = value
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, 'lease'):
                self.owner.validate_jobs(altered)

    def test_prerequisite_chain_requires_actual_three_gates_and_all_four_drain(self):
        candidate = {'image': self.owner.IMAGE, 'component_head': self.owner.SOURCE_HEAD,
                     'runtime_manifest_sha256': self.owner.RUNTIME_SHA}
        capsule = {'window_id': 'a' * 32, 'manifest': {'candidate': candidate}}
        descriptor = {'attempt_id': 'b' * 32, 'window_id': 'a' * 32,
                      'capsule_sha256': 'c' * 64,
                      'jobs': [{'kind': k} for k in ('p0', 'g1', 'g2')]}
        terminal = {'outcome': 'passed', 'passed_gates': ['p0', 'g1', 'g2'],
                    'quiescence': {'quiescent': True, 'node_count': 4},
                    'descriptor_sha256': self.owner.digest(self.owner.canonical(descriptor)),
                    'attempt_id': 'b' * 32, 'window_id': 'a' * 32}
        values = dict(terminal=terminal, descriptor=descriptor, capsule=capsule)
        binding = {'prerequisites': {k: {'path': k, 'sha256': 'c' * 64}
                                    for k in values}}
        with mock.patch.object(self.owner, 'pinned_json', side_effect=lambda b: values[b['path']]):
            self.owner.validate_prerequisites(binding, candidate)
            for key, value in [('outcome', 'failed'), ('passed_gates', ['p0', 'g1']),
                               ('quiescence', {'quiescent': True, 'node_count': 3}),
                               ('quiescence', {'quiescent': 'true', 'node_count': 4}),
                               ('quiescence', {'quiescent': True, 'node_count': True}),
                               ('attempt_id', 'd' * 32), ('window_id', 'e' * 32),
                               ('descriptor_sha256', 'f' * 64)]:
                original = terminal[key]
                terminal[key] = value
                with self.subTest(key=key, value=value), self.assertRaises(RuntimeError):
                    self.owner.validate_prerequisites(binding, candidate)
                terminal[key] = original
            for key in candidate:
                altered = dict(candidate, **{key: 'foreign'})
                with self.subTest(candidate_key=key), self.assertRaises(RuntimeError):
                    self.owner.validate_prerequisites(binding, altered)


class OwnerBindingTests(NoExternalOperations):
    def setUp(self):
        super().setUp()
        owner = self.owner
        test_root = self.directory / 'test-checkout'
        (test_root / '.git').mkdir(parents=True)
        fixed = {"model_config.max_model_len": 1048576, "parallel_config.tensor_parallel_size": 4,
                 "parallel_config.pipeline_parallel_size": 1, "parallel_config.data_parallel_size": 1,
                 "parallel_config.nnodes": 4, "parallel_config.world_size": 4,
                 "parallel_config.local_world_size": 1, "parallel_config.distributed_executor_backend": "mp",
                 "scheduler_config.max_num_seqs": 16, "scheduler_config.max_num_batched_tokens": 8192,
                 "cache_config.kv_cache_memory_bytes": 11274289152, "cache_config.block_size": 256,
                 "speculative_config.method": "dspark", "speculative_config.num_speculative_tokens": 5,
                 "compilation_config.max_cudagraph_capture_size": 96,
                 "compilation_config.pass_config.enable_sp": False}
        expected = dict.fromkeys(owner.EFFECTIVE_KEYS, None)
        expected.update(fixed)
        self.documents = {
            'profile': {'shape': 'tp4-ring', 'fabric': {'transport': 'oneshot-ring4'}},
            'nodes': {'nodes': [{'rank': r, 'name': f'node{r}', 'head': r == 0} for r in range(4)]},
            'effective': {state: copy.deepcopy(expected) for state in owner.STEERING}}
        runtime_raw = owner.canonical({'source_head': owner.SOURCE_HEAD, 'source_tree': owner.SOURCE_TREE})
        guard = mock.patch.object(owner, 'RUNTIME_SHA', owner.digest(runtime_raw))
        guard.start()
        self.addCleanup(guard.stop)
        guard = mock.patch.object(owner, 'read_owned_bytes', return_value=runtime_raw)
        guard.start()
        self.addCleanup(guard.stop)
        candidate = {'image': owner.IMAGE, 'component_head': owner.SOURCE_HEAD,
                     'runtime_manifest_sha256': owner.RUNTIME_SHA,
                     'runtime_manifest_path': str(self.directory / 'runtime.json')}
        self.manifest = {'window_seconds': 10800,
            'jobs': [{'kind': 'owner_model', 'timeout_seconds': 120,
                      'selection': {'mode': 'off', 'steering': 'off'}}],
            'candidate': candidate, 'independent_root': str(self.directory),
            'preflight': {'independent_checkout': str(self.directory / 'controller')},
            'owner_model': {'schema_version': 1, 'master_port': 29633, 'handoff_seconds': 30,
                           'tests': {'path': str(test_root), 'head': 'a' * 40,
                                     'tree': owner.TEST_TREE, 'fixture_sha256': owner.TEST_SHA,
                                     'git_config_sha256': 'b' * 64},
                           'profile': {'path': 'profile', 'sha256': owner.PROFILE_SHA},
                           'nodes': {'path': 'nodes', 'sha256': 'c' * 64},
                           'effective_config': {'path': 'effective', 'sha256': 'd' * 64},
                           'prerequisites': {}}}
        self.responses = {'HEAD': 'a' * 40, 'HEAD^{tree}': owner.TEST_TREE,
                          owner.SOURCE_HEAD + '^{tree}': owner.SOURCE_TREE,
                          'status': '', 'diff': owner.TEST_FILE}
        def run(argv):
            if 'status' in argv:
                return SimpleNamespace(stdout=self.responses['status'])
            if 'diff' in argv:
                return SimpleNamespace(stdout=self.responses['diff'])
            return SimpleNamespace(stdout=self.responses[argv[-1]])
        self.ops = SimpleNamespace(run=run, sha256=lambda path: owner.TEST_SHA)
        import q03_git_toolchain
        for obj, name, replacement in [
                (q03_git_toolchain, 'validate_git_metadata', lambda *args: None),
                (owner, 'pinned_json', lambda binding: copy.deepcopy(self.documents[binding['path']])),
                (owner, 'validate_prerequisites', lambda *args: None),
                (owner, 'load_spark', lambda: SimpleNamespace(topology=SimpleNamespace(
                    configuration_errors=lambda profile, nodes: [],
                    problems=lambda profile, nodes: [])))]:
            guard = mock.patch.object(obj, name, replacement)
            guard.start()
            self.addCleanup(guard.stop)

    def bind(self):
        return self.owner.validate_owner_binding(self.manifest, self.ops)

    def test_actual_binding_requires_clean_exact_test_delta(self):
        before = copy.deepcopy(self.manifest)
        bound = self.bind()
        self.assertEqual(self.manifest, before)
        self.assertEqual(bound['candidate'], self.manifest['candidate'])
        for key, wrong in [('HEAD', 'f' * 40), ('HEAD^{tree}', 'e' * 40),
                           (self.owner.SOURCE_HEAD + '^{tree}', 'd' * 40),
                           ('status', ' M runtime.py'), ('diff', self.owner.TEST_FILE + '\nruntime.py'),
                           ('diff', '')]:
            old = self.responses[key]
            self.responses[key] = wrong
            with self.subTest(key=key, wrong=wrong), self.assertRaises(RuntimeError):
                self.bind()
            self.responses[key] = old

    def test_actual_binding_rejects_each_wrong_rank_head_or_duplicate_node(self):
        original = copy.deepcopy(self.documents['nodes'])
        changes = [(0, 'rank', 1), (2, 'rank', True), (3, 'name', 'node2'),
                   (0, 'head', False), (1, 'head', True)]
        for rank, key, value in changes:
            self.documents['nodes'] = copy.deepcopy(original)
            self.documents['nodes']['nodes'][rank][key] = value
            with self.subTest(rank=rank, key=key), self.assertRaises(RuntimeError):
                self.bind()
        self.documents['nodes'] = copy.deepcopy(original)
        self.documents['nodes']['nodes'].pop()
        with self.assertRaises(RuntimeError):
            self.bind()

    def test_actual_binding_never_learns_equal_but_wrong_all_rank_capacity(self):
        for key, wrong in [('parallel_config.tensor_parallel_size', 3),
                           ('parallel_config.world_size', 1),
                           ('parallel_config.distributed_executor_backend', 'external_launcher'),
                           ('scheduler_config.max_num_seqs', 8),
                           ('scheduler_config.max_num_batched_tokens', 4096),
                           ('cache_config.kv_cache_memory_bytes', 1),
                           ('model_config.max_model_len', 65536),
                           ('speculative_config.num_speculative_tokens', 1),
                           ('compilation_config.pass_config.enable_sp', True)]:
            original = copy.deepcopy(self.documents['effective'])
            for state in self.owner.STEERING:
                self.documents['effective'][state][key] = wrong
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                self.bind()
            self.documents['effective'] = original
        del self.documents['effective']['active']['model_config.model']
        with self.assertRaises(RuntimeError):
            self.bind()


class OwnerPlanFixture(NoExternalOperations):
    def setUp(self):
        super().setUp()
        self.runtime = self.owner.canonical({'source_head': self.owner.SOURCE_HEAD,
                                             'source_tree': self.owner.SOURCE_TREE})
        self.build_receipt = self.owner.canonical({'image_labels': {'local.spark3.vllm.tree': self.owner.SOURCE_TREE}})
        self.binding = {
            'candidate': {'build_receipt_path': str(self.directory / 'build-receipt.json'),
                          'build_receipt_sha256': self.owner.digest(self.build_receipt),
                          'pytest_base_pythonpath': '/image/base',
                          'runtime_manifest_path': str(self.directory / 'runtime.json'),
                          'tblib_path': str(self.directory / 'tblib')},
            'master_port': 29633, 'handoff_seconds': 30,
            'profile_value': {'distributed': {'master_addr': '192.0.2.1'},
                              'container': {'image': 'unused', 'expected_labels': {},
                                            'mounts': [['/fixture/models', '/models', 'ro'],
                                                       ['/original/cache', '/cache', 'rw']],
                                            'docker_run_args': ['--network=host']},
                              'host': {'home': '/fixture/home'}},
            'nodes_value': {'nodes': [{'rank': r, 'name': f'node{r}', 'head': r == 0}
                                       for r in range(4)]},
            'effective_value': {s: {'reviewed': s} for s in self.owner.STEERING},
            'independent_root': str(self.directory / 'independent'),
            'tests': {'path': str(self.directory / 'test-checkout')}}
        self.spark = SimpleNamespace(expected_environment=lambda profile, node:
                    {'PYTHONPATH': '/image/base', 'NODE_RANK': str(node['rank'])})
        for target, replacement in [('load_spark', lambda: self.spark),
                                    ('read_owned_bytes', self.read_input)]:
            guard = mock.patch.object(self.owner, target, replacement)
            guard.start()
            self.addCleanup(guard.stop)
        # Synthetic immutable runtime bytes have their own exact expected hash.
        guard = mock.patch.object(self.owner, 'RUNTIME_SHA', self.owner.digest(self.runtime))
        guard.start()
        self.addCleanup(guard.stop)

    def read_input(self, path):
        if str(path) == self.binding['candidate']['runtime_manifest_path']:
            return self.runtime
        if str(path) == self.binding['candidate']['build_receipt_path']:
            return self.build_receipt
        return Path(path).read_bytes()

    def plan(self, mode='off', steering='off'):
        return self.owner.owner_plan(self.binding, 'a' * 32, 'b' * 32,
                                     {'mode': mode, 'steering': steering})


class OwnerPlanTests(OwnerPlanFixture):
    def test_four_rank_plan_is_deterministic_separate_and_closed(self):
        original = copy.deepcopy(self.binding)
        first, again = self.plan(), self.plan()
        self.assertEqual(first, again)
        self.assertEqual(self.binding, original)
        self.assertEqual([r['rank'] for r in first['ranks']], list(range(4)))
        self.assertEqual(len({r['name'] for r in first['ranks']}), 4)
        self.assertEqual(len({r['root'] for r in first['ranks']}), 4)
        plan_bytes = dict(first)
        del plan_bytes['plan_sha256']
        self.assertEqual(first['plan_sha256'], self.owner.digest(self.owner.canonical(plan_bytes)))
        self.assertEqual(first['spec_sha256'], self.owner.digest(self.owner.canonical(first['spec'])))
        for rank in first['ranks']:
            self.assertNotIn('PYTHONPATH', rank['environment'])
            mounts = {dst: (src, mode) for src, dst, mode in rank['mounts']}
            self.assertEqual(mounts['/models'], ('/fixture/models', 'ro'))
            self.assertEqual(mounts['/validation/tests'][1], 'ro')
            self.assertEqual(mounts['/runtime/owner-spec.json'][1], 'ro')
            self.assertEqual(mounts['/runtime/runtime-manifest.json'][1], 'ro')
            self.assertEqual(mounts['/pytest-dependency'][1], 'ro')
            self.assertEqual({dst for dst, (_, mode) in mounts.items() if mode == 'rw'},
                             {'/cache', '/results'})
            self.assertNotEqual(mounts['/cache'][0], '/original/cache')
        self.assertNotIn('--headless', first['ranks'][0]['python_argv'])
        for rank in first['ranks'][1:]:
            argv = rank['python_argv']
            self.assertEqual(argv[-1], '--headless')
            self.assertEqual(argv[argv.index('--node-rank') + 1], str(rank['rank']))

    def test_only_six_predeclared_mode_steering_groups_change_identity(self):
        hashes = set()
        for mode in self.owner.MODES:
            for steering in self.owner.STEERING:
                with self.subTest(mode=mode, steering=steering):
                    plan = self.plan(mode, steering)
                    hashes.add(plan['plan_sha256'])
                    self.assertEqual(plan['spec']['mode'], mode)
                    self.assertEqual(plan['spec']['steering']['state'], steering)
                    self.assertEqual('additional_config' in plan['spec']['engine_kwargs'], steering != 'off')
                    self.assertTrue(all(r['environment']['VLLM_DS41_Q03_MHC_MXFP8'] ==
                                        ('1' if mode == 'dual' else '0') for r in plan['ranks']))
        self.assertEqual(len(hashes), 6)

    def test_foreign_uuid_pythonpath_and_writable_immutable_mount_are_rejected(self):
        for value in ('foreign', 'a' * 31, 'A' * 32, None):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                self.owner.owner_plan(self.binding, value, 'b' * 32, {'mode': 'off', 'steering': 'off'})
        self.spark.expected_environment = lambda profile, node: {'PYTHONPATH': '/foreign'}
        with self.assertRaisesRegex(RuntimeError, 'Python path'):
            self.plan()
        self.spark.expected_environment = lambda profile, node: {'PYTHONPATH': '/image/base'}
        self.binding['profile_value']['container']['mounts'][0][-1] = 'rw'
        with self.assertRaisesRegex(RuntimeError, 'writable mount'):
            self.plan()

    def test_changed_runtime_bytes_and_identity_are_rejected_at_plan_boundary(self):
        valid = self.runtime
        for value in ({'source_head': 'f' * 40, 'source_tree': 'e' * 40},
                      {'source_head': self.owner.SOURCE_HEAD, 'source_tree': 'foreign'},
                      {'source_head': self.owner.SOURCE_HEAD, 'source_tree': self.owner.SOURCE_TREE,
                       'changed': True}):
            self.runtime = self.owner.canonical(value)
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, 'runtime|source'):
                self.plan()
        self.runtime = valid

class FakeOwnerOps:
    """Injected daemon/epoch boundary, not a replacement controller loop."""
    def __init__(self, owner, session, plan):
        self.owner, self.session, self.plan = owner, session, plan
        self.events = []
        self.items, self.guards = {}, {}
        self.bad_id = None
        self.mutate = None
        self.fail_action = None
        self.bad_guard = None
        self.ready_mutate = None
        self.ready_missing = False
        self.authority_failure = None
        self.abort_after = None
        self.unknown_epoch = False
        self.stubborn = False
        self.ack_error = False
        self.sleeps = 0
        self.acked = False

    def durable(self):
        return json.loads((self.journal.directory / 'state.json').read_text())

    def container(self, rank, row):
        target = self.plan['ranks'][rank]
        env = dict(target['environment'], PYTHONPATH=self.plan['binding']['candidate']['pytest_base_pythonpath'])
        return {'Id': row['container_id'], 'Image': self.owner.IMAGE,
                'State': {'Running': False, 'ExitCode': 0, 'OOMKilled': False},
                'Config': {'Labels': {'q03.window': self.plan['window_id'],
                           'q03.attempt': self.plan['attempt_id'], 'q03.job': 'owner_model',
                           'q03.rank': str(rank), 'q03.plan': self.plan['plan_sha256']},
                           'Entrypoint': ['python3'], 'Cmd': target['python_argv'],
                           'WorkingDir': '/validation',
                           'Env': [f'{key}={value}' for key, value in env.items()]},
                'HostConfig': {'NetworkMode': 'host', 'IpcMode': 'host',
                               'Privileged': False, 'AutoRemove': False,
                               'RestartPolicy': {'Name': 'no'},
                               'Memory': 112 * 1024**3, 'MemorySwap': 112 * 1024**3,
                               'ShmSize': 16 * 1024**3, 'CpusetCpus': '5-9,15-19',
                               'DeviceRequests': [{'Count': -1, 'Capabilities': [['gpu']]}],
                               'Devices': [{'PathOnHost': '/dev/infiniband', 'PathInContainer': '/dev/infiniband', 'CgroupPermissions': 'rwm'}],
                               'CapAdd': ['IPC_LOCK'], 'SecurityOpt': ['label=disable', 'seccomp=unconfined'],
                               'DeviceCgroupRules': ['c 226:* rw'],
                               'Ulimits': [{'Name': name, 'Soft': value, 'Hard': value} for name, value in
                                           [('memlock', -1), ('stack', 67108864), ('core', 0)]]},
                'Mounts': [{'Source': s, 'Destination': d, 'RW': m == 'rw'}
                           for s, d, m in target['mounts']] + [
                           {'Source': '/dev/dri/by-path/pci-000f:01:00.0-card',
                            'Destination': '/dev/dri/card0', 'RW': True}]}

    def action(self, rank, kind, row):
        state = self.durable()
        if kind in ('prepare', 'create', 'startup_guard', 'start', 'steady_guard'):
            last = json.loads((self.journal.directory / f"{state['sequence']:06d}.json").read_text())
            wanted = {'startup_guard': 'startup-guard', 'steady_guard': 'steady-guard'}.get(kind, kind)
            assert last['kind'] == wanted + '-intent' and last['rank'] == rank
        self.events.append((kind, rank))
        if self.fail_action == (kind, rank):
            raise RuntimeError(f'injected {kind} rank{rank}')
        if kind == 'create':
            cid = f'{rank + 1:x}' * 64
            self.items[rank] = self.container(rank, dict(row, container_id=cid))
            if self.mutate is not None:
                self.mutate(rank, self.items[rank])
            if self.bad_id is not None and self.bad_id[0] == rank:
                return self.bad_id[1]
            return cid
        if kind == 'startup_guard':
            self.guards[rank] = {'owned': True, 'active': True, 'phase': 'startup',
                                 'container_id': row['container_id']}
        elif kind == 'steady_guard':
            self.guards[rank]['phase'] = 'steady'
        elif kind == 'start':
            assert len(self.items) == 4
            assert all(r['container_id'] and len(r['container_id']) == 64 for r in state['ranks'])
            assert len(self.guards) == 4 and all(g['active'] for g in self.guards.values())
            self.items[rank]['State']['Running'] = True
        elif kind == 'stop':
            if not self.stubborn:
                self.items[rank]['State']['Running'] = False
        elif kind == 'stop_guard':
            self.guards[rank]['active'] = False
        elif kind == 'remove':
            if not self.stubborn:
                self.items.pop(rank, None)
        return None

    def inspect(self, rank, row):
        self.events.append(('inspect', rank))
        return copy.deepcopy(self.items.get(rank))

    def guard_state(self, rank, row):
        self.events.append(('guard', rank))
        state = copy.deepcopy(self.guards.get(rank, {'absent': True}))
        if self.bad_guard is not None and self.bad_guard == (rank, state.get('phase')):
            state['active'] = False
        return state

    def read_ready(self):
        self.events.append(('ready', None))
        assert all(item['State']['Running'] for item in self.items.values()) and len(self.items) == 4
        if self.ready_missing:
            return None
        expected = self.plan['spec']['expected_effective_config']
        reports = []
        for rank in range(4):
            reports.append({'worker_rank': rank, 'tp_rank': rank, 'config_rank': rank,
                            'node_rank': rank, 'node_rank_within_dp': rank, 'local_rank': 0,
                            'effective_config': expected, 'runner_config': expected,
                            'owner': {'rank': rank, 'sm': [12, 1], 'tp': 4,
                                      'sequence_parallel': False,
                                      'owner_present': self.plan['selection']['mode'] == 'dual',
                                      'steering_profile': None}})
        value = {'schema_version': 1, 'window_id': self.plan['window_id'],
                 'attempt_id': self.plan['attempt_id'], 'spec_sha256': self.plan['spec_sha256'],
                 'reports': reports, 'reports_sha256': self.owner.digest(self.owner.canonical(reports))}
        if self.ready_mutate:
            self.ready_mutate(value)
            value['reports_sha256'] = self.owner.digest(self.owner.canonical(value['reports']))
        return self.owner.canonical(value)

    def publish_ack(self, ack):
        self.events.append(('ack', None))
        assert self.durable()['phase'] == 'steady_guards4'
        assert len(self.guards) == 4 and all(g['active'] and g['phase'] == 'steady'
                                           for g in self.guards.values())
        assert ack['steady_guard_ready'] is True
        assert ack['window_id'] == self.plan['window_id']
        assert ack['attempt_id'] == self.plan['attempt_id']
        assert ack['spec_sha256'] == self.plan['spec_sha256']
        if self.ack_error:
            self.unknown_epoch = True
            raise RuntimeError('uncertain ACK publication')
        self.acked = True
        self.items[0]['State']['Running'] = False

    def collect_result(self):
        self.events.append(('collect', None))
        return {'status': 'observed-only', 'attempt_id': self.plan['attempt_id']}

    def check_authority(self):
        if self.authority_failure and (self.abort_after is None or self.abort_after in self.events):
            raise self.authority_failure

    def seal_all(self):
        self.events.append(('seal', None))
        if self.unknown_epoch:
            raise RuntimeError('UNKNOWN remote epoch')

    def sleep(self, seconds):
        self.sleeps += 1
        if self.sleeps >= 2:
            raise TimeoutError('injected bounded readiness timeout')


class OwnerLifecycleTests(OwnerPlanFixture):
    def setUp(self):
        super().setUp()
        # Filesystem ownership/alias admission is covered elsewhere. Here all
        # durable state is real temporary data; only that boundary is substituted.
        guard = mock.patch.object(self.owner, 'read_owned_bytes',
            self.read_input)
        guard.start()
        self.addCleanup(guard.stop)

    def scenario(self, name):
        plan = self.plan()
        directory = self.directory / name
        (directory / 'attempts' / plan['attempt_id']).mkdir(parents=True)
        checks = []
        session = SimpleNamespace(directory=directory, capsule={'window_id': plan['window_id']},
                                  check=lambda phase: checks.append(phase))
        ops = FakeOwnerOps(self.owner, session, plan)
        return session, plan, ops, checks

    def drain(self, session, plan, ops):
        ops.authority_failure = None  # Recovery retains authority despite a completed cancel.
        return self.owner.quiesce_owner(session, plan['attempt_id'], ops=ops)

    def test_actual_run_orders_all_four_barriers_and_common_drain(self):
        session, plan, ops, checks = self.scenario('happy')
        result = self.owner.run_owner(session, plan, ops=ops)
        self.assertEqual(result['status'], 'observed-only')
        phases = [json.loads(path.read_text())['phase'] for path in sorted(ops.journal.directory.glob('[0-9]*.json'))
                  if json.loads(path.read_text())['kind'] == 'phase']
        self.assertEqual(phases, ['prepared4', 'created4', 'startup_guards4', 'started4',
                                  'config_ready', 'steady_guards4', 'acked', 'leader_terminal'])
        self.assertLess(max(ops.events.index(('create', rank)) for rank in range(4)),
                        min(ops.events.index(('startup_guard', rank)) for rank in range(4)))
        self.assertLess(max(ops.events.index(('startup_guard', rank)) for rank in range(4)),
                        min(ops.events.index(('start', rank)) for rank in range(4)))
        self.assertLess(max(ops.events.index(('start', rank)) for rank in range(4)), ops.events.index(('ready', None)))
        self.assertLess(max(ops.events.index(('steady_guard', rank)) for rank in range(4)), ops.events.index(('ack', None)))
        proof = self.drain(session, plan, ops)
        self.assertTrue(proof['daemon_quiescent'])
        self.assertTrue(proof['gpu_drain_required_by_caller'])
        self.assertNotIn('quiescent', proof)
        self.assertEqual(checks, [])
        self.assertFalse(ops.items)
        self.assertEqual(ops.durable()['phase'], 'drained')
        # A later watchdog-style reopen uses the same durable journal and method.
        again = self.drain(session, plan, ops)
        self.assertEqual(again, proof)

    def test_missing_delayed_or_malformed_create_id_never_starts_or_restores(self):
        for index, value in enumerate((None, '', 'a' * 12, 'A' * 64, 'a' * 65)):
            session, plan, ops, checks = self.scenario('badid' + str(index))
            ops.bad_id = (2, value)
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, 'UNKNOWN'):
                self.owner.run_owner(session, plan, ops=ops)
            self.assertFalse(any(kind == 'start' for kind, _ in ops.events))
            with self.assertRaisesRegex(RuntimeError, 'UNKNOWN'):
                self.drain(session, plan, ops)
            self.assertFalse(checks)
            self.assertFalse((ops.journal.directory / 'drained.json').exists())

    def test_wrong_image_rank_labels_environment_or_mount_blocks_start(self):
        mutations = [lambda item: item.update(Image='sha256:' + 'f' * 64),
                     lambda item: item['Config']['Labels'].update({'q03.rank': '3'}),
                     lambda item: item['Config']['Labels'].update({'q03.attempt': 'e' * 32}),
                     lambda item: item['Config']['Env'].append('VLLM_DS41_Q03_MHC_MXFP8=1'),
                     lambda item: item['Mounts'][0].update(RW=True),
                     lambda item: item['HostConfig'].update(NetworkMode='none')]
        for index, mutate in enumerate(mutations):
            session, plan, ops, checks = self.scenario('inspect' + str(index))
            ops.mutate = lambda rank, item: mutate(item) if rank == 1 else None
            with self.subTest(index=index), self.assertRaises((RuntimeError, ValueError)):
                self.owner.run_owner(session, plan, ops=ops)
            self.assertFalse(any(kind == 'start' for kind, _ in ops.events))
            self.assertFalse(checks)

    def test_partial_startup_or_steady_guard_never_crosses_barrier(self):
        for phase in ('startup', 'steady'):
            for rank in range(4):
                session, plan, ops, checks = self.scenario(f'guard-{phase}-{rank}')
                ops.bad_guard = (rank, phase)
                with self.subTest(phase=phase, rank=rank), self.assertRaisesRegex(RuntimeError, 'guard'):
                    self.owner.run_owner(session, plan, ops=ops)
                self.assertFalse(ops.acked)
                if phase == 'startup':
                    self.assertFalse(any(kind == 'start' for kind, _ in ops.events))
                ops.bad_guard = None
                self.assertTrue(self.drain(session, plan, ops)['daemon_quiescent'])

    def test_foreign_ready_partial_rank_and_wrong_config_never_ack(self):
        mutations = [lambda value: value.update(attempt_id='f' * 32),
                     lambda value: value['reports'].pop(),
                     lambda value: value['reports'][2].update(node_rank=1),
                     lambda value: value['reports'][3].update(effective_config={'wrong': True})]
        for index, mutate in enumerate(mutations):
            session, plan, ops, checks = self.scenario('ready' + str(index))
            ops.ready_mutate = mutate
            with self.subTest(index=index), self.assertRaises(RuntimeError):
                self.owner.run_owner(session, plan, ops=ops)
            self.assertFalse(ops.acked)
            self.assertFalse(any(kind == 'steady_guard' for kind, _ in ops.events))
            self.assertTrue(self.drain(session, plan, ops)['daemon_quiescent'])

    def test_request_death_and_timeout_stop_progress_then_use_common_drain(self):
        for label, exc in [('request', RuntimeError('requested')), ('death', SystemExit('main died')),
                           ('timeout', TimeoutError('deadline'))]:
            session, plan, ops, checks = self.scenario(label)
            ops.authority_failure, ops.abort_after = exc, ('start', 1)
            with self.subTest(label=label), self.assertRaises(type(exc)):
                self.owner.run_owner(session, plan, ops=ops)
            self.assertNotIn(('start', 2), ops.events)
            self.assertFalse(ops.acked)
            self.assertTrue(self.drain(session, plan, ops)['daemon_quiescent'])
        session, plan, ops, checks = self.scenario('readiness-timeout')
        ops.ready_missing = True
        with self.assertRaises(TimeoutError):
            self.owner.run_owner(session, plan, ops=ops)
        self.assertFalse(ops.acked)
        self.assertTrue(self.drain(session, plan, ops)['daemon_quiescent'])

    def test_unknown_epoch_ack_or_live_daemon_never_reaches_restore_proof(self):
        for name in ('epoch', 'ack', 'daemon'):
            session, plan, ops, checks = self.scenario(name)
            if name == 'ack':
                ops.ack_error = True
                with self.assertRaisesRegex(RuntimeError, 'ACK'):
                    self.owner.run_owner(session, plan, ops=ops)
            else:
                self.owner.run_owner(session, plan, ops=ops)
                ops.unknown_epoch = name == 'epoch'
                ops.stubborn = name == 'daemon'
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                self.drain(session, plan, ops)
            self.assertFalse(checks)
            self.assertFalse((ops.journal.directory / 'drained.json').exists())

    def test_retained_plan_tamper_blocks_watchdog_reopen(self):
        session, plan, ops, checks = self.scenario('tamper')
        self.owner.run_owner(session, plan, ops=ops)
        path = ops.journal.directory / 'plan.json'
        value = json.loads(path.read_text())
        value['ranks'][0]['name'] = 'foreign'
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(RuntimeError, 'digest'):
            self.drain(session, plan, ops)
        self.assertFalse(checks)

class OwnerRemoteBoundaryTests(OwnerPlanFixture):
    def setUp(self):
        super().setUp()
        patch = mock.patch.object(self.owner, 'read_owned_bytes', self.read_input)
        patch.start()
        self.addCleanup(patch.stop)
        self.plan_value = self.plan()
        self.session_root = self.directory / 'session'
        (self.session_root / 'attempts' / self.plan_value['attempt_id']).mkdir(parents=True)
        self.session = SimpleNamespace(directory=self.session_root,
            capsule={'window_id': self.plan_value['window_id'], 'recovery_source_sha256': {},
                     'core_process': {'pid': 987654321, 'start_ticks': 'synthetic-core'}},
            owns_hold=lambda: True, requested=lambda: False,
            ops=SimpleNamespace(recovery_sources=lambda: {}, read_json=lambda path: {
                'active_attempt': {'attempt_id': self.plan_value['attempt_id']},
                'active_gate': 'owner_model', 'held_sequence': 0}))
        self.journal = self.owner.Journal(self.session, self.plan_value, create=True)
        self.addCleanup(self.journal.close)
        self.adapter = object.__new__(self.owner.OwnerOps)
        self.adapter.session, self.adapter.plan, self.adapter.journal = self.session, self.plan_value, self.journal
        self.adapter.binding = self.plan_value['binding']
        self.adapter.recovering = False
        self.adapter.end = self.owner.time.time() + 100
        self.adapter.helper = SimpleNamespace(bound_command=lambda argv: ['bounded', *argv],
            verified_helper_command=lambda path, sha, argv: ['python', 'verified', *argv])
        self.adapter.pre = {'remote_helper_path': '/fixture/helper', 'remote_helper_sha256': 'a' * 64,
                            'remote_authority_path': '/fixture/authority', 'remote_authority_sha256': 'b' * 64}
        self.adapter.sleep = lambda _: None

    def test_actual_mutate_journals_epoch_before_launch_and_seals_all_four_nodes(self):
        self.adapter.check_authority = lambda: None
        calls = []
        def helper(rank, action, epoch, operation=None, extra=()):
            calls.append((rank, action, epoch, operation))
            if action == 'launch':
                durable = json.loads((self.journal.directory / 'state.json').read_text())
                self.assertEqual(durable['epochs'][-1]['operation'], operation)
                self.assertEqual(durable['epochs'][-1]['epoch'], epoch)
                self.assertEqual(durable['epochs'][-1]['rank'], rank)
                last = json.loads((self.journal.directory / f"{durable['sequence']:06d}.json").read_text())
                self.assertEqual(last['kind'], 'remote-intent')
                return {'status': 'uncertain', 'state': {}}
            if action == 'status':
                return {'status': 'terminal', 'action_succeeded': True, 'stdout': 'exact-id\n'}
            self.assertEqual(action, 'seal')
            return {'safe_to_overlap': True}
        self.adapter.helper_call = helper
        result = self.adapter.mutate(2, 'create', ['docker', 'create', 'fixed'], 90)
        self.assertEqual(result, 'exact-id\n')
        self.assertEqual([(rank, action) for rank, action, _, _ in calls],
                         [(2, 'launch'), (2, 'status'), (0, 'seal'), (1, 'seal'), (2, 'seal'), (3, 'seal')])

    def test_sealed_epochs_are_durable_only_after_all_four_and_not_polled_quadratically(self):
        calls = []
        self.adapter.helper_call = lambda rank, action, epoch: calls.append((rank, epoch)) or {'safe_to_overlap': True}
        self.journal.state['epochs'] = [{'epoch': 'a' * 32}, {'epoch': 'b' * 32}]
        self.adapter.seal_all()
        self.assertEqual(len(calls), 8)
        retained = json.loads((self.journal.directory / 'state.json').read_text())
        self.assertEqual(retained['sealed_epochs'], ['a' * 32, 'b' * 32])
        self.adapter.seal_all()
        self.assertEqual(len(calls), 8)
        self.journal.state['epochs'].append({'epoch': 'c' * 32})
        self.adapter.seal_all()
        self.assertEqual(calls[8:], [(rank, 'c' * 32) for rank in range(4)])
        def partial(rank, action, epoch):
            if rank == 2:
                raise RuntimeError('lost terminal seal receipt')
            return {'safe_to_overlap': True}
        self.adapter.helper_call = partial
        self.journal.state['epochs'].append({'epoch': 'd' * 32})
        with self.assertRaisesRegex(RuntimeError, 'lost terminal'):
            self.adapter.seal_all()
        self.assertNotIn('d' * 32, self.journal.state['sealed_epochs'])
        self.adapter.helper_call = lambda rank, action, epoch: calls.append((rank, epoch)) or {'safe_to_overlap': True}
        self.adapter.seal_all()
        self.assertEqual(calls[-4:], [(rank, 'd' * 32) for rank in range(4)])

    def test_actual_mutate_lost_supervisor_or_failed_terminal_is_not_success(self):
        self.adapter.check_authority = lambda: None
        for value in ({'status': 'uncertain', 'state': {'supervisor': {'pid': 1}}},
                      {'status': 'terminal', 'action_succeeded': False, 'stdout': ''}):
            calls = []
            self.adapter.helper_call = lambda rank, action, *args: calls.append(action) or value
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                self.adapter.mutate(1, 'start', ['docker', 'start', 'a' * 64], 90)
            self.assertEqual(calls, ['launch'])

    def test_actual_seal_rejects_unknown_and_cancels_only_running_record(self):
        for value in ({'safe_to_overlap': False, 'sealed': False},
                      {'safe_to_overlap': False, 'sealed': True, 'operations': []},
                      {'safe_to_overlap': False, 'sealed': True,
                       'operations': [{'status': 'uncertain', 'operation': 'e' * 32}]},
                      {'safe_to_overlap': False, 'sealed': True,
                       'operations': [{'status': 'terminal', 'safe_to_overlap': False,
                                       'operation': 'e' * 32}]}):
            self.adapter.helper_call = lambda *args: value
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, 'seal|UNKNOWN'):
                self.adapter.seal_epoch('f' * 32)
        calls, counts = [], {}
        def helper(rank, action, epoch, operation=None):
            calls.append((rank, action, operation))
            counts[(rank, action)] = counts.get((rank, action), 0) + 1
            if rank == 0 and action == 'seal' and counts[(rank, action)] == 1:
                return {'safe_to_overlap': False, 'sealed': True,
                        'operations': [{'status': 'running', 'operation': 'e' * 32}]}
            return {'safe_to_overlap': True}
        self.adapter.helper_call = helper
        self.adapter.seal_epoch('f' * 32)
        self.assertEqual(calls, [(0, 'seal', None), (0, 'cancel', 'e' * 32),
                                 (0, 'seal', None), (1, 'seal', None), (2, 'seal', None), (3, 'seal', None)])

    def test_actual_helper_receipt_rejects_foreign_epoch_window_and_failed_transport(self):
        # This test isolates receipt parsing; filesystem/physical authority has its own real guard tests.
        self.adapter.verify_boundary = lambda rank: None
        value = {'window': self.plan_value['window_id'], 'epoch': 'e' * 32}
        self.adapter.transport = lambda *args: SimpleNamespace(returncode=0, stdout=json.dumps(value), stderr='')
        self.assertEqual(self.adapter.helper_call(0, 'seal', 'e' * 32), value)
        for wrong in ({'window': 'f' * 32, 'epoch': 'e' * 32},
                      {'window': self.plan_value['window_id'], 'epoch': 'f' * 32},
                      {'window': self.plan_value['window_id'], 'operation': 'f' * 32}):
            self.adapter.transport = lambda *args: SimpleNamespace(returncode=0, stdout=json.dumps(wrong), stderr='')
            with self.subTest(wrong=wrong), self.assertRaises(RuntimeError):
                self.adapter.helper_call(0, 'seal', 'e' * 32)
        self.adapter.transport = lambda *args: SimpleNamespace(returncode=255, stdout='', stderr='unreachable')
        with self.assertRaisesRegex(RuntimeError, 'UNKNOWN'):
            self.adapter.helper_call(0, 'seal', 'e' * 32)

    def test_actual_inspect_exact_absence_is_distinct_from_daemon_unknown(self):
        cid = 'a' * 64
        self.adapter.transport = lambda *args: SimpleNamespace(returncode=1, stdout='[]',
                                                               stderr='Error: No such container: ' + cid)
        self.assertIsNone(self.adapter.inspect(0, {'container_id': cid}))
        for code, stdout, stderr in [(1, '', 'Cannot connect to the Docker daemon'),
                                      (1, '[]', 'Error: No such container: ' + 'b' * 64),
                                      (125, '', 'Error: No such container: ' + cid),
                                      (0, '[]', '')]:
            self.adapter.transport = lambda *args: SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)
            with self.subTest(code=code, stderr=stderr), self.assertRaises(RuntimeError):
                self.adapter.inspect(0, {'container_id': cid})


    def test_actual_guard_read_requires_exact_owned_environment_and_live_pid(self):
        cid = 'a' * 64
        self.adapter.binding['checkout'] = '/fixture/controller'
        self.adapter.binding['nodes_value']['ssh_user'] = 'fixture-user'
        environment = {'CONTAINER_NAME': cid, 'MEMGUARD_PHASE': 'startup',
                       'THRESHOLD_GIB': '5', 'INTERVAL': '0.25',
                       'WAIT_FOR_CONTAINER_SECONDS': '120',
                       'READY_FILE': '/tmp/' + cid + '-memguard.abc'}
        properties = {'LoadState': 'loaded', 'ActiveState': 'active', 'SubState': 'running',
                      'User': 'fixture-user', 'WorkingDirectory': '/fixture/controller',
                      'Environment': ' '.join(k + '=' + v for k, v in environment.items()),
                      'ExecStart': '{ path=/fixture/controller/scripts/memguard.sh ; argv[]=/fixture/controller/scripts/memguard.sh ; }',
                      'MainPID': '12345'}
        def transport(*args):
            return SimpleNamespace(returncode=0,
                stdout='\n'.join(k + '=' + v for k, v in properties.items()), stderr='')
        self.adapter.transport = transport
        state = self.adapter.guard_state(0, {'container_id': cid})
        self.assertEqual(state, {'owned': True, 'active': True, 'phase': 'startup', 'container_id': cid})
        for key, value in [('User', 'foreign'), ('WorkingDirectory', '/foreign'),
                           ('MainPID', '0'), ('ExecStart', '/wrong/script'),
                           ('Environment', properties['Environment'] + ' CONTAINER_NAME=' + cid)]:
            old = properties[key]
            properties[key] = value
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                self.adapter.guard_state(0, {'container_id': cid})
            properties[key] = old
        properties.clear()
        properties.update(LoadState='not-found', ActiveState='inactive')
        self.assertEqual(self.adapter.guard_state(0, {'container_id': cid}), {'absent': True})
        properties['ActiveState'] = 'active'
        with self.assertRaises(RuntimeError):
            self.adapter.guard_state(0, {'container_id': cid})

    def test_actual_closed_action_mapping_never_accepts_an_arbitrary_command(self):
        calls = []
        self.adapter.binding['checkout'] = '/fixture/controller'
        self.adapter.mutate = lambda rank, kind, argv, timeout: calls.append((rank, kind, argv, timeout)) or ('a' * 64 + '\n')
        row = {'container_id': 'a' * 64}
        self.assertEqual(self.adapter.action(0, 'create', row), 'a' * 64)
        argv = calls[-1][2]
        self.assertEqual(argv[:2], ['docker', 'create'])
        self.assertEqual(argv[argv.index('--entrypoint') + 1:][:2], ['python3', self.owner.IMAGE])
        files = [{'path': '/fixture/controller/scripts/' + name, 'sha256': 'c' * 64}
                 for name in ('memguard-start.sh', 'memguard.sh')]
        with mock.patch.object(self.owner, 'input_contract', return_value={'files': files}):
            for kind in ('start', 'stop', 'remove', 'startup_guard', 'steady_guard', 'stop_guard'):
                self.adapter.action(2, kind, row)
                self.assertIn('a' * 64, ' '.join(calls[-1][2]))
        before = len(calls)
        with self.assertRaisesRegex(RuntimeError, 'unknown'):
            self.adapter.action(0, 'exec-arbitrary', row)
        with self.assertRaisesRegex(RuntimeError, 'exact'):
            self.adapter.action(0, 'start', {'container_id': 'short'})
        self.assertEqual(len(calls), before)

    def test_actual_authority_refuses_dead_or_reused_main_before_generation(self):
        import q03_window_watchdog
        with mock.patch.object(self.owner, 'verify_plan_identity', return_value=None):
            with mock.patch.object(q03_window_watchdog, 'process_alive', return_value=False):
                with self.assertRaisesRegex(RuntimeError, 'core|main|process'):
                    self.owner.OwnerOps.check_authority(self.adapter)
            with mock.patch.object(q03_window_watchdog, 'process_alive', return_value=True):
                self.owner.OwnerOps.check_authority(self.adapter)
            self.adapter.recovering = True
            with mock.patch.object(q03_window_watchdog, 'process_alive', return_value=False):
                self.owner.OwnerOps.check_authority(self.adapter)

class OwnerControllerIntegrationTests(NoExternalOperations):

    def test_actual_owner_entry_without_held_refuses_before_layout_host_or_io(self):
        import q03_window as core
        ops = SimpleNamespace(validate_host=mock.Mock(side_effect=AssertionError('host before refusal')))
        with mock.patch.object(core, 'validate_layout', side_effect=AssertionError('layout before refusal')):
            with self.assertRaisesRegex(ValueError, 'held'):
                core.run_session({'jobs': [{'kind': 'owner_model'}]}, self.directory / 'unused', ops)
        ops.validate_host.assert_not_called()
        self.assertFalse((self.directory / 'unused').exists())

    def test_actual_owner_paths_protect_build_git_controller_and_all_immutable_inputs(self):
        root = self.directory / 'independent'
        root.mkdir()
        protected = self.directory / 'protected'
        candidate = {key: str(root / 'inputs' / key) for key in
                     ('tests_path', 'runtime_manifest_path', 'tblib_path', 'build_receipt_path',
                      'build_inputs_path', 'git_toolchain_path')}
        candidate.update(cache_path=str(root / 'cache'), output_path=str(root / 'results'))
        preflight = {key: str(protected / key) for key in
                     ('hold_path', 'request_path', 'legacy_hold_path', 'legacy_request_path', 'policy_path')}
        preflight.update(protected_roots=[str(protected)],
                         registered_policy={'repository': str(protected / 'repo')},
                         independent_checkout=str(root / 'controller'))
        binding = {key: {'path': str(root / 'inputs' / key)} for key in
                   ('tests', 'profile', 'nodes', 'effective_config')}
        binding['prerequisites'] = {key: {'path': str(root / 'inputs' / key)}
                                   for key in ('terminal', 'descriptor', 'capsule')}
        manifest = {'independent_root': str(root), 'candidate': candidate,
                    'preflight': preflight, 'owner_model': binding}
        self.owner.validate_paths(manifest, root / 'session')
        immutable = [candidate[key] for key in ('tests_path', 'runtime_manifest_path', 'tblib_path',
                     'build_receipt_path', 'build_inputs_path', 'git_toolchain_path')]
        immutable += [preflight['independent_checkout']]
        immutable += [binding[key]['path'] for key in ('tests', 'profile', 'nodes', 'effective_config')]
        immutable += [v['path'] for v in binding['prerequisites'].values()]
        for location in immutable:
            for target in ('cache_path', 'output_path', 'session'):
                altered = copy.deepcopy(manifest)
                directory = root / 'session'
                value = str(Path(location) / 'evidence')
                if target == 'session':
                    directory = Path(value)
                else:
                    altered['candidate'][target] = value
                with self.subTest(location=location, target=target), self.assertRaisesRegex(RuntimeError, 'overlap'):
                    self.owner.validate_paths(altered, directory)
        # Resource roots may not alias outputs or the session journal either.
        for target in ('cache_path', 'output_path'):
            altered = copy.deepcopy(manifest)
            altered['candidate'][target] = str(root / 'owner-resources' / 'evidence')
            with self.subTest(target=target), self.assertRaisesRegex(RuntimeError, 'overlap'):
                self.owner.validate_paths(altered, root / 'session')

    def test_actual_restore_blocks_before_original_actions_on_owner_unknown_or_gpu_unknown(self):
        import q03_window as core
        for reason in ('owner', 'gpu'):
            events = []
            state = {'active_process': None, 'active_attempt': {'attempt_id': 'b' * 32},
                     'restored': False}
            def cleanup(session):
                events.append('owner-drain')
                if reason == 'owner':
                    raise RuntimeError('UNKNOWN owner epoch')
                return {'daemon_quiescent': True, 'gpu_drain_required_by_caller': True}
            def check(phase):
                events.append(phase)
                if phase == 'restore_drained' and reason == 'gpu':
                    raise RuntimeError('UNKNOWN GPU drain')
            session = SimpleNamespace(state=state, owns_hold=lambda: True,
                manifest={'jobs': [{'kind': 'owner_model'}]},
                update=lambda **fields: state.update(fields), check=check,
                release=lambda: events.append('release'),
                ops=SimpleNamespace(terminate_owned_process=lambda p: events.append('terminate'),
                    cleanup_owner=cleanup, quiesce_original=lambda s: events.append('original-quiesce'),
                    cleanup_candidates=lambda s: events.append('other-cleanup')))
            with mock.patch.object(core, 'transition_original',
                                   side_effect=lambda s, action, timeout: events.append('original-' + action)):
                with self.subTest(reason=reason), self.assertRaisesRegex(RuntimeError, 'UNKNOWN'):
                    core.restore(session)
            self.assertNotIn('original-start', events)
            self.assertNotIn('release', events)
            self.assertFalse(state['restored'])
            if reason == 'owner':
                self.assertNotIn('original-stop', events)
            else:
                self.assertLess(events.index('owner-drain'), events.index('original-stop'))
                self.assertIn('restore_drained', events)

    def test_actual_held_drain_requires_gpu_proof_after_daemon_cleanup(self):
        import q03_held_window as held
        events = []
        state = {'active_process': None}
        def check(phase):
            events.append('check-' + phase)
            raise RuntimeError('UNKNOWN all-four GPU drain')
        session = SimpleNamespace(state=state, manifest={'jobs': [{'kind': 'owner_model'}]},
            update=lambda **fields: state.update(fields), check=check,
            ops=SimpleNamespace(terminate_owned_process=lambda p: events.append('terminate'),
                cleanup_owner=lambda s, attempt_id=None: events.append('owner-daemon-drain'),
                cleanup_held_candidates=lambda s, attempt_id=None: events.append('other-drain')))
        with self.assertRaisesRegex(RuntimeError, 'GPU drain'):
            held.drain_attempt(session, {'descriptor': {'attempt_id': 'b' * 32}}, None)
        self.assertEqual(events, ['terminate', 'owner-daemon-drain', 'other-drain', 'check-stopped'])
        self.assertEqual(state['phase'], 'attempt-draining')

    def test_actual_cleanup_dispatch_is_supervised_and_receipt_bound(self):
        import q03_window as core
        root = self.directory / 'session'
        attempt = root / 'attempts' / ('b' * 32)
        (attempt / 'owner').mkdir(parents=True)
        session = SimpleNamespace(directory=root, manifest={'jobs': [{'kind': 'owner_model'}]},
                                  capsule={'window_id': 'a' * 32})
        calls = []
        def execute(s, argv, **kw):
            calls.append(('execute', argv, kw))
            path = root / 'quiesce.log'
            path.write_text(json.dumps({'window_id': 'a' * 32, 'attempt_id': 'b' * 32,
                'daemon_quiescent': True, 'gpu_drain_required_by_caller': True}))
            return {'log_path': str(path)}
        facade = SimpleNamespace(execute=execute)
        facade.require_receipt = lambda result, expected: core.RuntimeOps.require_receipt(facade, result, expected)
        # Use actual log-path receipt parsing rather than a fake success boolean.
        core.RuntimeOps.cleanup_owner(facade, session)
        self.assertEqual(len(calls), 1)
        _, argv, kw = calls[0]
        self.assertIn('quiesce', argv)
        self.assertEqual(argv[argv.index('--attempt-id') + 1], 'b' * 32)
        self.assertEqual(kw['timeout'], 600)
        def wrong(s, argv, **kw):
            path = root / 'quiesce.log'
            path.write_text(json.dumps({'window_id': 'a' * 32, 'attempt_id': 'b' * 32,
                'daemon_quiescent': False, 'gpu_drain_required_by_caller': True}))
            return {'log_path': str(path)}
        facade.execute = wrong
        with self.assertRaises(RuntimeError):
            core.RuntimeOps.cleanup_owner(facade, session)

    def test_actual_source_inventory_pins_owner_and_guard_import_closure(self):
        import q03_window as core
        inventory = core.RuntimeOps().recovery_sources()
        for name in self.owner.SOURCE_FILES:
            self.assertEqual(inventory[name], hashlib.sha256((OWNER_ROOT / 'scripts' / name).read_bytes()).hexdigest())
        self.assertIn('q03_owner_model.py', inventory)
        self.assertIn('memguard-start.sh', inventory)
        self.assertIn('memguard.sh', inventory)
        self.assertIn('../bin/spark', inventory)


class PhysicalResourceBoundaryTests(NoExternalOperations):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory(dir=OWNER_ROOT)
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        root = self.base / 'independent'
        root.mkdir(mode=0o700)
        authority = root / 'authority.json'
        protected = [str(self.base / 'production')]
        authority.write_text(json.dumps({'independent_root': str(root), 'protected_roots': protected}))
        authority.chmod(0o600)
        immutable = root / 'fixture.py'
        immutable.write_bytes(b'fixture')
        self.contract = dict(independent_root=str(root), resource_path=str(root / 'owner-resources' / 'new'),
            protected_roots=protected, immutable_paths=[str(immutable)], input_directories=[],
            input_files=[{'path': str(immutable), 'sha256': hashlib.sha256(b'fixture').hexdigest()}],
            hostname=__import__('platform').node(), authority_path=str(authority),
            authority_sha256=hashlib.sha256(authority.read_bytes()).hexdigest(), authority_protected_roots=protected)

    def test_real_remote_guard_accepts_new_path_without_creating_it(self):
        result = self.owner.validate_boundary(self.contract)
        self.assertTrue(result['paths_safe'])
        self.assertFalse(Path(self.contract['resource_path']).exists())

    def test_prepare_under_permissive_inherited_umask_keeps_every_new_ancestor_private(self):
        import contextlib
        import io
        root = Path(self.contract['independent_root'])
        target = root / 'owner-resources' / ('a' * 32) / ('b' * 32) / '0'
        spec = '{}'
        payload = dict(root=str(target), independent_root=str(root), files=[], directories=[],
                       spec=spec, spec_sha256=hashlib.sha256(spec.encode()).hexdigest())
        previous = os.umask(0o002)
        try:
            with mock.patch.object(sys, 'argv', ['prepare', json.dumps(payload)]), contextlib.redirect_stdout(io.StringIO()):
                exec(compile(self.owner.PREPARE, '<actual-fixed-prepare>', 'exec'), {'__name__': '_prepare_test'})
        finally:
            os.umask(previous)
        for path in (target, target.parent, target.parent.parent, target.parent.parent.parent,
                     target / 'cache', target / 'results'):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
        contract = dict(self.contract, resource_path=str(target))
        self.assertTrue(self.owner.validate_boundary(contract)['paths_safe'])

    def test_registered_readonly_original_modes_are_preserved_but_new_namespaces_remain_strict(self):
        model = self.base / 'production' / 'model'
        model.mkdir(parents=True)
        model.parent.chmod(0o775)
        model.chmod(0o775)
        contract = dict(self.contract, input_directories=[str(model)], site_readonly_roots=[str(model)])
        before = (model.stat().st_mode, model.parent.stat().st_mode)
        self.assertTrue(self.owner.validate_boundary(contract)['paths_safe'])
        self.assertEqual(before, (model.stat().st_mode, model.parent.stat().st_mode))
        with self.assertRaisesRegex(RuntimeError, 'ownership/mode'):
            self.owner.validate_boundary(dict(contract, site_readonly_roots=[]))
        model.chmod(0o777)
        with self.assertRaisesRegex(RuntimeError, 'ownership/mode'):
            self.owner.validate_boundary(contract)
        model.chmod(0o775)
        root = Path(contract['independent_root'])
        with self.assertRaisesRegex(RuntimeError, 'unregistered readonly'):
            self.owner.validate_boundary(dict(contract, site_readonly_roots=[str(root)]))

    def test_executable_file_and_test_dependency_roles_never_inherit_readonly_data_exception(self):
        model = self.base / 'production' / 'model'
        model.mkdir(parents=True); model.parent.chmod(0o775); model.chmod(0o775)
        contract = dict(self.contract, input_directories=[str(model)], site_readonly_roots=[str(model)])
        self.assertTrue(self.owner.validate_boundary(contract)['paths_safe'])
        script = model / 'control.py'; script.write_bytes(b'code'); script.chmod(0o664)
        test_dir = model / 'tests'; test_dir.mkdir(); test_dir.chmod(0o775)
        variants = [dict(contract, input_files=[{'path':str(script),'sha256':hashlib.sha256(b'code').hexdigest()}]),
                    dict(contract, input_directories=[str(model), str(test_dir)]),
                    dict(contract, strict_input_directories=[str(model)])]
        for value in variants:
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, 'ownership/mode'):
                self.owner.validate_boundary(value)

    def test_real_remote_guard_rejects_aliases_foreign_host_and_protected_output(self):
        link = self.base / 'alias'
        link.symlink_to(Path(self.contract['independent_root']), target_is_directory=True)
        variants = [dict(self.contract, resource_path=str(link / 'owner-resources' / 'new')),
                    dict(self.contract, hostname='foreign-spark'),
                    dict(self.contract, resource_path=self.contract['independent_root']),
                    dict(self.contract, protected_roots=[self.contract['resource_path']])]
        for contract in variants:
            with self.subTest(contract=contract), self.assertRaises(RuntimeError):
                self.owner.validate_boundary(contract)

    def test_real_remote_guard_rejects_changed_input_authority_and_writable_ancestry(self):
        immutable = Path(self.contract['input_files'][0]['path'])
        immutable.write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError, 'hash drift'):
            self.owner.validate_boundary(self.contract)
        immutable.write_bytes(b'fixture')
        authority = Path(self.contract['authority_path'])
        authority.chmod(0o666)
        with self.assertRaisesRegex(RuntimeError, 'authority ownership'):
            self.owner.validate_boundary(self.contract)
        authority.chmod(0o600)
        root = Path(self.contract['independent_root'])
        root.chmod(0o777)
        with self.assertRaisesRegex(RuntimeError, 'ancestry ownership'):
            self.owner.validate_boundary(self.contract)
        root.chmod(0o700)

    def test_registered_physical_rank_mapping_rejects_alias_and_duplicate_host(self):
        physical, logical = [], []
        for rank in range(4):
            name = 'spark0' + str(rank + 1)
            inventory = self.base / (name + '.json')
            inventory.write_text(json.dumps({'exit_code': 0, 'node': name,
                'inventory': {'hostname': 'gb10-' + str(rank), 'pin_errors': []}}))
            physical.append(dict(name=name, rank=rank + 1, local=rank == 0,
                ssh_host='admin@10.0.0.' + str(rank + 1), inventory_path=str(inventory),
                inventory_sha256=hashlib.sha256(inventory.read_bytes()).hexdigest()))
            logical.append(dict(name=name, rank=rank, ssh_host='10.0.0.' + str(rank + 1)))
        binding = {'preflight': {'nodes': physical}, 'nodes_value': {'nodes': logical, 'ssh_user': 'admin'}}
        self.assertEqual([n['expected_hostname'] for n in self.owner.remote_nodes(binding)],
                         ['gb10-' + str(rank) for rank in range(4)])
        for field, value in [('ssh_host', 'alias'), ('name', 'foreign')]:
            bad = copy.deepcopy(binding)
            bad['nodes_value']['nodes'][1][field] = value
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                self.owner.remote_nodes(bad)
        bad = copy.deepcopy(binding)
        bad['preflight']['nodes'][1]['rank'] = 1
        with self.assertRaises(RuntimeError):
            self.owner.remote_nodes(bad)


if __name__ == '__main__':
    unittest.main()
