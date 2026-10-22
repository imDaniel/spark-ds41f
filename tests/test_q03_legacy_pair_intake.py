"""Synthetic CPU regression for the retained first-attempt manifest contract.

No runtime, model, remote, GPU, or full field-packet validation is performed.
"""
import copy
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
import q03_legacy_pair_report as P
import test_q03_baseline_service as BridgeTests


class AttemptIntakeTests(unittest.TestCase):
    def setUp(self):
        helper = BridgeTests.Tests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        self.B = BridgeTests.B
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        binding = helper.binding('r6x188', 'loaded0')
        fixed = {}
        for key, projection in (('profile', 'profile_value'), ('nodes', 'nodes_value'),
                                ('effective_config', 'effective_value')):
            path = root / (key + '.json')
            raw = P.canonical(binding[projection]); path.write_bytes(raw)
            binding[key] = fixed[key] = {'path': str(path), 'sha256': P.digest(raw)}
        binding['http']['baseline_inputs'] = fixed
        manifest = helper.manifest()
        manifest['jobs'][0]['selection'] = binding['selection']
        manifest.update(candidate=copy.deepcopy(binding['candidate']),
                        http_service=copy.deepcopy(binding['http']), held_window={'schema_version': 1})
        self.session = SimpleNamespace(manifest=manifest, directory=root,
            capsule={'window_id': 'a' * 32}, state={'phase': 'closed-stopped'})
        raw = P.canonical(self.session.capsule); (root / 'capsule.json').write_bytes(raw)
        self.descriptor = dict(schema_version=1, window_id='a' * 32, attempt_id='b' * 32,
            capsule_sha256=P.digest(raw), previous_receipt_sha256=None, candidate_updates={},
            jobs=copy.deepcopy(manifest['jobs']))
        attempt = P.derive_attempt_manifest(self.session, 'b' * 32,
                                           self.descriptor['jobs'], self.descriptor['candidate_updates'])
        binding['candidate'] = attempt['candidate']
        legacy = patch.object(self.B, 'legacy_module', return_value=SimpleNamespace(client_plan=lambda b, s: {'selection': s}))
        legacy.start(); self.addCleanup(legacy.stop)
        self.plan = self.B.http_plan(binding, 'a' * 32, 'b' * 32, binding['selection'])
        self.descriptor['command_plan_sha256'] = P.digest(P.canonical([['http_service', self.plan]]))
        self.bridge = SimpleNamespace(PROFILE_SHA256=fixed['profile']['sha256'],
            EFFECTIVE_SHA256=fixed['effective_config']['sha256'], validate_jobs=self.B.validate_jobs,
            identity_module=self.B.identity_module, http_plan=self.B.http_plan)

    def check(self, plan=None, descriptor=None):
        return P._fixed_plan(self.plan if plan is None else plan, self.session, self.bridge,
                             self.descriptor if descriptor is None else descriptor)

    def rebound(self, plan):
        return dict(self.descriptor, command_plan_sha256=P.digest(P.canonical([['http_service', plan]])))

    def test_real_outer_to_child_derivation_is_read_only(self):
        before = copy.deepcopy((self.plan, self.session.manifest, self.descriptor, self.session.state))
        for key in ('cache_path', 'output_path'):
            self.assertEqual(self.plan['binding']['candidate'][key],
                str(Path(self.session.manifest['candidate'][key]) / self.plan['attempt_id']))
            self.assertNotEqual(self.plan['binding']['candidate'][key], self.session.manifest['candidate'][key])
        with patch.object(subprocess, 'run', side_effect=AssertionError('no process')), \
             patch.object(socket, 'create_connection', side_effect=AssertionError('no network')):
            self.check()
        self.assertEqual(before, (self.plan, self.session.manifest, self.descriptor, self.session.state))

    def test_outer_wrong_attempt_cross_window_or_nested_path_is_not_normalized(self):
        for key in ('cache_path', 'output_path'):
            outer = self.session.manifest['candidate'][key]
            for wrong in (outer, outer + '/' + 'c' * 32, '/other-window/' + 'b' * 32,
                          outer + '/' + 'b' * 32 + '/' + 'b' * 32, outer + '/../' + 'b' * 32):
                plan = copy.deepcopy(self.plan); plan['binding']['candidate'][key] = wrong
                with self.subTest(key=key, wrong=wrong), self.assertRaisesRegex(RuntimeError, 'derived attempt'):
                    self.check(plan, self.rebound(plan))

    def test_every_nonpath_candidate_field_remains_exact(self):
        for key in self.plan['binding']['candidate']:
            if key in ('cache_path', 'output_path'): continue
            plan = copy.deepcopy(self.plan); plan['binding']['candidate'][key] += '-drift'
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, 'derived attempt'):
                self.check(plan, self.rebound(plan))
        plan = copy.deepcopy(self.plan); plan['binding']['candidate']['extra'] = True
        with self.assertRaisesRegex(RuntimeError, 'derived attempt'): self.check(plan, self.rebound(plan))

    def test_descriptor_identity_schema_predecessor_and_updates_fail_closed(self):
        changes = [lambda d: d.update(schema_version=True), lambda d: d.update(extra=True),
            lambda d: d.pop('candidate_updates'), lambda d: d.update(attempt_id='c' * 32),
            lambda d: d.update(attempt_id='../' + 'b' * 32), lambda d: d.update(window_id='c' * 32),
            lambda d: d.update(capsule_sha256='c' * 64), lambda d: d.update(previous_receipt_sha256='c' * 64),
            lambda d: d.update(candidate_updates=[]), lambda d: d.update(candidate_updates={'cache_path': '/foreign'}),
            lambda d: d.update(candidate_updates={'g1_tests': {}}), lambda d: d.update(command_plan_sha256='c' * 64)]
        for change in changes:
            descriptor = copy.deepcopy(self.descriptor); change(descriptor)
            with self.subTest(change=change), self.assertRaises((RuntimeError, ValueError, KeyError)):
                self.check(descriptor=descriptor)

    def test_descriptor_jobs_are_typed_exact_not_just_equal(self):
        changes = [lambda j: j.update(timeout_seconds=7200.0), lambda j: j.update(timeout_seconds=True),
            lambda j: j.update(timeout_seconds=7199), lambda j: j.update(extra=True),
            lambda j: j['selection'].update(arm='native197-off'), lambda j: j['selection'].update(steering='loaded1')]
        for change in changes:
            descriptor = copy.deepcopy(self.descriptor); change(descriptor['jobs'][0])
            with self.subTest(change=change), self.assertRaises((RuntimeError, ValueError)):
                self.check(descriptor=descriptor)

    def test_other_http_resources_and_rendered_commands_still_fail_closed(self):
        changes = [lambda p: p['binding']['http'].update(api_port=18001),
            lambda p: p['binding']['profile_value']['serve_args'].__setitem__(1, 'changed'),
            lambda p: p['ranks'][0]['python_argv'].append('--unapproved'),
            lambda p: p.update(window_id='c' * 32), lambda p: p.update(attempt_id='c' * 32)]
        for change in changes:
            plan = copy.deepcopy(self.plan); change(plan)
            with self.subTest(change=change), self.assertRaises((RuntimeError, ValueError)):
                self.check(plan, self.rebound(plan))


if __name__ == '__main__':
    unittest.main()
