#!/usr/bin/env python3
"""Finite active1 short screen on the existing owned HTTP lane, never acceptance.

The frozen PR8 CLI/load/client/report code still owns request and timing semantics.
Instrumentation retains native Prom text and unrounded load records, prevents new
requests after a hard failure, and bounds each cell inside the existing supervised
client lifetime. No scheduler, service, fan or model control is introduced here.
"""
from __future__ import annotations
import copy
from decimal import Decimal, localcontext
import math
import os
from pathlib import Path
import re
import signal
import sys
import threading
import time
import types

import q03_owner_model as owner
from q03_held_window import exclusive_json, read_owned_bytes

require, canonical, digest, json_bytes = owner.require, owner.canonical, owner.digest, owner.json_bytes
_Q_PATH = Path(__file__).with_name('q03_qualification.py')
_Q_RAW = read_owned_bytes(_Q_PATH)
require(digest(_Q_RAW) == 'f41e9fac23ee88defa71d6aed6dddaa6b0630b8c5e369f14b02c17ee2d242c1e',
        'unchanged schema2 qualification source differs')
Q = types.ModuleType('_q03_performance_corpus'); Q.__file__ = str(_Q_PATH)
exec(compile(_Q_RAW, str(_Q_PATH), 'exec'), Q.__dict__)
WORKLOAD = 'lean_active1_performance_screen_v1'
CONCURRENCIES = (1, 8, 16)
EXTRA = dict(Q.EXTRA, steering_profile='heretic-1017-attn-v1', steering_scale=1)
METRICS = ('vllm:request_success_total', 'vllm:generation_tokens_total',
           'vllm:prompt_tokens_total', 'vllm:num_preemptions_total',
           'vllm:num_requests_running', 'vllm:num_requests_waiting')
GAUGES = set(METRICS[-2:])
LABEL = re.compile(r'\s*([a-zA-Z_][a-zA-Z0-9_]*)=("(?:[^"\\\r\n]|\\["\\n])*")\s*(?:,|$)')
UNKNOWN_CONDITIONS = {'thermal': 'UNKNOWN', 'fan_observation': 'UNKNOWN',
                      'fan_policy': 'UNCHANGED', 'cooling': 'UNKNOWN'}


def quality_module():
    # The composed controller owns the reviewed source pin. No arbitrary import
    # path or caller-provided source digest may stand in for the Stage1 validator.
    import q03_http_service as H
    require(hasattr(H, 'quality_module'), 'reviewed Stage1b source composition is required')
    return H.quality_module()


def validate_stage1(pin, corpus_sha, binding=None):
    require(isinstance(pin, dict) and set(pin) == {'capsule', 'session'}, 'full Stage1 session binding required')
    root = Path(pin['session'])
    require(root.is_absolute() and root.resolve() == root, 'canonical Stage1 session required')
    capsule = owner.pinned_json(pin['capsule'])
    require(Path(pin['capsule']['path']) == root / 'capsule.json', 'Stage1 capsule/session differs')
    module = quality_module()
    result = module.compare_session(root)
    require(result.get('status') == 'TOKEN_ID_MATCH' and result.get('corpus_sha256') == corpus_sha,
            'all complete Stage1 controls/equality gates must pass')
    prior = capsule['manifest']['http_service']
    require(prior.get('schema_version') == 3, 'full Stage1b capsule required')
    if binding is not None:
        for key in ('profile', 'effective_config', 'nodes'):
            require(prior['owner_inputs'][key]['sha256'] == binding['http']['owner_inputs'][key]['sha256'],
                    'Stage1 qualification identity differs: ' + key)
        native = capsule['manifest']['candidate']
        require(all(native[k] == binding['candidate'][k]
                    for k in ('image', 'component_head', 'runtime_manifest_sha256')), 'Stage1 native identity differs')
    return result


