"""Synthetic CPU contracts only; no source/image/runtime freeze or GPU claim."""
from contextlib import contextmanager
import copy
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import q03_baseline_identity as I
import q03_baseline_service as S
import q03_baseline_worker as W
import q03_http_service as H
import q03_legacy_case_replay as L
import q03_quality_capture as C
from test_q03_baseline_identity import runtime as old_runtime, plan_and_ready
import test_q03_baseline_service as service_fixtures


@contextmanager
def synthetic_freeze():
    """All fake pins are confined to this context; never write a live freeze."""
    freeze = copy.deepcopy(I.REVISED_FREEZE)
    for key in ('upstream_deployment_head', 'upstream_deployment_tree'):
        freeze[key] = 'e' * 40
    for key in ('upstream_patchset_sha256', 'steering_patchset_sha256', 'common_recipe_sha256', 'effective_sha256',
                'common_runtime_sha256', 'model_tokenizer_sha256'):
        freeze[key] = 'a' * 64
    for key in ('profile_sha256', 'build_inputs_sha256', 'build_receipt_sha256', 'runtime_manifest_sha256', 'image_rootfs_sha256'):
        freeze[key] = {arm: 'b' * 64 for arm in I.REVISED_ARMS}
    freeze['component_sources'] = {name: {'head': 'f' * 40, 'tree': 'e' * 40}
                                  for name in I.COMPONENTS if name != 'vllm'}
    identities = {arm: dict(image='sha256:' + str(index) * 64,
                           source_head=str(index) * 40, source_tree=str(index + 2) * 40)
                  for index, arm in enumerate(I.REVISED_ARMS, 1)}
    sources = {arm: {key: 'c' * 64 for key in (I.REVISED_COMMON_SOURCES |
                   (I.REVISED_STEERING_SOURCES if arm == 'r6x-steer' else set()))}
               for arm in I.REVISED_ARMS}
    with mock.patch.dict(I.REVISED_FREEZE, freeze, clear=True), \
         mock.patch.dict(I.REVISED_IDENTITIES, identities, clear=True), \
         mock.patch.dict(I.REVISED_SOURCE_SHA256, sources, clear=True):
        yield


def runtime(arm):
    value = old_runtime()
    value.update(I.checked_identity(arm), source_sha256=I.source_hashes(arm),
                 absent_source_paths=I.absent_source_paths(arm))
    I.REVISED_FREEZE['common_runtime_sha256'] = I.common_runtime_sha256(value)
    return value


