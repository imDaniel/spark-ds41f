"""CPU-only tests for the exact independent HTTP recovery bridge."""
import base64
import contextlib
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import uuid
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import q03_recover_http_logs as B
import q03_remote_operation as H


class AuthorizationTests(unittest.TestCase):
    def auth(self):
        return dict(schema_version=1, purpose=B.PURPOSE, window_id=B.WINDOW,
            capsule_sha256=B.CAPSULE_SHA, attempt_id=B.ATTEMPT, evidence_sha256=B.EVIDENCE_SHA,
            journal_dir='/new/recovery', helper_path='/new/helper', helper_sha256=B.NEW_HELPER,
            entry_sha256='d'*64, allowed_actions=B.ACTIONS, issued_epoch=100., deadline_epoch=7300.,
            authorization_reference='approved exact independent recovery')

    def validate(self, value, now=101.):
        return B.validate_authorization(value, now=now, journal=Path('/new/recovery'),
            helper_path=Path('/new/helper'), helper_sha=B.NEW_HELPER, entry_sha='d'*64)

    def test_new_budget_is_explicit_finite_and_nonrenewable(self):
        self.validate(self.auth())
        for changes in ({'deadline_epoch':7301}, {'deadline_epoch':100}, {'issued_epoch':102},
                        {'deadline_epoch':float('inf')}, {'deadline_epoch':True}):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                self.validate(dict(self.auth(), **changes))
        with self.assertRaises(RuntimeError): self.validate(self.auth(), now=7300)

    def test_wrong_capsule_helper_purpose_actions_or_extra_scope_refused(self):
        for changes in ({'capsule_sha256':'e'*64}, {'helper_sha256':B.OLD_HELPER},
                        {'purpose':'new-test'}, {'allowed_actions':B.ACTIONS + ['START']},
                        {'other':True}, {'authorization_reference':''}):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                self.validate(dict(self.auth(), **changes))

    def test_import_inert_and_no_old_restore_or_start_call(self):
        source = (ROOT/'scripts/q03_recover_http_logs.py').read_text()
        self.assertNotIn('transition_original(', source)
        self.assertNotIn('recover_normal_complete(', source)
        self.assertNotIn("action(rank, 'start'", source)
        with mock.patch.object(subprocess, 'run', side_effect=AssertionError('import action')):
            spec=importlib.util.spec_from_file_location('_inert_bridge', ROOT/'scripts/q03_recover_http_logs.py')
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.intent={'authorization':{'deadline_epoch':200}}
        self.state={'phase':'recovering'}
        self.target={'pid':123,'start_ticks':'456'}
        self.hold={'window':B.WINDOW,'holder':'q03-isolated:'+B.WINDOW}
        self.beat={'epoch':100,'process':self.target,'intent_sha256':'a'*64}

    def decision(self, **changes):
        args=dict(intent=self.intent,state=self.state,hold=self.hold,target_state='alive',
                  heartbeat=self.beat,now=101,target=self.target,intent_sha='a'*64)
        args.update(changes);return B.monitor_step(**args)

    def test_live_and_single_dead_takeover(self):
        self.assertEqual(self.decision(),'wait')
        self.assertEqual(self.decision(target_state='dead'),'retry')
        with self.assertRaises(RuntimeError):self.decision(target_state='dead',retried=True)

    def test_unknown_pid_hold_deadline_or_heartbeat_retains(self):
        for changes in ({'target_state':'reused'}, {'target_state':'unknown'}, {'hold':None},
                        {'hold':dict(self.hold,window='b'*32)}, {'now':200},
                        {'heartbeat':dict(self.beat,epoch=0)},
                        {'heartbeat':dict(self.beat,process={'pid':999,'start_ticks':'1'})},
                        {'heartbeat':dict(self.beat,intent_sha256='b'*64)},
                        {'state':{'phase':'UNKNOWN'}}):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):self.decision(**changes)

    def test_retry_readiness_has_small_bound_without_accepting_foreign_heartbeat(self):
        child={'pid':999,'start_ticks':'1'}
        self.assertEqual(self.decision(target=child,retried=True,retry_previous=self.target,
            retry_ready_until=110),'wait-retry-ready')
        with self.assertRaises(RuntimeError):self.decision(target=child,retried=True,
            retry_previous=self.target,retry_ready_until=101)
        with self.assertRaises(RuntimeError):self.decision(target=child,retried=True,
            retry_previous={'pid':777,'start_ticks':'1'},retry_ready_until=110)

    def test_release_ready_reconciles_crash_after_unlink_only_with_proof(self):
        self.assertEqual(self.decision(state={'phase':'release-ready','closure_proof_sha256':'c'*64},
                                      hold=None,target_state='dead'),'done')
        self.assertEqual(self.decision(state={'phase':'closed-stopped','closure_proof_sha256':'c'*64},
                                      hold=None),'done')
        with self.assertRaises(RuntimeError):self.decision(state={'phase':'release-ready'},hold=None)
        self.assertEqual(self.decision(state={'phase':'release-ready','closure_proof_sha256':'c'*64}),'wait')


class FileAndStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='q03-bound-recovery-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def test_exclusive_private_files_no_overwrite_symlink_or_hardlink(self):
        p=self.root/'record.json'
        old=os.umask(0o002)
        try:B.put(p,{'x':1})
        finally:os.umask(old)
        self.assertEqual(p.stat().st_mode&0o777,0o600)
        with self.assertRaises(FileExistsError):B.put(p,{'x':2})
        self.assertEqual(B.parse(B.read(p)),{'x':1})
        (self.root/'alias').symlink_to(p)
        with self.assertRaises(RuntimeError):B.read(self.root/'alias')
        os.link(p,self.root/'hardlink')
        with self.assertRaises(RuntimeError):B.read(p)

    def test_actual_lock_exclusion_and_path_replacement(self):
        p=self.root/'lock'
        with contextlib.ExitStack() as stack:
            B.lock(stack,p,create=True)
            with contextlib.ExitStack() as other, self.assertRaises(BlockingIOError):B.lock(other,p)
        original=B.fcntl.flock
        def replace(fd, flags):
            original(fd,flags);p.rename(self.root/'old-lock');p.touch(mode=0o600)
        with mock.patch.object(B.fcntl,'flock',replace), contextlib.ExitStack() as stack:
            with self.assertRaisesRegex(RuntimeError,'lock path changed'):B.lock(stack,p)

    def state(self):
        rows=[dict(rank=i,container_id=B.CONTAINERS[i],config_sha256=str(i+1)*64,
                   guard_intent=True,guard_phase='steady',removed=False) for i in range(4)]
        rows[0]['log_capture']={'capture_id':'c'*32,'index_sha256':'e'*64}
        return dict(phase='client_terminal',sequence=0,ranks=rows,epochs=[],sealed_epochs=[])

    def test_rank_config_guard_and_epoch_integrity(self):
        state=self.state();exact=copy.deepcopy(state['ranks'])
        B.validate_journal(state,exact,[],H)
        for mutate in (lambda s:s['ranks'].reverse(),lambda s:s['ranks'][1].update(guard_intent=False),
                       lambda s:s['ranks'][2].update(config_sha256='f'*64),lambda s:s['ranks'].pop(),
                       lambda s:s['ranks'][0].update(log_capture=None)):
            bad=copy.deepcopy(state);mutate(bad)
            with self.assertRaises(RuntimeError):B.validate_journal(bad,exact,[],H)
        rec=dict(rank=1,action_kind='capture_logs',epoch='1'*32,operation='2'*32,
                 argv_sha256=B.digest(B.canonical(H.bound_command(['docker','logs','--timestamps','--',B.CONTAINERS[1]]))))
        state['epochs']=[rec];B.validate_journal(state,exact,[],H)
        for mutate in (lambda s:s['epochs'].append(copy.deepcopy(rec)),
                       lambda s:s['epochs'][0].update(action_kind='start'),
                       lambda s:s.update(sealed_epochs=[rec['epoch']])):
            bad=copy.deepcopy(state);mutate(bad)
            with self.assertRaises(RuntimeError):B.validate_journal(bad,exact,[],H)

    def test_only_exact_unloaded_owned_guard_lifecycle_is_acknowledged(self):
        ident=B.CONTAINERS[1];unit=ident+'-memguard.service'
        receipt=dict(action_succeeded=False,exit_code=5,cancelled=False,timed_out=False,
            group_quiescent=True,lifecycle_acknowledged=True,lifecycle_unit=unit,stdout='',
            stderr='Failed to stop '+unit+': Unit '+unit+' not loaded.\n')
        self.assertTrue(B.mutation_terminal_ok('stop_guard',receipt,ident,H))
        self.assertFalse(receipt['action_succeeded'])
        for kind in ('stop','remove','create'):
            self.assertFalse(B.mutation_terminal_ok(kind,receipt,ident,H))
        for changes in ({'exit_code':1},{'stderr':'permission denied'},{'stdout':'unexpected'},
                        {'cancelled':True},{'timed_out':True},{'group_quiescent':False},
                        {'lifecycle_acknowledged':False},{'lifecycle_unit':'foreign-memguard.service'},
                        {'action_succeeded':True}):
            with self.subTest(changes=changes):
                self.assertFalse(B.mutation_terminal_ok('stop_guard',dict(receipt,**changes),ident,H))

    def test_cached_seal_requires_all_four_bound_receipts(self):
        state=self.state();exact=copy.deepcopy(state['ranks']);epoch='1'*32;operation='2'*32
        state['epochs']=[dict(rank=1,action_kind='remove',epoch=epoch,operation=operation,
            argv_sha256=B.digest(B.canonical(H.bound_command(['docker','rm',B.CONTAINERS[1]]))))]
        state['sealed_epochs']=[epoch]
        events=[dict(kind='epoch-sealed',epoch=epoch,rank=r,window_id=B.WINDOW,attempt_id=B.ATTEMPT,receipt=dict(window=B.WINDOW,epoch=epoch,
            sealed=True,safe_to_overlap=True,operations=[] if r!=1 else [dict(operation=operation,window=B.WINDOW,
                status='terminal',group_quiescent=True,epoch_sealed=True,safe_to_overlap=True,manifest_sha256='a'*64,
                action_succeeded=True,exit_code=0,cancelled=False,timed_out=False)])) for r in range(4)]
        B.validate_journal(state,exact,events,H)
        for mutate in (lambda e:e.pop(),lambda e:e[1]['receipt']['operations'][0].update(group_quiescent=False),
                       lambda e:e[0]['receipt'].update(window='f'*32),
                       lambda e:e[0].update(attempt_id='f'*32),
                       lambda e:e[1]['receipt']['operations'][0].update(window='f'*32),
                       lambda e:e[1]['receipt']['operations'][0].update(action_succeeded=False),
                       lambda e:e[1]['receipt']['operations'][0].update(exit_code=5),
                       lambda e:e[1]['receipt']['operations'][0].update(cancelled=True),
                       lambda e:e[1]['receipt']['operations'][0].update(timed_out=True),
                       lambda e:e[1]['receipt']['operations'][0].pop('action_succeeded')):
            bad=copy.deepcopy(events);mutate(bad)
            with self.assertRaises(RuntimeError):B.validate_journal(state,exact,bad,H)

    def test_cached_guard_ack_requires_separate_exact_inactive_observation(self):
        state=self.state();exact=copy.deepcopy(state['ranks']);epoch='1'*32;op='2'*32
        ident=B.CONTAINERS[1];unit=ident+'-memguard.service'
        state['epochs']=[dict(rank=1,action_kind='stop_guard',epoch=epoch,operation=op,
            argv_sha256=B.digest(B.canonical(H.bound_command(['sudo','-n','systemctl','stop',unit]))))]
        state['sealed_epochs']=[epoch]
        terminal=dict(window=B.WINDOW,operation=op,status='terminal',group_quiescent=True,
            epoch_sealed=True,safe_to_overlap=True,manifest_sha256='a'*64,
            action_succeeded=False,exit_code=5,cancelled=False,timed_out=False,
            lifecycle_acknowledged=True,lifecycle_unit=unit,stdout='',
            stderr='Failed to stop '+unit+': Unit '+unit+' not loaded.')
        events=[dict(kind='epoch-sealed',window_id=B.WINDOW,attempt_id=B.ATTEMPT,epoch=epoch,rank=r,
            receipt=dict(window=B.WINDOW,epoch=epoch,sealed=True,safe_to_overlap=True,
                         operations=[terminal] if r==1 else [])) for r in range(4)]
        with self.assertRaises(RuntimeError):B.validate_journal(state,exact,events,H)
        ack=dict(kind='guard-lifecycle-acknowledged',window_id=B.WINDOW,attempt_id=B.ATTEMPT,
            rank=1,epoch=epoch,operation=op,manifest_sha256='a'*64,unit=unit,
            action_succeeded=False,original_exit_code=5,guard={'absent':True})
        B.validate_journal(state,exact,[*events,ack],H)
        for changes in ({'unit':'wrong'},{'guard':{'owned':True,'active':True}},
                        {'operation':'e'*32},{'manifest_sha256':'e'*64},{'action_succeeded':True}):
            with self.subTest(changes=changes),self.assertRaises(RuntimeError):
                B.validate_journal(state,exact,[*events,dict(ack,**changes)],H)