def validate_binding(value, binding=None):
    require(isinstance(value, dict) and set(value) == {'workload', 'corpus', 'stage1', 'cell_seconds', 'metric_labels'}
            and value['workload'] == WORKLOAD, 'finite active1 screening binding differs')
    # The duration is fixed; this explicit external bound includes scrape/drain.
    require(type(value['cell_seconds']) is int and 41 <= value['cell_seconds'] <= 180,
            'reviewed per-cell external bound must be 41..180 seconds')
    labels = value['metric_labels']
    require(isinstance(labels, dict) and set(labels) == {'model_name', 'engine'}
            and labels['model_name'] == Q.MODEL and isinstance(labels['engine'], str)
            and re.fullmatch(r'[0-9]+', labels['engine']), 'registered native model/engine labels required')
    corpus = Q.validate_binding({'workload': Q.WORKLOAD, 'corpus': value['corpus']}, binding)
    validate_stage1(value['stage1'], value['corpus']['sha256'], binding)
    if binding is not None:
        require(binding['http']['client_seconds'] >= 3 * value['cell_seconds'] + 15,
                'client bound must cover all three external cell bounds plus receipt margin')
    return corpus


def client_plan(binding, selection):
    require(selection in ({'mode': 'off', 'steering': 'active'}, {'mode': 'dual', 'steering': 'active'}),
            'performance screen is active1 OFF/DUAL only')
    config = binding['http']['performance_screen']
    corpus = validate_binding(config, binding)
    identity = quality_module().state_identity(binding, 'active')
    return {'workload': WORKLOAD, 'corpus': corpus, 'corpus_sha256': config['corpus']['sha256'],
            'stage1_capsule_sha256': config['stage1']['capsule']['sha256'],
            'concurrencies': list(CONCURRENCIES), 'cell_seconds': config['cell_seconds'],
            'metric_labels': config['metric_labels'], 'state_identity': identity}


def argv(plan, concurrency, output):
    require(type(concurrency) is int and concurrency in CONCURRENCIES, 'unknown screen concurrency')
    q = plan['client']['performance_screen']
    return ['run', plan['url'], '--model', Q.MODEL, '--prompts', q['corpus']['prompts']['path'],
            '--mode', 'closed', '--concurrency', str(concurrency), '--context', '0', '--seed', '0',
            '--warmup', '10', '--duration', '30', '--batches', '3', '--max-tokens', '64',
            '--save-requests', '--extra-body', canonical(EXTRA).decode(),
            '--label', 'active1_c' + str(concurrency), '--output', str(output)]


def native_series(text, expected):
    """Reject unknown, reset or foreign counters; never turn missing into zero.

    Minimal exact-series rules derived from the reviewed paired-performance
    receipt reader (b867f9a4c464324bfff01eddf30fa154cdb5eb3e32ff9718318df808520b0663).
    This is inline validation, not a runtime dependency on that offline tool.
    """
    series = {}
    for line in text.splitlines():
        if not line or line.startswith('#'):
            continue
        name = re.split(r'[\s{]', line, maxsplit=1)[0]
        if name not in METRICS:
            continue
        match = re.fullmatch(r'([^\s{]+)\{(.*)\}[ \t]+([^\s]+)(?:[ \t]+([+-]?[0-9]+))?[ \t]*', line)
        require(match is not None, 'unlabeled/malformed required native metric')
        labels, pos = {}, 0
        while pos < len(match[2]):
            item = LABEL.match(match[2], pos)
            require(item is not None and item[1] not in labels, 'invalid/duplicate native labels')
            labels[item[1]] = re.sub(r'\\(["\\n])', lambda m: {'n': '\n', '\\': '\\', '"': '"'}[m[1]], item[2][1:-1])
            pos = item.end()
        require(all(labels.get(k) == v for k, v in expected.items()), 'foreign native model/engine series')
        require(re.fullmatch(r'[+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?', match[3]),
                'invalid native numeric spelling')
        number = Decimal(match[3])
        require(number.is_finite() and 0 <= number <= Decimal('1.7976931348623157e308')
                and number == number.to_integral_value(), 'invalid native counter/gauge')
        number = Decimal(int(number))
        if match[4] is not None:
            require(-(2 ** 63) <= int(match[4]) < 2 ** 63, 'native sample timestamp out of range')
        key = canonical([name, labels]).decode()
        require(key not in series, 'duplicate native metric series')
        series[key] = number
    require({json_bytes(key)[0] for key in series} == set(METRICS), 'missing required native metric')
    return series


def totals(series):
    with localcontext() as context:
        context.prec = 700
        return {name: sum(value for key, value in series.items() if json_bytes(key)[0] == name) for name in METRICS}