class UnboundAdmissionTests(unittest.TestCase):
    @mock.patch.dict(I.REVISED_FREEZE, {'common_runtime_sha256': 'UNBOUND'})
    def test_new_arms_fail_before_static_plan_files_or_operations(self):
        for arm in I.REVISED_ARMS:
            with self.subTest(arm=arm), mock.patch.object(I, 'read_owned_bytes') as read:
                with self.assertRaisesRegex(ValueError, 'UNBOUND'):
                    I.static_capture_plan(arm, 'a' * 32)
                with self.assertRaisesRegex(ValueError, 'UNBOUND'):
                    I.validate_host({'schema_version': 1, 'arm': arm,
                        'provenance': {'path': '/unused', 'sha256': 'b' * 64}}, {}, mock.Mock())
                read.assert_not_called()

    @mock.patch.dict(I.REVISED_FREEZE, {'common_runtime_sha256': 'UNBOUND'})
    @mock.patch.object(H, 'baseline_module', lambda: S)
    @mock.patch.object(S, 'identity_module', lambda: I)
    def test_both_schema_entries_fail_before_ops(self):
        for schema in (6, 7):
            for arm, state in (('r6x-unpatched', 'absent'), ('r6x-steer', 'loaded0')):
                manifest = {'jobs': [{'kind': 'http_service', 'timeout_seconds': 7200,
                    'selection': {'arm': arm, 'steering': state}}], 'http_service': {'schema_version': schema}}
                ops = mock.Mock()
                with self.subTest(schema=schema, arm=arm), self.assertRaisesRegex(ValueError, 'UNBOUND'):
                    H.validate_binding(manifest, ops)
                ops.run.assert_not_called()

    def test_each_missing_pin_remains_a_hard_gate(self):
        with synthetic_freeze():
            I.validate_revised_freeze()
            for key in I.REVISED_FREEZE:
                old = I.REVISED_FREEZE[key]
                I.REVISED_FREEZE[key] = 'UNBOUND'
                with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'UNBOUND'):
                    I.validate_revised_freeze()
                I.REVISED_FREEZE[key] = old
            for arm in I.REVISED_ARMS:
                old = I.REVISED_SOURCE_SHA256[arm]
                I.REVISED_SOURCE_SHA256[arm] = 'UNBOUND'
                with self.assertRaisesRegex(ValueError, 'UNBOUND'):
                    I.checked_identity(arm)
                I.REVISED_SOURCE_SHA256[arm] = old

    def test_truthful_steering_image_reuse_but_never_relabel_as_unpatched(self):
        historical = copy.deepcopy(I.IDENTITIES)
        with synthetic_freeze():
            I.REVISED_IDENTITIES['r6x-steer'] = copy.deepcopy(I.IDENTITIES['r6x188'])
            I.validate_revised_freeze()
            I.REVISED_IDENTITIES['r6x-unpatched'] = copy.deepcopy(I.IDENTITIES['r6x188'])
            with self.assertRaisesRegex(ValueError, 'relabelled as unpatched'):
                I.validate_revised_freeze()
        self.assertEqual(I.IDENTITIES, historical)


