"""Synthetic CPU-only protocol fixtures, never model or candidate-code evidence."""
import base64
import contextlib
import copy
import http.client
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import q03_quality_capture as q
from quality_test_fixtures import MATERIAL, toolbox


class Response:
    def __init__(self, raw, status=200, headers=None, fail_after=None, failure=None):
        self.status, self.headers = status, headers or {}
        self.stream, self.fail_after, self.failure = io.BytesIO(raw), fail_after, failure
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def read(self, n):
        if self.fail_after is not None and self.stream.tell() >= self.fail_after:
            raise self.failure or OSError('synthetic disconnected transport')
        if self.fail_after is not None:
            n = min(n, self.fail_after - self.stream.tell())
        return self.stream.read(n)


def answer(index=0, **overrides):
    value = {'id': 'synthetic-response', 'model': q.MODEL, 'created': 123,
        'system_fingerprint': 'synthetic', 'unexpected': {'preserve': ['yes', None]},
        'prompt_token_ids': [1, index + 2],
        'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': '  答案\r\n',
                     'reasoning_content': 'full reasoning\n', 'reasoning': None, 'tool_calls': []},
                     'token_ids': [3, 4], 'finish_reason': 'stop', 'stop_reason': 2}],
        'usage': {'prompt_tokens': 2, 'completion_tokens': 2, 'total_tokens': 4,
                  'completion_tokens_details': {'reasoning_tokens': 1}}}
    value.update(overrides)
    return value


