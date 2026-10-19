"""Bounded synthetic API and fixed-workload probes on the loopback test server."""
import argparse
import concurrent.futures
import hashlib
import json
import math
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
ROOT = REPOSITORY / '.work/steering-gpu-qualification'
THERMAL_SOURCE = None
BASE = 'http://127.0.0.1:8004'
PROFILE = 'heretic-1017-attn-v1'
PROMPTS = [
    'Explain why a binary search needs a sorted input. Give a small example.\nAnswer:',
    'Write a short story about a courier delivering a parcel to a lighthouse.\nStory:',
    '请用简洁的中文解释为什么 1 到 100 的整数和等于 5050。\n回答：',
]


def call(path, body=None, timeout=180):
    req = urllib.request.Request(BASE + path)
    if body is not None:
        req = urllib.request.Request(
            BASE + path, data=json.dumps(body).encode(),
            headers={'Content-Type': 'application/json'}, method='POST',
        )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = response.read()
            return response.status, json.loads(data) if data.strip() else None
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def rpc(method, args=None, timeout=600):
    status, result = call('/collective_rpc', {'method': method, 'args': args or [], 'timeout': timeout}, timeout + 30)
    assert status == 200, (status, result)
    return result


def reset():
    status, result = call('/reset_prefix_cache', {}, timeout=120)
    assert status == 200, (status, result)


def thermal_record_ready(record, now):
    samples = record.get('samples', [])
    if len(samples) != 4 or any(type(s.get('node')) is not int for s in samples):
        return False
    if {s['node'] for s in samples} != {1, 2, 3, 4}:
        return False
    for sample in samples:
        stamp, temperature = sample.get('time'), sample.get('hottest_c')
        if any(type(v) not in (int, float) or not math.isfinite(v)
               for v in (stamp, temperature)):
            return False
        if not 0 <= now - stamp < 20 or not 0 <= temperature < 55:
            return False
    return True


def thermal_gate():
    """Use the retained firmware fan policy; do not install/change host modules."""
    if THERMAL_SOURCE is None:
        raise RuntimeError('Measured phases require --telemetry with four fresh host samples')
    deadline = time.monotonic() + 600
    while True:
        rows = THERMAL_SOURCE.read_text().splitlines()
        latest = json.loads(rows[-1])
        if thermal_record_ready(latest, time.time()):
            return latest
        if time.monotonic() >= deadline:
            raise RuntimeError('Thermal baseline unavailable; no measurement started')
        time.sleep(5)


def complete(prompt, extra=None, tokens=64):
    body = {
        'model': 'deepseek-v4.1-flash', 'prompt': prompt,
        'temperature': 0, 'seed': 2048, 'max_tokens': tokens,
        'ignore_eos': True, 'logprobs': 5, 'return_token_ids': True,
        'return_tokens_as_token_ids': True,
    }
    body.update(extra or {})
    start = time.perf_counter()
    status, data = call('/v1/completions', body)
    result = {'status': status, 'seconds': time.perf_counter() - start, 'response': data}
    # Retain every response before any caller can reject it during validation.
    folder = ROOT / 'evidence' / 'raw-completions'
    folder.mkdir(parents=True, exist_ok=True)
    receipt = folder / (uuid.uuid4().hex + '.json')
    with receipt.open('x') as out:
        json.dump(result, out, ensure_ascii=False, indent=2)
    result['raw_receipt'] = str(receipt)
    return result