def validate_record(row):
    require(row.get('error') is None and not row.get('stopped') and row.get('token_times') == 'exact'
            and type(row.get('prompt_tokens')) is int and row['prompt_tokens'] == 2048
            and type(row.get('completion_tokens')) is int and row['completion_tokens'] == 64
            and row.get('finish_reason') == 'length', 'request failed or actual 2048/64 token/time contract differs')
    times = [row.get(k) for k in ('sent', 'first', 'end')]
    require(all(type(t) in (int, float) and math.isfinite(t) for t in times)
            and times[0] < times[1] < times[2], 'request head clock ordering differs')
    events = row.get('events')
    require(isinstance(events, list) and events
            and all(isinstance(e, list) and len(e) == 2 for e in events)
            and events[0][0] == times[1] and type(events[0][1]) is int and events[0][1] > 0
            and events[-1][1] == 64, 'complete exact chunk times required')
    previous_t, previous_n = times[1], 0
    for t, n in events:
        require(type(t) in (int, float) and math.isfinite(t) and previous_t <= t <= times[2]
                and type(n) is int and previous_n <= n <= 64, 'invalid/unordered token event')
        previous_t, previous_n = t, n


def validate_context(value, plan):
    require(isinstance(value, dict) and set(value) == {'window_id', 'attempt_id', 'plan_sha256', 'url', 'ranks', 'ready_sha256'},
            'owned runtime context fields differ')
    require(all(value[k] == plan[k] for k in ('window_id', 'attempt_id', 'plan_sha256', 'url')),
            'owned endpoint/attempt context differs')
    ranks = value['ranks']
    require(isinstance(ranks, list) and len(ranks) == 4, 'all-four boot container binding required')
    ids = set()
    for rank, row in enumerate(ranks):
        require(set(row) == {'rank', 'container_id', 'config_sha256'} and type(row['rank']) is int and row['rank'] == rank
                and all(isinstance(row[k], str) and re.fullmatch('[0-9a-f]{64}', row[k])
                        for k in ('container_id', 'config_sha256')), 'exact owned container/config identity required')
        ids.add(row['container_id'])
    require(len(ids) == 4 and re.fullmatch('[0-9a-f]{64}', value['ready_sha256']), 'duplicate container or missing readiness')
    return value


class ScreenStopped(RuntimeError):
    pass


