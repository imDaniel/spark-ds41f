#!/usr/bin/env python3
"""Optional source-only allocator observation; NOT selected by any current plan.

One fixed no-argument RPC, combined with the existing HTTP readiness extension.
The CPU envelope retains bytes and clock uncertainty, not transport proof. Neither
entry point produces a capacity verdict or a complete interval-health receipt.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import types

HTTP_WORKER_SHA256 = '3b1a358ad47051dd3bed3628d6d34d8433241de6271f764a522026534ba381cf'
METHOD = 'q03_capacity_allocator_snapshot'
SCHEMA = 'q03.allocator-observation.v1'
KEYS = ('allocated_bytes.all.current', 'allocated_bytes.all.peak',
        'reserved_bytes.all.current', 'reserved_bytes.all.peak',
        'num_alloc_retries', 'num_ooms')
PROC_STAT = '/proc/self/stat'
BOOT_ID = '/proc/sys/kernel/random/boot_id'
UNKNOWN = ('driver_error_total', 'all_source_oom_total', 'host_memavailable_interval_min',
           'host_swap_in_bytes', 'host_swap_out_bytes', 'guard_interval_coverage',
           'all_four_gpu_drain', 'protected_input_integrity', 'full_context_residency',
           'cross_host_clock_conversion', 'counter_interval_delta',
           'all_source_allocation_retries', 'engine_restart_count', 'guard_halts',
           'request_error_count', 'daemon_quiescence', 'effective_resource_validation')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def integer(value):
    return type(value) is int and value >= 0


def matches(pattern, value):
    return type(value) is str and re.fullmatch(pattern, value) is not None


def sha256(value):
    return matches(r'[0-9a-f]{64}', value)


def unknown(reason):
    return {'status': 'UNKNOWN', 'value': None, 'reason': reason}


def retained(raw):
    """Preserve even non-UTF-8/error bytes, never a reserialized approximation."""
    if type(raw) is not bytes:
        raise ValueError('actual bytes required')
    return {'encoding': 'base64', 'data': base64.b64encode(raw).decode('ascii'),
            'sha256': digest(raw), 'size_bytes': len(raw)}


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key: ' + key)
            result[key] = value
        return result
    def constant(value):
        raise ValueError('nonfinite JSON value: ' + value)
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)


def _http_source():
    path = Path(__file__).with_name('q03_http_worker.py')
    raw = path.read_bytes()
    if digest(raw) != HTTP_WORKER_SHA256:
        raise RuntimeError('pinned HTTP worker source changed')
    return path, raw


def _load_http():
    # No sys.path import, preloaded module, stale pyc or extension-module shadow.
    path, raw = _http_source()
    module = types.ModuleType('_q03_allocator_http_base')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


_http = _load_http()  # CPU-only; existing module constructs no engine on import.


def clock_origin(boot_id, time_namespace):
    if (not matches(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', boot_id)
            or not matches(r'time:\[[0-9]+\]', time_namespace)):
        raise ValueError('boot/time namespace identity unavailable')
    return {'boot_id': boot_id, 'time_namespace': time_namespace,
            'clock': 'time.monotonic_ns', 'unit': 'ns'}


def valid_generation(value):
    return (type(value) is dict
            and set(value) == {'boot_id', 'pid_namespace', 'pid', 'start_ticks'}
            and matches(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value['boot_id'])
            and matches(r'pid:\[[0-9]+\]', value['pid_namespace'])
            and integer(value['pid']) and value['pid'] > 0
            and integer(value['start_ticks']) and value['start_ticks'] > 0)


def build_snapshot(*, rank, local_rank, device, torch_version, backend, stats,
                   proc_stat, boot_id, time_namespace, pid_namespace,
                   start_ns, end_ns, binding, observer_source_sha256):
    """CPU serialization seam, not a claim that supplied fixture values are live."""
    issues, raw_sources = [], {}
    generation = origin = None
    for name, raw in ((PROC_STAT, proc_stat), (BOOT_ID, boot_id)):
        if type(raw) is bytes:
            raw_sources[name] = retained(raw)
        else:
            issues.append(name + ': unavailable')
    try:
        text = proc_stat.decode('utf-8')
        prefix = text[:text.index('(')].strip()
        fields = text[text.rindex(')') + 1:].split()
        if not matches(r'[0-9]+', prefix) or len(fields) < 20 or not matches(r'[0-9]+', fields[19]):
            raise ValueError('malformed self stat')
        boot = boot_id.decode('ascii').strip()
        candidate = {'boot_id': boot, 'pid_namespace': pid_namespace,
                     'pid': int(prefix), 'start_ticks': int(fields[19])}
        if not valid_generation(candidate):
            raise ValueError('process generation unavailable')
        generation = candidate
        origin = clock_origin(boot, time_namespace)
    except (AttributeError, TypeError, ValueError, IndexError, UnicodeError) as exc:
        issues.append('process/clock identity unavailable: ' + str(exc))
    if (not all(integer(x) for x in (rank, local_rank, start_ns, end_ns))
            or rank not in range(4) or local_rank != 0 or start_ns >= end_ns):
        issues.append('rank or local clock bracket invalid')
    if (type(binding) is not dict
            or set(binding) != {'window_id', 'attempt_id', 'http_spec_sha256'}
            or any(type(x) is not str or not x for x in binding.values())
            or not sha256(binding.get('http_spec_sha256'))):
        issues.append('verified attempt/spec identity unavailable')
    if not sha256(observer_source_sha256):
        issues.append('allocator source identity unavailable')
    if not isinstance(torch_version, str) or not torch_version:
        issues.append('torch version unavailable')
    values = {}
    for key in KEYS:
        value = stats.get(key) if isinstance(stats, dict) else None
        if not integer(value):
            values[key] = unknown('not returned as a nonnegative integer; no default zero')
        elif backend != 'native':
            values[key] = {'status': 'UNKNOWN', 'value': None, 'raw_reported_value': value,
                           'reason': 'allocator backend not verified native; other backends may report meaningless zeros'}
        else:
            values[key] = {'status': 'OBSERVED', 'value': value,
                           'scope': 'worker-process PyTorch native allocator snapshot only'}
    return {'schema': SCHEMA, 'status': 'INCONCLUSIVE' if issues else 'OBSERVATION_ONLY',
            'capacity_status': 'INCONCLUSIVE', 'field_ready': False,
            'sources': {'allocator_observer_sha256': observer_source_sha256 if sha256(observer_source_sha256) else None,
                        'http_worker_sha256': HTTP_WORKER_SHA256,
                        'runtime_manifest_sha256': _http.RUNTIME_SHA},
            'http_binding': binding if type(binding) is dict and all(type(x) is str for x in binding.values()) else None,
            'runtime_validation': 'MANIFEST_AND_TORCH_VERSION_ONLY; separate existing HTTP readiness required',
            'worker': {'rank': rank if integer(rank) else None,
                       'local_rank': local_rank if integer(local_rank) else None,
                       'device': str(device), 'torch_version': str(torch_version) if isinstance(torch_version, str) else None,
                       'generation': generation},
            'local_clock': {'origin': origin, 'start_ns': start_ns if integer(start_ns) else None,
                            'end_ns': end_ns if integer(end_ns) else None,
                            'point_timestamp': None, 'head_clock_conversion': 'UNKNOWN'},
            'allocator_backend': backend if type(backend) is str else None,
            'allocator': values, 'raw_sources': raw_sources,
            'unknown': {key: unknown('not measured here; diagnostic, not an added protocol gate') for key in UNKNOWN},
            'issues': issues}


class AllocatorObservation(_http.HttpObservation):
    """Optional replacement extension retains q03_http_observe without modification.

    No initializer, worker override, reset, synchronization, model or tensor work.
    Current plans still select q03_http_worker.HttpObservation, not this class.
    """
    def q03_capacity_allocator_snapshot(self):
        own_sha = digest(Path(__file__).read_bytes())
        if os.environ.get('Q03_ALLOCATOR_OBSERVER_SHA256') != own_sha:
            raise RuntimeError('allocator extension has not been separately frozen')
        _http_source()
        if (os.environ.get('Q03_HTTP_WORKER_SHA256') != HTTP_WORKER_SHA256
                or os.environ.get('VLLM_ALLOW_INSECURE_SERIALIZATION') != '0'):
            raise RuntimeError('existing HTTP source/safe serialization binding changed')
        raw = Path('/runtime/runtime-manifest.json').read_bytes()
        if digest(raw) != _http.RUNTIME_SHA:
            raise RuntimeError('HTTP native runtime manifest changed')
        runtime = strict_json(raw)
        spec_raw = Path('/runtime/http-spec.json').read_bytes()
        binding = {'window_id': os.environ.get('Q03_HTTP_WINDOW'),
                   'attempt_id': os.environ.get('Q03_HTTP_ATTEMPT'),
                   'http_spec_sha256': os.environ.get('Q03_HTTP_SPEC_SHA256')}
        identity = strict_json(spec_raw)['handshake']
        if (digest(spec_raw) != binding['http_spec_sha256']
                or any(type(binding[k]) is not str or not binding[k] for k in ('window_id', 'attempt_id'))
                or any(identity[k] != binding[k] for k in ('window_id', 'attempt_id'))):
            raise RuntimeError('HTTP worker attempt identity changed')
        import torch  # Only the explicit method invocation, never module import.
        if not isinstance(torch.__version__, str) or str(torch.__version__) != runtime['versions']['torch']:
            raise RuntimeError('runtime torch version changed')
        started = time.monotonic_ns()
        try:
            stats = torch.accelerator.memory_stats(self.device)
        except Exception:
            stats = None
        try:
            backend = torch.cuda.memory.get_allocator_backend()
        except Exception:
            backend = None
        raw_proc = []
        for name in (PROC_STAT, BOOT_ID):
            try:
                raw_proc.append(Path(name).read_bytes())
            except OSError:
                raw_proc.append(None)
        namespaces = []
        for name in ('time', 'pid'):
            try:
                namespaces.append(os.readlink('/proc/self/ns/' + name))
            except OSError:
                namespaces.append(None)
        ended = time.monotonic_ns()
        return build_snapshot(rank=self.rank, local_rank=self.local_rank, device=self.device,
                              torch_version=torch.__version__, backend=backend, stats=stats,
                              proc_stat=raw_proc[0], boot_id=raw_proc[1],
                              time_namespace=namespaces[0], pid_namespace=namespaces[1],
                              start_ns=started, end_ns=ended, binding=binding,
                              observer_source_sha256=own_sha)


def head_response_envelope(raw_response, *, raw_request, http_status, head_clock_origin,
                           sent_ns, received_ns, deadline_ns, binding,
                           observer_source_sha256, expected_generations=None):
    """CPU-only capture seam; no requests, point timestamps or transport attestation.

    Expected generations, if known from a retained earlier fence, are rank-ordered.
    An initial observation can retain generations but cannot claim an earlier pin.
    The actual supervisor still owns timeouts, raw capture and mandatory cleanup.
    """
    if type(raw_response) is not bytes or type(raw_request) is not bytes:
        raise ValueError('actual request and response bytes required')
    if (not all(integer(x) for x in (sent_ns, received_ns, deadline_ns))
            or not sent_ns < received_ns <= deadline_ns):
        raise ValueError('invalid or over-deadline head observation bracket')
    if (type(head_clock_origin) is not dict
            or set(head_clock_origin) != {'boot_id', 'time_namespace', 'clock', 'unit'}
            or head_clock_origin != clock_origin(head_clock_origin.get('boot_id'), head_clock_origin.get('time_namespace'))):
        raise ValueError('actual typed head clock origin required')
    if (type(binding) is not dict or set(binding) != {'window_id', 'attempt_id', 'http_spec_sha256'}
            or any(type(x) is not str or not x for x in binding.values())
            or not sha256(binding['http_spec_sha256']) or not sha256(observer_source_sha256)):
        raise ValueError('frozen source/attempt/spec identities required')
    request = strict_json(raw_request)
    timeout = request.get('timeout') if type(request) is dict else None
    # Existing HTTP read_ready uses a 15-second RPC ceiling. No new timeout.
    if (type(request) is not dict or set(request) != {'method', 'args', 'timeout'}
            or request['method'] != METHOD or request['args'] != []
            or type(timeout) not in (int, float) or not math.isfinite(timeout)
            or not 0 < timeout <= min(15, (deadline_ns - sent_ns) / 1e9)):
        raise ValueError('only the fixed no-argument bounded RPC request is supported')
    if expected_generations is not None and (type(expected_generations) is not list
            or len(expected_generations) != 4 or not all(valid_generation(x) for x in expected_generations)):
        raise ValueError('exact four rank-ordered expected worker generations required')
    issues, reports = [], None
    generations_match = False
    try:
        if type(http_status) is not int or http_status != 200:
            raise ValueError('HTTP observation response is not 200')
        response = strict_json(raw_response)
        if type(response) is not dict or set(response) != {'results'}:
            raise ValueError('not the native collective_rpc response shape')
        reports = response['results']
        if type(reports) is not list or len(reports) != 4:
            raise ValueError('exactly four worker reports required')
        seen = set()
        for report in reports:
            if type(report) is not dict or report.get('schema') != SCHEMA:
                raise ValueError('allocator report schema differs')
            worker = report.get('worker', {})
            rank = worker.get('rank')
            if (not integer(rank) or rank not in range(4) or rank in seen
                    or type(worker.get('local_rank')) is not int or worker['local_rank'] != 0
                    or not valid_generation(worker.get('generation'))):
                raise ValueError('duplicate/invalid rank or worker generation')
            seen.add(rank)
            if (report.get('sources') != {'allocator_observer_sha256': observer_source_sha256,
                    'http_worker_sha256': HTTP_WORKER_SHA256, 'runtime_manifest_sha256': _http.RUNTIME_SHA}
                    or report.get('http_binding') != binding):
                raise ValueError('source/runtime/attempt/spec binding differs')
            if report.get('status') != 'OBSERVATION_ONLY' or report.get('capacity_status') != 'INCONCLUSIVE' or report.get('field_ready') is not False:
                raise ValueError('worker observation is inconclusive or exceeds its scope')
            if expected_generations is not None and worker['generation'] != expected_generations[rank]:
                raise ValueError('worker generation changed')
        generations_match = expected_generations is not None
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError) as exc:
        issues.append(str(exc))
    return {'schema': 'q03.allocator-head-envelope.v1',
            'status': 'INCONCLUSIVE' if issues else 'OBSERVATION_ONLY',
            'capacity_status': 'INCONCLUSIVE', 'field_ready': False,
            'http_binding': dict(binding), 'rpc_method': METHOD,
            'expected_sources': {'allocator_observer_sha256': observer_source_sha256,
                                 'http_worker_sha256': HTTP_WORKER_SHA256,
                                 'runtime_manifest_sha256': _http.RUNTIME_SHA},
            'expected_worker_generations': ([dict(x) for x in expected_generations]
                                            if expected_generations is not None else None),
            'head_clock_origin': dict(head_clock_origin), 'sent_ns': sent_ns,
            'received_ns': received_ns, 'deadline_ns': deadline_ns,
            'http_status': http_status if type(http_status) is int else None,
            'request': retained(raw_request), 'response': retained(raw_response),
            'candidate_remote_measurement_head_bracket': {'lower_ns': sent_ns, 'upper_ns': received_ns},
            'response_field_binding': 'UNKNOWN' if issues else 'MATCHES_EXPECTED_FIELDS_ONLY',
            'prior_worker_generation_binding': 'MATCHES_EXPECTED_FIELDS_ONLY' if generations_match else 'UNKNOWN',
            'synchronous_transport_and_response_binding': 'UNPROVEN_BY_THIS_CPU_ENVELOPE',
            'remote_local_clock_is_head_clock': False,
            'full_interval_health_or_guard_proof': 'UNKNOWN', 'issues': issues}


def install_worker_source():
    """Optional CLI/spawn source bootstrap; current plans never select this file."""
    raw = Path(__file__).read_bytes()
    if digest(raw) != os.environ.get('Q03_ALLOCATOR_OBSERVER_SHA256'):
        raise RuntimeError('allocator bootstrap source changed')
    if (os.environ.get('Q03_HTTP_WORKER_SHA256') != HTTP_WORKER_SHA256
            or os.environ.get('VLLM_ALLOW_INSECURE_SERIALIZATION') != '0'):
        raise RuntimeError('existing HTTP source/safe serialization binding changed')
    module = types.ModuleType('q03_allocator_observation')
    module.__file__ = __file__
    exec(compile(raw, __file__, 'exec'), module.__dict__)
    sys.modules['q03_allocator_observation'] = module


if __name__ in ('__main__', '__mp_main__'):
    install_worker_source()
if __name__ == '__main__':
    _http.main()
