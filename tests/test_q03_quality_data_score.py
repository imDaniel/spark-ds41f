"""Synthetic offline boundaries; no original gold/reference suite or model calls."""
import base64
import copy
import contextlib
import importlib
import io
import os
import json
from pathlib import Path
import random
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import q03_quality_data_score as S

FIXTURES = ROOT / 'tests/fixtures/r6x-quality-data-score.json'


class NumberTests(unittest.TestCase):
    def test_frozen_fixture_identity(self):
        self.assertEqual(S.digest(FIXTURES.read_bytes()), S.FIXTURE_SHA256)
        self.assertEqual(S.digest((ROOT / 'docs/r6x-quality-api-content-contract.md').read_bytes()), S.API_CONTENT_CONTRACT_SHA256)

    def test_frozen_synthetic_json_examples(self):
        for name, gold, answer, expected in json.loads(FIXTURES.read_text())['json']:
            with self.subTest(name=name):
                self.assertIs(S.score_content('strict-json-equality', S.load_json(gold), answer)[0], expected)

    def test_frozen_synthetic_text_examples(self):
        for name, gold, answer, expected in json.loads(FIXTURES.read_text())['text']:
            with self.subTest(name=name):
                self.assertIs(S.score_content('exact-text', gold, answer)[0], expected)

    def test_signed_exponent_offset_boundaries(self):
        rng = random.Random(113)
        for _ in range(300):
            number, offset = rng.randrange(-100000, 100000), rng.randrange(-999, 999)
            self.assertEqual(S._signed_add_small(str(number), offset), str(number + offset))
        self.assertEqual(S._signed_add_small('+' + '0' * 10000 + '1', -1), '0')
        self.assertEqual(S._signed_add_small('9' * 10000, 1), '1' + '0' * 10000)
        self.assertEqual(S._signed_add_small('-1' + '0' * 10000, 1), '-' + '9' * 10000)

    def test_unbounded_exponent_is_not_expanded(self):
        exponent = '8' * 10000
        self.assertTrue(S.same(S.load_json('1e' + exponent), S.load_json('1.0e' + exponent)))
        self.assertFalse(S.same(S.load_json('1e' + exponent), S.load_json('1e' + exponent[:-1] + '9')))
        self.assertFalse(S.same(S.load_json('1e' + exponent), S.load_json('1')))
        self.assertTrue(S.same(S.load_json('1' * 10000), S.load_json('1' * 10000 + '.0')))

    def test_resource_failure_is_not_syntax_or_wrong(self):
        with self.assertRaises(S.EvaluatorResourceError):
            S.score_content('strict-json-equality', S.load_json('[]'), '[' * 10000 + ']' * 10000)

    def test_no_float_context_or_bool_alias(self):
        self.assertFalse(S.same(S.load_json('{"x":[true]}'), S.load_json('{"x":[1]}')))
        self.assertFalse(S.same(S.load_json('1'), S.load_json('"1"')))
        self.assertTrue(S.same(S.load_json('123000e-5'), S.load_json('1.2300')))
        self.assertFalse(S.same(S.load_json('1.000000000000000000000000000000000001'), S.load_json('1')))


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.bound = 65536
        self.selection = {'arm': 'r6x188', 'steering': 'unloaded'}
        self.case = {'id': 'synthetic-case', 'prompt_text': 'Give the number as JSON.',
                     'prompt_utf8_sha256': S.digest(b'Give the number as JSON.'),
                     'category': 'synthetic', 'language': 'en', 'case_record_sha256': '1' * 64,
                     'gold': {'mode': 'strict-json-equality', 'expected': S.load_json('{"n":7}')}}
        self.original = {'index': 0, 'case_id': self.case['id'], 'category': 'synthetic', 'language': 'en',
                         'prompt_sha256': self.case['prompt_utf8_sha256'], 'expected_input_ids': [1, 2],
                         'expected_input_count': 2, 'expected_input_ids_sha256': S.digest(S.canonical([1, 2])),
                         'outcome': 'NOT_RUN', 'semantic_scoring': 'NOT_EVALUATED'}
        self.record = copy.deepcopy(self.original)
        payload = {'model': S.MODEL, 'messages': [{'role': 'user', 'content': self.case['prompt_text']}],
                   'max_tokens': 2048, 'temperature': 0, 'seed': 2048, 'return_token_ids': True, 'cache_salt': 'synthetic-0'}
        wire = json.dumps(payload).encode()
        self.record['request'] = {'endpoint': 'http://127.0.0.1:9999/v1/chat/completions', 'payload': payload,
                                 'cache_salt': 'synthetic-0', 'wire_base64': base64.b64encode(wire).decode(),
                                 'wire_sha256': S.digest(wire), 'semantic_sha256': S.digest(S.canonical({k: v for k, v in payload.items() if k != 'cache_salt'}))}
        self.answer = {'model': S.MODEL, 'prompt_token_ids': [1, 2],
                       'choices': [{'index': 0, 'token_ids': [9], 'message': {'role': 'assistant', 'content': '{"n":7}', 'reasoning': 'not the final answer'}, 'finish_reason': 'stop'}],
                       'usage': {'prompt_tokens': 2, 'completion_tokens': 1, 'total_tokens': 3},
                       'unknown_field': {'audit': 'preserved'}}
        self.record['outcome'] = 'COMPLETED'

    def write(self, *, raw=None, mutate=None):
        if raw is None:
            raw = S.canonical(self.answer)
        self.record['response'] = {'raw_path': 'response-000.bin', 'raw_bytes': len(raw), 'raw_sha256': S.digest(raw),
                                   'complete': True, 'overflow': False, 'http_status': 200, 'decoded': copy.deepcopy(self.answer), 'parse_error': None,
                                   'headers': [['Content-Length', str(len(raw))]], 'headers_overflow': False}
        if mutate:
            mutate(self.record)
        (self.directory / 'response-000.bin').write_bytes(raw)
        (self.directory / 'record-000.json').write_bytes(S.canonical(self.record))
        (self.directory / 'request-000.json').write_bytes(S.canonical(self.record))

    def score(self):
        return S._score_slot(self.directory, self.case, 0, self.record, self.original, self.selection, self.bound)

    def test_positive_preserves_full_raw_reference_and_ineligibility(self):
        self.write(); result = self.score()
        self.assertIs(result['correctness'], True)
        self.assertFalse(result['comparison_eligible'])
        self.assertEqual(result['raw_response']['sha256'], S.digest(S.canonical(self.answer)))
        self.assertEqual(json.loads((self.directory / result['raw_response']['path']).read_text())['unknown_field'], {'audit': 'preserved'})
        self.assertEqual(result['refusal_annotation']['status'], 'pending_contextual_review')

    def test_reasoning_cannot_rescue_wrong_final(self):
        self.answer['choices'][0]['message'].update(content='{"n":0}', reasoning='{"n":7}')
        self.write(); self.assertIs(self.score()['correctness'], False)

    def test_missing_null_empty_final_are_failures_not_infrastructure(self):
        for kind in ('missing', None, ''):
            with self.subTest(kind=kind):
                message = self.answer['choices'][0]['message']
                if kind == 'missing':
                    message.pop('content', None)
                else:
                    message['content'] = kind
                self.answer['choices'][0]['finish_reason'] = 'length'
                self.record['outcome'] = 'TRUNCATED'
                self.write(); result = self.score()
                self.assertEqual(result['evidence_status'], 'valid')
                self.assertIs(result['correctness'], False)
                self.assertTrue(result['truncated'])
                self.assertTrue(result['empty_final_answer'])
                self.assertEqual(result['constraint_violation'], 'NO_API_VISIBLE_ANSWER')

    def test_correct_truncated_is_correct_but_still_truncated(self):
        self.answer['choices'][0]['finish_reason'] = 'length'; self.record['outcome'] = 'TRUNCATED'
        self.write(); result = self.score()
        self.assertIs(result['correctness'], True); self.assertTrue(result['truncated'])

    def test_natural_stop_is_not_completion_proof(self):
        self.answer['choices'][0]['message']['content'] = '{"n":'
        self.write(); result = self.score()
        self.assertIs(result['correctness'], False); self.assertFalse(result['truncated'])

    def test_cap_reached_does_not_invent_truncation_or_reasoning_count(self):
        self.answer['choices'][0]['token_ids'] = [9] * 2048
        self.answer['usage'].update(completion_tokens=2048, total_tokens=2050)
        self.write(); result = self.score()
        self.assertTrue(result['cap_reached']); self.assertFalse(result['truncated'])
        self.assertIsNone(result['reasoning_token_count'])

    def test_nonstring_final_schema_error(self):
        self.answer['choices'][0]['message']['content'] = ['n', 7]
        self.write(); result = self.score()
        self.assertIsNone(result['correctness']); self.assertEqual(result['evidence_status'], 'invalid')

    def test_answer_invalid_json_is_quality_failure(self):
        self.answer['choices'][0]['message']['content'] = '{"n":7,"n":7}'
        self.write(); result = self.score()
        self.assertIs(result['correctness'], False); self.assertEqual(result['evidence_status'], 'valid')

    def test_outer_duplicate_and_non_success_are_evidence_errors(self):
        self.write(raw=b'{"choices":[], "choices":[]}'); result = self.score()
        self.assertIsNone(result['correctness']); self.assertEqual(result['evidence_status'], 'invalid')
        self.write(mutate=lambda r: r['response'].update(http_status=503)); result = self.score()
        self.assertIsNone(result['correctness']); self.assertEqual(result['evidence_status'], 'invalid')

    def test_retained_content_length_invalid_or_duplicate_is_not_quality(self):
        for headers in ([['Content-Length', '1']], [['Content-Length', '-1']],
                        [['Content-Length', '１２']], [['Content-Length', '9' * 10000]],
                        [['Content-Length', '1'], ['content-length', '1']],
                        [['Content-Length']], {'Content-Length': '1'}):
            with self.subTest(headers=str(headers)[:80]):
                self.write(mutate=lambda r: r['response'].update(headers=headers))
                result = self.score()
                self.assertEqual(result['evidence_status'], 'invalid')
                self.assertIsNone(result['correctness'])

    def test_retained_header_byte_bound_is_enforced(self):
        self.write()
        result = S._score_slot(self.directory, self.case, 0, self.record, self.original,
                               self.selection, self.bound, header_bound=1)
        self.assertEqual(result['evidence_status'], 'invalid')
        self.assertIsNone(result['correctness'])

    def test_capture_incomplete_overflow_or_notrun_not_wrong(self):
        self.write(mutate=lambda r: r['response'].update(complete=False, overflow=True))
        result = self.score(); self.assertEqual(result['evidence_status'], 'incomplete'); self.assertIsNone(result['correctness'])
        self.record['outcome'] = 'NOT_RUN'; self.write()
        result = self.score(); self.assertEqual(result['evidence_status'], 'incomplete'); self.assertIsNone(result['correctness'])

    def test_raw_hash_request_prompt_and_token_identity(self):
        mutations = [lambda r: r['response'].update(raw_sha256='0' * 64),
                     lambda r: r['request']['payload']['messages'][0].update(content='other prompt'),
                     lambda r: r.update(expected_input_ids=[1, True]),
                     lambda r: r.update(prompt_sha256='0' * 64)]
        clean = copy.deepcopy(self.record)
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.record = copy.deepcopy(clean); self.write(mutate=mutation)
                result = self.score(); self.assertEqual(result['evidence_status'], 'invalid'); self.assertIsNone(result['correctness'])

    def test_root_input_mismatch_is_not_model_failure(self):
        self.answer['prompt_token_ids'] = [1, 3]; self.write()
        result = self.score(); self.assertEqual(result['evidence_status'], 'invalid'); self.assertIsNone(result['correctness'])

    def test_all_decoded_fields_remain_raw_bound(self):
        self.write(mutate=lambda r: r['response']['decoded']['choices'][0]['message'].update(reasoning='changed'))
        result = self.score(); self.assertEqual(result['evidence_status'], 'invalid'); self.assertIsNone(result['correctness'])

    def test_fixed_literal_api_score_target_is_not_internal_extraction(self):
        self.write(); result = self.score()
        self.assertEqual(result['score_target'], {'kind': 'literal_api_field', 'json_pointer': '/choices/0/message/content'})
        self.assertEqual(result['evaluation_status'], 'scored')
        self.assertIs(result['correctness'], True)
        self.assertFalse(result['comparison_eligible'])

    def test_code_and_blind_cannot_execute_or_pass(self):
        marker = self.directory / 'MUST_NOT_EXIST'
        self.answer['choices'][0]['message']['content'] = f"__import__('pathlib').Path({str(marker)!r}).touch()"
        for mode in ('python-unit-assertions', 'blind-rubric'):
            self.case['gold'] = {'mode': mode}; self.write(); result = self.score()
            self.assertEqual(result['evaluation_status'], 'not_evaluated'); self.assertIsNone(result['correctness'])
            self.assertEqual(result['candidate_execution_status'], 'NOT_EVALUATED')
            self.assertFalse(marker.exists())

    def test_answer_parser_resource_failure_not_wrong(self):
        self.answer['choices'][0]['message']['content'] = '[' * 10000 + ']' * 10000
        self.write(); result = self.score()
        self.assertEqual(result['evaluation_status'], 'evaluator_error'); self.assertIsNone(result['correctness'])
        self.assertEqual(result['evidence_status'], 'valid')

    def test_symlink_and_path_escape_rejected(self):
        self.write(); (self.directory / 'response-000.bin').unlink()
        (self.directory / 'response-000.bin').symlink_to('/dev/null')
        result = self.score(); self.assertEqual(result['evidence_status'], 'invalid')
        (self.directory / 'response-000.bin').unlink()
        self.write(mutate=lambda r: r['response'].update(raw_path='../other'))
        result = self.score(); self.assertEqual(result['evidence_status'], 'invalid')