def run_cell(plan, concurrency, main, context, previous_counters=None):
    """Fixed in-process instrumentation; the existing remote operation owns exit."""
    from servebench import client, load, prom
    q = plan['client']['performance_screen']
    output = Path(plan['client']['output']).with_name('active1_c' + str(concurrency) + '.json')
    evidence_path = output.with_suffix('.evidence.json')
    require(not output.exists() and not evidence_path.exists(), 'screen cell output already exists')
    stop, lock = threading.Event(), threading.RLock()
    evidence = {'concurrency': concurrency, 'context_sha256': digest(canonical(context)),
                'url': plan['url'], 'metrics_url': plan['url'] + '/metrics', 'head_boot_id':
                Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                'started_monotonic': time.monotonic(), 'requests': [], 'scrapes': [], 'raw_run': None,
                'conditions': UNKNOWN_CONDITIONS, 'error': None,
                'bound_scope': 'cell execution and validation; durable finalization remains inside supervised client bound'}
    deadline = evidence['started_monotonic'] + q['cell_seconds']
    original_stream, original_fetch, original_run = client.stream_chat, prom.fetch, load.run
    old_alarm, old_abort = signal.getsignal(signal.SIGALRM), signal.getsignal(signal.SIGUSR1)
    def abort(reason):
        with lock:
            first = not stop.is_set()
            stop.set()
            if evidence['error'] is None:
                evidence['error'] = str(reason)
        if threading.current_thread() is threading.main_thread():
            raise ScreenStopped(str(reason))
        if first:
            os.kill(os.getpid(), signal.SIGUSR1)
    def handler(signum, frame):
        abort('per-cell hard deadline reached' if signum == signal.SIGALRM else evidence['error'])
    def stream(url, payload, **kwargs):
        if stop.is_set():
            raise SystemExit()  # no new request after the first hard failure
        require(url == plan['url'], 'screen client endpoint differs')
        kwargs['stop'] = stop
        kwargs['timeout'] = min(kwargs.get('timeout', 900), max(.001, deadline - time.monotonic()))
        record = original_stream(url, payload, **kwargs)
        with lock:
            evidence['requests'].append(copy.deepcopy(record))
        if stop.is_set():
            raise SystemExit()
        try:
            validate_record(record)
        except Exception as exc:
            abort(exc)
        return record
    def fetch(url, timeout=10):
        if stop.is_set():
            raise SystemExit()
        begin = time.monotonic()
        text = original_fetch(url, min(timeout, max(.001, deadline - begin)))
        end = time.monotonic()
        row = {'begin_monotonic': begin, 'end_monotonic': end, 'text': text}
        with lock:
            evidence['scrapes'].append(row)
        if stop.is_set():
            raise SystemExit()
        try:
            require(url == plan['url'] and isinstance(text, str), 'missing native Prom snapshot/endpoint')
            current = native_series(text, q['metric_labels'])
            with lock:
                previous = evidence.get('_last_series')
                if previous is not None:
                    require(set(current) == set(previous), 'native metric series inventory changed')
                    require(all(value >= previous[k] for k, value in current.items() if json_bytes(k)[0] not in GAUGES),
                            'native counter reset/negative delta')
                    require(totals(current)[METRICS[3]] == totals(previous)[METRICS[3]], 'native preemption observed')
                else:
                    require(all(totals(current)[name] == 0 for name in GAUGES), 'foreign load before cell')
                    require(previous_counters is None or current == previous_counters,
                            'native counter change/reset/foreign load between cells')
                evidence['_last_series'] = current
        except Exception as exc:
            abort(exc)
        return text
    def run(*args, **kwargs):
        result = original_run(*args, **kwargs)
        evidence['raw_run'] = result  # unrounded PR8 request, window and fence clocks
        return result
    code = 1
    signal.signal(signal.SIGALRM, handler); signal.signal(signal.SIGUSR1, handler)
    client.stream_chat, prom.fetch, load.run = stream, fetch, run
    signal.setitimer(signal.ITIMER_REAL, q['cell_seconds'])
    try:
        code = main(argv(plan, concurrency, output))
        require(type(code) is int and code == 0, 'servebench cell failed')
        validate_cell(json_bytes(read_owned_bytes(output)), evidence, plan, concurrency)
    except Exception as exc:
        stop.set()
        evidence['error'] = evidence['error'] or f'{type(exc).__name__}: {exc}'
        code = 1
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        # Failure returns directly to the fixed client process, which exits;
        # daemon streams cannot launch more requests because the latch stays set.
        if code == 0:
            client.stream_chat, prom.fetch, load.run = original_stream, original_fetch, original_run
        signal.signal(signal.SIGALRM, old_alarm); signal.signal(signal.SIGUSR1, old_abort)
        evidence['ended_monotonic'] = time.monotonic()
        with lock:
            evidence.pop('_last_series', None)
            exclusive_json(evidence_path, evidence)
    return {'concurrency': concurrency, 'exit_code': code,
            'output_sha256': digest(read_owned_bytes(output)) if output.exists() else None,
            'evidence_sha256': digest(read_owned_bytes(evidence_path))}


def frozen_summary(raw, plan, native_texts):
    """Use the same pinned PR8 reporting code, not trusted editable summaries."""
    package = '_q03_screen_report_source'
    names = ('prom', 'stats', 'client', 'report')
    require(not any(n == package or n.startswith(package + '.') for n in sys.modules),
            'private reporting source namespace already loaded')
    root = Path(plan['client']['toolbox_path'])
    files = plan['client']['toolbox_files']
    module = types.ModuleType(package); module.__path__ = []
    sys.modules[package] = module
    try:
        for name in names:
            relative = 'servebench/' + name + '.py'
            code = read_owned_bytes(root / relative)
            require(digest(code) == files[relative], 'frozen report source changed')
            child = types.ModuleType(package + '.' + name); child.__package__ = package
            sys.modules[child.__name__] = child; setattr(module, name, child)
            exec(compile(code, str(root / relative), 'exec'), child.__dict__)
        parsed = [module.prom.parse(text) for text in native_texts]
        return module.report.summarize(raw), [{key: series[name] for key, name in module.prom.VLLM.items()
                                              if name in series} for series in parsed]
    finally:
        for name in (*names, None):
            sys.modules.pop(package + '.' + name if name else package, None)


