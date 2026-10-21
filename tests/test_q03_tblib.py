"""CPU package/bootstrap contracts; no candidate pytest/native execution."""
import base64
import csv
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
P = load('q03_prepare_tblib')
C = load('q03_window')
sha = lambda raw: hashlib.sha256(raw).hexdigest()

def wheel(extra=None):
    payload = {name:b'inert test fixture\n' for name in P.MEMBERS}
    payload[P.DIST+'METADATA'] = b'Name: tblib\nVersion: 3.1.0\nRequires-Python: >=3.9\nLicense: BSD-2-Clause\n'
    payload[P.DIST+'WHEEL'] = b'Root-Is-Purelib: true\nTag: py3-none-any\n'
    if extra: payload.update(extra)
    out = io.StringIO(); writer = csv.writer(out)
    for name, data in payload.items():
        writer.writerow([name, '', ''] if name.endswith('/RECORD') else [name,
            'sha256='+base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode(),len(data)])
    payload[P.DIST+'RECORD'] = out.getvalue().encode()
    raw = io.BytesIO()
    with zipfile.ZipFile(raw,'w') as z:
        for name,data in payload.items(): z.writestr(name,data)
    return raw.getvalue()

class PreparationContracts(unittest.TestCase):
    def setUp(self):
        # Repository ancestry is owner-controlled; /tmp is intentionally shared.
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        self.raw=wheel()
        self.patch=mock.patch.object(P,'WHEEL_SHA256',sha(self.raw)); self.patch.start(); self.addCleanup(self.patch.stop)
        envpatch=mock.patch.dict(os.environ,{'PYTHONPATH':'/opt/native','Q03_EXPECTED_PYTHONPATH':'/opt/native'});envpatch.start();self.addCleanup(envpatch.stop)
        self.source=self.root/P.WHEEL; self.source.write_bytes(self.raw)
        self.authority=self.root/'authority.json'
        self.authority.write_text(json.dumps({'independent_root':str(self.root),'protected_roots':['/synthetic/protected']}))
        self.digest=sha(self.authority.read_bytes()); self.output=self.root/'bundle'
    def prepare(self,apply=False):
        return P.prepare(self.source,self.output,self.authority,self.digest,apply)
    def test_plan_no_write_apply_exact_bytes_and_exclusive_destination(self):
        self.assertFalse(self.prepare()['applied']); self.assertFalse(self.output.exists())
        result=self.prepare(True)
        self.assertEqual(set(p.name for p in self.output.iterdir()),{P.WHEEL,'manifest.json'})
        self.assertEqual((self.output/P.WHEEL).read_bytes(),self.raw)
        self.assertEqual(sha((self.output/'manifest.json').read_bytes()),result['manifest_sha256'])
        self.assertEqual((self.output/P.WHEEL).stat().st_mode & 0o777,0o444)
        with self.assertRaises(ValueError): self.prepare(True)
        self.output.chmod(0o700)
    def test_missing_or_tampered_wheel_refuses_before_output(self):
        self.source.write_bytes(self.raw+b'drift')
        with self.assertRaises(ValueError): self.prepare(True)
        self.source.unlink()
        with self.assertRaises(FileNotFoundError): self.prepare(True)
        self.assertFalse(self.output.exists())
    def test_rehashed_extra_member_and_dependency_rejected(self):
        for extra in ({'sitecustomize.py':b'bad'}, {P.DIST+'METADATA':b'Name: tblib\nVersion: 3.1.0\nRequires-Python: >=3.9\nLicense: BSD-2-Clause\nRequires-Dist: unexpected\n'}):
            raw=wheel(extra)
            with self.subTest(extra=list(extra)),mock.patch.object(P,'WHEEL_SHA256',sha(raw)),self.assertRaises(ValueError):
                P.validate_wheel(raw)
    def test_authority_hash_escape_overlap_and_symlink_refuse(self):
        with self.assertRaises(ValueError): P.validate_destination(self.output,self.authority,'0'*64)
        with self.assertRaises(ValueError): P.validate_destination(self.root.parent/'escape',self.authority,self.digest)
        alias=self.root/'alias'; alias.symlink_to(self.source)
        with self.assertRaises(ValueError): P.read_owned(alias)
        self.authority.write_text(json.dumps({'independent_root':str(self.root),'protected_roots':[str(self.root)]}))
        with self.assertRaises(ValueError): P.validate_destination(self.output,self.authority,sha(self.authority.read_bytes()))
    def test_shared_writable_input_rejected(self):
        self.source.chmod(0o666)
        with self.assertRaises(ValueError): self.prepare(True)
        self.assertFalse(self.output.exists())