class InventoryTests(unittest.TestCase):
    def test_all_sixty_retained_and_no_subset_or_claimed_parser_promotion(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            prompts = b'{"id":"synthetic","text":"synthetic"}\n'
            files = dict(S.MATERIAL_FILES); files['prompts.jsonl'] = (len(prompts), S.digest(prompts))
            modes = ['strict-json-equality'] * 36 + ['exact-text'] * 8 + ['python-unit-assertions'] * 8 + ['blind-rubric'] * 8
            cases = [{'id': f'synthetic-{i}', 'case_record_sha256': '1' * 64, 'prompt_utf8_sha256': '2' * 64, 'gold': {'mode': mode}} for i, mode in enumerate(modes)]
            records = [{'index': i, 'case_id': c['id']} for i, c in enumerate(cases)]
            bounds = {'response_bytes': 65536, 'header_bytes': 8192}
            identity = {'window_id': 'synthetic', 'attempt_id': 'primary', 'lane_id': 'lane', 'plan_sha256': '3' * 64,
                        'material_sha256': S.MATERIAL_SHA256, 'prompts_sha256': S.digest(prompts), 'transport_bounds': bounds,
                        'selection': {'arm': 'r6x188', 'steering': 'unloaded'},
                        'context': {'final_answer_extraction': {'kind': 'native-message-content', 'status': 'VERIFIED', 'parser_source_sha256': '4' * 64}}}
            capture = {'schema': 'r6x-quality-capture/1', 'identity': identity, 'transport_bounds': bounds, 'records': records}
            (directory / 'prompts.jsonl').write_bytes(prompts)
            (directory / 'identity.json').write_bytes(S.canonical(identity))
            (directory / 'case-index.json').write_bytes(S.canonical(records))
            for broken in (False, 'duplicate', 'list_salt', 'null_salt', 'dict_salt'):
                capture['records'] = copy.deepcopy(records)
                if broken == 'duplicate':
                    capture['records'][1] = capture['records'][0]
                elif broken:
                    capture['records'][0]['request'] = {'cache_salt': {'list_salt': [], 'null_salt': None, 'dict_salt': {}}[broken]}
                (directory / 'quality-capture.json').write_bytes(S.canonical(capture))
                with patch.object(S, 'load_material', return_value=cases), patch.object(S, 'MATERIAL_FILES', files):
                    report = S.evaluate(directory, directory, max_response_bytes=65536)
                self.assertEqual(len(report['records']), 60)
                self.assertEqual(report['overall_quality'], 'NOT_EVALUATED')
                self.assertEqual(report['counts']['evaluated'], 0)
                self.assertEqual(report['native_internal_final_extraction']['status'], 'UNBOUND')
                self.assertEqual(report['planned_pilot_slots'], 720)
                self.assertEqual(report['counts']['comparison_eligible'], 0)
                if broken:
                    self.assertFalse(report['inventory_valid'])
                    self.assertEqual(report['counts']['evidence_error'], 60)

    def test_no_executable_imports_or_network_hooks(self):
        source = Path(S.__file__).read_text()
        import ast
        tree = ast.parse(source)
        imports = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        self.assertFalse(imports & {'subprocess', 'socket', 'urllib', 'requests', 'pickle', 'importlib'})
        calls = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        self.assertFalse(calls & {'eval', 'exec', '__import__', 'compile'})


class RealCaptureInteropTests(unittest.TestCase):
    def _capture_report(self, fail_index=None):
        # Source-only PR8 loop + the actual capture writer, with fake HTTP only.
        # Read frozen prompts as data; do not run original gold/reference tests.
        import q03_quality_capture as C
        from q03_http_client import FrozenToolbox
        from quality_test_fixtures import MATERIAL, toolbox
        root = toolbox()
        material = MATERIAL
        inventory = C.strict_json((ROOT / 'scripts/q03_http_toolbox.json').read_bytes())['files']
        sources = {name: (root / name).read_bytes() for name in inventory}
        self.assertTrue(all(C.digest(sources[name]) == pin for name, pin in inventory.items()))
        self.assertFalse(any(name == 'servebench' or name.startswith('servebench.') for name in sys.modules))
        loader = FrozenToolbox(root, sources)
        sys.meta_path.insert(0, loader)
        try:
            cli = importlib.import_module('servebench.__main__')
            behaviour = importlib.import_module('servebench.behaviour')
            prompts = [json.loads(line) for line in (material / 'prompts.jsonl').read_bytes().splitlines()]
            cases = [{'id': item['id'], 'category': 'synthetic', 'language': 'en',
                      'prompt_sha256': C.digest(item['text'].encode()), 'input_ids': [1, i + 2]}
                     for i, item in enumerate(prompts)]
            with tempfile.TemporaryDirectory() as temp:
                calls = []
                class Response:
                    def __init__(self, raw):
                        self.status, self.headers, self.stream = 200, {'Content-Length': str(len(raw))}, io.BytesIO(raw)
                    def __enter__(self): return self
                    def __exit__(self, *args): pass
                    def read(self, size): return self.stream.read(size)
                def fake(request, timeout):
                    if request.full_url.endswith('/metrics'):
                        return Response(b'vllm:num_requests_running 0\nvllm:num_requests_waiting 0\n')
                    index = len(calls); calls.append(request)
                    if index == fail_index:
                        return Response(b'{invalid-envelope')
                    answer = {'model': C.MODEL, 'prompt_token_ids': [1, index + 2],
                              'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'synthetic, not a material gold answer',
                                            'reasoning': 'preserved synthetic reasoning'}, 'token_ids': [3], 'finish_reason': 'stop'}],
                              'usage': {'prompt_tokens': 2, 'completion_tokens': 1, 'total_tokens': 3}}
                    if fail_index is not None and index == fail_index - 1:
                        answer['choices'][0]['finish_reason'] = 'length'
                    return Response(C.canonical(answer))
                plan = {'url': 'http://127.0.0.1:8000', 'window_id': 'synthetic-window',
                        'attempt_id': 'synthetic-primary', 'lane_id': 'synthetic-lane', 'plan_sha256': '1' * 64,
                        'selection': {'arm': 'r6x188', 'steering': 'unloaded'},
                        'client': {'output': str(Path(temp) / 'pr8.json'), 'quality_capture': {
                            'material_sha256': C.MATERIAL_SHA256, 'prompts_path': str(material / 'prompts.jsonl'),
                            'prompts_sha256': C.PROMPTS_SHA256, 'cases': cases, 'max_response_bytes': 100000,
                            'max_header_bytes': 8192, 'context': {'runtime_receipt_sha256': '2' * 64,
                                'tokenizer_identity_sha256': '3' * 64, 'decode_identity_sha256': '4' * 64,
                                'observed_loaded_state': {'status': 'SYNTHETIC'},
                                'applied_state_evidence': {'status': 'UNVERIFIED_SYNTHETIC'},
                                'final_answer_extraction': {'status': 'UNBOUND'}}}}}
                with patch.object(behaviour, 'score', side_effect=AssertionError('scoring forbidden')), contextlib.redirect_stdout(io.StringIO()):
                    code = C.run_client(plan, cli.main, deadline=20, open_response=fake, clock=lambda: 10, request_seconds=3)
                self.assertEqual(code, int(fail_index is not None))
                self.assertEqual(len(calls), 60 if fail_index is None else fail_index + 1)
                captured = Path(temp) / 'quality-capture'
                before = {p.name: S.digest(p.read_bytes()) for p in captured.iterdir() if p.is_file()}
                report = S.evaluate(material, captured, max_response_bytes=100000)
                self.assertEqual(before, {p.name: S.digest(p.read_bytes()) for p in captured.iterdir() if p.is_file()})
                self.assertEqual(json.loads((captured / 'quality-capture.json').read_bytes())['semantic_scoring'], 'NOT_EVALUATED')
                self.assertEqual(report['schema'], 'r6x-quality-data-score/2')
                self.assertEqual(report['applied_scale_status'], 'UNBOUND')
                return report
        finally:
            sys.meta_path.remove(loader)
            for name in list(sys.modules):
                if name == 'servebench' or name.startswith('servebench.'):
                    del sys.modules[name]

    def test_actual_capture_bundle_scores_literal_field_without_native_proof(self):
        report = self._capture_report()
        self.assertTrue(report['inventory_valid'])
        self.assertEqual(report['counts']['completed'], 60)
        self.assertEqual(report['counts']['evidence_error'], 0)
        self.assertEqual(report['counts']['evaluated'], 44)
        self.assertEqual(report['counts']['incorrect'], 44)
        self.assertEqual(report['counts']['correct'], 0)
        self.assertTrue(all(r['evidence_status'] == 'valid' for r in report['records']))
        self.assertTrue(all(r['correctness'] is False for r in report['records'] if r['mode'] in S.DATA_MODES))
        self.assertTrue(all(r['correctness'] is None for r in report['records'] if r['mode'] not in S.DATA_MODES))
        self.assertEqual(report['counts']['pending_blind'], 8)
        self.assertEqual(report['counts']['pending_isolated_code'], 8)
        self.assertEqual(report['score_target'], S.SCORE_TARGET)
        self.assertEqual(report['native_internal_final_extraction']['status'], 'UNBOUND')
        self.assertEqual(report['overall_quality'], 'NOT_EVALUATED')
        self.assertEqual(report['counts']['comparison_eligible'], 0)
        self.assertEqual(report['identity']['transport_bounds']['request_seconds'], 3)

    def test_actual_partial_capture_keeps_truncated_wrong_error_and_notrun(self):
        report = self._capture_report(fail_index=11)
        self.assertTrue(report['inventory_valid'])
        self.assertEqual(len(report['records']), 60)
        self.assertEqual(report['counts']['planned'], 60)
        self.assertEqual(report['counts']['data_cases_planned'], 44)
        self.assertEqual(report['counts']['attempted'], 12)
        self.assertEqual(report['counts']['completed'], 10)
        self.assertEqual(report['counts']['truncated'], 1)
        self.assertEqual(report['counts']['evidence_error'], 1)
        self.assertEqual(report['counts']['not_run'], 48)
        self.assertEqual(report['counts']['evaluated'], 1)
        self.assertEqual(report['counts']['incorrect'], 1)
        self.assertEqual(report['content_accuracy']['denominator'], 1)
        self.assertTrue(report['records'][10]['truncated'])
        self.assertIs(report['records'][10]['correctness'], False)
        self.assertIsNone(report['records'][11]['correctness'])
        self.assertIsNone(report['records'][12]['correctness'])
        self.assertEqual(report['overall_quality'], 'NOT_EVALUATED')
        self.assertEqual(report['counts']['comparison_eligible'], 0)


if __name__ == '__main__':
    unittest.main()