def validate_cell(value, evidence, plan, concurrency):
    q = plan['client']['performance_screen']; raw = evidence.get('raw_run')
    expected = {'mode': 'closed', 'concurrency': concurrency, 'rate': 1.0, 'warmup_s': 10.0,
                'duration_s': 30.0, 'batches': 3, 'max_tokens': 64, 'fixed_length': True,
                'seed': 0, 'max_inflight': 512, 'context_tokens': 0,
                'prompts': q['corpus']['prompts']['path'], 'prompt_count': 16,
                'prompts_sha256': q['corpus']['prompts_sha256'], 'extra_body': EXTRA}
    require(value.get('schema') == 'servebench/1' and canonical(value.get('workload')) == canonical(expected)
            and value.get('label') == 'active1_c' + str(concurrency)
            and value.get('server') == {'url': plan['url'], 'model': Q.MODEL, 'adapter': 'vllm'},
            'screen workload/client/server identity differs')
    require(evidence.get('error') is None and isinstance(raw, dict), 'complete unrounded raw run required')
    require(isinstance(evidence.get('head_boot_id'), str) and
            re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', evidence['head_boot_id']),
            'observed head clock boot UUID required')
    start = raw['start']; expected_bounds = [start + 10 + i * 10 for i in range(4)]
    require(raw['window'] == [start + 10, start + 40] and raw['boundaries'] == expected_bounds
            and len(raw['scrapes']) == len(raw['scrape_times']) == 4 and raw['rejected'] == 0,
            'actual PR8 10+30/three-window timing differs')
    records = raw['records']
    require(records and len(records) == len(value.get('requests', [])) == len(evidence['requests']), 'missing raw requests')
    require(sorted(canonical(r) for r in evidence['requests']) == sorted(canonical(
                {k: v for k, v in row.items() if k not in ('prompt_id', 'slot', 'index')}) for row in records),
            'retained stream records differ from raw PR8 records')
    rounded = [{k: (round(v - start, 4) if k in ('sent', 'first', 'end') and v is not None else v)
                for k, v in row.items() if k != 'events'} |
               {'events': [[round(t - start, 4), n] for t, n in row['events']]} for row in records]
    require(canonical(value['requests']) == canonical(rounded), 'CLI request record projection differs')
    require(sorted(r.get('index') for r in records if type(r.get('index')) is int) == list(range(len(records)))
            and {r.get('slot') for r in records} == set(range(concurrency)), 'request indices/worker inventory differs')
    for row in records:
        validate_record(row)
        require(start <= row['sent'] < raw['window'][1], 'request outside PR8 issuing interval')
        require(type(row.get('prompt_id')) is int and 0 <= row['prompt_id'] < 16
                and type(row.get('slot')) is int and 0 <= row['slot'] < concurrency,
                'foreign prompt/worker record')
    scrapes = evidence['scrapes']
    require(len(scrapes) == 6, 'two fences and four native boundary snapshots required')
    series = [native_series(row['text'], q['metric_labels']) for row in scrapes]
    for i, row in enumerate(scrapes):
        require(evidence['started_monotonic'] <= row['begin_monotonic'] <= row['end_monotonic'], 'native head bracket differs')
        if i:
            require(scrapes[i-1]['end_monotonic'] <= row['begin_monotonic'] and set(series[i]) == set(series[i-1])
                    and all(v >= series[i-1][k] for k, v in series[i].items() if json_bytes(k)[0] not in GAUGES),
                    'native snapshots reordered/reset')
    require(evidence['started_monotonic'] <= raw['fences']['times'][0] <= scrapes[0]['begin_monotonic']
            and scrapes[0]['end_monotonic'] <= start
            and raw['window'][1] <= raw['fences']['times'][1] <= scrapes[-1]['begin_monotonic'], 'raw/native fence clocks differ')
    for i, nominal in enumerate(expected_bounds):
        require(nominal <= raw['scrape_times'][i] <= scrapes[i+1]['begin_monotonic'],
                'raw/native timed boundary clocks differ')
    first, last = totals(series[0]), totals(series[-1])
    with localcontext() as context:
        context.prec = 700
        require(all(first[n] == last[n] == 0 for n in GAUGES)
                and last[METRICS[0]] - first[METRICS[0]] == len(records)
                and last[METRICS[1]] - first[METRICS[1]] == len(records) * 64
                and last[METRICS[2]] - first[METRICS[2]] == len(records) * 2048
                and last[METRICS[3]] == first[METRICS[3]], 'foreign load, preemption, reset or incomplete native counts')
    require(scrapes[0]['end_monotonic'] <= min(r['sent'] for r in records)
            and max(r['end'] for r in records) <= scrapes[-1]['begin_monotonic'], 'native fences do not enclose all requests')
    summary = value['summary']
    computed, parsed = frozen_summary(raw, plan, [s['text'] for s in scrapes])
    require(canonical(raw['scrapes']) == canonical(parsed[1:-1])
            and canonical(raw['fences']['scrapes']) == canonical([parsed[0], parsed[-1]]),
            'raw parsed metrics differ from retained native snapshots')
    require(canonical(summary) == canonical(computed), 'saved summary differs from pinned raw recomputation')
    require(all(summary['requests'].get(k) == v for k, v in
                (('sent', len(records)), ('failed', 0), ('foreign', 0), ('rejected', 0), ('approximate_token_times', 0))),
            'screen summary request counters differ')
    require(summary.get('output_tps', {}).get('n') == 3, 'three subwindows required, not acceptance evidence')
    return True


