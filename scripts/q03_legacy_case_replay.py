#!/usr/bin/env python3
"""Two finite original warm4K cases, using the unchanged PR8 servebench loop.

No service launcher, generic argv, model import, quality judge, or historical
PASS claim lives here. The owned HTTP controller supplies identity, admission,
supervision and four-rank drain. A complete client receipt is not a drained run.
"""
from __future__ import annotations
import base64
import copy
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import re
import signal
import sys
import threading
import time
import urllib.request
import uuid

import q03_owner_model as owner
from q03_held_window import exclusive_json, read_owned_bytes

require, canonical, digest, json_bytes = owner.require, owner.canonical, owner.digest, owner.json_bytes
WORKLOAD = 'legacy_warm4k_v1'
MODEL = 'deepseek-v4.1-flash'
PROFILE = 'heretic-1017-attn-v1'
CASES = {'warm4k_c1': 1, 'warm4k_c4': 4}
ARMS = ('r6x188', 'native197-off')
STATES = ('unloaded', 'loaded0', 'loaded1', 'loaded2', 'loaded3', 'loaded4')
PROMPTS_SHA256 = 'ce2d8ed479d6cbf2f01e01de28faae01e311495472b2260b7c8bbf3c901a0961'
TOOLBOX_HEAD = 'c6342a1b6c5e5a98108905a71452a51d6d3a4a87'
TOOLBOX_INVENTORY_SHA256 = '44f8494c4ddcf45103c6dda5148448cc33cbb0a1c9eb5188d6227cacca36207a'
ORIGINAL_DRIVER_SHA256 = '33306eb79833929b26a736f95280eeed42970718be39cb98e7347d6b916ebdd3'
METRIC_NAMES = frozenset({'vllm:iteration_tokens_total_count', 'vllm:iteration_tokens_total_sum',
    'vllm:generation_tokens_total', 'vllm:prompt_tokens_total', 'vllm:spec_decode_num_drafts_total',
    'vllm:spec_decode_num_draft_tokens_total', 'vllm:spec_decode_num_accepted_tokens_total',
    'vllm:num_preemptions_total', 'vllm:prefix_cache_queries_total', 'vllm:prefix_cache_hits_total',
    'vllm:request_success_total', 'vllm:num_requests_running', 'vllm:num_requests_waiting'})
GAUGES = {'vllm:num_requests_running', 'vllm:num_requests_waiting'}
LABEL = re.compile(r'\s*([a-zA-Z_][a-zA-Z0-9_]*)=("(?:[^"\\\r\n]|\\["\\n])*")\s*(?:,|$)')
TOKENIZER_FIELDS = {'model_revision', 'model_config_sha256', 'weights_manifest_sha256',
                    'tokenizer_revision', 'tokenizer_assets_sha256', 'encoding_source_sha256',
                    'chat_template_sha256', 'special_tokens_sha256'}


def sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def ids(value):
    return isinstance(value, list) and bool(value) and all(type(v) is int and v >= 0 for v in value)


def validate_selection(value):
    require(isinstance(value, dict) and set(value) == {'arm', 'steering'}
            and value['arm'] in ARMS and value['steering'] in STATES,
            'finite A188/B197-OFF and unloaded/loaded0..4 selection required')
    return value


def extra_body(selection):
    validate_selection(selection)
    extra = {'temperature': 0, 'seed': 2048}
    if selection['steering'] != 'unloaded':
        extra.update(steering_profile=PROFILE, steering_scale=int(selection['steering'][-1]))
    return extra  # Deliberately no thinking/chat-template override, even at loaded0.


def toolbox_inventory():
    raw = read_owned_bytes(Path(__file__).with_name('q03_http_toolbox.json'))
    require(digest(raw) == TOOLBOX_INVENTORY_SHA256, 'original PR8 toolbox inventory changed')
    return json_bytes(raw)


def verify_toolbox(root, files):
    require(files == toolbox_inventory()['files'], 'exact PR8 source inventory required')
    for relative, expected in files.items():
        require(digest(read_owned_bytes(Path(root) / relative)) == expected,
                'original toolbox source changed: ' + relative)


def validate_tokenizer_identity(value):
    require(isinstance(value, dict) and set(value) == TOKENIZER_FIELDS, 'full shared tokenizer identity required')
    for key in TOKENIZER_FIELDS - {'tokenizer_assets_sha256', 'model_revision', 'tokenizer_revision'}:
        require(sha(value[key]), 'invalid tokenizer identity: ' + key)
    for key in ('model_revision', 'tokenizer_revision'):
        require(isinstance(value[key], str) and re.fullmatch('[0-9a-f]{40}', value[key]), 'exact model/tokenizer revision required')
    assets = value['tokenizer_assets_sha256']
    require(isinstance(assets, dict) and assets, 'full tokenizer asset inventory required')
    require(all(isinstance(k, str) and k and not Path(k).is_absolute() and '..' not in Path(k).parts
                and sha(v) for k, v in assets.items()), 'invalid tokenizer asset inventory')



def mounted_asset_files(value):
    identity, artifacts = value['tokenizer_identity'], value['artifacts']
    root = Path(artifacts['model_root'])
    return [{'path': str(root / 'config.json'), 'sha256': identity['model_config_sha256']}] + [
        {'path': str(root / name), 'sha256': pin} for name, pin in sorted(identity['tokenizer_assets_sha256'].items())]