def fingerprint(result, expected_tokens=64):
    """Hash a complete API observation, not internal activation tensors."""
    if result['status'] != 200:
        raise ValueError('Completion did not succeed')
    response = result['response']
    choices = response.get('choices')
    if not isinstance(choices, list) or len(choices) != 1:
        raise ValueError('Expected exactly one completion choice')
    choice = choices[0]
    ids = choice.get('token_ids')
    if (not isinstance(ids, list) or len(ids) != expected_tokens
            or any(type(token) is not int or token < 0 for token in ids)):
        raise ValueError('Missing or truncated output token IDs')
    usage = response.get('usage', {})
    if (type(usage.get('completion_tokens')) is not int
            or usage['completion_tokens'] != expected_tokens):
        raise ValueError('Completion usage does not match the requested length')
    if not isinstance(choice.get('text'), str) or choice.get('finish_reason') != 'length':
        raise ValueError('Expected text and a length-limited completion')
    logprobs = choice.get('logprobs')
    if not isinstance(logprobs, dict):
        raise TypeError('Missing output logprobs')
    for field in ('tokens', 'token_logprobs', 'top_logprobs', 'text_offset'):
        values = logprobs.get(field)
        if not isinstance(values, list) or len(values) != expected_tokens:
            raise ValueError('Missing or truncated logprobs: ' + field)
    numeric = lambda value: type(value) in (int, float) and math.isfinite(value)
    if (any(not isinstance(token, str) for token in logprobs['tokens'])
            or any(not numeric(v) for v in logprobs['token_logprobs'])
            or any(type(v) is not int or v < 0 for v in logprobs['text_offset'])):
        raise ValueError('Malformed token logprobs')
    for row in logprobs['top_logprobs']:
        if (not isinstance(row, dict) or len(row) < 5
                or any(not isinstance(k, str) or not numeric(v) for k, v in row.items())):
            raise ValueError('Expected at least five numeric top logprobs per token')
    selected = {key: choice[key] for key in ('text', 'token_ids', 'logprobs', 'finish_reason')}
    return hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()


def smoke(arm, output):
    status, models = call('/v1/models')
    assert status == 200 and models['data'][0]['max_model_len'] == 1048576
    result = {'arm': arm, 'models': models, 'before': rpc('qualification_state'), 'stock': [], 'contract': []}
    for prompt in PROMPTS:
        reset()
        sample = complete(prompt)
        sample['fingerprint'] = fingerprint(sample)
        result['stock'].append(sample)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    if arm != 'official':
        loaded = arm in ('candidate-loaded', 'parent')
        cases = [
            ({'steering_scale': 0}, 200),
            ({'steering_scale': 1}, 400),
            ({'steering_profile': 'unknown', 'steering_scale': 0}, 400),
            ({'steering_profile': PROFILE, 'steering_scale': 0}, 200 if loaded else 400),
            ({'steering_profile': PROFILE}, 200 if loaded else 400),
            ({'steering_profile': PROFILE, 'steering_scale': 1}, 200 if loaded else 400),
        ]
        for extra, expected in cases:
            sample = complete(PROMPTS[0], extra, tokens=8)
            result['contract'].append({'extra': extra, 'expected': expected, 'result': sample})
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
            assert sample['status'] == expected, result['contract'][-1]
        if loaded:
            assert fingerprint(result['contract'][-2]['result'], 8) == fingerprint(result['contract'][-1]['result'], 8), 'implicit scale differs from 1.0'
            result['active_then_zero'] = []
            for scale in (1, 0, 4, 0, 1):
                reset()
                sample = complete(PROMPTS[0], {'steering_profile': PROFILE, 'steering_scale': scale})
                sample['fingerprint'] = fingerprint(sample)
                sample['scale'] = scale
                if scale == 0:
                    assert sample['fingerprint'] == result['stock'][0]['fingerprint'], 'zero after active differs'
                result['active_then_zero'].append(sample)
                output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
            assert result['active_then_zero'][0]['fingerprint'] != result['stock'][0]['fingerprint'], 'nonzero steering had no observed effect'
            assert result['active_then_zero'][0]['fingerprint'] == result['active_then_zero'][-1]['fingerprint'], 'scale 1 replay changed'
            reset()
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                samples = list(pool.map(
                    lambda scale: complete(PROMPTS[0], {'steering_profile': PROFILE, 'steering_scale': scale}),
                    (0, 1, 0, 4),
                ))
            result['mixed'] = samples
            for index in (0, 2):
                assert fingerprint(samples[index]) == result['stock'][0]['fingerprint'], 'mixed stock differs'
            body = {'model': 'deepseek-v4.1-flash', 'prompt': PROMPTS[1],
                    'stream': True, 'max_tokens': 2048, 'ignore_eos': True,
                    'temperature': 0, 'steering_profile': PROFILE, 'steering_scale': 1}
            request = urllib.request.Request(BASE + '/v1/completions', data=json.dumps(body).encode(),
                                            headers={'Content-Type': 'application/json'}, method='POST')
            with urllib.request.urlopen(request, timeout=60) as response:
                for line in response:
                    if line.startswith(b'data: ') and b'[DONE]' not in line:
                        event = json.loads(line[6:])
                        if any(choice.get('text') for choice in event.get('choices', [])):
                            break
            deadline = time.monotonic() + 60
            while True:
                with urllib.request.urlopen(BASE + '/metrics', timeout=10) as response:
                    metrics = response.read().decode()
                live = [float(line.rsplit(' ', 1)[1]) for line in metrics.splitlines()
                        if line.startswith(('vllm:num_requests_running{', 'vllm:num_requests_waiting{'))]
                assert len(live) >= 2
                if not any(live):
                    break
                assert time.monotonic() < deadline, 'cancelled request did not drain'
                time.sleep(0.2)
            reset()
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                after_cancel = list(pool.map(
                    lambda scale: complete(PROMPTS[0], {'steering_profile': PROFILE, 'steering_scale': scale}), (0, 1)))
            assert fingerprint(after_cancel[0]) == result['stock'][0]['fingerprint'], 'stock slot after cancel differs'
            result['cancel_and_reuse'] = after_cancel
            sys.path.insert(0, str(REPOSITORY))
            from servebench import prompts
            long_prompts = [prompts.source_text(8192, seed) for seed in (11, 22)]
            reset()
            reference = complete(long_prompts[0], {'steering_profile': PROFILE, 'steering_scale': 0,
                                                   'cache_salt': 'qualification-long-reference'})
            reset()
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(complete, long_prompts[index % 2],
                                       {'steering_profile': PROFILE, 'steering_scale': scale,
                                        'cache_salt': f'qualification-long-mixed-{index}'})
                           for index, scale in enumerate((0, 1, 0, 4))]
                mixed_long = [future.result() for future in futures]
            for index in (0, 2):
                assert fingerprint(mixed_long[index]) == fingerprint(reference), 'mixed SP/CED stock differs'
            result['mixed_long_prefill'] = {'stock_reference': reference, 'mixed': mixed_long}
    result['after'] = rpc('qualification_state')
    result['passed'] = True
    return result


