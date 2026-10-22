"""Real CPU collector/filesystem/serializer tests; Docker calls are inert fakes."""
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
spec = importlib.util.spec_from_file_location('capture', ROOT / 'scripts/q03_runtime_capture.py')
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)
ARM = 'r6x-unpatched'
CID = 'a' * 32


def runtime(arm=ARM):
    root = '/opt/image/vllm/vllm'
    return dict(schema_version=1, **C.checked_identity(arm),
        installation={'mode': 'image-source', 'package_root': root},
        platform={'python': '3.12.3', 'machine': 'aarch64', 'system': 'Linux'},
        metadata_versions={n: '1.0' for n in ('torch', 'tilelang', 'tile_kernels')},
        package_origins={n: '/opt/site/' + n + '/__init__.py' for n in ('torch', 'tilelang', 'tile_kernels')},
        package_sha256={n: 'b'*64 for n in ('torch', 'tilelang', 'tile_kernels')},
        torch_version='1.0', torch_version_sha256='c'*64,
        native_paths={n: root + '/' + n.split('.')[-1] + '.abi3.so' for n in C.NATIVE_NAMES},
        native_sha256={n: 'd'*64 for n in C.NATIVE_NAMES}, source_sha256=C.source_hashes(arm),
        pythonpath='/opt/image/vllm:/opt/site', absent_source_paths=C.absent_source_paths(arm))


def image(arm=ARM):
    identity = C.checked_identity(arm)
    components = dict(C.IMAGE_FREEZE['component_sources'],
                      vllm={'head': identity['source_head'], 'tree': identity['source_tree']})
    labels = {'local.spark3.' + name + '.' + suffix: value[key]
              for name, value in components.items() for suffix, key in (('patch-head', 'head'), ('tree', 'tree'))}
    labels['local.site.native-parent'] = C.IMAGE_FREEZE['native_parent_image']
    return dict(Id=identity['image'], Architecture='arm64', Os='linux',
                Config={'Labels': labels, 'Env': ['PYTHONPATH=' + runtime(arm)['pythonpath']]},
                RootFS={'Type': 'layers', 'Layers': ['sha256:' + 'e'*64]})


