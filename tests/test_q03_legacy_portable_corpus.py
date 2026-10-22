"""Synthetic portable CPU fixtures; no site/model/registered-corpus claim."""
import copy
from contextlib import ExitStack
import importlib.machinery
import importlib.util
import json
import os
import py_compile
import shutil
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_q03_legacy_corpus_export as F
FixtureTokenizer, ROOT = F.FixtureTokenizer, F.ROOT
import q03_legacy_portable_corpus as P

E, Q = P.E, P.Q


class PortableCorpusTest(unittest.TestCase):
    setUp = F.LegacyExporterTest.setUp
    export = F.LegacyExporterTest.export

    def setUp(self):
        F.LegacyExporterTest.setUp(self)
        self.assets = {'config.json': b'{}', 'tokenizer.json': b'FAKE TOKENIZER',
                       'tokenizer_config.json': b'{}\n'}
        self.asset_hashes = {name: Q.digest(raw) for name, raw in self.assets.items()}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(P, 'REGISTERED_ASSETS', self.asset_hashes))

    def site_export(self):
        value = self.export()
        value['identity'].update(model_config_sha256=self.asset_hashes['config.json'],
            tokenizer_assets_sha256={k: v for k, v in self.asset_hashes.items() if k != 'config.json'},
            special_tokens_sha256=self.asset_hashes['tokenizer_config.json'])
        return value

    def content(self):
        return E.construct_corpus(FixtureTokenizer(), self.site_export(), self.toolbox, content_only=True)[0]

    def portable_file(self, content=None):
        content = content or self.content()
        value = {'schema_version': 1, 'kind': P.KIND, 'admissibility': P.ADMISSIBILITY,
                 'content': content, 'content_sha256': Q.digest(Q.canonical(content))}
        path = self.root / 'preparation.json'
        Q.exclusive_json(path, value)
        return path, Q.digest(path.read_bytes()), value

    def cloud_bindings(self):
        root = self.root / 'assets'
        root.mkdir()
        for name, raw in self.assets.items():
            (root / name).write_bytes(raw)
        inventory = {name: {'sha256': sha, 'size': len(self.assets[name]), 'file_identity': {'opaque': True}}
                     for name, sha in self.asset_hashes.items()}
        inventory.update({f'model-{i:03d}.safetensors': {'sha256': 'f' * 64, 'size': 123,
                           'file_identity': {'device': 1, 'inode': i}} for i in range(80)})
        manifest = {'generator': 'fixture', 'schema_version': 1, 'algorithm': 'sha256',
                    'root_directory': '/unchanged/site/root', 'hostname': 'site', 'coverage': 'complete',
                    'timestamp_utc': 'original', 'files': inventory}
        path = self.root / 'weights.json'
        path.write_bytes(json.dumps(manifest, indent=2).encode())
        value = {'schema_version': 1, 'mode': 'TOKENIZER_ONLY_CLOUD', 'model_repository': E.N.MODEL_REPOSITORY,
                 'model_revision': E.N.MODEL_REVISION, 'tokenizer_revision': E.N.MODEL_REVISION,
                 'model_root': str(root), 'weights_manifest': {'path': str(path), 'sha256': Q.digest(path.read_bytes())},
                 'native_sources': {arm: {'head': head, 'root': str(self.root / arm)}
                                    for arm, head in E.NATIVE_HEADS.items()}, 'toolbox_root': str(self.toolbox_root)}
        return value, path.read_bytes()

    def test_content_path_free_and_not_replay_admissible(self):
        path, sha, value = self.portable_file()
        self.assertEqual(P.read_preparation(path, sha)[1], value)
        with self.assertRaises(RuntimeError):
            E.L.validate_corpus(value)
        with self.assertRaises(RuntimeError):
            E.L.validate_corpus(value['content'])
        binding = {'workload': E.L.WORKLOAD, 'case_id': 'warm4k_c1',
                   'corpus': {'path': str(path), 'sha256': sha},
                   'metric_labels': {'model_name': E.L.MODEL, 'engine': '0'}}
        with self.assertRaises(RuntimeError):
            E.L.validate_binding(binding)
        selection = {'arm': 'r6x188', 'steering': 'unloaded'}
        client = {'workload': E.L.WORKLOAD, 'case_id': 'warm4k_c1', 'corpus': value,
                  'corpus_sha256': sha, 'metric_labels': binding['metric_labels'], 'selection': selection}
        with self.assertRaises(RuntimeError):
            E.L.validate_client_plan({'selection': selection, 'client': {'legacy_case': client}})
        for location, extra in [('content', {'artifacts': {}}), ('response', {'path': '/site'})]:
            changed = copy.deepcopy(value['content'])
            (changed if location == 'content' else changed['documents'][0]['calibration'][0]['response']).update(extra)
            with self.subTest(location=location), self.assertRaises(RuntimeError):
                P.validate_content(changed)
        with self.assertRaisesRegex(RuntimeError, 'file hash differs'):
            P.read_preparation(path, '0' * 64)

    def test_cloud_reads_only_registered_assets_and_original_manifest(self):
        bindings, raw = self.cloud_bindings()
        with patch.object(E, 'read_native_sources', return_value={'native197-off': {}}), \
             patch.object(E, 'read_toolbox', return_value=self.toolbox), \
             patch.object(E.N, 'validate_bindings', side_effect=AssertionError('full site validator used in cloud')):
            identity, _, _, retained = P.validate_cloud_bindings(bindings)
            self.assertEqual(retained, raw)
            self.assertEqual(identity['weights_manifest_sha256'], Q.digest(raw))
            self.assertFalse(any(Path(bindings['model_root']).glob('*.safetensors')))
            (Path(bindings['model_root']) / 'tokenizer.json').write_bytes(b'DRIFT')
            with self.assertRaisesRegex(RuntimeError, 'asset drift'):
                P.validate_cloud_bindings(bindings)

    def test_cloud_requires_exact_manifest_and_asset_closure(self):
        bindings, raw = self.cloud_bindings()
        with patch.object(E, 'read_native_sources', return_value={}), patch.object(E, 'read_toolbox', return_value={}):
            bad = copy.deepcopy(bindings)
            bad['weights_manifest']['sha256'] = 'f' * 64
            with self.assertRaisesRegex(RuntimeError, 'raw manifest hash'):
                P.validate_cloud_bindings(bad)
            (Path(bindings['model_root']) / 'chat_template.jinja').write_text('unexpected')
            with self.assertRaisesRegex(RuntimeError, 'closure differs'):
                P.validate_cloud_bindings(bindings)

    def test_native_source_drift_rejects_before_tokenizer_construction(self):
        bindings, _ = self.cloud_bindings()
        source_name, raw = 'vllm/tokenizers/synthetic.py', b'# source fixture\n'
        for item in bindings['native_sources'].values():
            source = Path(item['root']) / source_name
            source.parent.mkdir(parents=True)
            source.write_bytes(raw)
        source.write_bytes(raw + b'# drift')
        path = self.root / 'cloud-bindings.json'
        path.write_bytes(Q.canonical(bindings))
        with patch.object(P, 'isolate'), patch.object(E, 'SOURCE_FILES', {source_name: Q.digest(raw)}), \
             patch.object(E.N, 'native_tokenizer', side_effect=AssertionError('tokenizer constructed')) as loader:
            with self.assertRaisesRegex(RuntimeError, 'source drift'):
                P.prepare(path, self.root / 'blocked-native-drift')
            loader.assert_not_called()
        self.assertFalse((self.root / 'blocked-native-drift').exists())

    def test_prepare_publishes_separate_actual_provenance_and_stdlib_snapshot(self):
        bindings, manifest_raw = self.cloud_bindings()
        path = self.root / 'cloud-bindings.json'
        path.write_bytes(Q.canonical(bindings))
        sources = {'native197-off': {}}
        modules = {'transformers': SimpleNamespace(TokenizersBackend=object), 'tokenizers': object()}
        evidence = {name: {'version': 'FIXTURE'} for name in modules}
        with patch.object(P, 'isolate'), patch.object(E, 'read_native_sources', return_value=sources), \
             patch.object(E, 'read_toolbox', return_value=self.toolbox), \
             patch.object(P, 'packages', return_value=(modules, evidence)), patch.object(P, 'recheck_packages'), \
             patch.object(E.N, 'native_tokenizer', return_value=FixtureTokenizer()):
            result = P.prepare(path, self.root / 'cloud-out')
        prep = P.read_preparation(result['preparation']['path'], result['preparation']['sha256'])[1]
        provenance = json.loads(Path(result['cloud_provenance']['path']).read_text())
        self.assertEqual(provenance['weights']['raw_manifest_sha256'], Q.digest(manifest_raw))
        self.assertFalse(provenance['weights']['weights_present_verified'])
        self.assertEqual(provenance['packages'], evidence)
        self.assertNotIn('artifacts', prep['content'])
        self.assertEqual(len(provenance['rendered_rows']), 64)
        retained = {p.name: p.read_bytes() for p in Path(provenance['stdlib_sources_root']).iterdir()}
        self.assertEqual(P.snapshot_inventory(retained), prep['content']['generator']['stdlib_sources'])
        for file in (self.root / 'cloud-out').rglob('*'):
            self.assertEqual(file.stat().st_mode & 0o777, 0o700 if file.is_dir() else 0o600)

    def bind_fixture(self, path, sha, *, change_identity=False, tokenizer=None):
        export = self.site_export()
        if change_identity:
            export['identity']['weights_manifest_sha256'] = 'f' * 64
        native = {key: export['artifacts'][key] for key in ('model_root', 'weights_manifest', 'runtime_metadata')}
        bindings = {'native_cpu_bindings': {'path': str(self.root / 'native.json'), 'sha256': 'a' * 64},
                    'native_sources': {arm: {'head': head, 'root': str(self.root / arm)}
                                       for arm, head in E.NATIVE_HEADS.items()}, 'toolbox_root': str(self.toolbox_root)}
        binding_path = self.root / 'site-bindings.json'
        binding_path.write_bytes(Q.canonical(bindings))
        inputs = (native, export, {'native197-off': {}}, self.toolbox, {})
        modules = {'transformers': SimpleNamespace(TokenizersBackend=object), 'tokenizers': object()}
        evidence = {name: {'version': 'FIXTURE'} for name in modules}
        with ExitStack() as stack:
            stack.enter_context(patch.object(P, 'isolate'))
            validator = stack.enter_context(patch.object(E, 'validate_bindings', return_value=inputs))
            stack.enter_context(patch.object(P, 'packages', return_value=(modules, evidence)))
            stack.enter_context(patch.object(P, 'recheck_packages'))
            stack.enter_context(patch.object(E.N, 'native_tokenizer', return_value=tokenizer or FixtureTokenizer()))
            for name in ('construct_corpus', 'project_generator', 'python_sources', 'python_identity'):
                stack.enter_context(patch.object(E, name, side_effect=AssertionError('site attempted regeneration: ' + name)))
            result = P.bind_site(path, sha, binding_path, self.root / 'site-out')
            self.assertEqual(validator.call_count, 2)
        return result

    def test_bind_site_only_compares_frozen_vectors_and_keeps_prep_unchanged(self):
        path, sha, prep = self.portable_file()
        before = path.read_bytes()
        result = self.bind_fixture(path, sha)
        self.assertEqual(path.read_bytes(), before)
        corpus = json.loads(Path(result['corpus']['path']).read_text())
        E.L.validate_corpus(corpus)
        self.assertEqual({k: v for k, v in corpus.items() if k != 'artifacts'}, prep['content'])
        receipt = json.loads(Path(result['site_link_receipt']['path']).read_text())
        self.assertEqual(receipt['preparation_sha256'], sha)
        self.assertEqual(receipt['content_sha256'], prep['content_sha256'])
        self.assertEqual(receipt['final_corpus'], result['corpus'])
        self.assertFalse(receipt['comparison']['regenerated_documents'])

    def test_bind_rejects_identity_or_complete_id_drift_before_output(self):
        path, sha, _ = self.portable_file()
        with self.assertRaisesRegex(RuntimeError, 'identity differs'):
            self.bind_fixture(path, sha, change_identity=True)
        class Drift(FixtureTokenizer):
            def encode(self, text, **kw):
                result = super().encode(text, **kw)
                result[-1] += 1
                return result
        with self.assertRaisesRegex(RuntimeError, 'calibration IDs differ'):
            self.bind_fixture(path, sha, tokenizer=Drift())
        self.assertFalse((self.root / 'site-out').exists())

    def test_bind_calls_full_site_validator_and_propagates_missing_weights(self):
        path, sha, _ = self.portable_file()
        bindings_path = self.root / 'full-site.json'
        bindings_path.write_bytes(b'{}')
        with patch.object(P, 'isolate'), \
             patch.object(E, 'validate_bindings', side_effect=RuntimeError('retained manifest file absent in snapshot')) as full:
            with self.assertRaisesRegex(RuntimeError, 'manifest file absent'):
                P.bind_site(path, sha, bindings_path, self.root / 'blocked')
            full.assert_called_once_with({})
        self.assertFalse((self.root / 'blocked').exists())

    def test_default_full_field_corpus_shape_unchanged(self):
        full, rows = E.construct_corpus(FixtureTokenizer(), self.site_export(), self.toolbox)
        portable = self.content()
        self.assertEqual(full.pop('artifacts'), self.site_export()['artifacts'])
        self.assertEqual(full, portable)
        self.assertEqual(len(rows), 64)

    def isolated_scripts(self):
        scripts = self.root / 'isolated-scripts'
        scripts.mkdir()
        for name in ('q03_legacy_portable_corpus.py', 'q03_legacy_corpus_export.py',
                     'q03_native_corpus_export.py', 'q03_legacy_case_replay.py',
                     'q03_qualification.py', 'q03_owner_model.py', 'q03_held_window.py',
                     'q03_http_toolbox.json'):
            shutil.copy2(ROOT / 'scripts' / name, scripts / name)
        return scripts

    def test_initial_exporter_timestamp_poisoned_pyc_is_not_executed(self):
        scripts = self.isolated_scripts()
        source = scripts / 'q03_legacy_corpus_export.py'
        marker = self.root / 'POISON_EXECUTED'
        payload = (f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n"
                   "raise RuntimeError('TIMESTAMP_POISONED_INITIAL_EXPORTER_EXECUTED')\n").encode()
        poison = self.root / 'poison.py'
        poison.write_bytes(payload + b'#' + b' ' * (source.stat().st_size - len(payload) - 1))
        os.utime(poison, ns=(source.stat().st_atime_ns, source.stat().st_mtime_ns))
        cache = Path(importlib.util.cache_from_source(str(source)))
        cache.parent.mkdir()
        py_compile.compile(str(poison), cfile=str(cache), doraise=True,
                           invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
        result = subprocess.run([sys.executable, '-B', str(scripts / 'q03_legacy_portable_corpus.py'),
                                 'prepare-tokenizer-only', '--bindings', str(self.root / 'absent-bindings.json'),
                                 '--output-directory', str(self.root / 'never-created')],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('absent-bindings.json', result.stderr)
        self.assertNotIn('TIMESTAMP_POISONED_INITIAL_EXPORTER_EXECUTED', result.stderr)
        self.assertFalse(marker.exists())
        self.assertFalse((self.root / 'never-created').exists())

    def test_native_extension_shadows_cannot_replace_task_helper_sources(self):
        scripts = self.isolated_scripts()
        for name in P.HELPER_NAMES:
            (scripts / (name + importlib.machinery.EXTENSION_SUFFIXES[0])).write_bytes(b'INVALID_EXTENSION_FIXTURE')
        result = subprocess.run([sys.executable, '-B', str(scripts / 'q03_legacy_portable_corpus.py'),
                                 'prepare-tokenizer-only', '--bindings', str(self.root / 'absent-bindings.json'),
                                 '--output-directory', str(self.root / 'never-created')],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('absent-bindings.json', result.stderr)
        self.assertNotIn('ImportError', result.stderr)
        self.assertFalse((self.root / 'never-created').exists())

    def test_package_shadows_cannot_replace_task_helper_sources(self):
        scripts = self.isolated_scripts()
        marker = self.root / 'PACKAGE_SHADOW_EXECUTED'
        for name in P.HELPER_NAMES:
            package = scripts / name
            package.mkdir()
            (package / '__init__.py').write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n"
                "raise RuntimeError('PACKAGE_SHADOW_EXECUTED')\n")
        result = subprocess.run([sys.executable, '-B', str(scripts / 'q03_legacy_portable_corpus.py'),
                                 'prepare-tokenizer-only', '--bindings', str(self.root / 'absent-bindings.json'),
                                 '--output-directory', str(self.root / 'never-created')],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('absent-bindings.json', result.stderr)
        self.assertNotIn('PACKAGE_SHADOW_EXECUTED', result.stderr)
        self.assertFalse(marker.exists())

    def test_preimported_initial_exporter_is_rejected(self):
        code = """import sys
sys.path.insert(0, sys.argv[1])
import q03_legacy_corpus_export
import q03_legacy_portable_corpus as P
try:
    P.isolate()
except RuntimeError as error:
    assert 'fresh interpreter' in str(error)
else:
    raise AssertionError('preimported initial exporter accepted')
"""
        result = subprocess.run([sys.executable, '-B', '-c', code, str(ROOT / 'scripts')],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_prefix_absence_checked_before_initial_exporter_import(self):
        code = """import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
original = Path.exists
with patch.object(Path, 'exists', lambda p: True if str(p) == '/__q03_cpu_export_unused_bytecode__' else original(p)):
    try:
        import q03_legacy_portable_corpus
    except RuntimeError as error:
        assert 'prefix already exists' in str(error)
    else:
        raise AssertionError('existing bytecode prefix accepted')
assert 'q03_legacy_corpus_export' not in sys.modules
"""
        result = subprocess.run([sys.executable, '-B', '-c', code, str(ROOT / 'scripts')],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_portable_library_import_restores_caller_bytecode_state(self):
        code = """import sys
sys.path.insert(0, sys.argv[1])
before = sys.dont_write_bytecode, sys.pycache_prefix
finders = list(sys.meta_path)
import q03_legacy_portable_corpus
assert (sys.dont_write_bytecode, sys.pycache_prefix) == before
assert sys.meta_path == finders
"""
        result = subprocess.run([sys.executable, '-B', '-c', code, str(ROOT / 'scripts')],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_fresh_cli_isolation_without_tokenizer_or_model_imports(self):
        code = "import sys; sys.path.insert(0, sys.argv[1]); import q03_legacy_portable_corpus as P; P.isolate(); assert len(P.support_sources()) == 8"
        result = subprocess.run([sys.executable, '-B', '-c', code, str(ROOT / 'scripts')],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
