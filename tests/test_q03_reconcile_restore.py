"""Completed-build recovery CPU fakes, never live service restoration."""
import contextlib
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('q03_restore_test', ROOT / 'scripts/q03_reconcile_restore.py')
M = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = M
SPEC.loader.exec_module(M)


class CompletedBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.window = 'a' * 32
        self.plan = SimpleNamespace(session_dir=str(self.root), window_id=self.window)
        self.journal = {'official_build_completed': True, 'quiesced': True,
                        'build_group': {'pid': 424242}, 'builder_id': 'b' * 64, 'smoke_id': 'c' * 64}
        self.fence = self.root / 'fence.json'
        self.fence.write_text(json.dumps({'sealed': True, 'window_id': self.window}))
        self.journal_path = self.root / 'journal.json'
        self.journal_path.write_text(json.dumps(self.journal))
        self.adapter = SimpleNamespace(journal=self.journal, fence=self.fence, journal_path=self.journal_path,
            builder_id='b' * 64, name='synthetic-smoke', plan={'container': 'synthetic-builder', 'volume': 'synthetic-volume'})
        self.group = []
        self.names = []
        self.ids = []
        self.volume = {'Name': 'synthetic-volume', 'Driver': 'local', 'Options': {}}
        self.modules = {'q03_build_adapter': SimpleNamespace(Adapter=lambda path: self.adapter, group_members=lambda pid: self.group)}
        self.ops = SimpleNamespace(run=self.run_command)
        for name in ('Popen', 'run', 'check_call', 'check_output'):
            p = mock.patch.object(subprocess, name, side_effect=AssertionError('real process forbidden'))
            p.start()
            self.addCleanup(p.stop)

    def run_command(self, argv):
        if argv[-1] == '{{.Names}}':
            return SimpleNamespace(stdout='\n'.join(self.names))
        if argv[-1] == '{{.ID}}':
            return SimpleNamespace(stdout='\n'.join(self.ids))
        if argv[-3:] == ['volume', 'inspect', 'synthetic-volume']:
            return SimpleNamespace(stdout=json.dumps([self.volume]))
        raise AssertionError('unexpected command ' + repr(argv))

    def verify(self):
        return M.verify_completed_build(self.plan, self.modules, self.ops)

    def test_only_normal_completed_quiesced_sealed_and_absent_is_admitted(self):
        result = self.verify()
        self.assertTrue(result['already_quiesced'])
        for key, value in [('official_build_completed', False), ('official_build_completed', 1),
                           ('quiesced', False), ('quiesced', 'true'), ('builder_id', 'foreign'), ('smoke_id', '')]:
            old = self.journal[key]
            self.journal[key] = value
            try:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self.verify()
            finally:
                self.journal[key] = old
        self.group = [424243]
        with self.assertRaisesRegex(ValueError, 'group'):
            self.verify()
        self.group = []
        for attr, value in [('names', ['synthetic-builder']), ('names', ['synthetic-smoke']),
                            ('ids', ['b' * 64]), ('ids', ['c' * 64])]:
            setattr(self, attr, value)
            with self.subTest(attr=attr, value=value), self.assertRaisesRegex(ValueError, 'absent'):
                self.verify()
            setattr(self, attr, [])
        self.volume['Name'] = 'foreign'
        with self.assertRaisesRegex(ValueError, 'volume'):
            self.verify()

    def test_unknown_daemon_and_wrong_fence_do_not_become_absence(self):
        self.ops.run = mock.Mock(side_effect=RuntimeError('daemon unknown'))
        with self.assertRaisesRegex(RuntimeError, 'unknown'):
            self.verify()
        self.ops.run = self.run_command
        self.fence.write_text(json.dumps({'sealed': True, 'window_id': 'foreign'}))
        with self.assertRaisesRegex(ValueError, 'fence'):
            self.verify()

    def test_old_core_or_watchdog_alive_or_foreign_receipt_blocks_takeover(self):
        capsule = {'window_id': self.window, 'core_process': {'pid': 42, 'start_ticks': 'old'}}
        raw = json.dumps(capsule).encode()
        (self.root / 'capsule.json').write_bytes(raw)
        ready = {'window_id': self.window, 'capsule_sha256': hashlib.sha256(raw).hexdigest(),
                 'process': {'pid': 43, 'start_ticks': 'old-watchdog'}}
        (self.root / 'watchdog.ready.json').write_text(json.dumps(ready))
        for states in ([True], [False, True]):
            modules = {'q03_window_watchdog': SimpleNamespace(process_alive=mock.Mock(side_effect=states))}
            with self.subTest(states=states), mock.patch.object(M, 'recorded_process_alive', side_effect=states), self.assertRaisesRegex(ValueError, 'alive'):
                M.require_terminal(modules, capsule, self.root)
        ready['capsule_sha256'] = '0' * 64
        (self.root / 'watchdog.ready.json').write_text(json.dumps(ready))
        with mock.patch.object(M, 'recorded_process_alive', return_value=False), self.assertRaisesRegex(ValueError, 'identity'):
            M.require_terminal({'q03_window_watchdog': SimpleNamespace(process_alive=lambda identity: False)}, capsule, self.root)
    def test_unknown_process_reads_refuse_and_pid_reuse_never_signals(self):
        identity = {'pid': 424242, 'start_ticks': '123'}
        for error in (PermissionError('unknown'), OSError('io failure')):
            with self.subTest(error=error), mock.patch.object(Path, 'read_text', side_effect=error), self.assertRaises(OSError):
                M.recorded_process_alive(identity)
        with mock.patch.object(Path, 'read_text', side_effect=FileNotFoundError):
            self.assertFalse(M.recorded_process_alive(identity))
        for state, ticks, expected in [('S', '123', True), ('S', '456', False), ('Z', '123', False)]:
            fields = [state] + ['0'] * 18 + [ticks]
            with self.subTest(state=state, ticks=ticks), mock.patch.object(Path, 'read_text', return_value='424242 (fake) ' + ' '.join(fields)):
                self.assertEqual(M.recorded_process_alive(identity), expected)
        with mock.patch.object(Path, 'read_text', return_value='malformed'), self.assertRaises((ValueError, IndexError)):
            M.recorded_process_alive(identity)


