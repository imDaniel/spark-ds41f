"""Finite offline data-only scorer for the R2 A188/B197 quality pilot.

Reads data, never candidate source. No network, model, plugin, code runner or
runtime admission path. Content observations are not execution comparability.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat

SCHEMA = 'r6x-quality-data-score/2'
MATERIAL_SHA256 = 'c3db307e1f145026c33252c36cda819ffefda1d189400d6e06d316454f3bf580'
FIXTURE_SHA256 = '133c5058f820ee62ac97392f212d99838a9ffd9162ffdf66e7ecff35063c26ce'
API_CONTENT_CONTRACT_SHA256 = '0985bcfb196eeb158a0175c9968f0244ed9aaf10746b23ce406910dab530ced1'
CONTRACT_SHA256 = '366045261663fd4729e9d10fe3b927dbadd23351fc66a41a8d38e17b8fc88e76'
MATERIAL_FILES = {
    'SHA256SUMS': (3110, MATERIAL_SHA256),
    'cases-with-gold.json': (121950, '0b124c8a98c6f3b2e57fa7171ed2fde140b514e0bbb657a05701e5ea29f9a715'),
    'gold-and-assertions.json': (45748, 'ebf697d914d9330222fe26fe8dc3121afb5d69aa8d1f30d5bb6042c7393bb59d'),
    'prompts.jsonl': (45132, '8cdcf6143d80d4776c052166a9764d36d8b5020038ad24118e352f3493cf4ca0'),
    'reference-tests.json': (6088, 'ae524c722d361a4befe121922de557962c5252703945c707cfd1c5905f049b7f'),
    'rubric.json': (1948, '6308766bb099e0f3866ad0146b8a1c94f8d5b22427c89777c9dcc5628ef9ae6d'),
    'SCORING-CONTRACT.md': (5528, '232310504a13b1f7ee72ace10ac6f7b99435cf00179054cc560feeb0a88cdfd2'),
}
DATA_MODES = ('strict-json-equality', 'exact-text')
MODES = DATA_MODES + ('python-unit-assertions', 'blind-rubric')
MODEL = 'deepseek-v4.1-flash'
SCORE_TARGET = {'kind': 'literal_api_field', 'json_pointer': '/choices/0/message/content'}


class EvidenceError(ValueError):
    """Broken capture/material identity, not a wrong answer."""


class JsonSyntaxError(ValueError):
    """Invalid JSON syntax or duplicate keys."""


class EvaluatorResourceError(RuntimeError):
    """Resource ceiling or parser exhaustion, never model incorrectness."""


def require(condition, message):
    if not condition:
        raise EvidenceError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _signed_add_small(text, delta):
    """Canonical signed decimal integer plus bounded offset, without big ints."""
    negative = text.startswith('-')
    digits = text.lstrip('+-').lstrip('0') or '0'
    if digits == '0':
        negative = False
    if not delta:
        return ('-' if negative else '') + digits
    other = str(abs(delta))
    other_negative = delta < 0
    if negative == other_negative:
        carry, out = 0, []
        for i in range(max(len(digits), len(other))):
            total = carry + (ord(digits[-i-1]) - 48 if i < len(digits) else 0) + (ord(other[-i-1]) - 48 if i < len(other) else 0)
            out.append(chr(48 + total % 10)); carry = total // 10
        if carry:
            out.append('1')
    else:
        if (len(digits), digits) < (len(other), other):
            digits, other, negative = other, digits, other_negative
        borrow, out = 0, []
        for i in range(len(digits)):
            total = ord(digits[-i-1]) - 48 - borrow - (ord(other[-i-1]) - 48 if i < len(other) else 0)
            borrow = total < 0
            out.append(chr(48 + total + (10 if borrow else 0)))
    result = ''.join(reversed(out)).lstrip('0') or '0'
    return ('-' if negative and result != '0' else '') + result


@dataclass(frozen=True)
class Number:
    negative: bool
    coefficient: str
    exponent: str
    lexeme: str

    @classmethod
    def parse(cls, text):
        mantissa, _, exponent = text.lower().partition('e')
        negative = mantissa.startswith('-')
        whole, _, fraction = mantissa.lstrip('-').partition('.')
        coefficient = (whole + fraction).lstrip('0')
        if not coefficient:
            return cls(False, '0', '0', text)
        trimmed = coefficient.rstrip('0')
        exponent = _signed_add_small(exponent or '0', len(coefficient) - len(trimmed) - len(fraction))
        return cls(negative, trimmed, exponent, text)

    @property
    def value(self):
        return self.negative, self.coefficient, self.exponent


def load_json(raw, *, exact=True):
    """Strict UTF-8 JSON. Parser exhaustion is distinct from invalid syntax.

    Bound bytes before calling. CPython's finite recursion guard is deliberately
    not a new answer-depth rubric. Hitting it yields evaluator_error.
    """
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise JsonSyntaxError('duplicate decoded object key')
            result[key] = value
        return result

    def constant(_):
        raise JsonSyntaxError('nonfinite JSON constant')

    def metadata_float(text):
        value = float(text)
        if not math.isfinite(value):
            raise JsonSyntaxError('nonfinite metadata number')
        return value

    try:
        text = raw.decode('utf-8') if isinstance(raw, bytes) else raw
        return json.loads(text, object_pairs_hook=pairs, parse_int=Number.parse if exact else int,
                          parse_float=Number.parse if exact else metadata_float, parse_constant=constant)
    except (RecursionError, MemoryError) as exc:
        raise EvaluatorResourceError(type(exc).__name__) from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise JsonSyntaxError(type(exc).__name__) from exc
    except ValueError as exc:
        if isinstance(exc, JsonSyntaxError):
            raise
        # Integer conversion ceilings in metadata are evaluator limits, not gold.
        raise EvaluatorResourceError(type(exc).__name__) from exc


def same(left, right):
    pending = [(left, right)]
    while pending:
        a, b = pending.pop()
        if type(a) is not type(b):
            return False
        if isinstance(a, Number):
            if a.value != b.value:
                return False
        elif isinstance(a, dict):
            if a.keys() != b.keys():
                return False
            pending.extend((a[key], b[key]) for key in a)
        elif isinstance(a, list):
            if len(a) != len(b):
                return False
            pending.extend(zip(a, b))
        elif a != b:
            return False
    return True


def exact_text(text):
    text = text.replace('\r\n', '\n')
    return text[:-1] if text.endswith('\n') else text


def score_content(mode, expected, content):
    """Pure finite data comparison; callers own evidence/literal-field admission."""
    if content is None or content == '':
        return False, 'NO_API_VISIBLE_ANSWER'
    if not isinstance(content, str):
        raise EvidenceError('NON_STRING_FINAL_CONTENT')
    if mode == 'exact-text':
        return (True, None) if exact_text(content) == exact_text(expected) else (False, 'EXACT_TEXT_MISMATCH')
    require(mode == 'strict-json-equality', 'data-only mode required')
    try:
        answer = load_json(content)
    except JsonSyntaxError as exc:
        return False, 'ANSWER_JSON_INVALID: ' + str(exc)
    return (True, None) if same(expected, answer) else (False, 'JSON_VALUE_OR_KIND_MISMATCH')


def _read(path, bound):
    """No symlinks, special files, unbounded read or path supplied by a response."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise EvidenceError('cannot open evidence file: ' + Path(path).name) from exc
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode), 'regular evidence file required')
        if info.st_size > bound:
            raise EvaluatorResourceError('file exceeds approved input bound: ' + Path(path).name)
        raw = stream.read(bound + 1)
        if len(raw) > bound:
            raise EvaluatorResourceError('file grew beyond approved input bound')
        return raw


