"""Test-only checkout identity contracts; Git/Docker are explicitly injected."""
import copy
import hashlib
import json
import tempfile
import subprocess
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock
ROOT=Path(__file__).resolve().parents[1]
def load(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/(name+'.py'))
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);return module
C=load('q03_window'); G=sys.modules.get('q03_git_toolchain') or load('q03_git_toolchain')
P=sys.modules.get('q03_window_preflight') or load('q03_window_preflight')
class IdentityContracts(unittest.TestCase):
    def setUp(self):
        binding=mock.patch.dict(sys.modules,{'q03_git_toolchain':G,'q03_window_preflight':P})
        binding.start();self.addCleanup(binding.stop)
        self.selection={'source_head':'154159dc3e38ca8b2321c01a153a1b2ccaf8663b','source_tree':'a'*40}
        self.test={'path':'/synthetic/tests-new','head':'e'*40,'tree':'f'*40,'fixture_sha256':'d'*64,'git_config_sha256':'b'*64}
        self.test['artifact']={'checkout':'/synthetic/artifact','publication':{'url':'https://github.com/imDaniel/spark-ds41f.git','ref':'refs/heads/test','sha':'a'*40},'source_path':'review/source.json','source_sha256':'c'*64,'git_config_sha256':'b'*64}
        self.manifest={'candidate':{'tests_path':'/synthetic/runtime197','g1_tests':self.test},'jobs':[{'kind':'p0'},{'kind':'g1'}]}
        self.responses={'HEAD':'e'*40,'HEAD^{tree}':'f'*40,self.selection['source_head']+'^{tree}':self.selection['source_tree'],'status':'','diff':C.G1_TEST_FILE}
        self.calls=[];self.bad_hash=None
        def run(argv):
            self.calls.append(argv)
            self.assertEqual(argv[:7],['git','--no-replace-objects','-c','core.fsmonitor=false','-c','core.hooksPath=/dev/null','-C'])
            args=argv[8:]
            if argv[7]=='/synthetic/artifact':
                return SimpleNamespace(stdout=({'remote':self.test['artifact']['publication']['url'],'rev-parse':'a'*40,'status':'','show':'published'})[args[0]])
            return SimpleNamespace(stdout=self.responses[args[1] if args[0]=='rev-parse' else args[0]])
        def digest(path):
            path=str(path)
            if self.bad_hash and path.endswith(self.bad_hash):return '0'*64
            if path.startswith('/synthetic/artifact/'):return hashlib.sha256(b'published').hexdigest()
            if path.endswith(C.G1_TEST_FILE):return 'd'*64
            return 'c'*64
        self.ops=SimpleNamespace(run=run,sha256=digest)
        for module,name in ((G,'validate_git_metadata'),(P,'verify_build_publication'),(C,'g1_artifact_evidence'),(C,'validate_g1_ast')):
            patch=mock.patch.object(module,name);obj=patch.start();self.addCleanup(patch.stop);setattr(self,name,obj)
        self.metadata=self.validate_git_metadata
        self.g1_artifact_evidence.return_value=(Path('/synthetic/artifact/review/source.json'),Path('/synthetic/artifact/review/g1.json'),b'',{})
        patch=mock.patch.object(Path,'read_text',return_value='synthetic AST tested separately');patch.start();self.addCleanup(patch.stop)
        for name in ('run','Popen','check_call','check_output'):
            patch=mock.patch.object(subprocess,name,side_effect=AssertionError('external process forbidden'));patch.start();self.addCleanup(patch.stop)
    def validate(self):C.RuntimeOps.validate_g1_test_source(self.ops,self.manifest,self.selection)
    def test_exact_identity_accepts_without_relabeling_runtime(self):
        old=copy.deepcopy(self.manifest);self.validate();self.assertEqual(self.manifest,old)
        self.metadata.assert_any_call(self.test['path'],self.test['git_config_sha256'])
        self.assertEqual(self.metadata.call_count,2)
        self.assertEqual(len(self.calls),10)
    def test_wrong_pin_extra_field_and_missing_g1_fail_before_git(self):
        for key,value in [('head','x'*40),('tree','x'*40),('fixture_sha256','x'*64),('argv',['bad'])]:
            original=copy.deepcopy(self.test);self.test[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):self.validate()
            self.test.clear();self.test.update(original)
        self.manifest['jobs']=[{'kind':'p0'}]
        with self.assertRaises(ValueError):self.validate()
        self.assertEqual(self.calls,[])
    def test_wrong_tip_dirty_missing_base_and_native_delta_refuse(self):
        for key,value in [('HEAD','wrong'),('HEAD^{tree}','wrong'),('status',' M fixture'),(self.selection['source_head']+'^{tree}','wrong'),('diff',C.G1_TEST_FILE+'\nvllm/runtime.py')]:
            old=self.responses[key];self.responses[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):self.validate()
            self.responses[key]=old
    def test_inventory_and_working_file_drift_refuse(self):
        for suffix in (C.G1_TEST_FILE,):
            self.bad_hash=suffix
            with self.subTest(suffix=suffix),self.assertRaises(ValueError):self.validate()
    def test_preserved_root_conftest_and_baseline_drift_refuse(self):
        old=self.ops.sha256
        for name in C.G1_PRESERVED_FILES:
            self.ops.sha256=lambda path,name=name: '0'*64 if str(path)==self.test['path']+'/'+name else old(path)
            with self.subTest(name=name),self.assertRaises(ValueError):self.validate()
        self.ops.sha256=old
    def test_only_g1_mount_selects_new_tests_without_runtime_overlay(self):
        candidate=self.manifest['candidate']
        candidate.update(image='sha256:'+'a'*64,component_head=self.selection['source_head'],
            cache_path='/synthetic/cache',output_path='/synthetic/output',runtime_manifest_path='/synthetic/runtime.json',
            git_toolchain_path='/synthetic/git',git_toolchain_manifest_sha256='a'*64,
            git_config_sha256='b'*64,git_toolchain_base_path='/usr/bin:/bin',
            tblib_path='/synthetic/tblib',tblib_manifest_sha256='c'*64,pytest_base_pythonpath='/opt/native')
        for kind in ('p0','g1','g2'):
            _,argv=C.job_command(self.manifest,kind,'0'*32)
            mounts=[argv[i+1] for i,value in enumerate(argv[:-1]) if value=='--mount']
            selected=self.test['path'] if kind=='g1' else candidate['tests_path']
            suffix='/tests' if kind!='p0' else ''
            self.assertIn('type=bind,src='+selected+suffix+',dst=/validation'+suffix+',readonly',mounts)
            self.assertFalse(any('/vllm' in value for value in mounts))
            self.assertFalse(any(value.startswith('PYTHONPATH=') for value in argv))
            if kind=='p0':self.assertEqual(argv[argv.index('--expected-head')+1],self.selection['source_head'])

    def test_legacy_identity_is_unchanged_without_override(self):
        del self.manifest['candidate']['g1_tests'];self.validate()
        self.assertEqual(self.calls,[]);self.metadata.assert_not_called()
        self.assertEqual(C.g1_tests_path(self.manifest['candidate']),'/synthetic/runtime197')

