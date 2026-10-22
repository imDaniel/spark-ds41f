"""Synthetic localhost regressions for malformed SSE evidence, not model runs."""
import base64
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_q03_legacy_case_replay as F
L = F.L


def decode(response):
    return [base64.b64decode(line, validate=True) for line in response['raw_sse_base64']]


def malformed(kind, count=256, prompt_tokens=7):
    event = {'choices': [{'delta': {'content': 'INVALID_UTF8'}, 'finish_reason': 'length'}],
             'usage': {'prompt_tokens': prompt_tokens, 'completion_tokens': count}}
    if kind == 'choices':
        event['choices'] = [1]
    elif kind == 'delta':
        event['choices'][0]['delta'] = 1
    elif kind == 'usage':
        event['usage'] = 1  # PR8 then raises outside its parser try/except.
    elif kind == 'json':
        return b'data: {"choices":\n'
    raw = b'data: ' + json.dumps(event).encode() + b'\n'
    return raw.replace(b'INVALID_UTF8', b'\xff') if kind == 'utf8' else raw


class ParserTests(unittest.TestCase):
    def test_every_observed_byte_survives_all_parse_failures(self):
        prefix = b'data: {"choices":[{"delta":{"content":"before"}}]}\n'
        suffix = b'data: {"choices":[{"delta":{"content":"after"}}]}\n'
        for kind in ('choices', 'delta', 'usage', 'json', 'utf8'):
            with self.subTest(kind=kind):
                observed = [prefix, malformed(kind), b'\n', suffix]
                result = L._response_evidence(observed)
                self.assertEqual(decode(result), observed)
                self.assertIsNotNone(result['parse_error'])
                self.assertEqual(result['response_parts'], [{'kind': 'content', 'text': 'before'}])
                with self.assertRaisesRegex(RuntimeError, 'complete parseable'):
                    L.validate_response({'response': result, 'record': {}})

    def test_wrong_container_shapes_are_parse_errors_not_exceptions(self):
        for event in ([], None, 1, {'choices': 1}, {'choices': {}}, {'choices': [None]},
                      {'choices': [{'delta': []}]}, {'choices': [{'delta': False}]}, {'usage': []}):
            with self.subTest(event=event):
                observed = [b'data: ' + json.dumps(event).encode() + b'\n']
                response = L._response_evidence(observed)
                self.assertEqual(decode(response), observed)
                self.assertIsNotNone(response['parse_error'])

    def test_valid_and_empty_stream_contract_stays_unchanged(self):
        observed = [b': comment\n', b'data: {"choices":[{"delta":{"content":"ok"},"token_ids":[3]}]}\n',
                    b'\n', b'data: [DONE]\n']
        result = L._response_evidence(observed)
        self.assertEqual(decode(result), observed)
        self.assertIsNone(result['parse_error'])
        self.assertEqual(result['response_parts'], [{'kind': 'content', 'text': 'ok'}])
        self.assertEqual(result['output_token_ids'], [3])
        self.assertEqual(decode(L._response_evidence([])), [])


class MalformedHTTP:
    """Serve exact bad bytes; a wire suffix is intentionally never consumed."""
    write_context = F.LegacyCaseTest.write_context
    server = F.LegacyCaseTest.server

    def exercise(self, kind, case):
        server = self.server()
        original = F.FakeHandler.do_POST
        emitted = []

        def post(handler):
            if handler.path != '/v1/chat/completions':
                return original(handler)
            body = json.loads(handler.rfile.read(int(handler.headers['Content-Length'])))
            with server.lock:
                server.payloads.append(body)
            first = malformed(kind, body['max_tokens'], len(F.fake_ids(body['messages'][0]['content'], True)))
            observed = [first, b'\n', b'data: [DONE]\n'] if kind == 'utf8' else [first]
            emitted.append(observed)
            wire = first + b'\ndata: [DONE]\n\n: UNCONSUMED SUFFIX\n'
            handler.send_response(200)
            handler.send_header('Content-Type', 'text/event-stream')
            handler.send_header('Content-Length', str(len(wire)))
            handler.end_headers()
            try:
                handler.wfile.write(wire); handler.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        with patch.object(F.FakeHandler, 'do_POST', post):
            self.assertEqual(self.execute(case), 1)
        evidence = L.json_bytes((self.root / 'legacy-evidence.json').read_bytes())
        self.assertEqual(len(server.payloads), 1)
        self.assertEqual(len(evidence['requests']), 1)
        response = evidence['requests'][0]['response']
        self.assertEqual(decode(response), emitted[0])
        self.assertNotIn('capture_error', response)
        self.assertIsNotNone(response['parse_error'])
        self.assertNotIn(b'UNCONSUMED', b''.join(decode(response)))
        self.assertTrue(evidence['error'])
        receipt = L.json_bytes(Path(self.plan['client']['output']).read_bytes())
        self.assertEqual(receipt['exit_code'], 1)
        self.assertEqual(receipt['evidence_sha256'], L.digest((self.root / 'legacy-evidence.json').read_bytes()))
        if kind == 'usage':
            self.assertIsNone(evidence['requests'][0]['record'])  # Finalization, not normal return capture.


class WarmHTTPTests(MalformedHTTP, unittest.TestCase):
    setUp = F.LegacyCaseTest.setUp
    execute = F.LegacyCaseTest.execute

    @classmethod
    def setUpClass(cls):
        cls.corpus = F.fixture_corpus()

    def test_choices_scalar_preserves_observed_bytes(self):
        self.exercise('choices', 'warm4k_c4')

    def test_delta_scalar_preserves_observed_bytes(self):
        self.exercise('delta', 'warm4k_c4')

    def test_malformed_json_preserves_observed_bytes(self):
        self.exercise('json', 'warm4k_c4')

    def test_invalid_utf8_stops_before_another_primer(self):
        self.exercise('utf8', 'warm4k_c4')

    def test_finalization_keeps_bytes_when_driver_does_not_return(self):
        self.exercise('usage', 'warm4k_c4')


if __name__ == '__main__':
    unittest.main()
