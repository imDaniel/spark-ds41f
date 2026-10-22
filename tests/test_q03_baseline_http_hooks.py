"""New assembled HTTP hook checks; no model, devices, transport or full suite.

External observations are synthetic. These exercise the real controller hooks,
not the unchanged bridge renderer or the complete legacy benchmark validator.
"""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import q03_http_service as H
import q03_owner_model as O
import q03_held_window as W
import q03_baseline_identity as I
import q03_legacy_case_replay as L


class Journal:
    def __init__(self, root, ranks):
        self.directory = root
        self.state = {'ranks': ranks, 'sequence': 0}
        self.events = []

    def event(self, kind, **fields):
        self.state['sequence'] += 1
        event = dict(sequence=self.state['sequence'], kind=kind, window_id='a'*32, attempt_id='b'*32, **fields)
        self.events.append(event)
        (self.directory / f"{self.state['sequence']:06d}.json").write_bytes(H.canonical(event))

    def save(self):
        (self.directory / 'state.json').write_bytes(H.canonical(self.state))


class Hooks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'http').mkdir(); (self.root / 'results').mkdir()
        self.ranks = [dict(rank=i, container_id=f'{i+1:064x}', config_sha256=f'{i+5:064x}') for i in range(4)]
        self.plan = dict(window_id='a'*32, attempt_id='b'*32, plan_sha256='c'*64,
            url='http://127.0.0.1:29631', image=I.IDENTITIES['r6x188']['image'],
            source_head=I.IDENTITIES['r6x188']['source_head'], selection={'arm':'r6x188','steering':'unloaded'},
            binding={'http':{'schema_version':6}}, client={'legacy_case':{}, 'toolbox_head':'f'*40,
                'output':str(self.root/'results'/'legacy-case.json'), 'timeout_seconds':600,
                'runtime_context':str(self.root/'results'/'runtime-context.json')})
        self.ops = H.HttpOps.__new__(H.HttpOps)
        self.ops.plan = self.plan
        self.ops.binding = {'checkout':str(ROOT),'http':{'schema_version':6}}
        self.ops.journal = Journal(self.root/'http', copy.deepcopy(self.ranks))
        self.ops.end = 2000
        self.ops.check_authority = mock.Mock()
        self.ops.inspect = mock.Mock(return_value={'State':{'Running':True}})
        self.ops.guard_state = mock.Mock(return_value={})
        self.ops.helper = SimpleNamespace(bound_command=lambda command:command)
        self.ops.transport = mock.Mock(return_value=SimpleNamespace(returncode=0,stdout='GPU-1234abcd, 40\n',stderr=''))
        self.ops.sleep = mock.Mock()
        self.ops.mutate = mock.Mock(return_value='')
        self.ready = H.canonical({'ready':'four actual workers in separate readiness tests'})
        (self.ops.journal.directory/'ready-observed.json').write_bytes(self.ready)
        self.patches=[]
        for patch in (mock.patch.object(H,'read_owned_bytes',side_effect=lambda p:Path(p).read_bytes()),
            mock.patch.object(H,'validate_container'),mock.patch.object(H,'require_guard'),
            mock.patch.object(H.time,'time',return_value=1000),
            mock.patch.object(H.time,'monotonic',side_effect=[10,12]),
            mock.patch.object(H,'baseline_module',return_value=SimpleNamespace(legacy_module=lambda:L,identity_module=lambda:I))):
            patch.start();self.addCleanup(patch.stop);self.patches.append(patch)
        for name in ('run','Popen','check_call','check_output'):
            patch=mock.patch.object(subprocess,name,side_effect=AssertionError('no external process in CPU hook check'))
            patch.start();self.addCleanup(patch.stop)
        self.response=mock.Mock(status=200)
        self.response.__enter__=mock.Mock(return_value=self.response)
        self.response.__exit__=mock.Mock(return_value=False)
        self.response.read.return_value=b'{"success":true}'
        patch=mock.patch.object(H.urllib.request,'urlopen',return_value=self.response)
        self.urlopen=patch.start();self.addCleanup(patch.stop)

    def context(self):
        return self.ops.legacy_runtime_context()

    def retained_result(self):
        for path in self.ops.journal.directory.glob('[0-9][0-9][0-9][0-9][0-9][0-9].json'):path.unlink()
        self.ops.journal.events=[];self.ops.journal.state.update(sequence=0,ranks=copy.deepcopy(self.ranks))
        context=self.context()
        raw=H.canonical({'adapter':'validated independently'})
        path=Path(self.plan['client']['output']);path.write_bytes(raw)
        receipt={k:self.plan[k] for k in ('window_id','attempt_id','plan_sha256')}
        receipt.update(exit_code=0,error=None,output_sha256=H.digest(raw))
        path.with_name('client-receipt.json').write_bytes(H.canonical(receipt))
        self.write_context(context)
        self.ops.journal.event('baseline-runtime-context-pinned',context_sha256=H.digest(H.canonical(context)))
        return context

    def write_context(self, context, *, client=True, controller=True):
        if client:Path(self.plan['client']['runtime_context']).write_bytes(H.canonical(context))
        if controller:(self.ops.journal.directory/'baseline-runtime-context.json').write_bytes(H.canonical(context))

    def collect(self):
        with mock.patch.object(L,'validate_output',return_value={'status':'checked'}) as validator:
            result=self.ops.collect_result()
            self.assertEqual(validator.call_count,1)
        return result

    def test_reset_positive_native_bool_and_original_thermal_contract(self):
        context=self.context()
        self.assertEqual(context['ranks'],self.ranks)
        self.assertEqual(context['ready_sha256'],H.digest(self.ready))
        self.assertEqual(context['admission']['thermal']['wait_seconds'],2)
        self.assertEqual(context['admission']['thermal']['fan_policy'],'UNCHANGED')
        self.assertEqual(len(context['admission']['thermal']['samples']),4)
        self.assertEqual(self.ops.transport.call_count,4)
        for args in self.ops.transport.call_args_list:
            self.assertEqual(args.args[1],['nvidia-smi','--query-gpu=uuid,temperature.gpu','--format=csv,noheader,nounits'])
        request=self.urlopen.call_args.args[0]
        self.assertEqual(request.get_method(),'POST')
        self.assertEqual(request.full_url,self.plan['url']+'/reset_prefix_cache')
        self.assertEqual(request.data,b'')
        self.assertEqual([e['kind'] for e in self.ops.journal.events].count('baseline-prefix-reset'),1)

    def test_http200_does_not_make_false_or_nonboolean_reset_success(self):
        for raw in (b'{"success":false}',b'{"success":null}',b'{"success":1}',b'{}'):
            with self.subTest(raw=raw),mock.patch.object(H.time,'monotonic',side_effect=[10,12]):
                self.response.read.return_value=raw
                with self.assertRaisesRegex(RuntimeError,'positive native success'):self.context()
        self.assertFalse(any(e['kind']=='baseline-prefix-reset' for e in self.ops.journal.events))

    def test_nonfinite_or_negative_thermal_observation_stops_before_reset(self):
        for value in ('nan','inf','-1','unavailable'):
            with self.subTest(value=value),mock.patch.object(H.time,'monotonic',return_value=10):
                self.ops.transport.return_value.stdout='GPU-1234abcd, '+value+'\n'
                with self.assertRaisesRegex(RuntimeError,'thermal'):self.context()
        self.urlopen.assert_not_called()

    def test_thermal_600_second_bound_is_not_extended(self):
        self.ops.transport.return_value.stdout='GPU-1234abcd, 55\n'
        with mock.patch.object(H.time,'monotonic',side_effect=[10,611]):
            with self.assertRaisesRegex(RuntimeError,'thermal wait expired'):self.context()
        self.urlopen.assert_not_called()

    def test_run_client_pins_controller_capture_before_supervised_client(self):
        context=self.context()
        with mock.patch.object(self.ops,'legacy_runtime_context',return_value=context):self.ops.run_client()
        self.assertEqual((self.ops.journal.directory/'baseline-runtime-context.json').read_bytes(),
                         Path(self.plan['client']['runtime_context']).read_bytes())
        event=[e for e in self.ops.journal.events if e['kind']=='baseline-runtime-context-pinned']
        self.assertEqual(len(event),1)
        self.assertEqual(event[0]['context_sha256'],H.digest(H.canonical(context)))
        self.assertEqual(self.ops.mutate.call_count,1)
        args=self.ops.mutate.call_args.args
        self.assertEqual(args[:2],(0,'http_client'))
        self.assertEqual(args[2][:3],['python3','-I','-B'])
        self.assertEqual(args[3],600)

    def test_matching_owned_context_is_accepted(self):
        self.retained_result()
        self.assertEqual(self.collect()['image'],I.IDENTITIES['r6x188']['image'])

    def test_client_only_context_corruption_is_rejected(self):
        context=self.retained_result();context['admission']['thermal']['wait_seconds']=0
        self.write_context(context,controller=False)
        with self.assertRaisesRegex(RuntimeError,'controller-owned capture'):self.collect()

    def test_context_must_match_current_owned_container_or_config(self):
        for key in ('container_id','config_sha256'):
            with self.subTest(key=key),mock.patch.object(H.time,'monotonic',side_effect=[10,12]):
                self.retained_result();self.ops.journal.state['ranks'][0][key]='f'*64
                with self.assertRaisesRegex(RuntimeError,'owned journal'):self.collect()

    def test_changed_owned_ready_bytes_or_context_ready_hash_are_rejected(self):
        context=self.retained_result()
        (self.ops.journal.directory/'ready-observed.json').write_bytes(b'changed ready')
        with self.assertRaisesRegex(RuntimeError,'owned journal'):self.collect()
        (self.ops.journal.directory/'ready-observed.json').write_bytes(self.ready)
        context['ready_sha256']='f'*64;self.write_context(context)
        with self.assertRaisesRegex(RuntimeError,'durable controller event'):self.collect()

    def test_matching_thermal_edits_cannot_replace_journal_pinned_capture(self):
        context=self.retained_result()
        context['admission']['thermal']['samples'][0]['hottest_c']=1
        self.write_context(context)
        with self.assertRaisesRegex(RuntimeError,'durable controller event'):self.collect()

    def test_missing_duplicate_or_foreign_context_pin_event_is_rejected(self):
        self.retained_result()
        pins=list(self.ops.journal.directory.glob('[0-9][0-9][0-9][0-9][0-9][0-9].json'))
        path=next(p for p in pins if json.loads(p.read_bytes())['kind']=='baseline-runtime-context-pinned')
        raw=path.read_bytes();path.unlink()
        with self.assertRaisesRegex(RuntimeError,'durable controller event'):self.collect()
        path.write_bytes(raw)
        duplicate=self.ops.journal.directory/'999999.json';duplicate.write_bytes(raw)
        with self.assertRaisesRegex(RuntimeError,'durable controller event'):self.collect()
        duplicate.unlink()
        event=json.loads(raw);event['window_id']='f'*32;path.write_bytes(H.canonical(event))
        with self.assertRaisesRegex(RuntimeError,'durable controller event'):self.collect()

    def test_guard_dispatch_uses_http_lane_contract_not_native197_fixture(self):
        expected={'files':[{'path':'/independent/memguard-start.sh','sha256':'a'*64}]}
        with mock.patch.object(H,'input_contract',return_value=expected) as http_contract, \
             mock.patch.object(O,'input_contract',side_effect=AssertionError('197 contract must not be borrowed')):
            self.assertIs(self.ops.guard_input_contract(),expected)
            http_contract.assert_called_once_with(self.ops.binding)
        plain=O.OwnerOps.__new__(O.OwnerOps);plain.binding={'existing':'unchanged'}
        with mock.patch.object(O,'input_contract',return_value=expected) as original:
            self.assertIs(plain.guard_input_contract(),expected)
            original.assert_called_once_with(plain.binding)

    def test_actual_guard_action_resolves_only_http_approved_guard_bytes(self):
        self.plan['ranks']=[{} for _ in range(4)]
        expected={'files':[{'path':str(ROOT/'scripts/memguard-start.sh'),'sha256':'a'*64},
            {'path':str(ROOT/'scripts/memguard.sh'),'sha256':'b'*64},
            {'path':'/not/a/guard.py','sha256':'c'*64}]}
        with mock.patch.object(H,'input_contract',return_value=expected), \
             mock.patch.object(O,'input_contract',side_effect=AssertionError('old fixture contract called')):
            for phase in ('startup_guard','steady_guard'):
                self.ops.action(0,phase,self.ranks[0])
                args=self.ops.mutate.call_args.args
                self.assertEqual(args[:2],(0,phase))
                self.assertEqual(args[2][:5],['python3','-I','-B','-c',O.GUARD_EXEC])
                payload=json.loads(args[2][5])
                self.assertEqual(payload['files'],expected['files'][:2])
                self.assertEqual(payload['arguments'][0],self.ranks[0]['container_id'])
                self.assertEqual(args[3],30)

    def test_legacy_schemas_keep_exact_native197_image(self):
        with mock.patch.object(H,'baseline_module',side_effect=AssertionError('old schema must not load baseline')):
            for schema in (1,2,3,4):
                self.assertEqual(H.plan_image({'binding':{'http':{'schema_version':schema}},'image':'arbitrary'}),H.IMAGE)
        self.assertFalse(H.is_baseline({'http':{'schema_version':6.0}}))
        self.assertFalse(H.is_baseline({'http':{'schema_version':True}}))
        self.assertEqual(H.plan_image(self.plan),I.IDENTITIES['r6x188']['image'])
        changed=copy.deepcopy(self.plan);changed['image']=H.IMAGE
        with self.assertRaisesRegex(RuntimeError,'plan image differs'):H.plan_image(changed)


