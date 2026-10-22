"""Actual isolated quality client and scorer CLIs with loopback synthetic data.

No native service, image, model, candidate code or field qualification is used.
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import unittest

from test_q03_quality_pilot import ROOT, MATERIAL, TOOLBOX, plan
from test_q03_quality_capture import answer
import q03_quality_capture as C
import q03_quality_pilot as P
import q03_http_client as HC


class ExecutableEntryTests(unittest.TestCase):
    def run_entry(self, directory, mask, *, fail_token=False, arm='r6x188'):
        value, context = plan(directory)
        value['selection']['arm'] = arm
        value['lane_id'] = arm + '/unloaded'
        client = value['client']
        client['toolbox_path'] = TOOLBOX
        client['toolbox_files'] = C.strict_json((ROOT / 'scripts/q03_http_toolbox.json').read_bytes())['files']
        client['quality_sources'] = {
            name: C.digest((ROOT / 'scripts' / (name + '.py')).read_bytes())
            for name in HC.QUALITY_NAMES}
        prompts = [json.loads(line) for line in Path(MATERIAL, 'prompts.jsonl').read_bytes().splitlines()]
        lookup = {p['text']: i for i, p in enumerate(prompts)}
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                calls.append(self.path)
                if self.path != '/metrics':
                    self.send_error(404)
                    return
                self.reply(b'vllm:num_requests_running 0\nvllm:num_requests_waiting 0\n')

            def do_POST(self):
                calls.append(self.path)
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                index = lookup[body['messages'][0]['content']]
                if self.path == '/tokenize':
                    self.reply(C.canonical({'tokens': [1, 999 if fail_token and index == 1 else index + 2], 'count': 2}))
                elif self.path == '/v1/chat/completions':
                    self.reply(C.canonical(answer(index)))
                else:
                    self.send_error(404)

            def reply(self, raw):
                self.send_response(200)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
            value['url'] = context['url'] = 'http://127.0.0.1:' + str(server.server_port)
            Path(client['runtime_context']).write_bytes(C.canonical(context))
            path = Path(directory, 'client-plan.json')
            raw = C.canonical(value)
            path.write_bytes(raw)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                result = subprocess.run([sys.executable, '-I', '-B', str(ROOT / 'scripts/q03_http_client.py'),
                    '--plan', str(path), '--sha256', C.digest(raw)],
                    capture_output=True, text=True, timeout=30, umask=mask)
            finally:
                server.shutdown()
                worker.join(timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        capture = Path(directory, 'quality-capture')
        self.assertEqual(stat.S_IMODE(capture.stat().st_mode), 0o700)
        for item in capture.rglob('*'):
            self.assertEqual(stat.S_IMODE(item.stat().st_mode), 0o600, str(item))
            self.assertTrue(stat.S_ISREG(item.stat().st_mode), str(item))
        receipt_path = Path(directory, 'client-receipt.json')
        self.assertEqual(stat.S_IMODE(receipt_path.stat().st_mode), 0o600)
        receipt = C.strict_json(receipt_path.read_bytes())
        summary = C.strict_json((capture / 'quality-capture.json').read_bytes())
        return value, context, receipt, summary, calls

    def test_actual_unloaded_client_and_offline_scorer_keep_private_evidence(self):
        # Both approved first-pilot arms exercise the same no-override CLI.
        for mask, arm in ((0o000, 'r6x188'), (0o002, 'native197-off'),
                          (0o022, 'r6x188'), (0o077, 'native197-off')):
            with self.subTest(umask=oct(mask), arm=arm), tempfile.TemporaryDirectory() as directory:
                value, context, receipt, summary, calls = self.run_entry(directory, mask, arm=arm)
                self.assertEqual(receipt['exit_code'], 0)
                self.assertEqual(len(calls), 125)
                self.assertEqual(calls[:60], ['/tokenize'] * 60)
                output = Path(value['client']['output'])
                self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
                validated = P.validate_output(C.strict_json(output.read_bytes()), value, context=context)
                self.assertFalse(validated['comparison_eligible'])
                self.assertEqual(validated['semantic_scoring'], 'NOT_EVALUATED')
                self.assertEqual(summary['counts']['COMPLETED'], 60)
                self.assertEqual((output.parent / 'quality-capture/response-000.bin').read_bytes(), C.canonical(answer(0)))
                report = Path(directory, 'data-observations.json')
                command = [sys.executable, '-I', '-B', str(ROOT / 'scripts/q03_quality_data_score.py'),
                    '--materials', str(MATERIAL), '--capture', str(output.parent / 'quality-capture'),
                    '--max-response-bytes', '100000', '--output', str(report)]
                scored = subprocess.run(command, capture_output=True, text=True, timeout=10, umask=mask)
                self.assertEqual(scored.returncode, 0, scored.stdout + scored.stderr)
                self.assertEqual(stat.S_IMODE(report.stat().st_mode), 0o600)
                raw = report.read_bytes()
                result = C.strict_json(raw)
                self.assertEqual(result['overall_quality'], 'NOT_EVALUATED')
                self.assertEqual(result['counts']['evaluated'], 44)
                self.assertEqual(result['counts']['pending_isolated_code'], 8)
                self.assertEqual(result['counts']['pending_blind'], 8)
                self.assertEqual(result['counts']['comparison_eligible'], 0)
                self.assertEqual(result['counts']['evidence_error'], 0)
                again = subprocess.run(command, capture_output=True, text=True, timeout=10, umask=mask)
                self.assertEqual(again.returncode, 2)
                self.assertEqual(report.read_bytes(), raw, 'existing report must not be rewritten')

    def test_actual_token_mismatch_retains_all_slots_and_never_generates(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, receipt, summary, calls = self.run_entry(directory, 0, fail_token=True)
            self.assertEqual(receipt['exit_code'], 1)
            self.assertEqual(calls, ['/tokenize', '/tokenize'])
            self.assertEqual(summary['counts'], {'COMPLETED': 0, 'TRUNCATED': 0, 'ERROR': 0, 'NOT_RUN': 60})
            self.assertEqual(summary['semantic_scoring'], 'NOT_EVALUATED')
            self.assertTrue(summary['capture_error'])
            self.assertFalse(Path(directory, 'quality-pr8.json').exists())


if __name__ == '__main__':
    unittest.main()
