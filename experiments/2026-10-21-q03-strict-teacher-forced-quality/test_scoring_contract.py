"""Synthetic CPU tests only; no network, endpoint, GPU or native imports."""
import copy
import hashlib
import json
import unittest

import scoring_contract as contract


def fixture():
    digest = hashlib.sha256(b'synthetic fixture, not a model receipt').hexdigest()
    sequences = [
        {'id': 0, 'prompt_ids': [10, 11], 'output_ids': [12, 13], 'finish_reason': 'length'},
        {'id': 1, 'prompt_ids': [20], 'output_ids': [21, 22], 'finish_reason': 'length'},
    ]
    frozen = {
        'model': 'synthetic-fixture-only', 'context_limit': 5, 'top_k': 2,
        'identity': {'checkpoint_sha256': digest, 'tokenizer_assets_sha256': digest,
                     'tokenizer_implementation': 'synthetic fixture, no tokenizer called',
                     'chat_template_sha256': digest, 'encoding_options_sha256': digest,
                     'native_commit': '154159dc3e38ca8b2321c01a153a1b2ccaf8663b',
                     'image_digest': 'sha256:' + digest,
                     'state': {'startup_profile': 'fixture', 'profile_artifact_sha256': digest,
                               'profile_identity': digest, 'effective_scale_fp32_bits': '3f800000'}},
        'generation_extra_body': {'steering_profile': 'fixture', 'steering_scale': 1,
                                  'chat_template_kwargs': {'thinking': False}},
        'raw_corpus_sha256': digest, 'corpus_sha256': contract.corpus_digest(sequences),
        'sequences': sequences,
    }
    evidence = {key: copy.deepcopy(frozen[key]) for key in
                ('generation_extra_body', 'raw_corpus_sha256', 'corpus_sha256')}
    evidence.update(generation_identity=copy.deepcopy(frozen['identity']),
                    scoring_identity=copy.deepcopy(frozen['identity']), records=[])
    for seq in sequences:
        request = contract.strict_request(frozen, seq['id'], 'fixture-salt-' + str(seq['id']))
        ids = request['prompt']
        rows = [None] + [{str(token): {'logprob': -0.3, 'rank': 1},
                         str(token + 100): {'logprob': -1.5, 'rank': 2}} for token in ids[1:]]
        evidence['records'].append({'id': seq['id'], 'request': request, 'response': {
            'model': frozen['model'], 'choices': [{'index': 0, 'finish_reason': 'length',
                'prompt_token_ids': ids[:], 'prompt_logprobs': rows, 'token_ids': [900]}]}})
    return frozen, evidence


