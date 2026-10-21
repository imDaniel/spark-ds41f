"""Pure HTTP admission/normal-renderer contracts; no network/model/GPU work."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import q03_http_service as H


class AdmissionTests(unittest.TestCase):
    def manifest(self):
        return {'window_seconds': 10800, 'preflight': {'original_service_disposition': 'keep-stopped'},
                'jobs': [{'kind': 'http_service', 'timeout_seconds': 7200,
                          'selection': {'mode': 'off', 'steering': 'off'}}]}

    def test_typed_closed_job_and_keep_stopped(self):
        H.validate_jobs(self.manifest())
        variants = []
        for value in (True, 0, 7201, 60.5, '60'):
            item = self.manifest(); item['jobs'][0]['timeout_seconds'] = value; variants.append(item)
        for value in (True, 10800.0, 10799):
            item = self.manifest(); item['window_seconds'] = value; variants.append(item)
        item = self.manifest(); item['jobs'][0]['argv'] = ['custom']; variants.append(item)
        item = self.manifest(); item['preflight']['original_service_disposition'] = 'restore'; variants.append(item)
        item = self.manifest(); item['jobs'][0]['selection']['steering'] = 'custom'; variants.append(item)
        for item in variants:
            with self.subTest(item=item), self.assertRaises(RuntimeError): H.validate_jobs(item)

    def test_fixed_off_off_dual_sequence_does_not_advance_on_failed_arm(self):
        with tempfile.TemporaryDirectory() as temp:
            session = SimpleNamespace(directory=Path(temp), manifest=self.manifest(), state={'attempt_results': []})
            with mock.patch.object(H, 'read_owned_bytes', lambda path: Path(path).read_bytes()):
                for index, mode in enumerate(('off', 'off', 'dual')):
                    self.assertEqual(H.next_selection(session), {'mode': mode, 'steering': 'off'})
                    session.state['attempt_results'].append({'outcome': 'failed', 'passed_gates': []})
                    self.assertEqual(H.next_selection(session)['mode'], mode)
                    ident = str(index) * 32
                    path = session.directory / 'attempts' / ident / 'descriptor.json'
                    path.parent.mkdir(parents=True)
                    path.write_bytes(H.canonical({'jobs': [{'selection': {'mode': mode, 'steering': 'off'}}]}))
                    session.state['attempt_results'].append({'outcome': 'passed', 'passed_gates': ['http_service'], 'attempt_id': ident})
                with self.assertRaisesRegex(RuntimeError, 'complete'): H.next_selection(session)

    def test_owner_contract_is_not_reinterpreted_in_place(self):
        source = self.manifest(); source['http_service'] = {'owner_inputs': {'immutable': True}}
        before = copy.deepcopy(source)
        converted = H.owner_manifest(source)
        self.assertEqual(source, before)
        self.assertEqual(converted['jobs'][0]['kind'], 'owner_model')
        self.assertEqual(converted['owner_model'], {'immutable': True})
        self.assertFalse(H.owner.is_owner(source))


class OwnerMatrixIdentityTests(unittest.TestCase):
    def check_matrix(self, mutation=None):
        candidate = {'image': H.IMAGE, 'component_head': H.SOURCE_HEAD,
                     'runtime_manifest_sha256': H.RUNTIME_SHA}
        current = {'tests': {'tree': H.owner.TEST_TREE, 'fixture_sha256': H.owner.TEST_SHA,
                             'path': '/current/tests'},
                   'profile': {'path': '/current/profile.json', 'sha256': '1' * 64},
                   'effective_config': {'path': '/current/effective.json', 'sha256': '2' * 64},
                   'nodes': {'path': '/current/nodes.json', 'sha256': '3' * 64}}
        prior = copy.deepcopy(current)
        for key in ('tests', 'profile', 'effective_config', 'nodes'):
            prior[key]['path'] = '/prior/relocated/' + key
        if mutation is not None:
            mutation(prior)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def pinned(name, value):
                raw = H.canonical(value)
                path = root / (name + '.json')
                path.write_bytes(raw)
                return {'path': str(path), 'sha256': H.digest(raw)}
            capsule = pinned('capsule', {'window_id': 'a' * 32,
                'manifest': {'candidate': candidate, 'owner_model': prior}})
            matrix = {'capsule': capsule, 'terminals': []}
            for index, (mode, steering) in enumerate(
                    (m, s) for m in H.owner.MODES for s in H.owner.STEERING):
                ident = f'{index:032x}'
                descriptor = {'window_id': 'a' * 32, 'attempt_id': ident,
                    'capsule_sha256': capsule['sha256'], 'jobs': [{'kind': 'owner_model',
                        'selection': {'mode': mode, 'steering': steering}}]}
                terminal = {'window_id': 'a' * 32, 'attempt_id': ident,
                    'descriptor_sha256': H.digest(H.canonical(descriptor)),
                    'outcome': 'passed', 'passed_gates': ['owner_model'],
                    'quiescence': {'quiescent': True, 'node_count': 4}}
                matrix['terminals'].append({'descriptor': pinned('descriptor-' + ident, descriptor),
                                            'terminal': pinned('terminal-' + ident, terminal)})
            H.validate_owner_matrix({'owner_matrix': matrix, 'owner_inputs': current}, candidate)

    def test_same_fixture_and_semantic_inputs_allow_relocated_private_paths(self):
        self.check_matrix()

    def test_different_qualification_fixture_tree_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'qualification fixture'):
            self.check_matrix(lambda p: p['tests'].update(tree='0' * 40))

    def test_different_qualification_fixture_bytes_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'qualification fixture'):
            self.check_matrix(lambda p: p['tests'].update(fixture_sha256='0' * 64))

    def test_different_semantic_profile_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'qualification profile'):
            self.check_matrix(lambda p: p['profile'].update(sha256='0' * 64))

    def test_different_effective_config_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'qualification effective_config'):
            self.check_matrix(lambda p: p['effective_config'].update(sha256='0' * 64))

    def test_different_physical_node_map_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'qualification nodes'):
            self.check_matrix(lambda p: p['nodes'].update(sha256='0' * 64))


class NormalRendererTests(unittest.TestCase):
    def setUp(self):
        self.binding = {
            'profile_value': {'container': {'image': H.IMAGE, 'name': 'normal', 'mounts': [
                ['/fixture/models', '/models', 'ro'], ['/original/cache', '/cache', 'rw']],
                'docker_run_args': ['--network=host']}, 'host': {'home': '/fixture'},
                'environment': {'PYTHONPATH': '/image/base'}, 'fabric': {'transport': 'oneshot-ring4'},
                'distributed': {'master_addr': '192.0.2.1', 'master_port': '9999'},
                'serve_args': ['/models', '--host', '0.0.0.0', '--port', '8000',
                    '--max-model-len', '1048576', '--max-num-seqs', '16', '--max-num-batched-tokens', '8192',
                    '--kv-cache-memory-bytes', '11274289152', '--tensor-parallel-size', '4', '--nnodes', '4',
                    '--additional-config', '{"ds41_steering":{"fixed":"profile"}}']},
            'candidate': {'pytest_base_pythonpath': '/image/base', 'runtime_manifest_path': '/fixture/runtime',
                          'build_receipt_path': '/fixture/receipt'},
            'tests': {'path': '/fixture/tests'}, 'checkout': '/fixture/controller', 'master_port': 29633,
            'independent_root': '/fixture/independent',
            'http': {'api_port': 18000, 'startup_seconds': 3600, 'client_seconds': 180,
                     'toolbox': {'path': '/fixture/toolbox'}},
            'toolbox_inventory': {'head': 'c' * 40, 'files': {},
                                 'prompts': {'name': 'default', 'count': 256, 'sha256': 'd' * 64}}}
        self.spark = H.owner.load_spark()
        self.base = {'schema_version': 1, 'window_id': 'a' * 32, 'attempt_id': 'b' * 32,
                     'spec': {'handshake': {}}, 'spec_sha256': 'e' * 64,
                     'binding': self.binding, 'plan_sha256': 'f' * 64, 'ranks': []}
        for rank in range(4):
            node = {'rank': rank, 'head': rank == 0, 'management_ip': f'192.0.2.{rank+1}',
                    'roce_peer_hcas': {str((rank+1)%4): ['eth1'], str((rank-1)%4): ['eth2']}}
            self.base['ranks'].append({'rank': rank, 'node': node})

    def plan(self, mode='off', steering='off'):
        with mock.patch.object(H.owner, 'owner_plan', side_effect=lambda *a: copy.deepcopy(self.base)), \
             mock.patch.object(H.owner, 'load_spark', return_value=self.spark), \
             mock.patch.object(H, 'read_owned_bytes', return_value=b'{"image_labels":{}}'):
            return H.http_plan(self.binding, 'a' * 32, 'b' * 32, {'mode': mode, 'steering': steering})

    def test_actual_renderer_is_inert_and_all_four_normal_commands_remain_bound(self):
        original = copy.deepcopy(self.binding)
        for mode in ('off', 'dual'):
            for steering in ('off', 'loaded_zero', 'active'):
                plan = self.plan(mode, steering)
                self.assertEqual(len(plan['ranks']), 4)
                for row in plan['ranks']:
                    self.assertEqual(row['create_prefix'][:2], ['docker', 'create'])
                    self.assertNotIn('-d', row['create_prefix'])
                    argv = row['python_argv']
                    self.assertEqual(argv[:4], ['-B', '/q03-http/q03_http_worker.py', 'serve', '/models'])
                    self.assertEqual('--headless' in argv, row['rank'] != 0)
                    self.assertEqual('--additional-config' in argv, steering != 'off')
                    self.assertEqual(argv[argv.index('--node-rank')+1], str(row['rank']))
                    self.assertEqual(argv[argv.index('--max-model-len')+1], '1048576')
                    self.assertNotIn('PYTHONPATH', row['environment'])
                    self.assertEqual(row['environment']['Q03_HTTP_WINDOW'], 'a' * 32)
                    self.assertEqual(row['environment']['VLLM_DS41_Q03_MHC_MXFP8'], '1' if mode == 'dual' else '0')
                    self.assertTrue(all(not source.startswith('/original') for source, _, _ in row['mounts']))
        self.assertEqual(self.binding, original)

    def test_capacity_changes_and_foreign_worker_extension_refused(self):
        original = copy.deepcopy(self.binding['profile_value']['serve_args'])
        for flag in ('--max-model-len', '--max-num-seqs', '--max-num-batched-tokens',
                     '--kv-cache-memory-bytes', '--tensor-parallel-size', '--nnodes'):
            args = copy.deepcopy(original); args[args.index(flag)+1] = '1'
            self.binding['profile_value']['serve_args'] = args
            with self.subTest(flag=flag), self.assertRaises(RuntimeError): self.plan()
        self.binding['profile_value']['serve_args'] = [*original, '--worker-extension-cls', 'foreign.module']
        with self.assertRaises(RuntimeError): self.plan()


if __name__ == '__main__':
    unittest.main()
