"""Actual OwnerOps dispatch and helper epoch integration, synthetic command exit."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import q03_owner_model as O
import q03_remote_operation as R


class LogDispatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.events = []
        self.exit_code = 1
        self.dead = False
        self.ops = O.OwnerOps.__new__(O.OwnerOps)
        self.ops.plan = dict(window_id='a' * 32)
        self.ops.binding = dict(independent_root=str(self.root))
        self.ops.end = time.time() + 30
        self.ops.helper = R
        self.ops.check_authority = lambda: None
        self.ops.journal = SimpleNamespace(state={'epochs': []}, save=lambda: None,
            event=lambda kind, **fields: self.events.append((kind, fields)))
        self.ops.helper_call = self.call
        self.ops.sleep = lambda seconds: self.fail('terminal read should not poll')
        self.row = dict(container_id='b' * 64)

    def call(self, rank, action, epoch, operation=None, extra=()):
        root = self.root / str(rank)
        if action == 'launch':
            arguments = dict(zip(extra[::2], extra[1::2]))
            command = json.loads(arguments['--argv-json'])
            self.assertEqual(command, R.bound_command(['docker', 'logs', '--timestamps', '--', self.row['container_id']]))
            self.assertLessEqual(float(arguments['--timeout-seconds']), R.LOG_TIMEOUT_SECONDS)
            directory = R.create_operation(root, self.ops.plan['window_id'], operation, command,
                str(self.root), float(arguments['--timeout-seconds']), epoch)
            _, manifest_sha = R.read_manifest(directory)
            (directory / 'output.log').write_bytes(b'partial or complete stdout\xff')
            (directory / 'error.log').write_bytes(b'actual reader stderr\x00')
            if self.dead:
                R.atomic(directory / 'state.json', dict(phase='running', supervisor={'pid': 99999999, 'start_ticks': '1'}))
            else:
                R.atomic(directory / 'receipt.json', dict(window=self.ops.plan['window_id'],
                    operation=operation, manifest_sha256=manifest_sha, group_quiescent=True,
                    exit_code=self.exit_code, cancelled=False, timed_out=False, finished_epoch=time.time()))
            return R.status(root, self.ops.plan['window_id'], operation, alive=lambda record: False)
        if action == 'seal':
            return R.seal_epoch(root, self.ops.plan['window_id'], epoch, alive=lambda record: False)
        if action == 'status':
            return R.status(root, self.ops.plan['window_id'], operation, alive=lambda record: False)
        self.fail('unexpected helper action ' + action)

    def test_failed_read_seals_without_faking_success_and_fresh_read_can_retry(self):
        first = self.ops.capture_logs(0, self.row)
        self.assertFalse(first['action_succeeded'])
        self.assertEqual(first['exit_code'], 1)
        self.assertFalse(first['log_capture']['complete'])
        self.assertTrue(first['safe_to_overlap'])
        self.assertTrue(first['epoch_sealed'])
        self.ops.seal_all()
        self.exit_code = 0
        second = self.ops.capture_logs(0, self.row)
        self.assertNotEqual(first['operation'], second['operation'])
        self.assertNotEqual(first['epoch'], second['epoch'])
        self.assertTrue(second['log_capture']['complete'])
        self.assertEqual(len(self.ops.journal.state['sealed_epochs']), 2)
        for kind, fields in self.events:
            if kind in ('log-read-terminal', 'epoch-sealed'):
                self.assertNotIn('data_base64', json.dumps(fields))

    def test_dead_supervisor_does_not_create_a_retry_or_seal_proof(self):
        self.dead = True
        with self.assertRaisesRegex(RuntimeError, 'supervisor lost'):
            self.ops.capture_logs(0, self.row)
        self.assertEqual(len(self.ops.journal.state['epochs']), 1)
        with self.assertRaisesRegex(RuntimeError, 'UNKNOWN'):
            self.ops.seal_all()
        self.assertFalse(self.ops.journal.state.get('sealed_epochs'))


if __name__ == '__main__':
    unittest.main()
