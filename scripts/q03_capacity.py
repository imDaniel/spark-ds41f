#!/usr/bin/env python3
"""Only the finite c4 x (64512 input + 1024 output) HTTP capacity producer.

No tokenizer, runtime launcher, arbitrary argv, allocator RPC or offline PASS.
The existing HTTP owner supplies admission, deadlines and descendant/rank drain.
"""
from __future__ import annotations

import argparse
import copy
from decimal import Decimal, localcontext
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request

import q03_qualification as Q

require, canonical, digest, json_bytes = Q.require, Q.canonical, Q.digest, Q.json_bytes
read_owned_bytes, exclusive_json = Q.read_owned_bytes, Q.exclusive_json
WORKLOAD = 'capacity_64k_c4'
INPUT_TOKENS, OUTPUT_TOKENS, REQUESTS = 64512, 1024, 4
CAPACITY = {'max_model_len': 1048576, 'max_num_seqs': 16,
            'max_num_batched_tokens': 8192, 'kv_cache_memory_bytes': 11274289152}
EXPORT_FIELDS = {'schema_version', 'workload', 'tokenizer_mode', 'identity', 'artifacts', 'request', 'rows'}
UNKNOWN = {key: 'UNKNOWN' for key in ('native_c4_admission', 'full_residency',
    'allocation_retries', 'oom_health', 'driver_errors', 'per_rank_memory_minima',
    'swap_movement', 'common_clock')}
METRICS = {'running': 'vllm:num_requests_running', 'waiting': 'vllm:num_requests_waiting',
           'finished': 'vllm:request_success_total', 'preemptions': 'vllm:num_preemptions_total',
           'input_tokens': 'vllm:prompt_tokens_total', 'output_tokens': 'vllm:generation_tokens_total'}


# Strict native Prom parsing derived from PR19 523d10d40c6752c7b2c20145493b8785c4074183
# servebench/paired_performance.py. No runtime import of the offline validator.
LABEL = re.compile(r'[ \t]*([a-zA-Z_][a-zA-Z0-9_]*)=("(?:[^"\\\r\n]|\\["\\n])*")[ \t]*(?:,|$)')
COUNTER_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
TIMESTAMP = re.compile(r"[+-]?[0-9]+\Z")
MAX_NATIVE_COUNTER = Decimal.from_float(float.fromhex("0x1.fffffffffffffp+1023"))


def validate_export(value):
    require(isinstance(value, dict) and set(value) == EXPORT_FIELDS
            and type(value['schema_version']) is int and value['schema_version'] == 1
            and value['workload'] == WORKLOAD and value['tokenizer_mode'] == 'deepseek_v41',
            'first capacity export schema differs')
    Q.validate_identity(value['identity']); Q.artifact_files(value)
    require(canonical(value['request']) == canonical(Q.EXTRA), 'capacity template/generation defaults differ')
    rows = value['rows']
    require(isinstance(rows, list) and len(rows) == REQUESTS, 'exactly four real capacity prompts required')
    for index, row in enumerate(rows):
        require(isinstance(row, dict) and set(row) == {'id', 'text', 'prompt_ids'}
                and type(row['id']) is int and row['id'] == index
                and isinstance(row['text'], str) and bool(row['text'])
                and Q.token_ids(row['prompt_ids'], INPUT_TOKENS),
                'ordered fully templated 64512-token prompt required; no sizing or clipping')
    require(len({r['text'] for r in rows}) == len({tuple(r['prompt_ids']) for r in rows}) == REQUESTS,
            'four distinct texts and frozen input-ID vectors required')
    return value


