"""Synthetic CPU protocol fixtures only. No real corpus, service or capacity evidence."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import q03_capacity as C
import q03_http_service as H
import q03_http_client as CLIENT
import q03_window as W


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        identity = {key: 'a' * 64 for key in C.Q.IDENTITY_FIELDS}
        identity.update(model_repository='SYNTHETIC-CPU-TEST/model', model_revision='b' * 40,
                        tokenizer_revision='c' * 40, tokenizer_assets_sha256={'tokenizer.json': 'd' * 64},
                        transformers_version='TEST-ONLY', tokenizers_version='TEST-ONLY',
                        encoding_source_sha256=C.Q.NATIVE_SOURCES['deepseek_v41_encoding.py'],
                        chat_template_sha256=C.Q.TEMPLATE_SHA, special_tokens_sha256='d' * 64)
        artifacts = {'model_root': str(self.root / 'model'),
                     'weights_manifest': {'path': str(self.root / 'weights.json'), 'sha256': 'a' * 64},
                     'runtime_metadata': {'path': str(self.root / 'runtime.json'), 'sha256': 'e' * 64},
                     'native_sources': {name: {'path': str(self.root / name), 'sha256': sha}
                                        for name, sha in C.Q.NATIVE_SOURCES.items()},
                     'special_tokens_file': 'tokenizer.json'}
        self.export = {'schema_version': 1, 'workload': C.WORKLOAD, 'tokenizer_mode': 'deepseek_v41',
                       'identity': identity, 'artifacts': artifacts, 'request': C.Q.EXTRA,
                       'rows': [{'id': i, 'text': f'SYNTHETIC CPU fixture {i}',
                                 'prompt_ids': [i] + list(range(C.INPUT_TOKENS - 1))} for i in range(4)]}
        path = self.root / 'export.json'; path.write_bytes(C.canonical(self.export))
        # The only bypass is test-local; production has no synthetic/skip-assets mode.
        with patch.object(C.Q, 'verify_artifacts', return_value=[]):
            self.pin = C.prepare(path, self.root / 'prepared')
            self.corpus = C.validate_binding({'workload': C.WORKLOAD, 'corpus': self.pin, 'metric_labels': {'model_name': C.Q.MODEL, 'engine': '0'}})
        self.plan = {'window_id': 'a' * 32, 'attempt_id': 'b' * 32, 'spec_sha256': 'c' * 64,
                     'url': 'http://127.0.0.1:18000', 'selection': {'mode': 'off', 'steering': 'off'},
                     'client': {'toolbox_head': 'd' * 40, 'timeout_seconds': 60,
                         'output': str(self.root / 'capacity.json'), 'capacity': {
                         'workload': C.WORKLOAD, 'corpus': self.corpus, 'corpus_sha256': self.pin['sha256'],
                         'effective_capacity': C.CAPACITY, 'metric_labels': {'model_name': C.Q.MODEL, 'engine': '0'}, 'extra_body': C.request_extra({'mode': 'off', 'steering': 'off'})}}}
        self.plan['plan_sha256'] = C.digest(C.canonical(self.plan))

    def generated(self):
        return {'schema': 'servebench-model/1', 'label': self.plan['attempt_id'],
                'server': {'url': self.plan['url'], 'model': C.Q.MODEL},
                'workload': {'prompts': self.corpus['prompts']['path'], 'prompt_count': 4,
                    'prompts_sha256': self.corpus['prompts_sha256'], 'concurrency': 4, 'max_tokens': 1024,
                    'fixed_length': True, 'top': 20, 'extra_body': self.plan['client']['capacity']['extra_body']},
                'generation': {'sequences': [{'id': r['id'], 'prompt_ids': r['prompt_ids'],
                    'output_ids': list(range(1024)), 'finish_reason': 'length'} for r in self.corpus['rows']]},
                'scores': None, 'score_seconds': None, 'self_agreement': None}

    def prom(self, label):
        numbers = dict(running=0, waiting=0, finished=10, preemptions=0, input_tokens=100, output_tokens=100)
        if label == 'after':
            numbers.update(finished=14, input_tokens=258148, output_tokens=4196)
        return ''.join(f'{name}{{engine="0",model_name="{C.Q.MODEL}"}} {numbers[key]}\n'
                       for key, name in C.METRICS.items()).encode()

    def snapshot(self, plan, label, deadline):
        raw = self.prom(label); path = Path(plan['client']['output']).with_name('capacity-' + label + '.prom')
        C.write_raw(path, raw)
        n = 1 if label == 'before' else 3
        return dict(label=label, identity=C.identity(plan), endpoint=plan['url'] + '/metrics', http_status=200,
                    clock='client-process-monotonic-ns', start_ns=n, end_ns=n+1,
                    raw={'path': str(path), 'sha256': C.digest(raw)}, error=None)

    def run_client(self, mutation=None, code=0):
        calls = []
        def main(argv):
            calls.append(argv)
            value = self.generated()
            if mutation:
                mutation(value)
            Path(argv[argv.index('--output') + 1]).write_bytes(C.canonical(value))
            return code
        with patch.object(C, 'snapshot', side_effect=self.snapshot):
            status = C.run_client(self.plan, main)
        return status, json.loads(Path(self.plan['client']['output']).read_bytes()), calls

    def test_preparation_preserves_exact_real_export_contract_without_resizing(self):
        self.assertEqual(self.corpus['rows'], self.export['rows'])
        self.assertEqual(C.Q.owner.pinned_json(self.corpus['source_export']), self.export)
        self.assertEqual(C.validate_corpus(self.corpus), self.corpus)
        with patch.object(C.Q, 'verify_artifacts', return_value=[]), self.assertRaises(FileExistsError):
            C.prepare(self.root / 'export.json', self.root / 'prepared')

    def test_export_rejects_short_later_duplicate_and_unbound_inputs(self):
        changes = [lambda v: v.update(workload='capacity_256k_c4'), lambda v: v['rows'].pop(),
                   lambda v: v['rows'][1].update(text=v['rows'][0]['text']),
                   lambda v: v['rows'][1].update(prompt_ids=v['rows'][0]['prompt_ids']),
                   lambda v: v['rows'][1].update(prompt_ids=list(range(2048))),
                   lambda v: v['rows'][1]['prompt_ids'].append(9),
                   lambda v: v['rows'][1]['prompt_ids'].__setitem__(0, True),
                   lambda v: v['identity'].pop('model_revision'), lambda v: v['request'].update(truncate_prompt_tokens=64512)]
        for change in changes:
            value = copy.deepcopy(self.export); change(value)
            with self.subTest(change=change), self.assertRaises(RuntimeError): C.validate_export(value)

    def test_missing_real_assets_fail_before_creating_output(self):
        with self.assertRaises(FileNotFoundError): C.prepare(self.root / 'export.json', self.root / 'not-created')
        self.assertFalse((self.root / 'not-created').exists())

    def test_retained_export_and_jsonl_drift_rejected(self):
        path = Path(self.corpus['source_export']['path']); path.write_bytes(b'{}')
        with self.assertRaises(RuntimeError): C.validate_corpus(self.corpus)

    def test_fixed_client_runs_exactly_once_and_never_returns_capacity_pass(self):
        status, value, calls = self.run_client()
        self.assertEqual(status, 0); self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], C.argv(self.plan, self.root / 'capacity-generation.json'))
        outcome = C.validate_output(value, self.plan)
        self.assertEqual(outcome['counts'], {'requests': 4, 'input_tokens': 258048, 'output_tokens': 4096, 'errors': 0})
        self.assertEqual(outcome['overall_status'], 'INCONCLUSIVE')
        self.assertEqual(set(outcome['unknown'].values()), {'UNKNOWN'})
        self.assertNotIn('--apply', calls[0]); self.assertIn('--no-score', calls[0])

    def test_all_steering_requests_preserve_frozen_defaults(self):
        for mode in ('off', 'dual'):
            for state, scale in (('off', None), ('loaded_zero', 0), ('active', 1)):
                extra = C.request_extra({'mode': mode, 'steering': state})
                self.assertEqual({k: extra[k] for k in C.Q.EXTRA}, C.Q.EXTRA)
                self.assertEqual(extra.get('vllm_xargs'), None if scale is None else {
                    'steering_profile': 'heretic-1017-attn-v1', 'steering_scale': scale})

    def test_generation_dropped_error_clipped_duplicate_and_wrong_endpoint_rejected(self):
        changes = [lambda v: v['generation']['sequences'].pop(),
                   lambda v: v['generation']['sequences'][0].update(error='OOM'),
                   lambda v: v['generation']['sequences'][0]['output_ids'].pop(),
                   lambda v: v['generation']['sequences'][0]['prompt_ids'].pop(),
                   lambda v: v['generation']['sequences'][1].update(id=0),
                   lambda v: v['server'].update(url='http://127.0.0.1:18001'),
                   lambda v: v['generation']['sequences'][0].update(finish_reason='stop'),
                   lambda v: v['workload'].update(max_tokens=1)]
        for change in changes:
            value = copy.deepcopy(self.generated()); change(value)
            with self.subTest(change=change), self.assertRaises(RuntimeError): C.validate_generation(value, self.plan)

    def test_generation_failure_retains_raw_after_and_error_not_retry(self):
        status, value, calls = self.run_client(code=1)
        self.assertEqual(status, 1); self.assertEqual(len(calls), 1)
        self.assertTrue(value['error']); self.assertEqual(set(value['scrapes']), {'before', 'after'})
        self.assertIsNotNone(value['generation'])
        with self.assertRaises(RuntimeError): C.validate_output(value, self.plan)

    def test_non_idle_before_never_sends_work(self):
        original = self.prom
        self.prom = lambda label: original(label).replace(b'num_requests_running{', b'num_requests_running{', 1).replace(
            f'num_requests_running{{engine="0",model_name="{C.Q.MODEL}"}} 0'.encode(),
            f'num_requests_running{{engine="0",model_name="{C.Q.MODEL}"}} 1'.encode())
        status, value, calls = self.run_client()
        self.assertEqual(status, 1); self.assertFalse(calls); self.assertIn('not idle', value['error'])

    def test_missing_preemption_metric_not_treated_as_zero(self):
        original = self.prom
        self.prom = lambda label: b'\n'.join(line for line in original(label).splitlines() if b'preemptions' not in line)
        status, value, calls = self.run_client()
        self.assertEqual(status, 1); self.assertFalse(calls); self.assertIn('missing required counters', value['error'])

    def test_prom_drift_nonfinite_foreign_work_and_preemptions_fail(self):
        status, value, _ = self.run_client(); self.assertEqual(status, 0)
        snap = value['scrapes']['after']; path = Path(snap['raw']['path']); good = path.read_bytes()
        for raw in (good.replace(b' 14\n', b' 15\n'), good.replace(b' 14\n', b' 0\n'),
                    good.replace(b' 14\n', b' NaN\n'), good.replace(b' 14\n', b' Inf\n'),
                    good.replace(b' 14\n', b' 14.0000000000000001\n'),
                    good.replace(b' 14\n', b' 14 not-a-timestamp\n'),
                    good.replace(b'num_preemptions_total{engine="0",model_name="deepseek-v4.1-flash"} 0',
                                 b'num_preemptions_total{engine="0",model_name="deepseek-v4.1-flash"} 1')):
            path.write_bytes(raw); snap['raw']['sha256'] = C.digest(raw)
            with self.assertRaises((RuntimeError, ValueError)): C.validate_output(value, self.plan)
        path.write_bytes(good); snap['raw']['sha256'] = '0' * 64
        with self.assertRaisesRegex(RuntimeError, 'raw capacity scrape changed'): C.validate_output(value, self.plan)

    def test_metrics_foreign_model_duplicate_series_and_label_drift_rejected(self):
        _, value, _ = self.run_client()
        snap = value['scrapes']['after']; path = Path(snap['raw']['path']); good = path.read_bytes()
        variants = [good.replace(b'deepseek-v4.1-flash', b'foreign-model'),
                    good + good.splitlines(keepends=True)[0],
                    good.replace(b'model_name=', b'model_name="deepseek-v4.1-flash",model_name='),
                    good.replace(b'model_name=', b'engine="foreign",model_name=')]
        for raw in variants:
            path.write_bytes(raw); snap['raw']['sha256'] = C.digest(raw)
            with self.assertRaises(RuntimeError): C.validate_output(value, self.plan)

    def test_embedded_corpus_hash_or_boolean_semantics_rejected(self):
        for change in (lambda q: q.update(corpus_sha256='0' * 64),
                       lambda q: q['extra_body'].update(seed=False),
                       lambda q: q.update(argv=['anything'])):
            plan = copy.deepcopy(self.plan); change(plan['client']['capacity'])
            with self.assertRaises(RuntimeError): C.argv(plan, self.root / 'not-created.json')

    def test_outer_whitespace_cannot_hide_required_foreign_or_duplicate_series(self):
        raw = self.prom('before')
        whitespace = b'\n'.join(b' \t' + line + b' \t' for line in raw.splitlines())
        labels = self.plan['client']['capacity']['metric_labels']
        self.assertEqual(C._counter_series(raw, C.METRICS, labels), C._counter_series(whitespace, C.METRICS, labels))
        extra = b'  vllm:num_preemptions_total{engine="0",model_name="deepseek-v4.1-flash"} 1\n'
        for suffix in (extra, extra.replace(b'engine="0"', b'engine="foreign"'),
                       extra.replace(b'deepseek-v4.1-flash', b'foreign')):
            with self.assertRaises(RuntimeError): C._counter_series(raw + suffix, C.METRICS, labels)

    def test_large_integral_counters_preserve_one_preemption(self):
        _, value, _ = self.run_client()
        baseline = 10 ** 50
        for label, number in (('before', baseline), ('after', baseline + 1)):
            snap = value['scrapes'][label]; path = Path(snap['raw']['path'])
            raw = path.read_bytes().replace(b'num_preemptions_total{engine="0",model_name="deepseek-v4.1-flash"} 0',
                ('num_preemptions_total{engine="0",model_name="deepseek-v4.1-flash"} ' + str(number)).encode())
            path.write_bytes(raw); snap['raw']['sha256'] = C.digest(raw)
        with self.assertRaisesRegex(RuntimeError, 'traffic/preemption'): C.validate_output(value, self.plan)

    def test_schema_five_inputs_pin_the_native_export_and_helper(self):
        binding = {'checkout': str(ROOT), 'http': {'schema_version': 5,
            'capacity': {'workload': C.WORKLOAD, 'corpus': self.pin,
                         'metric_labels': {'model_name': C.Q.MODEL, 'engine': '0'}},
            'toolbox': {'path': '/fixture/toolbox', 'git_config_sha256': 'a' * 64}},
            'toolbox_inventory': {'files': {}}}
        with patch.object(H.owner, 'input_contract', return_value={'files': [], 'directories': []}), \
             patch.object(H, 'capacity_module', return_value=C), \
             patch.object(C, 'validate_binding', return_value=self.corpus):
            inputs = H.input_contract(binding)
        self.assertIn(self.corpus['source_export'], inputs['files'])
        self.assertIn(self.pin, inputs['files'])
        self.assertIn({'path': str(ROOT / 'scripts/q03_capacity.py'),
                       'sha256': H.CAPACITY_SOURCE_SHA256}, inputs['files'])

    def test_wrong_boot_or_false_capacity_pass_rejected(self):
        _, good, _ = self.run_client()
        for change in (lambda v: v['identity'].update(attempt_id='f' * 32),
                       lambda v: v['scrapes']['before']['identity'].update(spec_sha256='f' * 64),
                       lambda v: v.update(capacity_status='PASS'),
                       lambda v: v['unknown'].update(native_c4_admission='PASS'),
                       lambda v: v.update(counts={'errors': 0})):
            value = copy.deepcopy(good); change(value)
            with self.assertRaises(RuntimeError): C.validate_output(value, self.plan)

    def test_one_attempt_not_matrix_or_failure_retry(self):
        session = SimpleNamespace(manifest={'http_service': {'schema_version': 5},
            'jobs': [{'selection': self.plan['selection']}]}, state={'attempt_results': []})
        self.assertEqual(H.next_selection(session), self.plan['selection'])
        for status in ('passed', 'failed', 'inconclusive'):
            session.state['attempt_results'] = [{'outcome': status}]
            with self.assertRaises(RuntimeError): H.next_selection(session)

    def test_schema_five_normal_rendering_is_closed_and_capacity_preserving(self):
        from test_q03_http_service import NormalRendererTests
        renderer = NormalRendererTests(); renderer.setUp()
        renderer.binding['http'].update(schema_version=5, capacity={
            'workload': C.WORKLOAD, 'corpus': self.pin,
            'metric_labels': {'model_name': C.Q.MODEL, 'engine': '0'}})
        with patch.object(H, 'capacity_module', return_value=C), \
             patch.object(C, 'client_plan', return_value=self.plan['client']['capacity']):
            plan = renderer.plan()
        self.assertEqual(plan['client']['capacity']['workload'], C.WORKLOAD)
        self.assertEqual(set(plan['client']['capacity_sources']), {
            'q03_capacity', 'q03_qualification', 'q03_owner_model', 'q03_held_window'})
        self.assertNotIn('qualification', plan['client'])
        self.assertTrue(plan['client']['output'].endswith('/capacity.json'))
        for row in plan['ranks']:
            args = row['python_argv']
            for flag, count in (('--max-model-len', 1048576), ('--max-num-seqs', 16),
                                ('--max-num-batched-tokens', 8192), ('--kv-cache-memory-bytes', 11274289152)):
                self.assertEqual(args[args.index(flag) + 1], str(count))
            self.assertIn('q03_http_worker.HttpObservation', args)
            self.assertNotIn('q03_capacity_allocator_snapshot', args)

    def test_controller_source_pin_and_recovery_inventory(self):
        self.assertEqual(H.capacity_module().WORKLOAD, C.WORKLOAD)
        self.assertEqual(H.CAPACITY_SOURCE_SHA256, C.digest((ROOT / 'scripts/q03_capacity.py').read_bytes()))
        self.assertIn('q03_capacity.py', H.SOURCE_FILES)
        self.assertIn('q03_capacity.py', W.RuntimeOps().recovery_sources())
        with patch.object(H, 'CAPACITY_SOURCE_SHA256', '0' * 64), self.assertRaises(RuntimeError): H.capacity_module()

    def test_unknown_or_preloaded_client_closure_rejected(self):
        with self.assertRaises(RuntimeError): CLIENT.load_capacity({'capacity_sources': {}})
        pins = {name: '0' * 64 for name in ('q03_capacity', 'q03_qualification', 'q03_owner_model', 'q03_held_window')}
        with self.assertRaises(RuntimeError): CLIENT.load_capacity({'capacity_sources': pins})

    def test_actual_snapshot_retains_raw_bytes_and_endpoint_bracket(self):
        raw = self.prom('before') + b'# retained spacing\n'
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, n): return raw
            def geturl(self): return self_url + '/metrics'
        self_url = self.plan['url']
        with patch.object(C.urllib.request, 'build_opener', return_value=SimpleNamespace(open=lambda *a, **kw: Response())):
            snap = C.snapshot(self.plan, 'before', C.time.monotonic() + 10)
        self.assertEqual(Path(snap['raw']['path']).read_bytes(), raw)
        self.assertEqual(C.counters(snap, self.plan, 'before')['finished'], 10)
        self.assertLessEqual(snap['start_ns'], snap['end_ns'])

    def test_snapshot_timeout_is_unknown_and_raw_empty_not_success(self):
        with patch.object(C.urllib.request, 'build_opener', return_value=SimpleNamespace(open=lambda *a, **kw: (_ for _ in ()).throw(TimeoutError()))):
            snap = C.snapshot(self.plan, 'before', C.time.monotonic() + 10)
        self.assertIsNotNone(snap['error']); self.assertEqual(Path(snap['raw']['path']).read_bytes(), b'')
        with self.assertRaises(RuntimeError): C.counters(snap, self.plan, 'before')

    def test_redirect_refused(self):
        with self.assertRaises(RuntimeError): C.NoRedirect().redirect_request(None, None, 302, None, {}, 'http://foreign/')

    def test_real_isolated_client_dispatches_fixed_source_without_network(self):
        toolbox = self.root / 'toolbox' / 'servebench'; toolbox.mkdir(parents=True)
        (toolbox / '__init__.py').write_text('')
        # Explicit synthetic transport fake, not HTTP/device or producer evidence.
        source = '''import json, urllib.request
from pathlib import Path
VALUES = %r
RAWS = %r
COUNT = 0
URL = %r
class Response:
 status = 200
 def __enter__(self): return self
 def __exit__(self, *a): pass
 def read(self, n):
  global COUNT
  raw = RAWS[COUNT]; COUNT += 1; return raw
 def geturl(self): return URL + '/metrics'
class Opener:
 def open(self, *a, **kw): return Response()
urllib.request.build_opener = lambda *a: Opener()
def main(argv):
 Path(argv[argv.index('--output') + 1]).write_text(json.dumps(VALUES))
 return 0
''' % (self.generated(), [self.prom('before'), self.prom('after')], self.plan['url'])
        (toolbox / '__main__.py').write_text(source)
        self.plan['client']['capacity_sources'] = {name: hashlib.sha256((ROOT / 'scripts' / (name + '.py')).read_bytes()).hexdigest()
            for name in ('q03_capacity', 'q03_qualification', 'q03_owner_model', 'q03_held_window')}
        self.plan['client'].update(toolbox_path=str(toolbox.parent), toolbox_files={
            'servebench/' + path.name: C.digest(path.read_bytes()) for path in toolbox.iterdir()})
        self.plan.pop('plan_sha256'); self.plan['plan_sha256'] = C.digest(C.canonical(self.plan))
        path = self.root / 'plan.json'; path.write_bytes(C.canonical(self.plan))
        result = subprocess.run([sys.executable, '-I', '-B', str(ROOT / 'scripts/q03_http_client.py'),
            '--plan', str(path), '--sha256', C.digest(path.read_bytes())], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads((self.root / 'client-receipt.json').read_bytes())
        self.assertEqual(receipt['exit_code'], 0, receipt)
        value = json.loads((self.root / 'capacity.json').read_bytes())
        self.assertEqual(C.validate_output(value, self.plan)['overall_status'], 'INCONCLUSIVE')


if __name__ == '__main__':
    unittest.main()
