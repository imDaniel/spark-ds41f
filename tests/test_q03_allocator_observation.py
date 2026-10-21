"""CPU interface/negative fixtures only. Never imports real torch/vLLM or uses a GPU."""
import base64
from collections import OrderedDict
import copy
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

FILE = Path(__file__).resolve().parents[1] / 'scripts/q03_allocator_observation.py'
spec = importlib.util.spec_from_file_location('allocator_fixture', FILE)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
OWN = FILE.read_bytes()
HTTP = FILE.with_name('q03_http_worker.py').read_bytes()
BOOT = '01234567-89ab-cdef-0123-456789abcdef'


def fixture(rank=0):
    return dict(rank=rank, local_rank=0, device='cuda:0', torch_version='synthetic', backend='native',
                stats={k: 0 for k in p.KEYS},
                proc_stat=str(100 + rank).encode() + b' (name with ) chars) ' + b' '.join([b'S'] + [b'0'] * 18 + [b'12345']),
                boot_id=(BOOT + '\n').encode(), time_namespace='time:[42]', pid_namespace='pid:[43]',
                start_ns=100, end_ns=200,
                binding={'window_id': 'synthetic-window', 'attempt_id': 'synthetic-attempt',
                         'http_spec_sha256': p.digest(b'synthetic-spec')},
                observer_source_sha256=p.digest(OWN))


def envelope_fixture():
    reports = [p.build_snapshot(**fixture(rank)) for rank in range(4)]
    return dict(raw_response=p.canonical({'results': reports}),
                raw_request=b'{ "timeout": 1, "args": [], "method": "q03_capacity_allocator_snapshot" }\n',
                http_status=200, head_clock_origin=p.clock_origin(BOOT, 'time:[1]'),
                sent_ns=1, received_ns=2, deadline_ns=2_000_000_001,
                binding=fixture()['binding'], observer_source_sha256=p.digest(OWN),
                expected_generations=[r['worker']['generation'] for r in reports])


def reports_mutation(mutate):
    args = envelope_fixture()
    reports = json.loads(args['raw_response'])['results']
    mutate(reports)
    args['raw_response'] = p.canonical({'results': reports})
    return args


