"""Binding-only tests adapted from reviewed 39d8f9d; synthetic evidence only."""
import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_q03_legacy_portable_corpus as F
import test_q03_legacy_corpus_export as W

P, E, Q = F.P, F.E, F.Q


class RevisedSourcesTest(unittest.TestCase):
    setUp = F.PortableCorpusTest.setUp


    cloud_bindings = F.PortableCorpusTest.cloud_bindings


    def revised_sources(self):
        bindings, _ = self.cloud_bindings()
        bindings['native_sources'] = {arm: {'head': head, 'root': str(self.root / arm)}
                                     for arm, head in E.REVISED_NATIVE_HEADS.items()}
        sources = {arm: {name: b'# SYNTHETIC SOURCE ONLY\n' + name.encode() + b'\n'
                        for name in E.SOURCE_FILES} for arm in E.REVISED_NATIVE_HEADS}
        sources['r6x-steer'][E.CHAT_PROTOCOL] += b'# SYNTHETIC steering protocol difference\n'
        hashes = {arm: {name: Q.digest(raw) for name, raw in files.items()} for arm, files in sources.items()}
        for arm, files in sources.items():
            for name, raw in files.items():
                path = Path(bindings['native_sources'][arm]['root']) / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
        self.stack.enter_context(patch.object(E, 'REVISED_SOURCE_FILES', hashes))
        return bindings, sources, hashes


    def test_historical_callers_do_not_gain_schema2_admission(self):
        direct, _, _, _, _ = self.direct_bindings()
        with patch.object(E.N, 'asset_bindings', side_effect=AssertionError('asset backend reached')), \
             self.assertRaisesRegex(RuntimeError, 'quality site-binding route'):
            E.validate_bindings(direct)

    def test_exact_revised_pair_preserves_both_full_protocols(self):
        bindings, sources, _ = self.revised_sources()
        result = E.read_native_sources(bindings['native_sources'], allow_revised=True)
        self.assertEqual(result, sources)
        self.assertNotEqual(result['r6x-unpatched'][E.CHAT_PROTOCOL], result['r6x-steer'][E.CHAT_PROTOCOL])
        for name in E.SOURCE_FILES.keys() - {E.CHAT_PROTOCOL}:
            self.assertEqual(result['r6x-unpatched'][name], result['r6x-steer'][name])


    def test_mixed_missing_extra_and_relabelled_heads_fail_closed(self):
        bindings, _, _ = self.revised_sources()
        for alter in (lambda b: b.pop('r6x-unpatched'),
                      lambda b: b.update(r6x188=b.pop('r6x-unpatched')),
                      lambda b: b.update(extra=b['r6x-steer']),
                      lambda b: b['r6x-unpatched'].update(head=E.REVISED_NATIVE_HEADS['r6x-steer']),
                      lambda b: b['r6x-unpatched'].update(image='pretend-runtime')):
            bad = copy.deepcopy(bindings['native_sources'])
            alter(bad)
            with self.assertRaises(RuntimeError):
                E.read_native_sources(bad, allow_revised=True)


    def test_protocol_substitution_and_whole_file_tail_drift_fail_closed(self):
        bindings, sources, _ = self.revised_sources()
        root = Path(bindings['native_sources']['r6x-unpatched']['root'])
        for name, raw in sources['r6x-unpatched'].items():
            path = root / name
            path.write_bytes(raw + b'# changed trailing bytes\n')
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'source drift'):
                E.read_native_sources(bindings['native_sources'], allow_revised=True)
            path.write_bytes(raw)
        (root / E.CHAT_PROTOCOL).write_bytes(sources['r6x-steer'][E.CHAT_PROTOCOL])
        with self.assertRaisesRegex(RuntimeError, 'source drift'):
            E.read_native_sources(bindings['native_sources'], allow_revised=True)


    def direct_bindings(self):
        bindings, sources, hashes = self.revised_sources()
        metadata = {}
        for arm in E.REVISED_NATIVE_HEADS:
            path = self.root / (arm + '-metadata.json')
            path.write_bytes(Q.canonical({'arm': arm, 'packages': {
                name: {'version': 'FIXTURE'} for name in ('transformers', 'tokenizers')},
                'tokenizer_source_sha256': {name.removeprefix('vllm/'): sha for name, sha in hashes[arm].items()}}))
            metadata[arm] = {'path': str(path), 'sha256': Q.digest(path.read_bytes())}
        export = W.LegacyExporterTest.export(self)
        export['artifacts']['runtime_metadata'] = metadata['r6x-unpatched']
        native = {'native_root': bindings['native_sources']['r6x-unpatched']['root'],
                  **{k: export['artifacts'][k] for k in ('model_root', 'weights_manifest', 'runtime_metadata')}}
        path = self.root / 'native.json'
        path.write_bytes(Q.canonical(native))
        direct = {'schema_version': 2, 'native_cpu_bindings': {'path': str(path), 'sha256': Q.digest(path.read_bytes())},
                  'native_sources': bindings['native_sources'], 'toolbox_root': bindings['toolbox_root'],
                  'tokenizer_metadata': metadata}
        self.stack.enter_context(patch.object(E, 'metadata_validator', return_value=SimpleNamespace(
            validate_metadata=lambda value, arm: value)))  # Strict sidecar validator has its own tests.
        self.stack.enter_context(patch.object(E, 'read_toolbox', return_value=self.toolbox))
        self.stack.enter_context(patch.object(E.N, 'asset_bindings', return_value=(
            export, {key: sources['r6x-unpatched'][key] for key in E.N.SOURCE_FILES}, {'synthetic': 'a' * 64})))
        return direct, native, export, sources, hashes


    def test_schema2_rejects_missing_captures_mixed_sources_and_old_runtime_pin(self):
        direct, native, _, _, _ = self.direct_bindings()
        E.validate_bindings(direct, allow_revised=True)
        changes = [lambda d: d.pop('tokenizer_metadata'),
                   lambda d: d['tokenizer_metadata'].pop('r6x-steer'),
                   lambda d: d['native_sources'].update(r6x188=d['native_sources'].pop('r6x-unpatched')),
                   lambda d: d.update(schema_version=True)]
        for change in changes:
            bad = copy.deepcopy(direct); change(bad)
            with self.assertRaises(RuntimeError):
                E.validate_bindings(bad, allow_revised=True)
        bad = copy.deepcopy(direct)
        native['runtime_metadata'] = {'path': '/old-native197/runtime.json', 'sha256': 'a' * 64}
        native_path = Path(bad['native_cpu_bindings']['path']); native_path.write_bytes(Q.canonical(native))
        bad['native_cpu_bindings']['sha256'] = Q.digest(native_path.read_bytes())
        with self.assertRaisesRegex(RuntimeError, 'actual U176 sidecar pin'):
            E.validate_bindings(bad, allow_revised=True)
        path = Path(direct['native_cpu_bindings']['path'])
        native['runtime_metadata'] = direct['tokenizer_metadata']['r6x-unpatched']
        native['native_root'] = direct['native_sources']['r6x-steer']['root']
        path.write_bytes(Q.canonical(native)); direct['native_cpu_bindings']['sha256'] = Q.digest(path.read_bytes())
        with self.assertRaisesRegex(RuntimeError, 'loader source root'):
            E.validate_bindings(direct, allow_revised=True)


    def test_captured_image_tokenizer_sources_must_match_retained_local_closure(self):
        direct, _, _, _, _ = self.direct_bindings()
        pin = direct['tokenizer_metadata']['r6x-unpatched']; path = Path(pin['path'])
        value = Q.json_bytes(path.read_bytes())
        value['tokenizer_source_sha256']['tokenizers/hf.py'] = 'a' * 64
        path.write_bytes(Q.canonical(value)); pin['sha256'] = Q.digest(path.read_bytes())
        with self.assertRaisesRegex(RuntimeError, 'image/local tokenizer source closure differs'):
            E.validate_bindings(direct, allow_revised=True)


    def test_captured_pair_package_version_mismatch_rejects_before_backend(self):
        direct, _, _, _, _ = self.direct_bindings()
        pin = direct['tokenizer_metadata']['r6x-steer']; path = Path(pin['path'])
        value = Q.json_bytes(path.read_bytes()); value['packages']['tokenizers']['version'] = 'DIFFERENT'
        path.write_bytes(Q.canonical(value)); pin['sha256'] = Q.digest(path.read_bytes())
        with self.assertRaisesRegex(RuntimeError, 'package versions differ'):
            E.validate_bindings(direct, allow_revised=True)