def _artifact(directory, name, bound):
    raw = _read(directory / name, bound)
    return raw, {'path': name, 'sha256': digest(raw), 'bytes': len(raw)}


def load_material(directory):
    """Read only pinned data. Never import/run reference or verification files."""
    data = {}
    for name, (size, pin) in MATERIAL_FILES.items():
        raw = _read(Path(directory) / name, size)
        require(len(raw) == size and digest(raw) == pin, 'material hash differs: ' + name)
        data[name] = raw
    cases = load_json(data['cases-with-gold.json'])
    require(len(cases) == 60, 'fixed sixty cases required')
    require(Counter(c['gold']['mode'] for c in cases) == {
        'strict-json-equality': 36, 'exact-text': 8, 'python-unit-assertions': 8, 'blind-rubric': 8}, 'fixed mode inventory differs')
    return cases


def _int(value):
    if isinstance(value, Number) and re.fullmatch(r'-?(?:0|[1-9][0-9]*)', value.lexeme):
        # Operational integer fields never need unbounded conversion.
        if len(value.lexeme) <= 20:
            return int(value.lexeme)
    return None


def _ids(value, *, empty=False):
    return isinstance(value, list) and (empty or bool(value)) and all(_int(x) is not None and _int(x) >= 0 for x in value)


def _project_numbers(value):
    """Metadata only, for identity checking against capture's integer schema."""
    return load_json(canonical(value))