class SnapshotTests(unittest.TestCase):
    def test_snapshot_never_capacity_pass(self):
        result = p.build_snapshot(**fixture())
        self.assertEqual('OBSERVATION_ONLY', result['status'])
        self.assertEqual('INCONCLUSIVE', result['capacity_status'])
        self.assertFalse(result['field_ready'])
        self.assertEqual({'boot_id': BOOT, 'pid_namespace': 'pid:[43]', 'pid': 100, 'start_ticks': 12345}, result['worker']['generation'])
        self.assertTrue(all(x['status'] == 'UNKNOWN' and x['value'] is None for x in result['unknown'].values()))
        self.assertEqual(fixture()['proc_stat'], base64.b64decode(result['raw_sources'][p.PROC_STAT]['data']))

    def test_ordered_dictionary_stats_supported_without_defaulting(self):
        data = fixture(); data['stats'] = OrderedDict([('num_ooms', 0), ('num_alloc_retries', 2)])
        result = p.build_snapshot(**data)
        self.assertEqual(0, result['allocator']['num_ooms']['value'])
        self.assertEqual(2, result['allocator']['num_alloc_retries']['value'])
        self.assertEqual('UNKNOWN', result['allocator']['allocated_bytes.all.current']['status'])

    def test_missing_invalid_or_unavailable_counter_is_unknown_not_zero(self):
        for stats in ({}, None, [], {'num_ooms': True}, {'num_ooms': 0.0}, {'num_ooms': -1}, {'num_ooms': float('inf')}, {'num_ooms': '0'}):
            with self.subTest(stats=stats):
                data = fixture(); data['stats'] = stats
                value = p.build_snapshot(**data)['allocator']['num_ooms']
                self.assertEqual('UNKNOWN', value['status'])
                self.assertIsNone(value['value'])

    def test_non_native_backend_zero_is_not_health_proof(self):
        for backend in ('cudaMallocAsync', None, 'unknown'):
            data = fixture(); data['backend'] = backend
            value = p.build_snapshot(**data)['allocator']['num_ooms']
            self.assertEqual('UNKNOWN', value['status'])
            self.assertEqual(0, value['raw_reported_value'])
            self.assertIsNone(value['value'])

    def test_known_lifetime_total_retained_when_other_counter_missing(self):
        data = fixture(); data['stats'] = {'num_ooms': 3}
        result = p.build_snapshot(**data)
        self.assertEqual(3, result['allocator']['num_ooms']['value'])
        self.assertIsNone(result['allocator']['num_alloc_retries']['value'])
        self.assertIsNone(result['unknown']['counter_interval_delta']['value'])

    def test_bad_process_identity_or_clock_cannot_be_observation_only(self):
        for key, value in (('boot_id', None), ('time_namespace', None), ('pid_namespace', None),
                           ('proc_stat', b'invalid'), ('proc_stat', b'\xff'),
                           ('rank', True), ('rank', 4), ('local_rank', 1),
                           ('start_ns', float('inf')), ('end_ns', 100), ('start_ns', True)):
            data = fixture(); data[key] = value
            result = p.build_snapshot(**data)
            self.assertEqual('INCONCLUSIVE', result['status'], (key, value))
            p.canonical(result)
            if key == 'proc_stat':
                self.assertEqual(value, base64.b64decode(result['raw_sources'][p.PROC_STAT]['data']))

    def test_invalid_binding_and_source_are_inconclusive(self):
        for key, value in (('binding', {}), ('binding', {'bad': float('inf')}),
                           ('observer_source_sha256', 'not-a-digest'), ('observer_source_sha256', float('inf')), ('torch_version', None)):
            data = fixture(); data[key] = value
            result = p.build_snapshot(**data)
            self.assertEqual('INCONCLUSIVE', result['status'])
            p.canonical(result)

    def test_serializer_does_not_mutate_caller_values(self):
        data = fixture(); original = copy.deepcopy(data)
        p.build_snapshot(**data)
        self.assertEqual(original, data)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.data = fixture()
        self.runtime = p.canonical({'versions': {'torch': 'synthetic'}})
        self.spec = p.canonical({'handshake': {'window_id': 'w', 'attempt_id': 'a'}})
        self.env = {'Q03_ALLOCATOR_OBSERVER_SHA256': p.digest(OWN),
                    'Q03_HTTP_WORKER_SHA256': p.HTTP_WORKER_SHA256,
                    'VLLM_ALLOW_INSECURE_SERIALIZATION': '0',
                    'Q03_HTTP_WINDOW': 'w', 'Q03_HTTP_ATTEMPT': 'a',
                    'Q03_HTTP_SPEC_SHA256': p.digest(self.spec)}
        self.reads = {str(FILE): OWN, str(FILE.with_name('q03_http_worker.py')): HTTP,
                      '/runtime/runtime-manifest.json': self.runtime,
                      '/runtime/http-spec.json': self.spec,
                      p.PROC_STAT: self.data['proc_stat'], p.BOOT_ID: self.data['boot_id']}
        self.fake = types.SimpleNamespace(__version__='synthetic',
            accelerator=types.SimpleNamespace(memory_stats=self.memory_stats),
            cuda=types.SimpleNamespace(memory=types.SimpleNamespace(get_allocator_backend=lambda: 'native')))
        self.worker = types.SimpleNamespace(rank=0, local_rank=0, device='cuda:0')

    def memory_stats(self, device):
        self.calls.append(('memory_stats', device))
        return self.data['stats']

    def read(self, path):
        self.calls.append(('read', str(path)))
        value = self.reads[str(path)]
        if isinstance(value, Exception):
            raise value
        return value

    def invoke(self):
        with patch.dict(sys.modules, {'torch': self.fake}), patch.dict(os.environ, self.env, clear=True), \
             patch.object(Path, 'read_bytes', lambda path: self.read(path)), patch.object(p._http, 'RUNTIME_SHA', p.digest(self.runtime)), \
             patch.object(p.os, 'readlink', side_effect=['time:[42]', 'pid:[43]']), \
             patch.object(p.time, 'monotonic_ns', side_effect=[100, 200]):
            return p.AllocatorObservation.q03_capacity_allocator_snapshot(self.worker)

    def test_only_fixed_read_api_and_paths(self):
        result = self.invoke()
        self.assertEqual('OBSERVATION_ONLY', result['status'])
        self.assertEqual([('read', str(FILE)), ('read', str(FILE.with_name('q03_http_worker.py'))),
                          ('read', '/runtime/runtime-manifest.json'), ('read', '/runtime/http-spec.json'),
                          ('memory_stats', 'cuda:0'), ('read', p.PROC_STAT), ('read', p.BOOT_ID)], self.calls)
        self.assertEqual({'window_id': 'w', 'attempt_id': 'a', 'http_spec_sha256': p.digest(self.spec)}, result['http_binding'])

    def test_all_identity_failures_precede_allocator_access(self):
        changes = [lambda: self.env.pop('Q03_ALLOCATOR_OBSERVER_SHA256'),
                   lambda: self.env.update(Q03_ALLOCATOR_OBSERVER_SHA256='0' * 64),
                   lambda: self.env.update(Q03_HTTP_WORKER_SHA256='0' * 64),
                   lambda: self.env.update(VLLM_ALLOW_INSECURE_SERIALIZATION='1'),
                   lambda: self.reads.update({str(FILE.with_name('q03_http_worker.py')): b'wrong'}),
                   lambda: self.reads.update({'/runtime/runtime-manifest.json': b'wrong'}),
                   lambda: self.env.update(Q03_HTTP_ATTEMPT='foreign'),
                   lambda: self.env.update(Q03_HTTP_SPEC_SHA256='0' * 64),
                   lambda: setattr(self.fake, '__version__', 'different')]
        for mutate in changes:
            with self.subTest(mutate=mutate):
                self.setUp(); mutate()
                with self.assertRaises(RuntimeError): self.invoke()
                self.assertNotIn(('memory_stats', 'cuda:0'), self.calls)

    def test_torch_version_string_subclass_is_preserved_exactly(self):
        class TorchVersionLike(str):
            pass
        self.fake.__version__ = TorchVersionLike('synthetic')
        result = self.invoke()
        self.assertEqual('OBSERVATION_ONLY', result['status'])
        self.assertEqual('synthetic', result['worker']['torch_version'])
        self.assertIs(type(result['worker']['torch_version']), str)

    def test_torch_version_comparison_does_not_use_subclass_equality(self):
        class PermissiveVersion(str):
            def __eq__(self, other):
                return True
            def __ne__(self, other):
                return False
        self.fake.__version__ = PermissiveVersion('different')
        with self.assertRaisesRegex(RuntimeError, 'torch version changed'):
            self.invoke()
        self.assertNotIn(('memory_stats', 'cuda:0'), self.calls)

    def test_missing_allocator_api_returns_unknown(self):
        self.fake.accelerator = types.SimpleNamespace()
        result = self.invoke()
        self.assertTrue(all(v['status'] == 'UNKNOWN' and v['value'] is None for v in result['allocator'].values()))

    def test_missing_backend_api_does_not_turn_zero_into_evidence(self):
        self.fake.cuda = types.SimpleNamespace()
        result = self.invoke()
        self.assertEqual('UNKNOWN', result['allocator']['num_ooms']['status'])
        self.assertEqual(0, result['allocator']['num_ooms']['raw_reported_value'])

    def test_proc_read_failure_retains_unknown_and_other_actual_values(self):
        self.reads[p.PROC_STAT] = OSError('fixture unavailable')
        result = self.invoke()
        self.assertEqual('INCONCLUSIVE', result['status'])
        self.assertIsNone(result['worker']['generation'])
        self.assertEqual(0, result['allocator']['num_ooms']['value'])

    def test_method_has_no_args_and_inherits_exact_readiness_method(self):
        self.assertEqual(['self'], list(inspect.signature(p.AllocatorObservation.q03_capacity_allocator_snapshot).parameters))
        self.assertIs(p.AllocatorObservation.q03_http_observe, p._http.HttpObservation.q03_http_observe)
        self.assertNotIn('__init__', p.AllocatorObservation.__dict__)
        self.assertEqual({'q03_http_observe', p.METHOD}, {name for name in dir(p.AllocatorObservation) if not name.startswith('__') and callable(getattr(p.AllocatorObservation, name))})
        self.assertEqual(p.HTTP_WORKER_SHA256, p.digest(HTTP))

    def test_safe_source_bootstrap_ignores_preloaded_extension(self):
        replacement = types.ModuleType('q03_allocator_observation')
        with patch.dict(sys.modules, {'q03_allocator_observation': replacement, 'q03_http_worker': object()}), \
             patch.dict(os.environ, self.env, clear=True):
            p.install_worker_source()
            actual = sys.modules['q03_allocator_observation']
            self.assertIsNot(actual, replacement)
            self.assertEqual(['self'], list(inspect.signature(actual.AllocatorObservation.q03_capacity_allocator_snapshot).parameters))
            self.assertIs(actual.AllocatorObservation.q03_http_observe, actual._http.HttpObservation.q03_http_observe)

    def test_bootstrap_rejects_unsafe_or_unfrozen_configuration(self):
        for key, value in (('Q03_ALLOCATOR_OBSERVER_SHA256', '0' * 64),
                           ('Q03_HTTP_WORKER_SHA256', '0' * 64), ('VLLM_ALLOW_INSECURE_SERIALIZATION', '1')):
            env = dict(self.env); env[key] = value
            with self.subTest(key=key), patch.dict(os.environ, env, clear=True), self.assertRaises(RuntimeError):
                p.install_worker_source()

    def test_import_does_not_import_torch_or_vllm(self):
        import builtins
        real_import = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name.split('.')[0] in ('torch', 'vllm'):
                self.fail('runtime import during CPU module import: ' + name)
            return real_import(name, *args, **kwargs)
        module = types.ModuleType('isolated_cpu_import'); module.__file__ = str(FILE)
        with patch.object(builtins, '__import__', guarded):
            exec(compile(OWN, str(FILE), 'exec'), module.__dict__)