def inputs(n=3):
    prompts = [{'id': f'synthetic-{i}', 'text': f'exact prompt {i} 中文'} for i in range(n)]
    cases = [{'id': p['id'], 'category': 'synthetic', 'language': 'en',
              'prompt_sha256': q.digest(p['text'].encode()), 'input_ids': [1, i + 2]}
             for i, p in enumerate(prompts)]
    return prompts, cases


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.calls, self.responses = [], []
        self.now = 10.0
        self.prompts, self.cases = inputs()

    def opener(self, request, timeout):
        self.calls.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def capture(self, state='unloaded', **kwargs):
        return q.Capture(self.directory, 'http://127.0.0.1:8000', self.prompts, self.cases, state,
            deadline=20, max_response_bytes=kwargs.pop('max_response_bytes', 100000),
            max_header_bytes=kwargs.pop('max_header_bytes', 8192), open_response=self.opener,
            clock=lambda: self.now, **kwargs)

    def body(self, i=0, state='unloaded'):
        return {'model': q.MODEL, 'messages': [{'role': 'user', 'content': self.prompts[i]['text']}],
                'max_tokens': 2048, 'temperature': 0, 'return_token_ids': True,
                'cache_salt': f'synthetic-salt-{i}'} | q.extra_body(state)

    def send(self, capture, i=0, body=None):
        return capture(capture.url + '/v1/chat/completions', body or self.body(i), 1800)

    def test_lossless_full_response_and_original_wire(self):
        original = answer()
        raw = json.dumps(original, ensure_ascii=False, indent=2).encode() + b'\n'
        response = Response(raw, headers={'Content-Length': str(len(raw)), 'X-Request-ID': 'synthetic'})
        self.responses = [response]
        capture = self.capture()
        self.assertEqual(self.send(capture), original)
        row = capture.records[0]
        self.assertEqual((self.directory / row['response']['raw_path']).read_bytes(), raw)
        self.assertEqual(row['response']['raw_sha256'], q.digest(raw))
        self.assertEqual(row['response']['decoded'], original)
        self.assertEqual(base64.b64decode(row['request']['wire_base64']), self.calls[0][0].data)
        self.assertEqual(self.calls[0][0].data, json.dumps(self.body()).encode())
        self.assertEqual(row['request']['timeout_seconds'], 10)
        self.assertEqual(row['outcome'], 'COMPLETED')
        self.assertEqual(row['semantic_scoring'], 'NOT_EVALUATED')
        self.assertTrue(response.closed)

    def test_length_empty_answer_and_tools_are_retained_not_retried(self):
        first, second, third = answer(), answer(1), answer(2)
        first['choices'][0]['finish_reason'] = 'length'
        second['choices'][0]['message']['content'] = ''
        third['choices'][0]['message']['content'] = None
        third['choices'][0]['message']['tool_calls'] = [{'id': 'synthetic', 'function': {'arguments': '{bad json'}}]
        third['choices'][0]['finish_reason'] = 'tool_calls'
        self.responses = [Response(q.canonical(x)) for x in (first, second, third)]
        capture = self.capture()
        for i in range(3):
            self.send(capture, i)
        self.assertEqual([r['outcome'] for r in capture.records], ['TRUNCATED', 'COMPLETED', 'COMPLETED'])
        self.assertTrue(capture.records[1]['diagnostics']['empty_final_answer'])
        self.assertTrue(capture.records[2]['diagnostics']['unexpected_tool_call_finish'])
        self.assertIsNone(capture.failure)
        self.assertEqual(len(self.calls), 3)

    def test_missing_null_and_wrong_content_type_are_not_rewritten(self):
        values = [answer(i) for i in range(3)]
        del values[0]['choices'][0]['message']['content']
        values[1]['choices'][0]['message']['content'] = None
        values[2]['choices'][0]['message']['content'] = ['not text']
        self.responses = [Response(q.canonical(x)) for x in values]
        capture = self.capture()
        for i in range(3):
            self.send(capture, i)
        self.assertEqual([r['diagnostics']['final_content_type'] for r in capture.records], ['MISSING', 'NoneType', 'list'])
        self.assertEqual([r['response']['decoded'] for r in capture.records], values)

    def test_loaded_zero_differs_from_unloaded_and_no_thinking_override(self):
        capture = self.capture('loaded0')
        self.responses = [Response(q.canonical(answer()))]
        self.send(capture, body=self.body(state='loaded0'))
        payload = capture.records[0]['request']['payload']
        self.assertEqual(payload['steering_scale'], 0)
        self.assertEqual(payload['steering_profile'], q.PROFILE)
        self.assertNotIn('steering_scale', self.body())
        for field in ('ignore_eos', 'min_tokens', 'stream', 'tools', 'response_format', 'chat_template_kwargs'):
            self.assertNotIn(field, payload)

    def test_all_six_state_payloads(self):
        for state in ('unloaded', 'loaded0', 'loaded1', 'loaded2', 'loaded3', 'loaded4'):
            value = q.extra_body(state)
            self.assertEqual(value['seed'], 2048)
            if state != 'unloaded':
                self.assertEqual(value['steering_scale'], int(state[-1]))

    def assert_fence(self, capture):
        for i in (1, 2):
            with self.assertRaises(q.CaptureError):
                self.send(capture, i)
        records = capture.finish()
        self.assertEqual([r['outcome'] for r in records], ['ERROR', 'NOT_RUN', 'NOT_RUN'])
        self.assertEqual(len(self.calls), 1)
        for i in range(3):
            self.assertTrue((self.directory / f'record-{i:03d}.json').is_file())

    def test_malformed_duplicate_nonfinite_and_invalid_utf8_fence(self):
        bad = [b'{', b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}', b'{"x":"\xff"}']
        for raw in bad:
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as root:
                self.directory = Path(root); self.calls = []; self.responses = [Response(raw)]
                capture = self.capture()
                with self.assertRaises(Exception):
                    self.send(capture)
                self.assertEqual((self.directory / 'response-000.bin').read_bytes(), raw)
                self.assertIsNotNone(capture.records[0]['response']['parse_error'])
                self.assert_fence(capture)

    def test_non_2xx_http_error_preserves_body(self):
        raw = b'{"error":{"message":"synthetic failure"}}'
        self.responses = [urllib.error.HTTPError('http://127.0.0.1:8000/v1/chat/completions', 503,
            'synthetic failure', {'Content-Length': str(len(raw))}, io.BytesIO(raw))]
        capture = self.capture()
        with self.assertRaises(q.CaptureError):
            self.send(capture)
        self.assertEqual(capture.records[0]['response']['http_status'], 503)
        self.assertEqual((self.directory / 'response-000.bin').read_bytes(), raw)
        self.assert_fence(capture)

    def test_partial_read_and_incomplete_read_preserve_available_bytes(self):
        for failure in (OSError('synthetic break'), http.client.IncompleteRead(b'xyz', 20)):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as root:
                self.directory = Path(root); self.calls = []
                self.responses = [Response(b'abcdef', fail_after=3, failure=failure)]
                capture = self.capture()
                with self.assertRaises(Exception):
                    self.send(capture)
                expected = b'abcxyz' if isinstance(failure, http.client.IncompleteRead) else b'abc'
                self.assertEqual((self.directory / 'response-000.bin').read_bytes(), expected)
                self.assertFalse(capture.records[0]['response']['complete'])
                self.assert_fence(capture)

    def test_transport_overflow_is_not_silent_text_truncation(self):
        self.responses = [Response(b'0123456789')]
        capture = self.capture(max_response_bytes=5)
        with self.assertRaises(q.CaptureError):
            self.send(capture)
        self.assertEqual((self.directory / 'response-000.bin').read_bytes(), b'012345')
        self.assertTrue(capture.records[0]['response']['overflow'])
        self.assertFalse(capture.records[0]['response']['complete'])
        self.assert_fence(capture)

    def test_evidence_errors_stop_next_requests(self):
        def wrong_model(x): x['model'] = 'wrong'
        def wrong_prompt(x): x['prompt_token_ids'] = [123]
        def missing_output(x): del x['choices'][0]['token_ids']
        def bad_usage(x): x['usage']['completion_tokens'] = 3
        def bool_ids(x): x['choices'][0]['token_ids'] = [True, 4]
        def no_finish(x): x['choices'][0]['finish_reason'] = None
        def extra_choice(x): x['choices'].append(copy.deepcopy(x['choices'][0]))
        for change in (wrong_model, wrong_prompt, missing_output, bad_usage, bool_ids, no_finish, extra_choice):
            with self.subTest(change=change.__name__), tempfile.TemporaryDirectory() as root:
                self.directory = Path(root); self.calls = []
                value = answer(); change(value); self.responses = [Response(q.canonical(value))]
                capture = self.capture()
                with self.assertRaises(q.CaptureError): self.send(capture)
                self.assertEqual(capture.records[0]['response']['decoded'], value)
                self.assert_fence(capture)

    def test_invalid_answer_json_is_not_http_json_error(self):
        value = answer(); value['choices'][0]['message']['content'] = '{not valid answer JSON'
        self.responses = [Response(q.canonical(value))]
        capture = self.capture()
        self.send(capture)
        self.assertIsNone(capture.failure)
        self.assertEqual(capture.records[0]['outcome'], 'COMPLETED')

    def test_content_length_mismatch_fails(self):
        self.responses = [Response(q.canonical(answer()), headers={'Content-Length': '99999'})]
        capture = self.capture()
        with self.assertRaises(q.CaptureError): self.send(capture)
        self.assert_fence(capture)

    def test_header_overflow_retains_bounded_prefix_and_raw_body(self):
        raw = q.canonical(answer())
        self.responses = [Response(raw, headers={'X-Request-ID': 'x' * 200})]
        capture = self.capture(max_header_bytes=32)
        with self.assertRaises(q.CaptureError): self.send(capture)
        response = capture.records[0]['response']
        self.assertEqual((self.directory / response['raw_path']).read_bytes(), raw)
        self.assertTrue(response['headers_overflow'])
        self.assertEqual(response['headers'], [])
        self.assertEqual(len(base64.b64decode(response['headers_overflow_evidence']['canonical_prefix_base64'])), 32)
        self.assert_fence(capture)

    def test_deadline_after_read_keeps_bytes_and_stops_admission(self):
        raw = q.canonical(answer())
        capture = self.capture()
        response = Response(raw)
        original = response.read
        def read(n):
            result = original(n)
            self.now = 20
            return result
        response.read = read
        self.responses = [response]
        with self.assertRaises(q.CaptureError): self.send(capture)
        self.assertEqual((self.directory / 'response-000.bin').read_bytes(), raw)
        self.assertFalse(capture.records[0]['response']['complete'])
        self.assert_fence(capture)

    def test_duplicate_salt_or_changed_prompt_not_sent(self):
        self.responses = [Response(q.canonical(answer()))]
        capture = self.capture()
        self.send(capture)
        body = self.body(1); body['cache_salt'] = self.body()['cache_salt']
        with self.assertRaises(q.CaptureError): self.send(capture, 1, body)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(capture.records[1]['outcome'], 'ERROR')

    def test_missing_or_duplicate_case_ids_rejected_before_transport(self):
        for mutation in ('duplicate', 'missing'):
            with self.subTest(mutation=mutation):
                self.prompts, self.cases = inputs()
                if mutation == 'duplicate':
                    self.prompts[1]['id'] = self.prompts[0]['id']; self.cases[1]['id'] = self.cases[0]['id']
                else:
                    del self.prompts[0]['id']
                with self.assertRaises(q.CaptureError): self.capture()
                self.assertFalse(self.calls)

    def test_deadline_and_request_override_fence_before_network(self):
        capture = self.capture()
        self.now = 20
        with self.assertRaises(q.CaptureError): self.send(capture)
        self.assertEqual(len(self.calls), 0)
        self.assertEqual(capture.records[0]['outcome'], 'ERROR')

    def test_default_override_is_rejected_before_network(self):
        capture = self.capture(); body = self.body(); body['ignore_eos'] = True
        with self.assertRaises(q.CaptureError): self.send(capture, body=body)
        self.assertFalse(self.calls)

    def test_identity_snapshot_is_copied_and_salt_only_excluded(self):
        self.responses = [Response(q.canonical(answer()))]
        capture = self.capture(); original = self.body()
        self.cases[0]['input_ids'][0] = 999
        self.send(capture, body=original)
        normalized = dict(original); del normalized['cache_salt']
        self.assertEqual(capture.records[0]['request']['semantic_sha256'], q.digest(q.canonical(normalized)))
        self.assertEqual(capture.records[0]['expected_input_ids'], [1, 2])

    def test_real_chunked_http_decoder_retains_partial_body(self):
        class Socket:
            def makefile(self, *args, **kwargs):
                return io.BytesIO(b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nabc')
        response = http.client.HTTPResponse(Socket()); response.begin()
        self.responses = [response]
        capture = self.capture()
        with self.assertRaises(http.client.IncompleteRead): self.send(capture)
        self.assertEqual((self.directory / 'response-000.bin').read_bytes(), b'abc')
        self.assertEqual(capture.records[0]['response']['raw_bytes'], 3)
        self.assert_fence(capture)

    def test_explicit_request_budget_clamps_transport_and_fences_slow_reads(self):
        response = Response(q.canonical(answer()))
        read = response.read
        def slow(size):
            chunk = read(size); self.now += 2
            return chunk
        response.read = slow; self.responses = [response]
        capture = self.capture(request_seconds=1)
        with self.assertRaises(q.CaptureError): self.send(capture)
        self.assertEqual(capture.records[0]['request']['timeout_seconds'], 1)
        self.assert_fence(capture)

    def test_existing_artifacts_never_overwritten(self):
        self.capture()
        with self.assertRaises(FileExistsError): self.capture()

    def test_persistence_failure_latches_before_next_request(self):
        self.responses = [Response(q.canonical(answer()))]
        capture = self.capture()
        real = q.exclusive
        def fail_record(path, raw):
            if path.name == 'record-000.json': raise OSError('synthetic disk failure')
            return real(path, raw)
        with patch.object(q, 'exclusive', side_effect=fail_record):
            with self.assertRaises(OSError): self.send(capture)
        self.assertIn('persistence failed', capture.failure)
        self.assertEqual(capture.records[0]['outcome'], 'ERROR')
        with self.assertRaises(q.CaptureError): self.send(capture, 1)
        self.assertEqual(len(self.calls), 1)


class OriginalPR8Tests(unittest.TestCase):
    def test_actual_unchanged_model_loop_no_score_and_failure_fence(self):
        from q03_http_client import FrozenToolbox
        import importlib
        root = toolbox()
        inventory = q.strict_json((ROOT / 'scripts/q03_http_toolbox.json').read_bytes())['files']
        sources = {name: (root / name).read_bytes() for name in inventory}
        self.assertTrue(all(q.digest(sources[name]) == pin for name, pin in inventory.items()))
        self.assertFalse(any(name == 'servebench' or name.startswith('servebench.') for name in sys.modules))
        loader = FrozenToolbox(root, sources); sys.meta_path.insert(0, loader)
        try:
            cli = importlib.import_module('servebench.__main__')
            behaviour = importlib.import_module('servebench.behaviour')
            prom = importlib.import_module('servebench.prom')
            prompt_path = MATERIAL / 'prompts.jsonl'
            prompts = [q.strict_json(line) for line in prompt_path.read_bytes().splitlines()]
            cases = [{'id': p['id'], 'category': 'synthetic-token-binding', 'language': 'en',
                      'prompt_sha256': q.digest(p['text'].encode()), 'input_ids': [1, i + 2]}
                     for i, p in enumerate(prompts)]
            for fail in (False, True):
                with self.subTest(fail=fail), tempfile.TemporaryDirectory() as temp:
                    calls = []
                    def fake(request, timeout):
                        if request.full_url.endswith('/metrics'):
                            return Response(b'vllm:num_requests_running 0\nvllm:num_requests_waiting 0\n')
                        i = len(calls); calls.append(request)
                        return Response(b'{bad' if fail and i == 1 else q.canonical(answer(i)))
                    plan = {'url': 'http://127.0.0.1:8000', 'window_id': 'synthetic-window',
                        'attempt_id': 'synthetic-attempt', 'lane_id': 'synthetic-lane', 'plan_sha256': '1' * 64,
                        'selection': {'arm': 'r6x188', 'steering': 'unloaded'},
                        'client': {'output': str(Path(temp) / 'pr8.json'), 'quality_capture': {
                            'material_sha256': q.MATERIAL_SHA256, 'prompts_path': str(prompt_path),
                            'prompts_sha256': q.PROMPTS_SHA256, 'cases': cases, 'max_response_bytes': 100000,
                            'max_header_bytes': 8192, 'context': {'runtime_receipt_sha256': '2' * 64,
                                'tokenizer_identity_sha256': '3' * 64, 'decode_identity_sha256': '4' * 64,
                                'observed_loaded_state': {'status': 'SYNTHETIC'},
                                'applied_state_evidence': {'status': 'UNVERIFIED_SYNTHETIC'}}}}}
                    with patch.object(prom, 'fetch', return_value=None), patch.object(behaviour, 'score', side_effect=AssertionError('scoring forbidden')), \
                         contextlib.redirect_stdout(io.StringIO()):
                        code = q.run_client(plan, cli.main, deadline=20, open_response=fake, clock=lambda: 10)
                    sidecar = json.loads((Path(temp) / 'quality-capture/quality-capture.json').read_text())
                    original = json.loads((Path(temp) / 'pr8.json').read_text())
                    self.assertIsNone(original['scores'])
                    self.assertFalse(original['workload']['fixed_length'])
                    self.assertEqual(original['workload']['concurrency'], 1)
                    self.assertEqual(len(original['generation']['sequences']), 60)
                    self.assertEqual(len(sidecar['records']), 60)
                    self.assertEqual(code, int(fail))
                    self.assertEqual(len(calls), 2 if fail else 60)
                    self.assertEqual(sidecar['counts'], {'COMPLETED': 1, 'ERROR': 1, 'NOT_RUN': 58, 'TRUNCATED': 0}
                                     if fail else {'COMPLETED': 60, 'ERROR': 0, 'NOT_RUN': 0, 'TRUNCATED': 0})
                    self.assertEqual(sidecar['semantic_scoring'], 'NOT_EVALUATED')
        finally:
            sys.meta_path.remove(loader)
            for name in list(sys.modules):
                if name == 'servebench' or name.startswith('servebench.'):
                    del sys.modules[name]


if __name__ == '__main__':
    unittest.main()
