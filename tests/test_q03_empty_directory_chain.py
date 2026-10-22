"""Matched capture -> manifest -> actual observer -> ready validators, CPU fixtures only."""
from contextlib import ExitStack, contextmanager
import copy
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import q03_baseline_identity as I
import q03_baseline_worker as W
import q03_baseline_service as S
import q03_http_service as H
ARM='r6x-unpatched'
PATH='models/deepseek_v4_1/steering'


class EmptyDirectoryChainTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.site=self.root/'site';self.site.mkdir()
        self.vllm=self.site/'vllm';self.vllm.mkdir();self.expected={}
        for relative in I.REVISED_SOURCE_SHA256[ARM]:
            path=self.vllm/relative;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(b'raise AssertionError("never execute synthetic package code")\n')
            self.expected[relative]=I.digest(path.read_bytes())
        for name in I.NATIVE_NAMES:
            (self.vllm/(name.split('.')[-1]+'.abi3.so')).write_bytes(b'\x7fELFnot-loadable-cpu-fixture')
        for name in ('torch','tilelang','tile_kernels'):
            package=self.site/name;package.mkdir();(package/'__init__.py').write_bytes(b'raise AssertionError\n')
            dist=self.site/(name+'-1.0.dist-info');dist.mkdir()
            (dist/'METADATA').write_text('Name: '+name+'\nVersion: 1.0\n')
        (self.site/'torch/version.py').write_text('__version__ = "1.0"\n')
        self.steering=self.vllm/PATH

    def collect(self, empty):
        if empty:self.steering.mkdir(mode=0o755)
        params=dict(identity=dict(arm=ARM,**I.REVISED_IDENTITIES[ARM]),
                    source_sha256=self.expected,native_names=I.NATIVE_NAMES,absent_source_paths=[PATH])
        # Isolated synthetic filesystem search, not a target image or actual GPU.
        prep='import sysconfig, platform\nsysconfig.get_path=lambda _: '+repr(str(self.site))+'\n'
        prep+='platform.machine=lambda: "aarch64"\nplatform.system=lambda: "Linux"\n'
        result=subprocess.run([sys.executable,'-I','-S','-B','-c',prep+'P='+repr(params)+'\n'+I.STATIC_CAPTURE],
                              env={'PATH':'/usr/bin:/bin','PYTHONPATH':str(self.site)},
                              capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        return I.json_bytes(result.stdout.split('Q03_BASELINE_STATIC=')[1])

    @contextmanager
    def fixture_bindings(self,runtime):
        # Synthetic CPU fixture pins are scoped here; actual frozen bindings stay unchanged.
        sources=copy.deepcopy(I.REVISED_SOURCE_SHA256);sources[ARM]=self.expected
        for key in ('tokenizers/deepseek_v41.py','tokenizers/deepseek_v41_encoding.py'):
            sources['r6x-steer'][key]=self.expected[key]
        raw=I.canonical(runtime);path=self.root/'runtime-manifest.json';path.write_bytes(raw);path.chmod(0o600)
        pins=copy.deepcopy(I.REVISED_FREEZE['runtime_manifest_sha256']);pins[ARM]=I.digest(raw)
        freeze={'common_runtime_sha256':I.common_runtime_sha256(runtime),'runtime_manifest_sha256':pins}
        with mock.patch.dict(I.REVISED_SOURCE_SHA256,sources,clear=True), mock.patch.dict(I.REVISED_FREEZE,freeze):
            binding={'schema_version':1,'arm':ARM,'provenance':{'path':str(path),'sha256':I.digest(raw)}}
            checked=I.validate_binding(binding)
            self.assertEqual(I.validate_static_capture_output('Q03_BASELINE_STATIC='+raw.decode(),ARM),runtime)
            yield checked,path

    def actual_observer_reports(self,checked,path):
        runtime=checked['runtime'];handshake={'window_id':'a'*32,'attempt_id':'b'*32}
        effective={'model_config.max_model_len':1048576}
        spec=dict(handshake=handshake,baseline_identity=checked,expected_effective_config=effective)
        raw=I.canonical(spec);specpath=self.root/'http-spec.json';specpath.write_bytes(raw)
        modules={}
        for name in ('torch','tilelang','tile_kernels','vllm','vllm.distributed',
                     'vllm.models','vllm.models.deepseek_v4_1','vllm.models.deepseek_v4_1.nvidia',
                     'vllm.models.deepseek_v4_1.nvidia.model',*I.NATIVE_NAMES):
            modules[name]=ModuleType(name)
        for name in ('torch','tilelang','tile_kernels'):
            modules[name].__file__=runtime['package_origins'][name]
        modules['vllm'].__file__=str(self.vllm/'__init__.py')
        modules['torch'].__version__=runtime['torch_version']
        # Deliberately inert API-shaped doubles; no native package/device is imported.
        modules['torch'].cuda=SimpleNamespace(get_device_capability=lambda:(12,1))
        for name in I.NATIVE_NAMES:modules[name].__file__=runtime['native_paths'][name]
        class Model:
            use_sequence_parallel=False
            def modules(self):return [self]
        modules['vllm.models.deepseek_v4_1.nvidia.model'].DeepseekV4Model=Model
        observer=W.HttpObservation();observer.local_rank=0;observer.vllm_config=object()
        observer.model_runner=SimpleNamespace(get_model=lambda:Model(),vllm_config=object())
        modules['vllm.distributed'].get_tensor_model_parallel_rank=lambda:observer.rank
        def mapped(value):
            return {'/runtime/http-spec.json':specpath,'/runtime/runtime-manifest.json':path}.get(str(value),Path(value))
        env={'Q03_HTTP_SPEC_SHA256':I.digest(raw),'Q03_HTTP_WINDOW':handshake['window_id'],
             'Q03_HTTP_ATTEMPT':handshake['attempt_id'],'VLLM_ALLOW_INSECURE_SERIALIZATION':'0'}
        with ExitStack() as stack:
            stack.enter_context(mock.patch.dict(sys.modules,modules))
            stack.enter_context(mock.patch.dict(os.environ,env,clear=True))
            stack.enter_context(mock.patch.object(W,'verify_bytecode_policy'))
            stack.enter_context(mock.patch.object(W,'load_identity',return_value=I))
            stack.enter_context(mock.patch.object(W,'Path',side_effect=mapped))
            stack.enter_context(mock.patch.object(W,'project_effective_config',return_value=effective))
            stack.enter_context(mock.patch.object(W.importlib.metadata,'version',side_effect=runtime['metadata_versions'].__getitem__))
            reports=[]
            for rank in range(4):
                observer.rank=rank;observer.parallel_config=SimpleNamespace(rank=rank,node_rank=rank,node_rank_within_dp=rank)
                reports.append(observer.q03_http_observe())
        plan=dict(handshake,spec_sha256=I.digest(raw),selection={'arm':ARM,'steering':'absent'},spec=spec)
        ready=dict(schema_version=1,**handshake,spec_sha256=plan['spec_sha256'],reports=reports,
                   reports_sha256=I.digest(I.canonical(reports)))
        return plan,ready

    def test_full_version_two_chain_and_rehashed_poststart_witness_faults(self):
        runtime=self.collect(True)
        self.assertEqual(runtime['schema_version'],2)
        self.assertEqual(runtime['absent_source_paths'],[])
        self.assertEqual(runtime['empty_source_directories'],[PATH])
        with self.fixture_bindings(runtime) as (checked,path):
            plan,ready=self.actual_observer_reports(checked,path)
            self.assertEqual(I.validate_ready(I.canonical(ready),plan),ready)
            for change in (lambda r:r.update(absent_source_paths=[PATH]),
                           lambda r:r.pop('empty_source_directories'),
                           lambda r:r.update(empty_source_directories=[]),
                           lambda r:r.update(steering_capability='present')):
                broken=copy.deepcopy(ready);change(broken['reports'][0])
                broken['reports_sha256']=I.digest(I.canonical(broken['reports']))
                with self.assertRaisesRegex(ValueError,'capability/absence'):
                    I.validate_ready(I.canonical(broken),plan)
            self.steering.rmdir()
            with self.assertRaisesRegex(RuntimeError,'absence witness changed'):
                self.actual_observer_reports(checked,path)

    def test_version_one_absent_chain_preserved_and_not_upgraded_silently(self):
        runtime=self.collect(False);self.assertEqual(runtime['schema_version'],1)
        with self.fixture_bindings(runtime) as (checked,path):
            plan,ready=self.actual_observer_reports(checked,path)
            self.assertEqual(I.validate_ready(I.canonical(ready),plan),ready)
            self.assertNotIn('empty_source_directories',ready['reports'][0])
            self.steering.mkdir()
            with self.assertRaisesRegex(RuntimeError,'absence witness changed'):
                self.actual_observer_reports(checked,path)

    def test_worker_rejects_child_bytecode_symlink_and_read_error(self):
        runtime=self.collect(True)
        with self.fixture_bindings(runtime) as (checked,path):
            for name in ('__init__.py','.hidden','code.pyc'):
                child=self.steering/name;child.write_bytes(b'')
                with self.subTest(name=name),self.assertRaisesRegex(ValueError,'child entry'):
                    self.actual_observer_reports(checked,path)
                child.unlink()
            child=self.steering/'__pycache__';child.mkdir()
            with self.assertRaisesRegex(ValueError,'child entry'):self.actual_observer_reports(checked,path)
            child.rmdir()
            with mock.patch.object(os,'scandir',side_effect=PermissionError('unreadable')),self.assertRaises(PermissionError):
                self.actual_observer_reports(checked,path)
            self.steering.rmdir();self.steering.symlink_to(self.root/'missing')
            with self.assertRaises(ValueError):self.actual_observer_reports(checked,path)

    def test_empty_namespace_does_not_authorize_imported_steering_or_schema_drift(self):
        runtime=self.collect(True)
        with self.fixture_bindings(runtime) as (checked,path):
            name='vllm.models.deepseek_v4_1.steering'
            with mock.patch.dict(sys.modules,{name:ModuleType(name)}), self.assertRaisesRegex(RuntimeError,'imported steering code'):
                self.actual_observer_reports(checked,path)
            for changes in ({'schema_version':1}, {'schema_version':True},
                            {'absent_source_paths':[PATH]}, {'empty_source_directories':[]},
                            {'empty_source_directories':['unknown']}, {'arm':'r6x-steer'}):
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    I.validate_runtime(dict(runtime,**changes),ARM)

    def test_actual_bindings_and_matched_capture_sources_preserved(self):
        # Exact collector/witness bytes independently sealed in standalone c9a983dc.
        self.assertEqual(I.digest(I.STATIC_CAPTURE.encode()),
                         'b21e5189173c59186d53be6cfcaf7eafe5b718722ced1b923b31d0b6a41b8336')
        self.assertEqual(I.digest(I.SOURCE_ABSENCE_CHECK.encode()),
                         '0fbdbfe8db492cc2b618c941c17037d2c3dfd100f9f84ef00e1b72e975ed663a')
        self.assertEqual(I.REVISED_FREEZE['runtime_manifest_sha256']['r6x-steer'],
                         'c7a9ec935683bff337560e128642e9a40f97367a8a71fe0c1a2117c136828ac2')
        self.assertEqual(I.REVISED_FREEZE['profile_sha256'][ARM],
                         '62f545bf727f23a5c24dddd35358ce6caba4fde2d81b5684187a414c998e7e83')
        self.assertEqual(I.REVISED_FREEZE['model_tokenizer_sha256'],
                         '43a054fb5dd965948b611ea5ccd68178177f97e544922d7cd2fdc3c115203013')
        self.assertEqual(I.REVISED_FREEZE['common_runtime_sha256'],
                         '71f2c58abc65e3083e528987bff23aff9a64a7551d4d9e9bee5e6ba875cb79a2')
        self.assertEqual(I.REVISED_FREEZE['runtime_manifest_sha256'][ARM],
                         '01be8e3f0d13a7ff446ae94d0c9f838081327156764cf6f79069079cf1e0e011')
        self.assertEqual(S.IDENTITY_SOURCE_SHA256,I.digest((ROOT/'scripts/q03_baseline_identity.py').read_bytes()))
        self.assertEqual(H.BASELINE_SOURCE_SHA256,I.digest((ROOT/'scripts/q03_baseline_service.py').read_bytes()))
        self.assertEqual(I.validate_revised_freeze(), I.REVISED_FREEZE)
        self.assertEqual(H.baseline_module().identity_module().checked_identity(ARM),
                         dict(arm=ARM, **I.REVISED_IDENTITIES[ARM]))


if __name__=='__main__':unittest.main()
