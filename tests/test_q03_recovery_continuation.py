"""CPU-only admission faults; real child integration lives in its own module."""
import copy
import json
import os
from pathlib import Path
import stat
import time
import types
import unittest
import test_q03_failed_window_recovery as base

r = base.r


def metadata(path, role):
    s = path.lstat()
    row = dict(path=str(path), resolved_path=str(path.resolve()), exists=True, role=role,
               lstat=dict(type='directory' if path.is_dir() else 'regular',
                          mode=format(stat.S_IMODE(s.st_mode), '04o'), uid=s.st_uid,
                          gid=s.st_gid, dev=s.st_dev, ino=s.st_ino, nlink=s.st_nlink,
                          size=s.st_size, mtime_ns=s.st_mtime_ns, ctime_ns=s.st_ctime_ns))
    if path.is_file(): row['sha256'] = r.digest(path.read_bytes())
    return row


class ContinuationAdmissionTests(base.AdmissionFixtureTests):
    def setUp(self):
        super().setUp()
        self.capsule['recovery_timeout_s'] = 7200
        self.prior_entry = self.root / 'prior-entry.py'
        self.prior_entry.write_text('Prior frozen entry fixture, never executed.\n')
        self.setattr(r, 'FAILED_ENTRY_SHA256', r.digest(self.prior_entry.read_bytes()))
        self.destination.mkdir(mode=0o700)
        self.deadline = time.time() + 600
        self.intent = dict(window_id=r.WINDOW, old_capsule_sha256=r.CAPSULE_SHA256,
                           entry_sha256=r.FAILED_ENTRY_SHA256, never_start=True,
                           journal_dir=str(self.destination), recovery_process={'pid':99999999,'start_ticks':'1'},
                           hold_identity=list(r.verify_hold(self.capsule)), deadline_epoch=self.deadline)
        command = [os.sys.executable,'-I','-B','-c',r.ENTRY_BOOTSTRAP,str(self.prior_entry),
                   r.FAILED_ENTRY_SHA256,'--old-session',str(self.old),'--journal-dir',str(self.destination),
                   '--adapter-step','quiesce','--epoch',r.OLD_EPOCH,'--live-witness','/private-fixture']
        self.state = dict(phase='recovery-failed',active_process=None,transition_epochs=[r.OLD_EPOCH],
                          last_exit_code=1,restored=False,hold_released=False,
                          recovery_process=self.intent['recovery_process'],
                          restoring_started_epoch=self.deadline-7200,
                          command_history=[dict(argv=command,log=str(self.destination/'command.log'))])
        for name, value in [('intent.json',self.intent),('state.json',self.state)]:
            r.private_json(self.destination/name,value,exclusive=True)
        for name in ('reconcile.lock','command.log'):
            (self.destination/name).touch(mode=0o600)
        self.authority = dict(failed_journal=dict(path=str(self.destination),
            directory=metadata(self.destination,'failed_journal'),
            files=[metadata(p,p.name) for p in self.destination.iterdir()]),
            current_hold=metadata(self.root/'hold.json','head_hold'),
            predecessor_processes=[self.intent['recovery_process']], absolute_recovery_deadline=self.deadline,
            files={},ancestors={})
        self.setattr(r,'_CONTINUATION',self.authority)

    def bind_state(self):
        r.private_json(self.destination/'state.json',self.state)
        self.authority['failed_journal']['files'] = [metadata(p,p.name) for p in self.destination.iterdir()]

    def test_exact_predecessor_keeps_original_deadline_and_every_byte(self):
        before={p.name:p.read_bytes() for p in self.destination.iterdir()}
        got=r.verify_predecessor(self.capsule,self.destination,initial_hold=True)
        self.assertEqual(got['deadline_epoch'],self.deadline)
        self.assertLess(got['deadline_epoch']-time.time(),601)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.destination.iterdir()})

    def test_predecessor_bytes_changed_refuses(self):
        for name in ('intent.json','state.json','command.log','reconcile.lock'):
            with self.subTest(name=name):
                p=self.destination/name; old=p.read_bytes();p.write_bytes(old+b' ')
                with self.assertRaises(RuntimeError):r.verify_predecessor(self.capsule,self.destination)
                p.write_bytes(old)
                self.authority['failed_journal']['files']=[metadata(q,q.name) for q in self.destination.iterdir()]

    def test_predecessor_state_semantics_fail_closed(self):
        for field,value in [('phase','restoring'),('active_process',{'pid':1}),('transition_epochs',[r.OLD_EPOCH,'a'*32]),
                            ('last_exit_code',0),('restored',True),('hold_released',True),('positive_all_four_drain',True)]:
            with self.subTest(field=field):
                original=copy.deepcopy(self.state);self.state[field]=value;self.bind_state()
                with self.assertRaises(RuntimeError):r.verify_predecessor(self.capsule,self.destination)
                self.state=original;self.bind_state()

    def test_live_or_unknown_recorded_predecessor_never_admits(self):
        original=r.process_state
        for observation in ('alive','unknown'):
            with self.subTest(observation=observation):
                def observe(record):
                    if observation=='unknown':raise PermissionError('proc unavailable')
                    return 'alive'
                r.process_state=observe
                try:
                    with self.assertRaises((RuntimeError,PermissionError)):
                        r.verify_predecessor(self.capsule,self.destination)
                finally:r.process_state=original

    def test_deadline_expired_or_reset_refuses(self):
        original=time.time
        try:
            time.time=lambda:self.deadline+1
            with self.assertRaisesRegex(RuntimeError,'deadline'):r.verify_predecessor(self.capsule,self.destination)
        finally:time.time=original
        self.state['restoring_started_epoch']+=1;self.bind_state()
        with self.assertRaisesRegex(RuntimeError,'deadline'):r.verify_predecessor(self.capsule,self.destination)

    def test_current_hold_new_heartbeat_requires_new_authority(self):
        p=self.root/'hold.json';record=json.loads(p.read_text())
        record['heartbeat']='2026-10-22T01:39:40.928178+00:00';p.write_text(json.dumps(record))
        with self.assertRaises(RuntimeError):r.verify_predecessor(self.capsule,self.destination,initial_hold=True)

    def test_replaced_predecessor_leaf_or_hold_refuses(self):
        for path in (self.destination/'state.json',self.root/'hold.json'):
            with self.subTest(path=path.name):
                raw=path.read_bytes(); replacement=path.with_name(path.name+'.replacement')
                replacement.write_bytes(raw);replacement.chmod(0o600);replacement.replace(path)
                with self.assertRaises(RuntimeError):r.verify_predecessor(self.capsule,self.destination)
                # This fixture is intentionally not repaired; all later calls must still refuse.

    def test_existing_continuation_claim_does_not_create_another(self):
        self.setattr(r,'verify_old',lambda _: (self.capsule,{}, {}, {}))
        self.setattr(r,'registered_assets',lambda *args:None)
        claim=self.destination/r.CONTINUATION_NAME;claim.mkdir(mode=0o700)
        before={p.name for p in self.destination.iterdir()}
        with self.assertRaisesRegex(RuntimeError,'fresh journal'):
            r.recover(self.old,self.destination)
        self.assertEqual(before,{p.name for p in self.destination.iterdir()})

    def test_predecessor_lock_busy_keeps_hold_and_no_continuation(self):
        import fcntl
        self.setattr(r,'verify_old',lambda _: (self.capsule,{}, {}, {}))
        self.setattr(r,'registered_assets',lambda *args:None)
        self.setattr(r,'load_modules',lambda *args:{'q03_window':types.SimpleNamespace(RuntimeOps=lambda:object()),
                                                   'q03_window_watchdog':object()})
        actual_read=r.read
        self.setattr(r,'read',lambda path,**kwargs:b'entry' if Path(path)==base.PATH else actual_read(path,**kwargs))
        before=(self.root/'hold.json').read_bytes()
        with (self.destination/'reconcile.lock').open('r+') as owner:
            fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):r.recover(self.old,self.destination,apply=True)
        self.assertEqual(before,(self.root/'hold.json').read_bytes())
        self.assertFalse((self.destination/r.CONTINUATION_NAME).exists())


    def test_whole_continuation_preserves_predecessor_and_absolute_deadline(self):
        base.ApplyBoundaryTests.setup_apply(self)
        self.setattr(r,'registered_assets',lambda *args:None)
        self.setattr(r,'verify_registered_inputs',lambda:None)
        self.authority['path']='/private-continuation'
        before={p.name:p.read_bytes() for p in self.destination.iterdir()}
        result=r.recover(self.old,self.destination,apply=True)
        claim=self.destination/r.CONTINUATION_NAME
        self.assertTrue(result['hold_released'])
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.destination.iterdir() if p.is_file()})
        intent=json.loads((claim/'intent.json').read_text())
        state=json.loads((claim/'state.json').read_text())
        self.assertEqual(intent['deadline_epoch'],self.deadline)
        self.assertEqual(state['restoring_started_epoch'],self.deadline-7200)
        self.assertEqual(intent['predecessor_journal'],str(self.destination))
        self.assertTrue((claim/'release-intent.json').exists())
        self.assertFalse(any('start:' in event for event in self.events))

    def test_whole_continuation_failed_stop_seals_without_release_or_budget_reset(self):
        base.ApplyBoundaryTests.setup_apply(self,'stop')
        self.setattr(r,'registered_assets',lambda *args:None)
        self.setattr(r,'verify_registered_inputs',lambda:None)
        self.authority['path']='/private-continuation'
        before={p.name:p.read_bytes() for p in self.destination.iterdir()}
        with self.assertRaisesRegex(RuntimeError,'STOP failed'):
            r.recover(self.old,self.destination,apply=True)
        claim=self.destination/r.CONTINUATION_NAME
        self.assertTrue((self.root/'hold.json').exists())
        self.assertTrue(self.events[-1].startswith('quiesce:'))
        self.assertFalse((claim/'release-intent.json').exists())
        self.assertEqual(json.loads((claim/'intent.json').read_text())['deadline_epoch'],self.deadline)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.destination.iterdir() if p.is_file()})


