"""Synthetic-only tokenizer metadata tests, never genuine image evidence.

Fresh Python subprocesses read invented package files. Every Docker call is an
inert fake; no image, model, GPU, network, download or native library is used.
"""
import ast
import copy
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('tokenizer_capture', ROOT / 'scripts/q03_tokenizer_metadata_capture.py')
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)
R = C.R
ARM = 'r6x-unpatched'
CID = 'a' * 32


def runtime(arm=ARM, *, schema_version=1):
    root = '/opt/image/vllm/vllm'
    value = dict(schema_version=1, **R.checked_identity(arm),
        installation={'mode': 'image-source', 'package_root': root},
        platform={'python': '3.12.3', 'machine': 'aarch64', 'system': 'Linux'},
        metadata_versions={n: '1.0' for n in ('torch', 'tilelang', 'tile_kernels')},
        package_origins={n: '/opt/site/' + n + '/__init__.py' for n in ('torch', 'tilelang', 'tile_kernels')},
        package_sha256={n: 'b'*64 for n in ('torch', 'tilelang', 'tile_kernels')},
        torch_version='1.0', torch_version_sha256='c'*64,
        native_paths={n: root + '/' + n.split('.')[-1] + '.abi3.so' for n in R.NATIVE_NAMES},
        native_sha256={n: 'd'*64 for n in R.NATIVE_NAMES}, source_sha256=R.source_hashes(arm),
        pythonpath='/opt/image/vllm:/opt/site', absent_source_paths=R.absent_source_paths(arm))
    if schema_version == 2:
        value.update(schema_version=2, absent_source_paths=[],
                     empty_source_directories=R.absent_source_paths(ARM))
    return value


def image(arm=ARM):
    identity = R.checked_identity(arm)
    components = dict(R.IMAGE_FREEZE['component_sources'],
                      vllm={'head': identity['source_head'], 'tree': identity['source_tree']})
    labels = {'local.spark3.' + name + '.' + suffix: value[key]
              for name, value in components.items() for suffix, key in (('patch-head', 'head'), ('tree', 'tree'))}
    labels['local.site.native-parent'] = R.IMAGE_FREEZE['native_parent_image']
    return dict(Id=identity['image'], Architecture='arm64', Os='linux',
                Config={'Labels': labels, 'Env': ['PYTHONPATH=' + runtime(arm)['pythonpath']]},
                RootFS={'Type': 'layers', 'Layers': ['sha256:' + 'e'*64]})


def packages():
    result = {}
    for name in C.PACKAGE_NAMES:
        files = {relative: {'path': '/opt/site/' + relative, 'sha256': C.digest(relative.encode())}
                 for relative in (name + '/__init__.py', name + '/nested/source.py',
                                  name + '-1.0.dist-info/METADATA', name + '-1.0.dist-info/RECORD')}
        result[name] = {'version': '1.0', 'module_path': '/opt/site/' + name + '/__init__.py',
                        'files': files, 'inventory_sha256': C.digest(C.canonical(files))}
    return result


def sidecar(arm=ARM, *, runtime_manifest=None):
    plan = C.static_capture_plan(arm, CID)
    r = runtime(arm) if runtime_manifest is None else copy.deepcopy(runtime_manifest)
    p = packages()
    provenance = {key: plan[key] for key in ('capture_id', 'runtime_capture_script_sha256',
        'capture_helper_sha256', 'collector_sha256', 'program_sha256')}
    provenance['raw_stdout_sha256'] = C.digest(C.canonical_stdout(r, p))
    return dict(schema_version=1, kind=C.KIND, **R.checked_identity(arm),
                runtime_manifest=r, runtime_manifest_sha256=C.digest(C.canonical(r)),
                image_inspect=image(arm), capture=provenance, packages=p,
                tokenizer_source_sha256=C.tokenizer_source_hashes(arm),
                status='captured-unreviewed', runtime_qualification=False, requires_independent_review=True)


