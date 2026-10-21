"""Synthetic CPU unit fixtures only. No native corpus/field proof is produced."""
import copy
import hashlib
import importlib.util
import json
import marshal
import os
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import q03_native_corpus_export as E
import q03_qualification as Q


class FixtureTokenizer:
    """Explicit character-ID test double; never reachable through exporter CLI."""
    def apply_chat_template(self, messages, *, thinking, tokenize):
        assert thinking is False and len(messages) == 1 and messages[0]['role'] == 'user'
        rendered = '<B><U>' + messages[0]['content'] + '<A></think>'
        return self(rendered, add_special_tokens=False, truncation=False)['input_ids'] if tokenize else rendered

    def __call__(self, text, *, add_special_tokens, truncation, max_length=None):
        assert add_special_tokens is False
        ids = [ord(char) for char in text]
        if truncation:
            assert max_length == E.HTTP_MAX_LENGTH
            ids = ids[:max_length]
        return {'input_ids': ids}


class ExporterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_real_text_construction_is_exact_distinct_deterministic_in_fixture(self):
        rows = [E.construct_row(FixtureTokenizer(), i)[0] for i in range(16)]
        self.assertEqual(len({row['text'] for row in rows}), 16)
        self.assertEqual(len({tuple(row['prompt_ids']) for row in rows}), 16)
        self.assertTrue(all(len(row['prompt_ids']) == 2048 for row in rows))
        self.assertEqual(rows[0], E.construct_row(FixtureTokenizer(), 0)[0])
        self.assertTrue(all(row['text'].startswith(E.TOPICS[row['id']][1]) for row in rows))

    def test_constructor_does_not_assume_monotonic_or_additive_lengths(self):
        class Nonmonotonic(FixtureTokenizer):
            def __call__(self, text, **kwargs):
                ids = super().__call__(text, **kwargs)['input_ids']
                if text.endswith(' access<A></think>'):
                    ids = [1000]  # A measured boundary merge; must reject this candidate.
                return {'input_ids': ids}
        row, _ = E.construct_row(Nonmonotonic(), 0)
        self.assertEqual(len(row['prompt_ids']), 2048)

    def test_no_truncation_fallback_even_when_http_returns_target(self):
        class Truncating(FixtureTokenizer):
            def __call__(self, text, **kwargs):
                ids = super().__call__(text, **kwargs)['input_ids']
                return {'input_ids': ids[:2048] if kwargs['truncation'] else ids}
        with self.assertRaisesRegex(RuntimeError, 'truncation'):
            E.verify_exact(Truncating(), 'a' * 3000)

    def test_native_http_id_drift_rejected_even_at_same_length(self):
        class Drift(FixtureTokenizer):
            def __call__(self, text, **kwargs):
                ids = super().__call__(text, **kwargs)['input_ids']
                if kwargs['truncation']:
                    ids[0] += 1
                return {'input_ids': ids}
        with self.assertRaisesRegex(RuntimeError, 'drift'):
            E.construct_row(Drift(), 0)

    def test_wrapper_encode_drift_rejected(self):
        class Drift(FixtureTokenizer):
            def apply_chat_template(self, *args, **kwargs):
                value = super().apply_chat_template(*args, **kwargs)
                if kwargs['tokenize']:
                    value[-1] += 1
                return value
        with self.assertRaisesRegex(RuntimeError, 'drift'):
            E.construct_row(Drift(), 0)

    def test_no_progress_and_candidate_budget_fail_closed(self):
        class Stuck(FixtureTokenizer):
            def __call__(self, *args, **kwargs):
                return {'input_ids': [1]}
        with self.assertRaisesRegex(RuntimeError, 'no bounded exact-length'):
            E.construct_row(Stuck(), 0)
        with patch.object(E, 'MAX_CANDIDATES_PER_ROW', 2):
            with self.assertRaisesRegex(RuntimeError, 'exhausted'):
                E.construct_row(FixtureTokenizer(), 0)

    def test_boolean_or_batched_ids_rejected(self):
        for ids in ([True], [[1]], [-1]):
            tokenizer = SimpleNamespace(apply_chat_template=lambda *a, **k: 'rendered')
            class Bad:
                apply_chat_template = tokenizer.apply_chat_template
                def __call__(self, *args, **kwargs): return {'input_ids': ids}
            with self.assertRaisesRegex(RuntimeError, 'integer IDs'):
                E.encode_text(Bad(), 'text')

    def test_existing_weight_manifest_exact_83_no_hash_scan(self):
        pairs = {f'model-{i:05}.safetensors': 'a' * 64 for i in range(83)}
        raw = json.dumps({'files': pairs}).encode()
        with patch.object(E, 'sha_file', side_effect=AssertionError('no weight scan')):
            self.assertEqual(E.weight_inventory(raw), pairs)
        raw = ''.join(f'{sha}  {name}\n' for name, sha in pairs.items()).encode()
        self.assertEqual(E.weight_inventory(raw), pairs)
        for bad in (raw + raw.splitlines(keepends=True)[0], b'a' * 64 + b'  ../weights\n',
                    json.dumps({'files': dict(list(pairs.items())[:82])}).encode()):
            with self.assertRaises(RuntimeError): E.weight_inventory(bad)

    def test_actual_source_hash_drift_precedes_execution(self):
        name = next(iter(E.SOURCE_FILES))
        path = self.root / name
        path.parent.mkdir(parents=True)
        path.write_text('raise AssertionError("must never execute")\n')
        with self.assertRaisesRegex(RuntimeError, 'source drift'):
            E.read_sources(str(self.root))

    def test_projection_preserves_bodies_and_never_imports_heavy_dependencies(self):
        raw = b'import forbidden_heavy_module\ndef untouched(value):\n    return value + 4\n'
        ns = {}
        E.execute_projection(raw, 'synthetic-source.py', ns)
        self.assertEqual(ns['untouched'](3), 7)
        ns = {}
        E.execute_projection(raw, 'synthetic-source.py', ns, function='untouched')
        self.assertEqual(ns['untouched'](8), 12)
        with self.assertRaisesRegex(RuntimeError, 'projection differs'):
            E.execute_projection(raw, 'synthetic-source.py', {}, function='missing')

    def test_source_constructor_wires_local_only_and_exact_wrapper(self):
        # Small synthetic source closure tests call wiring, not native semantics.
        sources = {
            'vllm/tokenizers/protocol.py': b'class TokenizerLike: pass\n',
            'vllm/utils/tool_names.py': b'def normalize_tool_namespace(value): return value\n',
            'vllm/tokenizers/hf.py': b'def get_cached_tokenizer(value):\n    value.cached = True\n    return value\n',
            'vllm/tokenizers/deepseek_v41_encoding.py': b'IMAGE_PLACEHOLDER = "image"\n',
            'vllm/tokenizers/deepseek_v41.py': b'''class DeepseekV41Tokenizer(TokenizerLike):
    @classmethod
    def from_pretrained(cls, *args, **kwargs):
        return get_cached_tokenizer(TokenizersBackend.from_pretrained(*args, **kwargs))
'''}
        backend = SimpleNamespace(from_pretrained=lambda *a, **k: SimpleNamespace(args=a, kwargs=k))
        tokenizer = E.native_tokenizer(sources, self.root, backend)
        self.assertEqual(tokenizer.args, (str(self.root),))
        self.assertEqual(tokenizer.kwargs, {'local_files_only': True})
        self.assertTrue(tokenizer.cached)

    def package_fixture(self):
        (self.root / 'tokenizers').mkdir()
        path = self.root / 'tokenizers/__init__.py'
        path.write_text('__version__ = "TEST-ONLY"\n')
        dist = SimpleNamespace(version='TEST-ONLY', files=[Path('tokenizers/__init__.py')],
                               locate_file=lambda relative: self.root / relative)
        module = SimpleNamespace(__version__='TEST-ONLY', __file__=str(path))
        return dist, module

    def test_package_actual_bytes_versions_and_origin(self):
        dist, module = self.package_fixture()
        with patch.object(E.importlib.metadata, 'distribution', return_value=dist):
            evidence = E.package_evidence('tokenizers', module)
            self.assertEqual(evidence['version'], 'TEST-ONLY')
            self.assertEqual(evidence['files']['tokenizers/__init__.py']['sha256'],
                             hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest())
            module.__version__ = 'DRIFT'
            with self.assertRaisesRegex(RuntimeError, 'version drift'): E.package_evidence('tokenizers', module)
            module.__version__ = 'TEST-ONLY'; module.__file__ = str(self.root / 'shadow.py')
            with self.assertRaisesRegex(RuntimeError, 'origin'): E.package_evidence('tokenizers', module)

    def test_package_byte_inventory_changes_when_content_changes(self):
        dist, module = self.package_fixture()
        with patch.object(E.importlib.metadata, 'distribution', return_value=dist):
            first = E.package_evidence('tokenizers', module)
            Path(module.__file__).write_text('# changed byte identity\n')
            self.assertNotEqual(first, E.package_evidence('tokenizers', module))

    def test_process_audit_refuses_network_child_and_gpu(self):
        for event, args in [('socket.connect', ()), ('subprocess.Popen', ()),
                            ('os.system', ()), ('open', ('/dev/nvidia0', 'rb', 0)),
                            ('open', ('/dev/dri/renderD128', 'rb', 0))]:
            with self.subTest(event=event), self.assertRaises(RuntimeError): E.offline_audit(event, args)
        E.offline_audit('open', (str(self.root / 'tokenizer.json'), 'rb', 0))
        for name in ('torch', 'vllm', 'torch.cuda', 'triton'):
            with self.assertRaisesRegex(RuntimeError, 'CPU-only'): E.PreparationIsolation().find_spec(name)

    def test_real_fresh_process_isolation_is_inert_and_blocks_socket(self):
        code = '''import sys
sys.path.insert(0, sys.argv[1])
import q03_native_corpus_export as E
E.isolate()
import socket
try:
    socket.socket()
except RuntimeError as error:
    assert "offline CPU" in str(error)
else:
    raise AssertionError("network allowed")
assert E.os.environ['USE_TORCH'] == '0'
assert not any(n.split('.')[0] in E.FORBIDDEN_IMPORTS for n in sys.modules)
'''
        result = subprocess.run([sys.executable, '-B', '-c', code, str(ROOT / 'scripts')],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_library_import_preserves_callers_bytecode_settings(self):
        code = """import sys
before = sys.dont_write_bytecode, sys.pycache_prefix
sys.path.insert(0, sys.argv[1])
import q03_native_corpus_export
assert (sys.dont_write_bytecode, sys.pycache_prefix) == before
"""
        result = subprocess.run([sys.executable, '-B', '-c', code, str(ROOT / 'scripts')],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_timestamp_valid_stale_helper_bytecode_cannot_execute(self):
        for name in ('q03_native_corpus_export.py', 'q03_qualification.py',
                     'q03_owner_model.py', 'q03_held_window.py'):
            (self.root / name).write_bytes((ROOT / 'scripts' / name).read_bytes())
        source = self.root / 'q03_qualification.py'
        marker = self.root / 'STALE_BYTECODE_EXECUTED'
        payload = compile(f'open({str(marker)!r}, "w").write("poison")', str(source), 'exec')
        stat = source.stat()
        raw = (importlib.util.MAGIC_NUMBER + struct.pack('<III', 0, int(stat.st_mtime), stat.st_size)
               + marshal.dumps(payload))
        cache = self.root / '__pycache__'; cache.mkdir()
        (cache / f'q03_qualification.{sys.implementation.cache_tag}.pyc').write_bytes(raw)
        result = subprocess.run([sys.executable, '-B', str(self.root / 'q03_native_corpus_export.py'), '--help'],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())

    def test_fresh_process_requirement_rejects_preimported_runtime(self):
        with patch.dict(sys.modules, {'torch': SimpleNamespace()}):
            with self.assertRaisesRegex(RuntimeError, 'fresh interpreter'): E.isolate()

    def bindings(self):
        model = self.root / 'model'; model.mkdir()
        for name in ('config.json', 'tokenizer.json', 'tokenizer_config.json'):
            (model / name).write_text('{}')
        return {'schema_version': 1, 'model_repository': E.MODEL_REPOSITORY,
                'model_revision': E.MODEL_REVISION, 'tokenizer_revision': E.MODEL_REVISION,
                'model_root': str(model), 'model_config_sha256': E.sha_file(model / 'config.json'),
                'tokenizer_assets_sha256': {name: E.sha_file(model / name) for name in ('tokenizer.json', 'tokenizer_config.json')},
                'special_tokens_file': 'tokenizer_config.json', 'weights_manifest': {},
                'runtime_metadata': {}, 'native_root': str(self.root / 'native')}

    def test_asset_revision_and_closure_negatives_precede_native_import(self):
        original = self.bindings()
        for change in (lambda value: value.update(model_revision='b' * 40),
                       lambda value: value['tokenizer_assets_sha256'].pop('tokenizer_config.json'),
                       lambda value: value.update(model_config_sha256='a' * 64),
                       lambda value: value['tokenizer_assets_sha256'].update({'../escape': 'a' * 64})):
            bindings = copy.deepcopy(original); change(bindings)
            with self.assertRaises(RuntimeError): E.validate_bindings(bindings)
        (Path(original['model_root']) / 'added_tokens.json').write_text('{}')
        with self.assertRaisesRegex(RuntimeError, 'closure'): E.validate_bindings(original)

    def test_config_alternate_tokenizer_and_remote_code_fail_closed(self):
        bindings = self.bindings()
        path = Path(bindings['model_root']) / 'tokenizer_config.json'
        for value in ({'auto_map': {'AutoTokenizer': 'untrusted.Code'}},
                      {'fast_tokenizer_files': ['elsewhere.json']},
                      {'tokenizer_file': '/outside/tokenizer.json'},
                      {'vocab_file': '../vocab.json'}, {'merges_file': '/outside/merges.txt'}):
            path.write_text(json.dumps(value))
            bindings['tokenizer_assets_sha256']['tokenizer_config.json'] = E.sha_file(path)
            with self.assertRaisesRegex(RuntimeError, 'alternate tokenizer'):
                E.validate_bindings(bindings)

    def export_fixture(self):
        bindings = self.bindings()
        identity = {key: 'a' * 64 for key in Q.IDENTITY_FIELDS}
        identity.update(model_repository=E.MODEL_REPOSITORY, model_revision=E.MODEL_REVISION,
                        tokenizer_revision=E.MODEL_REVISION,
                        tokenizer_assets_sha256=bindings['tokenizer_assets_sha256'],
                        encoding_source_sha256=Q.NATIVE_SOURCES['deepseek_v41_encoding.py'],
                        chat_template_sha256=Q.TEMPLATE_SHA,
                        special_tokens_sha256=bindings['tokenizer_assets_sha256']['tokenizer_config.json'],
                        transformers_version='TEST-ONLY', tokenizers_version='TEST-ONLY')
        artifacts = {'model_root': bindings['model_root'],
                     'weights_manifest': {'path': str(self.root / 'weights.json'), 'sha256': 'a' * 64},
                     'runtime_metadata': {'path': str(self.root / 'runtime.json'), 'sha256': 'a' * 64},
                     'native_sources': {name: {'path': str(self.root / name), 'sha256': sha}
                                        for name, sha in Q.NATIVE_SOURCES.items()},
                     'special_tokens_file': 'tokenizer_config.json'}
        bindings.update(weights_manifest=artifacts['weights_manifest'], runtime_metadata=artifacts['runtime_metadata'])
        path = self.root / 'bindings.json'; path.write_bytes(Q.canonical(bindings))
        export = {'schema_version': 1, 'tokenizer_mode': 'deepseek_v41', 'identity': identity,
                  'artifacts': artifacts, 'request': copy.deepcopy(Q.EXTRA), 'rows': []}
        return path, export

    def test_end_to_end_fixture_writes_all_text_ids_rendering_and_provenance(self):
        path, export = self.export_fixture()
        with patch.object(E, 'isolate'), \
             patch.object(E, 'validate_bindings', side_effect=lambda _: (copy.deepcopy(export), {}, {'file': 'a'})), \
             patch.object(E.importlib, 'import_module', return_value=SimpleNamespace(TokenizersBackend=object)), \
             patch.object(E, 'package_evidence', return_value={'version': 'TEST-ONLY', 'files': {'test-only': 'not-field-proof'}}), \
             patch.object(E, 'native_tokenizer', return_value=FixtureTokenizer()), \
             patch.object(Q, 'verify_artifacts', return_value=[]):
            result = E.run(path, self.root / 'result')
            retained = json.loads(Path(result['tokenizer_export']['path']).read_text())
            evidence = json.loads(Path(result['cpu_evidence']['path']).read_text())
            corpus = Q.validate_corpus(json.loads(Path(result['corpus']['path']).read_text()))
            self.assertEqual(corpus['rows'], retained['rows'])
            self.assertEqual(len(evidence['rendered_rows']), 16)
            self.assertFalse(evidence['weights']['weight_bytes_rehashed'])
            self.assertEqual(evidence['native_runtime_metadata'], export['artifacts']['runtime_metadata'])
            self.assertEqual(evidence['scope'], 'CPU_PREPARATION_ONLY_NOT_FIELD_QUALIFICATION')
            with self.assertRaisesRegex(RuntimeError, 'new canonical'):
                E.run(path, self.root / 'result')
            with self.assertRaisesRegex(RuntimeError, 'overlaps immutable'):
                E.run(path, Path(export['artifacts']['model_root']) / 'result')

    def test_runtime_package_version_drift_blocks_before_tokenizer_load(self):
        path, export = self.export_fixture()
        with patch.object(E, 'isolate'), \
             patch.object(E, 'validate_bindings', return_value=(export, {}, {})), \
             patch.object(E.importlib, 'import_module', return_value=SimpleNamespace()), \
             patch.object(E, 'package_evidence', return_value={'version': 'DRIFT'}), \
             patch.object(E, 'native_tokenizer', side_effect=AssertionError('must not load')):
            with self.assertRaisesRegex(RuntimeError, 'runtime version drift'):
                E.run(path, self.root / 'result')
            self.assertFalse((self.root / 'result').exists())

    def test_cli_help_never_imports_model_or_backend(self):
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/q03_native_corpus_export.py'), '--help'],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--bindings', result.stdout)


if __name__ == '__main__':
    unittest.main()