def run_client(plan, main):
    q = plan['client']['performance_screen']; Q.validate_corpus(q['corpus'])
    require(q['workload'] == WORKLOAD and q['concurrencies'] == list(CONCURRENCIES)
            and digest(canonical(q['corpus'])) == q['corpus_sha256'], 'frozen screen client inventory differs')
    output = Path(plan['client']['output'])
    context = validate_context(json_bytes(read_owned_bytes(plan['client']['runtime_context'])), plan)
    cells, previous = [], None
    for concurrency in CONCURRENCIES:
        cell = run_cell(plan, concurrency, main, context, previous)
        cells.append(cell)
        if cell['exit_code'] != 0:
            break
        path = output.with_name('active1_c' + str(concurrency) + '.evidence.json')
        previous = native_series(json_bytes(read_owned_bytes(path))['scrapes'][-1]['text'], q['metric_labels'])
    result = {'workload': WORKLOAD, 'window_id': plan['window_id'], 'attempt_id': plan['attempt_id'],
              'plan_sha256': plan['plan_sha256'], 'selection': plan['selection'],
              'corpus_sha256': q['corpus_sha256'], 'state_identity': q['state_identity'],
              'stage1_capsule_sha256': q['stage1_capsule_sha256'], 'toolbox_head': plan['client']['toolbox_head'],
              'runtime_context_sha256': digest(canonical(context)), 'cells': cells,
              'capacity': 'INCONCLUSIVE', 'performance': 'INCONCLUSIVE', 'conditions': UNKNOWN_CONDITIONS}
    exclusive_json(output, result)
    return 0 if len(cells) == 3 and all(row['exit_code'] == 0 for row in cells) else 1


def validate_output(value, plan):
    q = plan['client']['performance_screen']; output = Path(plan['client']['output'])
    context = validate_context(json_bytes(read_owned_bytes(plan['client']['runtime_context'])), plan)
    expected = {'workload': WORKLOAD, 'window_id': plan['window_id'], 'attempt_id': plan['attempt_id'],
                'plan_sha256': plan['plan_sha256'], 'selection': plan['selection'],
                'corpus_sha256': q['corpus_sha256'], 'state_identity': q['state_identity'],
                'stage1_capsule_sha256': q['stage1_capsule_sha256'], 'toolbox_head': plan['client']['toolbox_head'],
                'runtime_context_sha256': digest(canonical(context)),
                'capacity': 'INCONCLUSIVE', 'performance': 'INCONCLUSIVE', 'conditions': UNKNOWN_CONDITIONS}
    require(set(value) == set(expected) | {'cells'} and all(canonical(value[k]) == canonical(v) for k, v in expected.items()),
            'screen receipt identity/verdict differs')
    cells = value['cells']
    require(isinstance(cells, list) and len(cells) == 3, 'complete three-cell screen required')
    previous = None
    for row, concurrency in zip(cells, CONCURRENCIES):
        require(set(row) == {'concurrency', 'exit_code', 'output_sha256', 'evidence_sha256'}
                and type(row['concurrency']) is int and row['concurrency'] == concurrency
                and type(row['exit_code']) is int and row['exit_code'] == 0, 'cell failed/order differs')
        path = output.with_name('active1_c' + str(concurrency) + '.json')
        raw, ev = read_owned_bytes(path), read_owned_bytes(path.with_suffix('.evidence.json'))
        require(digest(raw) == row['output_sha256'] and digest(ev) == row['evidence_sha256'], 'screen raw artifact changed')
        evidence = json_bytes(ev)
        require(evidence['context_sha256'] == value['runtime_context_sha256']
                and evidence['url'] == plan['url'] and evidence['metrics_url'] == plan['url'] + '/metrics'
                and 0 < evidence['ended_monotonic'] - evidence['started_monotonic'] <= q['cell_seconds']
                and all(s['end_monotonic'] <= evidence['ended_monotonic'] for s in evidence['scrapes']),
                'screen endpoint/boot/external time bound differs')
        if previous is not None:
            require(evidence['head_boot_id'] == previous['head_boot_id']
                    and previous['ended_monotonic'] <= evidence['started_monotonic'],
                    'head clock reboot or overlapping cells')
            require(native_series(previous['scrapes'][-1]['text'], q['metric_labels']) ==
                    native_series(evidence['scrapes'][0]['text'], q['metric_labels']),
                    'native counter change/reset/foreign load between cells')
        validate_cell(json_bytes(raw), evidence, plan, concurrency)
        previous = evidence
    return {'screen_status': 'COMPLETE_SCREEN_ONLY', 'capacity': 'INCONCLUSIVE', 'performance': 'INCONCLUSIVE',
            'qualification': 'active1 2048-input/64-output c1,c8,c16 feasibility screen only; no noninferiority or capacity acceptance'}