def _slot_base(case, index):
    return {'index': index, 'case_id': case['id'], 'mode': case['gold']['mode'],
            'case_record_sha256': case['case_record_sha256'], 'prompt_sha256': case['prompt_utf8_sha256'],
            'score_target': dict(SCORE_TARGET),
            'evidence_status': 'incomplete', 'evidence_reasons': [], 'comparison_eligible': False,
            'comparison_reasons': ['execution/runtime/state/decode comparability not established by this scorer'],
            'evaluation_status': 'not_evaluated', 'correctness': None, 'constraint_violation': None,
            'final_content_kind': None, 'empty_final_answer': None, 'truncated': None,
            'refusal_annotation': {'status': 'pending_contextual_review', 'label': None},
            'blind_reviews': [], 'candidate_execution_status': 'NOT_EVALUATED',
            'benign_overrefusal_probe': 'benign_overrefusal_probe' in case.get('cross_labels', [])}


def _verify_request(record, case, selection):
    request = record.get('request')
    require(isinstance(request, dict), 'missing request')
    payload = request.get('payload')
    require(isinstance(payload, dict), 'missing payload')
    salt = request.get('cache_salt')
    require(isinstance(salt, str) and salt, 'missing cache salt')
    expected = {'model': MODEL, 'messages': [{'role': 'user', 'content': case['prompt_text']}],
                'max_tokens': 2048, 'temperature': 0, 'seed': 2048, 'return_token_ids': True, 'cache_salt': salt}
    if selection['steering'] not in ('absent', 'unloaded'):
        expected.update(steering_profile='heretic-1017-attn-v1', steering_scale=int(selection['steering'][-1]))
    require(canonical(payload) == canonical(expected), 'exact prompt/decode request differs')
    require(isinstance(request.get('endpoint'), str) and re.fullmatch(r'http://127\.0\.0\.1:[0-9]+/v1/chat/completions', request['endpoint']), 'unexpected request endpoint')
    try:
        wire = base64.b64decode(request['wire_base64'], validate=True)
    except (ValueError, KeyError) as exc:
        raise EvidenceError('invalid request wire encoding') from exc
    require(digest(wire) == request.get('wire_sha256'), 'request wire hash differs')
    require(same(load_json(wire), _project_numbers(expected)), 'request wire/payload differ')
    semantic = {k: v for k, v in payload.items() if k != 'cache_salt'}
    require(digest(canonical(semantic)) == request.get('semantic_sha256'), 'request semantic hash differs')
    return salt