class SidecarIntegrationTest(unittest.TestCase):
    setUp = W.LegacyExporterTest.setUp


    def test_strict_captured_metadata_contract_supplies_versions_without_old_runtime(self):
        import test_q03_tokenizer_metadata_capture as M
        pins = {}
        for arm in E.REVISED_NATIVE_HEADS:
            path = self.root / (arm + '.json')
            path.write_bytes(Q.canonical(M.sidecar(arm)))
            pins[arm] = {'path': str(path), 'sha256': Q.digest(path.read_bytes())}
        with patch.object(E, 'metadata_validator', return_value=M.C), \
             patch.dict(M.R.IMAGE_FREEZE['image_rootfs_sha256'],
                        {arm: M.C.digest(M.C.canonical(M.image(arm)['RootFS'])) for arm in E.REVISED_NATIVE_HEADS}):
            self.assertEqual(E.revised_metadata({'tokenizer_metadata': pins}),
                             {'transformers_version': '1.0', 'tokenizers_version': '1.0'})
            for arm in E.REVISED_NATIVE_HEADS:
                path = Path(pins[arm]['path']); original = path.read_bytes()
                value = Q.json_bytes(original); value['image'] = 'sha256:' + 'f' * 64
                path.write_bytes(Q.canonical(value)); pins[arm]['sha256'] = Q.digest(path.read_bytes())
                with self.assertRaisesRegex(ValueError, 'arm/image/source'):
                    E.revised_metadata({'tokenizer_metadata': pins})
                path.write_bytes(original); pins[arm]['sha256'] = Q.digest(original)


    def test_versioned_empty_u_and_v1_s_bind_without_relabelling_runtime(self):
        import test_q03_tokenizer_metadata_capture as M
        pins, captures = {}, {}
        for arm in E.REVISED_NATIVE_HEADS:
            value = M.sidecar(arm)
            if arm == 'r6x-unpatched':
                runtime = value['runtime_manifest']
                runtime.update(schema_version=2, absent_source_paths=[],
                               empty_source_directories=['models/deepseek_v4_1/steering'])
                value['runtime_manifest_sha256'] = M.C.digest(M.C.canonical(runtime))
                value['capture']['raw_stdout_sha256'] = M.C.digest(M.C.canonical_stdout(runtime, value['packages']))
            captures[arm] = value
            path = self.root / (arm + '.json'); path.write_bytes(Q.canonical(value))
            pins[arm] = {'path': str(path), 'sha256': Q.digest(path.read_bytes())}
        with patch.object(E, 'metadata_validator', return_value=M.C), \
             patch.dict(M.R.IMAGE_FREEZE['image_rootfs_sha256'],
                        {arm: M.C.digest(M.C.canonical(M.image(arm)['RootFS'])) for arm in E.REVISED_NATIVE_HEADS}):
            self.assertEqual(E.revised_metadata({'tokenizer_metadata': pins}),
                             {'transformers_version': '1.0', 'tokenizers_version': '1.0'})
            for arm, value in captures.items():
                self.assertEqual(M.C.validate_metadata(value, arm)['runtime_manifest'], value['runtime_manifest'])
                self.assertEqual(Path(pins[arm]['path']).read_bytes(), Q.canonical(value))
            self.assertEqual(captures['r6x-unpatched']['runtime_manifest']['schema_version'], 2)
            self.assertEqual(captures['r6x-steer']['runtime_manifest']['schema_version'], 1)


    def test_revised_metadata_helpers_load_only_from_source_in_fresh_cpu_process(self):
        code = """import sys
sys.path.insert(0, sys.argv[1])
import q03_legacy_corpus_export as E
E.isolate()
sources = E.preparation_sources(revised=True)
assert len(sources) == 9
assert E.metadata_validator().__spec__.cached.startswith(E.BYTECODE_PREFIX + '/')
assert all(sys.modules[name] is None for name in E.N.FORBIDDEN_IMPORTS)
assert 'transformers' not in sys.modules and 'tokenizers' not in sys.modules
"""
        result = subprocess.run([sys.executable, '-B', '-c', code, str(W.ROOT / 'scripts')],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
