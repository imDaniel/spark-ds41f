"""Independent CPU-only cleanup tests. Real immutable evidence files, fake daemons."""
import base64
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid
ROOT = Path(os.environ.get('Q03_LOG_ROOT', Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT / 'scripts'))
import q03_rank_logs as L
import q03_remote_operation as R

def canonical(x): return json.dumps(x,sort_keys=True,separators=(',',':')).encode()
def sha(x): return hashlib.sha256(x).hexdigest()

class Journal:
    def __init__(self, directory):
        self.directory = directory
        self.plan = dict(window_id='a'*32, attempt_id='b'*32, plan_sha256='c'*64)
        self.state = dict(sequence=0, phase='client_terminal', epochs=[], ranks=[dict(rank=r,
            container_id=str(r+1)*64, config_sha256=str(r+5)*64, guard_intent=True, removed=False) for r in range(4)])
        self.session=SimpleNamespace(directory=directory.parent, capsule={'window_id':'a'*32,
            'recovery_source_sha256':{'q03_rank_logs.py':sha((ROOT/'scripts/q03_rank_logs.py').read_bytes())}})
        self.save()
    def save(self): (self.directory/'state.json').write_bytes(canonical(self.state))
    def event(self, kind, **data):
        self.state['sequence'] += 1
        (self.directory/f"{self.state['sequence']:06}.json").write_bytes(canonical(dict(kind=kind, **data)))
        self.save()
    def phase(self, phase): self.state['phase']=phase; self.save()

class Ops:
    def __init__(self, journal):
        self.journal=journal; self.events=[]; self.fail_log=None; self.fail_stop=None; self.mutate_log=None
        self.items={row['rank']:dict(Id=row['container_id'],config_sha256=row['config_sha256'],
             State=dict(Running=True,ExitCode=0,OOMKilled=False,FinishedAt='fixed')) for row in journal.state['ranks']}
        self.guards={r:True for r in range(4)}
    def check_authority(self): self.events.append(('authority',None))
    def seal_all(self): self.events.append(('seal',None))
    def inspect(self,r,row): self.events.append(('inspect',r)); return copy.deepcopy(self.items.get(r))
    def guard_state(self,r,row): return dict(owned=True,active=self.guards[r],container_id=row['container_id'])
    def action(self,r,kind,row):
        self.events.append((kind,r))
        if kind=='stop':
            if r==self.fail_stop: raise RuntimeError('injected stopped-state uncertainty')
            self.items[r]['State']['Running']=False
        elif kind=='stop_guard': self.guards[r]=False
        elif kind=='remove':
            assert not any(x['State']['Running'] for x in self.items.values()), 'remove before all rank STOP'
            L.verified_capture(self.journal,row,validate)
            self.items.pop(r,None)
        else: raise AssertionError(kind)
    def capture_logs(self,r,row):
        self.events.append(('logs',r))
        assert not any(x['State']['Running'] for x in self.items.values()), 'log read before all rank STOP'
        assert not any(self.guards.values()), 'log read before guard drain'
        if r==self.fail_log: raise RuntimeError('injected log read transport failure')
        streams={}
        for name,raw in [('stdout',b'raw-stdout\x00\xff-rank'+bytes([r])),('stderr',b'raw-stderr\x80-rank'+bytes([r]))]:
            streams[name]=dict(data_base64=base64.b64encode(raw).decode(),bytes=len(raw),sha256=sha(raw))
        value=dict(window=self.journal.plan['window_id'],operation=uuid.uuid4().hex,epoch=uuid.uuid4().hex,
            manifest_sha256='d'*64,status='terminal',group_quiescent=True,epoch_sealed=True,
            action_succeeded=True,exit_code=0,cancelled=False,timed_out=False,
            log_capture=dict(container_id=row['container_id'],timestamps=True,stream_limit_bytes=R.LOG_STREAM_LIMIT,
                             complete=True,truncated=False,streams=streams))
        if self.mutate_log: self.mutate_log(r,value)
        return value

def validate(item,plan,row):
    if not isinstance(item,dict) or item.get('Id')!=row['container_id'] or item.get('config_sha256')!=row['config_sha256']:
        raise RuntimeError('exact-ID/config mismatch')