def _envelope(raw, record):
    obj = load_json(raw)
    require(isinstance(obj, dict) and obj.get('model') == MODEL and not obj.get('error'), 'invalid model/error envelope')
    choices = obj.get('choices')
    require(isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict) and _int(choices[0].get('index')) == 0,
            'expected single choice index zero')
    choice = choices[0]
    expected_ids = record.get('expected_input_ids')
    require(isinstance(expected_ids, list) and expected_ids and all(type(x) is int and x >= 0 for x in expected_ids), 'invalid expected input IDs')
    require(type(record.get('expected_input_count')) is int and record['expected_input_count'] == len(expected_ids)
            and record.get('expected_input_ids_sha256') == digest(canonical(expected_ids)), 'input vector hash/count differs')
    require(_ids(obj.get('prompt_token_ids')) and same(obj['prompt_token_ids'], _project_numbers(expected_ids)), 'root prompt token IDs differ')
    require(_ids(choice.get('token_ids'), empty=True), 'missing output token IDs')
    output_count = len(choice['token_ids'])
    require(output_count <= 2048, 'generated token count exceeds frozen budget')
    usage = obj.get('usage')
    require(isinstance(usage, dict) and _int(usage.get('prompt_tokens')) == len(expected_ids)
            and _int(usage.get('completion_tokens')) == output_count
            and _int(usage.get('total_tokens')) == len(expected_ids) + output_count, 'usage/token evidence differs')
    message = choice.get('message')
    require(isinstance(message, dict) and message.get('role') == 'assistant', 'missing assistant message')
    finish = choice.get('finish_reason')
    require(isinstance(finish, str) and finish, 'missing finish reason')
    mirror = record['response'].get('decoded')
    require(canonical(load_json(raw, exact=False)) == canonical(mirror), 'raw/decoded mirror differs')
    require(isinstance(mirror, dict) and isinstance(mirror.get('choices'), list) and len(mirror['choices']) == 1,
            'missing decoded mirror')
    mirror_message = mirror['choices'][0].get('message')
    require(isinstance(mirror_message, dict) and ('content' in message) == ('content' in mirror_message)
            and same(message.get('content'), _project_numbers(mirror_message.get('content'))), 'raw/mirror final content differs')
    return obj, choice, message, finish, output_count


def _verify_headers(response, raw_bytes, header_bound):
    headers = response.get('headers')
    require(isinstance(headers, list) and all(isinstance(pair, list) and len(pair) == 2
            and all(isinstance(value, str) for value in pair) and pair[0] for pair in headers),
            'invalid retained response headers')
    require(response.get('headers_overflow') is False, 'capture header overflow/missing status')
    if header_bound is not None:
        require(len(canonical(headers)) <= header_bound, 'retained headers exceed capsule header bound')
    lengths = [value for key, value in headers if key.lower() == 'content-length']
    if lengths:
        require(len(lengths) == 1 and re.fullmatch('[0-9]+', lengths[0]) is not None
                and (lengths[0].lstrip('0') or '0') == str(raw_bytes),
                'response Content-Length differs from retained bytes')