class OldRemoteProofTests(unittest.TestCase):
    """Exercise the exact shipped remote proof against real Linux filesystem/proc.

    No Docker, network, GPU or SSH operation is executed. The old helper only
    reads a synthetic completed operation; its strict0664 rejection is real.
    """
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='q03-old-proof-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.window=B.WINDOW;self.epoch='a'*32;self.op='b'*32
        self.helper=self.root/'old-helper.py';self.helper.write_bytes((ROOT/'scripts/q03_remote_operation.py').read_bytes());self.helper.chmod(0o600)
        self.authority=self.root/'authority.json';self.authority.write_bytes(B.canonical(dict(independent_root=str(self.root),protected_roots=['/imaginary-protected-root'])));self.authority.chmod(0o600)
        self.base=self.root/'remote-operations'/self.window
        self.directory=self.base/self.op;self.directory.mkdir(parents=True,mode=0o700)
        self.ep=self.base/'epochs'/self.epoch;self.ep.mkdir(parents=True);(self.ep/'lock').touch(mode=0o600)
        self.argv=H.bound_command(['docker','logs','--timestamps','--',B.CONTAINERS[1]])
        self.make_operation()

    def file(self,p,raw,mode=0o664):
        p.write_bytes(raw);p.chmod(mode)
        return dict(sha256=B.digest(raw),mode=oct(mode))

    def make_operation(self,*,exit_code=0,argv=None):
        argv=argv or self.argv
        manifest=dict(root=str(self.root/'remote-operations'),window=self.window,epoch=self.epoch,
            operation=self.op,argv=argv,cwd=str(self.root),timeout_seconds=20,
            created_epoch=1,helper_sha256=B.digest(self.helper.read_bytes()))
        manifest_raw=B.canonical(manifest);mh=B.digest(manifest_raw)
        receipt=dict(window=self.window,operation=self.op,manifest_sha256=mh,group_quiescent=True,
                     exit_code=exit_code,cancelled=False,timed_out=False,finished_epoch=2)
        state=dict(phase='terminal',child={'pid':90000001,'start_ticks':'1'},supervisor={'pid':90000002,'start_ticks':'1'})
        files={name:self.file(self.directory/name,raw) for name,raw in {
            'manifest.json':manifest_raw,'manifest.sha256':(mh+'\n').encode(),
            'receipt.json':B.canonical(receipt),'state.json':B.canonical(state),
            'output.log':b'historic stdout','error.log':b'historic stderr','supervisor.log':b''}.items()}
        epraw=B.canonical(dict(window=self.window,epoch=self.epoch,sealed=True,operations=[self.op]))
        ep=self.file(self.ep/'epoch.json',epraw)
        self.payload=dict(window=self.window,hostname=platform.node(),helper_path=str(self.helper),
            helper_sha256=B.digest(self.helper.read_bytes()),authority_path=str(self.authority),
            authority_sha256=B.digest(self.authority.read_bytes()),authority=json.loads(self.authority.read_bytes()),
            epochs=[dict(epoch=self.epoch,**ep)],operations=[dict(operation=self.op,epoch=self.epoch,files=files)],
            new_epochs=[],new_operations=[],client_operation='c'*32,client_argv_sha256='d'*64)

    def run_proof(self,ok=True):
        before={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        encoded=base64.b85encode(zlib.compress(B.canonical(self.payload))).decode()
        result=subprocess.run([sys.executable,'-I','-B','-c',B.REMOTE_OLD_PROOF,encoded],
                              capture_output=True,text=True,timeout=10)
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        if ok:self.assertEqual(result.returncode,0,result.stderr);return json.loads(result.stdout)
        self.assertNotEqual(result.returncode,0)
        return result.stderr

    def test_historic0664_read_stays_failed_but_sealed_read_barrier_is_valid(self):
        result=self.run_proof();op=result['operations'][0]
        self.assertTrue(op['safe_to_overlap']);self.assertTrue(op['action_succeeded'])
        self.assertEqual(set(op['artifact_errors']),{'stdout','stderr'})
        self.assertEqual((self.directory/'output.log').stat().st_mode&0o777,0o664)

    def test_edited_old_receipt_or_mode_unknown_epoch_and_live_group_refused(self):
        for kind in ('bytes','mode','epoch','process'):
            with self.subTest(kind=kind):
                self.make_operation()
                if kind=='bytes':(self.directory/'receipt.json').write_text('{}')
                elif kind=='mode':(self.directory/'output.log').chmod(0o600)
                elif kind=='epoch':(self.base/'epochs'/('f'*32)).mkdir()
                else:
                    state=json.loads((self.directory/'state.json').read_bytes());state['child']=B.identity()
                    item=self.file(self.directory/'state.json',B.canonical(state))
                    self.payload['operations'][0]['files']['state.json']=item
                self.run_proof(ok=False)
                if kind=='epoch':(self.base/'epochs'/('f'*32)).rmdir()

    def test_failed_mutating_operation_is_never_acknowledged(self):
        self.make_operation(exit_code=1,argv=H.bound_command(['docker','rm',B.CONTAINERS[1]]))
        self.assertIn('old mutating failure is UNKNOWN',self.run_proof(ok=False))

    def test_only_exact_bound_client_failure_can_be_group_quiescent(self):
        argv=H.bound_command(['python3','-I','-B','/fixed-client.py','--plan','/fixed-plan','--sha256','a'*64])
        self.make_operation(exit_code=1,argv=argv)
        self.payload.update(client_operation=self.op,client_argv_sha256=B.digest(B.canonical(argv)))
        result=self.run_proof();self.assertFalse(result['operations'][0]['action_succeeded'])
        self.payload['client_argv_sha256']='0'*64
        self.run_proof(ok=False)


class ReleaseCrashTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='q03-release-crash-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.proof=self.root/'closure-proof.json'
        B.put(self.proof,{'intent_sha256':'a'*64});self.hold=self.root/'hold';B.put(self.hold,{'owned':True})
        self.state={'phase':'recovering','hold_released':False};B.put(self.root/'state.json',self.state)

    def test_write_failure_before_release_preserves_hold(self):
        def failed(*a,**k):raise OSError('injected durable-write failure')
        with self.assertRaises(OSError):B.publish_release(self.root,self.state,self.proof,self.hold.unlink,lambda:None,writer=failed)
        self.assertTrue(self.hold.exists())

    def test_write_failure_after_release_preserves_release_ready(self):
        def writer(p,v,**kwargs):
            if v['phase']=='closed-stopped':raise OSError('injected final-write failure')
            B.put(p,v,**kwargs)
        with self.assertRaises(B.ReleasedClosureError):
            B.publish_release(self.root,self.state,self.proof,self.hold.unlink,lambda:None,writer=writer)
        self.assertFalse(self.hold.exists());retained=B.snapshot(self.root/'state.json')
        self.assertEqual(retained['phase'],'release-ready')
        self.assertEqual(retained['closure_proof_sha256'],B.digest(B.read(self.proof)))

    def test_real_kill_before_and_after_unlink_leaves_proven_reconciliation_state(self):
        script=ROOT/'scripts/q03_recover_http_logs.py'
        for after in (False,True):
            with self.subTest(after=after):
                if not self.hold.exists():B.put(self.hold,{'owned':True})
                code=("import importlib.util,json,os,signal,pathlib; "
                    "s=importlib.util.spec_from_file_location('bridge',"+repr(str(script))+"); "
                    "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
                    "p=pathlib.Path("+repr(str(self.root))+"); "
                    "state={'phase':'recovering','hold_released':False}; "
                    "release=lambda: ("+("(p/'hold').unlink(), " if after else "")+
                    "os.kill(os.getpid(),signal.SIGKILL)); "
                    "m.publish_release(p,state,p/'closure-proof.json',release,lambda:None)")
                result=subprocess.run([sys.executable,'-I','-B','-c',code],capture_output=True,timeout=5)
                self.assertEqual(result.returncode,-9,result.stderr)
                retained=B.snapshot(self.root/'state.json');self.assertEqual(retained['phase'],'release-ready')
                self.assertEqual(self.hold.exists(),not after)

    def test_real_retry_child_cannot_run_before_durable_identity_marker(self):
        output=self.root/'child-observed.json'
        code=("import json,os,pathlib; p=pathlib.Path("+repr(str(self.root))+"); "
              "v=json.loads((p/'supervisor-retry.json').read_text()); "
              "assert v['child']['pid']==os.getpid(); "
              "(p/'child-observed.json').write_text(json.dumps(v))")
        record=B.launch_retry(self.root,{'recovery_command':[sys.executable,'-I','-B','-c',code]},'a'*64,B.identity())
        child=next(p for p in B._CHILDREN if p.pid==record['pid'])
        self.assertEqual(child.wait(timeout=5),0)
        self.assertEqual(json.loads(output.read_text())['child'],record)


class NewGenerationBarrierTests(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('_recovery_cleanup_fixtures', ROOT/'tests/test_q03_rank_log_cleanup.py')
        self.f=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.f)
        self.tmp=tempfile.TemporaryDirectory(prefix='q03-recovery-generation-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.old=self.root/'old';self.old.mkdir(mode=0o700)
        self.old_journal=self.f.Journal(self.old)
        self.old_journal.plan.update(window_id=B.WINDOW,attempt_id=B.ATTEMPT)
        for i,row in enumerate(self.old_journal.state['ranks']):
            row.update(container_id=B.CONTAINERS[i],guard_phase='steady')
        oldops=self.f.Ops(self.old_journal)
        for item in oldops.items.values():item['State']['Running']=False
        oldops.guards={r:False for r in range(4)}
        row=self.old_journal.state['ranks'][0]
        row['log_capture']=self.f.L.capture_rank(self.old_journal,oldops,row,self.f.validate)
        self.old_journal.save()
        history=self.old/'rejected-peer.log';history.write_bytes(b'historic rejected bytes');history.chmod(0o664)
        self.old_before={str(p.relative_to(self.old)):(p.read_bytes(),p.stat().st_mode&0o777)
                         for p in self.old.rglob('*') if p.is_file()}
        self.journal=B.Journal(self.root/'new',self.old_journal.plan,self.old_journal.state,
                               self.old,self.f.L,self.f.validate)
        self.ops=self.f.Ops(self.journal)
        for item in self.ops.items.values():item['State']['Running']=False
        self.ops.guards={r:False for r in range(4)}

    def assert_old_unchanged(self):
        self.assertEqual(self.old_before,{str(p.relative_to(self.old)):(p.read_bytes(),p.stat().st_mode&0o777)
                         for p in self.old.rglob('*') if p.is_file()})

    def test_only_peers_get_new_generations_then_all_four_exact_ids_removed(self):
        result=self.f.L.quiesce_with_logs(self.journal,self.ops,self.f.validate)
        self.assertTrue(result['logs_complete']);self.assertTrue(result['gpu_drain_required_by_caller'])
        self.assertEqual([r for k,r in self.ops.events if k=='logs'],[1,2,3])
        self.assertEqual([r for k,r in self.ops.events if k=='remove'],[0,1,2,3])
        self.assert_old_unchanged()
        for row in self.journal.state['ranks']:self.f.L.verified_capture(self.journal,row,self.f.validate)

    def test_incomplete_peer_retains_all_four_and_resume_uses_completed_generations(self):
        self.ops.fail_log=1
        with self.assertRaisesRegex(RuntimeError,'logs incomplete'):
            self.f.L.quiesce_with_logs(self.journal,self.ops,self.f.validate)
        self.assertFalse(any(k=='remove' for k,r in self.ops.events));self.assertEqual(len(self.ops.items),4)
        self.assert_old_unchanged()
        self.ops.fail_log=None
        self.f.L.quiesce_with_logs(self.journal,self.ops,self.f.validate)
        self.assertEqual([r for k,r in self.ops.events if k=='logs'],[1,2,3,1])
        self.assert_old_unchanged()

    def test_corrupted_reused_head_proof_prevents_new_generation_admission(self):
        row=self.journal.state['ranks'][0]
        target=self.journal.directory/'rank-logs/0'/row['log_capture']['capture_id']/'stdout.log'
        target.write_bytes(b'changed')
        with self.assertRaises(RuntimeError):
            B.Journal(self.root/'new',self.old_journal.plan,self.old_journal.state,
                      self.old,self.f.L,self.f.validate)
        self.assert_old_unchanged()


if __name__=='__main__':unittest.main()