class RuntimeContracts(unittest.TestCase):
    setUp = PreparationContracts.setUp
    prepare = PreparationContracts.prepare
    def stage(self):
        result=self.prepare(True)
        self.output.chmod(0o700)
        self.addCleanup(lambda: self.output.chmod(0o700) if self.output.exists() else None)
        return {'tblib_path':str(self.output),'tblib_manifest_sha256':result['manifest_sha256']}
    def test_runtime_validator_matches_prepared_bundle_and_rejects_drift(self):
        candidate=self.stage()
        with mock.patch.object(C,'TBLIB_SHA256',sha(self.raw)):
            self.assertEqual(C.validate_tblib_dependency(candidate)['members'],P.validate_wheel(self.raw))
            with self.assertRaises(ValueError): C.validate_tblib_dependency(dict(candidate,tblib_manifest_sha256='0'*64))
            extra=self.output/'extra';extra.write_text('x')
            with self.assertRaises(ValueError): C.validate_tblib_dependency(candidate)
            extra.unlink(); target=self.output/P.WHEEL;target.chmod(0o644);target.write_bytes(b'wrong')
            with self.assertRaises(ValueError): C.validate_tblib_dependency(candidate)
            target.unlink()
            with self.assertRaises(ValueError): C.validate_tblib_dependency(candidate)
    def test_g1_g2_mount_and_pytest_arguments_preserve_conftests(self):
        candidate={'image':'sha256:'+'a'*64,'component_head':'b'*40,
            'tests_path':'/independent/tests197','cache_path':'/independent/cache',
            'output_path':'/independent/output','runtime_manifest_path':'/independent/runtime.json',
            'tblib_path':'/independent/tblib','tblib_manifest_sha256':'c'*64,
            'pytest_base_pythonpath':'/opt/native'}
        for kind,filename in [('g1','test_deepseek_v41_mhc_three_way.py'),('g2','test_deepseek_v41_q03_owner.py')]:
            _,argv=C.job_command({'candidate':candidate},kind,'0'*32)
            self.assertIn('type=bind,src=/independent/tblib,dst=/pytest-dependency,readonly',argv)
            self.assertIn('type=bind,src=/independent/tests197/tests,dst=/validation/tests,readonly',argv)
            self.assertEqual(argv[argv.index('-c')+2:],['-p','no:cacheprovider','-q','tests/kernels/'+filename,'--junitxml=/results/'+kind+'.xml'])
            self.assertFalse(any(a.startswith('PYTHONPATH=') or '--confcutdir' in a or '--noconftest' in a for a in argv))
    def test_p0_does_not_require_or_mount_dependency(self):
        candidate={'image':'sha256:'+'a'*64,'component_head':'b'*40,
            'tests_path':'/independent/tests197','cache_path':'/independent/cache',
            'output_path':'/independent/output','runtime_manifest_path':'/independent/runtime.json',
            'git_toolchain_base_path':'/usr/bin:/bin','git_toolchain_manifest_sha256':'c'*64,
            'git_config_sha256':'d'*64,'git_toolchain_path':'/independent/git'}
        _,argv=C.job_command({'candidate':candidate},'p0','0'*32)
        self.assertFalse(any('tblib' in a or 'pytest-dependency' in a for a in argv))
        self.assertIn('tests/standalone_tests/deepseek_v41_q03_registration.py',argv)
        self.assertFalse(C.needs_tblib({'jobs':[{'kind':'p0'}]}))
        self.assertTrue(C.needs_tblib({'jobs':[{'kind':'p0'},{'kind':'g1'}]}))
    def test_dependency_overlap_with_writable_paths_refuses(self):
        import test_q03_window as fixtures
        fixture=fixtures.WindowRecoveryTests(); fixture.setUpClass(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        manifest=fixture.manifest; candidate=manifest['candidate']
        candidate.update(git_toolchain_path=str(fixture.root/'git'),
            git_toolchain_base_path='/usr/bin:/bin',git_toolchain_manifest_sha256='a'*64,
            git_config_sha256='b'*64,tblib_path=str(fixture.root/'tblib'),tblib_manifest_sha256='c'*64)
        C.validate_layout(manifest,fixture.directory)
        for key in ('cache_path','output_path'):
            original=candidate[key];candidate[key]=candidate['tblib_path']
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'immutable'):
                C.validate_layout(manifest,fixture.directory)
            candidate[key]=original
        with self.assertRaisesRegex(ValueError,'immutable'):
            C.validate_layout(manifest,Path(candidate['tblib_path'])/'session')

    def test_image_pythonpath_must_be_single_exact_value(self):
        from types import SimpleNamespace
        helper=sys.modules.get('q03_git_toolchain') or load('q03_git_toolchain')
        candidate={'image':'sha256:'+'a'*64,'git_toolchain_base_path':'/usr/bin',
                   'pytest_base_pythonpath':'/opt/native'}
        ops=SimpleNamespace(run=mock.Mock())
        for values,valid in [(['PYTHONPATH=/opt/native'],True),([],False),
                (['PYTHONPATH=/wrong'],False),(['PYTHONPATH=/opt/native']*2,False)]:
            ops.run.return_value=SimpleNamespace(stdout=json.dumps([{'Id':candidate['image'],
                'Config':{'Env':['PATH=/usr/bin']+values}}]))
            with mock.patch.object(helper,'validate_toolchain'):
                if valid: C.RuntimeOps.validate_git_toolchain(ops,{'candidate':candidate,'jobs':[{'kind':'g1'}]})
                else:
                    with self.assertRaisesRegex(ValueError,'PYTHONPATH'):
                        C.RuntimeOps.validate_git_toolchain(ops,{'candidate':candidate,'jobs':[{'kind':'g1'}]})
    def test_bootstrap_refuses_missing_or_changed_pythonpath_before_wheel_read(self):
        for values in ({}, {'PYTHONPATH':'/wrong','Q03_EXPECTED_PYTHONPATH':'/opt/native'}):
            with mock.patch.dict(os.environ,values,clear=True),mock.patch.object(Path,'read_bytes') as read,self.assertRaises((KeyError,RuntimeError)):
                exec(compile(C.TBLIB_BOOTSTRAP,'<actual-bootstrap>','exec'),{})
            read.assert_not_called()

    def test_bootstrap_preserves_pytest_exit_and_arguments(self):
        from types import SimpleNamespace
        wheelpath='/pytest-dependency/'+C.TBLIB_WHEEL
        args=['-p','no:cacheprovider','-q','tests/kernels/selected.py','--junitxml=/results/g1.xml']
        for code in (0,1,2,4,5):
            fake=SimpleNamespace(main=mock.Mock(return_value=code))
            tblib=SimpleNamespace(__version__='3.1.0',__file__=wheelpath+'/tblib/__init__.py')
            # Only pytest is simulated; candidate test modules are never imported.
            script=C.TBLIB_BOOTSTRAP.replace(C.TBLIB_SHA256,sha(self.raw))
            with mock.patch.object(Path,'read_bytes',return_value=self.raw),mock.patch.dict(sys.modules,{'tblib':tblib,'pytest':fake}),mock.patch.object(sys,'path',list(sys.path)),mock.patch.object(sys,'argv',['-c']+args),self.assertRaises(SystemExit) as result:
                exec(compile(script,'<actual-bootstrap>','exec'),{})
            self.assertEqual(result.exception.code,code);fake.main.assert_called_once_with(args)
    def test_bootstrap_wrong_hash_or_preloaded_origin_never_calls_pytest(self):
        from types import SimpleNamespace
        fake=SimpleNamespace(main=mock.Mock(side_effect=AssertionError('pytest must not run')))
        for data,origin in ((b'tampered','/pytest-dependency/'+C.TBLIB_WHEEL+'/tblib/__init__.py'),(self.raw,'/untrusted/tblib.py')):
            script=C.TBLIB_BOOTSTRAP.replace(C.TBLIB_SHA256,sha(self.raw))
            with mock.patch.object(Path,'read_bytes',return_value=data),mock.patch.dict(sys.modules,{'tblib':SimpleNamespace(__version__='3.1.0',__file__=origin),'pytest':fake}),mock.patch.object(sys,'path',list(sys.path)),self.assertRaises(RuntimeError):
                exec(compile(script,'<actual-bootstrap>','exec'),{})
        fake.main.assert_not_called()

if __name__=='__main__': unittest.main()