def artifact_files(value):
    """Corpus production closure, distinct from each arm's live native identity."""
    artifacts, identity = value['artifacts'], value['tokenizer_identity']
    require(isinstance(artifacts, dict) and set(artifacts) == {'model_root', 'weights_manifest', 'runtime_metadata',
            'native_sources', 'special_tokens_file'}, 'registered corpus artifact bindings required')
    root = Path(artifacts['model_root'])
    require(root.is_absolute() and root.resolve() == root and '..' not in root.parts, 'canonical registered model root required')
    require(artifacts['special_tokens_file'] in identity['tokenizer_assets_sha256']
            and identity['tokenizer_assets_sha256'][artifacts['special_tokens_file']] == identity['special_tokens_sha256'],
            'inventoried special token asset differs')
    native = artifacts['native_sources']
    expected = {'deepseek_v41.py': '8450587010152846a2e3f6269c661b464615b7e46c312f4e38a397c44218a6e5',
                'deepseek_v41_encoding.py': '8ee7a8010c3ed257a0f1caa4dfbeb3ec68df0b03e36fcd20dad044ec7cf114fe'}
    require(isinstance(native, dict) and set(native) == set(expected), 'shared native tokenizer source closure differs')
    for name, pin in native.items():
        require(isinstance(pin, dict) and set(pin) == {'path', 'sha256'} and pin['sha256'] == expected[name],
                'actual shared native tokenizer source differs')
    pins = [*native.values(), artifacts['weights_manifest'], artifacts['runtime_metadata']]
    for pin in pins:
        require(isinstance(pin, dict) and set(pin) == {'path', 'sha256'} and sha(pin['sha256'])
                and isinstance(pin['path'], str) and Path(pin['path']).is_absolute()
                and Path(pin['path']).resolve() == Path(pin['path']), 'canonical immutable artifact pin required')
    require(artifacts['weights_manifest']['sha256'] == identity['weights_manifest_sha256'], 'weight manifest identity differs')
    require(identity['encoding_source_sha256'] == expected['deepseek_v41_encoding.py']
            and identity['chat_template_sha256'] == digest(canonical(expected)), 'shared native encoder/template identity differs')
    return pins

def validate_corpus(value):
    expected = {'schema_version', 'workload', 'model', 'tokenizer_identity', 'generator', 'prompts',
                'prompts_sha256', 'documents', 'historical_document_identity', 'artifacts'}
    require(isinstance(value, dict) and set(value) == expected and type(value['schema_version']) is int
            and value['schema_version'] == 1 and value['workload'] == WORKLOAD and value['model'] == MODEL,
            'finite warm4K corpus schema differs')
    validate_tokenizer_identity(value['tokenizer_identity'])
    artifact_files(value)
    require(value['historical_document_identity'] == 'UNAVAILABLE_NEW_SHARED_CORPUS',
            'historical document bytes were not retained; never claim exact historical replay')
    prompts = value['prompts']
    require(isinstance(prompts, list) and len(prompts) == 16
            and all(set(p) == {'id', 'text'} and p['id'] == f'd{i:03d}' and isinstance(p['text'], str)
                    for i, p in enumerate(prompts))
            and digest(json.dumps(prompts, sort_keys=True).encode()) == value['prompts_sha256'] == PROMPTS_SHA256,
            'original sixteen question bytes differ')
    gen = value['generator']
    require(isinstance(gen, dict) and set(gen) == {'toolbox_head', 'toolbox_files', 'driver_sha256',
            'context_tokens', 'seed', 'python', 'stdlib_sources'} and gen['toolbox_head'] == TOOLBOX_HEAD
            and gen['toolbox_files'] == toolbox_inventory()['files'] and gen['driver_sha256'] == ORIGINAL_DRIVER_SHA256
            and type(gen['context_tokens']) is int and gen['context_tokens'] == 4096
            and type(gen['seed']) is int and gen['seed'] == 2048, 'original document generator identity differs')
    require(isinstance(gen['python'], dict) and set(gen['python']) == {'implementation', 'version', 'executable_sha256'}
            and all(isinstance(gen['python'][k], str) and gen['python'][k] for k in ('implementation', 'version'))
            and sha(gen['python']['executable_sha256']), 'Python generator environment identity required')
    sources = gen['stdlib_sources']
    require(isinstance(sources, list) and sources and all(isinstance(s, dict) and set(s) == {'name', 'size', 'sha256'}
            and isinstance(s['name'], str) and Path(s['name']).name == s['name'] and s['name'].endswith('.py')
            and type(s['size']) is int and s['size'] > 4096 and sha(s['sha256']) for s in sources)
            and [s['name'] for s in sources] == sorted(set(s['name'] for s in sources)), 'stdlib source inventory differs')
    docs = value['documents']
    require(isinstance(docs, list) and len(docs) == 4, 'all four real shared documents required, including C1')
    for slot, doc in enumerate(docs):
        require(isinstance(doc, dict) and set(doc) == {'slot', 'text', 'text_sha256', 'raw_ids', 'calibration', 'chat_inputs'}
                and type(doc['slot']) is int and doc['slot'] == slot
                and isinstance(doc['text'], str) and doc['text'].startswith(f'Document {2048000 + slot}:\n')
                and digest(doc['text'].encode()) == doc['text_sha256'] and ids(doc['raw_ids']), 'real frozen document differs')
        calibration = doc['calibration']
        require(isinstance(calibration, list) and len(calibration) == 2, 'original two-step tokenizer rescaling required')
        for item in calibration:
            require(isinstance(item, dict) and set(item) == {'text', 'response'} and isinstance(item['text'], str)
                    and isinstance(item['response'], dict) and ids(item['response'].get('tokens'))
                    and type(item['response'].get('count')) is int
                    and item['response']['count'] == len(item['response']['tokens']), 'complete calibration text/IDs required')
        require(calibration[1]['text'] == doc['text'] and calibration[1]['response']['tokens'] == doc['raw_ids'],
                'final calibrated bytes/IDs differ')
        # The original generator always truncates character count, not token IDs.
        prefix = f'Document {2048000 + slot}:\n'
        require(len(calibration[0]['text']) == len(prefix) + int(4096 * 3.8)
                and len(doc['text']) == len(prefix) + int(4096 * (len(calibration[0]['text']) /
                                                       calibration[0]['response']['count'])),
                'original one-step character rescaling differs')
        rows = doc['chat_inputs']
        require(isinstance(rows, list) and len(rows) == 16, 'all slot/question template IDs required')
        for prompt, row in zip(prompts, rows):
            require(isinstance(row, dict) and set(row) == {'prompt_id', 'prompt_ids'}
                    and row['prompt_id'] == prompt['id'] and ids(row['prompt_ids']), 'complete template input IDs required')
    require(len({doc['text_sha256'] for doc in docs}) == 4, 'four distinct documents required')
    return value


