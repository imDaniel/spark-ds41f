"""CPU fixtures only: actual PR8 main/load/client/report over localhost fake HTTP.

Character token IDs and accelerated clocks below are explicitly synthetic. No
model/runtime import, device, SSH, remote endpoint, or field PASS is involved.
"""
import base64
import copy
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import importlib
import importlib.util
import atexit
import subprocess
from pathlib import Path
import random
import signal
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'scripts'))
import q03_legacy_case_replay as L
from q03_http_client import FrozenToolbox

# Extract immutable local Git objects only; never fetch or run a remote toolbox.
_TOOLBOX_TMP = tempfile.TemporaryDirectory(prefix='legacy-pr8-toolbox-')
atexit.register(_TOOLBOX_TMP.cleanup)
TOOLBOX = Path(_TOOLBOX_TMP.name)
_SOURCES = {}
for relative, expected in L.toolbox_inventory()['files'].items():
    raw = subprocess.check_output(['git', 'show', L.TOOLBOX_HEAD + ':' + relative], cwd=ROOT)
    assert L.digest(raw) == expected
    target = TOOLBOX / relative; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
    _SOURCES[relative] = raw
class FixtureToolbox(FrozenToolbox):
    @staticmethod
    def relative(name):
        return 'servebench/__init__.py' if name == '_legacy_pr8_fixture' else 'servebench/' + name.split('.', 1)[1].replace('.', '/') + '.py'
    def find_spec(self, fullname, path=None, target=None):
        if fullname != '_legacy_pr8_fixture' and not fullname.startswith('_legacy_pr8_fixture.'):
            return None
        relative = self.relative(fullname)
        if relative not in self.sources:
            raise ImportError('undeclared fixture toolbox import: ' + fullname)
        package = [str(self.root / 'servebench')] if fullname == '_legacy_pr8_fixture' else None
        return importlib.util.spec_from_file_location(fullname, self.root / relative,
            loader=self, submodule_search_locations=package)

sys.meta_path.insert(0, FixtureToolbox(TOOLBOX, _SOURCES))
from _legacy_pr8_fixture import client, load, prom, prompts
from _legacy_pr8_fixture.__main__ import main


def fake_ids(text, chat=False):
    result = [ord(c) for c in text]
    return [1000000] + result + [1000001] if chat else result


def fixture_corpus():
    """Run real PR8 generator; only tokenizer network replies are synthetic."""
    captured = []
    def count(url, model, text):
        captured.append({'text': text, 'response': {'count': len(text), 'tokens': fake_ids(text)}})
        return len(text)
    with patch.object(load, 'token_count', side_effect=count):
        generated = load.documents('http://not-contacted.invalid', L.MODEL,
                                   load.Load(concurrency=4, context_tokens=4096, seed=2048), True)
    questions = prompts.default_prompts()[:16]
    identity = {k: 'a' * 64 for k in L.TOKENIZER_FIELDS}
    identity.update(model_revision='b' * 40, tokenizer_revision='c' * 40,
                    tokenizer_assets_sha256={'tokenizer.json': 'd' * 64}, special_tokens_sha256='d' * 64)
    native = {'deepseek_v41.py': '8450587010152846a2e3f6269c661b464615b7e46c312f4e38a397c44218a6e5',
              'deepseek_v41_encoding.py': '8ee7a8010c3ed257a0f1caa4dfbeb3ec68df0b03e36fcd20dad044ec7cf114fe'}
    identity['encoding_source_sha256'] = native['deepseek_v41_encoding.py']
    identity['chat_template_sha256'] = L.digest(L.canonical(native))
    docs = []
    for slot, doc in enumerate(generated):
        docs.append({'slot': slot, 'text': doc['text'], 'text_sha256': L.digest(doc['text'].encode()),
                     'raw_ids': fake_ids(doc['text']), 'calibration': captured[slot * 2:slot * 2 + 2],
                     'chat_inputs': [{'prompt_id': p['id'], 'prompt_ids': fake_ids(doc['text'] + '\n\n' + p['text'], True)}
                                     for p in questions]})
    value = {'schema_version': 1, 'workload': L.WORKLOAD, 'model': L.MODEL, 'tokenizer_identity': identity,
             'generator': {'toolbox_head': L.TOOLBOX_HEAD, 'toolbox_files': L.toolbox_inventory()['files'],
                 'driver_sha256': L.ORIGINAL_DRIVER_SHA256, 'context_tokens': 4096, 'seed': 2048,
                 'python': {'implementation': 'CPython-CPU-FIXTURE', 'version': 'synthetic', 'executable_sha256': '1' * 64},
                 'stdlib_sources': [{'name': p.name, 'size': p.stat().st_size, 'sha256': L.digest(p.read_bytes())}
                                    for p in prompts.standard_library_sources()]},
             'prompts': questions, 'prompts_sha256': L.PROMPTS_SHA256, 'documents': docs,
             'historical_document_identity': 'UNAVAILABLE_NEW_SHARED_CORPUS',
             'artifacts': {'model_root': '/tmp/registered-model-fixture', 'special_tokens_file': 'tokenizer.json',
                'weights_manifest': {'path': '/tmp/fixture-weights-manifest', 'sha256': identity['weights_manifest_sha256']},
                'runtime_metadata': {'path': '/tmp/fixture-runtime-metadata', 'sha256': 'e' * 64},
                'native_sources': {name: {'path': '/tmp/fixture-native/' + name, 'sha256': pin} for name, pin in native.items()}}}
    L.validate_corpus(value)
    return value


class FakeServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self):
        super().__init__(('127.0.0.1', 0), FakeHandler)
        self.lock = threading.Lock(); self.running = 0
        self.finished = self.generated = self.inputs = 0
        self.payloads = []; self.failed_primer = False; self.drift_ids = False
        self.fail_load = False; self.metric_calls = 0; self.reset_on_scrape = None
        self.thread = threading.Thread(target=self.serve_forever, daemon=True); self.thread.start()


class FakeHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_GET(self):
        if self.path != '/metrics':
            self.send_error(404); return
        s = self.server
        with s.lock:
            s.metric_calls += 1
            values = {name: 0 for name in prom.VLLM.values()}
            values.update({'vllm:request_success_total': s.finished, 'vllm:generation_tokens_total': s.generated,
                           'vllm:prompt_tokens_total': s.inputs, 'vllm:num_requests_running': s.running,
                           'vllm:iteration_tokens_total_count': s.finished * 4,
                           'vllm:iteration_tokens_total_sum': s.generated,
                           'vllm:spec_decode_num_drafts_total': s.finished,
                           'vllm:spec_decode_num_draft_tokens_total': s.finished * 4,
                           'vllm:spec_decode_num_accepted_tokens_total': s.finished * 3,
                           'vllm:prefix_cache_queries_total': s.inputs,
                           'vllm:prefix_cache_hits_total': max(0, s.inputs - s.finished * 100)})
        if s.metric_calls == s.reset_on_scrape:
            values['vllm:generation_tokens_total'] = 0
        raw = ('\n'.join(f'{name}{{model_name="{L.MODEL}",engine="0"}} {value}' for name, value in values.items()) + '\n').encode()
        self.send_response(200); self.end_headers(); self.wfile.write(raw)
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        s = self.server
        if self.path == '/tokenize':
            chat = 'messages' in body
            text = body['messages'][0]['content'] if chat else body['prompt']
            token_ids = fake_ids(text, chat)
            if s.drift_ids:
                token_ids[0] += 1
            raw = json.dumps({'count': len(token_ids), 'tokens': token_ids}).encode()
            self.send_response(200); self.end_headers(); self.wfile.write(raw); return
        if self.path != '/v1/chat/completions':
            self.send_error(404); return
        with s.lock:
            s.payloads.append(copy.deepcopy(body)); s.running += 1
        n = body['max_tokens']; prompt_tokens = len(fake_ids(body['messages'][0]['content'], True))
        try:
            if (s.failed_primer and n == 1) or (s.fail_load and n == 256):
                self.send_response(500); self.end_headers(); self.wfile.write(b'fixture primer failure'); return
            self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
            time.sleep(.025)
            with s.lock:
                s.finished += 1; s.generated += n; s.inputs += prompt_tokens; s.running -= 1
            event = {'choices': [{'delta': {'content': 'SYNTHETIC REPLY'}, 'finish_reason': 'length',
                                  'token_ids': list(range(n))}],
                     'usage': {'prompt_tokens': prompt_tokens, 'completion_tokens': n}}
            self.wfile.write(b'data: ' + json.dumps(event).encode() + b'\n\ndata: [DONE]\n\n'); self.wfile.flush()
        finally:
            if (s.failed_primer and n == 1) or (s.fail_load and n == 256):
                with s.lock:
                    s.running -= 1


class LegacyCaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = fixture_corpus()

    def setUp(self):
        saved = {k: v for k, v in sys.modules.items() if k == 'servebench' or k.startswith('servebench.')}
        def restore_modules():
            for key in list(sys.modules):
                if key == 'servebench' or key.startswith('servebench.'):
                    del sys.modules[key]
            sys.modules.update(saved)
        self.addCleanup(restore_modules)
        for name, module in list(sys.modules.items()):
            if name == '_legacy_pr8_fixture' or name.startswith('_legacy_pr8_fixture.'):
                sys.modules[name.replace('_legacy_pr8_fixture', 'servebench', 1)] = module
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.plan = {'window_id': 'a' * 32, 'attempt_id': 'b' * 32, 'plan_sha256': 'c' * 64,
                     'url': 'http://127.0.0.1:18000', 'selection': {'arm': 'r6x188', 'steering': 'loaded0'},
                     'client': {'output': str(self.root / 'result.json'), 'runtime_context': str(self.root / 'runtime-context.json'),
                         'toolbox_head': L.TOOLBOX_HEAD, 'toolbox_path': str(TOOLBOX), 'toolbox_files': L.toolbox_inventory()['files'],
                         'timeout_seconds': 600, 'legacy_case': {'workload': L.WORKLOAD, 'case_id': 'warm4k_c1',
                            'corpus': self.corpus, 'corpus_sha256': L.digest(L.canonical(self.corpus)),
                            'metric_labels': {'model_name': L.MODEL, 'engine': '0'},
                            'selection': {'arm': 'r6x188', 'steering': 'loaded0'}}}}
        self.write_context()
        originals = client.stream_chat, prom.fetch, load.run, load.documents, L.urllib.request.urlopen
        def restore():
            client.stream_chat, prom.fetch, load.run, load.documents, L.urllib.request.urlopen = originals
        self.addCleanup(restore)

    def write_context(self):
        now = time.time()
        ranks = [{'rank': i, 'container_id': str(i + 1) * 64, 'config_sha256': 'd' * 64} for i in range(4)]
        self.context = {k: self.plan[k] for k in ('window_id', 'attempt_id', 'plan_sha256', 'url')}
        self.context.update(ranks=ranks, ready_sha256='e' * 64, admission={
            'thermal': {'wait_seconds': 0, 'fan_policy': 'UNCHANGED', 'samples': [
                {'rank': r['rank'], 'container_id': r['container_id'], 'sampled_utc': now - 1, 'hottest_c': 42} for r in ranks]},
            'prefix_reset': {k: self.plan[k] for k in ('window_id', 'attempt_id', 'plan_sha256')} |
                {'url': self.plan['url'] + '/reset_prefix_cache', 'method': 'POST', 'completed_utc': now - .5,
                 'status': 200, 'response_utf8': '{"success":true}', 'response_sha256': L.digest(b'{"success":true}')}})
        Path(self.plan['client']['runtime_context']).write_bytes(L.canonical(self.context))

    def server(self):
        server = FakeServer()
        def close():
            server.shutdown(); server.server_close(); server.thread.join(2)
        self.addCleanup(close)
        self.plan['url'] = f'http://127.0.0.1:{server.server_port}'; self.write_context()
        return server

    def execute(self, case='warm4k_c1'):
        self.plan['client']['legacy_case']['case_id'] = case
        original_run = load.run
        begin = time.monotonic(); clock = lambda: 1000 + (time.monotonic() - begin) * 100
        # The real loop, shuffling, payload, priming, scraping and report code run.
        # PR8 and retained acquisition evidence must share a monotonic domain.
        # Only the clock is accelerated; these are not field timings.
        with patch.object(load, 'run', partial(original_run, clock=clock)), \
             patch.object(L, 'time', SimpleNamespace(time=time.time, monotonic=clock)):
            return L.run_client(self.plan, main)

    def test_finite_original_argv_and_all_distinct_states(self):
        for case, concurrency in L.CASES.items():
            self.plan['client']['legacy_case']['case_id'] = case
            for arm in L.ARMS:
                for state in L.STATES:
                    self.plan['selection'] = {'arm': arm, 'steering': state}
                    args = L.argv(self.plan, '/not/executed')
                    for flag, value in (('--context', '4096'), ('--max-tokens', '256'), ('--concurrency', str(concurrency)),
                                        ('--warmup', '15'), ('--duration', '90'), ('--batches', '6'), ('--seed', '2048')):
                        self.assertEqual(args[args.index(flag) + 1], value)
                    extra = json.loads(args[args.index('--extra-body') + 1])
                    self.assertNotIn('chat_template_kwargs', extra); self.assertNotIn('thinking', extra)
                    self.assertEqual(extra['temperature'], 0); self.assertEqual(extra['seed'], 2048)
                    self.assertEqual('steering_scale' in extra, state != 'unloaded')
                    if state != 'unloaded': self.assertEqual(extra['steering_scale'], int(state[-1]))
        for selection in ({'arm': 'native197-dual', 'steering': 'loaded1'}, {'arm': 'r6x188', 'steering': 'loaded5'},
                          {'arm': 'r6x188', 'steering': 'loaded0', 'argv': []}):
            with self.assertRaises(RuntimeError): L.validate_selection(selection)

    def test_complete_shared_real_text_corpus_and_no_seed_only_fallback(self):
        self.assertEqual(L.validate_corpus(self.corpus), self.corpus)
        self.assertEqual(len(self.corpus['documents']), 4)
        for mutate in (lambda x: x['documents'].pop(), lambda x: x['documents'][0]['chat_inputs'].pop(),
                       lambda x: x['documents'][0].update(text='fake short context'),
                       lambda x: x['generator'].update(seed=0), lambda x: x['prompts'][0].update(text='new question'),
                       lambda x: x.update(historical_document_identity='EXACT_OLD_188')):
            value = copy.deepcopy(self.corpus); mutate(value)
            with self.assertRaises(RuntimeError): L.validate_corpus(value)

    def test_thermal_identity_reset_and_freshness_mutations(self):
        self.assertEqual(L.validate_runtime_context(self.context, self.plan), self.context)
        for mutate in (lambda x: x['admission']['thermal']['samples'][0].update(hottest_c=-1),
                       lambda x: x['admission']['thermal']['samples'][0].update(hottest_c=55),
                       lambda x: x['admission']['thermal']['samples'][0].update(sampled_utc=time.time() - 20),
                       lambda x: x['admission']['thermal']['samples'][0].update(container_id='f' * 64),
                       lambda x: x['admission']['thermal'].update(wait_seconds=601),
                       lambda x: x['admission']['prefix_reset'].update(status=201),
                       lambda x: x['admission']['prefix_reset'].update(response_utf8='true')):
            value = copy.deepcopy(self.context); mutate(value)
            with self.assertRaises(RuntimeError): L.validate_runtime_context(value, self.plan)

    def test_reset_requires_actual_boolean_success_not_just_http200(self):
        for body in ('null', '{}', '{"success":false}', '{"success":1}', '{"success":"true"}', '{"success":true,"other":0}'):
            value = copy.deepcopy(self.context)
            value['admission']['prefix_reset'].update(response_utf8=body, response_sha256=L.digest(body.encode()))
            with self.assertRaisesRegex(RuntimeError, 'positively succeed'):
                L.validate_runtime_context(value, self.plan)

    def test_canonical_binding_and_client_bound(self):
        path = self.root / 'corpus.json'; path.write_bytes(L.canonical(self.corpus))
        config = {'workload': L.WORKLOAD, 'case_id': 'warm4k_c4',
                  'corpus': {'path': str(path), 'sha256': L.digest(path.read_bytes())},
                  'metric_labels': {'model_name': L.MODEL, 'engine': '0'}}
        binding = {'http': {'client_seconds': 600, 'legacy_case': config}}
        self.assertEqual(L.validate_binding(config, binding), self.corpus)
        for seconds in (105, 601, True):
            binding['http']['client_seconds'] = seconds
            with self.assertRaises(RuntimeError): L.validate_binding(config, binding)
        path.write_text(json.dumps(self.corpus, indent=2)); config['corpus']['sha256'] = L.digest(path.read_bytes())
        with self.assertRaisesRegex(RuntimeError, 'canonical'): L.validate_binding(config)

    def test_real_servebench_c1_prime_and_raw_receipt(self):
        server = self.server(); self.assertEqual(self.execute(), 0)
        result = L.json_bytes(Path(self.plan['client']['output']).read_bytes())
        validated = L.validate_output(result, self.plan)
        self.assertEqual(validated['quality'], 'NOT_EVALUATED')
        self.assertEqual(validated['lifecycle'], 'REQUIRES_OWNED_FOUR_RANK_DRAIN')
        evidence = L.json_bytes((self.root / 'legacy-evidence.json').read_bytes())
        self.assertEqual(evidence['requests'][0]['phase'], 'prime')
        self.assertEqual(evidence['requests'][0]['record']['completion_tokens'], 1)
        self.assertTrue(all(r['record']['completion_tokens'] == 256 for r in evidence['requests'][1:]))
        self.assertEqual(len(evidence['scrapes']), 10)
        self.assertEqual(evidence['requests'][1]['response']['output_token_ids'], list(range(256)))
        self.assertEqual(len(server.payloads), len(evidence['requests']))
        self.assertNotIn('torch', sys.modules); self.assertNotIn('vllm', sys.modules)
        # The raw response is durable, not inferred from exit0 or regenerated text.
        self.assertIn(b'SYNTHETIC REPLY', base64.b64decode(evidence['requests'][1]['response']['raw_sse_base64'][0]))
        bad = copy.deepcopy(evidence); bad['requests'][0]['record']['completion_tokens'] = 256
        raw = L.json_bytes((self.root / 'legacy-run.json').read_bytes())
        with self.assertRaises(RuntimeError): L.validate_case(raw, bad, self.plan)
        bad = copy.deepcopy(evidence); bad['raw_run']['fences']['scrapes'][0]['requests_finished'] = 0
        with self.assertRaises(RuntimeError): L.validate_case(raw, bad, self.plan)

    def test_real_servebench_c4_keeps_four_salts_and_variable_inputs(self):
        self.server(); self.assertEqual(self.execute('warm4k_c4'), 0)
        result = L.json_bytes(Path(self.plan['client']['output']).read_bytes()); L.validate_output(result, self.plan)
        evidence = L.json_bytes((self.root / 'legacy-evidence.json').read_bytes())
        self.assertEqual([r['slot'] for r in evidence['requests'][:4]], [0, 1, 2, 3])
        self.assertEqual(len({r['payload']['cache_salt'] for r in evidence['requests']}), 4)
        self.assertGreater(len({r['record']['prompt_tokens'] for r in evidence['requests']}), 1)
        self.assertEqual(evidence['raw_run']['window'][1] - evidence['raw_run']['window'][0], 90)
        self.assertEqual(result['historical_comparison'], 'UNAVAILABLE_ORIGINAL_DOCUMENT_BYTES')
        raw = L.json_bytes((self.root / 'legacy-run.json').read_bytes())
        bad = copy.deepcopy(evidence); bad['raw_run']['records'][0]['slot'] = (bad['raw_run']['records'][0]['slot'] + 1) % 4
        with self.assertRaisesRegex(RuntimeError, 'records differ'): L.validate_case(raw, bad, self.plan)

    def test_scrape_timestamps_match_ordered_boundary_acquisitions(self):
        self.server(); self.assertEqual(self.execute(), 0)
        evidence = L.json_bytes((self.root / 'legacy-evidence.json').read_bytes())
        value = L.json_bytes((self.root / 'legacy-run.json').read_bytes())
        self.assertTrue(L.validate_case(value, evidence, self.plan))

        def reject(mutate, message):
            bad = copy.deepcopy(evidence); mutate(bad)
            with self.assertRaisesRegex(RuntimeError, message):
                L.validate_case(value, bad, self.plan)

        for invalid in (None, True, '1000', float('nan'), float('inf'), -float('inf')):
            for index in range(7):
                with self.subTest(invalid=invalid, scrape=index):
                    reject(lambda e: e['raw_run']['scrape_times'].__setitem__(index, invalid), 'finite original')
            for index in range(2):
                with self.subTest(invalid=invalid, fence=index):
                    reject(lambda e: e['raw_run']['fences']['times'].__setitem__(index, invalid), 'finite original')
            for key in ('begin_monotonic', 'end_monotonic'):
                for index in range(10):
                    with self.subTest(invalid=invalid, acquisition=index, key=key):
                        reject(lambda e: e['scrapes'][index].update({key: invalid}), 'finite original')
            with self.subTest(invalid=invalid, key='started_monotonic'):
                reject(lambda e: e.update(started_monotonic=invalid), 'finite original')

        mutations = [
            ('duplicate stamp', lambda e: e['raw_run']['scrape_times'].__setitem__(1, e['raw_run']['scrape_times'][0]), 'chronology'),
            ('reversed stamps', lambda e: e['raw_run']['scrape_times'].reverse(), 'chronology'),
            ('before boundary', lambda e: e['raw_run']['scrape_times'].__setitem__(0, e['raw_run']['boundaries'][0] - 1), 'boundary ordering'),
            ('inside fetch', lambda e: e['raw_run']['scrape_times'].__setitem__(0,
                (e['scrapes'][2]['begin_monotonic'] + e['scrapes'][2]['end_monotonic']) / 2), 'acquisition correspondence'),
            ('reversed acquisition', lambda e: e['scrapes'][2].update(begin_monotonic=e['scrapes'][2]['end_monotonic'] + 1), 'acquisition chronology'),
            ('overlapping acquisition', lambda e: e['scrapes'][2].update(begin_monotonic=e['scrapes'][1]['end_monotonic'] - 1), 'acquisition chronology'),
            ('before client interval', lambda e: e.update(started_monotonic=e['scrapes'][0]['begin_monotonic'] + 1), 'acquisition chronology'),
            ('stamp before preceding fetch ends', lambda e: e['scrapes'][2].update(
                end_monotonic=(e['raw_run']['scrape_times'][1] + e['scrapes'][3]['begin_monotonic']) / 2), 'acquisition correspondence'),
            ('start before post-prime fetch ends', lambda e: e['scrapes'][1].update(
                end_monotonic=e['raw_run']['start'] + 1), 'acquisition correspondence'),
            ('primer before pre-prime fetch ends', lambda e: e['scrapes'][0].update(
                end_monotonic=e['requests'][0]['record']['sent'] + 1), 'acquisition correspondence'),
        ]
        for name, mutate, message in mutations:
            with self.subTest(name=name):
                reject(mutate, message)

    def test_rehashed_stretched_timestamps_and_outside_client_interval_are_rejected(self):
        self.server(); self.assertEqual(self.execute(), 0)
        output = Path(self.plan['client']['output'])
        receipt = L.json_bytes(output.read_bytes())
        evidence_path, raw_path = self.root / 'legacy-evidence.json', self.root / 'legacy-run.json'
        evidence, value = L.json_bytes(evidence_path.read_bytes()), L.json_bytes(raw_path.read_bytes())
        self.assertEqual(L.validate_output(receipt, self.plan)['status'], 'COMPLETE_ORIGINAL_CASE_CLIENT_ONLY')
        original_tps = value['summary']['server']['generation_tps']['mean']
        times = evidence['raw_run']['scrape_times']; origin = times[0]
        evidence['raw_run']['scrape_times'] = [origin + (stamp - origin) * 10 for stamp in times]
        value['summary'], _ = L._frozen_report(evidence['raw_run'], self.plan)
        self.assertAlmostEqual(value['summary']['server']['generation_tps']['mean'], original_tps / 10)
        self.assertGreater(evidence['raw_run']['scrape_times'][-1], evidence['raw_run']['fences']['times'][-1])
        evidence_path.write_bytes(L.canonical(evidence)); raw_path.write_bytes(L.canonical(value))
        receipt.update(evidence_sha256=L.digest(evidence_path.read_bytes()), raw_sha256=L.digest(raw_path.read_bytes()))
        with self.assertRaisesRegex(RuntimeError, 'chronology'):
            L.validate_output(receipt, self.plan)

        evidence['raw_run']['scrape_times'] = times
        value['summary'], _ = L._frozen_report(evidence['raw_run'], self.plan)
        evidence['ended_monotonic'] = evidence['scrapes'][-1]['begin_monotonic']
        evidence_path.write_bytes(L.canonical(evidence)); raw_path.write_bytes(L.canonical(value))
        receipt.update(evidence_sha256=L.digest(evidence_path.read_bytes()), raw_sha256=L.digest(raw_path.read_bytes()))
        with self.assertRaisesRegex(RuntimeError, 'exceeds client interval'):
            L.validate_output(receipt, self.plan)

    def test_delayed_scrapes_are_valid_without_a_new_lateness_tolerance(self):
        self.server(); self.assertEqual(self.execute(), 0)
        evidence = L.json_bytes((self.root / 'legacy-evidence.json').read_bytes())
        value = L.json_bytes((self.root / 'legacy-run.json').read_bytes())
        raw = evidence['raw_run']
        # Model a slow serialized fetch crossing later planned boundaries. PR8
        # permits this and uses the actual stamp deltas in its server summary.
        raw['scrape_times'] = [raw['boundaries'][0] + 30 * (k + 1) for k in range(7)]
        for stamp, scrape in zip(raw['scrape_times'], evidence['scrapes'][2:-1]):
            scrape.update(begin_monotonic=stamp + 1, end_monotonic=stamp + 20)
        raw['fences']['times'][-1] = evidence['scrapes'][-2]['end_monotonic'] + 1
        evidence['scrapes'][-1].update(begin_monotonic=raw['fences']['times'][-1] + 1,
                                       end_monotonic=raw['fences']['times'][-1] + 2)
        evidence['ended_monotonic'] = evidence['scrapes'][-1]['end_monotonic'] + 1
        value['summary'], _ = L._frozen_report(raw, self.plan)
        self.assertTrue(L.validate_case(value, evidence, self.plan))
        evidence_path, raw_path = self.root / 'legacy-evidence.json', self.root / 'legacy-run.json'
        evidence_path.write_bytes(L.canonical(evidence)); raw_path.write_bytes(L.canonical(value))
        receipt = L.json_bytes(Path(self.plan['client']['output']).read_bytes())
        receipt.update(evidence_sha256=L.digest(evidence_path.read_bytes()), raw_sha256=L.digest(raw_path.read_bytes()))
        self.assertEqual(L.validate_output(receipt, self.plan)['status'], 'COMPLETE_ORIGINAL_CASE_CLIENT_ONLY')

    def test_failed_primer_stops_new_requests_and_keeps_partial_receipt(self):
        server = self.server(); server.failed_primer = True
        self.assertEqual(self.execute('warm4k_c4'), 1)
        self.assertEqual(len(server.payloads), 1)
        evidence = L.json_bytes((self.root / 'legacy-evidence.json').read_bytes())
        self.assertIsNotNone(evidence['error']); self.assertIsNone(evidence['raw_run'])
        self.assertEqual(len(evidence['requests']), 1)
        self.assertIn('HTTPError', evidence['requests'][0]['record']['error'])
        result = L.json_bytes(Path(self.plan['client']['output']).read_bytes())
        self.assertEqual(result['exit_code'], 1); self.assertIsNone(result['raw_sha256'])

    def test_failed_measured_request_stops_admission_and_keeps_actual_error(self):
        server = self.server(); server.fail_load = True
        self.assertEqual(self.execute(), 1)
        self.assertEqual([p['max_tokens'] for p in server.payloads], [1, 256])
        evidence = L.json_bytes((self.root / 'legacy-evidence.json').read_bytes())
        self.assertEqual(len(evidence['requests']), 2)
        self.assertIn('HTTPError', evidence['requests'][1]['record']['error'])

    def test_actual_timer_interrupt_retains_bounded_partial_evidence(self):
        server = self.server()
        original = signal.setitimer
        def short_timer(which, seconds, *args):
            return original(which, min(seconds, .03) if seconds else 0, *args)
        with patch.object(signal, 'setitimer', side_effect=short_timer):
            self.assertEqual(self.execute(), 1)
        evidence = L.json_bytes((self.root / 'legacy-evidence.json').read_bytes())
        self.assertIn('hard deadline', evidence['error'])
        self.assertEqual(server.payloads, [])
        self.assertLess((evidence['ended_monotonic'] - evidence['started_monotonic']) / 100, 2)

    def test_counter_reset_stops_during_actual_servebench(self):
        server = self.server(); server.reset_on_scrape = 3
        self.assertEqual(self.execute(), 1)
        evidence = L.json_bytes((self.root / 'legacy-evidence.json').read_bytes())
        self.assertIn('counter reset', evidence['error'])
        self.assertIsNone(evidence['raw_run'])

    def test_live_corpus_drift_stops_before_first_primer(self):
        server = self.server(); server.drift_ids = True
        self.assertEqual(self.execute(), 1); self.assertEqual(server.payloads, [])
        evidence = L.json_bytes((self.root / 'legacy-evidence.json').read_bytes())
        self.assertIn('actual raw document IDs differ', evidence['error'])
        self.assertEqual(len(evidence['tokenizer_verification']), 1)

    def test_native_missing_foreign_duplicate_and_reset_values_rejected(self):
        text = '\n'.join(f'{n}{{model_name="{L.MODEL}",engine="0"}} 0' for n in prom.VLLM.values())
        labels = {'model_name': L.MODEL, 'engine': '0'}
        self.assertEqual(len(L.native_series(text, labels)), len(prom.VLLM))
        self.assertEqual(L.METRIC_NAMES, frozenset(prom.VLLM.values()))
        with patch.object(prom, 'VLLM', {}):
            self.assertEqual(len(L.native_series(text, labels)), 13)
        for bad in ('\n'.join(text.splitlines()[:-1]), text.replace('engine="0"', 'engine="1"'),
                    text + '\n' + text.splitlines()[0], text.replace('} 0', '} -1'), text.replace('} 0', '} NaN')):
            with self.assertRaises(RuntimeError): L.native_series(bad, labels)

    def test_raw_response_keeps_missing_ids_and_malformed_bytes_honest(self):
        raw = [b'data: {"choices":[{"delta":{"content":"answer"},"finish_reason":"length"}]}\n', b'data: [DONE]\n']
        response = L._response_evidence(raw)
        self.assertIsNone(response['output_token_ids'])
        self.assertEqual(response['output_ids_status'], 'NOT_RETURNED_BY_ORIGINAL_REQUEST')
        bad = [b'data: {bad-json}\n']
        response = L._response_evidence(bad)
        self.assertEqual(base64.b64decode(response['raw_sse_base64'][0]), bad[0]); self.assertIsNotNone(response['parse_error'])
        wire = [b'data: {"choices":[{"delta":{"content":"answer"},"finish_reason":"length"}],"usage":{"prompt_tokens":42,"completion_tokens":256}}\n']
        response = L._response_evidence(wire)
        row = {'response': response, 'record': {'prompt_tokens': 43, 'completion_tokens': 256, 'finish_reason': 'length', 'events': [[1.0, 256]]}}
        with self.assertRaisesRegex(RuntimeError, 'raw response usage'): L.validate_response(row)

    def test_coverage_does_not_silently_erase_unimplemented_dimensions(self):
        value = L.coverage()
        self.assertEqual(value['steering_states'], list(L.STATES))
        self.assertEqual(value['model256_quality'], 'NOT_IMPLEMENTED')
        self.assertEqual(value['dual_arm'], 'NOT_IN_FIRST_ROUND')
        self.assertEqual(value['same_boot_scale_return_sequence'], 'NOT_IMPLEMENTED')


if __name__ == '__main__':
    unittest.main()
