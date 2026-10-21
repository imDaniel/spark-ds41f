"""Capture-only CPU fault tests. All service/process operations are inert fakes."""
import copy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

import test_q03_window as fixtures


class BuildPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guard = fixtures.load_module('q03_window_preflight', 'PREFLIGHT')

    def test_old_build_publication_is_not_replaced_by_controller_revision(self):
        old = {'url': 'https://github.com/imDaniel/spark-ds41f.git',
               'ref': 'refs/heads/completed-build', 'sha': 'a' * 40}
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=0, stdout=old['sha'] + '\t' + old['ref'] + '\n', stderr='')
        self.guard.verify_build_publication(old, SimpleNamespace(run=run))
        self.assertEqual(calls, [['git', '--no-optional-locks', 'ls-remote', '--exit-code',
                                  old['url'], old['ref']]])
        self.assertEqual(old['sha'], 'a' * 40)

    def test_wrong_tip_missing_ref_and_failed_query_fail_closed(self):
        old = {'url': 'https://github.com/imDaniel/spark-ds41f.git',
               'ref': 'refs/heads/completed-build', 'sha': 'a' * 40}
        for stdout, code in [('b' * 40 + '\t' + old['ref'], 0), ('', 0),
                             (old['sha'] + '\trefs/heads/other', 0), ('', 128)]:
            with self.subTest(stdout=stdout, code=code):
                run = mock.Mock(return_value=SimpleNamespace(returncode=code, stdout=stdout, stderr='failed'))
                with self.assertRaises((ValueError, RuntimeError)):
                    self.guard.verify_build_publication(old, SimpleNamespace(run=run))
        for key, value in [('url', 'https://example.invalid/repo.git'),
                           ('ref', 'main'), ('sha', 'short')]:
            run = mock.Mock(side_effect=AssertionError('invalid authority must precede I/O'))
            with self.subTest(key=key), self.assertRaises((ValueError, RuntimeError)):
                self.guard.verify_build_publication(dict(old, **{key: value}), SimpleNamespace(run=run))
            run.assert_not_called()

class CaptureWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.WindowRecoveryTests.setUpClass()
        fixtures.load_module('q03_window_preflight', 'PREFLIGHT')
        cls.build = fixtures.load_module('q03_window_build', 'PREFLIGHT')

    def setUp(self):
        self.fixture = fixtures.WindowRecoveryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.core = self.fixture.core
        self.manifest = self.fixture.manifest
        self.manifest['preflight']['independent_checkout'] = str(self.fixture.root / 'controller')
        self.manifest['jobs'] = [{'kind': 'capture', 'timeout_seconds': 120}]
        candidate = self.manifest['candidate']
        for key in ('runtime_manifest_path', 'runtime_manifest_sha256', 'tests_path', 'cache_path'):
            candidate.pop(key)
        candidate['component_head'] = self.build.SOURCE_HEAD
        Path(candidate['output_path']).mkdir()
        self.ops = self.fixture.ops
        self.ops.validate_capture_result = lambda manifest, execution: self.capture_result()

    def capture_result(self):
        self.ops.event('result:capture')
        return {'status': 'captured-unreviewed', 'requires_independent_review': True}

    def test_capture_has_no_runtime_prerequisite_and_stops_before_execution(self):
        state = self.fixture.run_window()
        self.assertEqual(state['job_results'], ['capture'])
        events = self.ops.events
        self.assertLess(events.index('stop:1'), events.index('job:capture'))
        self.assertLess(events.index('check:stopped'), events.index('job:capture'))
        self.assertLess(events.index('job:capture'), events.index('cleanup'))
        self.assertLess(events.index('cleanup'), events.index('restore:start'))
        self.assertTrue(state['restored'])
        self.assertFalse(self.fixture.hold.exists())
        receipt = self.ops.read_json(self.fixture.directory / 'capture-result.json')
        self.assertEqual(receipt['status'], 'captured-unreviewed')
        self.assertTrue(receipt['requires_independent_review'])
        self.assertNotIn('job:p0', events)

    def test_capture_failure_restores_without_success_receipt(self):
        self.ops.failure = 'result:capture'
        with self.assertRaisesRegex(RuntimeError, 'result:capture'):
            self.fixture.run_window()
        state = self.fixture.state()
        self.assertEqual(state['job_results'], [])
        self.assertEqual(state['outcome'], 'failed')
        self.assertTrue(state['restored'])
        self.assertFalse((self.fixture.directory / 'capture-result.json').exists())
        self.assertFalse(self.fixture.hold.exists())

    def test_uncertain_cleanup_keeps_hold_and_never_restores(self):
        self.ops.failure = 'cleanup'
        with self.assertRaisesRegex(RuntimeError, 'cleanup'):
            self.fixture.run_window()
        self.assertTrue(self.fixture.hold.exists())
        self.assertFalse(self.fixture.state()['restored'])
        self.assertNotIn('restore:start', self.ops.events)

    def test_create_failure_cannot_start_capture_or_forge_runtime(self):
        self.ops.prepare_candidate = mock.Mock(side_effect=RuntimeError('create failed'))
        with self.assertRaisesRegex(RuntimeError, 'create failed'):
            self.fixture.run_window()
        self.assertNotIn('job:capture', self.ops.events)
        self.assertTrue(self.fixture.state()['candidate_containers'])
        self.assertTrue(self.fixture.state()['restored'])
        self.assertFalse((Path(self.manifest['candidate']['output_path']) / 'runtime-manifest.json').exists())

    def test_capture_timeout_and_existing_evidence_rejected_before_hold(self):
        for timeout in (0, 121, True):
            self.manifest['jobs'][0]['timeout_seconds'] = timeout
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                self.fixture.run_window()
            self.assertFalse(self.fixture.directory.exists())
            self.assertFalse(self.fixture.hold.exists())
        self.manifest['jobs'][0]['timeout_seconds'] = 120
        output = Path(self.manifest['candidate']['output_path']) / 'runtime-manifest.json'
        output.write_text('existing evidence')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.fixture.run_window()
        self.assertEqual(output.read_text(), 'existing evidence')
        self.assertEqual(self.ops.commands, [])

    def test_capture_argv_fixed_image_no_devices_or_host_mounts(self):
        image, window = self.manifest['candidate']['image'], 'a' * 32
        name, argv = self.build.native_capture_command(image, window)
        self.assertEqual(name, 'q03-native-capture-' + window)
        for forbidden in ('--gpus', '--device', '--mount', '-v', '--privileged'):
            self.assertNotIn(forbidden, argv)
        for required in ('--read-only', '--network=none', '--runtime=nvidia', 'NVIDIA_VISIBLE_DEVICES=none', 'NVIDIA_DRIVER_CAPABILITIES=compute'):
            self.assertIn(required, argv)
        self.assertEqual(argv[argv.index('--entrypoint') + 1:][:2], ['python3', image])
        for bad_image, bad_window in [('mutable:tag', window), (image, 'foreign')]:
            with self.assertRaises(ValueError):
                self.build.native_capture_command(bad_image, bad_window)

    def test_layout_preserves_immutable_source_and_allows_separate_work_evidence(self):
        checkout = Path(self.manifest['preflight']['independent_checkout'])
        prepared = Path(self.manifest['candidate']['build_inputs_path']).parent
        immutable_paths = [checkout / name for name in ('.git', 'bin', 'scripts', 'config', 'experiments', 'docker')]
        immutable_paths += [prepared / 'src', prepared / 'context', Path(self.manifest['candidate']['build_inputs_path'])]
        for immutable in immutable_paths:
            for output in (True, False):
                changed = copy.deepcopy(self.manifest)
                directory = self.fixture.directory
                if output:
                    changed['candidate']['output_path'] = str(immutable / 'evidence')
                else:
                    directory = immutable / 'session'
                with self.subTest(immutable=immutable, output=output), self.assertRaisesRegex(ValueError, 'immutable'):
                    self.core.validate_layout(changed, directory)
        changed = copy.deepcopy(self.manifest)
        changed['candidate']['output_path'] = str(checkout / '.work' / 'capture-output')
        self.core.validate_layout(changed, checkout / '.work' / 'capture-session')

    def test_actual_prestart_capture_inspection_binds_config_before_start(self):
        import json
        window = 'f' * 32
        name, argv = self.build.native_capture_command(self.manifest['candidate']['image'], window)
        valid = {'Id': 'd' * 64, 'Image': self.manifest['candidate']['image'],
                 'State': {'Running': False},
                 'Config': {'Labels': {'q03.window': window},
                            'Env': ['NVIDIA_VISIBLE_DEVICES=none', 'NVIDIA_DRIVER_CAPABILITIES=compute']},
                 'HostConfig': {'Runtime': 'nvidia', 'Devices': [], 'DeviceRequests': [],
                                'Privileged': False, 'NetworkMode': 'none', 'ReadonlyRootfs': True},
                 'Mounts': []}
        state = {'candidate_containers': [name]}
        journal = self.fixture.root / 'actual-capture-state.json'
        def update(**values):
            state.update(values)
            journal.write_text(json.dumps(state))
        session = SimpleNamespace(directory=self.fixture.root, manifest=self.manifest,
                                  state=state, update=update, capsule={'window_id': window})
        calls = []
        facade = SimpleNamespace(execute=lambda _, command, **kw: calls.append(command),
                                 run=lambda command: SimpleNamespace(stdout=json.dumps([valid])))
        start = self.core.RuntimeOps.prepare_candidate(facade, session, name, argv)
        self.assertEqual(calls[0][:2], ['docker', 'create'])
        self.assertEqual(start, ['docker', 'start', '--attach', valid['Id']])
        saved = json.loads(journal.read_text())
        identity = saved['capture_container_identity']
        self.assertEqual(identity['image_id'], valid['Image'])
        self.assertEqual(identity['runtime'], 'nvidia')
        self.assertEqual(len(identity['config_sha256']), 64)
        self.assertEqual(saved['candidate_container_ids'][name], valid['Id'])
        mutations = [('Image', None, 'sha256:' + '0' * 64),
                     ('HostConfig', 'Runtime', 'runc'), ('HostConfig', 'Devices', [{'PathOnHost': '/dev/fake'}]),
                     ('HostConfig', 'DeviceRequests', [{'Count': 0}]),
                     ('HostConfig', 'Privileged', True), ('HostConfig', 'NetworkMode', 'host'),
                     ('HostConfig', 'ReadonlyRootfs', False),
                     ('Config', 'Env', ['NVIDIA_VISIBLE_DEVICES=all', 'NVIDIA_DRIVER_CAPABILITIES=compute']),
                     ('Config', 'Env', ['NVIDIA_VISIBLE_DEVICES=none', 'NVIDIA_DRIVER_CAPABILITIES=all']),
                     ('Config', 'Env', valid['Config']['Env'] + ['NVIDIA_VISIBLE_DEVICES=none']),
                     ('Config', 'Env', valid['Config']['Env'] + ['NVIDIA_VISIBLE_DEVICES=all'])]
        for section, key, value in mutations:
            altered = copy.deepcopy(valid)
            if key is None:
                altered[section] = value
            else:
                altered[section][key] = value
            state.clear()
            state['candidate_containers'] = [name]
            calls.clear()
            facade.run = lambda command: SimpleNamespace(stdout=json.dumps([altered]))
            with self.subTest(section=section, key=key, value=value), self.assertRaisesRegex(RuntimeError, 'runtime/device/environment'):
                self.core.RuntimeOps.prepare_candidate(facade, session, name, argv)
            self.assertNotIn('candidate_container_ids', state)
            self.assertNotIn('capture_container_identity', state)
            self.assertTrue(all(command[:2] == ['docker', 'create'] for command in calls))

    def runtime(self):
        names = ('vllm._C_stable_libtorch', 'vllm._moe_C_stable_libtorch')
        return {'source_head': self.build.SOURCE_HEAD,
                'installation': {'mode': 'image-source', 'package_root': '/image/vllm'},
                'versions': {'python': '3.12.14', 'torch': '2.13.0+cu130',
                             'tilelang': '0.1.15', 'tile_kernels': 'synthetic'},
                'native_paths': {name: '/image/' + name + '.so' for name in names},
                'native_sha256': {name: 'd' * 64 for name in names}}

    def test_native_capture_marker_and_identity_fail_closed(self):
        import json
        valid = self.runtime()
        text = 'log before\nQ03_NATIVE_MANIFEST=' + json.dumps(valid) + '\nlog after\n'
        self.assertEqual(self.build.validate_native_capture_output(text), valid)
        for invalid in ('', text + text, 'Q03_NATIVE_MANIFEST=not-json', 'Q03_NATIVE_MANIFEST={"x":1,"x":2}', 'x' * (1024 * 1024 + 1)):
            with self.subTest(invalid=invalid[:60]), self.assertRaises(ValueError):
                self.build.validate_native_capture_output(invalid)
        for key, value in [('source_head', '0' * 40), ('native_sha256', {}),
                           ('native_paths', {}), ('versions', {}), ('installation', {})]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.build.validate_native_capture_output('Q03_NATIVE_MANIFEST=' + json.dumps(dict(valid, **{key: value})))

    def test_actual_capture_result_is_exclusive_unreviewed_and_not_p0_authority(self):
        import hashlib
        import json
        log = self.fixture.root / 'capture.log'
        log.write_text('Q03_NATIVE_MANIFEST=' + json.dumps(self.runtime()))
        facade = SimpleNamespace(sha256=lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest())
        result = self.core.RuntimeOps.validate_capture_result(facade, self.manifest, {'log_path': str(log)})
        self.assertEqual(result['status'], 'captured-unreviewed')
        self.assertIs(result['requires_independent_review'], True)
        output = Path(result['runtime_manifest_path'])
        before = output.read_bytes()
        self.assertEqual(result['runtime_manifest_sha256'], hashlib.sha256(before).hexdigest())
        with self.assertRaises(FileExistsError):
            self.core.RuntimeOps.validate_capture_result(facade, self.manifest, {'log_path': str(log)})
        self.assertEqual(output.read_bytes(), before)

    def build_fixture(self):
        import json
        old, new = 'a' * 40, 'b' * 40
        image = self.manifest['candidate']['image']
        base = 'sha256:' + 'e' * 64
        labels = {'org.opencontainers.image.revision': old,
                  'org.opencontainers.image.base.digest': base,
                  'local.spark3.vllm.patch-head': self.build.SOURCE_HEAD,
                  'local.spark3.vllm.tree': self.build.SOURCE_TREE}
        official = {'tag': 'q03-validation:synthetic', 'image_id': image,
                    'deployment_commit': old, 'compile_jobs': 1,
                    'min_available_gib': 24, 'built_utc': '2026-01-01T00:00:00Z'}
        receipt = {'result': 'built-official-smoke-passed', 'image_id': image,
                   'source_head': self.build.SOURCE_HEAD, 'source_tree': self.build.SOURCE_TREE,
                   'image_labels': labels, 'official_receipt': official}
        candidate = self.manifest['candidate']
        publication = {'url': 'https://github.com/imDaniel/spark-ds41f.git',
                       'ref': 'refs/heads/original-build', 'sha': old}
        candidate['build_publication'] = publication
        self.manifest['preflight']['publication'] = dict(publication, ref='refs/heads/new-controller', sha=new)
        records = {candidate['build_receipt_path']: receipt,
                   candidate['build_inputs_path']: {'inputs': {'base_image': 'example@' + base,
                       'vllm': {'patch_head': self.build.SOURCE_HEAD, 'expected_tree': self.build.SOURCE_TREE}}}}
        observed = {'Id': image, 'Config': {'Labels': copy.deepcopy(labels)}}
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            if argv[:3] == ['git', '--no-optional-locks', 'ls-remote']:
                return SimpleNamespace(returncode=0, stdout=old + '\t' + publication['ref'] + '\n', stderr='')
            if argv[:3] == ['docker', 'image', 'inspect']:
                return SimpleNamespace(returncode=0, stdout=json.dumps([observed]), stderr='')
            raise AssertionError('unexpected command ' + repr(argv))
        ops = SimpleNamespace(read_json=lambda path: records[path], sha256=lambda path: 'a' * 64, run=run)
        return ops, receipt, observed, calls

    def test_actual_build_gate_preserves_old_build_revision_under_new_controller(self):
        ops, receipt, image, calls = self.build_fixture()
        result = self.build.validate_candidate_build(self.manifest, ops)
        self.assertIs(result, receipt)
        self.assertEqual(image['Config']['Labels']['org.opencontainers.image.revision'], 'a' * 40)
        self.assertEqual(self.manifest['preflight']['publication']['sha'], 'b' * 40)
        self.assertEqual(calls[0][-1], 'refs/heads/original-build')
        self.assertNotIn('runtime_manifest_path', self.manifest['candidate'])

    def test_actual_build_gate_rejects_wrong_ref_hash_receipt_image_and_labels(self):
        ops, receipt, image, calls = self.build_fixture()
        candidate = self.manifest['candidate']
        for target, key, value in [(candidate, 'build_receipt_sha256', '0' * 64),
                                   (candidate['build_publication'], 'sha', '0' * 40),
                                   (receipt, 'result', 'failed'),
                                   (image, 'Id', 'sha256:' + '0' * 64),
                                   (image['Config'], 'Labels', {})]:
            old = target[key]
            target[key] = value
            try:
                with self.subTest(key=key), self.assertRaises((ValueError, RuntimeError)):
                    self.build.validate_candidate_build(self.manifest, ops)
            finally:
                target[key] = old


if __name__ == '__main__':
    unittest.main()
