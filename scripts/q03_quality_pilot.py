"""One immutable sixty-case quality capture inside the existing owned HTTP lane.

No launcher, scheduler, arbitrary argv, candidate execution or semantic PASS.
The schema-7 caller retains the old client/job/lease ceilings unchanged.
"""
from __future__ import annotations
import copy
import json
from pathlib import Path
import time
import urllib.request
import urllib.error

import q03_quality_capture as C
import q03_legacy_case_replay as L
import q03_owner_model as owner
from q03_held_window import read_owned_bytes

require, canonical, digest = C.require, C.canonical, C.digest
WORKLOAD = 'quality_pilot60_v1'
CASE_INDEX_SHA256 = '4ccde61e2aaa825ce346e5569395054e445b6602d46435395e04e262473c3eac'
DEFAULT_RENDERING = {'thinking': True, 'reasoning_effort': 'high',
    'add_generation_prompt': True, 'overrides': {}}
EXTRACTION = {'status': 'UNBOUND', 'field': 'response.decoded.choices[0].message.content',
    'reason': 'native parser source/runtime configuration not yet independently bound'}
SOURCES = ('q03_quality_pilot', 'q03_quality_capture', 'q03_legacy_case_replay',
           'q03_owner_model', 'q03_held_window')


def validate_corpus(value):
    require(isinstance(value, dict) and set(value) == {'schema_version', 'workload', 'model',
        'material_sha256', 'prompts', 'tokenizer_identity', 'artifacts', 'cases', 'default_rendering'},
        'quality corpus fields differ')
    require(type(value['schema_version']) is int and value['schema_version'] == 1
        and value['workload'] == WORKLOAD and value['model'] == C.MODEL
        and value['material_sha256'] == C.MATERIAL_SHA256, 'quality corpus identity differs')
    require(canonical(value['default_rendering']) == canonical(DEFAULT_RENDERING),
        'quality requires unmodified native default thinking/high rendering')
    L.validate_tokenizer_identity(value['tokenizer_identity'])
    L.artifact_files(value)
    pin = value['prompts']
    require(isinstance(pin, dict) and set(pin) == {'path', 'sha256'}
        and pin['sha256'] == C.PROMPTS_SHA256, 'reviewed quality prompts required')
    raw = read_owned_bytes(pin['path'])
    require(digest(raw) == pin['sha256'], 'quality prompt snapshot changed')
    prompts = [C.strict_json(line) for line in raw.splitlines() if line.strip()]
    cases = value['cases']
    require(isinstance(cases, list) and len(cases) == len(prompts) == 60, 'all sixty exact cases required')
    for p, case in zip(prompts, cases):
        require(set(p) == {'id', 'text'} and isinstance(p['text'], str)
            and isinstance(case, dict) and set(case) == {'id', 'category', 'language', 'prompt_sha256', 'input_ids'}
            and case['id'] == p['id'] and case['prompt_sha256'] == digest(p['text'].encode())
            and L.ids(case['input_ids']) and case['language'] in ('zh', 'en')
            and isinstance(case['category'], str) and case['category'], 'quality prompt/case/input identity differs')
    require(len({c['id'] for c in cases}) == 60, 'quality IDs repeated')
    projection = [{k: c[k] for k in ('id', 'category', 'language', 'prompt_sha256')} for c in cases]
    require(digest(canonical(projection)) == CASE_INDEX_SHA256, 'quality immutable category/language case index differs')
    return value


def artifact_files(corpus):
    return [corpus['prompts'], *L.artifact_files(corpus)]


def mounted_asset_files(corpus):
    return L.mounted_asset_files(corpus)


