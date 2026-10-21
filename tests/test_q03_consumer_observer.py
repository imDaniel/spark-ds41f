"""Closed consumer lane CPU/protocol contracts, never native consumer-use proof."""
import copy
import hashlib
import json
import importlib.util
import marshal
import struct
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
import zipfile

from test_q03_owner_model import (
    OwnerBindingTests, OwnerPlanFixture, FakeOwnerOps,
)
import q03_consumer_observer as consumer
import q03_consumer_site as guard

ROOT = Path(__file__).resolve().parents[1]


class ConsumerAdmissionTests(OwnerBindingTests):
    # Reuse the actual original admission fixture, not a new mock validator.
    def select(self):
        binding = self.manifest['owner_model']
        binding['test_lane'] = consumer.LANE
        binding['tests'].update(head=consumer.COMMIT, tree=consumer.TREE,
                                fixture_sha256=consumer.TEST_SOURCES[
                                    consumer.TEST_FILE])
        self.manifest['jobs'][0]['selection']['mode'] = 'dual'
        self.responses.update(HEAD=consumer.COMMIT,
                              **{'HEAD^{tree}': consumer.TREE,
                                 'diff': '\n'.join(consumer.DELTA_FILES)})
        root = Path(binding['tests']['path'])
        self.ops.sha256 = lambda path: consumer.TEST_SOURCES[str(path.relative_to(root))]

    def test_separate_lane_requires_exact_composition_and_every_source_pin(self):
        self.select()
        self.assertEqual(self.bind()['test_lane'], consumer.LANE)
        for field, wrong in [('head', 'f' * 40), ('tree', self.owner.TEST_TREE),
                             ('fixture_sha256', self.owner.TEST_SHA)]:
            tests = self.manifest['owner_model']['tests']
            original = tests[field]
            tests[field] = wrong
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                self.bind()
            tests[field] = original
        original = self.ops.sha256
        for path in consumer.TEST_SOURCES:
            self.ops.sha256 = lambda p: '0' * 64 if str(p).endswith(path) else original(p)
            with self.subTest(path=path), self.assertRaises(RuntimeError):
                self.bind()
        self.ops.sha256 = original

    def test_consumer_lane_cannot_use_off_unknown_lane_or_runtime_delta(self):
        self.select()
        self.manifest['jobs'][0]['selection']['mode'] = 'off'
        with self.assertRaisesRegex(RuntimeError, 'dual'):
            self.bind()
        self.manifest['jobs'][0]['selection']['mode'] = 'dual'
        self.responses['diff'] += '\nvllm/unreviewed.py'
        with self.assertRaises(RuntimeError):
            self.bind()
        self.manifest['owner_model']['test_lane'] = 'unknown'
        with self.assertRaisesRegex(RuntimeError, 'unknown'):
            self.bind()