def verify_runtime_context(directory, plan):
    directory = Path(directory)
    context = validate_context(json_bytes(read_owned_bytes(plan['client']['runtime_context'])), plan)
    require(Path(plan['client']['runtime_context']) == Path(plan['client']['output']).with_name('runtime-context.json'),
            'runtime context is not the admitted fixed result location')
    journal = json_bytes(read_owned_bytes(directory / 'http' / 'state.json'))
    expected = [{k: r[k] for k in ('rank', 'container_id', 'config_sha256')} for r in journal['ranks']]
    require(context['ranks'] == expected and context['ready_sha256'] ==
            digest(read_owned_bytes(directory / 'http' / 'ready-observed.json')),
            'client runtime context differs from actual owned journal/ready evidence')
    return context


def read_drained(directory):
    """Read only the complete owned admission/history/client/drain evidence chain."""
    from q03_window import Session, RuntimeOps
    from q03_held_window import verify_outer, verify_history
    from q03_http_service import verify_plan_identity
    directory = Path(directory)
    session = Session(directory.parent.parent, RuntimeOps())
    verify_outer(session, require_hold=False); verify_history(session)
    require(directory.name in session.state.get('attempt_receipt_sha256', {}), 'screen attempt missing from history')
    plan = json_bytes(read_owned_bytes(directory / 'http' / 'plan.json'))
    verify_plan_identity(session, plan)
    descriptor = json_bytes(read_owned_bytes(directory / 'descriptor.json'))
    terminal_raw = read_owned_bytes(directory / 'terminal.json'); terminal = json_bytes(terminal_raw)
    require(digest(terminal_raw) == session.state['attempt_receipt_sha256'][directory.name]
            and terminal.get('outcome') == 'passed' and terminal.get('passed_gates') == ['http_service']
            and terminal['attempt_id'] == descriptor['attempt_id'] == plan['attempt_id'] == directory.name
            and terminal['window_id'] == descriptor['window_id'] == plan['window_id']
            and terminal['descriptor_sha256'] == digest(canonical(descriptor))
            and terminal['command_plan_sha256'] == descriptor['command_plan_sha256']
            and descriptor['jobs'] == [dict(session.manifest['jobs'][0], selection=plan['selection'])],
            'screen admission/terminal identity differs')
    drain = terminal.get('quiescence', {})
    require(drain.get('quiescent') is True and type(drain.get('node_count')) is int and drain['node_count'] == 4
            and drain.get('attempt_id') == plan['attempt_id'] and drain.get('phase') in ('stopped', 'restore_drained'),
            'all-four screen service drain required before another arm')
    path = Path(plan['client']['output']); raw = read_owned_bytes(path)
    receipt = json_bytes(read_owned_bytes(path.with_name('client-receipt.json')))
    require(all(receipt.get(k) == plan[k] for k in ('window_id', 'attempt_id', 'plan_sha256'))
            and type(receipt.get('exit_code')) is int and receipt['exit_code'] == 0
            and receipt.get('error') is None and receipt.get('output_sha256') == digest(raw),
            'screen fixed-client receipt differs')
    require(plan['binding']['http'] == session.manifest['http_service']
            and canonical(plan['client']['performance_screen']) == canonical(client_plan(plan['binding'], plan['selection'])),
            'retained screen differs from admitted Stage1/corpus/native binding')
    value = json_bytes(raw); validate_output(value, plan)
    verify_runtime_context(directory, plan)
    return plan, value, descriptor['capsule_sha256']