def validate_binding(value, binding=None):
    require(isinstance(value, dict) and set(value) == {'workload', 'corpus', 'request_seconds',
        'max_response_bytes', 'max_header_bytes'} and value['workload'] == WORKLOAD,
        'fixed quality pilot binding required; no argv, segmentation or retry')
    for name in ('request_seconds', 'max_response_bytes', 'max_header_bytes'):
        require(type(value[name]) is int and value[name] > 0, 'explicit positive capsule bound required: ' + name)
    corpus = validate_corpus(owner.pinned_json(value['corpus']))
    require(digest(canonical(corpus)) == value['corpus']['sha256'], 'canonical quality corpus required')
    if binding is not None:
        seconds = binding['http']['client_seconds']
        require(type(seconds) is int and 106 <= seconds <= 600
            and value['request_seconds'] <= seconds - 5, 'quality bounds exceed existing client ceiling/reserve')
    return corpus


def client_plan(binding, selection):
    L.validate_selection(selection)
    q = binding['http']['quality_pilot']
    corpus = validate_binding(q, binding)
    return dict(workload=WORKLOAD, corpus=corpus, corpus_sha256=q['corpus']['sha256'],
                request_seconds=q['request_seconds'], max_response_bytes=q['max_response_bytes'],
                max_header_bytes=q['max_header_bytes'])


def validate_context(value, plan):
    require(isinstance(value, dict) and set(value) == {'window_id', 'attempt_id', 'plan_sha256',
        'url', 'ranks', 'ready_sha256', 'observed_loaded_state'}, 'owned quality runtime context required')
    require(all(value[k] == plan[k] for k in ('window_id', 'attempt_id', 'plan_sha256', 'url')),
        'quality context belongs to another plan')
    require(L.sha(value['ready_sha256']) and isinstance(value['ranks'], list) and len(value['ranks']) == 4,
        'quality four-rank readiness missing')
    for rank, row in enumerate(value['ranks']):
        require(set(row) == {'rank', 'container_id', 'config_sha256'} and type(row['rank']) is int
            and row['rank'] == rank and L.sha(row['container_id']) and L.sha(row['config_sha256']),
            'quality exact four-rank identities required')
    observed = value['observed_loaded_state']
    require(set(observed) == {'status', 'profiles'} and observed['status'] == 'OBSERVED_FOUR_RANKS'
        and isinstance(observed['profiles'], list) and len(observed['profiles']) == 4,
        'quality loaded-profile observation missing')
    # Profile bytes and arm identities were verified by baseline validate_ready.
    if plan['selection']['steering'] in ('absent', 'unloaded'):
        require(observed['profiles'] == [None] * 4, 'quality unloaded state differs')
    else:
        require(all(isinstance(p, dict) and p.get('name') == C.PROFILE for p in observed['profiles']),
            'quality loaded state differs')
    return value


