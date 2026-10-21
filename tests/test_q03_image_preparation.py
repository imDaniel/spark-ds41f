"""Actual preparation journal/epoch guards with external transports faked."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import q03_image_preparation as M


class ImagePreparationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.directory = self.root / 'session'
        self.directory.mkdir(mode=0o700)
        self.journal = self.directory / 'image-preparation'
        self.journal.mkdir(mode=0o700)
        self.p = object.__new__(M.Preparation)
        self.p.root, self.p.recovery, self.p.end = self.journal, False, time.time() + 100
        self.requested, self.held, self.drift = False, True, False
        self.session = SimpleNamespace(directory=self.directory, manifest={'independent_root': str(self.root),
            'image_preparation': {'binding': 'fixed'}}, capsule={'window_id': 'a' * 32,
            'core_process': {'pid': 999999, 'start_ticks': 'synthetic'}, 'recovery_source_sha256': {}},
            owns_hold=lambda: self.held, requested=lambda: self.requested,
            ops=SimpleNamespace(recovery_sources=lambda: {'changed': 'x'} if self.drift else {},
                read_json=lambda path: {'phase': 'image-preparation', 'active_attempt': None}))
        self.p.session = self.session
        self.p.helper = SimpleNamespace(bound_command=lambda argv: ['bounded', *argv])
        M.exclusive_json(self.journal / 'binding.json', self.session.manifest['image_preparation'])
        patch = mock.patch('q03_window_watchdog.process_alive', return_value=True)
        patch.start(); self.addCleanup(patch.stop)
        for name in ('run', 'Popen', 'check_output', 'check_call'):
            patch = mock.patch('subprocess.' + name, side_effect=AssertionError('real process forbidden'))
            patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(M.time, 'sleep', return_value=None)
        patch.start(); self.addCleanup(patch.stop)

    def test_intent_precedes_launch_and_completed_load_is_sealed(self):
        calls = []
        def rpc(record, action, extra=()):
            calls.append(action)
            self.assertEqual(json.loads((self.journal / ('intent-' + record['operation'] + '.json')).read_bytes()), record)
            if action == 'launch': return {'status': 'running'}
            if action == 'status': return {'status': 'terminal', 'action_succeeded': True}
            return {'safe_to_overlap': True}
        self.p.rpc = rpc
        self.p.mutate(2, 'load', ['fixed', 'load'], 600)
        self.assertEqual(calls, ['launch', 'status', 'seal'])
        self.assertEqual(len(list(self.journal.glob('terminal-*.json'))), 1)

    def test_request_after_dispatch_leaves_recovery_visible_intent_and_drain_ignores_request(self):
        calls = []
        def rpc(record, action, extra=()):
            calls.append(action)
            if action == 'launch':
                self.requested = True
                return {'status': 'running'}
            return {'safe_to_overlap': True}
        self.p.rpc = rpc
        with self.assertRaisesRegex(RuntimeError, 'cluster requested'):
            self.p.mutate(2, 'load', ['fixed', 'load'], 600)
        self.assertEqual(calls, ['launch'])
        self.assertEqual(len(list(self.journal.glob('intent-*.json'))), 1)
        self.p.recovery = True
        self.assertTrue(self.p.quiesce()['positive_remote_epoch_drain'])
        self.assertEqual(calls, ['launch', 'seal'])

    def test_failed_or_lost_load_supervisor_never_proves_drain(self):
        for response in ({'status': 'uncertain', 'state': {'supervisor': {'pid': 1}}},
                         {'status': 'terminal', 'action_succeeded': False}):
            self.p.rpc = lambda *args: response
            with self.subTest(response=response), self.assertRaises(RuntimeError):
                self.p.mutate(1, 'load', ['fixed', 'load'], 600)
        self.p.rpc = lambda *args: {'sealed': True, 'safe_to_overlap': False,
                                  'operations': [{'status': 'terminal', 'safe_to_overlap': False}]}
        self.p.recovery = True
        with self.assertRaisesRegex(RuntimeError, 'retain hold'):
            self.p.quiesce()

    def test_drain_fences_delayed_launch_and_waits_for_running_cli_without_cancelling(self):
        calls = []
        responses = iter([{'sealed': True, 'safe_to_overlap': False,
                           'operations': [{'status': 'running'}]}, {'safe_to_overlap': True}])
        self.p.rpc = lambda record, action: calls.append(action) or next(responses)
        self.p.drain({'epoch': 'b' * 32})
        self.assertEqual(calls, ['seal', 'seal'])

    def test_source_drift_foreign_hold_or_dead_core_refuse_new_dispatch(self):
        for field in ('drift', 'held'):
            setattr(self, field, field == 'drift')
            with self.subTest(field=field), self.assertRaises(RuntimeError): self.p.check()
            self.drift, self.held = False, True
        with mock.patch('q03_window_watchdog.process_alive', return_value=False), self.assertRaises(RuntimeError):
            self.p.check()

    def test_changed_retained_binding_refuses_recovery(self):
        self.session.manifest['image_preparation'] = {'binding': 'changed'}
        self.p.recovery = True
        with self.assertRaisesRegex(RuntimeError, 'binding changed'): self.p.quiesce()

    def test_preparation_cannot_be_started_as_an_unowned_job_between_held_attempts(self):
        for state in ({'phase': 'held-ready', 'active_attempt': None},
                      {'phase': 'image-preparation', 'active_attempt': {'attempt_id': 'foreign'}}):
            self.session.ops.read_json = lambda path: state
            with self.subTest(state=state), self.assertRaisesRegex(RuntimeError, 'core-owned stage'):
                self.p.check()

    def test_original_images_and_tags_must_all_survive(self):
        old = [{'Id': 'original', 'RepoTags': ['original:fixed']}]
        M.preserve_images(old, old + [{'Id': M.IMAGE, 'RepoTags': []}])
        for after in ([], [{'Id': 'original', 'RepoTags': ['rewritten:tag']}],
                      old + [{'Id': 'unrelated', 'RepoTags': []}]):
            with self.subTest(after=after), self.assertRaises(RuntimeError): M.preserve_images(old, after)

    def test_unknown_preparation_blocks_registered_original_restore(self):
        import q03_window as core
        self.session.state = {'active_process': None}
        self.session.update = lambda **changes: self.session.state.update(changes)
        self.session.ops.terminate_owned_process = lambda record: None
        self.session.ops.image_preparation = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('UNKNOWN load'))
        with mock.patch.object(core, 'transition_original') as original:
            with self.assertRaisesRegex(RuntimeError, 'UNKNOWN load'): core.restore(self.session)
            original.assert_not_called()

    def test_real_outer_preparation_order_and_failure_short_circuit(self):
        # Exercise the existing real lifetime/hold/watchdog/restore orchestration.
        # Only external host/child operations and the later held workload are
        # inert here; binding and remote-epoch faults are tested separately.
        sys.path.insert(0, str(ROOT / 'tests'))
        import test_q03_held_window as held_tests
        held_tests.HeldCoreContracts.setUpClass()
        for fail in (False, True):
            fixture = held_tests.HeldCoreContracts('test_actual_outer_restores_once_after_pass_and_close')
            fixture.setUp()
            try:
                fixture.manifest.update(image_preparation={}, owner_model={}, jobs=[{
                    'kind': 'owner_model', 'timeout_seconds': 120,
                    'selection': {'mode': 'off', 'steering': 'off'}}])
                events = fixture.ops.events
                fixture.ops.validate_host = lambda manifest, prepare_images=False: events.append(
                    'validate-preparation' if prepare_images else 'validate-all-four')
                fixture.ops.cleanup_owner = lambda session: events.append('cleanup-owner')
                def preparation(session, quiesce=False):
                    if quiesce:
                        events.append('preparation-drained')
                    else:
                        self.assertIn('stop:1', events)
                        self.assertEqual(session.state['phase'], 'image-preparation')
                        events.append('preparation-run')
                        if fail: raise RuntimeError('preparation rejected')
                fixture.ops.image_preparation = preparation
                def later(session, core):
                    self.assertLess(events.index('preparation-drained'), events.index('validate-all-four'))
                    events.append('held-entered')
                with mock.patch.object(fixture.core, 'validate_layout', return_value=None), \
                     mock.patch('q03_held_window.held_loop', side_effect=later):
                    if fail:
                        with self.assertRaisesRegex(RuntimeError, 'preparation rejected'):
                            fixture.core.run_session(fixture.manifest, fixture.directory, fixture.ops)
                        self.assertNotIn('held-entered', events)
                    else:
                        fixture.core.run_session(fixture.manifest, fixture.directory, fixture.ops)
                        self.assertIn('held-entered', events)
                self.assertEqual(events.count('restore:start'), 1)
                self.assertLess(events.index('preparation-drained'), events.index('restore:start'))
            finally:
                fixture.doCleanups()


if __name__ == '__main__': unittest.main()