class CleanupLogFaults(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='q03-cleanup-log-')
        self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)
        self.journal=Journal(self.directory); self.ops=Ops(self.journal)
        for name in ('Popen','run','check_call','check_output'):
            p=mock.patch.object(subprocess,name,side_effect=AssertionError('no real process allowed'));p.start();self.addCleanup(p.stop)
        p=mock.patch.object(socket,'create_connection',side_effect=AssertionError('no network allowed'));p.start();self.addCleanup(p.stop)
    def run_cleanup(self): return L.quiesce_with_logs(self.journal,self.ops,validate)
    def test_all_four_exact_raw_logs_precede_removal_and_retry_is_idempotent(self):
        result=self.run_cleanup()
        self.assertTrue(result['logs_complete']); self.assertTrue(result['gpu_drain_required_by_caller'])
        for kind in ('stop','stop_guard','logs','remove'):
            self.assertEqual(sorted(r for k,r in self.ops.events if k==kind),list(range(4)))
        self.assertLess(max(i for i,(k,r) in enumerate(self.ops.events) if k in ('stop','stop_guard')),
                        min(i for i,(k,r) in enumerate(self.ops.events) if k=='logs'))
        self.assertLess(max(i for i,(k,r) in enumerate(self.ops.events) if k=='logs'),
                        min(i for i,(k,r) in enumerate(self.ops.events) if k=='remove'))
        original={str(p.relative_to(self.directory)):p.read_bytes() for p in self.directory.glob('rank-logs/**/*') if p.is_file()}
        self.run_cleanup()
        self.assertEqual(len([e for e in self.ops.events if e[0]=='logs']),4)
        self.assertEqual(original,{str(p.relative_to(self.directory)):p.read_bytes() for p in self.directory.glob('rank-logs/**/*') if p.is_file()})
    def test_failed_log_read_stops_all_four_retains_all_then_safely_retries(self):
        self.ops.fail_log=0
        with self.assertRaisesRegex(RuntimeError,'logs incomplete'): self.run_cleanup()
        self.assertTrue(all(not x['State']['Running'] for x in self.ops.items.values()))
        self.assertEqual(len(self.ops.items),4)
        self.assertFalse(any(e[0]=='remove' for e in self.ops.events))
        failed=list(self.directory.glob('rank-logs/0/*/failure.json'));self.assertEqual(len(failed),1)
        failed_raw=failed[0].read_bytes();self.ops.fail_log=None
        self.run_cleanup()
        self.assertEqual(failed_raw,failed[0].read_bytes())
        self.assertEqual(len(list(self.directory.glob('rank-logs/0/*/intent.json'))),2)
        self.assertEqual(len([e for e in self.ops.events if e==('logs',1)]),1)
    def test_unknown_stop_on_one_rank_does_not_prevent_other_rank_stops(self):
        self.ops.fail_stop=0
        with self.assertRaisesRegex(RuntimeError,'drain unproven'): self.run_cleanup()
        self.assertTrue(self.ops.items[0]['State']['Running'])
        self.assertTrue(all(not self.ops.items[r]['State']['Running'] for r in (1,2,3)))
        self.assertFalse(any(e[0] in ('logs','remove') for e in self.ops.events))
    def test_late_create_unknown_id_still_stops_other_ranks_and_never_removes(self):
        self.journal.state['ranks'][0]['container_id']=None
        self.journal.event('create-intent',rank=0)
        with self.assertRaisesRegex(RuntimeError,'durable ID'): self.run_cleanup()
        self.assertTrue(all(not self.ops.items[r]['State']['Running'] for r in (1,2,3)))
        self.assertFalse(any(e[0] in ('logs','remove') for e in self.ops.events))
    def test_id_reuse_or_changed_config_refuses_unknown_rank_but_stops_known_ranks(self):
        for field in ('Id','config_sha256'):
            with self.subTest(field=field):
                self.ops.items[0][field]='e'*64
                with self.assertRaisesRegex(RuntimeError,'mismatch'): self.run_cleanup()
                self.assertTrue(all(not self.ops.items[r]['State']['Running'] for r in (1,2,3)))
                self.assertFalse(any(e[0] in ('logs','remove') for e in self.ops.events))
                self.ops.items[0][field]=self.journal.state['ranks'][0]['container_id' if field=='Id' else 'config_sha256']
    def test_incomplete_or_corrupt_transport_never_removes_any_rank(self):
        faults=[lambda v:v.update(exit_code=1,action_succeeded=False),lambda v:v['log_capture'].update(complete=False),
                lambda v:v['log_capture'].update(truncated=True),lambda v:v.update(group_quiescent=False),
                lambda v:v.update(epoch_sealed=False),lambda v:v['log_capture'].update(container_id='f'*64),
                lambda v:v['log_capture']['streams']['stdout'].update(sha256='0'*64)]
        for index,fault in enumerate(faults):
            with self.subTest(index=index):
                self.ops.mutate_log=lambda r,v: fault(v) if r==0 else None
                with self.assertRaises(RuntimeError): self.run_cleanup()
                self.assertFalse(any(e[0]=='remove' for e in self.ops.events))
                self.assertEqual(len(self.ops.items),4)
        self.ops.mutate_log=None;self.run_cleanup()
    def test_retained_hash_corruption_blocks_duplicate_retirement(self):
        self.run_cleanup()
        row=self.journal.state['ranks'][0]
        path=self.directory/'rank-logs'/'0'/row['log_capture']['capture_id']/'stdout.log'
        path.write_bytes(path.read_bytes()+b'corrupt')
        with self.assertRaisesRegex(RuntimeError,'artifact differs'):self.run_cleanup()
    def test_restarted_container_after_capture_is_not_removed(self):
        def restart(r,value):
            if r==0:self.ops.items[r]['State'].update(Running=True,FinishedAt='changed')
        self.ops.mutate_log=restart
        with self.assertRaises(RuntimeError):self.run_cleanup()
        self.assertFalse(any(e[0]=='remove' for e in self.ops.events))
    def test_retained_fifo_is_rejected_without_waiting_for_a_writer(self):
        self.run_cleanup()
        row=self.journal.state['ranks'][0]
        path=self.directory/'rank-logs'/'0'/row['log_capture']['capture_id']/'stdout.log'
        path.unlink();os.mkfifo(path,0o600)
        def timeout(signum,frame):
            raise AssertionError('retained evidence reader blocks on FIFO')
        old=signal.signal(signal.SIGALRM,timeout);signal.setitimer(signal.ITIMER_REAL,0.25)
        try:
            with self.assertRaises((RuntimeError,ValueError,OSError)):
                L.verified_capture(self.journal,row,validate)
        finally:
            signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,old)

    def test_partial_raw_write_never_publishes_success_and_retry_preserves_partial(self):
        original=L.write_bytes
        broken=[]
        def fail_once(path,raw):
            if path.name=='stdout.log' and path.parent.parent.name=='0' and not broken:
                path.write_bytes(raw[:3]);broken.append(path)
                raise OSError('injected ENOSPC during raw stdout write')
            return original(path,raw)
        with mock.patch.object(L,'write_bytes',side_effect=fail_once):
            with self.assertRaisesRegex(RuntimeError,'logs incomplete'):self.run_cleanup()
        self.assertEqual(len(self.ops.items),4)
        self.assertFalse(any(e[0]=='remove' for e in self.ops.events))
        self.assertFalse((broken[0].parent/'index.json').exists())
        self.assertTrue((broken[0].parent/'failure.json').exists())
        partial=broken[0].read_bytes();self.run_cleanup()
        self.assertEqual(broken[0].read_bytes(),partial)
        self.assertEqual(len(list(self.directory.glob('rank-logs/0/*/intent.json'))),2)

    def test_process_death_before_complete_index_has_no_success_proof_and_retries_new_generation(self):
        original=L.exclusive_json;crashed=[]
        def crash_once(path,value):
            if path.name=='index.json' and path.parent.parent.name=='0' and not crashed:
                crashed.append(path.parent);raise SystemExit('injected writer death')
            return original(path,value)
        with mock.patch.object(L,'exclusive_json',side_effect=crash_once):
            with self.assertRaises(SystemExit):self.run_cleanup()
        self.assertFalse(self.journal.state['ranks'][0].get('log_capture'))
        self.assertFalse((crashed[0]/'index.json').exists())
        self.assertFalse(any(e[0]=='remove' for e in self.ops.events))
        retained={p.name:p.read_bytes() for p in crashed[0].iterdir()}
        self.run_cleanup()
        self.assertEqual(retained,{p.name:p.read_bytes() for p in crashed[0].iterdir()})

    def test_nonzero_container_exit_is_preserved_not_confused_with_log_reader_exit(self):
        self.ops.items[0]['State']['ExitCode']=137
        self.run_cleanup()
        row=self.journal.state['ranks'][0]
        directory=self.directory/'rank-logs'/'0'/row['log_capture']['capture_id']
        self.assertEqual(json.loads((directory/'post-inspect.json').read_bytes())['State']['ExitCode'],137)
        self.assertEqual(json.loads((directory/'receipt.json').read_bytes())['exit_code'],0)

    def test_missing_or_boolean_exit_identity_blocks_logs_and_removal(self):
        state=self.ops.items[0]['State']
        for val in (None,False):
            with self.subTest(exit=val):
                state['ExitCode']=val
                with self.assertRaisesRegex(RuntimeError,'exit identity'):self.run_cleanup()
                self.assertFalse(any(e[0] in ('logs','remove') for e in self.ops.events))
        state['ExitCode']=0;self.run_cleanup()

    def test_retained_evidence_is_bound_to_window_attempt_plan_rank_id_and_config(self):
        self.run_cleanup();row=self.journal.state['ranks'][0]
        for target,key in [(self.journal.plan,k) for k in ('window_id','attempt_id','plan_sha256')]+[(row,k) for k in ('rank','container_id','config_sha256')]:
            old=target[key];target[key]=9 if key=='rank' else 'e'*len(old)
            with self.subTest(key=key),self.assertRaises((RuntimeError,ValueError,OSError)):
                L.verified_capture(self.journal,row,validate)
            target[key]=old

    def test_complete_receipt_requires_terminal_status(self):
        self.ops.mutate_log=lambda r,v:v.update(status='running') if r==0 else None
        with self.assertRaises(RuntimeError):self.run_cleanup()
        self.assertFalse(any(e[0]=='remove' for e in self.ops.events))

if __name__=='__main__':unittest.main(verbosity=2)