def prepare(export_path, directory):
    """Copy a supplied real native export, never derive token IDs or resize text."""
    raw = read_owned_bytes(export_path)
    value = validate_export(json_bytes(raw)); Q.verify_artifacts(value)
    directory = Path(directory)
    directory.mkdir(mode=0o700, exist_ok=False)
    source_path = directory / 'native-export.json'
    write_raw(source_path, raw)
    prompts = [{'id': row['id'], 'text': row['text']} for row in value['rows']]
    prompt_raw = b''.join(canonical(row) + b'\n' for row in prompts)
    path = directory / 'prompts.jsonl'; write_raw(path, prompt_raw)
    corpus = dict(value, source_export={'path': str(source_path), 'sha256': digest(raw)},
                  prompts={'path': str(path), 'sha256': digest(prompt_raw)},
                  prompts_sha256=Q.servebench_prompt_digest(prompts))
    path = directory / 'corpus.json'
    return {'path': str(path), 'sha256': exclusive_json(path, corpus)}


def write_raw(path, raw):
    with Path(path).open('xb') as stream:
        os.chmod(path, 0o600)
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())


def validate_corpus(value):
    require(isinstance(value, dict) and set(value) == EXPORT_FIELDS | {
        'source_export', 'prompts', 'prompts_sha256'}, 'capacity corpus fields differ')
    export = validate_export({key: value[key] for key in EXPORT_FIELDS})
    source = Q.owner.pinned_json(value['source_export'])
    require(canonical(source) == canonical(export), 'retained native export differs')
    pin = value['prompts']; raw = read_owned_bytes(pin['path'])
    require(set(pin) == {'path', 'sha256'} and digest(raw) == pin['sha256'], 'raw prompt JSONL differs')
    prompts = [json_bytes(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
    expected = [{'id': row['id'], 'text': row['text']} for row in export['rows']]
    require(canonical(prompts) == canonical(expected)
            and Q.servebench_prompt_digest(prompts) == value['prompts_sha256'], 'parsed prompt identity differs')
    return value


def validate_binding(value, binding=None):
    require(isinstance(value, dict) and set(value) == {'workload', 'corpus', 'metric_labels'}
            and value['workload'] == WORKLOAD, 'only first finite capacity case supported')
    labels = value['metric_labels']
    require(isinstance(labels, dict) and set(labels) == {'model_name', 'engine'}
            and labels['model_name'] == Q.MODEL and isinstance(labels['engine'], str)
            and bool(re.fullmatch(r'[0-9]+', labels['engine'])),
            'independently frozen native model/engine-index labels required')
    corpus = validate_corpus(Q.owner.pinned_json(value['corpus']))
    require(digest(canonical(corpus)) == value['corpus']['sha256'], 'canonical capacity corpus required')
    Q.verify_artifacts(corpus, binding)
    return corpus


def next_selection(session):
    # No automatic sequence, expansion, favorable retry or continuation after failure.
    require(not session.state.get('attempt_results'), 'first capacity capsule already attempted; no continuation')
    return copy.deepcopy(session.manifest['jobs'][0]['selection'])


def request_extra(selection):
    Q.owner.validate_selection(selection)
    extra = copy.deepcopy(Q.EXTRA)
    if selection['steering'] != 'off':
        extra['vllm_xargs'] = {'steering_profile': 'heretic-1017-attn-v1',
                             'steering_scale': 0 if selection['steering'] == 'loaded_zero' else 1}
    return extra


def client_plan(binding, selection):
    corpus = validate_binding(binding['http']['capacity'], binding)
    return {'workload': WORKLOAD, 'corpus': corpus,
            'corpus_sha256': binding['http']['capacity']['corpus']['sha256'],
            'effective_capacity': CAPACITY, 'extra_body': request_extra(selection),
            'metric_labels': copy.deepcopy(binding['http']['capacity']['metric_labels'])}


def identity(plan):
    return {'workload': WORKLOAD, 'window_id': plan['window_id'], 'attempt_id': plan['attempt_id'],
            'plan_sha256': plan['plan_sha256'], 'spec_sha256': plan['spec_sha256'],
            'selection': plan['selection'], 'url': plan['url'], 'model': Q.MODEL,
            'corpus_sha256': plan['client']['capacity']['corpus_sha256'],
            'toolbox_head': plan['client']['toolbox_head'],
            'metric_labels': plan['client']['capacity']['metric_labels']}


def validate_client(plan):
    q = plan['client']['capacity']
    require(isinstance(q, dict) and set(q) == {'workload', 'corpus', 'corpus_sha256',
            'effective_capacity', 'extra_body', 'metric_labels'}, 'capacity client fields differ')
    labels = q['metric_labels']
    require(isinstance(labels, dict) and set(labels) == {'model_name', 'engine'}
            and labels['model_name'] == Q.MODEL and isinstance(labels['engine'], str)
            and bool(re.fullmatch(r'[0-9]+', labels['engine'])), 'capacity native metric label binding differs')
    corpus = validate_corpus(q['corpus'])
    require(q['workload'] == WORKLOAD and canonical(q['effective_capacity']) == canonical(CAPACITY)
            and canonical(q['extra_body']) == canonical(request_extra(plan['selection']))
            and digest(canonical(corpus)) == q['corpus_sha256'], 'finite capacity plan differs')
    return q


def argv(plan, output):
    q = validate_client(plan)
    return ['model', plan['url'], '--model', Q.MODEL, '--prompts', q['corpus']['prompts']['path'],
            '--limit', '4', '--concurrency', '4', '--max-tokens', '1024', '--fixed-length',
            '--no-score', '--extra-body', canonical(q['extra_body']).decode(),
            '--label', plan['attempt_id'], '--output', str(output)]


def validate_generation(value, plan):
    q = validate_client(plan); corpus = q['corpus']
    require(value.get('schema') == 'servebench-model/1' and value.get('label') == plan['attempt_id']
            and value.get('server') == {'url': plan['url'], 'model': Q.MODEL}
            and value.get('scores') is None and value.get('score_seconds') is None
            and value.get('self_agreement') is None, 'capacity generation/server/scoring differs')
    expected = {'prompts': corpus['prompts']['path'], 'prompt_count': REQUESTS,
                'prompts_sha256': corpus['prompts_sha256'], 'concurrency': REQUESTS,
                'max_tokens': OUTPUT_TOKENS, 'fixed_length': True, 'top': 20, 'extra_body': q['extra_body']}
    require(canonical(value.get('workload')) == canonical(expected), 'capacity workload differs')
    rows = value.get('generation', {}).get('sequences')
    require(isinstance(rows, list) and len(rows) == REQUESTS, 'exactly four completed requests required')
    seen = set()
    for row in rows:
        require(isinstance(row, dict) and set(row) == {'id', 'prompt_ids', 'output_ids', 'finish_reason'}
                and type(row['id']) is int and 0 <= row['id'] < REQUESTS and row['id'] not in seen,
                'errored, duplicate or foreign capacity request')
        require(row['prompt_ids'] == corpus['rows'][row['id']]['prompt_ids']
                and Q.token_ids(row['prompt_ids'], INPUT_TOKENS)
                and Q.token_ids(row['output_ids'], OUTPUT_TOKENS)
                and row['finish_reason'] == 'length', 'capacity token identity/count or finish differs')
        seen.add(row['id'])
    return {'requests': REQUESTS, 'input_tokens': REQUESTS * INPUT_TOKENS,
            'output_tokens': REQUESTS * OUTPUT_TOKENS, 'errors': 0}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError('capacity metrics endpoint redirect refused')


def snapshot(plan, label, deadline):
    """Retain actual HTTP bytes and local brackets. Never invent a common clock."""
    path = Path(plan['client']['output']).with_name('capacity-' + label + '.prom')
    url = plan['url'] + '/metrics'
    require(re.fullmatch(r'http://127\.0\.0\.1:[0-9]+/metrics', url) is not None,
            'capacity metrics must use the admitted exact loopback endpoint')
    start = time.monotonic_ns(); remaining = deadline - time.monotonic()
    raw, error, status = b'', None, None
    try:
        require(remaining > 0, 'capacity client deadline reached')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(url, timeout=min(10., remaining)) as response:
            status = response.status
            raw = response.read(8 * 1024 * 1024 + 1)
            require(response.geturl() == url and status == 200, 'capacity metrics HTTP identity differs')
            require(len(raw) <= 8 * 1024 * 1024, 'capacity metrics response too large')
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            raw = exc.read(8 * 1024 * 1024 + 1)
        finally:
            exc.close()
        error = f'{type(exc).__name__}: {exc}'
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
    end = time.monotonic_ns()
    write_raw(path, raw)
    return {'label': label, 'identity': identity(plan), 'endpoint': url, 'http_status': status,
            'clock': 'client-process-monotonic-ns', 'start_ns': start, 'end_ns': end,
            'raw': {'path': str(path), 'sha256': digest(raw)}, 'error': error}


def _counter_series(raw, names, expected_labels):
    """Keep exact series labels, including finish_reason; never silently sum foreign engines."""
    series = {}
    for line in raw.decode("utf-8").splitlines():
        line = line.strip()  # Exposition permits outer whitespace; never hide a foreign series.
        if not line or line.startswith("#"):
            continue
        name = re.split(r"[\s{]", line, maxsplit=1)[0]
        if name not in names.values():
            continue
        match = re.fullmatch(r"([^\s{]+)\{(.*)\}[ \t]+([^\s]+)(?:[ \t]+([^\s]+))?[ \t]*", line)
        require(match is not None, f"unlabeled/malformed required counter: {name}")
        labels, position = {}, 0
        while position < len(match[2]):
            label = LABEL.match(match[2], position)
            require(label is not None and label[1] not in labels, "invalid/duplicate native metric labels")
            labels[label[1]] = re.sub(r'\\(["\\n])', lambda m: {"n": "\n", "\\": "\\", '"': '"'}[m[1]], label[2][1:-1])
            position = label.end()
        require(all(labels.get(k) == v for k, v in expected_labels.items()), "foreign/missing engine/model counter labels")
        require(COUNTER_NUMBER.fullmatch(match[3]) is not None, "invalid native counter number")
        value = Decimal(match[3])
        require(value.is_finite() and 0 <= value <= MAX_NATIVE_COUNTER
                 and value == value.to_integral_value(), "missing/nonfinite/nonintegral native counter")
        value = Decimal(int(value))  # Exact integral normalization, including zero with a large exponent.
        if match[4] is not None:
            require(TIMESTAMP.fullmatch(match[4]) is not None, "invalid native sample timestamp")
            require(-(2 ** 63) <= int(match[4]) < 2 ** 63, "native sample timestamp out of range")
        key = (name, tuple(sorted(labels.items())))
        require(key not in series, "duplicate counter series")
        series[key] = value
    require(set(names.values()) == {key[0] for key in series}, "missing required counters")
    return series


def counters(snap, plan, label):
    path = Path(plan['client']['output']).with_name('capacity-' + label + '.prom')
    require(snap.get('label') == label and snap.get('identity') == identity(plan)
            and snap.get('endpoint') == plan['url'] + '/metrics'
            and type(snap.get('http_status')) is int and snap['http_status'] == 200 and snap.get('error') is None
            and snap.get('clock') == 'client-process-monotonic-ns'
            and type(snap.get('start_ns')) is int and type(snap.get('end_ns')) is int
            and 0 <= snap['start_ns'] <= snap['end_ns']
            and snap.get('raw', {}).get('path') == str(path), 'raw capacity scrape binding differs')
    raw = read_owned_bytes(path)
    require(digest(raw) == snap['raw']['sha256'], 'retained raw capacity scrape changed')
    series = _counter_series(raw, METRICS, plan['client']['capacity']['metric_labels'])
    with localcontext() as context:
        context.prec = max(v.adjusted() + 1 for v in series.values()) + len(str(len(series))) + 1
        samples = {key: sum((number for (metric, _), number in series.items() if metric == name), Decimal(0))
                   for key, name in METRICS.items()}
    return dict(samples, series=series)


def validate_output(value, plan):
    require(value.get('schema') == 'q03-first-capacity/1' and value.get('identity') == identity(plan)
            and canonical(value.get('effective_capacity')) == canonical(CAPACITY) and value.get('unknown') == UNKNOWN
            and value.get('capacity_status') == value.get('overall_status') == 'INCONCLUSIVE'
            and value.get('error') is None and type(value.get('exit_code')) is int and value['exit_code'] == 0,
            'capacity producer completion/identity differs')
    generation = value['generation']; path = Path(plan['client']['output']).with_name('capacity-generation.json')
    require(generation.get('path') == str(path), 'capacity generation path differs')
    raw = read_owned_bytes(path)
    require(digest(raw) == generation.get('sha256'), 'retained capacity generation changed')
    counts = validate_generation(json_bytes(raw), plan)
    require(canonical(value['counts']) == canonical(counts), 'capacity full counts differ')
    before, after = (counters(value['scrapes'][key], plan, key) for key in ('before', 'after'))
    require(value['scrapes']['before']['end_ns'] <= value['scrapes']['after']['start_ns'],
            'capacity scrape order differs')
    require(before['running'] == before['waiting'] == after['running'] == after['waiting'] == 0,
            'capacity endpoint not idle at both fences')
    require(set(before['series']) == set(after['series']), 'capacity metric series inventory changed')
    gauges = {METRICS[key] for key in ('running', 'waiting')}
    require(all(after['series'][key] >= count for key, count in before['series'].items()
                if key[0] not in gauges), 'capacity per-series counter reset')
    expected = {'finished': REQUESTS, 'preemptions': 0, 'input_tokens': REQUESTS * INPUT_TOKENS,
                'output_tokens': REQUESTS * OUTPUT_TOKENS}
    with localcontext() as context:
        context.prec = max(v.adjusted() + 1 for row in (before, after) for v in row['series'].values()) + len(str(len(before['series']))) + 1
        delta = {key: after[key] - before[key] for key in expected}
    require(delta == expected,
            'capacity traffic/preemption counters differ: reset, foreign work or inexact completion')
    return {'producer_status': 'FOUR_REQUESTS_COMPLETED', 'capacity_status': 'INCONCLUSIVE',
            'overall_status': 'INCONCLUSIVE', 'counts': counts, 'unknown': UNKNOWN}


def run_client(plan, main):
    """Existing immutable HTTP client calls this only inside its supervised epoch."""
    output = Path(plan['client']['output']); generation = output.with_name('capacity-generation.json')
    require(not any(p.exists() for p in (output, generation, output.with_name('capacity-before.prom'),
                                       output.with_name('capacity-after.prom'))), 'fresh capacity output required')
    args = argv(plan, generation)
    deadline = time.monotonic() + plan['client']['timeout_seconds']
    value = {'schema': 'q03-first-capacity/1', 'identity': identity(plan), 'effective_capacity': CAPACITY,
             'unknown': UNKNOWN, 'capacity_status': 'INCONCLUSIVE', 'overall_status': 'INCONCLUSIVE',
             'generation': None, 'scrapes': {}, 'counts': None, 'exit_code': 1, 'error': None}
    try:
        value['scrapes']['before'] = snapshot(plan, 'before', deadline)
        before = counters(value['scrapes']['before'], plan, 'before')
        require(before['running'] == before['waiting'] == 0, 'capacity endpoint not idle; no requests sent')
        code = main(args)
        require(type(code) is int and code == 0, 'capacity generation returned failure')
        value['counts'] = validate_generation(json_bytes(read_owned_bytes(generation)), plan)
        value['exit_code'] = 0
    except Exception as exc:
        value['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        value['scrapes']['after'] = snapshot(plan, 'after', deadline)
        if generation.is_file():
            raw = read_owned_bytes(generation)
            value['generation'] = {'path': str(generation), 'sha256': digest(raw)}
        if value['error'] is None:
            try:
                require(time.monotonic() <= deadline, 'capacity client deadline reached')
                validate_output(value, plan)
            except Exception as exc:
                value['error'] = f'{type(exc).__name__}: {exc}'
                value['exit_code'] = 1
        if value['error'] is not None:
            value['exit_code'] = 1
        exclusive_json(output, value)
    return value['exit_code']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export', required=True, help='genuine four-row native token export; never synthesized here')
    parser.add_argument('--output-directory', required=True)
    args = parser.parse_args()
    print(canonical(prepare(args.export, args.output_directory)).decode())


if __name__ == '__main__':
    main()
