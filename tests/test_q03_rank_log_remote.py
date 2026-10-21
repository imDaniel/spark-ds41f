"""Independent CPU-only remote log epoch contract probes; never runs commands."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import signal
import time
import subprocess
import tempfile
import unittest
from unittest import mock
import uuid

ROOT = Path(os.environ.get('Q03_LOG_ROOT', Path(__file__).resolve().parents[1]))

def load(name):
    spec = importlib.util.spec_from_file_location('independent_' + name, ROOT / 'scripts' / (name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

class RemoteLogEpochFaults(unittest.TestCase):
    def setUp(self):
        self.r = load('q03_remote_operation')
        self.temp = tempfile.TemporaryDirectory(prefix='q03-log-fault-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for target in ('Popen', 'run', 'check_output', 'check_call'):
            p = mock.patch.object(subprocess, target, side_effect=AssertionError('CPU-only probe forbids external process'))
            p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(socket, 'create_connection', side_effect=AssertionError('CPU-only probe forbids network'))
        p.start(); self.addCleanup(p.stop)

    def fixture(self, argv=None, **changes):
        window, operation, epoch = [uuid.uuid4().hex for _ in range(3)]
        command = self.r.bound_command(['docker', 'logs', '--timestamps', '--', '1' * 64]) if argv is None else argv
        directory = self.r.create_operation(self.root, window, operation, command, str(self.root), 15, epoch)
        manifest, sha = self.r.read_manifest(directory)
        receipt = dict(window=window, operation=operation, manifest_sha256=sha, group_quiescent=True,
                       exit_code=1, cancelled=False, timed_out=False, finished_epoch=1)
        receipt.update(changes)
        self.r.atomic(directory / 'receipt.json', receipt)
        (directory / 'output.log').write_bytes(b'raw\x00stdout\xff\n')
        (directory / 'error.log').write_bytes(b'raw\x80stderr\n')
        return window, operation, epoch, directory

    def test_quiescent_failed_exact_log_read_can_fence_without_claiming_success(self):
        for failure in ({'exit_code': 1}, {'exit_code': -25}, {'exit_code': -9, 'timed_out': True},
                        {'exit_code': -15, 'cancelled': True}):
            with self.subTest(failure=failure):
                window, op, epoch, directory = self.fixture(**failure)
                before = self.r.status(self.root, window, op)
                self.assertFalse(before['safe_to_overlap'])
                self.assertFalse(before['action_succeeded'])
                sealed = self.r.seal_epoch(self.root, window, epoch)
                self.assertTrue(sealed['safe_to_overlap'])
                after = self.r.status(self.root, window, op)
                self.assertFalse(after['action_succeeded'])
                self.assertEqual(after['exit_code'], failure['exit_code'])

    def test_near_match_mutations_do_not_acquire_readonly_failure_permission(self):
        variants = [
            ['docker', 'logs', '--timestamps', '--', 'container-name'],
            ['docker', 'logs', '--timestamps', '--', '1' * 12],
            ['docker', 'logs', '--timestamps', '--', 'A' * 64],
            ['docker', 'logs', '--timestamps', '--', '1' * 64, '2' * 64],
            ['docker', 'logs', '--timestamps', '--follow', '--', '1' * 64],
            ['docker', 'logs', '--tail', '1', '--timestamps', '--', '1' * 64],
            ['docker', 'stop', '--', '1' * 64],
            ['docker', 'rm', '--', '1' * 64],
            ['docker', '--host', 'tcp://foreign', 'logs', '--timestamps', '--', '1' * 64],
        ]
        for argv in variants:
            with self.subTest(argv=argv):
                window, op, epoch, directory = self.fixture(argv=self.r.bound_command(argv))
                self.assertFalse(self.r.seal_epoch(self.root, window, epoch)['safe_to_overlap'])
        window, op, epoch, directory = self.fixture(argv=['docker', 'logs', '--timestamps', '--', '1' * 64])
        self.assertFalse(self.r.seal_epoch(self.root, window, epoch)['safe_to_overlap'])

    def test_log_reader_without_positive_group_termination_never_fences(self):
        window, op, epoch, directory = self.fixture(group_quiescent=False)
        with self.assertRaises(RuntimeError):
            self.r.seal_epoch(self.root, window, epoch)
        (directory / 'receipt.json').unlink()
        result = self.r.seal_epoch(self.root, window, epoch, alive=lambda ident: False)
        self.assertFalse(result['safe_to_overlap'])
        self.assertEqual(result['operations'][0]['status'], 'uncertain')

    def test_raw_binary_streams_are_exact_and_hash_bound(self):
        window, op, epoch, directory = self.fixture(exit_code=0)
        value = self.r.status(self.root, window, op)
        import base64
        captured = value['log_capture']
        self.assertTrue(captured['complete'])
        for name, filename in (('stdout', 'output.log'), ('stderr', 'error.log')):
            expected = (directory / filename).read_bytes()
            stream = captured['streams'][name]
            self.assertEqual(base64.b64decode(stream['data_base64'], validate=True), expected)
            self.assertEqual(stream['bytes'], len(expected))
            self.assertEqual(stream['sha256'], hashlib.sha256(expected).hexdigest())

    def test_exact_cap_is_not_complete_even_if_process_returned_zero(self):
        self.r.LOG_STREAM_LIMIT = 64
        for name in ('output.log', 'error.log'):
            window, op, epoch, directory = self.fixture(exit_code=0)
            (directory / name).write_bytes(b'x' * 64)
            value = self.r.seal_epoch(self.root, window, epoch)
            self.assertTrue(value['safe_to_overlap'])
            self.assertFalse(value['operations'][0]['log_capture']['complete'])

    def test_missing_overlong_shared_or_aliased_logs_allow_fencing_but_never_completion(self):
        self.r.LOG_STREAM_LIMIT = 64
        for fault in ('missing', 'overlong', 'shared-writable', 'symlink'):
            with self.subTest(fault=fault):
                window, op, epoch, directory = self.fixture(exit_code=0)
                target = directory / 'output.log'
                if fault == 'missing': target.unlink()
                elif fault == 'overlong': target.write_bytes(b'x' * 65)
                elif fault == 'shared-writable': target.chmod(0o666)
                elif fault == 'symlink':
                    elsewhere = self.root / ('foreign-' + op)
                    elsewhere.write_bytes(b'not-the-owned-log')
                    target.unlink(); target.symlink_to(elsewhere)
                value = self.r.seal_epoch(self.root, window, epoch)
                self.assertTrue(value['safe_to_overlap'])
                self.assertFalse(value['operations'][0]['log_capture']['complete'])

    def test_fifo_is_refused_without_waiting_for_a_writer(self):
        window, op, epoch, directory = self.fixture(exit_code=0)
        target = directory / 'output.log'
        target.unlink(); os.mkfifo(target, 0o600)
        def timeout(signum, frame):
            raise AssertionError('log reader blocks opening FIFO before checking regular-file ownership')
        old_handler = signal.signal(signal.SIGALRM, timeout)
        signal.setitimer(signal.ITIMER_REAL, 0.25)
        try:
            value = self.r.seal_epoch(self.root, window, epoch)
            self.assertTrue(value['safe_to_overlap'])
            self.assertFalse(value['operations'][0]['log_capture']['complete'])
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_handler)

    def test_late_log_reader_launch_stays_refused_after_seal(self):
        window, op, epoch, directory = self.fixture()
        self.r.seal_epoch(self.root, window, epoch)
        with self.assertRaisesRegex(RuntimeError, 'permanently sealed'):
            self.r.create_operation(self.root, window, uuid.uuid4().hex,
                self.r.bound_command(['docker', 'logs', '--timestamps', '--', '1' * 64]), str(self.root), 15, epoch)

if __name__ == '__main__':
    unittest.main(verbosity=2)