class ScoringContractTests(unittest.TestCase):
    def assert_invalid(self, frozen, evidence):
        result = contract.validate(frozen, evidence)
        self.assertEqual(result['status'], 'INVALID_RECORDED_SCORING', result)
        self.assertEqual(result['scores'], [])
        self.assertFalse(result['quality_pass'])
        self.assertTrue(result['reasons'])

    def test_valid_complete_records_preserve_actual_echo_and_original_prefix(self):
        frozen, evidence = fixture()
        before = copy.deepcopy((frozen, evidence))
        result = contract.validate(frozen, evidence)
        self.assertEqual(result['status'], 'COMPLETE_RECORDED_SCORING_ONLY')
        self.assertEqual((result['sequences'], result['output_positions']), (2, 4))
        self.assertEqual(result['scores'][0]['prompt_ids'], [10, 11])
        self.assertEqual(result['scores'][0]['returned_prompt_token_ids'], [10, 11, 12, 13])
        self.assertEqual(result['scores'][0]['tokens'], [12, 13])
        self.assertEqual(result['scores'][0]['logprob'], [-0.3, -0.3])
        self.assertFalse(result['quality_pass'])
        self.assertIn('not full-vocabulary equality', result['scope'])
        self.assertEqual((frozen, evidence), before)

    def test_corpus_and_response_record_order_are_not_alignment(self):
        frozen, evidence = fixture()
        evidence['records'].reverse()
        self.assertEqual(contract.validate(frozen, evidence)['status'], 'COMPLETE_RECORDED_SCORING_ONLY')

    def test_return_ids_opt_in_changes_only_original_fixed_request_echo_option(self):
        frozen, evidence = fixture()
        request = contract.strict_request(frozen, 0, 'caller-salt')
        self.assertTrue(request.pop('return_token_ids'))
        self.assertEqual(request, {'model': frozen['model'], 'prompt': [10, 11, 12, 13],
                         'max_tokens': 1, 'temperature': 0, 'prompt_logprobs': 2,
                         'cache_salt': 'caller-salt', 'steering_profile': 'fixture', 'steering_scale': 1})
        self.assertNotIn('chat_template_kwargs', request)

    def test_original_pr8_projection_preserves_supported_forms_without_coercion(self):
        frozen, _ = fixture()
        for extra in (None, {}, {'steering_profile': 'fixture', 'steering_scale': 0},
                      {'steering_profile': None, 'steering_scale': None},
                      {'vllm_xargs': {'steering_profile': 'fixture', 'steering_scale': 1}},
                      {'steering_scale': 0, 'vllm_xargs': {'steering_scale': 1}}):
            with self.subTest(extra=extra):
                frozen['generation_extra_body'] = extra
                request = contract.strict_request(frozen, 0, 'salt')
                controls = {k: request[k] for k in ('steering_profile', 'steering_scale', 'vllm_xargs') if k in request}
                self.assertEqual(controls, contract.score_extra_body(extra))
        # Existing server validation, already covered in native steering contract,
        # remains responsible for conflicting/invalid semantic values.

    def test_missing_duplicate_error_and_unexpected_records_never_form_an_intersection(self):
        for mode in ('missing', 'duplicate', 'unexpected', 'error', 'empty_error', 'null_error', 'bool_id'):
            with self.subTest(mode=mode):
                frozen, evidence = fixture()
                if mode == 'missing': evidence['records'].pop()
                if mode == 'duplicate': evidence['records'].append(copy.deepcopy(evidence['records'][0]))
                if mode == 'unexpected': evidence['records'][1]['id'] = 99
                if mode == 'bool_id': evidence['records'][1]['id'] = True
                if mode in ('error', 'empty_error', 'null_error'):
                    evidence['records'][1]['error'] = {'error': 'failed', 'empty_error': '', 'null_error': None}[mode]
                self.assert_invalid(frozen, evidence)

    def test_freeze_is_independent_not_both_receipts_shared_truncation(self):
        frozen, evidence = fixture()
        evidence['records'] = evidence['records'][:1]
        evidence['corpus_sha256'] = contract.corpus_digest(frozen['sequences'][:1])
        self.assert_invalid(frozen, evidence)
        # If the caller also replaces its independent freeze, this cannot recover
        # the original intent. The README explicitly states the trust boundary.

    def test_each_declared_identity_and_generation_state_must_match_freeze(self):
        for field in contract.IDENTITY_KEYS:
            for phase in ('generation_identity', 'scoring_identity'):
                with self.subTest(field=field, phase=phase):
                    frozen, evidence = fixture()
                    evidence[phase][field] = 'changed'
                    self.assert_invalid(frozen, evidence)
        for field in ('raw_corpus_sha256', 'corpus_sha256', 'generation_extra_body'):
            frozen, evidence = fixture()
            evidence.pop(field)
            self.assert_invalid(frozen, evidence)

    def test_exact_request_cannot_change_ids_controls_context_or_sampling(self):
        mutations = {'model': 'other', 'prompt': [10, 11, 12], 'max_tokens': 0,
                     'temperature': 1, 'prompt_logprobs': 3, 'steering_scale': 0,
                     'steering_profile': 'other', 'return_token_ids': False,
                     'truncate_prompt_tokens': 3, 'echo': True, 'cache_salt': None,
                     'max_tokens_bool': True, 'temperature_bool': False}
        for key, value in mutations.items():
            with self.subTest(key=key):
                frozen, evidence = fixture()
                key = key.removesuffix('_bool')
                evidence['records'][0]['request'][key] = value
                self.assert_invalid(frozen, evidence)
        frozen, evidence = fixture()
        del evidence['records'][0]['request']['return_token_ids']
        self.assert_invalid(frozen, evidence)

    def test_distinct_salts_are_preserved_and_reuse_rejected(self):
        frozen, evidence = fixture()
        evidence['records'][1]['request']['cache_salt'] = evidence['records'][0]['request']['cache_salt']
        self.assert_invalid(frozen, evidence)

    def test_choice_level_echo_is_required_and_root_echo_cannot_substitute(self):
        for replacement in (None, [], [10, 11, 12], [999, 11, 12, 13], [10, 11, True, 13]):
            frozen, evidence = fixture()
            response = evidence['records'][0]['response']
            response['prompt_token_ids'] = [10, 11, 12, 13]
            response['choices'][0]['prompt_token_ids'] = replacement
            self.assert_invalid(frozen, evidence)
        frozen, evidence = fixture()
        del evidence['records'][0]['response']['choices'][0]['prompt_token_ids']
        self.assert_invalid(frozen, evidence)

    def test_exact_one_successful_choice_and_model(self):
        for mode in ('zero', 'two', 'wrong_index', 'bool_index', 'error', 'model', 'finish', 'root_error'):
            frozen, evidence = fixture()
            response = evidence['records'][0]['response']
            choice = response['choices'][0]
            if mode == 'zero': response['choices'] = []
            if mode == 'two': response['choices'].append(copy.deepcopy(choice))
            if mode == 'wrong_index': choice['index'] = 1
            if mode == 'bool_index': choice['index'] = False
            if mode == 'error': choice['error'] = None
            if mode == 'model': response['model'] = 'other'
            if mode == 'finish': choice['finish_reason'] = 'error'
            if mode == 'root_error': response['error'] = 'failed'
            self.assert_invalid(frozen, evidence)

    def test_every_row_is_complete_even_unscored_prefix_not_only_output_slice(self):
        for position in range(4):
            frozen, evidence = fixture()
            rows = evidence['records'][0]['response']['choices'][0]['prompt_logprobs']
            rows[position] = {} if position == 0 else None
            self.assert_invalid(frozen, evidence)
        for delta in (-1, 1):
            frozen, evidence = fixture()
            rows = evidence['records'][0]['response']['choices'][0]['prompt_logprobs']
            if delta == -1: rows.pop()
            else: rows.append(None)
            self.assert_invalid(frozen, evidence)

    def test_extreme_numeric_logprob_is_invalid_not_a_parser_crash(self):
        frozen, evidence = fixture()
        evidence['records'][0]['response']['choices'][0]['prompt_logprobs'][1]['11']['logprob'] = 10 ** 400
        self.assert_invalid(frozen, evidence)

    def test_nonfinite_missing_or_malformed_logprobs_are_rejected(self):
        for value in (float('nan'), float('inf'), -float('inf'), True, None, '0'):
            for token in ('11', '111'):
                frozen, evidence = fixture()
                evidence['records'][0]['response']['choices'][0]['prompt_logprobs'][1][token]['logprob'] = value
                self.assert_invalid(frozen, evidence)
        frozen, evidence = fixture()
        del evidence['records'][0]['response']['choices'][0]['prompt_logprobs'][2]['12']
        self.assert_invalid(frozen, evidence)

    def test_native_censor_sentinel_is_unresolved_not_quality_failure_or_pass(self):
        for position, token in ((1, '11'), (2, '112')):
            frozen, evidence = fixture()
            evidence['records'][0]['response']['choices'][0]['prompt_logprobs'][position][token]['logprob'] = -9999.0
            result = contract.validate(frozen, evidence)
            self.assertEqual(result['status'], 'UNRESOLVED_LOGPROB_CENSORING')
            self.assertEqual(result['scores'], [])
            self.assertFalse(result['quality_pass'])
            evidence['records'][1]['error'] = 'later failure must not disappear'
            self.assert_invalid(frozen, evidence)

    def test_positive_finite_logprob_is_invalid_probability(self):
        frozen, evidence = fixture()
        evidence['records'][0]['response']['choices'][0]['prompt_logprobs'][1]['11']['logprob'] = 0.00001
        self.assert_invalid(frozen, evidence)

    def test_unbounded_declared_top_k_rejects_without_range_allocation(self):
        frozen, evidence = fixture()
        frozen['top_k'] = 10 ** 12
        for record in evidence['records']:
            record['request']['prompt_logprobs'] = frozen['top_k']
        self.assert_invalid(frozen, evidence)

    def test_rank_top_k_key_alignment_and_given_outside_top_k(self):
        for value in (None, True, 0, -1, 1.5, '1', 2):
            frozen, evidence = fixture()
            evidence['records'][0]['response']['choices'][0]['prompt_logprobs'][1]['11']['rank'] = value
            self.assert_invalid(frozen, evidence)
        for key in ('01', '-1', '1.0', 'x'):
            frozen, evidence = fixture()
            row = evidence['records'][0]['response']['choices'][0]['prompt_logprobs'][1]
            row[key] = row.pop('111')
            self.assert_invalid(frozen, evidence)
        frozen, evidence = fixture()
        row = evidence['records'][0]['response']['choices'][0]['prompt_logprobs'][2]
        row['12'] = {'logprob': -4.0, 'rank': 3}
        row['212'] = {'logprob': -0.3, 'rank': 1}
        result = contract.validate(frozen, evidence)
        self.assertEqual(result['status'], 'COMPLETE_RECORDED_SCORING_ONLY')
        self.assertEqual(result['scores'][0]['rank'][0], 3)

    def test_context_plus_one_exact_boundary_and_full_1m_rejection_without_allocation(self):
        frozen, evidence = fixture()
        self.assertEqual(contract.validate(frozen, evidence)['status'], 'COMPLETE_RECORDED_SCORING_ONLY')
        frozen['context_limit'] = 4
        self.assert_invalid(frozen, evidence)
        # Same inequality as actual 1M case; avoid allocating million-token fixtures.
        self.assertGreater(1_047_552 + 1_024 + 1, 1_048_576)
        self.assertLessEqual(1_047_551 + 1_024 + 1, 1_048_576)

    def test_bad_frozen_corpus_is_not_filtered_or_retokenized(self):
        for mode in ('error', 'empty', 'dup', 'bool_id', 'bool_token', 'empty_prefix', 'empty_output', 'finish', 'digest'):
            frozen, evidence = fixture()
            seq = frozen['sequences'][0]
            if mode == 'error': seq['error'] = ''
            if mode == 'empty': frozen['sequences'] = []
            if mode == 'dup': frozen['sequences'].append(copy.deepcopy(seq))
            if mode == 'bool_id': seq['id'] = False
            if mode == 'bool_token': seq['prompt_ids'] = [True]
            if mode == 'empty_prefix': seq['prompt_ids'] = []
            if mode == 'empty_output': seq['output_ids'] = []
            if mode == 'finish': seq['finish_reason'] = 'stop'
            if mode == 'digest': frozen['corpus_sha256'] = 'wrong'
            self.assert_invalid(frozen, evidence)

    def test_json_reader_rejects_duplicate_members_and_nonfinite_constants(self):
        for text in ('{"id":0,"id":1}', '{"choices":[{"prompt_token_ids":[],"prompt_token_ids":[1]}]}',
                     '{"logprob":NaN}', '{"logprob":Infinity}', '{"logprob":-Infinity}'):
            with self.subTest(text=text), self.assertRaises(contract.InvalidEvidence):
                contract.load_json(text)
        frozen, evidence = fixture()
        self.assertEqual(contract.load_json(json.dumps(evidence)), evidence)



