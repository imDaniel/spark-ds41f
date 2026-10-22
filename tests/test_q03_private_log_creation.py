"""Linux child/file regressions for new helper logs; no Docker, SSH or service.

The executable named docker is an inert temporary Python stub. Historical logs
are never repaired: exclusive creation must refuse them byte-for-byte unchanged.
"""
import base64
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import q03_remote_operation as R

STDOUT = b'child stdout\x00\xff\n'
STDERR = b'child stderr\x80\n'


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux process and file contract')
class PrivateLogCreation(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='q03-private-logs-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.window, self.operation, self.epoch = [uuid.uuid4().hex for _ in range(3)]

    def operation_directory(self):
        return R.create_operation(self.root, self.window, self.operation,
            R.bound_command(['docker', 'logs', '--timestamps', '--', '1' * 64]),
            str(self.root), 2, self.epoch)

    def assert_no_receipt(self, directory):
        self.assertFalse((directory / 'receipt.json').exists())
        result = R.seal_epoch(self.root, self.window, self.epoch, alive=lambda record: False)
        self.assertFalse(result['safe_to_overlap'])
        self.assertEqual(result['operations'][0]['status'], 'uncertain')

    def test_real_launch_supervisor_child_and_strict_reader_under_each_umask(self):
        for mask in (0o000, 0o002, 0o022, 0o077):
            with self.subTest(umask=oct(mask)), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                independent = Path(temporary)
                authority = independent / 'authority.json'
                authority.write_text(json.dumps({'independent_root': str(independent),
                    'protected_roots': [str(self.root / 'protected')]}))
                stub = independent / 'docker'
                stub.write_text('#!' + sys.executable + '\n'
                    'import json,os,stat\n'
                    'm=os.umask(0); os.umask(m)\n'
                    'with open("child-probe.json","x") as f:\n'
                    ' json.dump({"umask":m,"streams":[{"mode":stat.S_IMODE(os.fstat(i).st_mode),'
                    '"uid":os.fstat(i).st_uid,"nlink":os.fstat(i).st_nlink} for i in (1,2)]},f)\n'
                    'os.write(1,' + repr(STDOUT) + ')\nos.write(2,' + repr(STDERR) + ')\n')
                stub.chmod(0o700)
                operation_root = independent / 'remote-operations'
                command = [sys.executable, '-I', '-B', str(ROOT / 'scripts/q03_remote_operation.py'),
                    'launch', '--root', str(operation_root), '--authority', str(authority),
                    '--authority-sha256', hashlib.sha256(authority.read_bytes()).hexdigest(),
                    '--window', self.window, '--operation', self.operation, '--epoch', self.epoch,
                    '--cwd', str(independent), '--timeout-seconds', '2', '--argv-json',
                    json.dumps(R.bound_command(['docker', 'logs', '--timestamps', '--', '1' * 64]))]
                launched = subprocess.run(command, env=dict(os.environ,
                    PATH=str(independent) + os.pathsep + os.environ['PATH']),
                    umask=mask, capture_output=True, timeout=10, check=True)
                self.assertIn(json.loads(launched.stdout)['status'], ('uncertain', 'running', 'terminal'))
                directory = operation_root / self.window / self.operation
                deadline = time.monotonic() + 10
                while not (directory / 'receipt.json').exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue((directory / 'receipt.json').exists(),
                    (directory / 'supervisor.log').read_text(errors='replace'))
                result = R.seal_epoch(operation_root, self.window, self.epoch)
                self.assertTrue(result['safe_to_overlap'])
                terminal = result['operations'][0]
                self.assertTrue(terminal['action_succeeded'])
                self.assertTrue(terminal['log_capture']['complete'])
                self.assertEqual(terminal['log_capture']['artifact_errors'], {})
                for stream, name, expected in (('stdout', 'output.log', STDOUT),
                                                ('stderr', 'error.log', STDERR)):
                    captured = terminal['log_capture']['streams'][stream]
                    self.assertEqual(base64.b64decode(captured['data_base64']), expected)
                    self.assertEqual(captured['bytes'], len(expected))
                    self.assertEqual(captured['sha256'], hashlib.sha256(expected).hexdigest())
                # Control JSON, digest sidecar and every newly created ancestor
                # must not depend on the SSH child's inherited umask either.
                for path in (directory / name for name in
                        ('manifest.json', 'manifest.sha256', 'state.json',
                         'receipt.json', 'supervisor.lock')):
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, str(path))
                for path in operation_root.rglob('*'):
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode),
                                     0o700 if path.is_dir() else 0o600, str(path))
                self.assertEqual(stat.S_IMODE(operation_root.stat().st_mode), 0o700)
                identities = []
                for name in ('output.log', 'error.log', 'supervisor.log'):
                    value = (directory / name).lstat()
                    self.assertTrue(stat.S_ISREG(value.st_mode))
                    self.assertEqual(stat.S_IMODE(value.st_mode), 0o600)
                    self.assertEqual(value.st_uid, os.geteuid())
                    self.assertEqual(value.st_nlink, 1)
                    identities.append((value.st_dev, value.st_ino))
                self.assertEqual(len(set(identities)), 3)
                probe = json.loads((independent / 'child-probe.json').read_text())
                self.assertEqual(probe['umask'], mask, 'helper must not mutate inherited umask')
                self.assertEqual(probe['streams'], [{'mode': 0o600, 'uid': os.geteuid(), 'nlink': 1}] * 2)

    def test_window_and_watchdog_control_lifecycle_in_real_umask_children(self):
        tests = [
            'test_q03_held_window.HeldCoreContracts.test_new_controls_pass_strict_reader_before_first_stop',
            'test_q03_held_window.HeldCoreContracts.test_invalid_new_capsule_mode_refuses_before_hold_or_stop',
            'test_q03_held_window.HeldCoreContracts.test_invalid_new_readiness_refuses_before_stop',
            'test_q03_held_window.HeldCoreContracts.test_actual_failed_gate_pauses_then_close_restores_once',
            'test_q03_held_window.HeldCoreContracts.test_unknown_drain_blocks_final_restore_and_retains_hold',
            'test_q03_held_window.HeldCoreContracts.test_interrupted_attempt_cleared_only_after_registered_drain',
            'test_q03_window.WindowRecoveryTests.test_restore_failures_retain_hold_and_recovery_can_retry',
            'test_q03_window.WatchdogFaultTests.test_dead_core_recovers_without_signalling_reused_pid',
            'test_q03_private_log_creation.PrivateControlFiles',
            'test_q03_reconcile_restore.RecoveryOrderingTests.test_new_journal_epochs_and_readiness_precede_hold_release',
        ]
        for mask in (0o000, 0o002, 0o022, 0o077):
            with self.subTest(umask=oct(mask)):
                child = subprocess.run([sys.executable, '-B', '-m', 'unittest', '-q', *tests],
                    cwd=ROOT, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
                    PYTHONPATH=os.pathsep.join((str(ROOT / 'scripts'), str(ROOT / 'tests')))),
                    umask=mask, capture_output=True, text=True, timeout=30)
                self.assertEqual(child.returncode, 0, child.stdout + child.stderr)

    def test_existing_log_files_are_not_reopened_repaired_or_truncated(self):
        directory = self.operation_directory()
        for name in ('output.log', 'error.log', 'supervisor.log'):
            path = directory / name
            path.write_bytes(b'historical 0664 evidence\x00\xff')
            path.chmod(0o664)  # Create a deliberately invalid historical fixture.
            before = path.stat(), path.read_bytes()
            with self.assertRaises(FileExistsError):
                R.create_private_log(directory, name)
            after = path.stat(), path.read_bytes()
            self.assertEqual((before[0].st_ino, before[0].st_mode, before[0].st_mtime_ns, before[1]),
                             (after[0].st_ino, after[0].st_mode, after[0].st_mtime_ns, after[1]))
        streams, errors = R.log_streams(directory)
        self.assertEqual(streams, {})
        self.assertEqual(set(errors), {'stdout', 'stderr'})

    def test_existing_symlink_hardlink_fifo_and_directory_are_refused_without_mutation(self):
        directory = self.operation_directory()
        target = self.root / 'old-evidence'
        target.write_bytes(b'preserve exact old bytes')
        target.chmod(0o664)
        before = target.stat(), target.read_bytes()
        for kind in ('symlink', 'hardlink', 'fifo', 'directory'):
            with self.subTest(kind=kind):
                path = directory / 'output.log'
                if kind == 'symlink': path.symlink_to(target)
                elif kind == 'hardlink': os.link(target, path)
                elif kind == 'fifo': os.mkfifo(path, 0o600)
                else: path.mkdir()
                with self.assertRaises(FileExistsError):
                    R.create_private_log(directory, 'output.log')
                self.assertEqual(target.read_bytes(), before[1])
                self.assertEqual(target.stat().st_mode, before[0].st_mode)
                self.assertEqual(target.stat().st_mtime_ns, before[0].st_mtime_ns)
                if kind == 'directory': path.rmdir()
                else: path.unlink()

    def test_invalid_log_names_and_relative_directory_are_refused(self):
        directory = self.operation_directory()
        for name in ('../outside', 'sub/output.log', str(directory / 'output.log'), 'manifest.json'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'log name'):
                R.create_private_log(directory, name)
        with self.assertRaisesRegex(ValueError, 'absolute'):
            R.create_private_log(Path('.'), 'output.log')
        self.assertEqual(list(directory.glob('*.log')), [])

    def test_symlink_directory_and_ancestor_are_refused(self):
        directory = self.operation_directory()
        direct = self.root / 'direct-alias'
        direct.symlink_to(directory, target_is_directory=True)
        ancestor = self.root / 'ancestor-alias'
        ancestor.symlink_to(directory.parent, target_is_directory=True)
        for alias in (direct, ancestor / directory.name):
            with self.subTest(alias=alias), self.assertRaisesRegex(ValueError, 'aliases'):
                R.create_private_log(alias, 'output.log')
        self.assertFalse((directory / 'output.log').exists())

    def test_shared_directory_and_foreign_owner_are_refused_before_creation(self):
        directory = self.operation_directory()
        directory.chmod(0o770)
        with self.assertRaisesRegex(RuntimeError, 'owned and private'):
            R.create_private_log(directory, 'output.log')
        directory.chmod(0o700)
        with mock.patch.object(R.os, 'geteuid', return_value=os.geteuid() + 1):
            with self.assertRaisesRegex(RuntimeError, 'owned and private'):
                R.create_private_log(directory, 'output.log')
        self.assertFalse((directory / 'output.log').exists())

    def test_file_owner_and_mode_validation_close_descriptor_without_repair(self):
        directory = self.operation_directory()
        real_open = os.open
        for fault in ('owner', 'mode'):
            with self.subTest(fault=fault):
                opened = []
                def open_file(path, flags, mode=0o777, **kwargs):
                    fd = real_open(path, flags, 0o400 if fault == 'mode' and path == 'output.log' else mode, **kwargs)
                    opened.append(fd)
                    return fd
                # The second owner check refers to the newly created file.
                owners = [os.geteuid(), os.geteuid() + 1] if fault == 'owner' else [os.geteuid()] * 2
                with mock.patch.object(R.os, 'open', side_effect=open_file), \
                        mock.patch.object(R.os, 'geteuid', side_effect=owners):
                    with self.assertRaisesRegex(RuntimeError, 'owned private unaliased'):
                        R.create_private_log(directory, 'output.log')
                for fd in opened:
                    with self.assertRaises(OSError): os.fstat(fd)
                self.assertEqual(stat.S_IMODE((directory / 'output.log').stat().st_mode),
                                 0o400 if fault == 'mode' else 0o600)
                (directory / 'output.log').unlink()

    def test_creation_time_hardlink_is_refused_and_preserved(self):
        directory = self.operation_directory()
        real_open = os.open
        def link_after_create(path, flags, mode=0o777, **kwargs):
            fd = real_open(path, flags, mode, **kwargs)
            if path == 'output.log': os.link(directory / path, self.root / 'new-alias')
            return fd
        with mock.patch.object(R.os, 'open', side_effect=link_after_create):
            with self.assertRaisesRegex(RuntimeError, 'owned private unaliased'):
                R.create_private_log(directory, 'output.log')
        self.assertEqual((directory / 'output.log').stat().st_nlink, 2)
        self.assertEqual((self.root / 'new-alias').stat().st_ino, (directory / 'output.log').stat().st_ino)

    def test_creation_time_path_replacement_is_refused_without_repair(self):
        directory = self.operation_directory()
        real_open = os.open
        def replace_after_create(path, flags, mode=0o777, **kwargs):
            fd = real_open(path, flags, mode, **kwargs)
            if path == 'output.log':
                (directory / path).rename(directory / 'retained-original')
                (directory / path).write_bytes(b'replacement evidence')
            return fd
        with mock.patch.object(R.os, 'open', side_effect=replace_after_create):
            with self.assertRaisesRegex(RuntimeError, 'changed during creation'):
                R.create_private_log(directory, 'output.log')
        self.assertEqual((directory / 'output.log').read_bytes(), b'replacement evidence')
        self.assertEqual((directory / 'retained-original').read_bytes(), b'')

    def test_creation_time_directory_replacement_is_refused(self):
        directory = self.operation_directory()
        retained = directory.with_name('retained-operation')
        real_open = os.open
        def move_after_create(path, flags, mode=0o777, **kwargs):
            fd = real_open(path, flags, mode, **kwargs)
            if path == 'output.log':
                directory.rename(retained)
                directory.mkdir(mode=0o700)
            return fd
        with mock.patch.object(R.os, 'open', side_effect=move_after_create):
            with self.assertRaisesRegex(RuntimeError, 'directory changed'):
                R.create_private_log(directory, 'output.log')
        self.assertTrue((retained / 'output.log').exists())
        self.assertFalse((directory / 'output.log').exists())

    def test_output_creation_failure_prevents_child_and_receipt(self):
        directory = self.operation_directory()
        (directory / 'output.log').write_bytes(b'old output')
        with mock.patch.object(R.subprocess, 'Popen') as popen:
            with self.assertRaises(FileExistsError): R.supervise(directory, R.LocalOps())
            popen.assert_not_called()
        self.assertEqual((directory / 'output.log').read_bytes(), b'old output')
        self.assert_no_receipt(directory)

    def test_stderr_creation_failure_closes_stdout_without_child_or_receipt(self):
        directory = self.operation_directory()
        (directory / 'error.log').write_bytes(b'old error')
        before = set(os.listdir('/proc/self/fd'))
        with mock.patch.object(R.subprocess, 'Popen') as popen:
            with self.assertRaises(FileExistsError): R.supervise(directory, R.LocalOps())
            popen.assert_not_called()
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)
        self.assertEqual((directory / 'error.log').read_bytes(), b'old error')
        self.assertEqual(stat.S_IMODE((directory / 'output.log').stat().st_mode), 0o600)
        self.assert_no_receipt(directory)

    def test_pipe_and_spawn_failure_close_all_descriptors_without_receipt(self):
        for failure in ('pipe', 'spawn'):
            with self.subTest(failure=failure):
                self.operation, self.epoch = uuid.uuid4().hex, uuid.uuid4().hex
                directory = self.operation_directory()
                before = set(os.listdir('/proc/self/fd'))
                target = R.os if failure == 'pipe' else R.subprocess
                attribute = 'pipe' if failure == 'pipe' else 'Popen'
                with mock.patch.object(target, attribute, side_effect=OSError(errno.EMFILE, failure)):
                    with self.assertRaises(OSError): R.supervise(directory, R.LocalOps())
                self.assertEqual(set(os.listdir('/proc/self/fd')), before)
                for name in ('output.log', 'error.log'):
                    self.assertEqual(stat.S_IMODE((directory / name).stat().st_mode), 0o600)
                    self.assertEqual((directory / name).read_bytes(), b'')
                self.assert_no_receipt(directory)

    def test_new_file_open_failure_closes_directory_descriptor(self):
        directory = self.operation_directory()
        real_open = os.open
        before = set(os.listdir('/proc/self/fd'))
        def fail_file_create(path, flags, mode=0o777, **kwargs):
            if path == 'output.log': raise OSError(errno.ENOSPC, 'injected full filesystem')
            return real_open(path, flags, mode, **kwargs)
        with mock.patch.object(R.os, 'open', side_effect=fail_file_create):
            with self.assertRaises(OSError): R.create_private_log(directory, 'output.log')
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)
        self.assertFalse((directory / 'output.log').exists())

    def test_umask_removing_owner_write_fails_closed_without_chmod(self):
        directory = self.operation_directory()
        previous = os.umask(0o200)
        try:
            with mock.patch.object(R.os, 'chmod', side_effect=AssertionError('no permission repair')), \
                    mock.patch.object(R.os, 'fchmod', side_effect=AssertionError('no permission repair')):
                with self.assertRaisesRegex(RuntimeError, 'owned private unaliased'):
                    R.create_private_log(directory, 'output.log')
        finally:
            os.umask(previous)
        self.assertEqual(stat.S_IMODE((directory / 'output.log').stat().st_mode), 0o400)
        self.assert_no_receipt(directory)


