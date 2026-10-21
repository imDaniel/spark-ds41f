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


if __name__ == '__main__':
    unittest.main()