class ArtifactContracts(unittest.TestCase):
    """Actual metadata and AST validators; no metadata-validator substitutions."""
    def setUp(self):
        temp=tempfile.TemporaryDirectory(dir=ROOT);self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve()
        base=ROOT/'experiments/2026-10-21-mhc-input-validation'
        self.selection=json.loads((base/'validation-selection.json').read_text())
        self.expected=json.loads((base/self.selection['expected']['G1']['path']).read_text())
        self.expected['source_tree']='f'*40
        self.artifact={'checkout':str(self.root),'source_path':'source.json','source_sha256':''}
        self.test={'head':'e'*40,'tree':'f'*40,'fixture_sha256':'d'*64,'artifact':self.artifact}
        self.candidate={'g1_tests':self.test}
        self.source={'vllm':{'patch_head':'e'*40,'expected_tree':'f'*40},'q03_g1_test':{'fixture_path':C.G1_TEST_FILE,'fixture_sha256':'d'*64,'runtime_head':self.selection['source_head'],'runtime_tree':self.selection['source_tree'],'count':20,'expected_path':'g1.json','expected_sha256':''}}
        self.write()
    def write(self):
        raw=json.dumps(self.expected).encode();(self.root/'g1.json').write_bytes(raw)
        self.source['q03_g1_test']['expected_sha256']=hashlib.sha256(raw).hexdigest()
        raw=json.dumps(self.source).encode();(self.root/'source.json').write_bytes(raw)
        self.artifact['source_sha256']=hashlib.sha256(raw).hexdigest()
    def validate(self):return C.g1_artifact_evidence(self.candidate,self.selection)
    def test_exact_twenty_ids_and_separate_runtime_identity(self):
        self.assertEqual(len(json.loads(self.validate()[2])['nodeids']),20)
        self.assertNotEqual(self.test['head'],self.selection['source_head'])
    def test_rehashed_wrong_runtime_count_or_fixture_refuses(self):
        for key,value in [('runtime_head','a'*40),('runtime_tree','b'*40),('count',True),('count',19),('fixture_path','vllm/runtime.py')]:
            old=self.source['q03_g1_test'][key];self.source['q03_g1_test'][key]=value;self.write()
            with self.subTest(key=key),self.assertRaises(ValueError):self.validate()
            self.source['q03_g1_test'][key]=old
    def test_rehashed_equal_count_wrong_nodeid_refuses(self):
        self.expected['nodeids'][0]='test_unrelated';self.write()
        with self.assertRaises(ValueError):self.validate()
    def test_source_and_inventory_byte_tamper_refuse(self):
        for filename in ('source.json','g1.json'):
            path=self.root/filename;old=path.read_bytes();path.write_bytes(old+b' ')
            with self.subTest(filename=filename),self.assertRaises(ValueError):self.validate()
            path.write_bytes(old)
    def test_parent_escape_absolute_path_and_symlink_refuse(self):
        for value in ('../source.json','/tmp/source.json'):
            self.artifact['source_path']=value
            with self.assertRaises(ValueError):self.validate()
        self.artifact['source_path']='source.json'
        path=self.root/'source.json';path.rename(self.root/'alias-target');path.symlink_to('alias-target')
        with self.assertRaises(ValueError):self.validate()
    def test_ast_preserves_numeric_bodies_scope_and_identity_assertions(self):
        original='@fixture(scope="module")\ndef three_way_kernels():\n    assert baseline is not off\n    return old_compile()\ndef test_numeric():\n    assert actual == expected\n'
        corrected=original.replace('old_compile()', 'independent_compile()')
        C.validate_g1_ast(original,corrected)
        for changed in (corrected.replace('actual == expected','True'),corrected.replace('module','function'),corrected.replace('    assert baseline is not off\n',''),corrected+'\ndef test_extra(): pass\n',corrected+'\ndef test_numeric(): pass\n'):
            with self.subTest(source=changed),self.assertRaises(ValueError):C.validate_g1_ast(original,changed)

if __name__=='__main__':unittest.main(verbosity=2)