def _tokenize(url, payload, deadline):
    remaining = deadline - time.monotonic()
    require(remaining > 0, 'client bound exhausted during corpus verification')
    request = urllib.request.Request(url.rstrip('/') + '/tokenize', data=canonical(payload),
                                     headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(request, timeout=min(remaining, 60)) as response:
        raw = response.read()
    result = json_bytes(raw)
    require(isinstance(result, dict) and ids(result.get('tokens')) and type(result.get('count')) is int
            and result['count'] == len(result['tokens']), 'tokenizer must return actual complete IDs; no estimates')
    return result


def validate_binding(value, binding=None):
    require(isinstance(value, dict) and set(value) == {'workload', 'case_id', 'corpus', 'metric_labels'}
            and value['workload'] == WORKLOAD and value['case_id'] in CASES, 'finite original-case binding required')
    labels = value['metric_labels']
    require(isinstance(labels, dict) and set(labels) == {'model_name', 'engine'} and labels['model_name'] == MODEL
            and isinstance(labels['engine'], str) and re.fullmatch('[0-9]+', labels['engine']), 'exact native metric labels required')
    corpus = validate_corpus(owner.pinned_json(value['corpus']))
    require(digest(canonical(corpus)) == value['corpus']['sha256'], 'canonical frozen corpus bytes required')
    if binding is not None:
        require(type(binding['http']['client_seconds']) is int and 106 <= binding['http']['client_seconds'] <= 600,
                'original 105-second measurement plus bounded preflight/drain must fit <=600 seconds')
    return corpus


def client_plan(binding, selection):
    validate_selection(selection)
    config = binding['http']['legacy_case']
    corpus = validate_binding(config, binding)
    return {'workload': WORKLOAD, 'case_id': config['case_id'], 'corpus': corpus,
            'corpus_sha256': config['corpus']['sha256'], 'metric_labels': config['metric_labels'],
            'selection': selection}


def validate_client_plan(plan):
    q = plan['client']['legacy_case']
    require(isinstance(q, dict) and set(q) == {'workload', 'case_id', 'corpus', 'corpus_sha256', 'metric_labels', 'selection'}
            and q['workload'] == WORKLOAD and q['case_id'] in CASES, 'fixed legacy client projection differs')
    validate_selection(plan['selection'])
    require(q['selection'] == plan['selection'], 'client state differs from owned selection')
    validate_corpus(q['corpus'])
    require(digest(canonical(q['corpus'])) == q['corpus_sha256'], 'immutable canonical corpus hash differs')
    require(type(plan['client']['timeout_seconds']) is int and 106 <= plan['client']['timeout_seconds'] <= 600,
            'original client bound must remain 106..600 seconds')
    require(plan['client']['toolbox_head'] == TOOLBOX_HEAD, 'original toolbox head differs')
    verify_toolbox(plan['client']['toolbox_path'], plan['client']['toolbox_files'])
    return q


def argv(plan, output):
    q = plan['client']['legacy_case']
    require(q['workload'] == WORKLOAD and q['case_id'] in CASES, 'unknown original finite case')
    prompts_path = Path(plan['client']['output']).with_name('legacy-prompts.jsonl')
    return ['run', plan['url'], '--model', MODEL, '--prompts', str(prompts_path), '--mode', 'closed',
            '--concurrency', str(CASES[q['case_id']]), '--warmup', '15', '--duration', '90', '--batches', '6',
            '--max-tokens', '256', '--context', '4096', '--seed', '2048', '--save-requests',
            '--extra-body', canonical(extra_body(plan['selection'])).decode(), '--label', q['case_id'], '--output', str(output)]


def validate_runtime_context(value, plan, *, now=None, check_fresh=True):
    require(isinstance(value, dict) and set(value) == {'window_id', 'attempt_id', 'plan_sha256', 'url', 'ranks', 'ready_sha256', 'admission'},
            'owned runtime/admission context fields differ')
    require(all(value[k] == plan[k] for k in ('window_id', 'attempt_id', 'plan_sha256', 'url')),
            'owned endpoint/attempt context differs')
    ranks = value['ranks']
    require(isinstance(ranks, list) and len(ranks) == 4, 'all four boot container bindings required')
    for rank, row in enumerate(ranks):
        require(isinstance(row, dict) and set(row) == {'rank', 'container_id', 'config_sha256'}
                and type(row['rank']) is int and row['rank'] == rank and sha(row['container_id'])
                and sha(row['config_sha256']), 'owned container/config identity differs')
    require(len({r['container_id'] for r in ranks}) == 4 and sha(value['ready_sha256']), 'duplicate container or missing readiness')
    admission = value['admission']
    require(isinstance(admission, dict) and set(admission) == {'thermal', 'prefix_reset'}, 'thermal and reset admission required')
    thermal = admission['thermal']
    require(isinstance(thermal, dict) and set(thermal) == {'wait_seconds', 'fan_policy', 'samples'}
            and number(thermal['wait_seconds']) and 0 <= thermal['wait_seconds'] <= 600
            and thermal['fan_policy'] == 'UNCHANGED', 'original separate cooling bound/fan policy differs')
    samples = thermal['samples']; now = time.time() if now is None else now
    require(number(now) and isinstance(samples, list) and len(samples) == 4, 'four thermal samples required')
    for rank, sample in enumerate(samples):
        require(isinstance(sample, dict) and set(sample) == {'rank', 'container_id', 'sampled_utc', 'hottest_c'}
                and type(sample['rank']) is int and sample['rank'] == rank and sample['container_id'] == ranks[rank]['container_id']
                and number(sample['sampled_utc']) and number(sample['hottest_c']) and 0 <= sample['hottest_c'] < 55,
                'thermal rank, sample or original <55C threshold differs')
        if check_fresh:
            require(0 <= now - sample['sampled_utc'] < 20, 'original <20-second thermal freshness required')
    reset = admission['prefix_reset']
    require(isinstance(reset, dict) and set(reset) == {'window_id', 'attempt_id', 'plan_sha256', 'url', 'method',
            'completed_utc', 'status', 'response_utf8', 'response_sha256'}
            and all(reset[k] == plan[k] for k in ('window_id', 'attempt_id', 'plan_sha256'))
            and reset['url'] == plan['url'] + '/reset_prefix_cache' and reset['method'] == 'POST'
            and type(reset['status']) is int and reset['status'] == 200 and number(reset['completed_utc'])
            and max(s['sampled_utc'] for s in samples) <= reset['completed_utc'] <= now
            and isinstance(reset['response_utf8'], str)
            and digest(reset['response_utf8'].encode()) == reset['response_sha256'], 'owned post-thermal prefix-reset receipt differs')
    reset_body = json_bytes(reset['response_utf8'])
    require(isinstance(reset_body, dict) and set(reset_body) == {'success'} and reset_body['success'] is True,
            'native prefix reset must positively succeed; HTTP200 alone is insufficient')
    return value


def native_series(text, expected):
    require(isinstance(text, str), 'missing native metrics')
    series = {}
    for line in text.splitlines():
        if not line or line.startswith('#'):
            continue
        name = re.split(r'[\s{]', line, maxsplit=1)[0]
        if name not in METRIC_NAMES:
            continue
        match = re.fullmatch(r'([^\s{]+)\{(.*)\}[ \t]+([^\s]+)(?:[ \t]+([+-]?[0-9]+))?[ \t]*', line)
        require(match is not None, 'unlabeled/malformed required native metric')
        labels, pos = {}, 0
        while pos < len(match[2]):
            item = LABEL.match(match[2], pos)
            require(item is not None and item[1] not in labels, 'invalid/duplicate native labels')
            labels[item[1]] = json.loads(item[2]); pos = item.end()
        require(all(labels.get(k) == v for k, v in expected.items()), 'foreign native model/engine series')
        require(re.fullmatch(r'[+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?', match[3]), 'invalid native number')
        value = Decimal(match[3])
        require(value.is_finite() and 0 <= value <= Decimal('1.7976931348623157e308')
                and value == value.to_integral_value(), 'invalid native counter/gauge')
        key = canonical([name, labels]).decode()
        require(key not in series, 'duplicate native metric series')
        series[key] = int(value)
    require({json_bytes(k)[0] for k in series} == METRIC_NAMES, 'missing original server/cache/acceptance metrics')
    return series


def totals(series):
    result = {}
    for key, value in series.items():
        name = json_bytes(key)[0]; result[name] = result.get(name, 0) + value
    return result


def validate_record(row, prompt_ids, output_tokens):
    require(row.get('error') is None and not row.get('stopped') and row.get('token_times') == 'exact'
            and type(row.get('prompt_tokens')) is int and row['prompt_tokens'] == len(prompt_ids)
            and type(row.get('completion_tokens')) is int and row['completion_tokens'] == output_tokens
            and row.get('finish_reason') == 'length', 'request failed or actual variable-input/fixed-output contract differs')
    sent, first, end = (row.get(k) for k in ('sent', 'first', 'end'))
    require(all(number(t) for t in (sent, first, end)) and sent <= first <= end, 'request clock ordering differs')
    events = row.get('events')
    require(isinstance(events, list) and events and events[0][0] == first and events[-1][1] == output_tokens,
            'complete exact chunk times required')
    previous_t, previous_n = first, 0
    for event in events:
        require(isinstance(event, list) and len(event) == 2, 'malformed token event')
        at, n = event
        require(number(at) and previous_t <= at <= end and type(n) is int and previous_n <= n <= output_tokens,
                'invalid/unordered token event')
        previous_t, previous_n = at, n


class ReplayStopped(RuntimeError):
    pass


def _response_evidence(raw_lines):
    events, text, output_ids, parse_error = [], [], [], None
    for raw in raw_lines:
        line = raw.decode('utf-8', errors='replace').strip()
        if not line.startswith('data: ') or line == 'data: [DONE]':
            continue
        try:
            event = json_bytes(line[6:])
        except Exception as exc:
            parse_error = f'{type(exc).__name__}: {exc}'; break
        if not isinstance(event, dict):
            parse_error = 'SSE event is not an object'; break
        events.append(event)
        for choice in event.get('choices') or []:
            delta = choice.get('delta') or {}
            for key in ('content', 'reasoning_content', 'reasoning'):
                if isinstance(delta.get(key), str):
                    text.append({'kind': key, 'text': delta[key]})
            if isinstance(choice.get('token_ids'), list):
                output_ids.extend(choice['token_ids'])
    return {'raw_sse_base64': [base64.b64encode(line).decode() for line in raw_lines], 'events': events,
            'response_parts': text, 'output_token_ids': output_ids or None,
            'output_ids_status': 'OBSERVED' if output_ids else 'NOT_RETURNED_BY_ORIGINAL_REQUEST', 'parse_error': parse_error}



def validate_response(row):
    response, record = row['response'], row['record']
    require(response['parse_error'] is None and response['events'], 'complete parseable raw response required')
    usage, finish, emitted_counts = {}, None, []
    for event in response['events']:
        require(not event.get('error'), 'native error in retained response')
        usage = event.get('usage') or usage
        emitted = False
        for choice in event.get('choices') or []:
            finish = choice.get('finish_reason') or finish
            delta = choice.get('delta') or {}
            emitted = emitted or any(isinstance(delta.get(k), str) and bool(delta[k])
                                     for k in ('content', 'reasoning_content', 'reasoning'))
        if emitted:
            emitted_counts.append((event.get('usage') or {}).get('completion_tokens'))
    require(usage.get('prompt_tokens') == record['prompt_tokens']
            and usage.get('completion_tokens') == record['completion_tokens']
            and finish == record['finish_reason']
            and emitted_counts == [event[1] for event in record['events']],
            'retained raw response usage/finish/chunk counts differ from timed record')
    if response['output_token_ids'] is not None:
        require(ids(response['output_token_ids']) and len(response['output_token_ids']) == record['completion_tokens'],
                'returned complete output token IDs differ from native usage')

def run_client(plan, main):
    """One admitted case, run by the existing supervised source-only HTTP client."""
    from servebench import client, load, prom
    q = validate_client_plan(plan)
    output = Path(plan['client']['output'])
    raw_path, evidence_path = output.with_name('legacy-run.json'), output.with_name('legacy-evidence.json')
    prompt_path = output.with_name('legacy-prompts.jsonl')
    require(not any(p.exists() for p in (output, raw_path, evidence_path, prompt_path)), 'legacy output already exists')
    context = validate_runtime_context(json_bytes(read_owned_bytes(plan['client']['runtime_context'])), plan)
    stop, lock, local = threading.Event(), threading.RLock(), threading.local()
    evidence = {'schema_version': 1, 'context_sha256': digest(canonical(context)), 'url': plan['url'],
                'started_utc': time.time(), 'started_monotonic': time.monotonic(), 'admitted_utc': None,
                'requests': [], 'scrapes': [], 'tokenizer_verification': [], 'raw_run': None, 'error': None}
    deadline = evidence['started_monotonic'] + plan['client']['timeout_seconds'] - 5
    original_stream, original_fetch, original_run = client.stream_chat, prom.fetch, load.run
    original_docs, original_open = load.documents, urllib.request.urlopen
    old_alarm, old_abort = signal.getsignal(signal.SIGALRM), signal.getsignal(signal.SIGUSR1)
    lookup = {}
    for doc in q['corpus']['documents']:
        for prompt, row in zip(q['corpus']['prompts'], doc['chat_inputs']):
            lookup[doc['text'] + '\n\n' + prompt['text']] = (doc['slot'], prompt['id'], row['prompt_ids'])
    salts, primed, last_series = {}, [], [None]
    order = list(q['corpus']['prompts']); random.Random(2048).shuffle(order)

    def abort(reason):
        with lock:
            first = not stop.is_set(); stop.set()
            evidence['error'] = evidence['error'] or str(reason)
        if threading.current_thread() is threading.main_thread():
            raise ReplayStopped(str(reason))
        if first:
            os.kill(os.getpid(), signal.SIGUSR1)

    def handler(signum, frame):
        abort('existing client hard deadline reached' if signum == signal.SIGALRM else evidence['error'])

    class Tee:
        def __init__(self, response, row):
            self.response, self.row = response, row
        def __enter__(self):
            self.response.__enter__(); return self
        def __exit__(self, *args):
            return self.response.__exit__(*args)
        def __iter__(self):
            for line in self.response:
                self.row['_lines'].append(line)
                yield line
        def __getattr__(self, name):
            return getattr(self.response, name)

    def urlopen(request, *args, **kwargs):
        row = getattr(local, 'row', None)
        if row is not None:
            require(request.full_url == plan['url'] + '/v1/chat/completions', 'stream endpoint changed')
            wire = json_bytes(request.data)
            expected = dict(row['payload'], stream=True,
                            stream_options={'include_usage': True, 'continuous_usage_stats': True})
            require(wire == expected, 'original stream wire payload differs')
            row['wire_payload'] = wire
            return Tee(original_open(request, *args, **kwargs), row)
        return original_open(request, *args, **kwargs)

    def documents(url, model, spec, vllm):
        require(url == plan['url'] and model == MODEL and vllm is True and spec.describe() == expected_load(q),
                'frozen document seam differs from exact original case')
        result = []
        for doc in q['corpus']['documents'][:CASES[q['case_id']]]:
            salt = uuid.uuid4().hex; salts[doc['slot']] = salt
            result.append({'slot': doc['slot'], 'text': doc['text'], 'tokens': len(doc['raw_ids']), 'salt': salt})
        return result

    def stream(url, payload, **kwargs):
        with lock:
            if stop.is_set():
                raise SystemExit()  # No new request after first failure, including failed primers.
            try:
                require(url == plan['url'] and isinstance(payload, dict), 'stream endpoint/payload differs')
                messages = payload.get('messages')
                require(isinstance(messages, list) and len(messages) == 1 and set(messages[0]) == {'role', 'content'}
                        and messages[0]['role'] == 'user' and messages[0]['content'] in lookup, 'non-corpus chat input')
                slot, prompt_id, prompt_ids = lookup[messages[0]['content']]
                count = payload.get('max_tokens'); primer = count == 1
                require(type(count) is int and count in (1, 256) and slot < CASES[q['case_id']], 'unreviewed output/slot')
                expected = {'model': MODEL, 'messages': messages, 'max_tokens': count, 'temperature': 0,
                            'cache_salt': salts[slot], 'ignore_eos': True, 'min_tokens': count} | extra_body(plan['selection'])
                require(payload == expected, 'original chat/salt/steering/sampling payload changed')
                if primer:
                    require(slot == len(primed) and prompt_id == order[slot]['id'], 'original serial primer order differs')
                else:
                    require(primed == list(range(CASES[q['case_id']])) and evidence['scrapes'], 'measurement before successful primers/fence')
                row = {'phase': 'prime' if primer else 'load', 'slot': slot, 'prompt_id': prompt_id,
                       'input_ids': prompt_ids, 'payload': copy.deepcopy(payload), 'wire_payload': None,
                       'record': None, 'response': None, '_lines': []}
                evidence['requests'].append(row)
            except Exception as exc:
                abort(exc); raise SystemExit()
        local.row = row
        try:
            kwargs['stop'] = stop
            kwargs['timeout'] = min(kwargs.get('timeout', 900), max(.001, deadline - time.monotonic()))
            record = original_stream(url, payload, **kwargs)
            with lock:
                row['record'] = copy.deepcopy(record)
                row['response'] = _response_evidence(row['_lines'])
                row.pop('_lines', None)
            if stop.is_set():
                raise SystemExit()
            validate_record(record, prompt_ids, count)
            if primer:
                with lock:
                    primed.append(slot)
            return record
        except Exception as exc:
            abort(exc); raise SystemExit()
        finally:
            local.row = None

    def fetch(url, timeout=10):
        if stop.is_set():
            raise SystemExit()
        begin = time.monotonic()
        text = original_fetch(url, min(timeout, max(.001, deadline - begin)))
        with lock:
            evidence['scrapes'].append({'begin_monotonic': begin, 'end_monotonic': time.monotonic(), 'text': text})
        try:
            require(url == plan['url'], 'metrics endpoint differs')
            current = native_series(text, q['metric_labels'])
            previous = last_series[0]
            if previous is not None:
                require(set(current) == set(previous), 'native metric series inventory changed')
                require(all(current[k] >= v for k, v in previous.items() if json_bytes(k)[0] not in GAUGES), 'native counter reset')
                require(totals(current)['vllm:num_preemptions_total'] == totals(previous)['vllm:num_preemptions_total'], 'native preemption observed')
            else:
                require(all(totals(current)[name] == 0 for name in GAUGES), 'foreign load before primers')
            last_series[0] = current
        except Exception as exc:
            abort(exc)
        return text

    def run(*args, **kwargs):
        raw = original_run(*args, **kwargs)
        evidence['raw_run'] = copy.deepcopy(raw)
        return raw

    code = 1
    signal.signal(signal.SIGALRM, handler); signal.signal(signal.SIGUSR1, handler)
    signal.setitimer(signal.ITIMER_REAL, max(.001, deadline - time.monotonic()))
    client.stream_chat, prom.fetch, load.run, load.documents = stream, fetch, run, documents
    urllib.request.urlopen = urlopen
    try:
        # Recheck actual template/raw IDs on BOTH arms; never infer sameness from seed.
        for doc in q['corpus']['documents']:
            response = _tokenize(plan['url'], {'model': MODEL, 'prompt': doc['text']}, deadline)
            evidence['tokenizer_verification'].append({'slot': doc['slot'], 'kind': 'raw', 'response': response})
            require(response['tokens'] == doc['raw_ids'], 'actual raw document IDs differ on this arm')
            for prompt, frozen in zip(q['corpus']['prompts'], doc['chat_inputs']):
                response = _tokenize(plan['url'], {'model': MODEL, 'messages': [{'role': 'user',
                    'content': doc['text'] + '\n\n' + prompt['text']}]}, deadline)
                evidence['tokenizer_verification'].append({'slot': doc['slot'], 'kind': prompt['id'], 'response': response})
                require(response['tokens'] == frozen['prompt_ids'], 'actual chat-template IDs differ on this arm')
        evidence['admitted_utc'] = time.time()
        validate_runtime_context(context, plan, now=evidence['admitted_utc'])
        prompt_raw = b''.join(canonical(p) + b'\n' for p in q['corpus']['prompts'])
        with prompt_path.open('xb') as handle:
            handle.write(prompt_raw); handle.flush(); os.fsync(handle.fileno())
        fetch(plan['url'])  # Separate pre-prime audit fence, never part of PR8 timing.
        code = main(argv(plan, raw_path))
        require(type(code) is int and code == 0, 'original servebench case failed')
        validate_case(json_bytes(read_owned_bytes(raw_path)), evidence, plan)
    except Exception as exc:
        stop.set(); evidence['error'] = evidence['error'] or f'{type(exc).__name__}: {exc}'; code = 1
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        # On failure, admission wrappers remain latched until the fixed process
        # exits. This is not quiescence; the supervisor still owns descendants.
        if code == 0:
            client.stream_chat, prom.fetch, load.run, load.documents = original_stream, original_fetch, original_run, original_docs
            urllib.request.urlopen = original_open
        signal.signal(signal.SIGALRM, old_alarm); signal.signal(signal.SIGUSR1, old_abort)
        evidence['ended_utc'], evidence['ended_monotonic'] = time.time(), time.monotonic()
        with lock:
            for row in evidence['requests']:
                if '_lines' in row:
                    try:
                        row['response'] = _response_evidence(row.pop('_lines'))
                    except Exception:
                        row['response'] = {'capture_error': 'partial malformed stream'}
            exclusive_json(evidence_path, evidence)
    result = receipt_identity(plan, context) | {'exit_code': code,
              'raw_sha256': digest(read_owned_bytes(raw_path)) if raw_path.exists() else None,
              'evidence_sha256': digest(read_owned_bytes(evidence_path))}
    exclusive_json(output, result)
    return code


def expected_load(q):
    return {'mode': 'closed', 'concurrency': CASES[q['case_id']], 'rate': 1.0, 'warmup_s': 15.0, 'duration_s': 90.0,
            'batches': 6, 'max_tokens': 256, 'fixed_length': True, 'seed': 2048, 'max_inflight': 512, 'context_tokens': 4096}


def _frozen_report(raw, plan):
    """Recompute using pinned source, never the mutable saved summary or .pyc."""
    import types
    package = '_q03_legacy_report'
    names = ('prom', 'stats', 'client', 'report')
    require(not any(n == package or n.startswith(package + '.') for n in sys.modules), 'private report namespace already loaded')
    parent = types.ModuleType(package); parent.__path__ = []; sys.modules[package] = parent
    try:
        for name in names:
            path = Path(plan['client']['toolbox_path']) / 'servebench' / (name + '.py')
            code = read_owned_bytes(path)
            require(digest(code) == toolbox_inventory()['files']['servebench/' + name + '.py'], 'frozen report source changed')
            child = types.ModuleType(package + '.' + name); child.__package__ = package
            sys.modules[child.__name__] = child; setattr(parent, name, child)
            exec(compile(code, str(path), 'exec'), child.__dict__)
        return parent.report.summarize(raw), parent.prom
    finally:
        for name in (*names, None):
            sys.modules.pop(package + '.' + name if name else package, None)


def validate_case(value, evidence, plan):
    q, raw = plan['client']['legacy_case'], evidence.get('raw_run')
    require(evidence.get('error') is None and isinstance(raw, dict), 'failed/incomplete original case')
    require(value['server'] == {'url': plan['url'], 'model': MODEL, 'adapter': 'vllm'}
            and value['label'] == q['case_id'], 'saved case endpoint/model/label differs')
    expected = expected_load(q) | {'prompts': str(Path(plan['client']['output']).with_name('legacy-prompts.jsonl')),
        'prompt_count': 16, 'prompts_sha256': PROMPTS_SHA256, 'extra_body': extra_body(plan['selection'])}
    require(canonical(value['workload']) == canonical(expected), 'original warm4K workload differs')
    require(number(raw.get('start')) and raw['window'] == [raw['start'] + 15, raw['start'] + 105]
            and raw['boundaries'] == [raw['start'] + 15 + k * 15 for k in range(7)]
            and raw.get('rejected') == 0, 'original 15+90/6 timing differs')
    rows = evidence['requests']; primers = [r for r in rows if r['phase'] == 'prime']; measured = [r for r in rows if r['phase'] == 'load']
    c = CASES[q['case_id']]
    require([r['slot'] for r in primers] == list(range(c)) and rows[:c] == primers and measured,
            'all successful serial primers before timed requests required')
    require(len(raw['records']) == len(measured), 'timed request inventory differs')
    order = list(q['corpus']['prompts']); random.Random(2048).shuffle(order)
    salts, measured_records = {}, []
    for row in rows:
        slot = row['slot']; doc = q['corpus']['documents'][slot]
        index = int(row['prompt_id'][1:]); prompt = q['corpus']['prompts'][index]
        wanted = doc['chat_inputs'][index]['prompt_ids']; n = 1 if row['phase'] == 'prime' else 256
        require(row['input_ids'] == wanted, 'retained complete input IDs differ')
        validate_record(row['record'], wanted, n)
        payload = row['payload']; salt = payload['cache_salt']
        require(isinstance(salt, str) and re.fullmatch('[0-9a-f]{32}', salt), 'per-slot salt required')
        if row['phase'] == 'prime':
            require(row['prompt_id'] == order[slot]['id'] and salt not in salts.values(), 'original primer prompt/fresh slot salt differs')
            salts[slot] = salt
        require(salts.get(slot) == salt, 'slot salt changed after priming')
        expected_payload = {'model': MODEL, 'messages': [{'role': 'user', 'content': doc['text'] + '\n\n' + prompt['text']}],
                            'max_tokens': n, 'temperature': 0, 'cache_salt': salt, 'ignore_eos': True, 'min_tokens': n} | extra_body(plan['selection'])
        require(payload == expected_payload and row['wire_payload'] == dict(payload, stream=True,
                stream_options={'include_usage': True, 'continuous_usage_stats': True}), 'retained original payload differs')
        response = row['response']
        require(isinstance(response, dict) and response == _response_evidence([base64.b64decode(x, validate=True)
                for x in response['raw_sse_base64']]) and response['events'] and response['parse_error'] is None, 'raw streamed response evidence changed/missing')
        validate_response(row)
        if row['phase'] == 'load':
            measured_records.append(row['record'])
    require(len(salts) == c, 'not all document slots primed')
    expected_records = sorted(canonical(r['record'] | {'slot': r['slot'], 'prompt_id': r['prompt_id']}) for r in measured)
    actual_records = sorted(canonical({k: v for k, v in r.items() if k != 'index'}) for r in raw['records'])
    require(actual_records == expected_records, 'unrounded timed request records differ')
    require(sorted(r['index'] for r in raw['records']) == list(range(len(measured))), 'original request index sequence differs')
    for row in raw['records']:
        require(row['prompt_id'] == order[row['index'] % 16]['id'] and row['slot'] in range(c), 'original question ordering differs')
    require(len(raw['documents']) == c and all(d['slot'] == i and d['tokens'] == len(q['corpus']['documents'][i]['raw_ids'])
            and d['prime_error'] is None for i, d in enumerate(raw['documents'])), 'original document/priming metadata differs')
    require(value.get('documents') == raw['documents'], 'saved primer metadata differs')
    scrapes = evidence['scrapes']
    # One separate pre-prime scrape + original post-prime fence + 7 boundaries + final drain fence.
    require(len(scrapes) == 10 and len(raw['scrapes']) == 7 and len(raw['scrape_times']) == 7
            and len(raw['fences']['scrapes']) == len(raw['fences']['times']) == 2, 'original fence/scrape inventory differs')
    series = [native_series(s['text'], q['metric_labels']) for s in scrapes]
    for previous, current in zip(series, series[1:]):
        require(set(current) == set(previous) and all(current[k] >= n for k, n in previous.items() if json_bytes(k)[0] not in GAUGES),
                'foreign/reset/missing native counters')
    stats = list(map(totals, series)); first, pre, final = stats[0], stats[1], stats[-1]
    require(all(stats[i][name] == 0 for i in (0, 1, -1) for name in GAUGES), 'foreign or undrained work at fences')
    require(all(s['vllm:num_preemptions_total'] == first['vllm:num_preemptions_total'] for s in stats), 'preemption observed')
    for before, after, records in ((first, pre, primers), (pre, final, measured), (first, final, rows)):
        require(after['vllm:request_success_total'] - before['vllm:request_success_total'] == len(records), 'foreign/missing successful requests')
        require(after['vllm:generation_tokens_total'] - before['vllm:generation_tokens_total'] == sum(r['record']['completion_tokens'] for r in records),
                'primer/timed generation counters differ')
        require(after['vllm:prompt_tokens_total'] - before['vllm:prompt_tokens_total'] == sum(r['record']['prompt_tokens'] for r in records),
                'actual variable input token counters differ')
    summary, prom = _frozen_report(raw, plan)
    parsed = [{k: prom.parse(s['text'])[name] for k, name in prom.VLLM.items()} for s in scrapes]
    require(raw['fences']['scrapes'] == [parsed[1], parsed[-1]] and raw['scrapes'] == parsed[2:-1], 'retained original scrape/fence values differ')
    require(max(r['record']['end'] for r in primers) <= raw['fences']['times'][0] <= raw['start']
            and max(r['end'] for r in raw['records']) <= raw['fences']['times'][-1], 'primer or final-drain fence ordering differs')
    require(canonical(summary) == canonical(value['summary']) and summary['requests']['foreign'] == 0
            and summary['requests']['failed'] == 0 and summary['output_tps']['n'] == 6, 'original summary differs')
    origin = raw['start']
    rounded = [{k: round(v - origin, 4) if k in ('sent', 'first', 'end') and v is not None else v
                for k, v in r.items() if k != 'events'} | {'events': [[round(t - origin, 4), n] for t, n in r['events']]}
               for r in raw['records']]
    require(value.get('requests') == rounded, 'original saved rounded requests differ')
    expected_tokenizations = []
    for doc in q['corpus']['documents']:
        expected_tokenizations.append((doc['slot'], 'raw', doc['raw_ids']))
        expected_tokenizations.extend((doc['slot'], r['prompt_id'], r['prompt_ids']) for r in doc['chat_inputs'])
    observations = evidence['tokenizer_verification']
    require(len(observations) == len(expected_tokenizations), 'complete live tokenizer crosscheck required')
    for row, (slot, kind, token_ids) in zip(observations, expected_tokenizations):
        require(row['slot'] == slot and row['kind'] == kind and row['response']['tokens'] == token_ids
                and row['response']['count'] == len(token_ids), 'actual corpus/tokenizer verification differs')
    return True


def receipt_identity(plan, context):
    q = plan['client']['legacy_case']
    return {'workload': WORKLOAD, 'case_id': q['case_id'], 'window_id': plan['window_id'], 'attempt_id': plan['attempt_id'],
            'plan_sha256': plan['plan_sha256'], 'selection': plan['selection'], 'corpus_sha256': q['corpus_sha256'],
            'runtime_context_sha256': digest(canonical(context)), 'toolbox_head': TOOLBOX_HEAD,
            'historical_comparison': 'UNAVAILABLE_ORIGINAL_DOCUMENT_BYTES', 'performance': 'NOT_EVALUATED',
            'quality': 'NOT_EVALUATED', 'capacity': 'NOT_EVALUATED', 'lifecycle': 'REQUIRES_OWNED_FOUR_RANK_DRAIN'}


def validate_output(value, plan):
    validate_client_plan(plan)
    output = Path(plan['client']['output'])
    evidence_raw = read_owned_bytes(output.with_name('legacy-evidence.json'))
    raw = read_owned_bytes(output.with_name('legacy-run.json'))
    evidence = json_bytes(evidence_raw)
    context = validate_runtime_context(json_bytes(read_owned_bytes(plan['client']['runtime_context'])), plan,
                                       now=evidence['admitted_utc'])
    expected = receipt_identity(plan, context) | {'exit_code': 0, 'raw_sha256': digest(raw), 'evidence_sha256': digest(evidence_raw)}
    require(canonical(value) == canonical(expected), 'complete legacy receipt identity/hash/status differs')
    require(evidence['context_sha256'] == value['runtime_context_sha256'] and evidence['url'] == plan['url']
            and number(evidence['started_utc']) and evidence['started_utc'] <= evidence['admitted_utc'] <= evidence['ended_utc']
            and 0 < evidence['ended_monotonic'] - evidence['started_monotonic'] <= plan['client']['timeout_seconds'],
            'owned endpoint/admission/existing client bound differs')
    validate_case(json_bytes(raw), evidence, plan)
    return {'status': 'COMPLETE_ORIGINAL_CASE_CLIENT_ONLY', 'case_id': value['case_id'], 'performance': 'NOT_EVALUATED',
            'quality': 'NOT_EVALUATED', 'capacity': 'NOT_EVALUATED', 'lifecycle': 'REQUIRES_OWNED_FOUR_RANK_DRAIN'}


def coverage():
    """Typed gaps, not silent substitutes for the approved comparison dimensions."""
    return {'arms': list(ARMS), 'steering_states': list(STATES),
            'implemented_cases': list(CASES), 'scope': 'ONE_CASE_PER_OWNED_ATTEMPT',
            'historical_document_bytes': 'UNAVAILABLE_NEW_SHARED_CORPUS_REQUIRED',
            'same_boot_scale_return_sequence': 'NOT_IMPLEMENTED', 'release256': 'NOT_IMPLEMENTED',
            'sampled_poisson': 'NOT_IMPLEMENTED', 'cold_ttft_long_prefill': 'NOT_IMPLEMENTED',
            'near1m_capacity': 'NOT_IMPLEMENTED', 'model256_quality': 'NOT_IMPLEMENTED',
            'semantic_quality': 'NOT_IMPLEMENTED', 'dual_arm': 'NOT_IN_FIRST_ROUND',
            'field_performance': 'NOT_RUN', 'field_quality': 'NOT_RUN', 'field_capacity': 'NOT_RUN'}
