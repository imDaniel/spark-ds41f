"""Focused real baseline validators; static CPU/file checks only, no model/GPU."""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import platform
import py_compile
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import q03_baseline_identity as B
import q03_baseline_worker as W


def runtime(arm='r6x188'):
    root = '/opt/image/vllm/vllm'
    return dict(schema_version=1, **B.checked_identity(arm),
        installation={'mode': 'image-source', 'package_root': root},
        platform={'python': '3.12.3', 'machine': 'aarch64', 'system': 'Linux'},
        metadata_versions={'torch': '2.13.0', 'tilelang': '0.1.15', 'tile_kernels': '2.0.0'},
        package_origins={name: '/opt/site/' + name + '/__init__.py' for name in ('torch', 'tilelang', 'tile_kernels')},
        package_sha256={name: str(index) * 64 for index, name in enumerate(('torch', 'tilelang', 'tile_kernels'), 3)},
        torch_version='2.13.0+cu130', torch_version_sha256='a' * 64,
        native_paths={name: root + '/' + name.split('.')[-1] + '.abi3.so' for name in B.NATIVE_NAMES},
        native_sha256={name: str(index) * 64 for index, name in enumerate(B.NATIVE_NAMES, 1)},
        source_sha256=copy.deepcopy(B.SOURCE_SHA256[arm]), pythonpath='/opt/image/vllm:/opt/image/tile_kernels')


def image(arm='r6x188'):
    labels = {}
    for index, name in enumerate(B.COMPONENTS, 1):
        labels['local.spark3.' + name + '.tree'] = str(index) * 40
        labels['local.spark3.' + name + '.patch-head'] = str(index) * 40
    identity = B.checked_identity(arm)
    labels.update({'local.spark3.vllm.tree': identity['source_tree'],
                   'local.spark3.vllm.patch-head': identity['source_head']})
    return dict(Id=identity['image'], Architecture='arm64', Os='linux',
                Config={'Labels': labels, 'Env': ['PYTHONPATH=' + runtime(arm)['pythonpath']]},
                RootFS={'Type': 'layers', 'Layers': ['sha256:' + 'a' * 64]})


def checked(arm='r6x188'):
    return dict(identity=B.checked_identity(arm), runtime=runtime(arm),
                provenance={'path': '/fixture/runtime.json', 'sha256': 'b' * 64})


def plan_and_ready(arm='r6x188', steering='loaded4'):
    value = checked(arm)
    plan = dict(window_id='a' * 32, attempt_id='b' * 32, spec_sha256='c' * 64,
                selection={'arm': arm, 'steering': steering}, spec={
                    'baseline_identity': value, 'expected_effective_config': {'frozen': True}})
    reports = []
    r = runtime(arm)
    for rank in range(4):
        reports.append(dict(worker_rank=rank, tp_rank=rank, config_rank=rank, node_rank=rank,
            worker_pid=101, worker_starttime_ticks=123456 + rank,
            node_rank_within_dp=rank, local_rank=0, identity=B.checked_identity(arm),
            runtime_sha256='b' * 64, window_id=plan['window_id'], attempt_id=plan['attempt_id'],
            spec_sha256=plan['spec_sha256'], source_sha256=copy.deepcopy(r['source_sha256']),
            native_sha256=copy.deepcopy(r['native_sha256']), native_paths=copy.deepcopy(r['native_paths']),
            package_root=r['installation']['package_root'], effective_config={'frozen': True},
            runner_config={'frozen': True}, sm=[12, 1], q03_switch='0', q03_owner_present=False,
            sequence_parallel=False, actual_versions={'python': '3.12.3', 'torch': '2.13.0+cu130'},
            metadata_versions=copy.deepcopy(r['metadata_versions']), package_origins=copy.deepcopy(r['package_origins']),
            package_sha256=copy.deepcopy(r['package_sha256']), torch_version_sha256=r['torch_version_sha256'],
            steering_profile=None if steering == 'unloaded' else {
                'name': B.PROFILE_NAME, 'artifact_sha256': B.PROFILE_SHA256, 'identity': 'profile-identity'}))
    ready = dict(schema_version=1, window_id=plan['window_id'], attempt_id=plan['attempt_id'],
                 spec_sha256=plan['spec_sha256'], reports=reports,
                 reports_sha256=B.digest(B.canonical(reports)))
    return plan, ready


