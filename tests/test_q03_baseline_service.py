"""Focused CPU bridge tests, not image, model, site or performance evidence."""
import copy
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import q03_baseline_service as B

ARMS = {'r6x188': ('sha256:' + '8' * 64, 'a' * 40),
        'native197-off': ('sha256:' + '9' * 64, 'b' * 40)}
STATES = ('unloaded', 'loaded0', 'loaded1', 'loaded2', 'loaded3', 'loaded4')


class Identity:
    def validate_selection(self, value):
        B.require(isinstance(value, dict) and set(value) == {'arm', 'steering'}
            and value['arm'] in ARMS and value['steering'] in STATES, 'invalid finite selection')
        return value

    def validate_binding(self, value, candidate=None):
        image, head = ARMS[value['arm']]
        return {'identity': {'arm': value['arm'], 'image': image, 'source_head': head},
                'runtime': {}, 'provenance': value['provenance']}

    def input_contract(self, value):
        checked = self.validate_binding(value)
        return dict(files=[checked['provenance']], identity=checked['identity'], runtime={})


class Tests(unittest.TestCase):
    def setUp(self):
        self.identity = Identity()
        self.patch = mock.patch.object(B, 'identity_module', return_value=self.identity)
        self.patch.start(); self.addCleanup(self.patch.stop)

    def manifest(self):
        return {'window_seconds': 10800, 'jobs': [{'kind': 'http_service', 'timeout_seconds': 7200,
            'selection': {'arm': 'r6x188', 'steering': 'unloaded'}}],
            'preflight': {'original_service_disposition': 'keep-stopped'}}

    def binding(self, arm='r6x188', steering='unloaded'):
        selected = {'arm': arm, 'steering': steering}
        pin = {'path': '/fixture/runtime.json', 'sha256': 'd' * 64}
        identity = {'schema_version': 1, 'arm': arm, 'provenance': pin}
        checked = self.identity.validate_binding(identity)
        checked['image_observation'] = {'image_labels': {'actual': arm}}
        profile = {'shape': 'tp4-ring', 'fabric': {'transport': 'oneshot-ring4'},
            'container': {'name': 'original', 'image': 'original-image', 'mounts': [
                ['/fixture/models', '/models', 'ro'], ['/fixture/steering', '/steering', 'ro'],
                ['/original/cache', '/cache', 'rw']], 'docker_run_args': ['--network=host']},
            'host': {'home': '/fixture'}, 'environment': {'PYTHONPATH': '/image/base'},
            'distributed': {'master_addr': '192.0.2.1', 'master_port': '9999'},
            'serve_args': ['/models', '--host', '0.0.0.0', '--port', '8000',
                '--max-model-len', '1048576', '--max-num-seqs', '16', '--max-num-batched-tokens', '8192',
                '--kv-cache-memory-bytes', '11274289152', '--tensor-parallel-size', '4', '--nnodes', '4',
                '--additional-config', B.canonical(B.owner.FIXED_ENGINE_KWARGS['additional_config']).decode()]}
        nodes = [{'rank': rank, 'head': rank == 0, 'management_ip': f'192.0.2.{rank+1}',
            'roce_peer_hcas': {str((rank+1)%4): ['eth1'], str((rank-1)%4): ['eth2']}}
            for rank in range(4)]
        return dict(profile_value=profile, nodes_value={'nodes': nodes},
            effective_value={'off': {'loaded': False}, 'loaded_zero': {'loaded': True}},
            candidate=dict(image=ARMS[arm][0], component_head=ARMS[arm][1],
                runtime_manifest_path=pin['path'], runtime_manifest_sha256=pin['sha256'],
                pytest_base_pythonpath='/image/base', cache_path='/new/cache', output_path='/new/output'),
            http=dict(schema_version=6, baseline_identity=identity, api_port=18000, client_seconds=600,
                startup_seconds=3600, toolbox={'path': '/fixture/toolbox', 'git_config_sha256': 'c' * 64},
                legacy_case={'corpus': {'path': '/fixture/corpus.json', 'sha256': 'e' * 64}}),
            baseline_identity=checked, selection=selected, checkout=str(ROOT), independent_root='/new',
            master_port=29633, handoff_seconds=600, deployment_git_config_sha256='e'*64, profile={'path':'/profile','sha256':'1'*64},
            nodes={'path':'/nodes','sha256':'2'*64}, effective_config={'path':'/effective','sha256':'3'*64},
            preflight={'publication': {'sha':'f'*40,'url':'https://example.test/repo.git'}},
            toolbox_inventory={'head': 'c'*40, 'files': {}, 'prompts': {'name':'default','count':256,'sha256':'a'*64}})

    def plan(self, binding):
        case = SimpleNamespace(client_plan=lambda b, s: {'selection': s})
        with mock.patch.object(B, 'legacy_module', return_value=case), \
             mock.patch.object(B, 'read_owned_bytes', return_value=b'fixed-source'):
            return B.http_plan(binding, 'a'*32, 'b'*32, binding['selection'])

    def test_closed_selection_and_bounds(self):
        B.validate_jobs(self.manifest())
        mutations = [lambda v: v['jobs'][0].update(argv=['custom']),
            lambda v: v['jobs'][0]['selection'].update(mode='off'),
            lambda v: v['jobs'][0]['selection'].update(arm='arbitrary'),
            lambda v: v['jobs'][0]['selection'].update(steering='loaded5'),
            lambda v: v['preflight'].update(original_service_disposition='restore'),
            lambda v: v.update(image_preparation={})]
        for change in mutations:
            value=self.manifest(); change(value)
            with self.assertRaises(RuntimeError): B.validate_jobs(value)
        for value in (True, 0, 7201, 7200.0, '600'):
            item=self.manifest(); item['jobs'][0]['timeout_seconds']=value
            with self.assertRaises(RuntimeError): B.validate_jobs(item)
        for value in (True, 10800.0, 10799):
            item=self.manifest(); item['window_seconds']=value
            with self.assertRaises(RuntimeError): B.validate_jobs(item)

    def test_one_selected_case_does_not_claim_full_matrix(self):
        session=SimpleNamespace(manifest=self.manifest(), state={'attempt_results':[]})
        expected=session.manifest['jobs'][0]['selection']
        self.assertEqual(B.next_selection(session),expected)
        session.state['attempt_results'].append({'outcome':'failed','passed_gates':[]})
        self.assertEqual(B.next_selection(session),expected)
        session.state['attempt_results'].append({'outcome':'passed','passed_gates':['http_service']})
        with self.assertRaisesRegex(RuntimeError,'complete'): B.next_selection(session)

    def test_actual_normal_renderer_all_twelve_states_and_owned_delta(self):
        for arm in ARMS:
            for state in STATES:
                with self.subTest(arm=arm,state=state):
                    binding=self.binding(arm,state); before=copy.deepcopy(binding)
                    plan=self.plan(binding)
                    self.assertEqual(binding,before)
                    self.assertEqual(plan['image'],ARMS[arm][0])
                    self.assertEqual(plan['source_head'],ARMS[arm][1])
                    self.assertEqual(plan['selection'],binding['selection'])
                    self.assertEqual(len(plan['ranks']),4)
                    self.assertEqual(plan['spec']['expected_effective_config'],{'loaded':state!='unloaded'})
                    for row in plan['ranks']:
                        self.assertEqual(row['create_prefix'][:2],['docker','create'])
                        self.assertNotIn('-d',row['create_prefix'])
                        self.assertEqual(row['python_argv'][:4],['-B','/q03-http/q03_baseline_worker.py','serve','/models'])
                        self.assertEqual('--headless' in row['python_argv'],row['rank']!=0)
                        self.assertEqual('--additional-config' in row['python_argv'],state!='unloaded')
                        self.assertEqual(row['environment']['VLLM_DS41_Q03_MHC_MXFP8'],'0')
                        self.assertNotIn('PYTHONPATH',row['environment'])
                        self.assertFalse(any('/validation/tests' in mount for mount in row['mounts']))
                        self.assertFalse(any('owner.py' in str(mount) for mount in row['mounts']))
                        self.assertNotIn('/original/cache',str(row['mounts']))
                        self.assertIn(['/fixture/models','/models','ro'],row['mounts'])
                        self.assertIn(['/fixture/steering','/steering','ro'],row['mounts'])
                        self.assertEqual(row['environment']['Q03_HTTP_SPEC_SHA256'],plan['spec_sha256'])
                    retained=copy.deepcopy(plan); sha=retained.pop('plan_sha256')
                    self.assertEqual(B.digest(B.canonical(retained)),sha)

    def test_arm_and_steering_cannot_change_within_capsule(self):
        binding=self.binding()
        for selection in ({'arm':'native197-off','steering':'unloaded'}, {'arm':'r6x188','steering':'loaded0'}):
            with self.assertRaisesRegex(RuntimeError,'immutable'):
                B.http_plan(binding,'a'*32,'b'*32,selection)

    def test_capacity_or_custom_extension_cannot_change(self):
        for flag in ('--max-model-len','--max-num-seqs','--max-num-batched-tokens',
                     '--kv-cache-memory-bytes','--tensor-parallel-size','--nnodes'):
            binding=self.binding(); args=binding['profile_value']['serve_args']; args[args.index(flag)+1]='999'
            with self.assertRaisesRegex(RuntimeError,'capacity/topology'): self.plan(binding)
        binding=self.binding(); binding['profile_value']['serve_args'] += ['--worker-extension-cls','foreign.Worker']
        with self.assertRaisesRegex(RuntimeError,'unreviewed'): self.plan(binding)

    def test_readonly_assets_never_become_writable(self):
        binding=self.binding();binding['profile_value']['container']['mounts'][0][2]='rw'
        with self.assertRaisesRegex(RuntimeError,'writable mount'): self.plan(binding)

    def test_independent_input_contract_has_no_owner_fixture_or_native197_prerequisite(self):
        binding=self.binding()
        legacy=SimpleNamespace(validate_binding=lambda *a: {}, mounted_asset_files=lambda *a: [], artifact_files=lambda *a: [])
        with mock.patch.object(B,'read_owned_bytes',return_value=b'fixed'), mock.patch.object(B,'legacy_module',return_value=legacy):
            head=B.input_contract(binding)
            follower=B.input_contract(binding,include_toolbox=False)
        self.assertNotIn('tests',head)
        self.assertNotIn('base_head',head)
        self.assertNotIn('fixture',head)
        self.assertEqual(head['identity']['arm'],'r6x188')
        self.assertEqual(head['image_observation']['image_labels'],{'actual':'r6x188'})
        self.assertIn('/fixture/toolbox',head['directories'])
        self.assertNotIn('/fixture/toolbox',follower['directories'])
        self.assertIn(binding['http']['legacy_case']['corpus'],head['files'])
        self.assertNotIn(binding['http']['legacy_case']['corpus'],follower['files'])

    def test_source_only_loader_rejects_changed_bytes(self):
        with mock.patch.object(B,'read_owned_bytes',return_value=b'bad-source'):
            with self.assertRaisesRegex(RuntimeError,'source pin'):
                B._module('q03_baseline_identity','0'*64)

    def test_path_overlap_and_aliases_are_rejected_before_writes(self):
        manifest=self.manifest()
        manifest.update(independent_root='/new',candidate={'cache_path':'/new/cache','output_path':'/new/output'})
        with mock.patch.object(B,'_immutable',return_value=[Path('/new/input')]), \
             mock.patch('q03_original_spark_adapter.evidence_path',side_effect=lambda p,m: Path(p)):
            B.validate_paths(manifest,'/new/session')
            for path in ('/new/input','/new/input/child','/new'):
                changed=copy.deepcopy(manifest);changed['candidate']['output_path']=path
                with self.assertRaisesRegex(RuntimeError,'overlap'):
                    B.validate_paths(changed,'/new/session')
            changed=copy.deepcopy(manifest);changed['candidate']['output_path']='/new/cache'
            with self.assertRaisesRegex(RuntimeError,'overlap'):
                B.validate_paths(changed,'/new/session')
        with mock.patch.object(B,'_immutable',return_value=[Path('/new/../alias')]):
            with self.assertRaisesRegex(RuntimeError,'aliases'):
                B.validate_paths(manifest,'/new/session')

    def test_registered_asset_data_and_strict_provenance_are_distinct(self):
        binding=self.binding()
        binding['preflight'].update(protected_roots=['/fixture'],hold_path='/old/hold',request_path='/old/request',
            legacy_hold_path='/old/legacy-hold',legacy_request_path='/old/legacy-request',policy_path='/old/policy',
            registered_policy={'repository':'/old/repo'},remote_authority_path='/new/authority',remote_authority_sha256='f'*64)
        provenance={'path':'/new/frozen/weights.json','sha256':'f'*64}
        asset={'path':'/fixture/models/config.json','sha256':'e'*64}
        legacy=SimpleNamespace(validate_binding=lambda *a: {}, mounted_asset_files=lambda *a:[asset],
            artifact_files=lambda *a:[provenance])
        with mock.patch.object(B,'read_owned_bytes',return_value=b'fixed'), mock.patch.object(B,'legacy_module',return_value=legacy):
            head=B.boundary_contract(binding,{'rank':1,'expected_hostname':'head'},'/new/http-resources')
            follower=B.boundary_contract(binding,{'rank':2,'expected_hostname':'follower'},'/new/http-resources')
        self.assertEqual(head['site_readonly_roots'],['/fixture/models','/fixture/steering'])
        self.assertNotIn('/fixture/models',head['strict_input_directories'])
        self.assertIn(provenance,head['input_files'])
        self.assertNotIn(asset,head['input_files'])
        self.assertNotIn(provenance,follower['input_files'])
        self.assertIn('/fixture/toolbox',head['strict_input_directories'])
        self.assertNotIn('/fixture/toolbox',follower['strict_input_directories'])

    def test_prepare_is_existing_generic_fixed_body(self):
        self.assertIs(B.PREPARE,B.owner.PREPARE)
        self.assertNotIn('fixture',B.PREPARE)

    def test_host_never_prepares_images(self):
        with self.assertRaisesRegex(RuntimeError,'not supported'):
            B.validate_host({},None,prepare_images=True)


if __name__ == '__main__':
    unittest.main()