class CapabilityTests(unittest.TestCase):
    def test_unpatched_absence_not_steering_unloaded(self):
        with synthetic_freeze():
            selected = {'arm': 'r6x-unpatched', 'steering': 'absent'}
            self.assertEqual(I.validate_selection(selected), selected)
            self.assertEqual(I.steering_request(selected), {})
            self.assertEqual(L.extra_body(selected), {'temperature': 0, 'seed': 2048})
            self.assertEqual(C.extra_body('absent'), C.extra_body('unloaded'))
            for arm, state in (('r6x-unpatched', 'unloaded'), ('r6x-unpatched', 'loaded0'),
                               ('r6x-steer', 'absent'), ('r6x188', 'absent')):
                for validator in (I.validate_selection, L.validate_selection):
                    with self.subTest(arm=arm, state=state), self.assertRaises((ValueError, RuntimeError)):
                        validator({'arm': arm, 'steering': state})
            for state in I.STEERING:
                selected = {'arm': 'r6x-steer', 'steering': state}
                self.assertEqual(I.validate_selection(selected), selected)
                self.assertEqual(I.steering_request(selected), {} if state == 'unloaded' else {
                    'steering_profile': I.PROFILE_NAME, 'steering_scale': int(state[-1])})

    def test_model_observer_requires_absent_or_present_capability(self):
        self.assertEqual(W.observe_target(SimpleNamespace(), 'r6x-unpatched'), (None, False))
        with self.assertRaisesRegex(RuntimeError, 'has steering capability'):
            W.observe_target(SimpleNamespace(steering_profile=None), 'r6x-unpatched')
        with self.assertRaisesRegex(RuntimeError, 'capability is missing'):
            W.observe_target(SimpleNamespace(), 'r6x-steer')
        self.assertEqual(W.observe_target(SimpleNamespace(steering_profile=None), 'r6x-steer'), (None, False))
        for arm in I.REVISED_ARMS:
            with self.assertRaisesRegex(RuntimeError, 'Q03 model patch'):
                W.observe_target(SimpleNamespace(q03_input_arena=None, steering_profile=None), arm)
        profile = SimpleNamespace(profile_id=I.PROFILE_NAME, artifact_sha256=I.PROFILE_SHA256,
            identity='fixed', schedule_sha256='a' * 64, layers=[1], alphas=[1.0])
        observed, _ = W.observe_target(SimpleNamespace(steering_profile=profile), 'r6x-steer')
        self.assertEqual(observed['artifact_sha256'], I.PROFILE_SHA256)

    def test_static_source_inventory_and_common_runtime_are_exact(self):
        with synthetic_freeze():
            pure, steer = runtime('r6x-unpatched'), runtime('r6x-steer')
            self.assertEqual(I.validate_runtime(pure, 'r6x-unpatched'), pure)
            self.assertEqual(I.validate_runtime(steer, 'r6x-steer'), steer)
            self.assertFalse(any('/steering/' in name for name in pure['source_sha256']))
            self.assertTrue(all(name in steer['source_sha256'] for name in I.REVISED_STEERING_SOURCES))
            pure['absent_source_paths'] = []
            with self.assertRaisesRegex(ValueError, 'absent-source proof'):
                I.validate_runtime(pure, 'r6x-unpatched')
            pure['absent_source_paths'] = I.absent_source_paths('r6x-unpatched')
            pure['native_sha256'][I.NATIVE_NAMES[0]] = 'f' * 64
            with self.assertRaisesRegex(ValueError, 'common runtime'):
                I.validate_runtime(pure, 'r6x-unpatched')

    def test_revised_image_rechecks_frozen_layers_parent_and_common_components(self):
        with synthetic_freeze():
            for arm in I.REVISED_ARMS:
                value = runtime(arm)
                labels = {'local.spark3.vllm.patch-head': value['source_head'],
                          'local.spark3.vllm.tree': value['source_tree'],
                          'local.site.native-parent': I.REVISED_FREEZE['native_parent_image']}
                for name, component in I.REVISED_FREEZE['component_sources'].items():
                    labels['local.spark3.' + name + '.patch-head'] = component['head']
                    labels['local.spark3.' + name + '.tree'] = component['tree']
                image = dict(Id=value['image'], Architecture='arm64', Os='linux',
                    Config={'Labels': labels, 'Env': ['PYTHONPATH=' + value['pythonpath']]},
                    RootFS={'Type': 'layers', 'Layers': ['sha256:' + 'd' * 64]})
                I.REVISED_FREEZE['image_rootfs_sha256'][arm] = I.digest(I.canonical(image['RootFS']))
                I.validate_image(arm, image, value)
                for section, key, changed in (
                    ('RootFS', 'Layers', ['sha256:' + 'e' * 64]),
                    ('labels', 'local.site.native-parent', 'sha256:' + 'e' * 64),
                    ('labels', 'local.spark3.nccl.tree', 'd' * 40)):
                    bad = copy.deepcopy(image)
                    target = bad['Config']['Labels'] if section == 'labels' else bad[section]
                    target[key] = changed
                    with self.subTest(arm=arm, key=key), self.assertRaises(ValueError):
                        I.validate_image(arm, bad, value)

    def test_static_capture_measures_source_absence_without_model_import(self):
        with synthetic_freeze():
            value = I.static_capture_plan('r6x-unpatched', 'a' * 32)
            self.assertIn("'absent_source_paths': ['models/deepseek_v4_1/steering']", value['argv'][-1])
            self.assertIn('inspect_source_absence(root', value['argv'][-1])
            self.assertIn('os.O_NOFOLLOW', value['argv'][-1])
            self.assertIn('empty_source_directories', value['argv'][-1])
            self.assertNotIn('import torch', value['argv'][-1])
            self.assertNotIn('import vllm', value['argv'][-1])

    def test_absent_request_requires_only_the_existing_common_metrics(self):
        self.assertFalse(any('steer' in name or 'q03' in name for name in L.METRIC_NAMES))
        self.assertEqual(C.idle_gauges('vllm:num_requests_running 0\nvllm:num_requests_waiting 0\n'),
                         [0.0, 0.0])