class PlanAndSchemaTests(unittest.TestCase):
    def test_finite_fixed_arms_and_no_pending_dependencies(self):
        source = Path(C.__file__).read_text()
        self.assertNotIn('import q03_', source)
        self.assertNotIn('UNBOUND', source)
        self.assertEqual([len(C.source_hashes(a)) for a in C.REVISED_ARMS], [19, 31])
        for arm in C.REVISED_ARMS:
            plan = C.static_capture_plan(arm, CID)
            argv = plan['argv']
            self.assertIn(C.REVISED_IDENTITIES[arm]['image'], argv)
            for flag in ('--runtime=runc', '--network=none', '--read-only', '--cap-drop=ALL',
                         '--memory=1g', '--memory-swap=1g', '--cpus=1', '--pids-limit=64',
                         '--security-opt=no-new-privileges', 'NVIDIA_VISIBLE_DEVICES=void'):
                self.assertIn(flag, argv)
            self.assertFalse(any(v.startswith(('--gpus', '--device', '--mount', '--privileged')) for v in argv))
            self.assertEqual(argv[-5:-1], ['-I', '-S', '-B', '-c'])
            self.assertEqual(plan['timeout_seconds'], 120)
            imports = []
            for n in ast.walk(ast.parse(argv[-1])):
                if isinstance(n, ast.Import): imports.extend(a.name for a in n.names)
                elif isinstance(n, ast.ImportFrom): imports.append(n.module)
            self.assertTrue(set(imports) <= {'ast','hashlib','importlib.machinery','importlib.metadata',
                                           'json','os','pathlib','platform','stat','sys','sysconfig'})
            self.assertEqual(C.validate_capture(runtime(arm), arm), runtime(arm))
        for arm in ('r6x188', 'native197-off', 'custom', True):
            with self.assertRaises(ValueError): C.static_capture_plan(arm, CID)
        for cid in ('../x', 'a'*31, 'A'*32, True):
            with self.assertRaises(ValueError): C.static_capture_plan(ARM, cid)

    def test_actual_serializer_marker_roundtrip_and_faults(self):
        for arm in C.REVISED_ARMS:
            value = runtime(arm)
            raw = 'diagnostic\nQ03_BASELINE_STATIC=' + C.canonical(value).decode() + '\n'
            self.assertEqual(C.validate_static_capture_output(raw, arm), value)
            self.assertEqual(C.canonical(C.json_bytes(C.canonical(value))), C.canonical(value))
            for broken in (raw+raw, '{}', 'x'*(C.MAX_BYTES+1), raw.replace('"schema_version":1','"schema_version":true')):
                with self.assertRaises(ValueError): C.validate_static_capture_output(broken, arm)
        for raw in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'):
            with self.assertRaises(ValueError): C.json_bytes(raw)
        with self.assertRaises(ValueError): C.canonical({'x': float('nan')})

    def test_versioned_empty_directory_witness_is_only_fixed_u(self):
        value=runtime(); value.update(schema_version=2, absent_source_paths=[],
                                     empty_source_directories=C.absent_source_paths(ARM))
        self.assertEqual(C.validate_capture(value,ARM),value)
        self.assertEqual(C.validate_static_capture_output(
            'Q03_BASELINE_STATIC='+C.canonical(value).decode(),ARM),value)
        for changes in ({'schema_version':1}, {'schema_version':True}, {'schema_version':3},
                        {'absent_source_paths':C.absent_source_paths(ARM)},
                        {'empty_source_directories':[]},
                        {'empty_source_directories':['models/deepseek_v4_1/unknown']},
                        {'image':C.REVISED_IDENTITIES['r6x-steer']['image']}):
            broken=dict(value,**changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                C.validate_capture(broken,ARM)
        for arm in C.REVISED_ARMS:
            old=runtime(arm); old['empty_source_directories']=[]
            with self.assertRaises(ValueError): C.validate_capture(old,arm)
        steer=runtime('r6x-steer'); steer.update(schema_version=2,empty_source_directories=[])
        with self.assertRaises(ValueError): C.validate_capture(steer,'r6x-steer')

    def test_provenance_inventory_native_and_metadata_faults(self):
        changes = [('image', C.REVISED_IDENTITIES['r6x-steer']['image']), ('source_head','f'*40),
                   ('source_tree','f'*40), ('pythonpath','relative'), ('schema_version',True),
                   ('absent_source_paths',[]), ('torch_version_sha256','bad')]
        for key, value in changes:
            r=runtime(); r[key]=value
            with self.subTest(key=key), self.assertRaises(ValueError): C.validate_capture(r, ARM)
        for key in ('metadata_versions','package_origins','package_sha256','native_paths','native_sha256','source_sha256'):
            r=runtime(); r[key].pop(next(iter(r[key])))
            with self.subTest(key=key), self.assertRaises(ValueError): C.validate_capture(r, ARM)
        r=runtime(); r['native_paths'][C.NATIVE_NAMES[0]]='/other/_C_stable_libtorch.abi3.so'
        with self.assertRaises(ValueError): C.validate_capture(r, ARM)


class FilesystemExecutionTests(unittest.TestCase):
    def setUp(self):
        # CI deliberately puts TMPDIR inside the checkout. Capture evidence must
        # stay outside it, so use the canonical system temp parent explicitly.
        self.temp=tempfile.TemporaryDirectory(prefix='q03-runtime-evidence-', dir=Path('/tmp').resolve())
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve(); self.root.chmod(0o700)
        self.assertFalse(self.root.is_relative_to(ROOT))
        self.output=self.root/'new-capture'; self.calls=[]
        self.image=image()
        self.pin=mock.patch.dict(C.IMAGE_FREEZE['image_rootfs_sha256'],
                                {ARM: C.digest(C.canonical(self.image['RootFS']))})
        self.pin.start(); self.addCleanup(self.pin.stop)
        self.raw=b'diagnostic preserved\nQ03_BASELINE_STATIC='+C.canonical(runtime())+b'\n'

    def runner(self, argv, **kw):
        self.calls.append(argv)
        if 'image' in argv: result=C.canonical([self.image])
        elif 'run' in argv: result=self.raw
        else: result=b''
        kw['stdout'].write(result); kw['stderr'].write(b'raw stderr\n')
        self.assertEqual(kw['stdin'], subprocess.DEVNULL)
        self.assertFalse(kw['check']); self.assertEqual(kw['preexec_fn'],C.child_limits)
        return SimpleNamespace(returncode=0)

    def test_real_exclusive_writes_canonical_manifest_and_raw_receipts(self):
        receipt=C.capture(ARM,CID,str(self.output),self.runner)
        self.assertEqual(receipt['status'],'captured-unreviewed')
        self.assertIs(receipt['runtime_qualification'],False)
        self.assertEqual((self.output/'capture.stdout').read_bytes(),self.raw)
        manifest=(self.output/'runtime-manifest.json').read_bytes()
        self.assertEqual(manifest,C.canonical(runtime()))
        self.assertEqual(receipt['runtime_manifest_sha256'],C.digest(manifest))
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode),0o700)
        for p in self.output.iterdir(): self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o600)
        self.assertEqual(len(self.calls),4)
        self.assertEqual(self.calls[2],C.static_capture_plan(ARM,CID)['argv'])
        with self.assertRaises(FileExistsError): C.capture(ARM,CID,str(self.output),self.runner)
        self.assertEqual(len(self.calls),4)
        self.assertEqual((self.output/'capture.stdout').read_bytes(),self.raw)

    def test_parent_permission_and_alias_rejected_before_execution(self):
        for mode in (0o755,0o777,0o750):
            self.root.chmod(mode)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                C.capture(ARM,CID,str(self.output),self.runner)
        self.root.chmod(0o700)
        (self.root/'alias').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError): C.capture(ARM,CID,str(self.root/'alias'/'capture'),self.runner)
        self.assertEqual(self.calls,[])

    def test_in_checkout_output_rejected_before_execution(self):
        with tempfile.TemporaryDirectory(prefix='.runtime-capture-test-', dir=ROOT) as temp:
            parent=Path(temp).resolve(); parent.chmod(0o700)
            output=parent/'forbidden-capture'
            with self.assertRaisesRegex(ValueError, 'output must be separate from the source slice'):
                C.capture(ARM,CID,str(output),self.runner)
            self.assertFalse(output.exists())
            self.assertEqual(self.calls,[])

    def test_existing_file_and_symlink_never_overwritten(self):
        fd=C.open_output(str(self.output))
        try:
            C.save(fd,'owned.json',b'original')
            with self.assertRaises(FileExistsError): C.save(fd,'owned.json',b'changed')
            (self.output/'link.json').symlink_to(self.output/'owned.json')
            with self.assertRaises(FileExistsError): C.save(fd,'link.json',b'changed')
            self.assertEqual((self.output/'owned.json').read_bytes(),b'original')
        finally: os.close(fd)

    def test_nonzero_timeout_invalid_output_and_busy_keep_raw_not_manifest(self):
        for failure in ('nonzero','timeout','invalid','busy','postbusy'):
            output=self.root/failure
            def runner(argv, **kw):
                if 'run' in argv:
                    kw['stdout'].write(b'invalid' if failure=='invalid' else self.raw)
                    kw['stderr'].write(b'failure stderr')
                    if failure=='timeout': raise subprocess.TimeoutExpired(argv,120)
                    return SimpleNamespace(returncode=7 if failure=='nonzero' else 0)
                if 'container' in argv and (failure=='busy' or (failure=='postbusy' and (output/'capture.stdout').exists())):
                    kw['stdout'].write(b'existing-container\n'); return SimpleNamespace(returncode=0)
                return self.runner(argv,**kw)
            with self.subTest(failure=failure), self.assertRaises((ValueError,subprocess.TimeoutExpired)):
                C.capture(ARM,CID,str(output),runner)
            self.assertFalse((output/'runtime-manifest.json').exists())
            receipt=C.json_bytes((output/'receipt.json').read_bytes())
            self.assertEqual(receipt['status'],'failed-unreviewed')
            self.assertIs(receipt['runtime_qualification'],False)
            self.assertTrue((output/'preabsence.stdout').exists())

    def test_wrong_image_or_native_parent_never_starts_collector(self):
        for key,value in (('Id',C.REVISED_IDENTITIES['r6x-steer']['image']),('Architecture','amd64')):
            self.image=image(); self.image[key]=value
            with self.assertRaises(ValueError): C.capture(ARM,CID,str(self.root/key),self.runner)
            self.assertFalse(any('run' in a for a in self.calls))
        self.image=image(); self.image['Config']['Labels']['local.site.native-parent']='sha256:'+'0'*64
        with self.assertRaises(ValueError): C.capture(ARM,CID,str(self.root/'parent'),self.runner)
        self.assertFalse(any('run' in a for a in self.calls))


class RealCollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve(); self.site=self.root/'site'; self.site.mkdir()
        self.vllm=self.site/'vllm'; self.vllm.mkdir()
        self.expected={}
        for rel in C.source_hashes(ARM):
            path=self.vllm/rel; path.parent.mkdir(parents=True,exist_ok=True)
            raw=b'raise AssertionError("package must never import")\n'; path.write_bytes(raw)
            self.expected[rel]=C.digest(raw)
        for name in C.NATIVE_NAMES:
            (self.vllm/(name.split('.')[-1]+'.abi3.so')).write_bytes(b'\x7fELFsynthetic-not-loadable')
        for name in ('torch','tilelang','tile_kernels'):
            package=self.site/name; package.mkdir()
            (package/'__init__.py').write_text('raise AssertionError("do not import")\n')
            dist=self.site/(name+'-1.0.dist-info'); dist.mkdir()
            (dist/'METADATA').write_text('Name: '+name+'\nVersion: 1.0\n')
        (self.site/'torch/version.py').write_text('__version__ = "1.0"\n')

    def collect(self, **overrides):
        params=dict(identity=C.checked_identity(ARM), source_sha256=self.expected,
                    native_names=C.NATIVE_NAMES, absent_source_paths=C.absent_source_paths(ARM))
        params.update(overrides)
        # Only synthetic package search/platform are injected. Collector bytes are unchanged.
        prep='import sysconfig, platform\nsysconfig.get_path=lambda _: '+repr(str(self.site))+'\n'
        prep+='platform.machine=lambda: "aarch64"\nplatform.system=lambda: "Linux"\n'
        code=prep+'P='+repr(params)+'\n'+C.STATIC_CAPTURE
        return subprocess.run([sys.executable,'-I','-S','-B','-c',code],
                              env={'PYTHONPATH':str(self.site),'PATH':'/usr/bin:/bin'},
                              capture_output=True,text=True,timeout=20)

    def test_real_body_hashes_bytes_metadata_origins_without_import(self):
        result=self.collect(); self.assertEqual(result.returncode,0,result.stderr)
        value=C.json_bytes(result.stdout.split('Q03_BASELINE_STATIC=')[1])
        self.assertEqual(value['source_sha256'],self.expected)
        self.assertEqual(value['absent_source_paths'],C.absent_source_paths(ARM))
        for name,path in value['native_paths'].items():
            self.assertEqual(value['native_sha256'][name],C.digest(Path(path).read_bytes()))
        for name,path in value['package_origins'].items():
            self.assertEqual(value['package_sha256'][name],C.digest(Path(path).read_bytes()))
        self.assertEqual(value['metadata_versions'],{n:'1.0' for n in ('torch','tilelang','tile_kernels')})
        self.assertEqual(value['torch_version_sha256'],C.digest((self.site/'torch/version.py').read_bytes()))

    def test_absent_and_actual_empty_directory_are_truthful_distinct_versions(self):
        absent=self.collect(); self.assertEqual(absent.returncode,0,absent.stderr)
        old=C.json_bytes(absent.stdout.split('Q03_BASELINE_STATIC=')[1])
        self.assertEqual(old['schema_version'],1)
        self.assertEqual(old['absent_source_paths'],C.absent_source_paths(ARM))
        self.assertNotIn('empty_source_directories',old)
        steering=self.vllm/'models/deepseek_v4_1/steering'; steering.mkdir(mode=0o755)
        result=self.collect(); self.assertEqual(result.returncode,0,result.stderr)
        value=C.json_bytes(result.stdout.split('Q03_BASELINE_STATIC=')[1])
        self.assertEqual(value['schema_version'],2)
        self.assertEqual(value['absent_source_paths'],[])
        self.assertEqual(value['empty_source_directories'],C.absent_source_paths(ARM))
        with mock.patch.dict(C.REVISED_SOURCE_SHA256,{ARM:self.expected}):
            self.assertEqual(C.validate_static_capture_output(result.stdout,ARM),value)
        for key in old:
            if key not in ('schema_version','absent_source_paths'):
                self.assertEqual(value[key],old[key])

    def test_s_manifest_stays_exact_version_one_shape(self):
        expected={}
        for relative in C.source_hashes('r6x-steer'):
            path=self.vllm/relative; path.parent.mkdir(parents=True,exist_ok=True)
            if not path.exists(): path.write_bytes(b'raise AssertionError("do not import")\n')
            expected[relative]=C.digest(path.read_bytes())
        result=self.collect(identity=C.checked_identity('r6x-steer'),
                            source_sha256=expected,absent_source_paths=[])
        self.assertEqual(result.returncode,0,result.stderr)
        value=C.json_bytes(result.stdout.split('Q03_BASELINE_STATIC=')[1])
        self.assertEqual(set(value),set(runtime('r6x-steer')))
        self.assertEqual(value['schema_version'],1)
        self.assertEqual(value['absent_source_paths'],[])
        self.assertNotIn('empty_source_directories',value)
        with mock.patch.dict(C.REVISED_SOURCE_SHA256,{'r6x-steer':expected}):
            self.assertEqual(C.validate_capture(value,'r6x-steer'),value)

    def test_any_child_file_bytecode_directory_or_link_fails_closed(self):
        steering=self.vllm/'models/deepseek_v4_1/steering'; steering.mkdir()
        for name in ('__init__.py','code.pyc','.hidden','unknown'):
            child=steering/name; child.write_bytes(b'')
            with self.subTest(name=name): self.assertNotEqual(self.collect().returncode,0)
            child.unlink()
        child=steering/'__pycache__'; child.mkdir()
        self.assertNotEqual(self.collect().returncode,0); child.rmdir()
        child=steering/'link'; child.symlink_to(self.root/'missing')
        self.assertNotEqual(self.collect().returncode,0); child.unlink()
        steering.rmdir(); steering.symlink_to(self.root/'missing')
        self.assertNotEqual(self.collect().returncode,0); steering.unlink()
        actual=self.root/'empty'; actual.mkdir(); steering.symlink_to(actual,target_is_directory=True)
        self.assertNotEqual(self.collect().returncode,0); steering.unlink()
        steering.write_bytes(b'')
        self.assertNotEqual(self.collect().returncode,0)

    def test_directory_inspection_errors_and_identity_drift_fail_closed(self):
        steering=self.vllm/'models/deepseek_v4_1/steering'; steering.mkdir()
        for action in ('lstat','open','scandir'):
            target=mock.patch.object(Path,'lstat',side_effect=PermissionError('unreadable')) if action=='lstat' else mock.patch.object(os,action,side_effect=PermissionError('unreadable'))
            with self.subTest(action=action), target, self.assertRaises(PermissionError):
                C.inspect_source_absence(self.vllm,ARM)
        identity=C.checked_identity(ARM); identity['image']=C.REVISED_IDENTITIES['r6x-steer']['image']
        self.assertNotEqual(self.collect(identity=identity).returncode,0)

    def test_source_mutation_and_alias_fail_closed(self):
        source=self.vllm/'envs.py'; before=source.read_bytes(); source.write_bytes(b'changed')
        self.assertNotEqual(self.collect().returncode,0)
        source.unlink(); target=self.root/'actual.py'; target.write_bytes(before); source.symlink_to(target)
        self.assertNotEqual(self.collect().returncode,0)

    def test_native_ambiguity_bad_elf_and_alias_fail_closed(self):
        path=self.vllm/'_C_stable_libtorch.abi3.so'; original=path.read_bytes()
        other=self.vllm/'_C_stable_libtorch.other.so'; other.write_bytes(original)
        self.assertNotEqual(self.collect().returncode,0); other.unlink()
        path.write_bytes(b'not ELF'); self.assertNotEqual(self.collect().returncode,0)
        path.unlink(); target=self.root/'native.so'; target.write_bytes(original); path.symlink_to(target)
        self.assertNotEqual(self.collect().returncode,0)

    def test_duplicate_metadata_and_nonliteral_torch_version_fail_closed(self):
        dist=self.site/'torch-2.0.dist-info'; dist.mkdir(); (dist/'METADATA').write_text('Name: torch\nVersion: 2.0\n')
        self.assertNotEqual(self.collect().returncode,0)
        (dist/'METADATA').unlink(); dist.rmdir()
        (self.site/'torch/version.py').write_text('__version__ = str("1.0")\n')
        self.assertNotEqual(self.collect().returncode,0)


if __name__=='__main__': unittest.main()
