"""CPU-only fault injection; no process, service or device is launched."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('supervisor_under_test',
    Path(__file__).resolve().parents[1] / 'scripts/q03_recovery_supervisor.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class FakeOps:
    def __init__(self, health, restored_after=None):
        self.health = list(health)
        self.time = 0
        self.launches = []
        self.restored_after = restored_after
        self.foreign = False
        self.changed = False
    def now(self): return self.time
    def sleep(self, _): self.time += 1
    def process_state(self, record):
        return self.health.pop(0) if self.health else 'alive'
    def snapshot(self, _):
        done = self.restored_after is not None and self.time >= self.restored_after
        return ({'deadline_epoch': 10, 'window_id': 'window'}, {'holder': 'owner'},
                'changed' if self.changed and self.time else 'hash',
                {'restored': done, 'hold_released': done},
                None if done else {'window': 'foreign' if self.foreign else 'window', 'holder': 'owner'})
    def launch_retry(self, directory, intent, main):
        self.launches.append(main)
        return {'pid': 99, 'start_ticks': '2'}


class ReconcileWatchdogTests(unittest.TestCase):
    def test_alive_main_never_taken_over(self):
        ops = FakeOps(['alive'] * 4, restored_after=3)
        result = M.monitor(Path('/unused'), {'pid': 1, 'start_ticks': '1'}, 10, ops)
        self.assertFalse(result['retry_started'])
        self.assertEqual(ops.launches, [])

    def test_terminal_main_exactly_one_retry(self):
        ops = FakeOps(['dead', 'alive'], restored_after=2)
        result = M.monitor(Path('/unused'), {'pid': 1, 'start_ticks': '1'}, 10, ops)
        self.assertTrue(result['retry_started'])
        self.assertEqual(len(ops.launches), 1)

    def test_failed_retry_does_not_recurse(self):
        ops = FakeOps(['dead', 'dead'])
        with self.assertRaisesRegex(RuntimeError, 'single recovery retry'):
            M.monitor(Path('/unused'), {'pid': 1, 'start_ticks': '1'}, 10, ops)
        self.assertEqual(len(ops.launches), 1)

    def test_unknown_or_reused_pid_never_launches(self):
        for state in ('unknown', 'reused'):
            ops = FakeOps([state])
            with self.assertRaisesRegex(RuntimeError, 'identity uncertain'):
                M.monitor(Path('/unused'), {'pid': 1, 'start_ticks': '1'}, 10, ops)
            self.assertEqual(ops.launches, [])

    def test_foreign_hold_and_changed_intent_refuse(self):
        ops = FakeOps(['dead']); ops.foreign = True
        with self.assertRaisesRegex(RuntimeError, 'hold lost'):
            M.monitor(Path('/unused'), {}, 10, ops)
        self.assertEqual(ops.launches, [])
        ops = FakeOps(['alive']); ops.changed = True
        with self.assertRaisesRegex(RuntimeError, 'intent changed'):
            M.monitor(Path('/unused'), {}, 10, ops)
        self.assertEqual(ops.launches, [])

    def test_deadline_retains_unknown_without_retry(self):
        ops = FakeOps(['alive'] * 12)
        with self.assertRaisesRegex(RuntimeError, 'deadline reached'):
            M.monitor(Path('/unused'), {}, 10, ops)
        self.assertEqual(ops.launches, [])

    def test_terminal_restored_marker_no_restart(self):
        ops = FakeOps(['dead'], restored_after=0)
        result = M.monitor(Path('/unused'), {}, 10, ops)
        self.assertEqual(result['status'], 'restored-marker-observed')
        self.assertEqual(ops.launches, [])

    def test_release_before_final_state_write_does_not_restart(self):
        ops = FakeOps(['dead'], restored_after=0)
        original = ops.snapshot
        def snapshot(directory):
            intent, capsule, digest, state, hold = original(directory)
            state['hold_released'] = False
            return intent, capsule, digest, state, hold
        ops.snapshot = snapshot
        result = M.monitor(Path('/unused'), {}, 10, ops)
        self.assertTrue(result['hold_absent_observed'])
        self.assertEqual(ops.launches, [])

    def test_proc_errors_fail_closed(self):
        with mock.patch.object(Path, 'read_text', side_effect=PermissionError('denied')):
            self.assertEqual(M.process_state({'pid': 1, 'start_ticks': '1'}), 'unknown')
        with mock.patch.object(Path, 'read_text', side_effect=FileNotFoundError()):
            self.assertEqual(M.process_state({'pid': 1, 'start_ticks': '1'}), 'dead')

    def test_wrong_hold_refuses_before_ready_handshake(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            record = {'pid': 123, 'start_ticks': '456'}
            args = ['supervisor', '--journal-dir', str(directory), '--main-pid', '123',
                    '--main-start', '456', '--deadline-epoch', '100']
            intent = {'deadline_epoch': 100, 'window_id': 'window'}
            snapshot = (intent, {'holder': 'owner'}, 'hash', {'recovery_process': record},
                        {'window': 'foreign', 'holder': 'owner'})
            with mock.patch.object(M.sys, 'argv', args), \
                 mock.patch.object(M, 'verify_sources', return_value=(intent, {}, 'hash')), \
                 mock.patch.object(M.Ops, 'snapshot', return_value=snapshot), \
                 mock.patch.object(M, 'process_state', return_value='alive'), \
                 mock.patch.object(M, 'exclusive') as write, \
                 mock.patch.object(M, 'monitor') as monitor:
                with self.assertRaisesRegex(RuntimeError, 'hold lost before readiness'):
                    M.main()
                write.assert_not_called()
                monitor.assert_not_called()

    def test_source_hash_drift_rejected_before_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            intent = {'new_source_sha256': {name: '0' * 64 for name in
                ('q03_reconcile_restore.py', 'q03_recovery_compat.py',
                 'q03_remote_operation.py', 'q03_recovery_supervisor.py')}}
            import json
            (directory / 'intent.json').write_text(json.dumps(intent))
            with self.assertRaisesRegex(RuntimeError, 'source changed'):
                M.verify_sources(directory)


if __name__ == '__main__':
    unittest.main()
