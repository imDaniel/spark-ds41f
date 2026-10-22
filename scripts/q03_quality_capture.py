"""Lossless sidecar for the unchanged PR8 quality generation loop, not a runner.

No CLI, lifecycle admission, scoring, tokenization, retry, service or candidate-code
execution is provided. The owned client must supply a reviewed deadline and pins.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import http.client
import json
import math
import os
import re
from pathlib import Path
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

MODEL = 'deepseek-v4.1-flash'
PROFILE = 'heretic-1017-attn-v1'
MATERIAL_SHA256 = 'c3db307e1f145026c33252c36cda819ffefda1d189400d6e06d316454f3bf580'
PROMPTS_SHA256 = '8cdcf6143d80d4776c052166a9764d36d8b5020038ad24118e352f3493cf4ca0'
TOOLBOX_SHA256 = '44f8494c4ddcf45103c6dda5148448cc33cbb0a1c9eb5188d6227cacca36207a'
SCHEMA = 'r6x-quality-capture/1'


class CaptureError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise CaptureError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate JSON key: ' + key)
            result[key] = value
        return result

    def number(text):
        value = float(text)
        require(math.isfinite(value), 'nonfinite JSON number')
        return value

    def constant(text):
        raise CaptureError('nonfinite JSON constant: ' + text)

    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                      parse_float=number, parse_constant=constant)


def exclusive(path, raw):
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def ids(value, *, empty=False):
    return isinstance(value, list) and (empty or bool(value)) and all(type(x) is int and x >= 0 for x in value)


def extra_body(state):
    require(state in ('unloaded', 'loaded0', 'loaded1', 'loaded2', 'loaded3', 'loaded4'), 'unknown state')
    extra = {'temperature': 0, 'seed': 2048}
    if state != 'unloaded':
        extra.update(steering_profile=PROFILE, steering_scale=int(state[-1]))
    return extra


def idle_gauges(text):
    result = []
    for name in ('vllm:num_requests_running', 'vllm:num_requests_waiting'):
        values = []
        for line in text.splitlines():
            if re.match(re.escape(name) + r'(?:\{|\s)', line):
                field = line.rsplit('}', 1)[-1] if '{' in line else line[len(name):]
                value = float(field.strip().split()[0])
                require(math.isfinite(value) and value >= 0, 'quality idle gauge invalid')
                values.append(value)
        require(values, 'positive native idle gauge evidence required')
        result.append(sum(values))
    require(all(math.isfinite(n) for n in result), 'quality idle gauge sum invalid')
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Capture:
    """Serial _post seam. Every invocation has one immutable primary slot.

    `cases` is generation-only metadata, not gold. Raw bytes are stored before
    parsing; overflow preserves the received prefix and is explicitly incomplete.
    This admission fence is not the supervisor's hard deadline or drain proof.
    """
    def __init__(self, directory, base_url, prompts, cases, state, *, deadline,
                 max_response_bytes, max_header_bytes, open_response=None, clock=time.monotonic,
                 request_seconds=None):
        endpoint = urllib.parse.urlsplit(base_url)
        require(endpoint.scheme == 'http' and endpoint.hostname == '127.0.0.1'
                and endpoint.port is not None and endpoint.netloc == f'127.0.0.1:{endpoint.port}'
                and not endpoint.path and not endpoint.query and not endpoint.fragment,
                'exact owned loopback origin required')
        require(type(deadline) in (int, float) and math.isfinite(deadline), 'finite owned deadline required')
        for bound in (max_response_bytes, max_header_bytes):
            require(type(bound) is int and bound > 0, 'capsule transport bounds required')
        require(len(prompts) == len(cases) and bool(cases), 'complete ordered case index required')
        seen = set()
        for prompt, case in zip(prompts, cases):
            require(isinstance(prompt, dict) and set(prompt) == {'id', 'text'}
                    and isinstance(prompt['id'], str) and prompt['id'] and isinstance(prompt['text'], str),
                    'generation input must contain only id/text')
            require(prompt['id'] not in seen, 'duplicate case ID')
            seen.add(prompt['id'])
            require(set(case) == {'id', 'category', 'language', 'prompt_sha256', 'input_ids'}
                    and case['id'] == prompt['id'] and case['prompt_sha256'] == digest(prompt['text'].encode())
                    and ids(case['input_ids']) and isinstance(case['category'], str)
                    and case['language'] in ('zh', 'en'), 'case identity or frozen token evidence differs')
        self.directory, self.url = Path(directory), base_url
        self.prompts, self.cases = copy.deepcopy(prompts), copy.deepcopy(cases)
        self.extra, self.deadline, self.clock = extra_body(state), deadline, clock
        self.max_response_bytes, self.max_header_bytes = max_response_bytes, max_header_bytes
        require(request_seconds is None or (type(request_seconds) is int and request_seconds > 0),
                'positive capsule request bound required')
        self.request_seconds, self.request_deadline = request_seconds, None
        self.metrics_records, self.preflight_records = [], []
        self.open_response = open_response or urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect()).open
        self.lock, self.next_index, self.failure, self.salts = threading.Lock(), 0, None, set()
        self.records = [{'index': i, 'case_id': case['id'], 'category': case['category'],
                         'language': case['language'], 'prompt_sha256': case['prompt_sha256'],
                         'expected_input_ids': case['input_ids'], 'expected_input_count': len(case['input_ids']),
                         'expected_input_ids_sha256': digest(canonical(case['input_ids'])),
                         'outcome': 'NOT_RUN', 'semantic_scoring': 'NOT_EVALUATED',
                         'request': None, 'response': None, 'diagnostics': {}, 'error': None}
                        for i, case in enumerate(self.cases)]
        exclusive(self.directory / 'case-index.json', canonical(self.records))

    def fail(self, error):
        if self.failure is None:
            self.failure = str(error)

    def remaining(self):
        remaining = min(self.deadline, self.request_deadline or self.deadline) - self.clock()
        require(remaining > 0, 'owned client deadline exhausted')
        return remaining

    def _read(self, response, record, *, raw_name=None, parse=True):
        raw_path = self.directory / (raw_name or f"response-{record['index']:03d}.bin")
        result = record['response'] = {'http_status': response.status, 'headers': [],
            'raw_path': raw_path.name, 'raw_bytes': 0, 'raw_sha256': None, 'complete': False,
            'overflow': False, 'decoded': None, 'parse_error': None}
        # Preserve duplicate relevant headers, without silently clipping values.
        names = {'content-type', 'content-length', 'content-encoding', 'transfer-encoding',
                 'x-request-id', 'request-id', 'date', 'server'}
        result['headers'] = [[k, v] for k, v in response.headers.items() if k.lower() in names]
        header_raw = canonical(result['headers'])
        header_overflow = len(header_raw) > self.max_header_bytes
        result['headers_overflow'] = header_overflow
        if header_overflow:
            result['headers'] = []
            result['headers_overflow_evidence'] = {
                'canonical_prefix_base64': base64.b64encode(header_raw[:self.max_header_bytes]).decode(),
                'canonical_observed_bytes': len(header_raw), 'canonical_sha256': digest(header_raw)}
        collected = bytearray()
        try:
            with raw_path.open('xb') as output:
                try:
                    while True:
                        remaining = self.remaining()
                        # stdlib HTTPResponse.read1 makes at most one buffered/socket
                        # read. Refresh its socket timeout for the current absolute
                        # request deadline rather than granting a fresh full timeout.
                        socket = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
                        if socket is not None:
                            socket.settimeout(remaining)
                        size = min(65536, self.max_response_bytes + 1 - len(collected))
                        try:
                            chunk = getattr(response, 'read1', response.read)(size)
                        except http.client.IncompleteRead as exc:
                            chunk = exc.partial[:size]
                            result['overflow'] = len(exc.partial) > size or len(collected) + len(chunk) > self.max_response_bytes
                            output.write(chunk); collected.extend(chunk)
                            raise
                        require(isinstance(chunk, bytes), 'transport returned non-bytes')
                        if not chunk:
                            result['complete'] = True
                            break
                        output.write(chunk); collected.extend(chunk)
                        if len(collected) > self.max_response_bytes:
                            result['overflow'] = True
                            raise CaptureError('response transport bound exceeded')
                    self.remaining()
                finally:
                    output.flush(); os.fsync(output.fileno())
        finally:
            result['raw_bytes'], result['raw_sha256'] = len(collected), digest(collected)
        raw = bytes(collected)
        # Content-Length checking catches fake/early EOF implementations too.
        lengths = [v for k, v in result['headers'] if k.lower() == 'content-length']
        if lengths and not (len(lengths) == 1 and lengths[0].isdigit() and int(lengths[0]) == len(raw)):
            result['complete'] = False
            raise CaptureError('response Content-Length differs from retained bytes')
        if not parse:
            require(not header_overflow, 'relevant response headers exceed capsule bound')
            require(type(response.status) is int and 200 <= response.status < 300, 'non-success HTTP status')
            return raw.decode('utf-8')
        try:
            result['decoded'] = strict_json(raw)
        except Exception as exc:
            result['parse_error'] = f'{type(exc).__name__}: {exc}'
            raise
        require(not header_overflow, 'relevant response headers exceed capsule bound')
        require(type(response.status) is int and 200 <= response.status < 300, 'non-success HTTP status')
        return result['decoded']

    def _validate_response(self, answer, record):
        require(isinstance(answer, dict), 'HTTP JSON must be an object')
        require(not answer.get('error'), 'native error object')
        require(answer.get('model') == MODEL, 'response model identity differs')
        choices = answer.get('choices')
        require(isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict)
                and type(choices[0].get('index')) is int and choices[0]['index'] == 0, 'expected single choice index 0')
        choice = choices[0]
        diagnostic = record['diagnostics']
        diagnostic['input_ids_equal'] = ids(answer.get('prompt_token_ids')) and answer['prompt_token_ids'] == record['expected_input_ids']
        diagnostic['output_ids_present'] = ids(choice.get('token_ids'), empty=True)
        require(diagnostic['input_ids_equal'], 'missing or mismatched root prompt_token_ids')
        require(diagnostic['output_ids_present'], 'missing choice token_ids')
        usage = answer.get('usage')
        diagnostic['usage_consistent'] = (isinstance(usage, dict)
            and type(usage.get('prompt_tokens')) is int and usage['prompt_tokens'] == len(answer['prompt_token_ids'])
            and type(usage.get('completion_tokens')) is int and usage['completion_tokens'] == len(choice['token_ids'])
            and type(usage.get('total_tokens')) is int and usage['total_tokens'] == usage['prompt_tokens'] + usage['completion_tokens'])
        require(diagnostic['usage_consistent'], 'missing or inconsistent full usage/token evidence')
        require(len(choice['token_ids']) <= 2048, 'output exceeds frozen generated-token budget')
        message = choice.get('message')
        require(isinstance(message, dict) and message.get('role') == 'assistant', 'missing assistant message')
        # Keep presence/null/type, text, reasoning and tools in the full decoded object.
        # Never strip, extract, stringify, concatenate, run or semantically score them.
        diagnostic['final_content_present'] = 'content' in message
        diagnostic['final_content_type'] = type(message.get('content')).__name__ if 'content' in message else 'MISSING'
        diagnostic['empty_final_answer'] = message.get('content') in ('', None)
        finish = choice.get('finish_reason')
        require(isinstance(finish, str) and bool(finish), 'missing terminal finish reason')
        diagnostic['truncated'] = finish == 'length'
        diagnostic['unexpected_tool_call_finish'] = finish in ('tool_calls', 'function_call')
        record['outcome'] = 'TRUNCATED' if diagnostic['truncated'] else 'COMPLETED'

    def __call__(self, url, body, timeout):
        with self.lock:
            require(self.next_index < len(self.records), 'generation exceeded finite case inventory')
            index = self.next_index
            self.next_index += 1
            record = self.records[index]
            if self.failure is not None:
                record['error'] = 'admission stopped: ' + self.failure
                raise CaptureError(record['error'])
            record['started_utc'], record['started_monotonic'] = time.time(), self.clock()
            try:
                self.request_deadline = (self.clock() + self.request_seconds) if self.request_seconds else None
                remaining = self.remaining()
                require(url == self.url + '/v1/chat/completions', 'generation endpoint differs')
                require(isinstance(body, dict), 'generation payload must be an object')
                salt = body.get('cache_salt')
                require(isinstance(salt, str) and salt and salt not in self.salts, 'nonempty unique cache salt required')
                expected = {'model': MODEL, 'messages': [{'role': 'user', 'content': self.prompts[index]['text']}],
                            'max_tokens': 2048, 'temperature': 0, 'return_token_ids': True, 'cache_salt': salt} | self.extra
                require(canonical(body) == canonical(expected), 'fixed PR8 semantic request differs')
                require(type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0, 'finite request timeout required')
                self.salts.add(salt)
                # Exactly PR8's json.dumps representation; only cache_salt is omitted
                # from the semantic-body hash. Decode/template pins live in lane context.
                wire = json.dumps(body).encode()
                semantic = {k: v for k, v in body.items() if k != 'cache_salt'}
                record['request'] = {'endpoint': url, 'payload': copy.deepcopy(body),
                    'wire_base64': base64.b64encode(wire).decode(), 'wire_sha256': digest(wire),
                    'semantic_sha256': digest(canonical(semantic)), 'cache_salt': salt,
                    'timeout_seconds': min(timeout, remaining)}
                exclusive(self.directory / f'request-{index:03d}.json', canonical(record))
                request = urllib.request.Request(url, data=wire, headers={'Content-Type': 'application/json'}, method='POST')
                record['diagnostics']['transport'] = 'OPENING'
                try:
                    response = self.open_response(request, timeout=min(timeout, self.remaining()))
                except urllib.error.HTTPError as exc:
                    response = exc  # Non-2xx bodies are evidence, not discarded exceptions.
                with response:
                    answer = self._read(response, record)
                record['diagnostics']['transport'] = 'COMPLETE'
                self._validate_response(answer, record)
                return answer
            except BaseException as exc:
                record['outcome'] = 'ERROR'
                record['error'] = f'{type(exc).__name__}: {exc}'
                record['diagnostics']['transport'] = 'ERROR' if not record.get('response') or not record['response']['complete'] else 'COMPLETE'
                self.fail(record['error'])
                raise
            finally:
                self.request_deadline = None
                record['ended_utc'], record['ended_monotonic'] = time.time(), self.clock()
                try:
                    exclusive(self.directory / f'record-{index:03d}.json', canonical(record))
                except BaseException as exc:
                    record['outcome'] = 'ERROR'
                    record['error'] = f'evidence persistence failed: {type(exc).__name__}: {exc}'
                    self.fail(record['error'])
                    raise

    def metrics(self, url, timeout=10):
        if self.failure is not None:
            return None  # Never reopen a failed lane, including trailing scrapes.
        record = {'index': len(self.metrics_records), 'endpoint': url + '/metrics', 'response': None}
        self.metrics_records.append(record)
        try:
            require(url == self.url and len(self.metrics_records) <= 5, 'fixed PR8 metrics closure differs')
            self.request_deadline = self.clock() + min(timeout, self.request_seconds or timeout)
            request = urllib.request.Request(record['endpoint'], method='GET')
            try:
                response = self.open_response(request, timeout=self.remaining())
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                text = self._read(response, record, raw_name=f"metrics-{record['index']:03d}.bin", parse=False)
            gauges = idle_gauges(text)
            if record['index'] == 0:
                require(gauges == [0, 0], 'quality service not idle')
            return text
        except BaseException as exc:
            record['error'] = f'{type(exc).__name__}: {exc}'
            self.fail(record['error'])
            raise
        finally:
            self.request_deadline = None
            try:
                exclusive(self.directory / f"metrics-{record['index']:03d}.json", canonical(record))
            except BaseException as exc:
                self.fail(exc)
                raise

    def finish(self):
        for record in self.records:
            if record['outcome'] == 'NOT_RUN':
                record['error'] = record['error'] or self.failure or 'generation did not admit this slot'
                path = self.directory / f"record-{record['index']:03d}.json"
                if not path.exists():
                    exclusive(path, canonical(record))
        return self.records


def run_client(plan, main, *, deadline, open_response=None, clock=time.monotonic, request_seconds=None, preflight=None):
    """Owned schema-7 quality hook; never dispatched by existing schema-6 P1.

    `main` must be the existing source-only PR8 entry point. Fake transport injection
    is for CPU tests; no source, model, lifetime or evidence approval is implied.
    """
    from q03_http_client import FrozenToolbox
    from servebench import behaviour, prom
    from servebench import __main__ as cli

    inventory_raw = Path(__file__).with_name('q03_http_toolbox.json').read_bytes()
    require(digest(inventory_raw) == TOOLBOX_SHA256, 'PR8 toolbox inventory changed')
    inventory = strict_json(inventory_raw)['files']
    loader = cli.__loader__
    require(main is cli.main and isinstance(loader, FrozenToolbox)
            and set(loader.sources) == set(inventory)
            and all(digest(loader.sources[name]) == pin for name, pin in inventory.items())
            and behaviour.__loader__ is loader and prom.__loader__ is loader,
            'existing source-only exact PR8 loader required')
    config = plan['client']['quality_capture']
    require(set(config) == {'material_sha256', 'prompts_path', 'prompts_sha256', 'cases',
            'context', 'max_response_bytes', 'max_header_bytes'}, 'fixed capture binding required')
    require(config['material_sha256'] == MATERIAL_SHA256 and config['prompts_sha256'] == PROMPTS_SHA256,
            'reviewed pilot material differs')
    prompt_raw = Path(config['prompts_path']).read_bytes()
    require(digest(prompt_raw) == PROMPTS_SHA256, 'exact generation-only prompt bytes required')
    prompts = [strict_json(line) for line in prompt_raw.splitlines() if line.strip()]
    require(len(prompts) == 60, 'all sixty planned cases required; segmentation is not implemented')
    require(plan['selection']['arm'] in ('r6x188', 'native197-off'), 'unknown arm')
    context = copy.deepcopy(config['context'])
    require({'runtime_receipt_sha256', 'tokenizer_identity_sha256', 'decode_identity_sha256',
            'observed_loaded_state', 'applied_state_evidence'} <= set(context)
            <= {'runtime_receipt_sha256', 'tokenizer_identity_sha256', 'decode_identity_sha256',
                'observed_loaded_state', 'applied_state_evidence', 'final_answer_extraction'},
            'runtime/tokenizer/decode/state references required')
    for key in ('runtime_receipt_sha256', 'tokenizer_identity_sha256', 'decode_identity_sha256'):
        require(isinstance(context[key], str) and len(context[key]) == 64
                and all(x in '0123456789abcdef' for x in context[key]), 'missing context hash: ' + key)
    require(isinstance(context['observed_loaded_state'], dict) and isinstance(context['applied_state_evidence'], dict),
            'observed and applied state evidence must remain distinct')
    output = Path(plan['client']['output'])
    require(not output.exists(), 'ordinary PR8 output already exists')
    directory = output.with_name('quality-capture')
    directory.mkdir()  # Exclusive directory per attempt; never overwrite or resume.
    capture = Capture(directory, plan['url'], prompts, config['cases'], plan['selection']['steering'],
                      deadline=deadline, max_response_bytes=config['max_response_bytes'],
                      max_header_bytes=config['max_header_bytes'], open_response=open_response, clock=clock,
                      request_seconds=request_seconds)
    copied_prompts = directory / 'prompts.jsonl'
    exclusive(copied_prompts, prompt_raw)
    identity = {'window_id': plan['window_id'], 'attempt_id': plan['attempt_id'], 'lane_id': plan['lane_id'],
                'plan_sha256': plan['plan_sha256'], 'selection': copy.deepcopy(plan['selection']),
                'material_sha256': MATERIAL_SHA256, 'prompts_sha256': PROMPTS_SHA256, 'context': context,
                'transport_bounds': {'response_bytes': config['max_response_bytes'],
                    'header_bytes': config['max_header_bytes'], 'request_seconds': request_seconds}}
    exclusive(directory / 'identity.json', canonical(identity))
    original_post, original_fetch = behaviour._post, prom.fetch

    argv = ['model', plan['url'], '--model', MODEL, '--prompts', str(copied_prompts),
            '--concurrency', '1', '--max-tokens', '2048', '--no-score',
            '--extra-body', canonical(extra_body(plan['selection']['steering'])).decode(), '--output', str(output)]
    behaviour._post, prom.fetch = capture, capture.metrics
    code = 1
    try:
        if preflight is not None:
            preflight(capture)
        code = main(argv)
        require(type(code) is int and code in (0, 1), 'unexpected PR8 exit code')
        require(capture.next_index == 60, 'PR8 did not enumerate all sixty slots')
    except BaseException as exc:
        capture.fail(f'{type(exc).__name__}: {exc}')
        code = 1
        if not isinstance(exc, Exception):
            raise
    finally:
        # The fixed PR8 executor has joined all queued work before returning.
        # The owned supervisor still must abort/drain the real service on error.
        behaviour._post, prom.fetch = original_post, original_fetch
        records = capture.finish()
        summary = {'schema': SCHEMA, 'identity': identity, 'records': records,
            'live_tokenization': capture.preflight_records, 'metrics': capture.metrics_records, 'capture_error': capture.failure, 'exit_code': 1 if capture.failure else code,
            'transport_bounds': copy.deepcopy(identity['transport_bounds']),
            'counts': {kind: sum(r['outcome'] == kind for r in records) for kind in ('COMPLETED', 'TRUNCATED', 'ERROR', 'NOT_RUN')},
            'execution_evidence': 'CAPTURE_ONLY_NOT_EXECUTION_VALIDATED', 'semantic_scoring': 'NOT_EVALUATED',
            'ordinary_result_sha256': digest(output.read_bytes()) if output.is_file() else None}
        exclusive(directory / 'quality-capture.json', canonical(summary))
    return summary['exit_code']