class SourceOnlyHooks(unittest.TestCase):
    def test_bridge_and_identity_ignore_import_cache_and_shadow_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ('q03_baseline_service','q03_baseline_identity'):
                (root/(name+'.py')).write_bytes((ROOT/'scripts'/(name+'.py')).read_bytes())
                (root/(name+'.pyc')).write_bytes(b'ignored shadow')
                (root/(name+'.so')).write_bytes(b'ignored native shadow')
            bridge_raw=(root/'q03_baseline_service.py').read_bytes()
            def read(path):return Path(path).read_bytes()
            with mock.patch.object(H,'__file__',str(root/'q03_http_service.py')), \
                 mock.patch.object(H,'BASELINE_SOURCE_SHA256',H.digest(bridge_raw)), \
                 mock.patch.object(H,'read_owned_bytes',side_effect=read),mock.patch.object(W,'read_owned_bytes',side_effect=read), \
                 mock.patch.dict(sys.modules,{'q03_baseline_service':SimpleNamespace(poisoned=True),
                    'q03_baseline_identity':SimpleNamespace(poisoned=True)}):
                bridge=H.baseline_module()
                self.assertTrue(callable(bridge.http_plan))
                self.assertFalse(hasattr(bridge,'poisoned'))
                # Runtime authoring may still be changing the integration file;
                # patch only this test's expected pin, never production admission.
                bridge.IDENTITY_SOURCE_SHA256=H.digest((root/'q03_baseline_identity.py').read_bytes())
                identity=bridge.identity_module()
                self.assertEqual(identity.checked_identity('r6x188'),I.checked_identity('r6x188'))
                (root/'q03_baseline_identity.py').write_bytes(b'raise AssertionError("must not execute")')
                with self.assertRaisesRegex(RuntimeError,'source pin'):bridge.identity_module()
                (root/'q03_baseline_service.py').write_bytes(b'raise AssertionError("must not execute")')
                with self.assertRaisesRegex(RuntimeError,'bridge source pin'):H.baseline_module()


if __name__=='__main__':unittest.main()
