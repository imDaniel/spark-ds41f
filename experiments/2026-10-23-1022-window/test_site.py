import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch
import subprocess

spec=importlib.util.spec_from_file_location('site_adapter',Path(__file__).with_name('site_adapter.py'))
s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
class BindingTests(unittest.TestCase):
    def test_queue_needs_both_metrics(self):
        for text in ('','vllm:num_requests_running 0','vllm:num_requests_running 0\nvllm:num_requests_waiting 0.1'):
            with self.assertRaises(ValueError):s.queue(text)
    def test_queue_sums_engines(self):
        self.assertEqual(s.queue('vllm:num_requests_running{engine="0"} 1.0\nvllm:num_requests_running{engine="1"} 2.0\nvllm:num_requests_waiting 0.0'),{'running':3,'waiting':0})
    def test_firewall_scoped(self):
        m={'window_id':'123'}
        c=s.firewall(m,'-D')
        self.assertEqual(c[:5],['sudo','-n','iptables','-w','5'])
        self.assertIn('spark-window-123',c);self.assertIn('8000',c);self.assertIn('lo',c)
        self.assertNotIn('-F',c)
    def test_command_errors_not_success(self):
        with patch.object(s.subprocess,'run',return_value=subprocess.CompletedProcess(['x'],2,'','failure')):
            with self.assertRaises(RuntimeError):s.run(['x'])
    def test_alias_drift_rejected(self):
        with patch.object(s.Path,'read_text',return_value='fixture'),patch.object(s,'api',return_value=b'{"data":[{"id":"changed"}]}'):
            with self.assertRaises(ValueError):s.aliases({'gateway_key_file':'unused','gateway_base':'unused','gateway_model_ids':['old'],'gateway_alias_prefix':'old'})
    def test_drain_does_not_cut_live_stream(self):
        m={'window_id':'fixture'};calls=[]
        def run(args,**kwargs):
            calls.append(args)
            if '-C' in args:return subprocess.CompletedProcess(args,1,'','')
            if args[0]=='ss':return subprocess.CompletedProcess(args,0,'0 0 192.168.88.125:8000 192.168.88.210:1234','')
            return subprocess.CompletedProcess(args,0,'','')
        with patch.object(s,'run',side_effect=run),patch.object(s,'api',return_value=b'vllm:num_requests_running 1\nvllm:num_requests_waiting 0'),patch.object(s.time,'monotonic',side_effect=[0,1,301]),patch.object(s.time,'sleep'):
            with self.assertRaises(RuntimeError):s.block(m)
        self.assertFalse(any('-I' in c and '--ctstate' not in c for c in calls))
    def test_loopback_probe_does_not_block_external_drain(self):
        self.assertEqual(s.external_connections('0 0 127.0.0.1:8000 127.0.0.1:45'), [])
        self.assertEqual(len(s.external_connections('0 0 192.168.88.125:8000 192.168.88.210:45')),1)
    def test_restore_removes_both_owned_phases(self):
        calls=[]
        def run(args, **kwargs):
            calls.append(args);return subprocess.CompletedProcess(args,0,'','')
        with patch.object(s,'run',side_effect=run):s.restore_ingress({'window_id':'fixture'})
        deletes=[c for c in calls if '-D' in c]
        self.assertEqual(len(deletes),2)
        self.assertTrue(all('spark-window-fixture' in c for c in deletes))
        self.assertTrue(any('--ctstate' in c for c in deletes))
    def test_partial_gate_is_observed_as_draining(self):
        def run(args, **kwargs):
            return subprocess.CompletedProcess(args,0 if '--ctstate' in args else 1,'','')
        with patch.object(s,'run',side_effect=run):self.assertEqual(s.ingress_stage({'window_id':'fixture'}),'draining')
    def test_unrelated_gateway_model_is_outside_window(self):
        m={'gateway_key_file':'unused','gateway_base':'unused','gateway_alias_prefix':'DeepSeek-V4.1-Flash','gateway_model_ids':['DeepSeek-V4.1-Flash']}
        with patch.object(s.Path,'read_text',return_value='fixture'),patch.object(s,'api',return_value=b'{"data":[{"id":"DeepSeek-V4.1-Flash"},{"id":"office-qwen3.8-iq3"}]}'):
            self.assertEqual(s.aliases(m),s.digest(m['gateway_model_ids']))
if __name__=='__main__':unittest.main()