class TypedIdentityTests(unittest.TestCase):
    def test_only_two_arms_six_states_without_argv_image_or_dual(self):
        for arm in B.IDENTITIES:
            for state in B.STEERING:
                selection = {'arm': arm, 'steering': state}
                self.assertEqual(B.validate_selection(selection), selection)
                request = B.steering_request(selection)
                self.assertEqual(request, {} if state == 'unloaded' else {
                    'steering_profile': B.PROFILE_NAME, 'steering_scale': int(state[-1])})
        for value in ({'arm': 'native197-dual', 'steering': 'loaded1'},
                      {'arm': 'r6x188', 'steering': 'loaded5'},
                      {'arm': 'r6x188', 'steering': True},
                      {'arm': 'r6x188', 'steering': 'loaded0', 'argv': ['custom']},
                      {'arm': 'r6x188', 'steering': 'loaded0', 'image': B.IDENTITIES['r6x188']['image']}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                B.validate_selection(value)

    def test_both_actual_source_identities_validate_but_cross_arm_never_does(self):
        for arm in B.IDENTITIES:
            value = runtime(arm)
            self.assertEqual(B.validate_runtime(value, arm), value)
            other = 'native197-off' if arm == 'r6x188' else 'r6x188'
            with self.assertRaises(ValueError): B.validate_runtime(value, other)
            value['source_sha256'] = B.SOURCE_SHA256[other]
            with self.assertRaises(ValueError): B.validate_runtime(value, arm)
        self.assertNotEqual(B.SOURCE_SHA256['r6x188']['models/deepseek_v4_1/nvidia/model.py'],
                            B.SOURCE_SHA256['native197-off']['models/deepseek_v4_1/nvidia/model.py'])

    def test_provenance_faults(self):
        changes = [('image', 'sha256:' + 'e' * 64), ('source_head', 'f' * 40),
                   ('source_tree', 'd' * 40), ('schema_version', True), ('pythonpath', 'relative')]
        for key, value in changes:
            r = runtime(); r[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): B.validate_runtime(r, 'r6x188')
        for key in ('metadata_versions', 'package_origins', 'native_paths', 'native_sha256', 'source_sha256'):
            r = runtime(); r[key].pop(next(iter(r[key])))
            with self.subTest(key=key), self.assertRaises(ValueError): B.validate_runtime(r, 'r6x188')
        r = runtime(); r['native_paths'][B.NATIVE_NAMES[0]] = '/other/_C_stable_libtorch.abi3.so'
        with self.assertRaises(ValueError): B.validate_runtime(r, 'r6x188')

    def test_real_owned_file_binding_and_candidate_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'runtime.json'; raw = B.canonical(runtime()); path.write_bytes(raw); path.chmod(0o600)
            binding = {'schema_version': 1, 'arm': 'r6x188', 'provenance': {'path': str(path), 'sha256': B.digest(raw)}}
            candidate = dict(image=B.IDENTITIES['r6x188']['image'], component_head=B.IDENTITIES['r6x188']['source_head'],
                runtime_manifest_path=str(path), runtime_manifest_sha256=B.digest(raw),
                pytest_base_pythonpath=runtime()['pythonpath'])
            result = B.validate_binding(binding, candidate)
            self.assertEqual(result['runtime'], runtime())
            candidate['component_head'] = B.IDENTITIES['native197-off']['source_head']
            with self.assertRaises(ValueError): B.validate_binding(binding, candidate)
            path.write_bytes(raw + b'\n')
            with self.assertRaises(ValueError): B.validate_binding(binding)

    def test_image_inspect_closure_and_fixed_host_command(self):
        for arm in B.IDENTITIES:
            observed = B.validate_image(arm, image(arm), runtime(arm))
            self.assertEqual(observed['identity'], B.checked_identity(arm))
        for mutation in (lambda i: i.update(Id=B.IDENTITIES['native197-off']['image']),
                         lambda i: i['Config']['Labels'].pop('local.spark3.tile_kernels.tree'),
                         lambda i: i['Config'].update(Env=['PYTHONPATH=/different']),
                         lambda i: i['RootFS'].update(Layers=[])):
            i = image(); mutation(i)
            with self.assertRaises(ValueError): B.validate_image('r6x188', i, runtime())

    def test_capture_plans_are_finite_cpu_only_inert_and_arm_specific(self):
        for arm in B.IDENTITIES:
            p = B.static_capture_plan(arm, 'a' * 32)
            argv = p['argv']
            self.assertIn(B.IDENTITIES[arm]['image'], argv)
            for flag in ('--runtime=runc', '--network=none', '--read-only', '--cap-drop=ALL',
                         '--memory=1g', '--memory-swap=1g', '--cpus=1'):
                self.assertIn(flag, argv)
            self.assertFalse(any(flag.startswith(('--gpus', '--mount', '--device')) for flag in argv))
            self.assertEqual(argv[-5:-1], ['-I', '-S', '-B', '-c'])
            tree = ast.parse(argv[-1])
            imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
            imports += [alias.name for n in ast.walk(tree) if isinstance(n, ast.Import) for alias in n.names]
            self.assertNotIn('torch', imports); self.assertNotIn('vllm', imports)
        with self.assertRaises(ValueError): B.static_capture_plan('custom', 'a' * 32)
        with self.assertRaises(ValueError): B.static_capture_plan('r6x188', '../../x')

    def test_marked_capture_not_duplicate_or_wrong_arm(self):
        raw = 'log text\nQ03_BASELINE_STATIC=' + json.dumps(runtime())
        self.assertEqual(B.validate_static_capture_output(raw, 'r6x188'), runtime())
        for wrong in (raw + '\n' + raw, 'unmarked', raw.replace('"schema_version": 1', '"schema_version": true')):
            with self.assertRaises(ValueError): B.validate_static_capture_output(wrong, 'r6x188')
        with self.assertRaises(ValueError): B.validate_static_capture_output(raw, 'native197-off')


class ReadyTests(unittest.TestCase):
    def test_each_arm_state_all_four_real_validator(self):
        for arm in B.IDENTITIES:
            for state in B.STEERING:
                plan, ready = plan_and_ready(arm, state)
                self.assertEqual(B.validate_ready(B.canonical(ready), plan), ready)

    def test_fully_rehashed_mutated_readiness_rejected(self):
        mutations = [lambda r: r[0].update(worker_rank=1),
                     lambda r: r[0].update(identity=B.checked_identity('native197-off')),
                     lambda r: r[0].update(q03_switch='1'),
                     lambda r: r[0].update(q03_owner_present=True),
                     lambda r: r[0].update(sequence_parallel=True),
                     lambda r: r[0].update(runtime_sha256='e' * 64),
                     lambda r: r[0].update(spec_sha256='f' * 64),
                     lambda r: r[0]['actual_versions'].update(torch='2.99'),
                     lambda r: r[0]['metadata_versions'].update(tilelang='9.9'),
                     lambda r: r[0]['package_origins'].update(torch='/other/torch/__init__.py'),
                     lambda r: r[0]['native_sha256'].update({B.NATIVE_NAMES[0]: 'd' * 64}),
                     lambda r: r[0]['source_sha256'].update({'__init__.py': 'e' * 64}),
                     lambda r: r[0].update(effective_config={'learned': True}),
                     lambda r: r[0].update(steering_profile=None),
                     lambda r: r.pop()]
        for index, change in enumerate(mutations):
            plan, ready = plan_and_ready(); change(ready['reports'])
            ready['reports_sha256'] = B.digest(B.canonical(ready['reports']))
            with self.subTest(index=index), self.assertRaises(ValueError): B.validate_ready(B.canonical(ready), plan)

    def test_unloaded_not_loaded_zero(self):
        plan, ready = plan_and_ready(steering='unloaded')
        ready['reports'][0]['steering_profile'] = {'name': B.PROFILE_NAME, 'artifact_sha256': B.PROFILE_SHA256}
        ready['reports_sha256'] = B.digest(B.canonical(ready['reports']))
        with self.assertRaises(ValueError): B.validate_ready(B.canonical(ready), plan)


class StaticProducerTests(unittest.TestCase):
    def test_isolated_worker_bootstrap_has_only_two_source_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ('q03_baseline_identity.py', 'q03_baseline_worker.py'):
                (root / name).write_bytes((ROOT / 'scripts' / name).read_bytes())
            identity_path, worker_path = root / 'q03_baseline_identity.py', root / 'q03_baseline_worker.py'
            program = '''
import hashlib, importlib.metadata, os, pathlib, sys, types
root=pathlib.Path(sys.argv[1]); path=root/'q03_baseline_worker.py'
real_path=pathlib.Path
def local_path(p):
 return root/'q03_baseline_identity.py' if str(p)=='/q03-http/q03_baseline_identity.py' else real_path(p)
pathlib.Path=local_path
raw=path.read_bytes(); scope={'__name__':'__mp_main__','__file__':str(path)}
exec(compile(raw,str(path),'exec'),scope)
assert 'q03_held_window' not in sys.modules
assert 'q03_baseline_worker' in sys.modules
assert not any(n=='torch' or n=='vllm' or n.startswith(('torch.','vllm.')) for n in sys.modules)
print('source-only-bootstrap-ok')
'''
            env = dict(os.environ, Q03_HTTP_WORKER_SHA256=B.digest(worker_path.read_bytes()),
                       Q03_BASELINE_IDENTITY_SHA256=B.digest(identity_path.read_bytes()),
                       VLLM_ALLOW_INSECURE_SERIALIZATION='0',
                       PYTHONPYCACHEPREFIX='/cache/baseline-bytecode')
            result = subprocess.run([sys.executable, '-I', '-S', '-B', '-X', 'pycache_prefix=/cache/baseline-bytecode', '-c', program, str(root)],
                                    env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('source-only-bootstrap-ok', result.stdout)
            env['Q03_BASELINE_IDENTITY_SHA256'] = '0' * 64
            rejected = subprocess.run([sys.executable, '-I', '-S', '-B', '-X', 'pycache_prefix=/cache/baseline-bytecode', '-c', program, str(root)],
                                      env=env, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn('identity source changed', rejected.stderr)

    def test_real_poisoned_pyc_is_ignored_only_with_checked_fresh_prefix(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'baseline_poison_probe.py'
            source.write_text("VALUE = 'EVIL'\n")
            os.utime(source, (1700000000, 1700000000))
            py_compile.compile(str(source), doraise=True, invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
            # Same mtime and length means normal -B still executes stale code.
            source.write_text("VALUE = 'GOOD'\n")
            os.utime(source, (1700000000, 1700000000))
            base = 'import sys;sys.path.insert(0,sys.argv[1]);import baseline_poison_probe;print(baseline_poison_probe.VALUE)'
            env = dict(os.environ); env.pop('PYTHONPYCACHEPREFIX', None)
            vulnerable = subprocess.run([sys.executable, '-B', '-c', base, str(root)],
                                        env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(vulnerable.returncode, 0, vulnerable.stderr)
            self.assertEqual(vulnerable.stdout.strip(), 'EVIL')
            guarded = '''
import pathlib,sys
p=pathlib.Path(sys.argv[2]);scope={'__name__':'guard_test','__file__':str(p)}
exec(compile(p.read_bytes(),str(p),'exec'),scope)
scope['verify_bytecode_policy']()
''' + base
            worker = str(ROOT / 'scripts/q03_baseline_worker.py')
            safe_env = dict(env, PYTHONPYCACHEPREFIX='/cache/baseline-bytecode')
            safe = subprocess.run([sys.executable, '-B', '-c', guarded, str(root), worker],
                                  env=safe_env, capture_output=True, text=True, timeout=30)
            self.assertEqual(safe.returncode, 0, safe.stderr)
            self.assertEqual(safe.stdout.strip(), 'GOOD')
            rejected = subprocess.run([sys.executable, '-B', '-c', guarded, str(root), worker],
                                      env=env, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn('fresh isolated bytecode prefix', rejected.stderr)

    def test_actual_body_under_isolated_no_site_python_never_imports_packages(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); package = root / 'vllm'; package.mkdir()
            # Any accidental package import would fail this real subprocess.
            source = b'raise RuntimeError("MODEL IMPORT FORBIDDEN")\n'
            (package / '__init__.py').write_bytes(source)
            for name in B.NATIVE_NAMES:
                (package / (name.split('.')[-1] + '.abi3.so')).write_bytes(b'\x7fELF-not-executable-fixture')
            for name in ('torch', 'tilelang', 'tile_kernels'):
                directory = root / name; directory.mkdir(); (directory / '__init__.py').write_bytes(source)
                metadata = root / (name + '-1.0.dist-info'); metadata.mkdir()
                (metadata / 'METADATA').write_text('Metadata-Version: 2.1\nName: ' + name + '\nVersion: 1.0\n')
            (root / 'torch/version.py').write_text("__version__: str = '1.0+fixture'\n")
            params = dict(identity=B.checked_identity('r6x188'),
                          source_sha256={'__init__.py': B.digest(source)}, native_names=B.NATIVE_NAMES)
            # Isolate distro lookup from this test host's installed ML packages;
            # the production body and PathFinder/metadata implementations are real.
            prefix = 'import sysconfig\nsysconfig.get_path=lambda name:' + repr(str(root)) + '\nP=' + repr(params) + '\n'
            result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', prefix + B.STATIC_CAPTURE],
                                    env=dict(os.environ, PYTHONPATH=str(root)), capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            capture = json.loads(result.stdout.removeprefix('Q03_BASELINE_STATIC='))
            self.assertEqual(capture['installation']['package_root'], str(package))
            self.assertEqual(capture['metadata_versions'], {n: '1.0' for n in ('torch', 'tilelang', 'tile_kernels')})
            self.assertEqual(capture['torch_version'], '1.0+fixture')
            self.assertEqual(capture['source_sha256'], params['source_sha256'])
            self.assertEqual(capture['package_origins']['torch'], str(root / 'torch/__init__.py'))
            self.assertEqual(capture['torch_version_sha256'], B.digest((root / 'torch/version.py').read_bytes()))
            # Actual file mutation is caught by the same fixed producer.
            (package / '__init__.py').write_bytes(source + b'# drift\n')
            changed = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', prefix + B.STATIC_CAPTURE],
                                     env=dict(os.environ, PYTHONPATH=str(root)), capture_output=True, text=True, timeout=30)
            self.assertNotEqual(changed.returncode, 0)
            self.assertIn('actual source bytes differ', changed.stderr)

    def test_worker_import_is_inert_and_projection_matches_old_common_keys(self):
        import q03_owner_model as O
        config = SimpleNamespace()
        for section, fields in W.EFFECTIVE_FIELDS.items():
            setattr(config, section, SimpleNamespace(**{field: 1 for field in fields}))
        config.model_config.dtype = 'torch.bfloat16'
        config.model_config.multimodal_config = None
        config.compilation_config.pass_config = SimpleNamespace(enable_sp=False)
        config.speculative_config.draft_parallel_config = SimpleNamespace(tensor_parallel_size=4)
        config.additional_config = {}
        result = W.project_effective_config(config)
        self.assertEqual(set(result), set(O.EFFECTIVE_KEYS))
        self.assertEqual(result['additional_config.ds41_steering'], {'present': False, 'value': None})
        config.additional_config = {'ds41_steering': {'fixed': True}}
        self.assertEqual(W.project_effective_config(config)['additional_config.ds41_steering'],
                         {'present': True, 'value': {'fixed': True}})


if __name__ == '__main__':
    unittest.main()