class PlanSchemaTests(unittest.TestCase):
    def setUp(self):
        # Synthetic image fixture deliberately substitutes only a synthetic
        # RootFS hash. The fixed actual arm/image/source tables stay unchanged.
        patcher = mock.patch.dict(R.IMAGE_FREEZE['image_rootfs_sha256'],
                                 {a: C.digest(C.canonical(image(a)['RootFS'])) for a in R.REVISED_ARMS})
        patcher.start(); self.addCleanup(patcher.stop)

    def test_offline_plan_pins_helpers_and_preserves_cpu_sandbox(self):
        self.assertEqual(C.digest(Path(R.__file__).read_bytes()), C.RUNTIME_HELPER_SHA256)
        for arm in R.REVISED_ARMS:
            with self.subTest(arm=arm), mock.patch.object(C.subprocess, 'run', side_effect=AssertionError('must be inert')):
                plan = C.static_capture_plan(arm, CID)
                argv = plan['argv']
                self.assertEqual(plan['name'], 'q03-tokenizer-evidence-' + CID)
                self.assertEqual(argv[-5:-1], ['-I', '-S', '-B', '-c'])
                self.assertEqual(argv[argv.index('--entrypoint')+1], 'python3')
                for flag in ('--runtime=runc', '--network=none', '--read-only', '--cap-drop=ALL',
                             '--memory=1g', '--memory-swap=1g', '--cpus=1', '--pids-limit=64',
                             '--security-opt=no-new-privileges', 'NVIDIA_VISIBLE_DEVICES=void',
                             'NVIDIA_DRIVER_CAPABILITIES=', 'never'):
                    self.assertIn(flag, argv)
                self.assertFalse(any(v.startswith(('--gpus', '--device', '--mount', '--volume', '--privileged')) for v in argv))
                self.assertEqual(plan['program_sha256'], C.digest(argv[-1].encode()))
                self.assertIn(R.STATIC_CAPTURE, argv[-1])
                self.assertIn(C.TOKENIZER_CAPTURE, argv[-1])
                imports = []
                for node in ast.walk(ast.parse(argv[-1])):
                    if isinstance(node, ast.Import): imports.extend(a.name for a in node.names)
                    elif isinstance(node, ast.ImportFrom): imports.append(node.module)
                self.assertTrue(set(imports) <= {'ast', 'hashlib', 'importlib.machinery', 'importlib.metadata',
                                                'json', 'os', 'pathlib', 'platform', 'sys', 'sysconfig', 'stat', 'csv', 'io', 'email.parser'})
                self.assertEqual(C.validate_metadata(sidecar(arm), arm), sidecar(arm))
        for arm in ('native197-off', 'r6x188', True, ''):
            with self.assertRaises(ValueError): C.static_capture_plan(arm, CID)

    def test_rejected_sidecar_identity_manifest_image_status_and_helper_drift(self):
        alterations = (
            lambda v: v.update(schema_version=True), lambda v: v.update(extra=True),
            lambda v: v.update(arm='r6x-steer'), lambda v: v.update(image=R.checked_identity('r6x-steer')['image']),
            lambda v: v.update(source_head='f'*40), lambda v: v.update(source_tree='f'*40),
            lambda v: v.update(runtime_manifest_sha256='f'*64),
            lambda v: v['tokenizer_source_sha256'].update({'tokenizers/hf.py': 'f'*64}),
            lambda v: v['tokenizer_source_sha256'].pop('tokenizers/protocol.py'),
            lambda v: v['tokenizer_source_sha256'].update(C.tokenizer_source_hashes('r6x-steer')),
            lambda v: v['runtime_manifest']['source_sha256'].update({'envs.py': 'f'*64}),
            lambda v: v['runtime_manifest'].update(transformers_version='native197-assumed'),
            lambda v: v['image_inspect'].update(Architecture='amd64'),
            lambda v: v['image_inspect']['Config']['Labels'].update({'local.site.native-parent': 'sha256:'+'f'*64}),
            lambda v: v.update(status='admitted'), lambda v: v.update(runtime_qualification=True),
            lambda v: v.update(requires_independent_review=False),
        )
        for change in alterations:
            value = sidecar(); change(value)
            with self.subTest(change=change), self.assertRaises((ValueError, TypeError)):
                C.validate_metadata(value, ARM)
        for field in sidecar()['capture']:
            value = sidecar(); value['capture'][field] = 'f'*64
            with self.subTest(field=field), self.assertRaises(ValueError): C.validate_metadata(value, ARM)

    def test_rejected_package_version_origin_inventory_and_file_drift(self):
        mutations = (
            lambda p: p.pop('tokenizers'), lambda p: p.update(extra=p['tokenizers']),
            lambda p: p['tokenizers'].update(version=''), lambda p: p['tokenizers'].update(version=True),
            lambda p: p['tokenizers'].update(module_path='/other/tokenizers/__init__.py'),
            lambda p: p['tokenizers'].update(inventory_sha256='f'*64),
            lambda p: p['tokenizers']['files'].pop('tokenizers/__init__.py'),
            lambda p: p['tokenizers']['files'].update({'../escape': {'path': '/escape', 'sha256': 'f'*64}}),
            lambda p: p['tokenizers']['files'].update({'tokenizers/./alias': {'path': '/opt/site/tokenizers/alias', 'sha256': 'f'*64}}),
            lambda p: p['tokenizers']['files'].update({'tokenizers/__pycache__/x.pyc': {'path': '/opt/site/tokenizers/__pycache__/x.pyc', 'sha256': 'f'*64}}),
            lambda p: p['tokenizers']['files']['tokenizers/__init__.py'].update(sha256='bad'),
            lambda p: p['tokenizers']['files']['tokenizers/__init__.py'].update(path='/wrong/__init__.py'),
            lambda p: p['tokenizers']['files']['tokenizers/__init__.py'].update(size=1),
        )
        for mutate in mutations:
            value = packages(); mutate(value)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError): C.validate_packages(value)

    def test_pair_parser_requires_exact_two_canonical_markers_and_binding(self):
        raw = C.canonical_stdout(runtime(), packages())
        self.assertEqual(C.validate_static_capture_output(raw, ARM), (runtime(), packages()))
        for bad in (raw+raw, b'diagnostic\n'+raw, raw.replace(b'"schema_version":1', b'"schema_version":true'),
                    raw.replace(C.TOKENIZER_PREFIX.encode(), b'bad='), raw.rstrip(),
                    b'x'*(C.MAX_BYTES+1), raw.replace(b'"packages":', b'"packages":{},"packages":', 1)):
            with self.subTest(bad=bad[:40]), self.assertRaises(ValueError): C.validate_static_capture_output(bad, ARM)
        lines = raw.splitlines()
        value = C.json_bytes(lines[1][len(C.TOKENIZER_PREFIX):]); value['runtime_manifest_sha256'] = 'f'*64
        with self.assertRaisesRegex(ValueError, 'runtime/helper binding'):
            C.validate_static_capture_output(lines[0]+b'\n'+C.TOKENIZER_PREFIX.encode()+C.canonical(value)+b'\n', ARM)

    def test_helper_bytes_rechecked_after_import(self):
        with mock.patch.object(C, '_HELPER_SOURCE_SHA256', 'f'*64), self.assertRaisesRegex(ValueError, 'bytes changed'):
            C.static_capture_plan(ARM, CID)
        with mock.patch.object(C, 'RUNTIME_HELPER_SHA256', 'f'*64), self.assertRaisesRegex(ValueError, 'bytes changed'):
            C.static_capture_plan(ARM, CID)

    def test_versioned_runtime_roundtrip_preserves_old_u_s_and_empty_u_bytes(self):
        for arm, version in ((ARM, 1), ('r6x-steer', 1), (ARM, 2)):
            with self.subTest(arm=arm, version=version):
                manifest = runtime(arm, schema_version=version)
                manifest_bytes = C.canonical(manifest)
                value = sidecar(arm, runtime_manifest=manifest)
                sidecar_bytes = C.canonical(value)
                validated = C.validate_metadata(C.json_bytes(sidecar_bytes), arm)
                self.assertEqual(C.canonical(validated), sidecar_bytes)
                self.assertEqual(C.canonical(validated['runtime_manifest']), manifest_bytes)
                self.assertEqual(validated['schema_version'], 1)
                self.assertEqual(validated['runtime_manifest']['schema_version'], version)
                self.assertEqual(validated['runtime_manifest_sha256'], C.digest(manifest_bytes))
                raw = C.canonical_stdout(manifest, packages())
                parsed, actual_packages = C.validate_static_capture_output(raw, arm)
                self.assertEqual(C.canonical(parsed), manifest_bytes)
                self.assertEqual(C.canonical_stdout(parsed, actual_packages), raw)
                if version == 1:
                    self.assertNotIn('empty_source_directories', parsed)
                    self.assertEqual(parsed['absent_source_paths'], R.absent_source_paths(arm))
                else:
                    self.assertEqual(parsed['absent_source_paths'], [])
                    self.assertEqual(parsed['empty_source_directories'], R.absent_source_paths(ARM))

    def test_sidecar_and_pair_reject_v2_s_mixed_and_false_witnesses(self):
        valid = runtime(schema_version=2)
        alterations = (
            lambda v: v.update(schema_version=1),
            lambda v: v.update(schema_version=True),
            lambda v: v.update(schema_version=3),
            lambda v: v.pop('empty_source_directories'),
            lambda v: v.update(absent_source_paths=R.absent_source_paths(ARM)),
            lambda v: v.update(empty_source_directories=[]),
            lambda v: v.update(empty_source_directories=['models/deepseek_v4_1/other']),
            lambda v: v.update(empty_source_directories=R.absent_source_paths(ARM)*2),
            lambda v: v.update(empty_source_directories='models/deepseek_v4_1/steering'),
            lambda v: v.update(image=R.checked_identity('r6x-steer')['image']),
        )
        invalid = []
        for change in alterations:
            value = copy.deepcopy(valid); change(value); invalid.append((ARM, value))
        for arm in R.REVISED_ARMS:
            value = runtime(arm); value['empty_source_directories'] = []
            invalid.append((arm, value))
            value = runtime(arm, schema_version=2)
            if arm == 'r6x-steer':
                invalid.append((arm, value))
                value = copy.deepcopy(value); value['empty_source_directories'] = []
                invalid.append((arm, value))
        value = runtime(); value['absent_source_paths'] = []; invalid.append((ARM, value))
        for arm, manifest in invalid:
            with self.subTest(arm=arm, manifest=manifest):
                # Recompute every enclosing digest, so rejection must come from
                # the runtime witness contract, not a stale sidecar checksum.
                value = sidecar(arm, runtime_manifest=manifest)
                with self.assertRaises(ValueError): C.validate_metadata(value, arm)
                with self.assertRaises(ValueError):
                    C.validate_static_capture_output(C.canonical_stdout(manifest, packages()), arm)