def _score_slot(directory, case, index, record, original, selection, bound, header_bound=None):
    result = _slot_base(case, index)
    result['capture_outcome'] = record.get('outcome')
    try:
        require(type(record.get('index')) is int and record['index'] == index and record.get('case_id') == case['id'], 'slot identity differs')
        require(record.get('prompt_sha256') == case['prompt_utf8_sha256'], 'prompt hash differs')
        require(record.get('semantic_scoring') == 'NOT_EVALUATED', 'capture semantic status was mutated')
        raw_record, reference = _artifact(directory, f'record-{index:03d}.json', bound * 8)
        result['capture_record'] = reference
        require(same(load_json(raw_record), _project_numbers(record)), 'summary/persisted record differ')
        # The initial immutable case index binds the complete input vector too.
        for key in ('index', 'case_id', 'prompt_sha256', 'expected_input_ids', 'expected_input_count', 'expected_input_ids_sha256', 'category', 'language'):
            require(canonical(record.get(key)) == canonical(original.get(key)), 'initial case-index binding differs: ' + key)
        if record.get('outcome') == 'NOT_RUN':
            result['evidence_reasons'] = ['NOT_RUN']
            return result
        _verify_request(record, case, selection)
        request_raw, request_ref = _artifact(directory, f'request-{index:03d}.json', bound * 8)
        result['capture_request'] = request_ref
        request_copy = load_json(request_raw, exact=False)
        require(isinstance(request_copy, dict) and canonical(request_copy.get('request')) == canonical(record['request']), 'request sidecar differs')
        for key in ('index', 'case_id', 'prompt_sha256', 'expected_input_ids', 'expected_input_count', 'expected_input_ids_sha256'):
            require(canonical(request_copy.get(key)) == canonical(record.get(key)), 'request identity differs: ' + key)
        response = record.get('response')
        require(isinstance(response, dict), 'missing response')
        require(response.get('raw_path') == f'response-{index:03d}.bin', 'raw path differs from fixed primary slot')
        # Overflow capture may contain one extra byte; it is retained but never scored.
        raw, ref = _artifact(directory, response['raw_path'], bound + 1)
        result['raw_response'] = ref
        result['full_decoded_response_reference'] = {'path': response['raw_path'], 'format': 'strict UTF-8 JSON; raw is authoritative'}
        require(type(response.get('raw_bytes')) is int and len(raw) == response['raw_bytes']
                and digest(raw) == response.get('raw_sha256'), 'raw response hash/size differs')
        if response.get('complete') is not True or response.get('overflow') is not False:
            result['evidence_reasons'] = ['INCOMPLETE_OR_OVERFLOW_CAPTURE']
            return result
        require(len(raw) <= bound, 'complete response exceeds capture bound')
        require(type(response.get('http_status')) is int and 200 <= response['http_status'] < 300, 'non-success HTTP status')
        _verify_headers(response, len(raw), header_bound)
        require(response.get('parse_error') is None, 'capture parse error')
        require(record.get('outcome') in ('COMPLETED', 'TRUNCATED'), 'infrastructure/capture error')
        _, choice, message, finish, output_count = _envelope(raw, record)
        require(record['outcome'] == ('TRUNCATED' if finish == 'length' else 'COMPLETED'), 'finish/outcome mismatch')
        result.update(evidence_status='valid', finish_reason=finish,
                      stop_reason_reference={'path': response['raw_path'], 'json_pointer': '/choices/0/stop_reason'},
                      reported_output_tokens=output_count, reported_raw_bytes=len(raw),
                      cap_reached=output_count == 2048, truncated=finish == 'length',
                      unexpected_tool_call_finish=finish in ('tool_calls', 'function_call'),
                      reasoning_token_count=None, reasoning_token_count_status='NOT_INFERRED',
                      final_content_reference={'path': response['raw_path'], 'json_pointer': '/choices/0/message/content'})
        content = message.get('content')
        result['final_content_kind'] = 'missing' if 'content' not in message else 'null' if content is None else 'string' if isinstance(content, str) else 'unexpected'
        result['empty_final_answer'] = content is None or content == ''
        if content is not None and not isinstance(content, str):
            raise EvidenceError('NON_STRING_FINAL_CONTENT')
        # Predeclared observable API field, not reconstructed model/internal final.
        # No native parser claim, reasoning recovery, trimming or answer repair.
        if case['gold']['mode'] == 'python-unit-assertions':
            result['evaluation_blocker'] = 'Isolated candidate backend/driver remains UNBOUND.'
        elif case['gold']['mode'] == 'blind-rubric':
            result.update(evaluation_status='not_evaluated', review_status='pending_review',
                          evaluation_blocker='Two independent blinded reviews are not implemented by this data scorer.')
        else:
            result['correctness'], result['constraint_violation'] = score_content(case['gold']['mode'], case['gold']['expected'], content)
            result['evaluation_status'] = 'scored'
    except EvaluatorResourceError as exc:
        result.update(evaluation_status='evaluator_error', correctness=None)
        result['evidence_reasons'].append(str(exc))
    except (EvidenceError, JsonSyntaxError, KeyError, TypeError) as exc:
        result.update(evidence_status='invalid', evaluation_status='not_evaluated', correctness=None)
        result['evidence_reasons'].append(str(exc))
    return result