def engine(arm, output, active=False):
    # Short and representative normal-agent decode shapes, same dummy tokens.
    shapes = [
        {'num_reqs': reqs, 'num_tokens': reqs * rows, 'context_len': 4096}
        for rows in (2, 3, 6) for reqs in (1, 4, 16)
    ]
    shapes += [{'num_reqs': 1, 'num_tokens': 3, 'context_len': context} for context in (65536, 262144)]
    scales = [0, 1, 4, 0] if active else [0]
    result = {'arm': arm, 'runs': []}
    for scale in scales:
        thermal = thermal_gate()
        reset()
        spec = {'shapes': shapes, 'replays': 60, 'warmup': 3, 'settle': 1, 'qualification_scale': scale}
        sample = rpc('qualification_fixed_grid', [json.dumps(spec)], timeout=1500)
        result['runs'].append({'scale': scale, 'response': sample, 'thermal_start': thermal})
        output.write_text(json.dumps(result, indent=2))
        reset()
    result['passed'] = True
    return result


def prefill(arm, output, trace=False, capacity=False):
    sys.path.insert(0, str(REPOSITORY))
    from servebench import prefill as implementation
    loaded = arm in ('candidate-loaded', 'parent')
    scales = [0, 1] if loaded and not capacity else [1 if loaded else 0]
    result = {'arm': arm, 'runs': []}
    for scale in scales:
        thermal = thermal_gate()
        reset()
        extra = {'steering_profile': PROFILE, 'steering_scale': scale} if loaded else {}
        sizes = [8192] if trace else [8192, 32768]
        repeats = 1 if trace else 3
        if capacity:
            sizes, repeats = [1040000], 1
        if trace:
            # Warm the same shape before collecting the real-request trace.
            warmed = implementation.run(BASE, 'deepseek-v4.1-flash', sizes, repeats=1, extra_body=extra)
            assert not any(x['error'] for x in warmed), warmed
            reset()
            status, response = call('/start_profile', {}, timeout=60)
            assert status == 200, (status, response)
        try:
            samples = implementation.run(BASE, 'deepseek-v4.1-flash', sizes, repeats=repeats,
                                         extra_body=extra, log=lambda x: print(x, flush=True))
            result['runs'].append({'scale': scale, 'samples': samples, 'thermal_start': thermal})
            output.write_text(json.dumps(result, indent=2))
            assert not any(x['error'] for x in samples), samples
        finally:
            if trace:
                status, response = call('/stop_profile', {}, timeout=180)
                assert status == 200, (status, response)
        result['runs'][-1]['state'] = rpc('qualification_state')
    if trace:
        result.update(capture_succeeded=True, trace_acceptance='not_evaluated')
    else:
        result['passed'] = True
    return result


