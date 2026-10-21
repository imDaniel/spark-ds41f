"""Independent source/capsule closure probes. Filesystem only, no child launch."""
import copy
import hashlib
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
ROOT=Path(os.environ.get('Q03_LOG_ROOT', Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(ROOT/'scripts'))
import q03_window as W
import q03_window_watchdog as D
import q03_http_service as H
import q03_owner_model as O

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()
def sha(raw):return hashlib.sha256(raw).hexdigest()

class SourceBindingFaults(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='q03-source-log-');self.addCleanup(self.temp.cleanup)
  self.root=Path(self.temp.name)
  for name in ('Popen','run','check_call','check_output'):
   p=mock.patch.object(subprocess,name,side_effect=AssertionError('CPU-only source probe'));p.start();self.addCleanup(p.stop)
  p=mock.patch.object(socket,'create_connection',side_effect=AssertionError('no network'));p.start();self.addCleanup(p.stop)
 def capsule(self):
  sources=W.RuntimeOps().recovery_sources()
  return dict(window_id='a'*32,session_dir=str(self.root),deadline_epoch=100,heartbeat_timeout_s=20,recovery_timeout_s=30,
   recovery_command=[sys.executable,str(ROOT/'scripts/q03_window.py'),'recover','--session-dir',str(self.root)],
   recovery_script_sha256=sources['q03_window.py'],recovery_source_sha256=sources)
 def save(self,capsule):
  raw=canonical(capsule);(self.root/'capsule.json').write_bytes(raw);(self.root/'capsule.sha256').write_text(sha(raw))
 def test_watchdog_requires_new_log_source_and_rejects_missing_or_changed_pin(self):
  capsule=self.capsule();self.save(capsule)
  sources=capsule['recovery_source_sha256']
  self.assertEqual(sources['q03_rank_logs.py'],sha((ROOT/'scripts/q03_rank_logs.py').read_bytes()))
  self.assertIn('q03_rank_logs.py',O.SOURCE_FILES)
  self.assertEqual(D.load_capsule(self.root)[0],capsule)
  for mutation in ('missing','wrong'):
   bad=copy.deepcopy(capsule)
   if mutation=='missing':del bad['recovery_source_sha256']['q03_rank_logs.py']
   else:bad['recovery_source_sha256']['q03_rank_logs.py']='0'*64
   self.save(bad)
   with self.subTest(mutation=mutation),self.assertRaises(RuntimeError):D.load_capsule(self.root)
 def test_http_log_plan_remains_bound_to_admitted_descriptor_and_capsule(self):
  plan=dict(window_id='a'*32,attempt_id='b'*32,other='fixed')
  plan['plan_sha256']=sha(canonical(plan))
  capsule=self.capsule();self.save(capsule)
  attempt=self.root/'attempts'/plan['attempt_id'];attempt.mkdir(parents=True)
  descriptor=dict(window_id=plan['window_id'],capsule_sha256=sha((self.root/'capsule.json').read_bytes()),
   command_plan_sha256=sha(canonical([['http_service',plan]])))
  admitted=dict(descriptor_sha256=sha(canonical(descriptor)),command_plan_sha256=descriptor['command_plan_sha256'])
  (attempt/'descriptor.json').write_bytes(canonical(descriptor));(attempt/'admitted.json').write_bytes(canonical(admitted))
  session=SimpleNamespace(directory=self.root,capsule=capsule)
  H.verify_plan_identity(session,plan)
  for target,key in ((descriptor,'capsule_sha256'),(descriptor,'command_plan_sha256'),(admitted,'descriptor_sha256')):
   old=target[key];target[key]='e'*64
   (attempt/'descriptor.json').write_bytes(canonical(descriptor));(attempt/'admitted.json').write_bytes(canonical(admitted))
   with self.subTest(key=key),self.assertRaises(RuntimeError):H.verify_plan_identity(session,plan)
   target[key]=old
  (attempt/'descriptor.json').write_bytes(canonical(descriptor));(attempt/'admitted.json').write_bytes(canonical(admitted))
  capsule['unexpected']='changed';self.save(capsule)
  with self.assertRaises(RuntimeError):H.verify_plan_identity(session,plan)
 def test_actual_owner_and_http_entrypoints_dispatch_shared_log_barrier(self):
  import ast
  for module,name in ((O,'quiesce_owner'),(H,'quiesce_http')):
   tree=ast.parse(Path(module.__file__).read_bytes())
   function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
   imports=[n for n in ast.walk(function) if isinstance(n,ast.ImportFrom) and n.module=='q03_rank_logs']
   self.assertEqual([a.name for n in imports for a in n.names],['quiesce_with_logs'])
   calls=[n for n in ast.walk(function) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='quiesce_with_logs']
   self.assertEqual(len(calls),1)
   self.assertFalse(any(isinstance(n,ast.Constant) and n.value in ('start','remove') for n in ast.walk(function)))

if __name__=='__main__':unittest.main(verbosity=2)