def evaluate(material_directory, capture_directory, *, max_response_bytes):
    """Evaluate one complete 60-slot primary capture; never grant comparison.

    max_response_bytes must come from the approved execution capsule, not from
    model output or an improvised answer-length rule. It must equal the capture.
    No default is supplied. Metadata ceilings derive from this same bound.
    """
    require(type(max_response_bytes) is int and max_response_bytes > 0, 'approved positive capture bound required')
    cases = load_material(material_directory)
    directory = Path(capture_directory)
    bound = max_response_bytes
    capture_raw, capture_ref = _artifact(directory, 'quality-capture.json', 60 * bound * 8)
    capture = load_json(capture_raw, exact=False)
    require(isinstance(capture, dict) and capture.get('schema') == 'r6x-quality-capture/1', 'capture schema differs')
    transport_bounds = capture.get('transport_bounds')
    require(isinstance(transport_bounds, dict) and type(transport_bounds.get('response_bytes')) is int
            and transport_bounds['response_bytes'] == bound, 'approved capture bound differs')
    header_bound = transport_bounds.get('header_bytes')
    require(type(header_bound) is int and header_bound > 0, 'positive capsule header bound required')
    identity_raw, identity_ref = _artifact(directory, 'identity.json', bound)
    identity = load_json(identity_raw, exact=False)
    require(same(load_json(identity_raw), _project_numbers(capture.get('identity'))), 'identity sidecar differs')
    require(identity.get('material_sha256') == MATERIAL_SHA256 and identity.get('prompts_sha256') == MATERIAL_FILES['prompts.jsonl'][1], 'material/prompt identity differs')
    for key in ('window_id', 'attempt_id', 'lane_id', 'plan_sha256'):
        require(isinstance(identity.get(key), str) and identity[key], 'missing primary identity: ' + key)
    require(identity.get('transport_bounds') == capture.get('transport_bounds'), 'identity transport bounds differ')
    selection = identity.get('selection', {})
    require(set(selection) == {'arm', 'steering'}
        and selection.get('arm') in ('r6x188', 'native197-off', 'r6x-unpatched', 'r6x-steer')
        and selection.get('steering') in (('absent',) if selection['arm'] == 'r6x-unpatched' else
            ('unloaded', 'loaded0', 'loaded1', 'loaded2', 'loaded3', 'loaded4')), 'unknown arm/state')
    copied_prompts = _read(directory / 'prompts.jsonl', MATERIAL_FILES['prompts.jsonl'][0])
    require(digest(copied_prompts) == MATERIAL_FILES['prompts.jsonl'][1], 'capture prompt file changed')
    index_raw, index_ref = _artifact(directory, 'case-index.json', 60 * bound)
    original = load_json(index_raw, exact=False)
    records = capture.get('records')
    inventory_valid = (isinstance(records, list) and len(records) == 60 and isinstance(original, list) and len(original) == 60
        and all(isinstance(r, dict) for r in original)
        and all(isinstance(r, dict) and type(r.get('index')) is int and r['index'] == i and r.get('case_id') == c['id']
                for i, (c, r) in enumerate(zip(cases, records))))
    context = identity.get('context', {})
    # The API-visible score target is fixed before any output is observed.
    # Native internal reasoning/final separation remains separately unproved.
    if inventory_valid:
        salts = [r['request'].get('cache_salt') for r in records if isinstance(r.get('request'), dict)]
        inventory_valid = (all(isinstance(salt, str) and bool(salt) for salt in salts)
                           and len(salts) == len(set(salts)))
        if inventory_valid:
            results = [_score_slot(directory, case, i, records[i], original[i], selection, bound,
                                   header_bound) for i, case in enumerate(cases)]
    if not inventory_valid:
        results = [_slot_base(case, i) | {'evidence_status': 'invalid', 'evidence_reasons': ['INVALID_PRIMARY_INVENTORY_OR_DUPLICATE_REQUEST_SALT']} for i, case in enumerate(cases)]
    counts = {'planned': 60, 'attempted': sum(r.get('capture_outcome') not in (None, 'NOT_RUN') for r in results),
              'completed': sum(r.get('capture_outcome') == 'COMPLETED' for r in results),
              'truncated': sum(r.get('capture_outcome') == 'TRUNCATED' for r in results),
              'not_run': sum(r.get('capture_outcome') == 'NOT_RUN' for r in results),
              'evidence_error': sum(r['evidence_status'] == 'invalid' for r in results),
              'incomplete': sum(r['evidence_status'] == 'incomplete' for r in results),
              'evaluated': sum(r['evaluation_status'] == 'scored' for r in results),
              'correct': sum(r['correctness'] is True for r in results),
              'incorrect': sum(r['correctness'] is False for r in results),
              'evaluator_error': sum(r['evaluation_status'] == 'evaluator_error' for r in results),
              'pending_blind': sum(r['mode'] == 'blind-rubric' for r in results),
              'pending_isolated_code': sum(r['mode'] == 'python-unit-assertions' for r in results),
              'comparison_eligible': 0, 'data_cases_planned': 44,
              'data_evidence_valid': sum(r['mode'] in DATA_MODES and r['evidence_status'] == 'valid' for r in results)}
    observed_capture_counts = dict(Counter(r.get('outcome', 'MISSING') for r in records if isinstance(r, dict))) if isinstance(records, list) else {}
    if not inventory_valid:
        for key in ('attempted', 'completed', 'truncated', 'not_run'):
            counts[key] = None  # No trusted primary denominator; retain observations separately.
    return {'schema': SCHEMA, 'overall_quality': 'NOT_EVALUATED', 'comparison_status': 'NOT_ESTABLISHED', 'applied_scale_status': 'UNBOUND',
            'coverage': '44 data-only cases supported of 60; code and blinded review explicitly not evaluated',
            'capture': capture_ref, 'capture_directory': str(directory.resolve()), 'identity': identity,
            'identity_file': identity_ref, 'case_index_file': index_ref, 'inventory_valid': inventory_valid,
            'observed_capture_outcome_counts': observed_capture_counts, 'material_sha256': MATERIAL_SHA256, 'material_files': {k: v[1] for k, v in MATERIAL_FILES.items()},
            'synthetic_fixture_sha256': FIXTURE_SHA256, 'base_evaluator_contract_sha256': CONTRACT_SHA256,
            'interpretation_amendment': {'version': 'literal-api-content/1', 'sha256': API_CONTENT_CONTRACT_SHA256,
                                         'document': 'docs/r6x-quality-api-content-contract.md'}, 'evaluator_source_sha256': digest(Path(__file__).read_bytes()),
            'bound_response_bytes': bound, 'score_target': dict(SCORE_TARGET),
            'native_internal_final_extraction': {'status': 'UNBOUND', 'scope': 'native_internal_reasoning_final_split',
                                        'reported_context': context.get('final_answer_extraction')}, 'counts': counts,
            'content_accuracy': {'numerator': counts['correct'], 'denominator': counts['evaluated'], 'denominator_kind': 'literal_api_field_scored_data_cases_not_A_B_eligible'},
            'planned_pilot_slots': 420 if selection['arm'] in ('r6x-unpatched', 'r6x-steer') else 720,
            'planned_questions': 60, 'this_capture_planned_slots': 60,
            'field_unproved': ['actual runtime/source/OCI/model identity', 'actual tokenizer/template/default parser bindings',
                               'state/applied-scale evidence', 'lifecycle/deadline/drain validity', 'paired comparability',
                               'isolated code execution and driver integrity', 'independent blinded reviews'],
            'records': results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--materials', required=True, type=Path)
    parser.add_argument('--capture', required=True, type=Path)
    parser.add_argument('--max-response-bytes', required=True, type=int, help='exact pre-approved capsule transport bound; no default')
    parser.add_argument('--output', required=True, type=Path, help='new report outside immutable capture directory')
    args = parser.parse_args(argv)
    try:
        require(not args.output.resolve().is_relative_to(args.capture.resolve())
                and not args.output.resolve().is_relative_to(args.materials.resolve()), 'report must be outside immutable inputs')
        report = evaluate(args.materials, args.capture, max_response_bytes=args.max_response_bytes)
        # This offline entry is not the owned client's restrictive-umask child.
        # Create the new report privately; never repair or reopen old evidence.
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(canonical(report) + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        return 0  # Report written, never model or overall quality PASS.
    except (EvidenceError, JsonSyntaxError, EvaluatorResourceError, OSError) as exc:
        parser.exit(2, type(exc).__name__ + ': ' + str(exc) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