class PrivateControlFiles(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='q03-private-controls-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_watchdog_main_readiness_journal_and_recovery_artifacts_are_private(self):
        import q03_window_watchdog as W
        import q03_held_window as H
        capsule = {'window_id': 'a' * 32, 'recovery_command': [sys.executable, '-c', 'pass'],
                   'recovery_timeout_s': 5}
        with mock.patch.object(W, 'load_capsule', return_value=(capsule, 'b' * 64)), \
                mock.patch.object(W.LocalOps, 'verify_capsule'), \
                mock.patch.object(W.LocalOps, 'owned_hold'), \
                mock.patch.object(W.Watchdog, 'step', return_value='restored'), \
                mock.patch.object(sys, 'argv', ['watchdog', 'watch', '--session-dir', str(self.root)]):
            self.assertEqual(W.main(), 0)
        ready = json.loads(H.read_owned_bytes(self.root / 'watchdog.ready.json'))
        self.assertEqual(ready['window_id'], capsule['window_id'])
        ops = W.LocalOps(self.root, capsule, 'b' * 64)
        ops.journal('first')
        ops.journal('second')
        ops.mark_recovery_attempted()
        ops.recover()
        with self.assertRaises(FileExistsError):
            ops.mark_recovery_attempted()
        with mock.patch.object(W, 'load_capsule', return_value=(capsule, 'b' * 64)), \
                mock.patch.object(W.subprocess, 'Popen') as popen:
            W.launch_watchdog(self.root)
            self.assertEqual(popen.call_count, 1)
        for path in self.root.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path.name)
            self.assertEqual(path.stat().st_nlink, 1)
        self.assertEqual(len((self.root / 'watchdog-journal.jsonl').read_text().splitlines()), 2)

    def test_recovery_supervisor_logs_are_private_with_real_inert_retry_child(self):
        import q03_recovery_supervisor as S
        main = S.identity(os.getpid())
        deadline = time.time() + 30
        intent = {'deadline_epoch': deadline}
        S.exclusive(self.root / 'intent.json', intent)
        process = mock.Mock(pid=os.getpid())
        def spawn(*args, **kwargs):
            S.exclusive(self.root / 'supervisor-ready.json', {
                'supervisor': main, 'main': main, 'intent_sha256': 'digest'})
            return process
        with mock.patch.object(S, 'verify_sources', return_value=(intent, {}, 'digest')), \
                mock.patch.object(S.subprocess, 'Popen', side_effect=spawn):
            S.start_watchdog(self.root, main, deadline)
        children, real_popen = [], subprocess.Popen
        def retry(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            children.append(child)
            return child
        try:
            with mock.patch.object(S, 'retry_command', return_value=[sys.executable, '-I', '-B', '-c', 'pass']), \
                    mock.patch.object(S.subprocess, 'Popen', side_effect=retry):
                S.Ops().launch_retry(self.root, intent, main)
            self.assertEqual(children[0].wait(timeout=5), 0)
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
        for name in ('supervisor.log', 'supervisor-retry.log', 'supervisor-retry.json'):
            self.assertEqual(stat.S_IMODE((self.root / name).stat().st_mode), 0o600, name)
        before = (self.root / 'supervisor-retry.log').read_bytes()
        descriptors = set(os.listdir('/proc/self/fd'))
        with self.assertRaises(FileExistsError):
            S.Ops().launch_retry(self.root, intent, main)
        self.assertEqual(set(os.listdir('/proc/self/fd')), descriptors)
        self.assertEqual((self.root / 'supervisor-retry.log').read_bytes(), before)

    def test_http_and_owner_journal_locks_are_private_and_refuse_unsafe_reopen(self):
        from types import SimpleNamespace
        import q03_http_service as HTTP
        import q03_owner_model as Owner
        parent = self.root / 'attempts' / ('a' * 32)
        R.private_mkdir(parent, parents=True)
        for module, kind in ((HTTP, 'http'), (Owner, 'owner')):
            plan = {'attempt_id': 'a' * 32}
            journal = module.Journal(SimpleNamespace(directory=self.root), plan, create=True)
            journal.close()
            for path in journal.directory.iterdir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path.name)
            lock = journal.directory / (kind + '.lock')
            lock.chmod(0o664)
            before = lock.stat()
            with self.assertRaisesRegex(RuntimeError, 'not shared-writable'):
                module.Journal(SimpleNamespace(directory=self.root), plan)
            self.assertEqual((lock.stat().st_ino, lock.stat().st_mode, lock.stat().st_mtime_ns),
                             (before.st_ino, before.st_mode, before.st_mtime_ns))

    def test_unsafe_existing_append_targets_and_aliases_are_not_modified(self):
        path = self.root / 'session.lock'
        path.write_bytes(b'historical evidence')
        path.chmod(0o664)
        before = path.stat(), path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'not shared-writable'):
            R.open_private(path, 'a')
        self.assertEqual((path.stat().st_ino, path.stat().st_mode, path.stat().st_mtime_ns, path.read_bytes()),
                         (before[0].st_ino, before[0].st_mode, before[0].st_mtime_ns, before[1]))
        alias = self.root / 'alias'
        alias.symlink_to(path)
        with self.assertRaises(OSError):
            R.open_private(alias, 'a')
        with self.assertRaises(FileExistsError):
            R.open_private(path, 'x')
        os.link(path, self.root / 'hardlink')
        with self.assertRaisesRegex(RuntimeError, 'unaliased'):
            R.open_private(path, 'a')
        fifo = self.root / 'fifo'
        os.mkfifo(fifo, 0o600)
        with self.assertRaises(OSError):
            R.open_private(fifo, 'a')
        self.assertEqual(path.read_bytes(), before[1])

    def test_atomic_failure_keeps_old_bytes_and_removes_only_own_temporary(self):
        import q03_window_watchdog as W
        for writer in (R.atomic, W.atomic_json):
            with self.subTest(writer=writer.__module__):
                path = self.root / 'state.json'
                R.atomic(path, {'old': True})
                before = path.stat(), path.read_bytes()
                with self.assertRaises(TypeError):
                    writer(path, {'invalid': object()})
                self.assertEqual((path.stat().st_ino, path.read_bytes()), (before[0].st_ino, before[1]))
                self.assertEqual(list(self.root.iterdir()), [path])
                writer(path, {'new': True})
                self.assertEqual(json.loads(path.read_bytes()), {'new': True})
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_private_directory_does_not_repair_shared_existing_ancestry(self):
        parent = self.root / 'shared'
        parent.mkdir(mode=0o700)
        parent.chmod(0o775)
        with self.assertRaisesRegex(RuntimeError, 'owner-controlled'):
            R.private_mkdir(parent, parents=True, exist_ok=True)
        self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o775)
        with self.assertRaisesRegex(RuntimeError, 'ancestry'):
            R.private_mkdir(parent / 'child' / 'leaf', parents=True)
        self.assertFalse((parent / 'child').exists())


if __name__ == '__main__':
    unittest.main()