def run_client(plan, main, *, open_response=None, clock=time.monotonic):
    q = plan['client']['quality_pilot']
    require(set(q) == {'workload', 'corpus', 'corpus_sha256', 'request_seconds',
        'max_response_bytes', 'max_header_bytes'} and q['workload'] == WORKLOAD,
        'immutable finite quality client differs')
    corpus = validate_corpus(q['corpus'])
    require(digest(canonical(corpus)) == q['corpus_sha256'], 'quality corpus changed before client')
    require(type(plan['client']['timeout_seconds']) is int and 106 <= plan['client']['timeout_seconds'] <= 600
        and type(q['request_seconds']) is int and 0 < q['request_seconds'] <= plan['client']['timeout_seconds'] - 5,
        'quality client/request bound differs')
    context = validate_context(C.strict_json(read_owned_bytes(plan['client']['runtime_context'])), plan)
    adapted = copy.deepcopy(plan)
    adapted['client']['quality_capture'] = dict(material_sha256=C.MATERIAL_SHA256,
        prompts_path=corpus['prompts']['path'], prompts_sha256=C.PROMPTS_SHA256, cases=corpus['cases'],
        max_response_bytes=q['max_response_bytes'], max_header_bytes=q['max_header_bytes'], context=dict(
            runtime_receipt_sha256=digest(canonical(context)),
            tokenizer_identity_sha256=digest(canonical(corpus['tokenizer_identity'])),
            decode_identity_sha256=digest(canonical(corpus['default_rendering'])),
            observed_loaded_state=context['observed_loaded_state'],
            applied_state_evidence={'status': 'UNVERIFIED', 'requested_state': plan['selection']['steering']},
            final_answer_extraction=copy.deepcopy(EXTRACTION)))
    deadline = clock() + plan['client']['timeout_seconds'] - 5

    def tokenize(capture):
        prompts = [C.strict_json(line) for line in read_owned_bytes(corpus['prompts']['path']).splitlines()]
        for index, (prompt, case) in enumerate(zip(prompts, corpus['cases'])):
            record = {'index': index, 'case_id': case['id'], 'endpoint': plan['url'] + '/tokenize',
                'payload': {'model': C.MODEL, 'messages': [{'role': 'user', 'content': prompt['text']}]},
                'response': None, 'input_ids_equal': False}
            capture.preflight_records.append(record)
            capture.request_deadline = clock() + q['request_seconds']
            try:
                wire = canonical(record['payload']); record['request_sha256'] = digest(wire)
                C.exclusive(capture.directory / f'tokenize-request-{index:03d}.json', canonical(record))
                request = urllib.request.Request(record['endpoint'], data=wire,
                    headers={'Content-Type': 'application/json'}, method='POST')
                try:
                    response = capture.open_response(request, timeout=capture.remaining())
                except urllib.error.HTTPError as exc:
                    response = exc
                with response:
                    result = capture._read(response, record, raw_name=f'tokenize-{index:03d}.bin')
                record['input_ids_equal'] = (isinstance(result, dict) and L.ids(result.get('tokens'))
                    and result['tokens'] == case['input_ids'] and type(result.get('count')) is int
                    and result['count'] == len(case['input_ids']))
                require(record['input_ids_equal'], 'live quality tokenizer IDs differ: ' + case['id'])
            except BaseException as exc:
                record['error'] = f'{type(exc).__name__}: {exc}'
                raise
            finally:
                capture.request_deadline = None
                C.exclusive(capture.directory / f'tokenize-{index:03d}.json', canonical(record))
        C.exclusive(capture.directory / 'live-tokenization.json', canonical(capture.preflight_records))

    return C.run_client(adapted, main, deadline=deadline, open_response=open_response,
                        clock=clock, request_seconds=q['request_seconds'], preflight=tokenize)


