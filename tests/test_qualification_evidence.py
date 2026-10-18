"""Qualification must reject incomplete observations, partial captures and reused runs."""
import copy
import gzip
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / 'experiments/2026-10-19-steering-gpu'


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = load('probe_model')
traces = load('check_traces')
stock = load('check_stock_evidence')


def response(count=64):
    return {'status': 200, 'response': {'usage': {'completion_tokens': count}, 'choices': [{
        'text': 'x' * count, 'token_ids': list(range(count)), 'finish_reason': 'length',
        'logprobs': {'tokens': ['x'] * count, 'token_logprobs': [-0.5] * count,
                     'text_offset': list(range(count)),
                     'top_logprobs': [{str(i): -float(i) for i in range(5)} for _ in range(count)]},
    }]}}


class ObservationTest(unittest.TestCase):
    def test_complete_observation_keeps_the_original_hash(self):
        obj = response()
        selected = obj['response']['choices'][0]
        expected = hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()
        self.assertEqual(probe.fingerprint(obj), expected)
        self.assertEqual(len(probe.fingerprint(response(8), 8)), 64)

    def test_missing_null_or_truncated_token_ids_fail(self):
        for value in (None, list(range(63)), [True] * 64):
            obj = response()
            obj['response']['choices'][0]['token_ids'] = value
            with self.subTest(value_type=type(value)), self.assertRaisesRegex(ValueError, 'token IDs'):
                probe.fingerprint(obj)
        obj = response()
        del obj['response']['choices'][0]['token_ids']
        with self.assertRaises(ValueError):
            probe.fingerprint(obj)

    def test_matching_truncation_and_usage_do_not_pass(self):
        with self.assertRaises(ValueError):
            probe.fingerprint(response(63))
        for usage in (None, 63, True):
            obj = response()
            obj['response']['usage']['completion_tokens'] = usage
            with self.subTest(usage=usage), self.assertRaisesRegex(ValueError, 'usage'):
                probe.fingerprint(obj)

    def test_missing_null_and_short_logprobs_fail(self):
        for value in (None, {}):
            obj = response()
            obj['response']['choices'][0]['logprobs'] = value
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                probe.fingerprint(obj)
        for field in ('tokens', 'token_logprobs', 'top_logprobs', 'text_offset'):
            obj = response()
            obj['response']['choices'][0]['logprobs'][field].pop()
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'truncated logprobs'):
                probe.fingerprint(obj)

    def test_incomplete_or_malformed_top_five_fails(self):
        for value in (None, {'x': -1}, {str(i): None for i in range(5)},
                      {str(i): float('nan') for i in range(5)}):
            obj = response()
            obj['response']['choices'][0]['logprobs']['top_logprobs'][0] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'five numeric'):
                probe.fingerprint(obj)

    def test_unfinished_completion_and_missing_choice_fail(self):
        obj = response()
        obj['response']['choices'][0]['finish_reason'] = 'stop'
        with self.assertRaisesRegex(ValueError, 'length-limited'):
            probe.fingerprint(obj)
        obj['response']['choices'] = []
        with self.assertRaisesRegex(ValueError, 'one completion'):
            probe.fingerprint(obj)

    def test_thermal_gate_requires_unique_expected_nodes_and_fresh_past_samples(self):
        valid = {'samples': [{'node': n, 'time': 99, 'hottest_c': 50} for n in range(1, 5)]}
        self.assertTrue(probe.thermal_record_ready(valid, 100))
        for field, value in [('node', 2), ('node', True), ('time', 101), ('time', 80),
                             ('time', float('nan')), ('hottest_c', None), ('hottest_c', 55)]:
            obj = copy.deepcopy(valid)
            obj['samples'][0][field] = value
            with self.subTest(field=field, value=value):
                self.assertFalse(probe.thermal_record_ready(obj, 100))

    def test_run_ids_cannot_overwrite_existing_receipts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            run = probe.reserve_run(root, 'first')
            original = run / 'evidence/receipt.json'
            original.write_text('preserved')
            with self.assertRaises(FileExistsError):
                probe.reserve_run(root, 'first')
            self.assertEqual(original.read_text(), 'preserved')
            self.assertNotEqual(probe.reserve_run(root), probe.reserve_run(root))
            with self.assertRaises(ValueError):
                probe.reserve_run(root, '../escape')

    def test_http_capture_success_is_not_trace_acceptance(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(probe, 'complete', return_value={'status': 200}), \
                patch.object(probe, 'reset'), patch.object(probe, 'call', return_value=(200, None)):
            result = probe.decode_trace('candidate-loaded', Path(folder) / 'capture.json')
        self.assertTrue(result['capture_succeeded'])
        self.assertEqual(result['trace_acceptance'], 'not_evaluated')
        self.assertNotIn('passed', result)

    def test_prefill_capture_and_plain_engine_have_distinct_status(self):
        fake = SimpleNamespace(prefill=SimpleNamespace(run=lambda *a, **k: [{'error': None}]))
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules, {'servebench': fake}), \
                patch.object(probe, 'thermal_gate', return_value={}), patch.object(probe, 'reset'), \
                patch.object(probe, 'call', return_value=(200, None)), \
                patch.object(probe, 'rpc', return_value={'results': []}):
            path = Path(folder) / 'result.json'
            capture = probe.prefill('candidate-loaded', path, trace=True)
            engine = probe.engine('official', path)
        self.assertEqual(capture['trace_acceptance'], 'not_evaluated')
        self.assertNotIn('passed', capture)
        self.assertTrue(engine['passed'])
        self.assertNotIn('capture_succeeded', engine)

    def test_cli_existing_run_fails_before_network_or_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            run = probe.reserve_run(root, 'existing')
            receipt = run / 'evidence/official-smoke.json'
            receipt.write_text('original')
            result = subprocess.run([sys.executable, str(ROOT / 'probe_model.py'), 'official', 'smoke',
                                     '--output-dir', str(root), '--run-id', 'existing'],
                                    check=False, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('FileExistsError', result.stderr)
            self.assertEqual(receipt.read_text(), 'original')

    def test_rejected_first_completion_retains_raw_response(self):
        bad = response(3)
        with tempfile.TemporaryDirectory() as folder, patch.object(probe, 'ROOT', Path(folder)), \
                patch.object(probe, 'call', return_value=(200, bad['response'])):
            observed = probe.complete('synthetic prompt')
            with self.assertRaisesRegex(ValueError, 'token IDs'):
                probe.fingerprint(observed)
            self.assertEqual(json.loads(Path(observed['raw_receipt']).read_text())['response'], bad['response'])

    def test_cli_failure_receipt_preserves_the_error(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result = subprocess.run([sys.executable, str(ROOT / 'probe_model.py'), 'official', 'smoke',
                                     '--url', 'unsupported-scheme://qualification',
                                     '--output-dir', str(root), '--run-id', 'failure'],
                                    check=False, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            record = json.loads((root / 'runs/failure/evidence/validation-failure.json').read_text())
            self.assertFalse(record['validation_passed'])
            self.assertIn('unsupported-scheme', record['error'])


class TraceEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.entries, self.files = [], {}
        for node in range(1, 5):
            for stage, scale in (('prefill', 0), ('prefill', 1), ('decode', 0), ('decode', 1)):
                name = f'rank{node - 1}-{stage}-s{scale}.json.gz'
                events = [{'cat': 'cpu_op', 'name': 'aten::copy_'},
                          {'cat': 'cuda_runtime', 'name': 'cudaGraphLaunch', 'ph': 'X', 'dur': 1,
                           'args': {'correlation': node * 100 + scale, 'phase': stage}},
                          {'cat': 'kernel', 'name': '_dot_kernel' if scale else 'stock_mhc'}]
                raw = gzip.compress(json.dumps({'distributedInfo': {'rank': node - 1, 'world_size': 4},
                                                'traceEvents': events}).encode(), mtime=0)
                self.files[name] = raw
                self.entries.append({'node': node, 'stage': stage, 'scale': scale, 'path': name,
                                     'sha256': hashlib.sha256(raw).hexdigest(),
                                     'source_trace_sha256': hashlib.sha256(raw).hexdigest(),
                                     'source_trace_name': name,
                                     'events_sha256': hashlib.sha256(json.dumps(events, separators=(',', ':')).encode()).hexdigest()})

    def check(self, entries=None):
        return traces.check_manifest({'traces': self.entries if entries is None else entries}, self.files.__getitem__)

    def test_complete_trace_gate_has_explicit_scope(self):
        result = self.check()
        self.assertEqual(result['known_steering_event_gate'], 'passed')
        self.assertEqual(result['generic_copy_launch_sp_materialization'], 'not_evaluated')
        self.assertNotIn('passed', result)
        expected = {(node, stage, scale) for node in (1, 2, 3, 4)
                    for stage, scale in (('prefill', 0), ('prefill', 1), ('decode', 0), ('decode', 1))}
        self.assertEqual({(r['node'], r['stage'], r['scale']) for r in result['traces']}, expected)

    def test_renamed_capture_cannot_be_reused_for_another_stage(self):
        for field in ('source_trace_sha256', 'source_trace_name', 'sha256', 'events_sha256'):
            entries = copy.deepcopy(self.entries)
            entries[1][field] = entries[0][field]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'Repeated trace capture'):
                self.check(entries)

    def test_missing_rank_duplicate_trace_or_missing_file_fails(self):
        with self.assertRaisesRegex(ValueError, 'sixteen unique'):
            self.check(self.entries[:-1])
        with self.assertRaisesRegex(ValueError, 'sixteen unique'):
            self.check(self.entries[:-1] + [self.entries[0]])
        del self.files[self.entries[0]['path']]
        with self.assertRaises(KeyError):
            self.check()

    def test_changed_bytes_cannot_use_old_trace_hash(self):
        self.files[self.entries[0]['path']] += b'changed'
        with self.assertRaisesRegex(ValueError, 'SHA mismatch'):
            self.check()

    def test_stock_forbidden_event_fails_even_with_valid_hashes(self):
        entry = next(x for x in self.entries if x['scale'] == 0)
        trace = json.loads(gzip.decompress(self.files[entry['path']]))
        trace['traceEvents'].append({'cat': 'kernel', 'name': '_project_kernel'})
        self.replace(entry, trace)
        with self.assertRaisesRegex(ValueError, 'forbidden'):
            self.check()

    def replace(self, entry, trace):
        raw = gzip.compress(json.dumps(trace).encode(), mtime=0)
        self.files[entry['path']] = raw
        entry['sha256'] = hashlib.sha256(raw).hexdigest()
        entry['events_sha256'] = hashlib.sha256(json.dumps(trace['traceEvents'], separators=(',', ':')).encode()).hexdigest()

    def test_independent_trace_guards_fail_with_valid_hashes(self):
        for case, message in (('cpu', 'nonempty GPU kernel and CPU-op'),
                              ('gpu', 'nonempty GPU kernel and CPU-op'),
                              ('rank', 'rank/world'), ('replay', 'graph replay'),
                              ('annotation', 'graph replay')):
            self.setUp()
            entry = next(x for x in self.entries if x['stage'] == 'decode')
            trace = json.loads(gzip.decompress(self.files[entry['path']]))
            if case in ('cpu', 'gpu'):
                category = 'cpu_op' if case == 'cpu' else 'kernel'
                trace['traceEvents'] = [e for e in trace['traceEvents'] if e['cat'] != category]
            elif case == 'rank':
                trace['distributedInfo']['rank'] = 99
            elif case == 'annotation':
                for event in trace['traceEvents']:
                    if event['name'] == 'cudaGraphLaunch':
                        event['cat'] = 'user_annotation'
            else:
                trace['traceEvents'] = [e for e in trace['traceEvents'] if e['name'] != 'cudaGraphLaunch']
            self.replace(entry, trace)
            with self.subTest(case=case), self.assertRaisesRegex(ValueError, message):
                self.check()


class StockEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.parent = json.loads((ROOT / 'evidence/mhc-parent.json').read_text())
        self.candidate = json.loads((ROOT / 'evidence/mhc-candidate.json').read_text())

    def test_real_independent_mhc_records_pass(self):
        self.assertEqual(stock.check_mhc(self.parent, self.candidate)['cases'], 12)

    def test_same_implementation_cannot_be_its_own_oracle(self):
        with self.assertRaisesRegex(ValueError, 'arm/source identity'):
            stock.check_mhc(self.parent, self.parent)
        self.candidate['mhc_source_sha256'] = self.parent['mhc_source_sha256']
        with self.assertRaisesRegex(ValueError, 'arm/source identity'):
            stock.check_mhc(self.parent, self.candidate)

    def test_null_or_invalid_digest_contents_fail(self):
        for field, value in (('input_sha256', {'x': None}),
                             ('input_sha256', {**self.parent['cases'][0]['input_sha256'], 'writer': None}),
                             ('output_sha256', [None] * 5),
                             ('output_sha256', ['not-a-sha'] * 5)):
            self.setUp()
            for record in (self.parent, self.candidate):
                record['cases'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, 'byte/replay evidence'):
                stock.check_mhc(self.parent, self.candidate)

    def test_matching_bad_case_metadata_still_fails(self):
        for field, value in (('seed', 0), ('mode', 'wrong'), ('world', True),
                             ('rows', 2), ('output_shapes', [[1]] * 5)):
            self.setUp()
            for record in (self.parent, self.candidate):
                record['cases'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                stock.check_mhc(self.parent, self.candidate)

    def test_real_gpu_junit_passes_but_unrelated_twenty_nine_cases_fail(self):
        original = ROOT / 'evidence/gpu-kernels.xml'
        self.assertEqual(stock.check_junit(original)['recorded_tests'], 29)
        tree = ET.parse(original)
        for index, case in enumerate(tree.iter('testcase')):
            case.set('name', f'unrelated-{index}')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'changed.xml'
            tree.write(path)
            with self.assertRaisesRegex(ValueError, 'case identity'):
                stock.check_junit(path)

    def test_full_gpu_case_manifest_cannot_hide_failed_error_or_skipped_status(self):
        for tag in ('failure', 'error', 'skipped'):
            tree = ET.parse(ROOT / 'evidence/gpu-kernels.xml')
            ET.SubElement(next(tree.iter('testcase')), tag)
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'status.xml'
                tree.write(path)
                with self.subTest(tag=tag), self.assertRaisesRegex(ValueError, '29 unique passing'):
                    stock.check_junit(path)


if __name__ == '__main__':
    unittest.main()