for _name in dir(base.AdmissionFixtureTests):
    if _name.startswith('test_') and _name not in ContinuationAdmissionTests.__dict__:
        setattr(ContinuationAdmissionTests,_name,None)


class OriginalAuthorityLoaderTests(unittest.TestCase):
    """Exercise the production private-authority loader, not a child admission stub."""
    def setUp(self):
        import tempfile
        from unittest.mock import patch
        self.temp = tempfile.TemporaryDirectory(prefix='q03-authority-', dir=base.PATH.parents[1])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repository = self.root/'registered'; self.repository.mkdir(mode=0o775)
        self.repository.chmod(0o775)
        for name in ('bin','scripts','config'):
            p=self.repository/name;p.mkdir(mode=0o775);p.chmod(0o775)
        self.old=self.root/'old';self.old.mkdir(mode=0o700)
        self.contract=self.root/'contract.json'
        self.profile=self.repository/'config/profile.json'
        self.nodes=self.repository/'config/nodes.json'
        self.upstream=self.repository/'config/upstream.json'
        for p in (self.profile,self.nodes,self.upstream):p.write_text('{}');p.chmod(0o664)
        self.spark=self.repository/'bin/spark';self.spark.write_text('# fixture only\n');self.spark.chmod(0o775)
        helpers={}
        for name in ('topology','mesh_fabric','kernel_backend','kernel_readiness','transport_profiles','model_layout','recipe'):
            p=self.repository/'scripts'/(name+'.py');p.write_text('# '+name+'\n');p.chmod(0o664)
            helpers[name+'.py']=r.digest(p.read_bytes())
        self.policy=self.root/'policy.json';self.policy.write_text('{}');self.policy.chmod(0o600)
        self.inventory=self.root/'inventory.json'
        self.inventory.write_text(json.dumps({'inventory':{'files':[dict(path=str(p),sha256=r.digest(p.read_bytes()))
            for p in (self.profile,self.nodes,self.upstream)]}}));self.inventory.chmod(0o600)
        self.capsule={'manifest':{'preflight':{'registered_policy':{'repository':str(self.repository),
            'cluster_config':'config/profile.json','config_sha256':r.digest(self.profile.read_bytes())},
            'policy_path':str(self.policy),'policy_sha256':r.digest(self.policy.read_bytes()),
            'spark_sha256':r.digest(self.spark.read_bytes()),'nodes':[{'inventory_path':str(self.inventory),
            'inventory_sha256':r.digest(self.inventory.read_bytes())}]}},'recovery_source_sha256':helpers}
        p=self.old/'capsule.json';p.write_text(json.dumps(self.capsule));p.chmod(0o600)
        files=[self.policy,self.spark,self.profile,self.nodes,self.upstream,self.inventory,*sorted((self.repository/'scripts').iterdir())]
        self.authority={'schema_version':1,'window_id':r.WINDOW,'old_capsule_sha256':r.CAPSULE_SHA256,
            'failed_entry_sha256':r.FAILED_ENTRY_SHA256,'original_witness_sha256':r.WITNESS_SHA256,
            'original_files':[metadata(p,'registered_input') for p in files],
            'original_ancestors':[metadata(p,'registered_ancestor') for p in
                (self.repository,*(self.repository/n for n in ('bin','scripts','config')))],
            'configuration_paths':[str(p) for p in (self.profile,self.nodes,self.upstream)]}
        self.patches=[]
        for name,value in [('_CONTINUATION',None),('_WITNESS',None),('OLD_FILES',{'capsule.json':base.fingerprint(p)})]:
            patcher=patch.object(r,name,value);patcher.start();self.addCleanup(patcher.stop)
        self.pin=patch.object(r,'CONTINUATION_WITNESS_SHA256','');self.pin.start();self.addCleanup(self.pin.stop)
        self.bind()

    def bind(self):
        self.contract.write_text(json.dumps(self.authority));self.contract.chmod(0o600)
        r.CONTINUATION_WITNESS_SHA256=r.digest(self.contract.read_bytes())

    def test_exact_original_0775_0664_contract_loads_all_thirteen_inputs(self):
        r.load_continuation(self.contract,self.old)
        self.assertEqual(len(r._CONTINUATION['files']),13)
        self.assertEqual(len(r._CONTINUATION['config_files']),3)
        self.assertEqual(r.read(self.profile),b'{}')

    def test_changed_private_contract_refuses_before_any_exception(self):
        self.contract.write_bytes(self.contract.read_bytes()+b' ')
        with self.assertRaisesRegex(RuntimeError,'witness hash'):r.load_continuation(self.contract,self.old)
        self.assertIsNone(r._CONTINUATION)

    def test_changed_original_configuration_refuses(self):
        self.nodes.write_bytes(b'{"changed":true}')
        with self.assertRaises(RuntimeError):r.load_continuation(self.contract,self.old)

    def test_protected_inventory_pin_cannot_be_replaced_by_current_config_hash(self):
        row=next(row for row in self.authority['original_files'] if row['path']==str(self.nodes))
        row['sha256']='0'*64;self.bind()
        with self.assertRaisesRegex(RuntimeError,'protected authority'):r.load_continuation(self.contract,self.old)

    def test_unlisted_writable_ancestry_is_not_an_exception(self):
        self.authority['original_ancestors']=[row for row in self.authority['original_ancestors']
            if row['path']!=str(self.repository/'config')];self.bind()
        with self.assertRaisesRegex(RuntimeError,'Untrusted directory ancestry'):r.load_continuation(self.contract,self.old)

    def test_same_byte_inode_replacement_refuses(self):
        replacement=self.root/'replacement';replacement.write_bytes(self.profile.read_bytes());replacement.chmod(0o664)
        replacement.replace(self.profile)
        with self.assertRaises(RuntimeError):r.load_continuation(self.contract,self.old)

    def test_duplicate_original_file_record_refuses(self):
        self.authority['original_files'].append(self.authority['original_files'][0]);self.bind()
        with self.assertRaisesRegex(RuntimeError,'Duplicate original file'):r.load_continuation(self.contract,self.old)