def validate_output(value, plan, *, context):
    """Rehash every retained slot, ordinary PR8 row and live input receipt.

    Result is capture completion only. All-four drain is a later existing owner
    terminal gate, and parser/application/semantic evidence remains UNBOUND.
    """
    q = plan['client']['quality_pilot']; corpus = validate_corpus(q['corpus'])
    validate_context(context, plan)
    root = Path(plan['client']['output']).with_name('quality-capture')
    summary_raw = read_owned_bytes(root / 'quality-capture.json'); summary = C.strict_json(summary_raw)
    identity = C.strict_json(read_owned_bytes(root / 'identity.json'))
    require(summary.get('schema') == C.SCHEMA and summary['identity'] == identity
        and all(identity[k] == plan[k] for k in ('window_id', 'attempt_id', 'lane_id', 'plan_sha256', 'selection'))
        and identity['material_sha256'] == C.MATERIAL_SHA256 and identity['prompts_sha256'] == C.PROMPTS_SHA256,
        'quality capture task identity differs')
    ctx = identity['context']
    require(ctx['runtime_receipt_sha256'] == digest(canonical(context))
        and ctx['tokenizer_identity_sha256'] == digest(canonical(corpus['tokenizer_identity']))
        and ctx['decode_identity_sha256'] == digest(canonical(corpus['default_rendering']))
        and ctx['observed_loaded_state'] == context['observed_loaded_state']
        and ctx['final_answer_extraction'] == EXTRACTION
        and ctx['applied_state_evidence'] == {'status':'UNVERIFIED','requested_state':plan['selection']['steering']},
        'quality capture runtime/input/default binding differs')
    require(identity['transport_bounds'] == {'response_bytes': q['max_response_bytes'],
        'header_bytes': q['max_header_bytes'], 'request_seconds': q['request_seconds']}
        and summary['transport_bounds'] == identity['transport_bounds'],
        'quality retained capsule transport bounds differ')
    require(digest(read_owned_bytes(root / 'prompts.jsonl')) == C.PROMPTS_SHA256,
        'quality retained prompt bytes differ')
    require(summary['exit_code'] == 0 and summary['capture_error'] is None
        and summary['ordinary_result_sha256'] == digest(read_owned_bytes(plan['client']['output']))
        and summary['execution_evidence'] == 'CAPTURE_ONLY_NOT_EXECUTION_VALIDATED'
        and summary['semantic_scoring'] == 'NOT_EVALUATED', 'quality capture incomplete or relabelled')
    require(value['scores'] is None and value['score_seconds'] is None and value['corpus']['source'] == 'self'
        and value['server'] == {'url': plan['url'], 'model': C.MODEL}, 'quality ordinary PR8 source/scoring differs')
    prompts = [C.strict_json(line) for line in read_owned_bytes(root / 'prompts.jsonl').splitlines()]
    work = value['workload']
    require(work['prompts'] == str(root / 'prompts.jsonl')
        and work['prompts_sha256'] == digest(json.dumps(prompts, sort_keys=True).encode())
        and type(work['prompt_count']) is int and work['prompt_count'] == 60 and type(work['concurrency']) is int and work['concurrency'] == 1
        and type(work['max_tokens']) is int and work['max_tokens'] == 2048 and work['fixed_length'] is False
        and canonical(work['extra_body']) == canonical(C.extra_body(plan['selection']['steering'])),
        'quality ordinary PR8 workload differs')
    records, rows = summary['records'], value['generation']['sequences']
    index_rows = C.strict_json(read_owned_bytes(root / 'case-index.json'))
    require(len(records) == len(rows) == len(index_rows) == 60, 'quality denominator incomplete')

    def raw_response(record, expected):
        response = record['response']
        require(response['raw_path'] == expected and response['complete'] is True
            and response['overflow'] is False and response['headers_overflow'] is False
            and response['parse_error'] is None and len(canonical(response['headers'])) <= q['max_header_bytes']
            and type(response['http_status']) is int and 200 <= response['http_status'] < 300, 'quality response invalid/partial/overflow')
        headers = response['headers']
        require(isinstance(headers, list) and all(isinstance(h, list) and len(h) == 2
            and all(isinstance(x, str) for x in h) for h in headers), 'quality headers malformed')
        raw = read_owned_bytes(root / expected)
        lengths = [v for k, v in headers if k.lower() == 'content-length']
        require(not lengths or (len(lengths) == 1 and lengths[0].isdigit() and int(lengths[0]) == len(raw)),
            'quality retained Content-Length differs')
        require(len(raw) == response['raw_bytes'] <= q['max_response_bytes'] and digest(raw) == response['raw_sha256'],
            'quality raw response changed')
        return raw

    tokens = C.strict_json(read_owned_bytes(root / 'live-tokenization.json'))
    require(len(tokens) == 60 and summary['live_tokenization'] == tokens, 'quality live input denominator differs')
    salts = set()
    for i, (case, record, row, initial, token) in enumerate(zip(corpus['cases'], records, rows, index_rows, tokens)):
        require(record == C.strict_json(read_owned_bytes(root / f'record-{i:03d}.json'))
            and type(record['index']) is int and record['index'] == i and record['case_id'] == case['id'] and record['prompt_sha256'] == case['prompt_sha256']
            and record['expected_input_ids'] == case['input_ids'] and record['expected_input_count'] == len(case['input_ids'])
            and record['expected_input_ids_sha256'] == digest(canonical(case['input_ids']))
            and record['semantic_scoring'] == 'NOT_EVALUATED' and record['error'] is None
            and record['outcome'] in ('COMPLETED', 'TRUNCATED'), 'quality immutable primary slot differs')
        require(initial['index'] == i and initial['case_id'] == case['id'] and initial['expected_input_ids'] == case['input_ids']
            and initial['outcome'] == 'NOT_RUN', 'quality immutable inventory differs')
        answer = C.strict_json(raw_response(record, f'response-{i:03d}.bin'))
        require(answer == record['response']['decoded'], 'quality decoded mirror differs')
        checker = object.__new__(C.Capture); checked = copy.deepcopy(record)
        checker._validate_response(answer, checked)
        require(checked == record, 'quality reconstructed response diagnostics differ')
        request = record['request']; before = C.strict_json(read_owned_bytes(root / f'request-{i:03d}.json'))
        require(before['request'] == request and before['case_id'] == case['id'], 'quality request receipt differs')
        payload = request['payload']; salt = payload.get('cache_salt')
        prompt = prompts[i]
        expected = dict(model=C.MODEL, messages=[{'role': 'user', 'content': prompt['text']}], max_tokens=2048,
                        return_token_ids=True, cache_salt=salt, **C.extra_body(plan['selection']['steering']))
        require(isinstance(salt, str) and salt and salt not in salts and canonical(payload) == canonical(expected)
            and request['endpoint'] == plan['url'] + '/v1/chat/completions'
            and 0 < request['timeout_seconds'] <= q['request_seconds'], 'quality wire request differs')
        salts.add(salt)
        import base64
        wire = base64.b64decode(request['wire_base64'], validate=True)
        require(canonical(C.strict_json(wire)) == canonical(payload) and request['wire_sha256'] == digest(wire)
            and request['semantic_sha256'] == digest(canonical({k:v for k,v in payload.items() if k != 'cache_salt'})),
            'quality request wire/hash differs')
        require(type(row['id']) is int and row == {'id': i, 'prompt_ids': case['input_ids'], 'output_ids': answer['choices'][0]['token_ids'],
            'finish_reason': answer['choices'][0]['finish_reason']}, 'quality ordinary PR8 row differs')
        require(token == C.strict_json(read_owned_bytes(root / f'tokenize-{i:03d}.json')) and token['index'] == i
            and token['case_id'] == case['id'] and token['input_ids_equal'] is True
            and token['endpoint'] == plan['url'] + '/tokenize'
            and token['payload'] == {'model': C.MODEL, 'messages': expected['messages']}, 'quality live-token receipt differs')
        before_token = C.strict_json(read_owned_bytes(root / f'tokenize-request-{i:03d}.json'))
        require(before_token['payload'] == token['payload'] and before_token['case_id'] == case['id']
            and before_token['request_sha256'] == token['request_sha256'] == digest(canonical(token['payload'])),
            'quality live-token request changed')
        live = C.strict_json(raw_response(token, f'tokenize-{i:03d}.bin'))
        require(live == token['response']['decoded'] and live['tokens'] == case['input_ids']
            and type(live['count']) is int and live['count'] == len(case['input_ids']), 'quality live-token IDs differ')
    require(summary['counts'] == {k: sum(r['outcome'] == k for r in records)
        for k in ('COMPLETED', 'TRUNCATED', 'ERROR', 'NOT_RUN')}, 'quality counts differ')
    metrics = summary['metrics']; require(len(metrics) == 5, 'quality metrics closure incomplete')
    for i, metric in enumerate(metrics):
        require(metric == C.strict_json(read_owned_bytes(root / f'metrics-{i:03d}.json'))
            and metric['index'] == i and metric['endpoint'] == plan['url'] + '/metrics', 'quality metrics identity differs')
        text = raw_response(metric, f'metrics-{i:03d}.bin').decode('utf-8')
        gauges = C.idle_gauges(text)
        if i == 0:
            require(gauges == [0, 0], 'quality initial idle evidence differs')
    return dict(quality_capture_sha256=digest(summary_raw), quality_counts=summary['counts'],
        execution_evidence='CAPTURE_COMPLETE_AWAITING_TERMINAL_DRAIN', semantic_scoring='NOT_EVALUATED',
        comparison_eligible=False, final_answer_extraction=copy.deepcopy(EXTRACTION),
        applied_state_evidence=ctx['applied_state_evidence'])