class CaptureFilesystemTests(unittest.TestCase):
    def setUp(self):
        PlanSchemaTests.setUp(self)
        # Capture evidence stays outside the source slice even when CI sets
        # TMPDIR inside the checkout; other synthetic fixtures can use TMPDIR.
        self.temp = tempfile.TemporaryDirectory(prefix='q03-tokenizer-evidence-', dir=Path('/tmp').resolve())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve(); self.root.chmod(0o700)
        self.assertFalse(self.root.is_relative_to(ROOT))
        self.calls = []
        self.capture_runtime = runtime()

    def runner(self, argv, **kw):
        self.calls.append(argv)
        value = C.canonical([image(self.capture_runtime['arm'])]) if 'image' in argv else (
            C.canonical_stdout(self.capture_runtime, packages()) if 'run' in argv else b'')
        kw['stdout'].write(value); kw['stderr'].write(b'SYNTHETIC ONLY\n')
        self.assertEqual(kw['stdin'], subprocess.DEVNULL)
        self.assertFalse(kw['check']); self.assertEqual(kw['preexec_fn'], C.child_limits)
        return SimpleNamespace(returncode=0)

    def test_capture_writes_separate_runtime_and_metadata_without_schema_change(self):
        output = self.root/'capture'
        receipt = C.capture(ARM, CID, str(output), self.runner)
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(receipt['status'], 'captured-unreviewed')
        self.assertIs(receipt['runtime_qualification'], False)
        raw_runtime = (output/'runtime-manifest.json').read_bytes()
        self.assertEqual(raw_runtime, C.canonical(runtime()))
        self.assertEqual(R.validate_capture(C.json_bytes(raw_runtime), ARM), runtime())
        value = C.json_bytes((output/'tokenizer-metadata.json').read_bytes())
        self.assertEqual(C.validate_metadata(value, ARM), value)
        self.assertEqual(value['runtime_manifest_sha256'], C.digest(raw_runtime))
        self.assertEqual(receipt['tokenizer_metadata_sha256'], C.digest(C.canonical(value)))
        self.assertEqual((output/'capture.stdout').read_bytes(), C.canonical_stdout(runtime(), packages()))
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
        for path in output.iterdir(): self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        with self.assertRaises(FileExistsError): C.capture(ARM, CID, str(output), self.runner)
        self.assertEqual(len(self.calls), 4)

    def test_fake_capture_preserves_exact_v1_u_s_and_v2_u_runtime_bytes(self):
        for arm, version in ((ARM, 1), ('r6x-steer', 1), (ARM, 2)):
            with self.subTest(arm=arm, version=version):
                self.capture_runtime = runtime(arm, schema_version=version)
                original = C.canonical(self.capture_runtime)
                stdout = C.canonical_stdout(self.capture_runtime, packages())
                output = self.root/(arm+'-v'+str(version))
                receipt = C.capture(arm, CID, str(output), self.runner)
                self.assertEqual((output/'runtime-manifest.json').read_bytes(), original)
                self.assertEqual((output/'capture.stdout').read_bytes(), stdout)
                value = C.json_bytes((output/'tokenizer-metadata.json').read_bytes())
                self.assertEqual(value['schema_version'], 1)
                self.assertEqual(C.canonical(value['runtime_manifest']), original)
                self.assertEqual(C.validate_metadata(value, arm), value)
                self.assertEqual(receipt['runtime_manifest_sha256'], C.digest(original))
                self.assertEqual(value['runtime_manifest_sha256'], C.digest(original))
                self.assertEqual(value['capture']['raw_stdout_sha256'], C.digest(stdout))
                self.assertIs(receipt['runtime_qualification'], False)
        self.assertEqual(len(self.calls), 12)

    def test_invalid_image_fails_before_start(self):
        def runner(argv, **kw):
            self.calls.append(argv)
            bad = image(); bad['Id'] = 'sha256:'+'0'*64
            kw['stdout'].write(C.canonical([bad]))
            return SimpleNamespace(returncode=0)
        with self.assertRaises(ValueError): C.capture(ARM, CID, str(self.root/'wrong'), runner)
        self.assertEqual(len(self.calls), 1)

    def test_capture_failures_preserve_raw_and_never_publish_sidecar(self):
        for failure in ('nonzero', 'timeout', 'invalid', 'busy', 'postbusy'):
            output = self.root/failure
            def runner(argv, **kw):
                if 'run' in argv:
                    kw['stdout'].write(b'invalid' if failure=='invalid' else C.canonical_stdout(runtime(), packages()))
                    kw['stderr'].write(b'SYNTHETIC failure retained\n')
                    if failure=='timeout': raise subprocess.TimeoutExpired(argv, 120)
                    return SimpleNamespace(returncode=1 if failure=='nonzero' else 0)
                if 'container' in argv and (failure=='busy' or (failure=='postbusy' and (output/'capture.stdout').exists())):
                    kw['stdout'].write(b'occupied\n'); return SimpleNamespace(returncode=0)
                return self.runner(argv, **kw)
            with self.subTest(failure=failure), self.assertRaises((ValueError, subprocess.TimeoutExpired)):
                C.capture(ARM, CID, str(output), runner)
            self.assertFalse((output/'tokenizer-metadata.json').exists())
            self.assertFalse((output/'runtime-manifest.json').exists())
            self.assertEqual(C.json_bytes((output/'receipt.json').read_bytes())['status'], 'failed-unreviewed')

    def test_private_parent_required_before_docker_and_output_alias_rejected(self):
        self.root.chmod(0o755)
        with self.assertRaises(ValueError): C.capture(ARM, CID, str(self.root/'open'), self.runner)
        self.root.chmod(0o700)
        (self.root/'alias').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError): C.capture(ARM, CID, str(self.root/'alias'/'new'), self.runner)
        self.assertEqual(self.calls, [])


class StaticSyntheticCollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve(); self.site = self.root/'site'; self.site.mkdir()
        for name in C.PACKAGE_NAMES: self.make_package(name)
        self.vllm = self.site/'vllm'; self.vllm.mkdir()
        self.expected_sources = {}
        for rel in C.tokenizer_source_hashes(ARM):
            path = self.vllm/rel; path.parent.mkdir(parents=True, exist_ok=True)
            raw = b'# SYNTHETIC tokenizer protocol source, never executed\n'+rel.encode()+b'\n'
            path.write_bytes(raw); self.expected_sources[rel] = C.digest(raw)

    def make_package(self, name):
        package = self.site/name; package.mkdir()
        (package/'__init__.py').write_text('raise AssertionError("SYNTHETIC package must never import")\n')
        (package/'sub').mkdir(); (package/'sub/module.py').write_text('raise AssertionError("never import")\n')
        (package/'native.so').write_bytes(b'SYNTHETIC non-loadable native library\x00\x01')
        (package/'data.json').write_bytes(b'{"synthetic":true}\n')
        metadata = self.site/(name+'-1.0.dist-info'); metadata.mkdir()
        (metadata/'METADATA').write_text('Name: '+name+'\nVersion: 1.0\n')
        (metadata/'RECORD').touch()
        self.rewrite_record(name)

    def rewrite_record(self, name):
        files = []
        for top in (self.site/name, self.site/(name+'-1.0.dist-info'), self.site/(name+'.libs')):
            if top.exists(): files.extend(str(p.relative_to(self.site)) for p in top.rglob('*') if p.is_file())
        (self.site/(name+'-1.0.dist-info')/'RECORD').write_text(''.join(f'{rel},,\n' for rel in sorted(files)))

    def collect(self, *, suffix='', isolated=True):
        # Only synthetic fixture search path and runtime data are supplied.
        # The complete tokenizer collector bytes are unchanged.
        synthetic_runtime = runtime()
        synthetic_runtime['installation']['package_root'] = str(self.vllm)
        prefix = ('import hashlib, importlib.machinery, importlib.metadata, json, os, pathlib, sys\n'
                  'def need(ok,message):\n if not ok: raise RuntimeError(message)\n'
                  'def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()\n'
                  'def normalize(name): return name.lower().replace("-","_").replace(".","_")\n'
                  'search='+repr([str(self.site)])+'\nresult='+repr(synthetic_runtime)+'\n'
                  'TOKENIZER_SOURCES='+repr(self.expected_sources)+'\n'
                  'TOKENIZER_HELPER_SHA256='+repr(C.helper_sha256())+'\n')
        flags = ['-I', '-S', '-B'] if isolated else ['-I', '-B']
        return subprocess.run([sys.executable, *flags, '-c', prefix+suffix+C.TOKENIZER_CAPTURE],
                              capture_output=True, text=True, timeout=20,
                              env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(self.root/'must-not-load')})

    def result(self):
        result = self.collect(); self.assertEqual(result.returncode, 0, result.stderr)
        return C.json_bytes(result.stdout.split(C.TOKENIZER_PREFIX)[1])

    def test_versions_origins_every_source_data_native_and_metadata_byte_measured_without_import(self):
        result = self.result()
        synthetic_runtime = runtime(); synthetic_runtime['installation']['package_root'] = str(self.vllm)
        self.assertEqual(result['runtime_manifest_sha256'], C.digest(C.canonical(synthetic_runtime)))
        self.assertEqual(result['tokenizer_source_sha256'], self.expected_sources)
        C.validate_packages(result['packages'])
        for name, package in result['packages'].items():
            self.assertEqual(package['version'], '1.0')
            self.assertEqual(package['module_path'], str(self.site/name/'__init__.py'))
            self.assertEqual(len(package['files']), 6)
            for relative, pin in package['files'].items():
                self.assertEqual(pin['path'], str(self.site/relative))
                self.assertEqual(pin['sha256'], C.digest((self.site/relative).read_bytes()))

    def test_missing_or_duplicate_metadata_or_package_origin_rejected(self):
        metadata = self.site/'tokenizers-2.0.dist-info'; metadata.mkdir()
        (metadata/'METADATA').write_text('Name: tokenizers\nVersion: 2.0\n')
        self.assertNotEqual(self.collect().returncode, 0)
        (metadata/'METADATA').unlink(); metadata.rmdir()
        source = self.site/'tokenizers/__init__.py'; source.unlink()
        self.assertNotEqual(self.collect().returncode, 0)

    def test_unrecorded_files_missing_files_duplicate_record_and_traversal_rejected(self):
        record = self.site/'tokenizers-1.0.dist-info/RECORD'; original = record.read_text()
        unexpected = self.site/'tokenizers/extra.py'; unexpected.write_text('# unrecorded\n')
        self.assertNotEqual(self.collect().returncode, 0); unexpected.unlink()
        for tail in ('tokenizers/missing.py,,\n', 'tokenizers/__init__.py,,\n', 'tokenizers/../tokenizers/__init__.py,,\n'):
            record.write_text(original+tail)
            self.assertNotEqual(self.collect().returncode, 0)
        record.write_text(original)
        target = self.site/'tokenizers/sub/module.py'; target.unlink()
        self.assertNotEqual(self.collect().returncode, 0)

    def test_bytecode_excluded_auxiliary_wheel_libraries_included(self):
        cache = self.site/'tokenizers/__pycache__'; cache.mkdir()
        (cache/'dangerous.pyc').write_bytes(b'not executable')
        (self.site/'tokenizers/legacy.pyc').write_bytes(b'not executable either')
        libs = self.site/'tokenizers.libs'; libs.mkdir(); (libs/'library.so').write_bytes(b'not a real library')
        self.rewrite_record('tokenizers')
        value = self.result()['packages']['tokenizers']
        self.assertEqual(len(value['files']), 7)
        self.assertIn('tokenizers.libs/library.so', value['files'])
        self.assertFalse(any('.pyc' in path for path in value['files']))

    def test_file_and_directory_aliases_rejected(self):
        target = self.root/'real.py'; target.write_text('# outside\n')
        source = self.site/'tokenizers/sub/module.py'; source.unlink(); source.symlink_to(target)
        self.assertNotEqual(self.collect().returncode, 0)
        source.unlink(); source.write_text('# restored\n')
        (self.site/'tokenizers/unrecorded-alias').symlink_to(self.root, target_is_directory=True)
        self.assertNotEqual(self.collect().returncode, 0)

    def test_record_missing_not_isolated_and_preimported_packages_rejected(self):
        self.assertNotEqual(self.collect(isolated=False).returncode, 0)
        self.assertNotEqual(self.collect(suffix='sys.modules["transformers"]=object()\n').returncode, 0)
        (self.site/'tokenizers-1.0.dist-info/RECORD').unlink()
        self.assertNotEqual(self.collect().returncode, 0)


    def collect_combined(self, arm=ARM):
        # Both complete collectors run unchanged. Only synthetic source hashes,
        # package search paths and platform facts replace real-image inputs.
        expected = {}
        for rel in set(R.source_hashes(arm)) | set(C.tokenizer_source_hashes(arm)):
            path = self.vllm/rel; path.parent.mkdir(parents=True, exist_ok=True)
            raw = b'# SYNTHETIC ONLY, never imported\n'+rel.encode()+b'\n'
            path.write_bytes(raw); expected[rel] = C.digest(raw)
        for name in R.NATIVE_NAMES:
            (self.vllm/(name.split('.')[-1]+'.abi3.so')).write_bytes(b'\x7fELFsynthetic-not-loadable')
        for name in ('torch', 'tilelang', 'tile_kernels'):
            package = self.site/name; package.mkdir(exist_ok=True)
            (package/'__init__.py').write_text('raise AssertionError("never import")\n')
            metadata = self.site/(name+'-1.0.dist-info'); metadata.mkdir(exist_ok=True)
            (metadata/'METADATA').write_text('Name: '+name+'\nVersion: 1.0\n')
        (self.site/'torch/version.py').write_text('__version__ = "1.0"\n')
        source_pins = {rel: expected[rel] for rel in R.source_hashes(arm)}
        tokenizer_pins = {rel: expected[rel] for rel in C.tokenizer_source_hashes(arm)}
        with mock.patch.dict(R.REVISED_SOURCE_SHA256, {arm: source_pins}), \
                mock.patch.object(C, 'tokenizer_source_hashes', return_value=tokenizer_pins):
            plan = C.static_capture_plan(arm, CID)
            prefix = ('import sysconfig, platform\nsysconfig.get_path=lambda _: '+repr(str(self.site))+'\n'
                      'platform.machine=lambda: "aarch64"\nplatform.system=lambda: "Linux"\n')
            process = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', prefix+plan['argv'][-1]],
                                     capture_output=True, timeout=20,
                                     env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(self.site)})
            if process.returncode == 0:
                actual_runtime, actual_packages = C.validate_static_capture_output(process.stdout, arm)
                self.assertEqual(actual_runtime['source_sha256'], source_pins)
                self.assertEqual(actual_runtime['installation']['package_root'], str(self.vllm))
                self.assertEqual(set(actual_packages), set(C.PACKAGE_NAMES))
                return process, actual_runtime, actual_packages
        return process, None, None

    def test_full_combined_collectors_in_fresh_python_with_synthetic_image_tree(self):
        process, manifest, _ = self.collect_combined()
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(manifest['schema_version'], 1)
        self.assertEqual(manifest['absent_source_paths'], R.absent_source_paths(ARM))
        self.assertNotIn('empty_source_directories', manifest)
        self.assertFalse((self.vllm/R.absent_source_paths(ARM)[0]).exists())

    def test_combined_collectors_measure_empty_u_without_deleting_or_relabeling(self):
        steering = self.vllm/R.absent_source_paths(ARM)[0]
        steering.mkdir(parents=True)
        original = steering.stat()
        process, manifest, _ = self.collect_combined()
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(manifest['schema_version'], 2)
        self.assertEqual(manifest['absent_source_paths'], [])
        self.assertEqual(manifest['empty_source_directories'], R.absent_source_paths(ARM))
        self.assertTrue(steering.is_dir())
        self.assertFalse(steering.is_symlink())
        self.assertEqual(list(steering.iterdir()), [])
        self.assertEqual((steering.stat().st_dev, steering.stat().st_ino),
                         (original.st_dev, original.st_ino))
        baseline = process.stdout.splitlines()[0][len(C.BASELINE_PREFIX):]
        self.assertEqual(baseline, C.canonical(manifest))

    def test_combined_collectors_keep_steer_schema_one_with_all_sources(self):
        process, manifest, _ = self.collect_combined('r6x-steer')
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(manifest['schema_version'], 1)
        self.assertEqual(manifest['absent_source_paths'], [])
        self.assertNotIn('empty_source_directories', manifest)
        steering = self.vllm/R.absent_source_paths(ARM)[0]
        self.assertEqual(len(list(steering.iterdir())), 12)

    def test_combined_collectors_reject_every_kind_of_steering_child(self):
        steering = self.vllm/R.absent_source_paths(ARM)[0]
        steering.mkdir(parents=True)
        for name, kind in (('source.py', 'file'), ('.hidden', 'file'), ('old.pyc', 'file'),
                           ('__pycache__', 'directory'), ('empty-child', 'directory'),
                           ('file-link', 'file-link'), ('directory-link', 'directory-link'),
                           ('dangling-link', 'dangling-link')):
            with self.subTest(name=name):
                child = steering/name
                if kind == 'file': child.write_bytes(b'SYNTHETIC child, never executed\n')
                elif kind == 'directory': child.mkdir()
                elif kind == 'file-link': child.symlink_to(self.site/'tokenizers/__init__.py')
                elif kind == 'directory-link': child.symlink_to(self.site/'tokenizers', target_is_directory=True)
                else: child.symlink_to(self.root/'missing')
                try:
                    process, manifest, _ = self.collect_combined()
                    self.assertNotEqual(process.returncode, 0)
                    self.assertIn(b'contains a child entry', process.stderr)
                    self.assertNotIn(C.BASELINE_PREFIX.encode(), process.stdout)
                    self.assertNotIn(C.TOKENIZER_PREFIX.encode(), process.stdout)
                    self.assertIsNone(manifest)
                    self.assertTrue(child.exists() or child.is_symlink())
                finally:
                    if kind == 'directory': child.rmdir()
                    else: child.unlink()

    def test_combined_collectors_reject_steering_and_ancestor_aliases(self):
        steering = self.vllm/R.absent_source_paths(ARM)[0]
        steering.parent.mkdir(parents=True)
        target = self.root/'empty-target'; target.mkdir()
        for kind in ('directory-link', 'dangling-link', 'file'):
            with self.subTest(kind=kind):
                if kind == 'directory-link': steering.symlink_to(target, target_is_directory=True)
                elif kind == 'dangling-link': steering.symlink_to(self.root/'missing')
                else: steering.write_bytes(b'SYNTHETIC non-directory\n')
                try:
                    process, _, _ = self.collect_combined()
                    self.assertNotEqual(process.returncode, 0)
                    self.assertIn(b'aliased' if kind.endswith('link') else b'not an actual directory', process.stderr)
                    self.assertNotIn(C.BASELINE_PREFIX.encode(), process.stdout)
                    self.assertNotIn(C.TOKENIZER_PREFIX.encode(), process.stdout)
                finally:
                    steering.unlink()
        # Preserve every required source through the alias; rejection must not
        # depend on a missing pinned file or deleting the aliased directory.
        ancestor = steering.parent
        relocated = self.root/'relocated-deepseek'; ancestor.rename(relocated)
        (relocated/'steering').mkdir()
        ancestor.symlink_to(relocated, target_is_directory=True)
        process, _, _ = self.collect_combined()
        self.assertNotEqual(process.returncode, 0)
        self.assertIn(b'unpatched steering path is aliased', process.stderr)
        self.assertNotIn(C.BASELINE_PREFIX.encode(), process.stdout)
        self.assertNotIn(C.TOKENIZER_PREFIX.encode(), process.stdout)
        self.assertTrue(ancestor.is_symlink())
        self.assertEqual(list((relocated/'steering').iterdir()), [])

    def test_all_eight_tokenizer_sources_measured_and_source_drift_rejected(self):
        self.assertEqual(len(self.expected_sources), 8)
        path = self.vllm/'tokenizers/hf.py'; original = path.read_bytes()
        path.write_bytes(original+b'# changed trailing bytes\n')
        self.assertNotEqual(self.collect().returncode, 0)
        path.write_bytes(original)
        target = self.root/'alias.py'; target.write_bytes(original)
        path.unlink(); path.symlink_to(target)
        self.assertNotEqual(self.collect().returncode, 0)

    def test_unrecorded_auxiliary_libraries_fail_closed(self):
        libs = self.site/'tokenizers.libs'; libs.mkdir()
        (libs/'unrecorded.so').write_bytes(b'SYNTHETIC')
        self.assertNotEqual(self.collect().returncode, 0)


    def test_duplicate_metadata_fields_and_shadow_package_origin_fail_closed(self):
        metadata = self.site/'tokenizers-1.0.dist-info/METADATA'; original = metadata.read_text()
        for extra in ('Version: 2.0\n', 'Name: tokenizers\n'):
            metadata.write_text(original+extra)
            self.assertNotEqual(self.collect().returncode, 0)
        metadata.write_text(original)
        alternate = self.root/'shadow'; (alternate/'tokenizers').mkdir(parents=True)
        (alternate/'tokenizers/__init__.py').write_text('raise AssertionError("never import")\n')
        self.assertNotEqual(self.collect(suffix='search.append('+repr(str(alternate))+')\n').returncode, 0)


    def test_record_cannot_substitute_unrelated_distribution_metadata_tree(self):
        counterfeit = self.site/'tokenizers-9.0.dist-info'; counterfeit.mkdir()
        (counterfeit/'METADATA').write_text('Name: unrelated\nVersion: 9.0\n')
        (counterfeit/'RECORD').write_text('SYNTHETIC unrelated metadata\n')
        entries = [str(path.relative_to(self.site))
                   for path in (self.site/'tokenizers').rglob('*') if path.is_file()]
        entries += ['tokenizers-9.0.dist-info/METADATA', 'tokenizers-9.0.dist-info/RECORD']
        (self.site/'tokenizers-1.0.dist-info/RECORD').write_text(
            ''.join(relative+',,\n' for relative in entries))
        result = self.collect()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('RECORD does not inventory selected distribution metadata root', result.stderr)
        self.assertNotIn(C.TOKENIZER_PREFIX, result.stdout)

    def test_inventory_binds_actual_version_metadata_and_record_snapshots(self):
        value = self.result()['packages']['tokenizers']
        for name in ('METADATA', 'RECORD'):
            relative = 'tokenizers-1.0.dist-info/'+name
            self.assertEqual(value['files'][relative]['sha256'], C.digest((self.site/relative).read_bytes()))
        self.assertEqual(value['version'], '1.0')
        # Simulate a file changing between snapshot parsing and inventory hashing.
        # No real image or external process participates in this fault injection.
        suffix = ('original_sha=sha\n'
                  'def sha(path):\n'
                  ' if path.name=="METADATA" and path.parent.name=="tokenizers-1.0.dist-info": return "f"*64\n'
                  ' return original_sha(path)\n')
        result = self.collect(suffix=suffix)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('inventoried METADATA/RECORD differ from selected distribution snapshots', result.stderr)

    def test_record_hashes_not_trusted_and_inventory_changes_with_actual_bytes(self):
        before = self.result()['packages']['tokenizers']
        source = self.site/'tokenizers/sub/module.py'; source.write_bytes(b'# changed actual bytes\n')
        after = self.result()['packages']['tokenizers']
        self.assertNotEqual(before['inventory_sha256'], after['inventory_sha256'])
        self.assertEqual(after['files']['tokenizers/sub/module.py']['sha256'], C.digest(source.read_bytes()))


if __name__ == '__main__':
    unittest.main()
