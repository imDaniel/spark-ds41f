"""Synthetic CPU fixtures only; never registered tokenizer or field evidence."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import q03_legacy_corpus_export as E


class FixtureTokenizer:
    """Character IDs with explicit special token; CLI cannot select this double."""
    def encode(self, text, *, add_special_tokens, truncation):
        assert truncation is False
        return ([999999] if add_special_tokens else []) + [ord(char) for char in text]

    def apply_chat_template(self, messages, *, tokenize, add_generation_prompt,
                            continue_final_message, truncation=False):
        # The signature rejects thinking/enable_thinking/reasoning_effort overrides.
        assert add_generation_prompt is True and continue_final_message is False
        assert len(messages) == 1 and messages[0]['role'] == 'user'
        text = '<B><U>' + messages[0]['content'] + '<A><think>'
        return self.encode(text, add_special_tokens=False, truncation=truncation) if tokenize else text


class LegacyExporterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.toolbox = {name: (ROOT / name).read_bytes() for name in ('servebench/load.py', 'servebench/prompts.py')}
        # Keep mocked source inputs separate from outputs even when TMPDIR is in ROOT.
        self.toolbox_root = self.root / 'toolbox-source-fixture'
        for name, raw in self.toolbox.items():
            source = self.toolbox_root / name
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(raw)

    def export(self):
        identity = {key: 'a' * 64 for key in E.Q.IDENTITY_FIELDS}
        identity.update(model_repository=E.N.MODEL_REPOSITORY, model_revision=E.N.MODEL_REVISION,
                        tokenizer_revision=E.N.MODEL_REVISION,
                        tokenizer_assets_sha256={'tokenizer.json': 'b' * 64, 'tokenizer_config.json': 'c' * 64},
                        encoding_source_sha256=E.Q.NATIVE_SOURCES['deepseek_v41_encoding.py'],
                        chat_template_sha256=E.Q.TEMPLATE_SHA, special_tokens_sha256='c' * 64,
                        transformers_version='FIXTURE', tokenizers_version='FIXTURE')
        return {'identity': identity, 'artifacts': {
            'model_root': str(self.root / 'model'),
            'weights_manifest': {'path': str(self.root / 'weights.json'), 'sha256': 'a' * 64},
            'runtime_metadata': {'path': str(self.root / 'runtime.json'), 'sha256': 'd' * 64},
            'native_sources': {name: {'path': str(self.root / 'native' / name), 'sha256': sha}
                               for name, sha in E.Q.NATIVE_SOURCES.items()},
            'special_tokens_file': 'tokenizer_config.json'}}

    def test_exact_generator_and_all_full_template_vectors(self):
        tokenizer = FixtureTokenizer()
        corpus, renderings = E.construct_corpus(tokenizer, self.export(), self.toolbox)
        self.assertEqual(len(corpus['documents']), 4)
        self.assertEqual(len(corpus['prompts']), 16)
        self.assertEqual(len(renderings), 64)
        self.assertEqual(corpus['historical_document_identity'], 'UNAVAILABLE_NEW_SHARED_CORPUS')
        prompts = E.project_generator(self.toolbox)
        for doc in corpus['documents']:
            first, second = doc['calibration']
            seed = 2048000 + doc['slot']
            self.assertEqual(first['text'], prompts.source_text(4096, seed))
            self.assertEqual(second['text'], prompts.source_text(
                4096, seed, chars_per_token=len(first['text']) / first['response']['count']))
            self.assertEqual(first['response']['tokens'][0], 999999)
            self.assertEqual(doc['raw_ids'][0], 999999)
            for question, row in zip(corpus['prompts'], doc['chat_inputs']):
                rendered, full = E.chat_ids(tokenizer, doc['text'] + '\n\n' + question['text'])
                self.assertEqual(row['prompt_ids'], full)
                self.assertNotEqual(len(full), 4096)  # No forced length or truncation.
                self.assertTrue(rendered.endswith('<think>'))
        E.verify_corpus(tokenizer, corpus, renderings)
        second, other = E.construct_corpus(tokenizer, self.export(), self.toolbox)
        self.assertEqual(corpus, second)
        self.assertEqual(renderings, other)

    def test_whole_text_encoding_handles_cross_boundary_merges(self):
        class PairTokenizer(FixtureTokenizer):
            def encode(self, text, *, add_special_tokens, truncation):
                assert truncation is False
                # Length/IDs are deliberately non-additive across text boundaries.
                value = [ord(text[index]) * 65536 + (ord(text[index + 1]) if index + 1 < len(text) else 0)
                         for index in range(0, len(text), 2)]
                return ([999999] if add_special_tokens else []) + value
        tokenizer = PairTokenizer()
        corpus, rows = E.construct_corpus(tokenizer, self.export(), self.toolbox)
        E.verify_corpus(tokenizer, corpus, rows)
        self.assertTrue(all(doc['raw_ids'] != doc['chat_inputs'][0]['prompt_ids']
                            for doc in corpus['documents']))

    def test_invalid_actual_ids_and_direct_render_drift_fail_closed(self):
        for value in ([], [False], [-1], ['1'], None):
            tokenizer = SimpleNamespace(encode=lambda *a, **kw: value)
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, 'integer IDs'):
                E.raw_ids(tokenizer, 'actual text')
        class Drift(FixtureTokenizer):
            def apply_chat_template(self, messages, **kwargs):
                value = super().apply_chat_template(messages, **kwargs)
                return value + [7] if kwargs['tokenize'] else value
        with self.assertRaisesRegex(RuntimeError, 'full template IDs differ'):
            E.chat_ids(Drift(), 'actual text')

    def test_final_reverification_rejects_token_changes(self):
        tokenizer = FixtureTokenizer()
        corpus, rows = E.construct_corpus(tokenizer, self.export(), self.toolbox)
        corpus['documents'][0]['calibration'][0]['response']['tokens'][0] += 1
        with self.assertRaisesRegex(RuntimeError, 'raw calibration'):
            E.verify_corpus(tokenizer, corpus, rows)
        corpus, rows = E.construct_corpus(tokenizer, self.export(), self.toolbox)
        corpus['documents'][0]['chat_inputs'][0]['prompt_ids'][0] += 1
        with self.assertRaisesRegex(RuntimeError, 'full template'):
            E.verify_corpus(tokenizer, corpus, rows)

    def test_generator_source_drift_and_stdlib_mutation_rejected(self):
        for name in ('servebench/prompts.py', 'servebench/load.py'):
            broken = dict(self.toolbox)
            broken[name] += b'\n# drift\n'
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'source drift'):
                E.construct_corpus(FixtureTokenizer(), self.export(), broken)
        with patch.object(E, 'python_sources', side_effect=[
                [{'name': 'a.py', 'size': 5000, 'sha256': 'a' * 64}],
                [{'name': 'a.py', 'size': 5000, 'sha256': 'b' * 64}]]):
            with self.assertRaisesRegex(RuntimeError, 'environment changed'):
                E.construct_corpus(FixtureTokenizer(), self.export(), self.toolbox)

    def test_two_fixed_head_source_pins_and_parity(self):
        name, raw = 'vllm/tokenizers/test.py', b'# synthetic source\n'
        bindings = {}
        for arm, head in E.NATIVE_HEADS.items():
            root = self.root / arm
            path = root / name
            path.parent.mkdir(parents=True)
            path.write_bytes(raw)
            bindings[arm] = {'head': head, 'root': str(root)}
        with patch.object(E, 'SOURCE_FILES', {name: E.Q.digest(raw)}):
            result = E.read_native_sources(bindings)
            self.assertEqual(result['r6x188'], result['native197-off'])
            bad = copy.deepcopy(bindings)
            bad['r6x188']['head'] = 'f' * 40
            with self.assertRaisesRegex(RuntimeError, 'head differs'):
                E.read_native_sources(bad)
            (Path(bindings['r6x188']['root']) / name).write_bytes(raw + b'# changed')
            with self.assertRaisesRegex(RuntimeError, 'source drift'):
                E.read_native_sources(bindings)
        with self.assertRaisesRegex(RuntimeError, 'both fixed'):
            E.read_native_sources({'native197-off': bindings['native197-off']})

    def test_wrapper_bindings_reject_wrong_source_root_or_schema(self):
        native = {'native_root': str(self.root / 'native197')}
        files = {name: b'synthetic loader source' for name in E.N.SOURCE_FILES}
        bindings = {'schema_version': 1, 'native_cpu_bindings': {'path': '/unused', 'sha256': 'a' * 64},
                    'native_sources': {arm: {'head': head, 'root': str(self.root / arm)}
                                       for arm, head in E.NATIVE_HEADS.items()}, 'toolbox_root': str(self.toolbox_root)}
        bindings['native_sources']['native197-off']['root'] = native['native_root']
        with patch.object(E.Q.owner, 'pinned_json', return_value=native), \
             patch.object(E.N, 'validate_bindings', return_value=(self.export(), files, {})), \
             patch.object(E, 'read_native_sources', return_value={arm: files for arm in E.NATIVE_HEADS}), \
             patch.object(E, 'read_toolbox', return_value=self.toolbox):
            self.assertEqual(E.validate_bindings(bindings)[0], native)
            wrong = copy.deepcopy(bindings)
            wrong['native_sources']['native197-off']['root'] = str(self.root / 'another')
            with self.assertRaisesRegex(RuntimeError, 'loader source root'):
                E.validate_bindings(wrong)
            for mutate in (lambda b: b.update(schema_version=True), lambda b: b.update(endpoint='http://not-allowed'),
                           lambda b: b.pop('native_cpu_bindings')):
                wrong = copy.deepcopy(bindings)
                mutate(wrong)
                with self.assertRaisesRegex(RuntimeError, 'complete legacy CPU'):
                    E.validate_bindings(wrong)

    def test_full_original_toolbox_inventory_is_required(self):
        inventory = {'files': {'servebench/load.py': E.Q.digest(b'load'),
                               'servebench/another.py': E.Q.digest(b'original')}}
        root = self.root / 'toolbox'
        (root / 'servebench').mkdir(parents=True)
        (root / 'servebench/load.py').write_bytes(b'load')
        (root / 'servebench/another.py').write_bytes(b'original')
        with patch.object(E.L, 'toolbox_inventory', return_value=inventory):
            self.assertEqual(set(E.read_toolbox(str(root))), set(inventory['files']))
            (root / 'servebench/another.py').write_bytes(b'changed non-generator file')
            with self.assertRaisesRegex(RuntimeError, 'toolbox source changed'):
                E.read_toolbox(str(root))

    def test_original_corpus_questions_artifacts_and_calibration_cannot_be_changed(self):
        corpus, _ = E.construct_corpus(FixtureTokenizer(), self.export(), self.toolbox)
        changes = [lambda c: c['prompts'][0].update(text='different'),
                   lambda c: c['generator'].update(seed=2049),
                   lambda c: c['generator'].update(context_tokens=2048),
                   lambda c: c['documents'].pop(),
                   lambda c: c['documents'][0]['chat_inputs'].pop(),
                   lambda c: c['documents'][0]['calibration'].pop(),
                   lambda c: c.update(historical_document_identity='EXACT_HISTORICAL_REPLAY'),
                   lambda c: c['artifacts']['weights_manifest'].update(sha256='f' * 64)]
        for change in changes:
            value = copy.deepcopy(corpus)
            change(value)
            with self.assertRaises(RuntimeError):
                E.L.validate_corpus(value)

    def test_run_writes_canonical_corpus_evidence_and_pins_in_fixture_only(self):
        export = self.export()
        native = {'model_root': export['artifacts']['model_root'],
                  'weights_manifest': export['artifacts']['weights_manifest'],
                  'runtime_metadata': export['artifacts']['runtime_metadata']}
        bindings = {'schema_version': 1, 'native_cpu_bindings': {'path': str(self.root / 'native.json'), 'sha256': 'a' * 64},
                    'native_sources': {arm: {'head': head, 'root': str(self.root / arm)}
                                       for arm, head in E.NATIVE_HEADS.items()}, 'toolbox_root': str(self.toolbox_root)}
        path = self.root / 'bindings.json'
        path.write_bytes(E.Q.canonical(bindings))
        inputs = (native, export, {'native197-off': {}}, self.toolbox, {'mock': 'a' * 64})
        module = SimpleNamespace(TokenizersBackend=object)
        with patch.object(E, 'isolate'), patch.object(E, 'validate_bindings', return_value=inputs), \
             patch.object(E.N, 'native_tokenizer', return_value=FixtureTokenizer()), \
             patch.object(E.importlib, 'import_module', return_value=module), \
             patch.object(E.N, 'package_evidence', return_value={'version': 'FIXTURE', 'files': {}}):
            result = E.run(path, self.root / 'out')
            changed = copy.deepcopy(inputs)
            changed[1]['identity']['model_config_sha256'] = 'f' * 64
            with patch.object(E, 'validate_bindings', side_effect=[inputs, changed]):
                with self.assertRaisesRegex(RuntimeError, 'inputs changed during export'):
                    E.run(path, self.root / 'drift-output')
            self.assertFalse((self.root / 'drift-output').exists())
            with self.assertRaisesRegex(RuntimeError, 'overlaps immutable'):
                E.run(path, Path(native['model_root']) / 'new-output')
        for name in ('corpus', 'cpu_evidence', 'bindings'):
            pin = result[name]
            self.assertEqual(E.Q.digest(Path(pin['path']).read_bytes()), pin['sha256'])
        corpus = json.loads(Path(result['corpus']['path']).read_text())
        self.assertEqual(E.Q.canonical(corpus), Path(result['corpus']['path']).read_bytes())
        evidence = json.loads(Path(result['cpu_evidence']['path']).read_text())
        self.assertEqual(evidence['scope'], 'CPU_PREPARATION_ONLY_NOT_FIELD_QUALIFICATION')
        self.assertEqual(evidence['raw_tokenization'], {'add_special_tokens': True, 'truncation': False})
        self.assertEqual(evidence['chat_tokenization']['thinking_override'], 'ABSENT')
        self.assertEqual(len(evidence['rendered_rows']), 64)
        self.assertEqual(set(evidence['native_sources']), set(E.NATIVE_HEADS))
        self.assertFalse(evidence['weights']['weight_bytes_rehashed'])
        self.assertEqual(os.stat(self.root / 'out').st_mode & 0o777, 0o700)
        for file in (self.root / 'out').iterdir():
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)

    def test_preimported_helpers_rejected_in_fresh_child(self):
        code = """import sys
