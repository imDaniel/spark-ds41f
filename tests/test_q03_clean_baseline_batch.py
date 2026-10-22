"""Composed clean U/S warm-batch contracts; synthetic CPU evidence only."""
import copy
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
import q03_baseline_batch as Batch
import q03_baseline_identity as Identity
import q03_baseline_service as Bridge
import q03_http_service as Http
import q03_legacy_pair_report as Report
import test_q03_unpatched_baseline as Revised
import test_q03_baseline_batch as Existing
import test_q03_legacy_pair_report as Historical

SELECTIONS = [('r6x-unpatched', 'absent')] + [
    ('r6x-steer', state) for state in ('unloaded', 'loaded0', 'loaded1', 'loaded2', 'loaded3', 'loaded4')]


class CompositionTests(unittest.TestCase):
    def test_real_source_loaders_and_union_inventory(self):
        bridge = Http.baseline_module()
        self.assertEqual(bridge.identity_module().REVISED_FREEZE, Identity.REVISED_FREEZE)
        self.assertEqual(bridge.batch_module().CASES, Batch.CASES)
        self.assertEqual(bridge.quality_pilot_module().WORKLOAD, Bridge.quality_pilot_module().WORKLOAD)
        from q03_window import RuntimeOps
        sources = RuntimeOps().recovery_sources()
        for name in ('q03_baseline_batch.py', 'q03_quality_capture.py', 'q03_quality_pilot.py'):
            self.assertIn(name, sources)
            self.assertIn(name, bridge.SOURCE_FILES)
            self.assertIn(name, Http.SOURCE_FILES)
            self.assertEqual(sources[name], Identity.digest((ROOT / 'scripts' / name).read_bytes()))

    def test_all_revised_fixed_batches_fail_before_external_operations_when_unbound(self):
        for arm, state in SELECTIONS:
            manifest = dict(jobs=[dict(kind='http_service', timeout_seconds=7200,
                selection=dict(arm=arm, steering=state))], http_service=dict(schema_version=6,
                case_sequence=list(Batch.CASES), legacy_case=dict(case_id='warm4k_c1')))
            ops = mock.Mock()
            with self.subTest(arm=arm, state=state), self.assertRaisesRegex(ValueError, 'UNBOUND'):
                Http.validate_binding(manifest, ops)
            self.assertEqual(ops.mock_calls, [])

    def test_quality_is_not_a_batch_and_single_case_scope_remains_unchanged(self):
        with self.assertRaisesRegex(RuntimeError, 'only fixed'):
            Batch.validate_sequence(dict(schema_version=7, case_sequence=list(Batch.CASES),
                quality_pilot={}))
        ops = Http.HttpOps.__new__(Http.HttpOps)
        ops.binding = dict(http=dict(schema_version=7))
        ops.plan = dict(client=dict(quality_pilot={}))
        ops.journal = mock.Mock(directory=Path('/synthetic/quality'))
        self.assertEqual(ops.client_scope(), (ops.plan, ops.journal.directory))
        with self.assertRaisesRegex(RuntimeError, 'single client'):
            ops.client_scope(0)

    def test_only_two_real_freeze_hashes_remain_unbound(self):
        def unbound(value, prefix=''):
            if isinstance(value, dict):
                return [p for key, item in value.items() for p in unbound(item, prefix + '/' + key)]
            return [prefix] if value == 'UNBOUND' else []
        self.assertEqual(set(unbound(Identity.REVISED_FREEZE)), {
            '/common_runtime_sha256', '/runtime_manifest_sha256/r6x-unpatched'})
        self.assertEqual(Identity.REVISED_FREEZE['profile_sha256']['r6x-unpatched'],
            '62f545bf727f23a5c24dddd35358ce6caba4fde2d81b5684187a414c998e7e83')
        self.assertEqual(Identity.REVISED_FREEZE['runtime_manifest_sha256']['r6x-steer'],
            'c7a9ec935683bff337560e128642e9a40f97367a8a71fe0c1a2117c136828ac2')
        self.assertEqual(Identity.REVISED_FREEZE['model_tokenizer_sha256'],
            '43a054fb5dd965948b611ea5ccd68178177f97e544922d7cd2fdc3c115203013')