def next_selection(session):
    rows = session.state.get('attempt_results', [])
    require(all(r.get('outcome') == 'passed' and r.get('passed_gates') == ['http_service'] for r in rows),
            'screen stops after the first failed attempt; no automatic retry or advance')
    require(len(rows) < 2, 'one OFF/DUAL screen pair is complete')
    if rows:
        plan, _, _ = read_drained(session.directory / 'attempts' / rows[0]['attempt_id'])
        require(plan['selection'] == {'mode': 'off', 'steering': 'active'}, 'screen predecessor differs')
    return {'mode': 'dual' if rows else 'off', 'steering': 'active'}


def compare_session(directory):
    """Point-estimate flags only. Three within-run windows are not boot samples."""
    directory = Path(directory)
    state = json_bytes(read_owned_bytes(directory / 'state.json'))
    rows = state['attempt_results']
    require(len(rows) == 2 and all(r.get('outcome') == 'passed' and r.get('passed_gates') == ['http_service'] for r in rows),
            'exact complete clean OFF/DUAL pair required; no cherry-picked attempts')
    attempts = [read_drained(directory / 'attempts' / r['attempt_id']) for r in rows]
    (op, off, oc), (dp, dual, dc) = attempts
    require(oc == dc and op['window_id'] == dp['window_id'] and op['attempt_id'] != dp['attempt_id']
            and op['selection'] == {'mode': 'off', 'steering': 'active'}
            and dp['selection'] == {'mode': 'dual', 'steering': 'active'}, 'screen pair capsule/order differs')
    contexts = [verify_runtime_context(directory / 'attempts' / p['attempt_id'], p) for p in (op, dp)]
    require(not ({r['container_id'] for r in contexts[0]['ranks']} &
                 {r['container_id'] for r in contexts[1]['ranks']}), 'OFF/DUAL service boot IDs overlap')
    for key in ('corpus_sha256', 'state_identity', 'stage1_capsule_sha256', 'toolbox_head'):
        require(canonical(off[key]) == canonical(dual[key]), 'screen arm identity differs: ' + key)
    cells = []
    for c in CONCURRENCIES:
        name = 'active1_c' + str(c) + '.json'
        a, b = [json_bytes(read_owned_bytes(Path(p['client']['output']).with_name(name)))['summary'] for p in (op, dp)]
        point = {'concurrency': c, 'flags': [], 'within_run_windows': [a['output_tps']['batches'], b['output_tps']['batches']]}
        metrics = [('output_tps', a['output_tps']['mean'], b['output_tps']['mean'], -3)]
        metrics += [(f'{kind}_{pct}', a['latency'][kind].get(pct), b['latency'][kind].get(pct), 5)
                    for kind in ('ttft_s', 'tpot_ms') for pct in ('p50', 'p99')]
        point['point_estimates'] = {}
        for name, left, right, margin in metrics:
            valid = all(type(v) in (int, float) and math.isfinite(v) and v > 0 for v in (left, right))
            change = 100 * (right / left - 1) if valid else None
            point['point_estimates'][name] = {'off': left, 'dual': right, 'change_percent': change}
            if change is None or (change < margin if name == 'output_tps' else change > margin):
                point['flags'].append(name + (': UNKNOWN' if change is None else ': investigate'))
        cells.append(point)
    return {'status': 'SCREEN_COMPLETE_NOT_ACCEPTANCE', 'capacity': 'INCONCLUSIVE', 'performance': 'INCONCLUSIVE',
            'conditions': UNKNOWN_CONDITIONS, 'cells': cells, 'independent_boot_pairs': 1,
            'next_window': 'NOT_SCHEDULED', 'qualification': 'no long-prefill, capacity, 256-output or noninferiority claim'}