class ConsumerPlanTests(OwnerPlanFixture):
    def selected_plan(self, steering='off'):
        self.binding.update(test_lane=consumer.LANE, checkout=str(ROOT))
        return self.plan('dual', steering)

    def test_plan_preserves_common_spec_and_binds_every_new_rank(self):
        baseline = self.plan('dual', 'active')
        plan = self.selected_plan('active')
        for key in baseline['spec']:
            if key != 'engine_kwargs':
                self.assertEqual(plan['spec'][key], baseline['spec'][key])
        original = dict(baseline['spec']['engine_kwargs'],
                        worker_extension_cls=consumer.EXTENSION)
        self.assertEqual(plan['spec']['engine_kwargs'], original)
        self.assertEqual(plan['spec']['consumer_binding'], consumer.closure())
        self.assertEqual(plan['test_tree'], consumer.TREE)
        self.assertEqual(plan['spec_sha256'], self.owner.digest(
            self.owner.canonical(plan['spec'])))
        for rank in plan['ranks']:
            self.assertEqual(rank['python_argv'][:4], ['-I', '-B', '-S', '-c'])
            self.assertEqual(rank['python_argv'][4], consumer.BOOTSTRAP)
            self.assertEqual(rank['environment']['VLLM_WORKER_MULTIPROC_METHOD'], 'spawn')
            self.assertEqual(rank['environment']['Q03_CONSUMER_SPEC_SHA256'],
                             plan['spec_sha256'])
            self.assertEqual(rank['environment']['PYTHONPATH'], '/binding:/image/base')
            self.assertNotIn('VLLM_Q03_PRODUCTION_OWNER_TEST', rank['environment'])
            self.assertIn([str(ROOT / 'scripts' / consumer.SITE_FILE),
                           '/binding/sitecustomize.py', 'ro'], rank['mounts'])
            if rank['rank'] == 0:
                self.assertIn(consumer.TEST_FILE + '::' + consumer.TEST_CASE,
                              rank['python_argv'])
            else:
                argv = rank['python_argv']
                self.assertEqual(argv[argv.index('--worker-extension-cls') + 1],
                                 consumer.EXTENSION)
        self.assertNotEqual(plan['plan_sha256'], baseline['plan_sha256'])

    def test_new_lane_actual_initial_container_environment_and_mounts(self):
        plan = self.selected_plan()
        ops = FakeOwnerOps(self.owner, SimpleNamespace(), plan)
        for rank in range(4):
            row = {'rank': rank, 'container_id': str(rank + 1) * 64}
            item = ops.container(rank, row)
            # This existing fake emulates the old baked environment. Model the
            # explicit override Docker applies in this new lane before intake.
            item['Config']['Env'] = [entry for entry in item['Config']['Env']
                                     if not entry.startswith('PYTHONPATH=')]
            item['Config']['Env'].append('PYTHONPATH=' +
                                         plan['ranks'][rank]['environment']['PYTHONPATH'])
            self.owner.validate_container(item, plan, row, initial=True)
            item['Config']['Env'][-1] = 'PYTHONPATH=/image/base'
            with self.assertRaisesRegex(RuntimeError, 'environment'):
                self.owner.validate_container(item, plan, row, initial=True)

    def test_optional_binding_cannot_mutate_old_plan_or_select_off(self):
        first = self.plan()
        self.selected_plan()
        self.binding.pop('test_lane')
        self.binding.pop('checkout')
        self.assertEqual(self.plan(), first)
        self.binding.update(test_lane=consumer.LANE, checkout=str(ROOT))
        with self.assertRaisesRegex(RuntimeError, 'dual'):
            self.plan('off')


class ObservationReceiptTests(OwnerPlanFixture):
    def receipt(self):
        self.binding.update(test_lane=consumer.LANE, checkout=str(ROOT))
        plan = self.plan('dual')
        pins = plan['spec']['use_fixture']
        def row(rank):
            return dict(rank=rank, binding=[rank, 100 + rank, 0, 0, 0],
                        source_head=plan['spec']['source_head'],
                        owner_fixture_sha256=pins['owner_sha256'],
                        worker_sha256=pins['worker_sha256'],
                        observer_sha256=pins['observer_sha256'],
                        worker_extension_cls=consumer.EXTENSION,
                        ready=False, active_request=None, restoration_complete=True,
                        accepted_paths=[], accepted_path_replay_count=0,
                        accepted=[], accepted_replay_count=0,
                        replay_path_status='UNKNOWN',
                        accepted_replay_path_counts=dict.fromkeys(
                            ('eligible', 'selected', 'use', 'fallback')))
        requests = []
        for step in range(6):
            token = 'q03-use-step-' + str(step)
            requests.append(dict(request_id=token, ranks=[
                dict(row(rank), request_id=token, replay_scope='current_request')
                for rank in range(4)]))
        value = dict(readiness={'verified': 'protocol'}, outputs=[[], []],
                     retention=[dict(rank=rank, arena_unchanged=True,
                                     retained_view_objects=2) for rank in range(4)])
        value['use_observation'] = dict(
            source_head=plan['spec']['source_head'], fixture_sha256=pins['fixture_sha256'],
            readiness=value['readiness'], outputs=value['outputs'], requests=requests,
            initial=[row(rank) for rank in range(4)], stopped=[row(rank) for rank in range(4)])
        return value, plan

    def test_event_free_request_remains_unknown_and_never_consumer_pass(self):
        value, plan = self.receipt()
        result = consumer.validate_observation(value, plan)
        self.assertEqual(result['consumer_use'], 'UNQUALIFIED')
        self.assertEqual(result['missing_events'], 'UNKNOWN')
        self.assertTrue(all(count is None for count in value['use_observation'][
            'requests'][0]['ranks'][0]['accepted_replay_path_counts'].values()))

    def test_invented_zero_positive_boolean_or_prior_request_counts_fail(self):
        for count in (0, 1, True, -1):
            value, plan = self.receipt()
            value['use_observation']['requests'][0]['ranks'][0][
                'accepted_replay_path_counts']['use'] = count
            with self.subTest(count=count), self.assertRaises(RuntimeError):
                consumer.validate_observation(value, plan)
        value, plan = self.receipt()
        value['use_observation']['requests'][1]['ranks'][0]['request_id'] = 'q03-use-step-0'
        with self.assertRaises(RuntimeError):
            consumer.validate_observation(value, plan)

    def test_observed_records_still_do_not_qualify_native_consumer_use(self):
        value, plan = self.receipt()
        row = value['use_observation']['requests'][0]['ranks'][0]
        row.update(accepted_paths=[['q03-use-step-0',
                    [row['binding'], 'synthetic-segment', [[0, 0, 'use']]]]],
                   accepted_path_replay_count=1, replay_path_status='OBSERVED')
        row['accepted_replay_path_counts']['use'] = 1
        self.assertEqual(consumer.validate_observation(value, plan)['consumer_use'],
                         'UNQUALIFIED')
        row['accepted_paths'][0][1][0] = [1, 101, 0, 0, 0]
        with self.assertRaisesRegex(RuntimeError, 'rank/runner'):
            consumer.validate_observation(value, plan)

    def test_missing_retention_or_cleanup_and_foreign_source_are_not_success(self):
        changes = [lambda v: v['retention'].pop(),
                   lambda v: v['use_observation']['stopped'][3].update(restoration_complete=False),
                   lambda v: v['use_observation']['initial'][0].update(ready=True),
                   lambda v: v['use_observation']['requests'][0]['ranks'][2].update(worker_sha256='bad')]
        for change in changes:
            value, plan = self.receipt()
            change(value)
            with self.assertRaises(RuntimeError):
                consumer.validate_observation(value, plan)


