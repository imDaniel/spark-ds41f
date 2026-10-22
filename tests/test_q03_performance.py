"""CPU-only screen contracts. Synthetic timings are not native field evidence."""
import copy
import hashlib
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'scripts'))
import q03_performance as P
import q03_http_service as H
import test_q03_qualification as qualification_tests
from servebench import prom, report


class ScreenTest(unittest.TestCase):
    def setUp(self):
        helper = qualification_tests.QualificationTest('test_preparation_is_deterministic_and_not_tokenization')
        helper.setUp(); self.addCleanup(helper.doCleanups)
        self.root, self.corpus, self.pin = helper.root, helper.corpus, helper.pin
        inventory = json.loads((ROOT / 'scripts/q03_http_toolbox.json').read_text())
        self.plan = {'window_id': 'a' * 32, 'attempt_id': 'b' * 32, 'plan_sha256': 'c' * 64,
                     'url': 'http://127.0.0.1:18000', 'selection': {'mode': 'off', 'steering': 'active'},
                     'client': {'output': str(self.root / 'performance-screen.json'),
                                'runtime_context': str(self.root / 'runtime-context.json'),
                                'toolbox_head': inventory['head'], 'toolbox_path': str(ROOT),
                                'toolbox_files': inventory['files'], 'timeout_seconds': 195,
                                'performance_screen': {'workload': P.WORKLOAD, 'corpus': self.corpus,
                                    'corpus_sha256': self.pin['sha256'], 'stage1_capsule_sha256': 'f' * 64,
                                    'state_identity': {'native': 'TEST-ONLY'}, 'concurrencies': [1, 8, 16],
                                    'cell_seconds': 60, 'metric_labels': {'model_name': P.Q.MODEL, 'engine': '0'}}}}
        self.context = {k: self.plan[k] for k in ('window_id', 'attempt_id', 'plan_sha256', 'url')}
        self.context.update(ranks=[{'rank': i, 'container_id': str(i + 1) * 64, 'config_sha256': 'd' * 64} for i in range(4)],
                            ready_sha256='e' * 64)
        Path(self.plan['client']['runtime_context']).write_bytes(P.canonical(self.context))

    def text(self, count):
        values = (count, count * 64, count * 2048, 0, 0, 0)
        return '\n'.join(f'{name}{{model_name="{P.Q.MODEL}",engine="0"}} {value}' for name, value in zip(P.METRICS, values))

    def fixture(self, c=1, offset=0, completed_before=0):
        rows = [{'sent': 111 + offset + i / 1000, 'first': 112 + offset + i / 1000, 'end': 114 + offset + i / 1000,
                 'events': [[112 + offset + i / 1000, 1], [113 + offset + i / 1000, 64]], 'prompt_tokens': 2048,
                 'completion_tokens': 64, 'finish_reason': 'length', 'token_times': 'exact', 'error': None,
                 'prompt_id': i % 16, 'slot': i, 'index': i} for i in range(c)]
        scrapes = [{'begin_monotonic': t + offset + .01, 'end_monotonic': t + offset + .02, 'text': self.text(n + completed_before)}
                   for t, n in zip((99, 110, 120, 130, 140, 141), (0, 0, c, c, c, c))]
        parsed = [{k: prom.parse(row['text'])[name] for k, name in prom.VLLM.items() if name in P.METRICS} for row in scrapes]
        raw = {'start': 100 + offset, 'window': [110 + offset, 140 + offset],
               'boundaries': [t + offset for t in (110, 120, 130, 140)], 'scrape_times': [t + offset for t in (110, 120, 130, 140)],
               'scrapes': parsed[1:-1], 'fences': {'times': [99 + offset, 141 + offset], 'scrapes': [parsed[0], parsed[-1]]},
               'records': rows, 'rejected': 0, 'documents': []}
        evidence = {'concurrency': c, 'context_sha256': P.digest(P.canonical(self.context)), 'url': self.plan['url'],
                    'metrics_url': self.plan['url'] + '/metrics', 'head_boot_id': '12345678-1234-1234-1234-123456789abc', 'started_monotonic': 98 + offset,
                    'ended_monotonic': 142 + offset, 'requests': [{k: v for k, v in r.items() if k not in ('prompt_id', 'slot', 'index')} for r in rows],
                    'scrapes': scrapes, 'raw_run': raw, 'conditions': P.UNKNOWN_CONDITIONS, 'error': None}
        work = {'mode': 'closed', 'concurrency': c, 'rate': 1.0, 'warmup_s': 10.0, 'duration_s': 30.0,
                'batches': 3, 'max_tokens': 64, 'fixed_length': True, 'seed': 0, 'max_inflight': 512,
                'context_tokens': 0, 'prompts': self.corpus['prompts']['path'], 'prompt_count': 16,
                'prompts_sha256': self.corpus['prompts_sha256'], 'extra_body': P.EXTRA}
        rounded = [{k: round(v - 100 - offset, 4) if k in ('sent', 'first', 'end') else v for k, v in r.items() if k != 'events'} |
                   {'events': [[round(t - 100 - offset, 4), n] for t, n in r['events']]} for r in rows]
        value = {'schema': 'servebench/1', 'label': 'active1_c' + str(c),
                 'server': {'url': self.plan['url'], 'model': P.Q.MODEL, 'adapter': 'vllm'},
                 'workload': work, 'summary': report.summarize(raw), 'requests': rounded}
        return value, evidence

    def test_finite_args_match_preregistered_scope(self):
        for c in (1, 8, 16):
            args = P.argv(self.plan, c, '/tmp/not-executed.json')
            self.assertEqual(args[:2], ['run', self.plan['url']])
            for key, value in (('--mode', 'closed'), ('--concurrency', str(c)), ('--warmup', '10'),
                               ('--duration', '30'), ('--batches', '3'), ('--max-tokens', '64'), ('--context', '0')):
                self.assertEqual(args[args.index(key) + 1], value)
            self.assertEqual(json.loads(args[args.index('--extra-body') + 1]), P.EXTRA)
            self.assertIn('--save-requests', args)
            self.assertNotIn('--natural-length', args)
        for c in (True, 2, 32, '8'):
            with self.assertRaises(RuntimeError): P.argv(self.plan, c, '/unused')

    def test_complete_synthetic_cell_is_screen_only(self):
        for c in (1, 8, 16):
            value, evidence = self.fixture(c)
            self.assertTrue(P.validate_cell(value, evidence, self.plan, c))

    def test_raw_numeric_and_label_faults_refused(self):
        base = self.text(0)
        for replacement in ('NaN', 'Infinity', '1_00', '1e-400', '-1', '100.000000000000000001'):
            with self.subTest(replacement=replacement), self.assertRaises((RuntimeError, ValueError)):
                P.native_series(base.replace(' 0', ' ' + replacement, 1), self.plan['client']['performance_screen']['metric_labels'])
        exact = P.native_series(base.replace(' 0', ' 9007199254740993', 1), self.plan['client']['performance_screen']['metric_labels'])
        self.assertEqual(P.totals(exact)[P.METRICS[0]], 9007199254740993)
        for text in (base.replace('engine="0"', 'engine="1"'), '\n'.join(base.splitlines()[:-1]),
                     base + '\n' + base.splitlines()[0], base.replace(' 0', ' 0 9999999999999999999999999', 1)):
            with self.assertRaises(RuntimeError): P.native_series(text, self.plan['client']['performance_screen']['metric_labels'])

    def test_raw_timing_counts_summary_and_saved_projection_are_bound(self):
        mutations = [lambda v, e: v['workload'].update(concurrency=8),
                     lambda v, e: e['raw_run']['window'].__setitem__(1, 150),
                     lambda v, e: e['raw_run']['records'][0].update(index=2),
                     lambda v, e: e['raw_run']['records'][0].update(sent=140),
                     lambda v, e: e['raw_run']['records'][0]['events'][0].__setitem__(0, 111.9),
                     lambda v, e: e['raw_run']['records'][0].update(prompt_tokens=2047),
                     lambda v, e: e['scrapes'][-1].update(text=self.text(2)),
                     lambda v, e: e['scrapes'][2].update(text=self.text(2)),
                     lambda v, e: e['scrapes'].pop(),
                     lambda v, e: e.pop('head_boot_id'),
                     lambda v, e: e['raw_run']['fences']['times'].__setitem__(0, -10000),
                     lambda v, e: e['raw_run']['scrapes'][1].update(generation_tokens=123456),
                     lambda v, e: e['scrapes'][2].update(begin_monotonic=100),
                     lambda v, e: v['summary']['output_tps'].update(mean=999),
                     lambda v, e: v['summary']['latency']['ttft_s'].update(p99=999),
                     lambda v, e: v['requests'][0].update(first=999),
                     lambda v, e: e['requests'][0].update(completion_tokens=63)]
        for mutation in mutations:
            value, evidence = copy.deepcopy(self.fixture())
            mutation(value, evidence)
            with self.subTest(mutation=mutation), self.assertRaises((RuntimeError, ValueError)):
                P.validate_cell(value, evidence, self.plan, 1)

    def test_runtime_context_rejects_foreign_endpoint_boot_or_attempt(self):
        P.validate_context(self.context, self.plan)
        for mutation in (lambda c: c.update(url='http://foreign'), lambda c: c.update(attempt_id='0' * 32),
                         lambda c: c['ranks'][1].update(container_id=c['ranks'][0]['container_id']),
                         lambda c: c['ranks'].pop()):
            value = copy.deepcopy(self.context); mutation(value)
            with self.assertRaises(RuntimeError): P.validate_context(value, self.plan)

    def test_stop_first_cell_failure_and_inconclusive_receipt(self):
        with patch.object(P, 'run_cell', return_value={'concurrency': 1, 'exit_code': 1,
                         'output_sha256': None, 'evidence_sha256': 'f' * 64}) as run:
            self.assertEqual(P.run_client(self.plan, None), 1)
            self.assertEqual(run.call_count, 1)
        value = json.loads(Path(self.plan['client']['output']).read_text())
        self.assertEqual(value['capacity'], 'INCONCLUSIVE'); self.assertEqual(value['performance'], 'INCONCLUSIVE')
        self.assertEqual(value['conditions']['thermal'], 'UNKNOWN')
        with self.assertRaises(RuntimeError): P.validate_output(value, self.plan)

    def test_deadline_retains_failure_and_does_not_retry(self):
        from servebench import client, load
        originals = client.stream_chat, prom.fetch, load.run
        self.addCleanup(lambda: setattr(client, 'stream_chat', originals[0]))
        self.addCleanup(lambda: setattr(prom, 'fetch', originals[1]))
        self.addCleanup(lambda: setattr(load, 'run', originals[2]))
        self.plan['client']['performance_screen']['cell_seconds'] = .03  # injected CPU timer, not admission
        began = time.monotonic()
        result = P.run_cell(self.plan, 1, lambda argv: time.sleep(2), self.context)
        self.assertLess(time.monotonic() - began, 1)
        self.assertEqual(result['exit_code'], 1)
        value = json.loads((self.root / 'active1_c1.evidence.json').read_text())
        self.assertIn('hard deadline', value['error']); self.assertIsNone(value['raw_run'])

    def test_complete_three_cell_receipt_and_native_continuity(self):
        total = 0
        def fake_cell(plan, c, main, context, previous):
            nonlocal total
            index = list(P.CONCURRENCIES).index(c)
            value, evidence = self.fixture(c, offset=index * 50, completed_before=total)
            total += c
            path = self.root / ('active1_c' + str(c) + '.json')
            ev_path = path.with_suffix('.evidence.json')
            path.write_bytes(P.canonical(value)); ev_path.write_bytes(P.canonical(evidence))
            return {'concurrency': c, 'exit_code': 0, 'output_sha256': P.digest(path.read_bytes()),
                    'evidence_sha256': P.digest(ev_path.read_bytes())}
        with patch.object(P, 'run_cell', side_effect=fake_cell) as run:
            self.assertEqual(P.run_client(self.plan, None), 0)
            self.assertEqual(run.call_count, 3)
        value = json.loads(Path(self.plan['client']['output']).read_text())
        self.assertEqual(P.validate_output(value, self.plan)['performance'], 'INCONCLUSIVE')
        path = self.root / 'active1_c8.evidence.json'; evidence = json.loads(path.read_text())
        evidence['head_boot_id'] = '00000000-0000-0000-0000-000000000000'
        path.write_bytes(P.canonical(evidence)); value['cells'][1]['evidence_sha256'] = P.digest(path.read_bytes())
        with self.assertRaisesRegex(RuntimeError, 'reboot'): P.validate_output(value, self.plan)

    def test_owned_runtime_context_and_existing_fixed_supervisor_command(self):
        Path(self.plan['client']['runtime_context']).unlink()
        directory = self.root / 'attempt' / 'http'; directory.mkdir(parents=True)
        ready = b'{"synthetic":true}'
        (directory / 'ready-observed.json').write_bytes(ready)
        calls = []
        ops = SimpleNamespace(plan=self.plan, binding={'checkout': str(ROOT)},
                              journal=SimpleNamespace(directory=directory, state={'ranks': self.context['ranks']}),
                              check_authority=lambda: None, mutate=lambda *args: calls.append(args))
        ops.client_scope = lambda index=None: H.HttpOps.client_scope(ops, index)
        H.HttpOps.run_client(ops)
        context = json.loads(Path(self.plan['client']['runtime_context']).read_text())
        self.assertEqual(context['ready_sha256'], P.digest(ready))
        self.assertEqual(context['ranks'], self.context['ranks'])
        rank, kind, args, seconds = calls[0]
        self.assertEqual((rank, kind, seconds), (0, 'http_client', 195))
        self.assertEqual(args[:4], ['python3', '-I', '-B', str(ROOT / 'scripts/q03_http_client.py')])
        self.assertEqual(args[4], '--plan'); self.assertEqual(args[6], '--sha256')

    def test_exact_native_inherited_transport_forwarding_and_conflict(self):
        from q03_native197_steering_forward import _forward_steering_fields
        value = _forward_steering_fields(None, copy.deepcopy(P.EXTRA))
        self.assertEqual(value['vllm_xargs'], {'steering_profile': 'heretic-1017-attn-v1', 'steering_scale': 1})
        for key, other in (('steering_profile', 'foreign'), ('steering_scale', 0)):
            request = dict(P.EXTRA, vllm_xargs={key: other})
            with self.assertRaisesRegex(ValueError, 'Conflicting'): _forward_steering_fields(None, request)
        self.assertNotIn('vllm_xargs', P.EXTRA)  # no transport normalization changed the registered suite

    def test_source_pin_and_fixed_client_route(self):
        self.assertEqual(H.performance_module().WORKLOAD, P.WORKLOAD)
        import q03_window
        self.assertIn('q03_performance.py', q03_window.RuntimeOps().recovery_sources())
        self.assertIn('q03_performance.py', H.SOURCE_FILES)

    def test_isolated_performance_source_closure_rejects_wrong_or_extra_pin(self):
        pins = {name: P.digest((ROOT / 'scripts' / (name + '.py')).read_bytes())
                for name in ('q03_performance', 'q03_owner_model', 'q03_held_window')}
        code = ('import json,sys;sys.path.insert(0,sys.argv[1]);import q03_http_client as c;'
                'f=c.load_performance({"performance_sources":json.loads(sys.argv[2])});'
                'print(f.__module__)')
        for mutation, success in ((lambda p: None, True),
                                  (lambda p: p.update(q03_performance='0' * 64), False),
                                  (lambda p: p.update(arbitrary='0' * 64), False)):
            value = copy.deepcopy(pins); mutation(value)
            result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(ROOT / 'scripts'), json.dumps(value)],
                                    text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode == 0, success, result.stderr)
            if success: self.assertEqual(result.stdout.strip(), 'q03_performance')

    def test_failed_attempt_blocks_progress_and_complete_pair_is_bounded(self):
        session = SimpleNamespace(state={'attempt_results': []})
        self.assertEqual(P.next_selection(session), {'mode': 'off', 'steering': 'active'})
        session.state['attempt_results'] = [{'outcome': 'failed', 'passed_gates': []}]
        with self.assertRaises(RuntimeError): P.next_selection(session)
        session.state['attempt_results'] = [{'outcome': 'passed', 'passed_gates': ['http_service']}] * 2
        with self.assertRaises(RuntimeError): P.next_selection(session)

    def test_stage1_gate_cannot_be_a_summary_or_missing_composition(self):
        with self.assertRaises(RuntimeError): P.validate_stage1({'status': 'TOKEN_ID_MATCH'}, self.pin['sha256'])
        if not hasattr(H, 'quality_module'):
            with self.assertRaisesRegex(RuntimeError, 'composition'): P.quality_module()


if __name__ == '__main__':
    unittest.main()
