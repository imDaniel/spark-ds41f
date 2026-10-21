"""Synthetic CPU control-flow tests only, never native capacity corpus evidence."""
import copy
import json
import marshal
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import q03_capacity_corpus_export as X
E, C, Q = X.E, X.C, X.Q
import test_q03_native_corpus_export as fixtures
FixtureTokenizer = fixtures.FixtureTokenizer


class CapacityExporterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def child(self, code):
        return subprocess.run([sys.executable, '-B', '-c',
            'import sys\nsys.path.insert(0, sys.argv[1])\n' + code, str(ROOT / 'scripts')],
            capture_output=True, text=True, timeout=20)

    def fixture(self):
        helper = fixtures.ExporterTest(); helper.root = self.root
        return helper.export_fixture()

    def test_fixed_four_exact_distinct_whole_text_rows(self):
        rows = [X.construct_row(FixtureTokenizer(), i) for i in range(4)]
        self.assertEqual(len({r['text'] for r, _ in rows}), 4)
        self.assertEqual(len({tuple(r['prompt_ids']) for r, _ in rows}), 4)
        for row, evidence in rows:
            self.assertEqual(len(row['prompt_ids']), 64512)
            self.assertEqual(row['prompt_ids'], [ord(c) for c in evidence['rendered_text']])
            self.assertLessEqual(evidence['candidate_calls'], 512)
            self.assertLessEqual(len(row['text'].encode()), X.MAX_TEXT_BYTES)
        self.assertEqual(rows[0], X.construct_row(FixtureTokenizer(), 0))
        for index in (-1, 4, True, 1.0):
            with self.subTest(index=index), self.assertRaisesRegex(RuntimeError, 'only four'):
                X.construct_row(FixtureTokenizer(), index)

    def test_block_overshoot_is_rejected_not_clipped(self):
        class Observe(FixtureTokenizer):
            overshoots = 0
            def __call__(self, text, **kwargs):
                result = super().__call__(text, **kwargs)
                if len(result['input_ids']) > 64512: self.overshoots += 1
                return result
        tokenizer = Observe()
        row, rendered = X.construct_row(tokenizer, 0)
        self.assertGreater(tokenizer.overshoots, 0)
        self.assertEqual(row['prompt_ids'], [ord(c) for c in rendered['rendered_text']])

    def test_nonmonotonic_boundary_merge_rejected_then_smaller_full_text_measured(self):
        class BoundaryMerge(FixtureTokenizer):
            contractions = 0
            def __call__(self, text, **kwargs):
                result = super().__call__(text, **kwargs)
                # Synthetic boundary merge: a particular appended keyword
                # reduces the full ID count. Try another measured full text.
                if text.endswith(' access<A></think>'):
                    self.contractions += 1
                    return {'input_ids': [77]}
                return result
        tokenizer = BoundaryMerge()
        row, _ = X.construct_row(tokenizer, 0)
        self.assertGreater(tokenizer.contractions, 0)
        self.assertEqual(len(row['prompt_ids']), 64512)

    def test_nonmonotonic_plateau_fails_closed_without_changing_bounds(self):
        class Plateau(FixtureTokenizer):
            def __call__(self, text, **kwargs):
                ids = super().__call__(text, **kwargs)['input_ids']
                return {'input_ids': [77] if text.count('Visitors should') in (10, 11)
                        and len(text) < 20000 else ids}
        with self.assertRaisesRegex(RuntimeError, 'bounded capacity construction exhausted'):
            X.construct_row(Plateau(), 0)

    def test_http_truncation_never_repairs_oversize_prompt(self):
        class Clip(FixtureTokenizer):
            def __call__(self, text, **kwargs):
                ids = super().__call__(text, **kwargs)['input_ids']
                return {'input_ids': ids[:64512] if kwargs['truncation'] else ids}
        with self.assertRaisesRegex(RuntimeError, 'truncation'):
            X.verify_exact(Clip(), 'a' * 65000)
        with self.assertRaisesRegex(RuntimeError, 'exactly 64512'):
            X.verify_exact(FixtureTokenizer(), 'short')

    def test_equal_length_http_and_wrapper_id_drift_fail(self):
        class HttpDrift(FixtureTokenizer):
            def __call__(self, text, **kwargs):
                ids = super().__call__(text, **kwargs)['input_ids']
                if kwargs['truncation']: ids[-1] += 1
                return {'input_ids': ids}
        class WrapperDrift(FixtureTokenizer):
            def apply_chat_template(self, *args, **kwargs):
                result = super().apply_chat_template(*args, **kwargs)
                if kwargs['tokenize']: result[0] += 1
                return result
        for tokenizer in (HttpDrift(), WrapperDrift()):
            with self.assertRaisesRegex(RuntimeError, 'drift'):
                X.construct_row(tokenizer, 0)

    def test_finite_candidate_text_and_stuck_bounds_fail_closed(self):
        class Stuck(FixtureTokenizer):
            def __call__(self, *args, **kwargs): return {'input_ids': [1]}
        with self.assertRaisesRegex(RuntimeError, 'no bounded exact-length'):
            X.construct_row(Stuck(), 0)
        for name, bound, message in (('MAX_CANDIDATES_PER_ROW', 2, 'exhausted'),
                                     ('MAX_TEXT_BYTES', 500, 'text size')):
            with patch.object(X, name, bound), self.assertRaisesRegex(RuntimeError, message):
                X.construct_row(FixtureTokenizer(), 0)

    def test_missing_retained_provenance_fails_before_backend_or_output(self):
        path, _ = self.fixture()
        original = Q.json_bytes(path.read_bytes())
        for field in ('weights_manifest', 'runtime_metadata'):
            for value in ({}, None, {'path': '/absent', 'sha256': 'fake'}):
                bindings = copy.deepcopy(original); bindings[field] = value
                path.write_bytes(Q.canonical(bindings))
                with patch.object(X, 'isolate'), \
                     patch.object(X.importlib, 'import_module', side_effect=AssertionError('backend must not load')):
                    with self.assertRaisesRegex(RuntimeError, 'provenance pin'):
                        X.run(path, self.root / 'result')
                self.assertFalse((self.root / 'result').exists())
            bindings = copy.deepcopy(original); bindings.pop(field)
            with self.assertRaisesRegex(RuntimeError, 'complete native'):
                X.validate_bindings(bindings)

    def test_composition_writes_capacity_consumer_outputs_and_opaque_manifest_pin(self):
        path, export = self.fixture()
        original_manifest = export['artifacts']['weights_manifest'].copy()
        manifest_raw = b'{"retained":"opaque original bytes, never replaced"}\n'
        Path(original_manifest['path']).write_bytes(manifest_raw)
        patches = [patch.object(X, 'isolate'),
            patch.object(X, 'validate_bindings', side_effect=lambda _: (copy.deepcopy(export), {}, {'file': 'a'})),
            patch.object(X.importlib, 'import_module', return_value=SimpleNamespace(TokenizersBackend=object)),
            patch.object(E, 'package_evidence', return_value={'version': 'TEST-ONLY', 'files': {'synthetic': True}}),
            patch.object(E, 'native_tokenizer', return_value=FixtureTokenizer()),
            patch.object(Q, 'verify_artifacts', return_value=[])]
        for p in patches: p.start(); self.addCleanup(p.stop)
        result = X.run(path, self.root / 'result')
        export = Q.owner.pinned_json(result['tokenizer_export'])
        evidence = Q.owner.pinned_json(result['cpu_evidence'])
        corpus = C.validate_corpus(Q.owner.pinned_json(result['corpus']))
        C.validate_binding({'workload': C.WORKLOAD, 'corpus': result['corpus'],
                            'metric_labels': {'model_name': Q.MODEL, 'engine': '0'}})
        self.assertEqual(export['workload'], 'capacity_64k_c4')
        self.assertEqual(corpus['rows'], export['rows'])
        self.assertEqual(len(corpus['rows']), 4)
        self.assertEqual(evidence['weights']['manifest'], original_manifest)
        self.assertEqual(Path(original_manifest['path']).read_bytes(), manifest_raw)
        self.assertFalse(evidence['weights']['weight_bytes_rehashed'])
        self.assertEqual(evidence['scope'], 'CPU_PREPARATION_ONLY_NOT_FIELD_CAPACITY')
        self.assertEqual(set(evidence['preparation_sources']), {
            'q03_capacity_corpus_export.py', 'q03_capacity.py', 'q03_native_corpus_export.py',
            'q03_qualification.py', 'q03_owner_model.py', 'q03_held_window.py'})
        self.assertEqual((self.root / 'result/prepared/native-export.json').read_bytes(),
                         Path(result['tokenizer_export']['path']).read_bytes())
        with self.assertRaisesRegex(RuntimeError, 'new canonical'):
            X.run(path, self.root / 'result')
        with self.assertRaisesRegex(RuntimeError, 'overlaps immutable'):
            X.run(path, Path(corpus['artifacts']['model_root']) / 'result')

    def test_runtime_package_mismatch_and_contract_drift_fail_before_tokenizer(self):
        path, export = self.fixture()
        with patch.object(X, 'isolate'), \
             patch.object(X, 'validate_bindings', return_value=(export, {}, {})), \
             patch.object(X.importlib, 'import_module', return_value=SimpleNamespace()), \
             patch.object(E, 'package_evidence', return_value={'version': 'DRIFT'}), \
             patch.object(E, 'native_tokenizer', side_effect=AssertionError('must not load')):
            with self.assertRaisesRegex(RuntimeError, 'runtime version drift'):
                X.run(path, self.root / 'result')
            with patch.object(C, 'INPUT_TOKENS', 2048):
                with self.assertRaisesRegex(RuntimeError, 'consumer contract'):
                    X.run(path, self.root / 'result')
        self.assertFalse((self.root / 'result').exists())

    def test_all_helpers_source_isolated_and_cpu_guards_active(self):
        result = self.child('''import importlib.util
import q03_capacity_corpus_export as X
X.isolate()
for module in (X.E, X.C, X.Q, X.Q.owner, sys.modules['q03_held_window']):
    assert module.__spec__.cached.startswith(X.BYTECODE_PREFIX + '/')
for name in X.E.FORBIDDEN_IMPORTS:
    assert importlib.util.find_spec(name) is None
for event, args in [('socket.connect', ()), ('subprocess.Popen', ()),
                    ('open', ('/dev/nvidia0', 'rb', 0))]:
    try: sys.audit(event, *args)
    except RuntimeError: pass
    else: raise AssertionError(event + ' allowed')
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_preimported_capacity_or_exporter_refused(self):
        for name in ('q03_capacity', 'q03_native_corpus_export'):
            result = self.child(f'''import {name}
import q03_capacity_corpus_export as X
try: X.isolate()
except RuntimeError as error: assert 'fresh interpreter' in str(error)
else: raise AssertionError('preimported helper accepted')
''')
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_capacity_helper_origin_and_spec_guards(self):
        with patch.object(C, '__file__', '/tmp/shadow/q03_capacity.py'):
            with self.assertRaisesRegex(RuntimeError, 'import origin'):
                X.preparation_sources()
        result = self.child('''from types import SimpleNamespace
import q03_capacity_corpus_export as X
X.C.__spec__ = SimpleNamespace(cached='/tmp/stale.pyc')
try: X.isolate()
except RuntimeError as error: assert 'fresh interpreter' in str(error)
else: raise AssertionError('stale helper spec accepted')
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_entire_preparation_closure_must_share_new_exporter_directory(self):
        for name in E.preparation_sources():
            sources = E.preparation_sources()
            sources[name]['path'] = '/tmp/shadow/' + name
            with self.subTest(helper=name), patch.object(E, 'preparation_sources', return_value=sources):
                with self.assertRaisesRegex(RuntimeError, 'different source directory'):
                    X.preparation_sources()

    def test_stale_capacity_bytecode_is_not_loaded(self):
        for name in ('q03_capacity_corpus_export.py', 'q03_capacity.py', 'q03_native_corpus_export.py',
                     'q03_qualification.py', 'q03_owner_model.py', 'q03_held_window.py'):
            (self.root / name).write_bytes((ROOT / 'scripts' / name).read_bytes())
        source = self.root / 'q03_capacity.py'; stat = source.stat()
        marker = self.root / 'STALE_EXECUTED'
        payload = compile(f'open({str(marker)!r}, "w").write("poison")', str(source), 'exec')
        raw = (importlib_magic() + struct.pack('<III', 0, int(stat.st_mtime), stat.st_size)
               + marshal.dumps(payload))
        cache = self.root / '__pycache__'; cache.mkdir()
        (cache / f'q03_capacity.{sys.implementation.cache_tag}.pyc').write_bytes(raw)
        result = subprocess.run([sys.executable, '-B', str(self.root / 'q03_capacity_corpus_export.py'), '--help'],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())

    def test_original_defaults_unchanged_and_cli_has_no_general_target(self):
        self.assertEqual((E.TARGET_LENGTH, len(E.TOPICS), E.MAX_CANDIDATES_PER_ROW), (2048, 16, 4096))
        self.assertEqual(len(E.construct_row(FixtureTokenizer(), 0)[0]['prompt_ids']), 2048)
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/q03_capacity_corpus_export.py'),
                                 '--target-length', '1048576'], capture_output=True, text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        result = self.child('''before = sys.dont_write_bytecode, sys.pycache_prefix
import q03_capacity_corpus_export
assert (sys.dont_write_bytecode, sys.pycache_prefix) == before
''')
        self.assertEqual(result.returncode, 0, result.stderr)


def importlib_magic():
    import importlib.util
    return importlib.util.MAGIC_NUMBER


if __name__ == '__main__':
    unittest.main()