class EnvelopeTests(unittest.TestCase):
    def test_exact_bytes_and_non_point_clock_bracket(self):
        data = envelope_fixture(); original = copy.deepcopy(data)
        result = p.head_response_envelope(**data)
        self.assertEqual('OBSERVATION_ONLY', result['status'])
        self.assertEqual('INCONCLUSIVE', result['capacity_status'])
        self.assertFalse(result['field_ready'])
        self.assertEqual({'lower_ns': 1, 'upper_ns': 2}, result['candidate_remote_measurement_head_bracket'])
        self.assertFalse(result['remote_local_clock_is_head_clock'])
        self.assertEqual('UNPROVEN_BY_THIS_CPU_ENVELOPE', result['synchronous_transport_and_response_binding'])
        for key, raw in (('request', data['raw_request']), ('response', data['raw_response'])):
            self.assertEqual(raw, base64.b64decode(result[key]['data']))
            self.assertEqual(len(raw), result[key]['size_bytes'])
            self.assertEqual(p.digest(raw), result[key]['sha256'])
        self.assertEqual('MATCHES_EXPECTED_FIELDS_ONLY', result['prior_worker_generation_binding'])
        self.assertEqual(original, data)

    def test_expected_pins_are_retained_without_caller_aliasing(self):
        data = envelope_fixture()
        result = p.head_response_envelope(**data)
        self.assertEqual(data['observer_source_sha256'], result['expected_sources']['allocator_observer_sha256'])
        self.assertEqual(p.HTTP_WORKER_SHA256, result['expected_sources']['http_worker_sha256'])
        self.assertEqual(p._http.RUNTIME_SHA, result['expected_sources']['runtime_manifest_sha256'])
        self.assertEqual(data['expected_generations'], result['expected_worker_generations'])
        data['expected_generations'][0]['pid'] = 999
        self.assertEqual(100, result['expected_worker_generations'][0]['pid'])

    def test_first_fence_does_not_fabricate_prior_generation_pin(self):
        data = envelope_fixture(); data['expected_generations'] = None
        result = p.head_response_envelope(**data)
        self.assertEqual('OBSERVATION_ONLY', result['status'])
        self.assertEqual('UNKNOWN', result['prior_worker_generation_binding'])

    def test_failure_or_malformed_raw_bytes_are_retained(self):
        for raw, status in ((b'\xff\x00failure\n', 200), (b'<html>failed</html>', 500),
                            (b'{"results": [], "results": []}', 200), (b'{"results":NaN}', 200)):
            data = envelope_fixture(); data.update(raw_response=raw, http_status=status)
            result = p.head_response_envelope(**data)
            self.assertEqual('INCONCLUSIVE', result['status'])
            self.assertEqual('UNKNOWN', result['response_field_binding'])
            self.assertEqual(raw, base64.b64decode(result['response']['data']))

    def test_missing_duplicate_or_restarted_rank_rejected(self):
        mutations = [lambda r: r.pop(), lambda r: r.append(r[0]), lambda r: r.__setitem__(3, r[0]),
                     lambda r: r[0]['worker'].update(rank=True),
                     lambda r: r[0]['worker']['generation'].update(start_ticks=999),
                     lambda r: r[0]['worker']['generation'].update(pid_namespace='pid:[99]'),
                     lambda r: r[0]['worker'].update(generation=None)]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                result = p.head_response_envelope(**reports_mutation(mutate))
                self.assertEqual('INCONCLUSIVE', result['status'])
                self.assertEqual('UNKNOWN', result['prior_worker_generation_binding'])

    def test_foreign_sources_runtime_attempt_or_scope_rejected(self):
        mutations = [lambda r: r[0]['sources'].update(allocator_observer_sha256='0' * 64),
                     lambda r: r[0]['sources'].update(http_worker_sha256='0' * 64),
                     lambda r: r[0]['sources'].update(runtime_manifest_sha256='0' * 64),
                     lambda r: r[0]['http_binding'].update(attempt_id='foreign'),
                     lambda r: r[0]['http_binding'].update(http_spec_sha256='0' * 64),
                     lambda r: r[0].update(capacity_status='PASS'),
                     lambda r: r[0].update(field_ready=True),
                     lambda r: r[0].update(status='INCONCLUSIVE')]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.assertEqual('INCONCLUSIVE', p.head_response_envelope(**reports_mutation(mutate))['status'])

    def test_reordered_native_rank_reports_preserve_pinning(self):
        result = p.head_response_envelope(**reports_mutation(lambda r: r.reverse()))
        self.assertEqual('MATCHES_EXPECTED_FIELDS_ONLY', result['prior_worker_generation_binding'])

    def test_missing_counter_stays_unknown_inside_retained_response(self):
        data = envelope_fixture()
        reports = [p.build_snapshot(**dict(fixture(r), stats={})) for r in range(4)]
        data['raw_response'] = p.canonical({'results': reports})
        result = p.head_response_envelope(**data)
        self.assertEqual('OBSERVATION_ONLY', result['status'])
        body = json.loads(base64.b64decode(result['response']['data']))
        self.assertIsNone(body['results'][0]['allocator']['num_ooms']['value'])

    def test_only_exact_named_no_argument_rpc_and_existing_timeout_ceiling(self):
        good = {'method': p.METHOD, 'args': [], 'timeout': 1}
        for request in (dict(good, method='arbitrary'), dict(good, args=['code']),
                        dict(good, kwargs={}), dict(good, timeout=True), dict(good, timeout=0),
                        dict(good, timeout=16), dict(good, timeout=3), []):
            data = envelope_fixture(); data['raw_request'] = p.canonical(request)
            with self.subTest(request=request), self.assertRaises(ValueError): p.head_response_envelope(**data)

    def test_head_clock_deadline_and_identity_are_strict(self):
        changes = [('sent_ns', True), ('received_ns', 1), ('received_ns', 3_000_000_000),
                   ('received_ns', 2.0), ('head_clock_origin', {'id': 'head'}),
                   ('head_clock_origin', dict(p.clock_origin(BOOT, 'time:[1]'), clock='time.time')),
                   ('expected_generations', [{}] * 4), ('expected_generations', []),
                   ('observer_source_sha256', None), ('raw_response', '{}'), ('http_status', True)]
        for key, value in changes:
            data = envelope_fixture(); data[key] = value
            with self.subTest(key=key, value=value):
                if key == 'http_status':
                    self.assertEqual('INCONCLUSIVE', p.head_response_envelope(**data)['status'])
                else:
                    with self.assertRaises(ValueError): p.head_response_envelope(**data)


if __name__ == '__main__':
    unittest.main()