sys.path.insert(0, sys.argv[1])
import q03_owner_model
import q03_legacy_corpus_export as E
try:
    E.isolate()
except RuntimeError as error:
    assert 'fresh interpreter' in str(error)
else:
    raise AssertionError('preloaded source helper accepted')
"""
        result = subprocess.run([sys.executable, '-B', '-c', code, str(ROOT / 'scripts')],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_real_isolation_and_audit_guards_in_fresh_child(self):
        code = """import sys, importlib.util
sys.path.insert(0, sys.argv[1])
import q03_legacy_corpus_export as E
E.isolate()
assert len(E.preparation_sources()) == 7
for name in E.N.FORBIDDEN_IMPORTS:
    assert name in sys.modules and sys.modules[name] is None
    assert importlib.util.find_spec(name) is None
for event, args in [('socket.connect', (None, ('127.0.0.1', 1))),
                    ('subprocess.Popen', ('/bin/true', [], None, None)),
                    ('open', ('/dev/nvidia0', 'rb', 0))]:
    try:
        sys.audit(event, *args)
    except RuntimeError:
        pass
    else:
        raise AssertionError('isolation audit allowed: ' + event)
"""
        result = subprocess.run([sys.executable, '-B', '-c', code, str(ROOT / 'scripts')],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_library_import_preserves_caller_settings(self):
        code = """import sys
sys.path.insert(0, sys.argv[1])
before = sys.dont_write_bytecode, sys.pycache_prefix
import q03_legacy_corpus_export
assert (sys.dont_write_bytecode, sys.pycache_prefix) == before
"""
        result = subprocess.run([sys.executable, '-B', '-c', code, str(ROOT / 'scripts')],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