class StrictProducerTests(unittest.TestCase):
    def test_only_injected_memory_transport_and_preserved_complete_raw_echo(self):
        from strict_producer import collect_records
        frozen, evidence = fixture()
        captured = []
        def post(path, body, timeout):
            captured.append((path, copy.deepcopy(body), timeout))
            index = len(captured) - 1
            return copy.deepcopy(evidence['records'][index]['response'])
        salts = iter(['producer-0', 'producer-1'])
        records = collect_records(frozen, post, timeout=19, salt_factory=lambda: next(salts))
        evidence['records'] = records
        self.assertEqual(len(captured), 2)
        self.assertTrue(all(path == '/v1/completions' and timeout == 19 for path, _, timeout in captured))
        self.assertTrue(all(body['return_token_ids'] is True for _, body, _ in captured))
        self.assertEqual(contract.validate(frozen, evidence)['status'], 'COMPLETE_RECORDED_SCORING_ONLY')

    def test_producer_keeps_error_record_and_other_success_without_qualification(self):
        from strict_producer import collect_records
        frozen, evidence = fixture()
        calls = []
        def post(path, body, timeout):
            calls.append(body)
            if len(calls) == 1: raise ValueError('synthetic rejected request')
            return evidence['records'][1]['response']
        records = collect_records(frozen, post)
        self.assertEqual([r['id'] for r in records], [0, 1])
        self.assertIn('error', records[0])
        self.assertNotIn('error', records[1])
        evidence['records'] = records
        self.assertEqual(contract.validate(frozen, evidence)['scores'], [])

    def test_boolean_numeric_transport_mutation_is_not_silent(self):
        from strict_producer import collect_records
        frozen, evidence = fixture()
        def post(path, body, timeout):
            body['temperature'] = False
            return {}
        records = collect_records(frozen, post)
        self.assertTrue(all('error' in record for record in records))
        self.assertTrue(all(type(record['request']['temperature']) is int for record in records))

    def test_invalid_last_frozen_sequence_and_duplicate_salts_send_nothing(self):
        from strict_producer import collect_records
        for invalid_salt in (False, True):
            frozen, _ = fixture()
            calls = []
            if not invalid_salt: frozen['sequences'][1]['output_ids'] = []
            with self.assertRaises(contract.InvalidEvidence):
                collect_records(frozen, lambda *args: calls.append(args), salt_factory=lambda: 'same')
            self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
