"""Held-loop CPU fault tests. Fake process/Docker boundaries, real temp journals."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('q03_held_window',ROOT/'scripts/q03_held_window.py')
H=importlib.util.module_from_spec(spec);sys.modules[spec.name]=H;spec.loader.exec_module(H)

class FakeSession:
    def __init__(self,root):
        self.directory=root/'session';self.directory.mkdir()
        for name in ('controls','attempts'):(self.directory/name).mkdir()
        self.manifest={'held_window':{'schema_version':1},'window_seconds':10800,'independent_root':str(root),
            'candidate':{'image':'sha256:'+'a'*64,'cache_path':str(root/'cache'),'output_path':str(root/'output')},
            'jobs':[{'kind':'p0','timeout_seconds':60},{'kind':'g1','timeout_seconds':60},{'kind':'g2','timeout_seconds':60}]}
        for key in ('cache_path','output_path'):Path(self.manifest['candidate'][key]).mkdir()
        self.capsule={'window_id':'b'*32,'deadline_epoch':10900,'recovery_source_sha256':{'core':'a'*64}}
        raw=H.canonical_bytes(self.capsule);(self.directory/'capsule.json').write_bytes(raw)
        (self.directory/'capsule.sha256').write_text(H.sha256(raw))
        self.state={'phase':'held-ready','held_sequence':0,'active_process':None,'active_attempt':None,'last_attempt_receipt_sha256':None,'attempt_receipt_sha256':{}}
        self.events=[];self.fail=None;self.foreign=False;self.request=False;self.drift=False
        self.ops=SimpleNamespace(recovery_sources=lambda:{'core':('c' if self.drift else 'a')*64},
            validate_host=lambda m:self.event('validate-host'),prepare_candidate=self.prepare,
            execute=self.execute,validate_result=self.result,
            cleanup_held_candidates=lambda s,attempt_id=None:self.event('cleanup'),
            terminate_owned_process=lambda p:self.event('terminate'))
    def event(self,event):
        self.events.append(event)
        if self.fail==event:raise RuntimeError('injected '+event)
    def update(self,**values):self.state.update(values);(self.directory/'state.json').write_text(json.dumps(self.state))
    def check(self,phase):self.event('check:'+phase);return {'node_count':4}
    def owns_hold(self):return not self.foreign
    def requested(self):return self.request
    def beat(self):self.event('heartbeat')
    def hold_status(self):return 'foreign' if self.foreign else 'owned'
    def prepare(self,s,name,argv,held_attempt):
        assert s.state['active_attempt']['attempt_id']==held_attempt['attempt_id']
        assert name in s.state['candidate_containers']
        self.event('prepare:'+held_attempt['kind']);return argv
    def execute(self,s,argv,**kwargs):
        kwargs['poll']();self.event('execute:'+argv[1])
    def result(self,manifest,kind):self.event('result:'+kind);return {'kind':kind,'passed':True}

class HeldContracts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.s=FakeSession(self.root)
        self.core=SimpleNamespace(job_command=lambda m,k,w,attempt_id=None:
            ('candidate-'+attempt_id+'-'+k,['inert',k]),validate_layout=lambda m,p:None,g1_tests_path=lambda c:c['g1_tests']['path'])
        patch=mock.patch.object(H.time,'time',return_value=100);patch.start();self.addCleanup(patch.stop)
        import subprocess
        for name in ('run','Popen','check_output','check_call'):
            patch=mock.patch.object(subprocess,name,side_effect=AssertionError('external operation forbidden'))
            patch.start();self.addCleanup(patch.stop)
    def descriptor(self,ident='1'*32):
        d={'schema_version':1,'window_id':self.s.capsule['window_id'],
           'capsule_sha256':H.sha256((self.s.directory/'capsule.json').read_bytes()),'attempt_id':ident,
           'previous_receipt_sha256':self.s.state.get('last_attempt_receipt_sha256'),
           'candidate_updates':{},'jobs':copy.deepcopy(self.s.manifest['jobs']),'command_plan_sha256':''}
        commands=H.attempt_commands(self.s.manifest,d['window_id'],ident,self.core)
        d['command_plan_sha256']=H.sha256(H.canonical_bytes(commands));return d
    def admit(self,ident='1'*32):
        d=self.descriptor(ident);path=self.root/(ident+'.json');path.write_bytes(H.canonical_bytes(d))
        return H.admit_attempt(self.s,{'attempt_path':str(path),'attempt_sha256':H.sha256(path.read_bytes())},self.core)
    def test_pass_drains_without_intermediate_restore(self):
        a=self.admit();receipt=H.run_attempt(self.s,a,self.core)
        self.assertEqual(receipt['passed_gates'],['p0','g1','g2']);self.assertEqual(self.s.state['phase'],'held-ready')
        self.assertEqual(self.s.capsule['deadline_epoch'],10900)
        self.assertLess(self.s.events.index('cleanup'),self.s.events.index('prepare:g1'))
        self.assertNotIn('restore',self.s.events)
    def test_failure_drains_pauses_shortcircuits_then_fresh_resume(self):
        a=self.admit();self.s.fail='result:g1';receipt=H.run_attempt(self.s,a,self.core)
        self.assertEqual(receipt['outcome'],'failed');self.assertEqual(self.s.state['phase'],'paused-quiescent')
        self.assertNotIn('prepare:g2',self.s.events);before=(a['directory']/'terminal.json').read_bytes()
        self.s.fail=None;b=self.admit('2'*32);self.assertNotEqual(a['directory'],b['directory'])
        H.run_attempt(self.s,b,self.core);self.assertEqual((a['directory']/'terminal.json').read_bytes(),before)
        self.assertEqual(self.s.capsule['deadline_epoch'],10900)
    def test_unknown_cleanup_never_becomes_resumable(self):
        a=self.admit();self.s.fail='cleanup'
        with self.assertRaisesRegex(RuntimeError,'cleanup'):H.run_attempt(self.s,a,self.core)
        self.assertNotIn(self.s.state['phase'],('held-ready','paused-quiescent'))
        self.assertIsNotNone(self.s.state['active_attempt']);self.assertFalse((a['directory']/'terminal.json').exists())
    def test_timeout_pauses_only_after_successful_drain(self):
        a=self.admit();self.s.ops.execute=lambda *a,**k:(_ for _ in ()).throw(TimeoutError('deadline'))
        r=H.run_attempt(self.s,a,self.core);self.assertIn('TimeoutError',r['failure'])
        self.assertEqual(self.s.state['phase'],'paused-quiescent');self.assertIn('terminate',self.s.events)
    def test_request_and_expiry_close_not_pause(self):
        for requested,now in ((True,100),(False,10900)):
            self.s.request=requested
            with mock.patch.object(H.time,'time',return_value=now),self.assertRaises(H.CloseWindow):H.check_open(self.s)
        self.assertEqual(self.s.capsule['deadline_epoch'],10900)
    def test_duplicate_and_stale_controls_cannot_replay(self):
        H.submit_control(self.s,'pause',0)
        with self.assertRaises(FileExistsError):H.submit_control(self.s,'close',0)
        self.assertEqual(H.take_control(self.s)['action'],'pause');self.assertEqual(self.s.state['held_sequence'],1)
        self.assertIsNone(H.take_control(self.s))
        with self.assertRaises(ValueError):H.submit_control(self.s,'resume',0)
    def test_tampered_descriptor_and_command_plan_refuse_before_launch(self):
        path=self.root/'descriptor.json';path.write_bytes(H.canonical_bytes(self.descriptor()));digest=H.sha256(path.read_bytes());path.write_text('{}')
        with self.assertRaises(ValueError):H.admit_attempt(self.s,{'attempt_path':str(path),'attempt_sha256':digest},self.core)
        d=self.descriptor();d['command_plan_sha256']='0'*64
        with self.assertRaises(ValueError):H.build_attempt(self.s,d,self.core)
        self.assertFalse(any(e.startswith('prepare:') for e in self.s.events))
    def test_authority_native_mutation_and_wrong_predecessor_refuse(self):
        for key,value in [('window_id','c'*32),('previous_receipt_sha256','0'*64),('candidate_updates',{'image':'other'}),('jobs',[{'kind':'g2','timeout_seconds':60}])]:
            d=self.descriptor();d[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):H.build_attempt(self.s,d,self.core)
    def test_foreign_hold_source_drift_and_capsule_tamper_refuse(self):
        self.s.foreign=True
        with self.assertRaises(ValueError):H.check_open(self.s)
        self.s.foreign=False;self.s.drift=True
        with self.assertRaises(ValueError):H.check_open(self.s)
        self.s.drift=False;(self.s.directory/'capsule.json').write_text('{}')
        with self.assertRaises(ValueError):H.check_open(self.s)
    def test_pause_during_attempt_requires_drain(self):
        a=self.admit();H.submit_control(self.s,'pause',0);r=H.run_attempt(self.s,a,self.core)
        self.assertEqual(r['passed_gates'],[]);self.assertEqual(self.s.state['phase'],'paused-quiescent')
        self.assertIn('cleanup',self.s.events)
    def test_held_loop_heartbeats_until_close_without_own_restore(self):
        # New loop creates these directories itself; no previous evidence exists.
        (self.s.directory/'attempts').rmdir();(self.s.directory/'controls').rmdir()
        def sleep(_):H.submit_control(self.s,'close',self.s.state['held_sequence'])
        with mock.patch.object(H.time,'sleep',side_effect=sleep):H.held_loop(self.s,self.core)
        self.assertEqual(self.s.state['outcome'],'closed');self.assertIn('heartbeat',self.s.events)
        self.assertNotIn('restore',self.s.events)  # Outer core finally owns restoration.

    def test_atomic_control_publication_never_exposes_partial_json(self):
        path=self.root/'atomic.json';real_link=H.os.link;seen=[]
        def link(source,destination,**kwargs):
            self.assertFalse(path.exists());self.assertEqual(json.loads(Path(source).read_bytes()),{'complete':True})
            seen.append(True);return real_link(source,destination,**kwargs)
        with mock.patch.object(H.os,'link',side_effect=link):H.exclusive_json(path,{'complete':True})
        original=path.read_bytes()
        with self.assertRaises(FileExistsError):H.exclusive_json(path,{'replacement':True})
        self.assertEqual(path.read_bytes(),original);self.assertEqual(seen,[True]);self.assertEqual(list(self.root.glob('.*.tmp')),[])
    def test_prior_terminal_and_descriptor_tamper_block_resume(self):
        a=self.admit();H.run_attempt(self.s,a,self.core)
        for name in ('terminal.json','descriptor.json'):
            path=a['directory']/name;old=path.read_bytes();path.write_bytes(old+b' ')
            with self.subTest(name=name),self.assertRaises(ValueError):H.check_open(self.s)
            path.write_bytes(old)
        H.check_open(self.s)
    def test_policy_is_fixed_typed_lease_and_gate_prefix(self):
        for key,value in (('window_seconds',10800.0),('window_seconds',10801),('held_window',{'schema_version':True}),('jobs',[{'kind':'g1','timeout_seconds':1}])):
            manifest=copy.deepcopy(self.s.manifest);manifest[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):H.validate_held_policy(manifest)

    def test_concurrent_duplicate_controls_publish_exactly_one_request(self):
        import concurrent.futures
        import threading
        barrier=threading.Barrier(2)
        def submit(action):
            barrier.wait()
            try:return ('accepted',H.submit_control(self.s,action,0))
            except FileExistsError:return ('duplicate',None)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(submit,action) for action in ('pause','close')]
            results=[f.result(timeout=5) for f in futures]
        self.assertEqual(sorted(r[0] for r in results),['accepted','duplicate'])
        accepted=H.take_control(self.s)
        self.assertIn(accepted['action'],('pause','close'));self.assertIsNone(H.take_control(self.s))
        self.assertEqual(self.s.state['held_sequence'],1)
    def test_plan_is_not_submission_and_resume_requires_exact_sequence(self):
        (self.s.directory/'plans').mkdir()
        plan=H.plan_attempt(self.s,self.core,through='g1')
        self.assertEqual(plan['status'],'planned-not-submitted')
        self.assertEqual(plan['deadline_epoch'],10900)
        self.assertEqual(self.s.events,[]);self.assertIsNone(H.take_control(self.s))
        for sequence in (True,0.0,-1,1):
            with self.subTest(sequence=sequence),self.assertRaises(ValueError):
                H.submit_control(self.s,'resume',sequence,attempt_path=plan['attempt_path'],attempt_sha256=plan['attempt_sha256'])
        H.submit_control(self.s,'resume',0,attempt_path=plan['attempt_path'],attempt_sha256=plan['attempt_sha256'])
        request=H.take_control(self.s);attempt=H.admit_attempt(self.s,request,self.core)
        self.assertEqual(attempt['descriptor']['jobs'],self.s.manifest['jobs'][:2])
        self.assertEqual(Path(plan['attempt_path']).read_bytes(),H.canonical_bytes(attempt['descriptor']))
    def test_descriptor_schema_rejects_boolean_and_float(self):
        for value in (True,1.0):
            descriptor=self.descriptor();descriptor['schema_version']=value
            with self.subTest(value=value),self.assertRaises(ValueError):H.build_attempt(self.s,descriptor,self.core)

class HeldCoreContracts(unittest.TestCase):
    """Actual core+held loop, with existing explicit inert service/process ops."""
    @classmethod
    def setUpClass(cls):
        import test_q03_window as legacy
        cls.legacy=legacy
        legacy.load_module('q03_window_watchdog','WATCHDOG')
        root=Path(os.environ.get('Q03_HELD_CORE_ROOT',ROOT))
        spec=importlib.util.spec_from_file_location('q03_held_core_contract',root/'scripts/q03_window.py')
        cls.core=importlib.util.module_from_spec(spec);sys.modules[spec.name]=cls.core;spec.loader.exec_module(cls.core)
    def setUp(self):
        factory=tempfile.TemporaryDirectory
        with mock.patch.object(tempfile,'TemporaryDirectory',side_effect=lambda:factory(dir=ROOT)):
            self.legacy.WindowRecoveryTests.setUp(self)
        self.manifest.update(held_window={'schema_version':1},window_seconds=10800)
        for key in ('cache_path','output_path'):Path(self.manifest['candidate'][key]).mkdir()
        self.ops.cleanup_held_candidates=lambda s,attempt_id=None:self.ops.event('cleanup-held')
        old_prepare=self.ops.prepare_candidate
        self.ops.prepare_candidate=lambda s,n,a,held_attempt=None:old_prepare(s,n,a)
        old_execute=self.ops.execute
        def execute(s,a,**kw):
            poll=kw.pop('poll',None)
            if poll:poll()
            return old_execute(s,a,**kw)
        self.ops.execute=execute
        sources=self.ops.recovery_sources()
        self.ops.recovery_sources=lambda:dict(sources,**{'q03_held_window.py':'a'*64})
    def run_controlled(self,failure=None):
        self.ops.failure=failure
        checkpoints=[]
        def control(_):
            s=self.core.Session(self.directory,self.ops)
            checkpoints.append((s.state['phase'],self.hold.exists(),self.ops.events.count('restore:start'),s.capsule['deadline_epoch']))
            if len(checkpoints)==1:
                plan=H.plan_attempt(s,self.core)
                H.submit_control(s,'resume',s.state['held_sequence'],attempt_path=plan['attempt_path'],attempt_sha256=plan['attempt_sha256'])
            else:H.submit_control(s,'close',s.state['held_sequence'])
        with mock.patch.object(H.time,'sleep',side_effect=control):
            result=self.core.run_session(self.manifest,self.directory,self.ops)
        return result,checkpoints
    def test_actual_outer_restores_once_after_pass_and_close(self):
        result,points=self.run_controlled()
        self.assertEqual([p[0] for p in points],['held-ready','held-ready'])
        self.assertTrue(all(p[1] and p[2]==0 for p in points))
        self.assertEqual(len({p[3] for p in points}),1)
        self.assertEqual(self.ops.events.count('restore:start'),1)
        self.assertTrue(result['restored']);self.assertTrue(result['hold_released']);self.assertFalse(self.hold.exists())
    def test_actual_failed_gate_pauses_then_close_restores_once(self):
        result,points=self.run_controlled('result:g1')
        self.assertEqual(points[-1][0],'paused-quiescent')
        self.assertTrue(points[-1][1]);self.assertEqual(points[-1][2],0)
        self.assertEqual(self.ops.events.count('restore:start'),1)
        self.assertEqual(result['attempt_results'][0]['passed_gates'],['p0'])
    def test_unknown_drain_blocks_final_restore_and_retains_hold(self):
        with self.assertRaisesRegex(RuntimeError,'cleanup-held'):self.run_controlled('cleanup-held')
        self.assertTrue(self.hold.exists());self.assertNotIn('restore:start',self.ops.events)
    def test_actual_cleanup_requires_id_and_known_daemon_absence(self):
        ident='c'*64
        state={'held_candidates':[{'attempt_id':'a'*32,'container_id':None}]}
        session=SimpleNamespace(state=state,update=lambda **v:state.update(v))
        ops=SimpleNamespace(run=mock.Mock())
        with self.assertRaisesRegex(RuntimeError,'durable exact ID'):self.core.RuntimeOps.cleanup_held_candidates(ops,session)
        ops.run.assert_not_called();state['held_candidates'][0]['container_id']=ident
        for error in ('permission denied','cannot connect to daemon','No such container: wrong-id'):
            ops.run.return_value=SimpleNamespace(returncode=1,stdout='',stderr=error)
            with self.subTest(error=error),self.assertRaisesRegex(RuntimeError,'unknown'):self.core.RuntimeOps.cleanup_held_candidates(ops,session)
            self.assertNotIn('removed',state['held_candidates'][0])
        ops.run.return_value=SimpleNamespace(returncode=1,stdout='',stderr='No such container: '+ident)
        self.core.RuntimeOps.cleanup_held_candidates(ops,session)
        self.assertTrue(state['held_candidates'][0]['removed'])

    def test_interrupted_admission_closes_instead_of_pausing(self):
        calls=[]
        def validate(manifest):
            calls.append(True)
            if len(calls)>1:raise self.core.SessionInterrupted('synthetic TERM')
        self.ops.validate_host=validate
        result,points=self.run_controlled()
        self.assertEqual(len(points),1)
        self.assertEqual(result['held_close_reason'],'synthetic TERM')
        self.assertNotIn('rejected_attempt',result)
        self.assertTrue(result['restored']);self.assertFalse(self.hold.exists())

    def test_terminal_status_is_readonly_after_owned_hold_release(self):
        result,_=self.run_controlled()
        session=self.core.Session(self.directory,self.ops)
        before={p.name:p.read_bytes() for p in self.directory.iterdir() if p.is_file()}
        commands=list(self.ops.commands)
        status=H.read_status(session)
        self.assertTrue(status['restored']);self.assertTrue(status['hold_released'])
        self.assertEqual(status['hold_status'],'absent')
        self.assertEqual(commands,self.ops.commands)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.directory.iterdir() if p.is_file()})
        self.assertFalse(self.hold.exists())

if __name__=='__main__':unittest.main(verbosity=2)