class RecoveryOrderingTests(unittest.TestCase):
    def setUp(self):
        import threading
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.old_dir = self.root / 'old'
        self.old_dir.mkdir()
        for name in ('session.lock', 'watchdog.lock'):
            (self.old_dir / name).write_text('')
        self.destination = self.root / 'new-journal'
        self.capsule = {'window_id': 'a' * 32, 'recovery_timeout_s': 300, 'manifest': {'independent_root': str(self.root),
                        'preflight': {'protected_roots': [str(self.root / 'protected')],
                                      'nodes': [{'name': 'node' + str(i), 'rank': i} for i in range(4)]}}}
        (self.old_dir / 'capsule.json').write_text(json.dumps(self.capsule))
        (self.old_dir / 'state.json').write_text('original state bytes')
        self.events, self.commands = [], []
        self.failure, self.owned = None, True
        self.plan = SimpleNamespace(session_dir=str(self.old_dir), capsule_sha256='b' * 64,
                                    window_id='a' * 32)
        outer = self
        class FakeOps:
            @contextlib.contextmanager
            def acquire_gpu_lock(inner):
                outer.events.append('lock')
                yield
                outer.events.append('unlock')
            def terminate_owned_process(inner, record):
                outer.events.append('terminate-record')
            def cleanup_candidates(inner, session):
                outer.events.append('cleanup')
            def execute(inner, session, argv, **kwargs):
                action, epoch = argv[argv.index('--action') + 1], argv[argv.index('--epoch') + 1]
                outer.commands.append((action, epoch))
                if action != 'quiesce':
                    assert session.state['transition_epochs'][-1] == {'epoch': epoch, 'action': action}
                outer.events.append(action)
                import uuid
                path = session.directory / ('command-' + uuid.uuid4().hex + '.log')
                report = {'window_id': outer.plan.window_id, 'epoch': epoch, 'safe_to_overlap': True,
                          'sealed_epochs': [{'node': 'node' + str(i), 'window': outer.plan.window_id,
                                             'epoch': epoch, 'sealed': True, 'safe_to_overlap': True} for i in range(4)]}
                path.write_text(json.dumps(report))
                return {'log_path': str(path)}
            def require_receipt(inner, result, expected):
                outer.events.append('proof')
                if outer.failure == 'proof':
                    raise RuntimeError('unknown remote proof')
        self.ops = FakeOps()
        class FakeSession:
            def __init__(inner, directory, ops):
                inner.directory, inner.ops = Path(directory), ops
                inner.capsule, inner.manifest, inner.hold = outer.capsule, outer.capsule['manifest'], 'synthetic-hold'
                inner.state = {'mutation_started': True, 'transition_epochs': [{'epoch': 'c' * 32, 'action': 'stop'}]}
            def owns_hold(inner):
                return outer.owned
            def update(inner, **values):
                inner.state.update(values)
                (inner.directory / 'state.json').write_text(json.dumps(inner.state))
            def check(inner, phase):
                outer.events.append(phase)
                if outer.failure == phase:
                    raise RuntimeError('injected ' + phase)
            def beat(inner):
                outer.events.append('heartbeat')
            def release(inner):
                outer.events.append('release')
        modules = {'q03_window': SimpleNamespace(RuntimeOps=lambda: self.ops, Session=FakeSession),
                   'q03_window_watchdog': SimpleNamespace(process_identity=lambda pid: {'pid': pid, 'start_ticks': '123'})}
        original_read = M._read
        def source_read(path):
            if Path(path).name == 'q03_recovery_supervisor.py':
                # Only orchestration spawn boundary is inert. Dedicated tests exercise real supervisor.
                return b"import sys\ndef start_watchdog(*args): return sys.modules['q03_restore_test']._test_watchdog_start(*args)\n"
            return original_read(path)
        for patch in (mock.patch.object(M, '_test_watchdog_start', create=True, side_effect=lambda *args: self.events.append('watchdog-ready')),
                      mock.patch.object(M, '_read', side_effect=source_read),
                      mock.patch.object(M, 'verify_restore_plan', return_value=self.plan),
                      mock.patch.object(M, 'load_old_modules', return_value=modules),
                      mock.patch.object(M, 'require_terminal', side_effect=lambda *a: self.events.append('terminal')),
                      mock.patch.object(M, 'verify_completed_build', side_effect=lambda *a: {'normal_complete': True}),
                      mock.patch.object(M, 'adapter_command_plan', side_effect=lambda plan, action, epoch: ['ignore'] * 4 + ['--action', action, '--epoch', epoch]),
                      mock.patch.object(threading, 'Thread'),
                      mock.patch.object(subprocess, 'run', side_effect=AssertionError('real process forbidden')),
                      mock.patch.object(subprocess, 'Popen', side_effect=AssertionError('real process forbidden'))):
            patch.start()
            self.addCleanup(patch.stop)
        self.compat = ROOT / 'scripts/q03_recovery_compat.py'
        self.compat_hash = hashlib.sha256(self.compat.read_bytes()).hexdigest()

    def recover(self, apply=True):
        return M.recover_normal_complete(self.old_dir, 'b' * 64, self.destination,
                                         self.compat, self.compat_hash, apply=apply)

    def test_new_journal_epochs_and_readiness_precede_hold_release(self):
        before = {p: p.read_bytes() for p in self.old_dir.iterdir()}
        result = self.recover()
        self.assertTrue(result['restored'])
        self.assertTrue(result['hold_released'])
        self.assertLess(self.events.index('lock'), self.events.index('cleanup'))
        self.assertLess(self.events.index('heartbeat'), self.events.index('watchdog-ready'))
        self.assertLess(self.events.index('watchdog-ready'), self.events.index('quiesce'))
        self.assertLess(self.events.index('proof'), self.events.index('cleanup'))
        self.assertLess(self.events.index('restore_drained'), self.events.index('start'))
        self.assertLess(self.events.index('restored'), self.events.index('release'))
        transitions = [epoch for action, epoch in self.commands if action in ('stop', 'start')]
        self.assertEqual(len(transitions), 2)
        self.assertEqual(len(set(transitions)), 2)
        self.assertNotIn('c' * 32, transitions)
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_unknown_proof_or_failed_readiness_never_releases_hold(self):
        for stage in ('proof', 'restored'):
            self.failure = stage
            self.destination = self.root / ('new-' + stage)
            self.events.clear()
            with self.subTest(stage=stage), self.assertRaises(RuntimeError):
                self.recover()
            self.assertNotIn('release', self.events)
            state = json.loads((self.destination / 'state.json').read_text())
            self.assertEqual(state['phase'], 'recovery-failed')
            self.assertFalse(state['hold_released'])

    def test_start_active_unit_does_not_revalidate_historical_unloaded_ack(self):
        original_execute = self.ops.execute
        active = False
        started_epoch = None
        def execute(session, argv, **kwargs):
            nonlocal active, started_epoch
            action = argv[argv.index('--action') + 1]
            epoch = argv[argv.index('--epoch') + 1]
            if action == 'stop':
                active = False
            elif action == 'start':
                active, started_epoch = True, epoch
            elif action == 'quiesce' and active and epoch != started_epoch:
                raise RuntimeError('historical unloaded unit is now legitimately active')
            return original_execute(session, argv, **kwargs)
        self.ops.execute = execute
        result = self.recover()
        self.assertTrue(active)
        self.assertTrue(result['restored'])
        self.assertTrue(result['hold_released'])
        self.assertEqual(self.commands, [('quiesce', 'c' * 32),
            ('stop', result['transition_epochs'][-2]['epoch']),
            ('quiesce', result['transition_epochs'][-2]['epoch']),
            ('start', started_epoch), ('quiesce', started_epoch)])
        self.assertLess(self.events.index('restored'), self.events.index('release'))

    def test_uncertain_current_epoch_blocks_later_transition_and_release(self):
        original = self.ops.require_receipt
        def require_receipt(result, expected):
            if expected['epoch'] != 'c' * 32:
                raise RuntimeError('current epoch is uncertain')
            return original(result, expected)
        self.ops.require_receipt = require_receipt
        with self.assertRaisesRegex(RuntimeError, 'current epoch'):
            self.recover()
        self.assertIn('stop', self.events)
        self.assertNotIn('start', self.events)
        self.assertNotIn('release', self.events)
        state = json.loads((self.destination / 'state.json').read_text())
        self.assertFalse(state['hold_released'])
        self.assertEqual(state['phase'], 'recovery-failed')

    def test_retry_uses_new_journal_epochs_before_another_transition(self):
        self.failure = 'restored'
        with self.assertRaises(RuntimeError):
            self.recover()
        prior = json.loads((self.destination / 'state.json').read_text())['transition_epochs']
        self.assertEqual(len(prior), 3)
        self.failure = None
        self.commands.clear()
        self.events.clear()
        result = self.recover()
        self.assertTrue(all(epoch not in {row['epoch'] for row in prior} for action, epoch in self.commands))
        self.assertEqual(len(result['sealed_epoch_proofs']), 5)
        self.assertEqual(len(result['transition_epochs']), 5)
        self.assertEqual((self.old_dir / 'state.json').read_text(), 'original state bytes')
        self.assertLess(self.events.index('restored'), self.events.index('release'))

    def test_cached_proof_rejects_changed_bytes_identity_and_incomplete_nodes(self):
        result = self.recover()
        transition = result['transition_epochs'][0]
        proof = result['sealed_epoch_proofs'][transition['epoch']]
        nodes = ['node' + str(i) for i in range(4)]
        verify = lambda value: M.verify_sealed_epoch_proof(value, transition, self.plan.window_id,
            proof['intent_sha256'], self.destination, nodes)
        verify(proof)
        for key, value in [('epoch', 'f' * 32), ('action', 'start'), ('window_id', 'f' * 32),
                           ('intent_sha256', '0' * 64), ('receipt_sha256', '0' * 64)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                verify(dict(proof, **{key: value}))
        path = Path(proof['receipt_path'])
        original = path.read_bytes()
        for edit in ('missing', 'duplicate', 'unsafe'):
            report = json.loads(original)
            if edit == 'missing':
                report['sealed_epochs'].pop()
            elif edit == 'duplicate':
                report['sealed_epochs'][-1]['node'] = 'node0'
            else:
                report['sealed_epochs'][0]['safe_to_overlap'] = False
            path.write_text(json.dumps(report))
            changed = dict(proof, receipt_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                verify(changed)
        path.write_bytes(original)

    def test_foreign_hold_and_plan_only_never_execute(self):
        result = self.recover(apply=False)
        self.assertFalse(result['execution_enabled'])
        self.assertEqual(self.events, [])
        self.assertFalse(self.destination.exists())
        self.owned = False
        with self.assertRaisesRegex(ValueError, 'hold'):
            self.recover()
        self.assertNotIn('cleanup', self.events)
        self.assertEqual(self.commands, [])
        self.assertFalse(self.destination.exists())


class SupervisorPublicationTests(unittest.TestCase):
    def test_exclusive_publishes_complete_json_without_overwriting(self):
        spec = importlib.util.spec_from_file_location(
            'q03_supervisor_publication_test', ROOT / 'scripts/q03_recovery_supervisor.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            target = root / 'ready.json'
            original = {'ready': True, 'identity': {'pid': 123, 'start_ticks': '456'}}
            module.exclusive(target, original)
            self.assertEqual(json.loads(target.read_text()), original)
            before = target.read_bytes()
            with self.assertRaises(FileExistsError):
                module.exclusive(target, {'ready': False})
            self.assertEqual(target.read_bytes(), before)
            self.assertEqual(list(root.glob('*.tmp')), [])


if __name__ == '__main__':
    unittest.main()