class RevisedScopedClients(Existing.ScopedClientFaults):
    def test_each_revised_state_keeps_one_image_and_two_independent_original_clients(self):
        with Revised.synthetic_freeze():
            for arm, state in SELECTIONS:
                fixture = Existing.ScopedClientFaults()
                try:
                    fixture.setUp()
                    fixture.plan['selection'] = dict(arm=arm, steering=state)
                    fixture.plan['binding']['selection'] = copy.deepcopy(fixture.plan['selection'])
                    fixture.plan['client']['legacy_case']['selection'] = copy.deepcopy(fixture.plan['selection'])
                    fixture.plan.update(image=Identity.checked_identity(arm)['image'],
                                        source_head=Identity.checked_identity(arm)['source_head'])
                    Existing.rehash(fixture.plan)
                    with self.subTest(arm=arm, state=state):
                        fixture.run_both()
                        self.assertEqual(fixture.ops.mutate.call_count, 2)
                        self.assertEqual(fixture.ops.transport.call_count, 8)
                        self.assertEqual(fixture.urlopen.call_count, 2)
                        a, _ = fixture.ops.client_scope(0)
                        b, _ = fixture.ops.client_scope(1)
                        self.assertEqual(a['image'], b['image'])
                        self.assertEqual(a['selection'], b['selection'])
                        self.assertNotEqual(a['plan_sha256'], b['plan_sha256'])
                        self.assertNotEqual(a['client']['output'], b['client']['output'])
                finally:
                    fixture.doCleanups()