class InterpreterClosureTests(unittest.TestCase):
    """Real Python exec/spawn; only mount paths, model sources and wheel are doubles."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        for name in ('binding', 'validation/tests/compile/fullgraph', 'cache',
                     'runtime', 'image/vllm/entrypoints/cli', 'pytest-dependency'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.paths = {path: str(self.root / path.lstrip('/')) for path in (
            '/binding', '/validation', '/cache', '/runtime', '/pytest-dependency')}
        self.marker = self.root / 'result.json'
        for path in ('tests/__init__.py', 'tests/compile/__init__.py',
                     'tests/compile/fullgraph/__init__.py'):
            (self.root / 'validation' / path).write_text('')
        self.worker = self.root / 'validation/tests/compile/fullgraph/q03_use_owner_worker.py'
        self.worker.write_text('VALUE = "verified source"\n')
        (self.worker.parent / 'q03_use_observer.py').write_text('VALUE = 1\n')
        runtime = self.root / 'image/vllm'
        for name in ('__init__.py', 'entrypoints/__init__.py', 'entrypoints/cli/__init__.py'):
            (runtime / name).write_text('')
        (runtime / 'entrypoints/cli/main.py').write_text('from probe import execute\nif __name__ == \"__main__\":\n    execute()\n')
        (runtime / 'probe.py').write_text('VALUE = \"runtime verified\"\n')
        (self.root / 'image/pytest.py').write_text(
            'from probe import execute\ndef main(args):\n    execute()\n    return 0\n')
        (self.root / 'image/probe.py').write_text('''import importlib, json, multiprocessing, os, pathlib, sys

def child(queue):
    if os.environ.get('PROBE_SHADOW'):
        sys.path.insert(0, os.environ['PROBE_SHADOW'])
    import q03_use_owner_worker as worker
    import vllm.probe
    if os.environ.get('PROBE_NATIVE'):
        importlib.import_module('vllm._C_stable_libtorch')
    queue.put(dict(pid=os.getpid(), path=worker.__file__,
                   loader=type(worker.__loader__).__name__,
                   prefix=sys.pycache_prefix, no_write=sys.dont_write_bytecode,
                   guard=sys.modules['sitecustomize'].__file__, value=worker.VALUE))

def execute():
    if os.environ.get('PROBE_DRIFT'):
        pathlib.Path(os.environ['PROBE_DRIFT']).write_text('VALUE = "drift"\\n')
    context = multiprocessing.get_context('spawn')
    queue = context.Queue()
    process = context.Process(target=child, args=(queue,))
    process.start()
    process.join(10)
    if process.is_alive():
        process.kill()
        process.join()
        raise RuntimeError('child did not terminate')
    if process.exitcode != 0:
        raise RuntimeError('guard rejected spawned child')
    value = queue.get(timeout=2)
    value['parent_pid'] = os.getpid()
    pathlib.Path(os.environ['PROBE_RESULT']).write_text(json.dumps(value))
''')
        native = {}
        for name in ('_C_stable_libtorch', '_moe_C_stable_libtorch'):
            path = runtime / (name + '.abi3.so')
            path.write_bytes(b'CPU protocol double, never loaded')
            native['vllm.' + name] = str(path)
        manifest = dict(source_head='synthetic-source',
                        installation=dict(mode='image-source', package_root=str(runtime)),
                        native_paths=native,
                        native_sha256={name: self.sha(Path(path)) for name, path in native.items()})
        manifest_path = self.root / 'runtime/runtime-manifest.json'
        manifest_path.write_text(json.dumps(manifest))
        tree, sources = guard.source_tree(self.root / 'validation/tests')
        spec = dict(source_head='synthetic-source', runtime_manifest_sha256=self.sha(manifest_path),
                    consumer_binding=dict(tests_subtree=tree,
                        test_sources={str(Path(p).relative_to(self.root / 'validation')): sha
                                      for p, sha in sources.items()},
                        runtime_sources={str(p.relative_to(runtime)): self.sha(p)
                                         for p in runtime.rglob('*.py')}))
        self.spec_path = self.root / 'runtime/owner-spec.json'
        self.spec_path.write_text(json.dumps(spec))
        wheel = self.root / 'pytest-dependency/tblib-3.1.0-py3-none-any.whl'
        with zipfile.ZipFile(wheel, 'w') as archive:
            archive.writestr('tblib/__init__.py', '__version__ = "3.1.0"\n')
        source = (ROOT / 'scripts/q03_consumer_site.py').read_text()
        source = source.replace('670bb4582578134b3d81a84afa1b016128b429f3d48e6cbbaecc9d15675e984e', self.sha(wheel))
        self.site = self.root / 'binding/sitecustomize.py'
        self.site.write_text(self.relocate(source))
        self.bootstrap = self.relocate(consumer.BOOTSTRAP)
        path = str(self.root / 'binding') + ':' + str(self.root / 'image')
        self.env = dict(os.environ, PYTHONPATH=path, Q03_EXPECTED_PYTHONPATH=path,
                        PYTHONDONTWRITEBYTECODE='1', PYTHONSAFEPATH='1',
                        PYTHONPYCACHEPREFIX=str(self.root / 'cache/consumer-bytecode'),
                        VLLM_WORKER_MULTIPROC_METHOD='spawn',
                        VLLM_ALLOW_INSECURE_SERIALIZATION='0',
                        Q03_CONSUMER_SITE_SHA256=self.sha(self.site),
                        Q03_CONSUMER_SPEC_SHA256=self.sha(self.spec_path),
                        PROBE_RESULT=str(self.marker))

    @staticmethod
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def relocate(self, source):
        for old, new in self.paths.items():
            source = re.sub(re.escape(old) + r"(?=/|['\"])", lambda _: new, source)
        return source

    def run_role(self, role='pytest'):
        args = ['pytest', '-q', 'synthetic-fixture'] if role == 'pytest' else ['serve', '/models', '--headless']
        return subprocess.run([sys.executable, '-I', '-B', '-S', '-c', self.bootstrap, *args],
                              env=self.env, text=True, capture_output=True, timeout=20)

    def test_both_role_execs_run_source_guard_in_real_fresh_spawn_child(self):
        for role in ('pytest', 'serve'):
            with self.subTest(role=role):
                result = self.run_role(role)
                self.assertEqual(result.returncode, 0, result.stderr)
                observed = json.loads(self.marker.read_text())
                self.assertNotEqual(observed['pid'], observed['parent_pid'])
                self.assertEqual(observed['path'], str(self.worker))
                self.assertEqual(observed['loader'], 'SourceLoader')
                self.assertEqual(observed['guard'], str(self.site))
                self.assertEqual(observed['prefix'], self.env['PYTHONPYCACHEPREFIX'])
                self.assertIs(observed['no_write'], True)
                self.assertEqual(observed['value'], 'verified source')
                self.assertFalse(Path(observed['prefix']).exists())
                self.marker.unlink()

    def test_parent_source_drift_fails_before_either_role_payload(self):
        self.worker.write_text('VALUE = "wrong"\n')
        for role in ('pytest', 'serve'):
            result = self.run_role(role)
            self.assertEqual(result.returncode, 78, result.stderr)
            self.assertIn('test subtree differs', result.stderr)
            self.assertFalse(self.marker.exists())

    def test_spawn_child_rechecks_tree_instead_of_inheriting_parent_guard(self):
        self.env['PROBE_DRIFT'] = str(self.worker)
        result = self.run_role()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('test subtree differs', result.stderr)
        self.assertFalse(self.marker.exists())

    def test_spawn_child_rejects_shadowed_worker_import(self):
        shadow = self.root / 'shadow'
        shadow.mkdir()
        (shadow / self.worker.name).write_text('VALUE = "wrong origin"\n')
        self.env['PROBE_SHADOW'] = str(shadow)
        result = self.run_role()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('escaped source', result.stderr)
        self.assertFalse(self.marker.exists())

    def test_same_root_runtime_and_native_shadows_fail_before_payload(self):
        for name in ('probe', '_C_stable_libtorch'):
            shadow = self.root / 'image/vllm' / name
            shadow.mkdir()
            (shadow / '__init__.py').write_text('VALUE = "wrong origin"\n')
            self.env['PROBE_NATIVE'] = '1'
            result = self.run_role()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('pinned module origin differs', result.stderr)
            self.assertFalse(self.marker.exists())
            (shadow / '__init__.py').unlink()
            shadow.rmdir()

    def test_valid_header_stale_pyc_never_executes_and_test_cache_is_rejected(self):
        malicious = self.root / 'stale-payload-ran'
        def stale(source):
            cache = Path(importlib.util.cache_from_source(str(source)))
            cache.parent.mkdir(parents=True, exist_ok=True)
            info = source.stat()
            code = compile('open(' + repr(str(malicious)) + ', "w").write("bad")',
                           str(source), 'exec')
            cache.write_bytes(importlib.util.MAGIC_NUMBER + b'\0' * 4
                + struct.pack('<II', int(info.st_mtime), info.st_size)
                + marshal.dumps(code))
        stale(self.site)
        stale(self.root / 'image/vllm/probe.py')
        result = self.run_role()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(malicious.exists())
        self.marker.unlink()
        stale(self.worker)
        result = self.run_role()
        self.assertEqual(result.returncode, 78)
        self.assertIn('test subtree differs', result.stderr)
        self.assertFalse(malicious.exists())
        self.assertFalse(self.marker.exists())

    def test_initial_no_site_exec_rejects_stale_prefix_and_guard_drift(self):
        prefix = Path(self.env['PYTHONPYCACHEPREFIX'])
        prefix.mkdir()
        result = self.run_role()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('prefix is not fresh', result.stderr)
        self.assertFalse(self.marker.exists())
        prefix.rmdir()
        self.site.write_text('raise RuntimeError("must never execute changed guard")\n')
        result = self.run_role()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('site source differs before startup', result.stderr)
        self.assertNotIn('must never execute changed guard', result.stderr)

    def test_site_package_shadow_is_rejected_by_no_site_first_exec(self):
        shadow = self.root / 'binding/sitecustomize'
        shadow.mkdir()
        (shadow / '__init__.py').write_text(
            'raise RuntimeError("shadow site payload must not execute")\n')
        result = self.run_role()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('site import origin differs before startup', result.stderr)
        self.assertNotIn('shadow site payload must not execute', result.stderr)
        self.assertFalse(self.marker.exists())

    def test_native_bytes_and_effective_prefix_drift_fail_closed(self):
        native = self.root / 'image/vllm/_C_stable_libtorch.abi3.so'
        native.write_bytes(b'changed')
        result = self.run_role()
        self.assertEqual(result.returncode, 78, result.stderr)
        self.assertIn('native library bytes differ', result.stderr)
        self.assertFalse(self.marker.exists())
        result = subprocess.run([sys.executable, '-B', '-X',
            'pycache_prefix=' + str(self.root / 'wrong-prefix'), '-c',
            'raise RuntimeError("payload must not run")'], env=self.env,
            text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 78)
        self.assertIn('fresh absent bytecode prefix', result.stderr)
        self.assertNotIn('payload must not run', result.stderr)


if __name__ == '__main__':
    unittest.main()