def capacity(arm, output):
    sys.path.insert(0, str(REPOSITORY))
    from servebench import prompts
    status, tokenized = call('/tokenize', {'model': 'deepseek-v4.1-flash', 'prompt': prompts.source_text(32768, 2048)})
    assert status == 200 and tokenized['tokens']
    count = 1048576 - 8
    source = tokenized['tokens']
    tokens = (source * (count // len(source) + 1))[:count]
    reset()
    body = {'model': 'deepseek-v4.1-flash', 'prompt': tokens,
            'max_tokens': 8, 'ignore_eos': True, 'temperature': 0,
            'seed': 2048, 'return_token_ids': True}
    if arm in ('candidate-loaded', 'parent'):
        body.update(steering_profile=PROFILE, steering_scale=1)
    result = {'arm': arm, 'input_tokens': count,
              'prompt_sha256': hashlib.sha256(json.dumps(tokens).encode()).hexdigest(),
              'before': rpc('qualification_state')}
    output.write_text(json.dumps(result, indent=2))
    start = time.time()
    status, response = call('/v1/completions', body, timeout=3600)
    result.update(status=status, response=response, request_seconds=time.time() - start)
    output.write_text(json.dumps(result, indent=2))
    assert status == 200, (status, response)
    assert response['usage']['prompt_tokens'] == count
    assert response['usage']['completion_tokens'] == 8
    assert response['usage']['total_tokens'] == 1048576
    result['after'] = rpc('qualification_state')
    result['passed'] = True
    reset()
    return result


def decode_trace(arm, output):
    result = {'arm': arm, 'runs': []}
    for scale in (0, 1):
        extra = {'steering_profile': PROFILE, 'steering_scale': scale, 'logprobs': None}
        assert complete(PROMPTS[0], extra)['status'] == 200
        reset()
        status, response = call('/start_profile', {}, timeout=60)
        assert status == 200, (status, response)
        try:
            sample = complete(PROMPTS[0], extra)
            assert sample['status'] == 200, sample
        finally:
            status, response = call('/stop_profile', {}, timeout=180)
            assert status == 200, (status, response)
        result['runs'].append({'scale': scale, 'sample': sample})
        output.write_text(json.dumps(result, indent=2))
    result.update(capture_succeeded=True, trace_acceptance='not_evaluated')
    return result


def bench(arm, output, stock_only=False):
    repo = REPOSITORY
    sys.path.insert(0, str(repo))
    from servebench import prompts
    source = prompts.default_prompts()[:16]
    prompt_file = ROOT / 'workload-prompts.jsonl'
    prompt_file.write_text(''.join(json.dumps(x) + '\n' for x in source))
    loaded = arm in ('candidate-loaded', 'parent')
    result = {'arm': arm, 'prompt_sha256': prompts.digest(source), 'runs': []}
    for scale in ([0, 1, 4, 0] if arm == 'candidate-loaded' and not stock_only else [0]):
        for concurrency in (1, 4):
            thermal = thermal_gate()
            reset()
            extra = {'temperature': 0, 'seed': 2048}
            if loaded:
                extra.update(steering_profile=PROFILE, steering_scale=scale)
            index = len(result['runs'])
            target = ROOT / 'evidence' / f'{output.stem}-load-{index}-s{scale}-c{concurrency}-{time.time_ns()}.json'
            cmd = [sys.executable, '-m', 'servebench', 'run', BASE,
                   '--mode', 'closed', '--concurrency', str(concurrency),
                   '--warmup', '15', '--duration', '90', '--batches', '6',
                   '--max-tokens', '256', '--context', '4096', '--seed', '2048',
                   '--prompts', str(prompt_file), '--extra-body', json.dumps(extra),
                   '--save-requests', '--label', f'{arm}-s{scale}-c{concurrency}',
                   '--output', str(target)]
            start = time.time()
            run = subprocess.run(cmd, cwd=repo, timeout=420, check=False)
            result['runs'].append({'scale': scale, 'concurrency': concurrency,
                                   'returncode': run.returncode, 'seconds': time.time() - start,
                                   'path': str(target), 'thermal_start': thermal})
            output.write_text(json.dumps(result, indent=2))
            assert run.returncode == 0
    result['passed'] = True
    return result


def reserve_run(output_dir, run_id=None):
    run_id = run_id or uuid.uuid4().hex
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,95}', run_id):
        raise ValueError('Invalid run ID')
    root = output_dir.resolve() / 'runs' / run_id
    root.mkdir(parents=True, exist_ok=False)
    (root / 'evidence').mkdir()
    return root


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('arm', choices=['official', 'parent', 'candidate-unloaded', 'candidate-loaded'])
    parser.add_argument('phase', choices=['smoke', 'engine', 'active-engine', 'prefill', 'trace', 'decode-trace', 'capacity', 'bench', 'bench-stock'])
    parser.add_argument('--url', default=BASE)
    parser.add_argument('--output-dir', type=Path, default=ROOT)
    parser.add_argument('--telemetry', type=Path)
    parser.add_argument('--run-id', help='Unique run directory; an existing ID is rejected')
    options = parser.parse_args()
    if options.phase in ('active-engine', 'decode-trace') and options.arm != 'candidate-loaded':
        parser.error('This phase requires candidate-loaded graph controls')
    ROOT = reserve_run(options.output_dir, options.run_id)
    BASE = options.url.rstrip('/')
    THERMAL_SOURCE = options.telemetry
    output = ROOT / 'evidence' / f'{options.arm}-{options.phase}.json'
    start = time.time()
    try:
        if options.phase == 'smoke':
            result = smoke(options.arm, output)
        elif options.phase in ('engine', 'active-engine'):
            result = engine(options.arm, output, options.phase == 'active-engine')
        elif options.phase in ('bench', 'bench-stock'):
            result = bench(options.arm, output, options.phase == 'bench-stock')
        elif options.phase == 'capacity':
            result = capacity(options.arm, output)
        elif options.phase == 'decode-trace':
            result = decode_trace(options.arm, output)
        else:
            result = prefill(options.arm, output, options.phase == 'trace')
        result['seconds'] = time.time() - start
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        status = ({'capture_succeeded': result['capture_succeeded'], 'trace_acceptance': result['trace_acceptance']}
                  if options.phase in ('trace', 'decode-trace') else {'passed': result['passed']})
        print(json.dumps({'arm': options.arm, 'phase': options.phase,
                          'output': str(output), **status, 'seconds': result['seconds']}))
    except BaseException:
        import traceback
        failure = {'arm': options.arm, 'phase': options.phase, 'validation_passed': False,
                   'seconds': time.time() - start, 'error': traceback.format_exc(),
                   'partial_receipt': str(output), 'raw_completion_directory': str(ROOT / 'evidence/raw-completions')}
        with (ROOT / 'evidence/validation-failure.json').open('x') as report:
            json.dump(failure, report, ensure_ascii=False, indent=2)
        traceback.print_exc()
        raise