class PlanningTests(unittest.TestCase):
    def binding(self, arm, state):
        fixture = service_fixtures.Tests()
        fixture.identity = service_fixtures.Identity()
        value = fixture.binding()
        value['selection'] = {'arm': arm, 'steering': state}
        value['http']['baseline_identity']['arm'] = arm
        value['baseline_identity']['identity'] = I.checked_identity(arm)
        value['candidate'].update(image=I.checked_identity(arm)['image'],
                                  component_head=I.checked_identity(arm)['source_head'])
        if arm == 'r6x-unpatched':
            args = value['profile_value']['serve_args']; index = args.index('--additional-config')
            del args[index:index + 2]
            value['profile_value']['container']['mounts'] = [m for m in
                value['profile_value']['container']['mounts'] if m[1] != '/steering']
        return value

    def make_plan(self, value):
        checked = {key: value['baseline_identity'][key] for key in ('identity', 'runtime', 'provenance')}
        with mock.patch.object(S, 'identity_module', return_value=I), \
             mock.patch.object(I, 'validate_binding', return_value=checked), \
             mock.patch.object(S, 'legacy_module', return_value=SimpleNamespace(client_plan=lambda b,s: {})), \
             mock.patch.object(S, 'read_owned_bytes', return_value=b'fixed-source'):
            return S.http_plan(value, 'a' * 32, 'b' * 32, value['selection'])

    def test_same_recipe_and_1m_pure_plan_has_no_steer_or_q03_inputs(self):
        with synthetic_freeze():
            pure = self.binding('r6x-unpatched', 'absent')
            steer = self.binding('r6x-steer', 'loaded0')
            pure['profile_value'].update(upstreams_config='upstream176.lock.json',
                                         promoted_baseline='genuine-upstream176')
            steer['profile_value'].update(upstreams_config='steer188.lock.json',
                                          promoted_baseline='upstream176-plus-steer')
            self.assertEqual(S.common_recipe(pure['profile_value']), S.common_recipe(steer['profile_value']))
            for binding in (pure, steer):
                plan = self.make_plan(binding)
                for row in plan['ranks']:
                    argv = row['python_argv']
                    self.assertEqual(argv[argv.index('--max-model-len') + 1], '1048576')
                    self.assertNotIn('VLLM_DS41_Q03_MHC_MXFP8', row['environment'])
                    if binding is pure:
                        self.assertNotIn('--additional-config', argv)
                        self.assertFalse(any('/steering' == m[1] for m in row['mounts']))
                    else:
                        self.assertIn('--additional-config', argv)
            pure['profile_value']['serve_args'] += ['--additional-config', '{}']
            with self.assertRaisesRegex(RuntimeError, 'unpatched profile'):
                self.make_plan(pure)

    def test_four_rank_absence_and_candidate_profile_proof_never_fabricated(self):
        with synthetic_freeze():
            for arm, state in (('r6x-unpatched', 'absent'), ('r6x-steer', 'loaded0')):
                plan, ready = plan_and_ready(steering='unloaded' if state == 'absent' else state)
                r = runtime(arm)
                plan['selection'] = {'arm': arm, 'steering': state}
                plan['spec']['baseline_identity'].update(identity=I.checked_identity(arm), runtime=r)
                expected = {'model_config.max_model_len': 1048576}
                plan['spec']['expected_effective_config'] = expected
                for report in ready['reports']:
                    report.update(identity=I.checked_identity(arm), source_sha256=r['source_sha256'],
                        q03_switch=None, absent_source_paths=I.absent_source_paths(arm),
                        steering_capability='absent' if state == 'absent' else 'present',
                        effective_config=expected, runner_config=expected)
                def raw():
                    ready['reports_sha256'] = I.digest(I.canonical(ready['reports']))
                    return I.canonical(ready)
                self.assertEqual(I.validate_ready(raw(), plan), ready)
                ready['reports'][0]['steering_capability'] = 'unknown'
                with self.assertRaisesRegex(ValueError, 'capability/absence'):
                    I.validate_ready(raw(), plan)


if __name__ == '__main__':
    unittest.main()