class RevisedPairTests(Historical.TrustedFixtureTests):
    def setUp(self):
        self.enterContext(Revised.synthetic_freeze())
        self.enterContext(mock.patch.object(Bridge, 'identity_module', return_value=Identity))
        self.enterContext(mock.patch.object(Report.H, 'baseline_module', return_value=Bridge))
        self.a = Historical.numerical_run('r6x188')
        self.b = Historical.numerical_run('native197-off', offset=4)
        self.configure('loaded0')

    def configure(self, state):
        for run, arm, selected in ((self.a, 'r6x-unpatched', 'absent'), (self.b, 'r6x-steer', state)):
            plan = run['plan']
            binding = Revised.PlanningTests().binding(arm, selected)
            plan['selection'] = dict(arm=arm, steering=selected)
            plan['binding'] = binding
            checked = plan['spec']['baseline_identity']
            checked.update(identity=Identity.checked_identity(arm), runtime=Revised.runtime(arm))
            plan['spec']['expected_effective_config'] = {
                'model_config.max_model_len': 1048576,
                'additional_config.ds41_steering': dict(present=selected not in ('absent', 'unloaded'),
                    value=None if selected in ('absent', 'unloaded') else
                    copy.deepcopy(Http.owner.FIXED_ENGINE_KWARGS['additional_config']['ds41_steering']))}
            neutral = dict(plan['spec']['expected_effective_config'],
                **{'additional_config.ds41_steering': dict(present=False, value=None)})
            loaded = dict(plan['spec']['expected_effective_config'],
                **{'additional_config.ds41_steering': dict(present=True,
                    value=copy.deepcopy(Http.owner.FIXED_ENGINE_KWARGS['additional_config']['ds41_steering']))})
            binding['effective_value'] = dict(off=neutral, loaded_zero=loaded, active=loaded)
            for row in run['ready']['reports']:
                row['steering_profile'] = None if selected in ('absent', 'unloaded') else {
                    'name': Identity.PROFILE_NAME, 'artifact_sha256': Identity.PROFILE_SHA256}
        Identity.REVISED_FREEZE['common_recipe_sha256'] = Identity.digest(
            Identity.canonical(Bridge.common_recipe(self.a['plan']['binding']['profile_value'])))

    def compare(self):
        with mock.patch.object(Report, 'read_drained', side_effect=[self.a, self.b]):
            return Report.compare_pair('/synthetic/u', '/synthetic/s')

    def test_each_fixed_s_state_is_truthful_descriptive_comparison_not_old_pair(self):
        for _, state in SELECTIONS[1:]:
            self.configure(state)
            with self.subTest(state=state):
                value = self.compare()
                self.assertEqual(value['schema'], 'r6x-baseline-pair-report/1')
                self.assertEqual(set(value['arms']), {'R6X_UNPATCHED', 'R6X_STEER'})
                self.assertEqual(value['steering'], {'unpatched': 'absent', 'steer': state})
                self.assertEqual(value['performance'], 'INCONCLUSIVE')
                self.assertIsNone(value['paired_boot_ci95'])
                self.assertNotIn('a188', value['paired_changes'][0])
                self.assertNotIn('b197_off', value['paired_changes'][0])
                self.assertIn('r6x_unpatched', value['paired_changes'][0])

    def test_exact_independently_frozen_effective_bytes_for_every_state(self):
        raw = (ROOT / 'tests/fixtures/r6x-expected-effective-config.json').read_bytes()
        self.assertEqual(Identity.digest(raw), Bridge.EFFECTIVE_SHA256)
        effective = Identity.json_bytes(raw)
        self.assertEqual(set(effective['off']), Http.owner.EFFECTIVE_KEYS)
        for _, state in SELECTIONS[1:]:
            self.configure(state)
            for run in (self.a, self.b):
                plan = run['plan']
                plan['binding']['effective_value'] = copy.deepcopy(effective)
                mode = 'off' if plan['selection']['steering'] in ('absent', 'unloaded') else 'loaded_zero'
                plan['spec']['expected_effective_config'] = copy.deepcopy(effective[mode])
            with self.subTest(state=state):
                self.assertEqual(self.compare()['performance'], 'INCONCLUSIVE')
        self.a['plan']['spec']['expected_effective_config']['additional_config.ds41_steering'] = None
        with self.assertRaisesRegex(RuntimeError, 'unpatched pair has steering'):
            self.compare()

    def test_mixed_identities_state_recipe_runtime_and_effective_drift_rejected(self):
        changes = [lambda a,b: b['plan']['selection'].update(arm='native197-off'),
            lambda a,b: a['plan']['selection'].update(steering='unloaded'),
            lambda a,b: b['plan']['selection'].update(steering='loaded5'),
            lambda a,b: b['plan']['spec']['baseline_identity']['identity'].update(source_head='f'*40),
            lambda a,b: b['plan']['binding']['profile_value']['serve_args'].extend(['--capacity', '2']),
            lambda a,b: b['plan']['spec']['baseline_identity']['runtime'].update(torch_version='foreign'),
            lambda a,b: b['plan']['spec']['expected_effective_config'].update(**{'model_config.max_model_len': 2}),
            lambda a,b: b['plan']['spec']['expected_effective_config'].update(**{'additional_config.ds41_steering': {}}),
            lambda a,b: b['evidence'].update(started_utc=1100),
            lambda a,b: b['context']['ranks'][0].update(container_id=a['context']['ranks'][0]['container_id'])]
        original = copy.deepcopy((self.a, self.b))
        for change in changes:
            self.a, self.b = copy.deepcopy(original)
            change(self.a, self.b)
            with self.subTest(change=change), self.assertRaises((ValueError, RuntimeError)):
                self.compare()

    def test_retained_plan_uses_actual_revised_profile_not_legacy_profile_pin(self):
        from types import SimpleNamespace
        plan = self.a['plan']
        plan.update(schema_version=6, kind='http_service')
        binding = plan['binding']
        Identity.REVISED_FREEZE['profile_sha256']['r6x-unpatched'] = '8' * 64
        Identity.REVISED_FREEZE['effective_sha256'] = '9' * 64
        for key, sha in (('profile', '8'*64), ('nodes', '7'*64), ('effective_config', '9'*64)):
            binding[key] = dict(path='/synthetic/' + key, sha256=sha)
        binding['http']['baseline_inputs'] = {key: binding[key] for key in ('profile','nodes','effective_config')}
        manifest = dict(http_service=binding['http'], candidate=binding['candidate'],
                        jobs=[dict(selection=plan['selection'])])
        checked = binding['baseline_identity']
        values = {binding[key]['path']: binding[projection] for key, projection in
                  (('profile','profile_value'), ('nodes','nodes_value'), ('effective_config','effective_value'))}
        with mock.patch.object(Bridge, 'validate_jobs'), \
             mock.patch.object(Report, '_attempt_manifest', return_value=manifest), \
             mock.patch.object(Http.owner, 'pinned_json', side_effect=lambda pin: values[pin['path']]), \
             mock.patch.object(Identity, 'validate_binding', return_value=checked), \
             mock.patch.object(Bridge, 'http_plan', return_value=plan):
            Report._fixed_plan(plan, SimpleNamespace(manifest=manifest), Bridge, {})
            binding['profile']['sha256'] = Bridge.PROFILE_SHA256
            with self.assertRaisesRegex(RuntimeError, 'reviewed normal profile'):
                Report._fixed_plan(plan, SimpleNamespace(manifest=manifest), Bridge, {})

    def test_unbound_revised_pair_never_returns_a_report(self):
        Identity.REVISED_FREEZE['common_runtime_sha256'] = 'UNBOUND'
        with self.assertRaisesRegex(ValueError, 'UNBOUND'):
            self.compare()

    def test_revised_c1_c4_still_count_as_only_one_boot_pair(self):
        a, b = copy.deepcopy(self.a), copy.deepcopy(self.b)
        a['plan']['client']['legacy_case']['case_id'] = 'warm4k_c4'
        b['plan']['client']['legacy_case']['case_id'] = 'warm4k_c4'
        values = [dict(scope='FIXED_C1_THEN_C4_SAME_BOOT', cases=[self.a,a], lineage=self.a['lineage']),
                  dict(scope='FIXED_C1_THEN_C4_SAME_BOOT', cases=[self.b,b], lineage=self.b['lineage'])]
        with mock.patch.object(Report, 'read_drained', side_effect=values):
            result = Report.compare_pair('/synthetic/u', '/synthetic/s')
        self.assertEqual(result['schema'], 'r6x-baseline-batch-pair-report/1')
        self.assertEqual(result['same_boot_cases'], 2)
        self.assertEqual(result['independent_boot_pairs'], 1)
        self.assertEqual(set(result['lineage']), {'R6X_UNPATCHED','R6X_STEER'})
        self.assertTrue(all(case['comparison'] == result['comparison'] for case in result['cases']))


if __name__ == '__main__':
    unittest.main()
