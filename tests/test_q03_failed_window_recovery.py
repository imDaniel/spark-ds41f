"""Focused CPU-only failure matrix for the single-window NEVERSTART entry."""
import contextlib
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
PATH = Path(__file__).resolve().parents[1] / 'scripts/q03_failed_window_recovery.py'
spec = importlib.util.spec_from_file_location('exact_recovery', PATH)
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)

def fingerprint(path):
    info = path.stat()
    return {'uid': info.st_uid, 'mode': oct(stat.S_IMODE(info.st_mode)), 'bytes': info.st_size, 'sha256': r.digest(path.read_bytes())}

def report(epoch='e' * 32):
    return {'window_id': r.WINDOW, 'epoch': epoch, 'safe_to_overlap': True, 'sealed_epochs': [dict(node='spark0' + str(i), window=r.WINDOW, epoch=epoch, sealed=True, safe_to_overlap=True, operations=[]) for i in range(1, 5)]}

class Protocol:

    def __init__(self, failure=None):
        self.events = []
        self.state = {}
        self.failure = failure

    def event(self, value):
        self.events.append(value)
        if self.failure == value:
            raise RuntimeError('injected ' + value)

    def seal(self, epoch):
        self.event('old-seal' if epoch == r.OLD_EPOCH else 'new-seal')

    def check(self, phase):
        self.event(phase)

    def check_window_absence(self):
        self.event('window-absence')

    def update(self, **fields):
        self.events.append('register' if 'transition_epochs' in fields else 'marker')
        self.state.update(fields)

    def command(self, action, epoch, timeout):
        assert action == 'stop' and epoch in self.state['transition_epochs']
        self.event('stop')

    def release(self):
        assert self.state['phase'] == 'closed-stopped'
        assert self.state['positive_all_four_drain'] is True
        assert self.state['restored'] is False
        self.event('release')
import unittest
import tempfile
from unittest import mock

class ExactWindowRecoveryTests(unittest.TestCase):

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='exact-recovery-cpu-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def setattr(self, target, name, value):
        patcher = mock.patch.object(target, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def isolated_ancestry(self):
        tmp_path = self.root

        def check(path):
            assert path.is_relative_to(tmp_path)
            for parent in (path, *path.parents):
                info = parent.lstat()
                r.require(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, os.getuid()) and (not info.st_mode & 18), 'Untrusted directory ancestry')
                if parent == tmp_path:
                    break
        self.setattr(r, 'ancestry', check)
        return tmp_path

    def test_exact_mode_exception_requires_exact_bytes(self):
        isolated_ancestry = self.isolated_ancestry()
        p = isolated_ancestry / 'capsule.sha256'
        p.write_bytes((r.CAPSULE_SHA256 + '\n').encode())
        p.chmod(436)
        fp = fingerprint(p)
        assert r.read(p, fingerprint=fp) == p.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'Unsafe file mode'):
            r.read(p)
        p.write_bytes(b'x' * 65)
        with self.assertRaisesRegex(RuntimeError, 'Historical bytes drift'):
            r.read(p, fingerprint=fp)

    def test_metadata_or_hash_drift_refuses_0(self):
        change = 'mode'
        isolated_ancestry = self.isolated_ancestry()
        p = isolated_ancestry / 'evidence'
        p.write_bytes(b'evidence')
        p.chmod(384)
        fp = fingerprint(p)
        fp[{'mode': 'mode', 'owner': 'uid', 'size': 'bytes', 'hash': 'sha256'}[change]] = {'mode': '0o664', 'owner': os.getuid() + 1, 'size': 100, 'hash': '0' * 64}[change]
        with self.assertRaises(RuntimeError):
            r.read(p, fingerprint=fp)

    def test_metadata_or_hash_drift_refuses_1(self):
        change = 'owner'
        isolated_ancestry = self.isolated_ancestry()
        p = isolated_ancestry / 'evidence'
        p.write_bytes(b'evidence')
        p.chmod(384)
        fp = fingerprint(p)
        fp[{'mode': 'mode', 'owner': 'uid', 'size': 'bytes', 'hash': 'sha256'}[change]] = {'mode': '0o664', 'owner': os.getuid() + 1, 'size': 100, 'hash': '0' * 64}[change]
        with self.assertRaises(RuntimeError):
            r.read(p, fingerprint=fp)

    def test_metadata_or_hash_drift_refuses_2(self):
        change = 'size'
        isolated_ancestry = self.isolated_ancestry()
        p = isolated_ancestry / 'evidence'
        p.write_bytes(b'evidence')
        p.chmod(384)
        fp = fingerprint(p)
        fp[{'mode': 'mode', 'owner': 'uid', 'size': 'bytes', 'hash': 'sha256'}[change]] = {'mode': '0o664', 'owner': os.getuid() + 1, 'size': 100, 'hash': '0' * 64}[change]
        with self.assertRaises(RuntimeError):
            r.read(p, fingerprint=fp)

    def test_metadata_or_hash_drift_refuses_3(self):
        change = 'hash'
        isolated_ancestry = self.isolated_ancestry()
        p = isolated_ancestry / 'evidence'
        p.write_bytes(b'evidence')
        p.chmod(384)
        fp = fingerprint(p)
        fp[{'mode': 'mode', 'owner': 'uid', 'size': 'bytes', 'hash': 'sha256'}[change]] = {'mode': '0o664', 'owner': os.getuid() + 1, 'size': 100, 'hash': '0' * 64}[change]
        with self.assertRaises(RuntimeError):
            r.read(p, fingerprint=fp)

    def test_no_alias_or_hardlink_0(self):
        alias = 'symlink'
        isolated_ancestry = self.isolated_ancestry()
        p = isolated_ancestry / 'evidence'
        p.write_bytes(b'x')
        p.chmod(384)
        q = isolated_ancestry / 'alias'
        if alias == 'symlink':
            q.symlink_to(p)
        else:
            os.link(p, q)
        with self.assertRaises(RuntimeError):
            r.read(q, fingerprint=fingerprint(p))

    def test_no_alias_or_hardlink_1(self):
        alias = 'hardlink'
        isolated_ancestry = self.isolated_ancestry()
        p = isolated_ancestry / 'evidence'
        p.write_bytes(b'x')
        p.chmod(384)
        q = isolated_ancestry / 'alias'
        if alias == 'symlink':
            q.symlink_to(p)
        else:
            os.link(p, q)
        with self.assertRaises(RuntimeError):
            r.read(q, fingerprint=fingerprint(p))

    def test_parent_group_writable_refuses(self):
        isolated_ancestry = self.isolated_ancestry()
        parent = isolated_ancestry / 'untrusted'
        parent.mkdir(mode=509)
        parent.chmod(509)
        p = parent / 'evidence'
        p.write_text('x')
        p.chmod(384)
        with self.assertRaisesRegex(RuntimeError, 'ancestry'):
            r.read(p)

    def test_duplicate_json_refuses(self):
        with self.assertRaisesRegex(RuntimeError, 'Duplicate'):
            r.decode(b'{"a":1,"a":2}')

    def test_unknown_proc_is_never_dead_0(self):
        error = PermissionError('denied')
        monkeypatch = self

        def read(*args, **kwargs):
            raise error
        monkeypatch.setattr(Path, 'read_text', read)
        with self.assertRaises(type(error)):
            r.process_state({'pid': 999, 'start_ticks': '10'})

    def test_unknown_proc_is_never_dead_1(self):
        error = OSError('unknown')
        monkeypatch = self

        def read(*args, **kwargs):
            raise error
        monkeypatch.setattr(Path, 'read_text', read)
        with self.assertRaises(type(error)):
            r.process_state({'pid': 999, 'start_ticks': '10'})

    def test_unknown_proc_is_never_dead_2(self):
        error = ValueError('malformed')
        monkeypatch = self

        def read(*args, **kwargs):
            raise error
        monkeypatch.setattr(Path, 'read_text', read)
        with self.assertRaises(type(error)):
            r.process_state({'pid': 999, 'start_ticks': '10'})

    def test_process_death_and_reuse_are_distinct_from_alive(self):
        monkeypatch = self
        record = {'pid': 999, 'start_ticks': '10'}
        fields = ['S'] + ['0'] * 18 + ['10']
        monkeypatch.setattr(Path, 'read_text', lambda *a, **k: '999 (test) ' + ' '.join(fields))
        assert r.process_state(record) == 'alive'
        fields[19] = '11'
        assert r.process_state(record) == 'dead'
        fields[19], fields[0] = ('10', 'Z')
        assert r.process_state(record) == 'dead'

        def gone(*args, **kwargs):
            raise FileNotFoundError()
        monkeypatch.setattr(Path, 'read_text', gone)
        assert r.process_state(record) == 'dead'

    def test_busy_existing_lock_refuses_without_changing_bytes(self):
        isolated_ancestry = self.isolated_ancestry()
        path = isolated_ancestry / 'session.lock'
        path.touch(mode=384)
        with contextlib.ExitStack() as first:
            r.lock_existing(first, path)
            with contextlib.ExitStack() as second:
                with self.assertRaises(BlockingIOError):
                    r.lock_existing(second, path)
        assert path.read_bytes() == b''
        assert stat.S_IMODE(path.stat().st_mode) == 384

    def test_all_four_seal_fault_matrix_0(self):
        fault = 'missing'
        value = report()
        row = value['sealed_epochs'][0]
        if fault == 'missing':
            value['sealed_epochs'].pop()
        elif fault == 'duplicate':
            row['node'] = 'spark02'
        elif fault == 'wrong_window':
            row['window'] = '0' * 32
        elif fault == 'wrong_epoch':
            row['epoch'] = '0' * 32
        elif fault == 'unsealed':
            row['sealed'] = False
        elif fault == 'unsafe':
            row['safe_to_overlap'] = False
        elif fault == 'missing_operations':
            row.pop('operations')
        else:
            op = dict(window=r.WINDOW, status='terminal', safe_to_overlap=True, epoch_sealed=True, group_quiescent=True, timed_out=False, cancelled=False, action_succeeded=True, exit_code=0)
            row['operations'] = [op]
            if fault == 'unknown':
                op['status'] = 'uncertain'
            elif fault == 'failed_operation':
                op['action_succeeded'] = False
            elif fault == 'timeout':
                op['timed_out'] = True
            elif fault == 'unquiescent':
                op['group_quiescent'] = False
        with self.assertRaises(RuntimeError):
            r.verify_seal(value, 'e' * 32, ['spark01', 'spark02', 'spark03', 'spark04'], 'example-memguard.service')

    def test_all_four_seal_fault_matrix_1(self):
        fault = 'duplicate'
        value = report()
        row = value['sealed_epochs'][0]
        if fault == 'missing':
            value['sealed_epochs'].pop()
        elif fault == 'duplicate':
            row['node'] = 'spark02'
        elif fault == 'wrong_window':
            row['window'] = '0' * 32
        elif fault == 'wrong_epoch':
            row['epoch'] = '0' * 32
        elif fault == 'unsealed':
            row['sealed'] = False
        elif fault == 'unsafe':
            row['safe_to_overlap'] = False
        elif fault == 'missing_operations':
            row.pop('operations')
        else:
            op = dict(window=r.WINDOW, status='terminal', safe_to_overlap=True, epoch_sealed=True, group_quiescent=True, timed_out=False, cancelled=False, action_succeeded=True, exit_code=0)
            row['operations'] = [op]
            if fault == 'unknown':
                op['status'] = 'uncertain'
            elif fault == 'failed_operation':
                op['action_succeeded'] = False
            elif fault == 'timeout':
                op['timed_out'] = True
            elif fault == 'unquiescent':
                op['group_quiescent'] = False
        with self.assertRaises(RuntimeError):
            r.verify_seal(value, 'e' * 32, ['spark01', 'spark02', 'spark03', 'spark04'], 'example-memguard.service')

    def test_all_four_seal_fault_matrix_2(self):
        fault = 'wrong_window'
        value = report()
        row = value['sealed_epochs'][0]
        if fault == 'missing':
            value['sealed_epochs'].pop()
        elif fault == 'duplicate':
            row['node'] = 'spark02'
        elif fault == 'wrong_window':
            row['window'] = '0' * 32
        elif fault == 'wrong_epoch':
            row['epoch'] = '0' * 32
        elif fault == 'unsealed':
            row['sealed'] = False
        elif fault == 'unsafe':
            row['safe_to_overlap'] = False
        elif fault == 'missing_operations':
            row.pop('operations')
        else:
            op = dict(window=r.WINDOW, status='terminal', safe_to_overlap=True, epoch_sealed=True, group_quiescent=True, timed_out=False, cancelled=False, action_succeeded=True, exit_code=0)
            row['operations'] = [op]
            if fault == 'unknown':
                op['status'] = 'uncertain'
            elif fault == 'failed_operation':
                op['action_succeeded'] = False
            elif fault == 'timeout':
                op['timed_out'] = True
            elif fault == 'unquiescent':
                op['group_quiescent'] = False
        with self.assertRaises(RuntimeError):
            r.verify_seal(value, 'e' * 32, ['spark01', 'spark02', 'spark03', 'spark04'], 'example-memguard.service')

    def test_all_four_seal_fault_matrix_3(self):
        fault = 'wrong_epoch'
        value = report()
        row = value['sealed_epochs'][0]
        if fault == 'missing':
            value['sealed_epochs'].pop()
        elif fault == 'duplicate':
            row['node'] = 'spark02'
        elif fault == 'wrong_window':
            row['window'] = '0' * 32
        elif fault == 'wrong_epoch':
            row['epoch'] = '0' * 32
        elif fault == 'unsealed':
            row['sealed'] = False
        elif fault == 'unsafe':
            row['safe_to_overlap'] = False
        elif fault == 'missing_operations':
            row.pop('operations')
        else:
            op = dict(window=r.WINDOW, status='terminal', safe_to_overlap=True, epoch_sealed=True, group_quiescent=True, timed_out=False, cancelled=False, action_succeeded=True, exit_code=0)
            row['operations'] = [op]
            if fault == 'unknown':
                op['status'] = 'uncertain'
            elif fault == 'failed_operation':
                op['action_succeeded'] = False
            elif fault == 'timeout':
                op['timed_out'] = True
            elif fault == 'unquiescent':
                op['group_quiescent'] = False
        with self.assertRaises(RuntimeError):
            r.verify_seal(value, 'e' * 32, ['spark01', 'spark02', 'spark03', 'spark04'], 'example-memguard.service')

    def test_all_four_seal_fault_matrix_4(self):
        fault = 'unsealed'
        value = report()
        row = value['sealed_epochs'][0]
        if fault == 'missing':
            value['sealed_epochs'].pop()
        elif fault == 'duplicate':
            row['node'] = 'spark02'
        elif fault == 'wrong_window':
            row['window'] = '0' * 32
        elif fault == 'wrong_epoch':
            row['epoch'] = '0' * 32
        elif fault == 'unsealed':
            row['sealed'] = False
        elif fault == 'unsafe':
            row['safe_to_overlap'] = False
        elif fault == 'missing_operations':
            row.pop('operations')
        else:
            op = dict(window=r.WINDOW, status='terminal', safe_to_overlap=True, epoch_sealed=True, group_quiescent=True, timed_out=False, cancelled=False, action_succeeded=True, exit_code=0)
            row['operations'] = [op]
            if fault == 'unknown':
                op['status'] = 'uncertain'
            elif fault == 'failed_operation':
                op['action_succeeded'] = False
            elif fault == 'timeout':
                op['timed_out'] = True
            elif fault == 'unquiescent':
                op['group_quiescent'] = False
        with self.assertRaises(RuntimeError):
            r.verify_seal(value, 'e' * 32, ['spark01', 'spark02', 'spark03', 'spark04'], 'example-memguard.service')

    def test_all_four_seal_fault_matrix_5(self):
        fault = 'unsafe'
        value = report()
        row = value['sealed_epochs'][0]
        if fault == 'missing':
            value['sealed_epochs'].pop()
        elif fault == 'duplicate':
            row['node'] = 'spark02'
        elif fault == 'wrong_window':
            row['window'] = '0' * 32
        elif fault == 'wrong_epoch':
            row['epoch'] = '0' * 32
        elif fault == 'unsealed':
            row['sealed'] = False
        elif fault == 'unsafe':
            row['safe_to_overlap'] = False
        elif fault == 'missing_operations':
            row.pop('operations')
        else:
            op = dict(window=r.WINDOW, status='terminal', safe_to_overlap=True, epoch_sealed=True, group_quiescent=True, timed_out=False, cancelled=False, action_succeeded=True, exit_code=0)
            row['operations'] = [op]
            if fault == 'unknown':
                op['status'] = 'uncertain'
            elif fault == 'failed_operation':
                op['action_succeeded'] = False
            elif fault == 'timeout':
                op['timed_out'] = True
            elif fault == 'unquiescent':
                op['group_quiescent'] = False
        with self.assertRaises(RuntimeError):
            r.verify_seal(value, 'e' * 32, ['spark01', 'spark02', 'spark03', 'spark04'], 'example-memguard.service')

    def test_all_four_seal_fault_matrix_6(self):
        fault = 'unknown'
        value = report()
        row = value['sealed_epochs'][0]
        if fault == 'missing':
            value['sealed_epochs'].pop()
        elif fault == 'duplicate':
            row['node'] = 'spark02'
        elif fault == 'wrong_window':
            row['window'] = '0' * 32
        elif fault == 'wrong_epoch':
            row['epoch'] = '0' * 32
        elif fault == 'unsealed':
            row['sealed'] = False
        elif fault == 'unsafe':
            row['safe_to_overlap'] = False
        elif fault == 'missing_operations':
            row.pop('operations')
        else:
            op = dict(window=r.WINDOW, status='terminal', safe_to_overlap=True, epoch_sealed=True, group_quiescent=True, timed_out=False, cancelled=False, action_succeeded=True, exit_code=0)
            row['operations'] = [op]
            if fault == 'unknown':
                op['status'] = 'uncertain'
            elif fault == 'failed_operation':
                op['action_succeeded'] = False
            elif fault == 'timeout':
                op['timed_out'] = True
            elif fault == 'unquiescent':
                op['group_quiescent'] = False
        with self.assertRaises(RuntimeError):
            r.verify_seal(value, 'e' * 32, ['spark01', 'spark02', 'spark03', 'spark04'], 'example-memguard.service')

    def test_all_four_seal_fault_matrix_7(self):
        fault = 'missing_operations'
        value = report()
        row = value['sealed_epochs'][0]
        if fault == 'missing':
            value['sealed_epochs'].pop()
        elif fault == 'duplicate':
            row['node'] = 'spark02'
        elif fault == 'wrong_window':
            row['window'] = '0' * 32
        elif fault == 'wrong_epoch':
            row['epoch'] = '0' * 32
        elif fault == 'unsealed':
            row['sealed'] = False
        elif fault == 'unsafe':
            row['safe_to_overlap'] = False
        elif fault == 'missing_operations':
            row.pop('operations')
        else:
            op = dict(window=r.WINDOW, status='terminal', safe_to_overlap=True, epoch_sealed=True, group_quiescent=True, timed_out=False, cancelled=False, action_succeeded=True, exit_code=0)
            row['operations'] = [op]
            if fault == 'unknown':
                op['status'] = 'uncertain'
            elif fault == 'failed_operation':
                op['action_succeeded'] = False
            elif fault == 'timeout':
                op['timed_out'] = True
            elif fault == 'unquiescent':
                op['group_quiescent'] = False
        with self.assertRaises(RuntimeError):
            r.verify_seal(value, 'e' * 32, ['spark01', 'spark02', 'spark03', 'spark04'], 'example-memguard.service')

    def test_all_four_seal_fault_matrix_8(self):
        fault = 'failed_operation'
        value = report()
        row = value['sealed_epochs'][0]
        if fault == 'missing':
            value['sealed_epochs'].pop()
        elif fault == 'duplicate':
            row['node'] = 'spark02'
        elif fault == 'wrong_window':
            row['window'] = '0' * 32
        elif fault == 'wrong_epoch':
            row['epoch'] = '0' * 32
        elif fault == 'unsealed':
            row['sealed'] = False
        elif fault == 'unsafe':
            row['safe_to_overlap'] = False
        elif fault == 'missing_operations':
            row.pop('operations')
        else:
            op = dict(window=r.WINDOW, status='terminal', safe_to_overlap=True, epoch_sealed=True, group_quiescent=True, timed_out=False, cancelled=False, action_succeeded=True, exit_code=0)
            row['operations'] = [op]
            if fault == 'unknown':
                op['status'] = 'uncertain'
            elif fault == 'failed_operation':
                op['action_succeeded'] = False
            elif fault == 'timeout':
                op['timed_out'] = True
            elif fault == 'unquiescent':
                op['group_quiescent'] = False
        with self.assertRaises(RuntimeError):
            r.verify_seal(value, 'e' * 32, ['spark01', 'spark02', 'spark03', 'spark04'], 'example-memguard.service')

    def test_all_four_seal_fault_matrix_9(self):
        fault = 'timeout'
        value = report()
        row = value['sealed_epochs'][0]
        if fault == 'missing':
            value['sealed_epochs'].pop()
        elif fault == 'duplicate':
            row['node'] = 'spark02'
        elif fault == 'wrong_window':
            row['window'] = '0' * 32
        elif fault == 'wrong_epoch':
            row['epoch'] = '0' * 32
        elif fault == 'unsealed':
            row['sealed'] = False
        elif fault == 'unsafe':
            row['safe_to_overlap'] = False
        elif fault == 'missing_operations':
            row.pop('operations')
        else:
            op = dict(window=r.WINDOW, status='terminal', safe_to_overlap=True, epoch_sealed=True, group_quiescent=True, timed_out=False, cancelled=False, action_succeeded=True, exit_code=0)
            row['operations'] = [op]
            if fault == 'unknown':
                op['status'] = 'uncertain'
            elif fault == 'failed_operation':
                op['action_succeeded'] = False
            elif fault == 'timeout':
                op['timed_out'] = True
            elif fault == 'unquiescent':
                op['group_quiescent'] = False
        with self.assertRaises(RuntimeError):
            r.verify_seal(value, 'e' * 32, ['spark01', 'spark02', 'spark03', 'spark04'], 'example-memguard.service')

    def test_all_four_seal_fault_matrix_10(self):
        fault = 'unquiescent'
        value = report()
        row = value['sealed_epochs'][0]
        if fault == 'missing':
            value['sealed_epochs'].pop()
        elif fault == 'duplicate':
            row['node'] = 'spark02'
        elif fault == 'wrong_window':
            row['window'] = '0' * 32
        elif fault == 'wrong_epoch':
            row['epoch'] = '0' * 32
        elif fault == 'unsealed':
            row['sealed'] = False
        elif fault == 'unsafe':
            row['safe_to_overlap'] = False
        elif fault == 'missing_operations':
            row.pop('operations')
        else:
            op = dict(window=r.WINDOW, status='terminal', safe_to_overlap=True, epoch_sealed=True, group_quiescent=True, timed_out=False, cancelled=False, action_succeeded=True, exit_code=0)
            row['operations'] = [op]
            if fault == 'unknown':
                op['status'] = 'uncertain'
            elif fault == 'failed_operation':
                op['action_succeeded'] = False
            elif fault == 'timeout':
                op['timed_out'] = True
            elif fault == 'unquiescent':
                op['group_quiescent'] = False
        with self.assertRaises(RuntimeError):
            r.verify_seal(value, 'e' * 32, ['spark01', 'spark02', 'spark03', 'spark04'], 'example-memguard.service')

    def test_acknowledgment_does_not_generalize_nonzero_status(self):
        value = report()
        row = value['sealed_epochs'][0]
        op = dict(window=r.WINDOW, status='terminal', safe_to_overlap=True, epoch_sealed=True, group_quiescent=True, timed_out=False, cancelled=False, action_succeeded=False, exit_code=5, lifecycle_acknowledged=True, lifecycle_unit='example-memguard.service')
        row['operations'] = [op]
        r.verify_seal(value, 'e' * 32, ['spark01', 'spark02', 'spark03', 'spark04'], 'example-memguard.service')
        op['lifecycle_unit'] = 'foreign.service'
        with self.assertRaises(RuntimeError):
            r.verify_seal(value, 'e' * 32, ['spark01', 'spark02', 'spark03', 'spark04'], 'example-memguard.service')

    def test_success_is_closed_stopped_only_after_positive_proofs(self):
        session = Protocol()
        result = r.run_protocol(session, lambda: None)
        assert session.events == ['old-seal', 'before_restore', 'register', 'stop', 'new-seal', 'restore_drained', 'window-absence', 'marker', 'release', 'marker']
        assert result['hold_released'] is True and result['restored'] is False

    def test_failures_never_release_or_start_0(self):
        failure = 'old-seal'
        session = Protocol(failure)
        with self.assertRaises(RuntimeError):
            r.run_protocol(session, lambda: None)
        assert 'release' not in session.events
        assert session.state.get('hold_released') is not True
        if failure == 'stop':
            assert session.events[-1] == 'new-seal'

    def test_failures_never_release_or_start_1(self):
        failure = 'before_restore'
        session = Protocol(failure)
        with self.assertRaises(RuntimeError):
            r.run_protocol(session, lambda: None)
        assert 'release' not in session.events
        assert session.state.get('hold_released') is not True
        if failure == 'stop':
            assert session.events[-1] == 'new-seal'

    def test_failures_never_release_or_start_2(self):
        failure = 'stop'
        session = Protocol(failure)
        with self.assertRaises(RuntimeError):
            r.run_protocol(session, lambda: None)
        assert 'release' not in session.events
        assert session.state.get('hold_released') is not True
        if failure == 'stop':
            assert session.events[-1] == 'new-seal'

    def test_failures_never_release_or_start_3(self):
        failure = 'new-seal'
        session = Protocol(failure)
        with self.assertRaises(RuntimeError):
            r.run_protocol(session, lambda: None)
        assert 'release' not in session.events
        assert session.state.get('hold_released') is not True
        if failure == 'stop':
            assert session.events[-1] == 'new-seal'

    def test_failures_never_release_or_start_4(self):
        failure = 'restore_drained'
        session = Protocol(failure)
        with self.assertRaises(RuntimeError):
            r.run_protocol(session, lambda: None)
        assert 'release' not in session.events
        assert session.state.get('hold_released') is not True
        if failure == 'stop':
            assert session.events[-1] == 'new-seal'

    def test_failures_never_release_or_start_5(self):
        failure = 'window-absence'
        session = Protocol(failure)
        with self.assertRaises(RuntimeError):
            r.run_protocol(session, lambda: None)
        assert 'release' not in session.events
        assert session.state.get('hold_released') is not True
        if failure == 'stop':
            assert session.events[-1] == 'new-seal'

    def test_source_or_hold_barrier_failure_never_releases(self):
        session = Protocol()

        def barrier():
            raise RuntimeError('source/hold drift')
        with self.assertRaises(RuntimeError):
            r.run_protocol(session, barrier)
        assert session.events == []

    def test_stop_interruption_still_attempts_sealing(self):
        session = Protocol()

        def interrupt(*args):
            session.events.append('stop')
            raise KeyboardInterrupt()
        session.command = interrupt
        with self.assertRaises(KeyboardInterrupt):
            r.run_protocol(session, lambda: None)
        assert session.events[-1] == 'new-seal' and 'release' not in session.events

    def test_cli_has_no_start_or_retry_surface(self):
        result = subprocess.run([sys.executable, '-I', '-B', str(PATH), '--old-session', '/x', '--journal-dir', '/y', '--live-witness', '/w', '--adapter-step', 'start'], capture_output=True)
        assert result.returncode == 2
        assert b'invalid choice' in result.stderr
        assert "action in ('stop', 'quiesce')" in PATH.read_text()

    def test_historical_fingerprints_are_exact_narrow_inventory(self):
        assert len(r.OLD_FILES) == 23
        assert r.OLD_FILES['capsule.json']['sha256'] == r.CAPSULE_SHA256
        assert r.OLD_FILES['capsule.sha256']['sha256'] == hashlib.sha256((r.CAPSULE_SHA256 + '\n').encode()).hexdigest()
        assert r.OLD_FILES['capsule.sha256']['mode'] == '0o664'
        assert r.OLD_FILES['watchdog.ready.json']['mode'] == '0o664'
        assert all((v['uid'] == 1000 and v['mode'] in ('0o600', '0o664') for v in r.OLD_FILES.values()))

    def test_private_output_is_0600_despite_umask_0002(self):
        tmp_path = self.root
        before = os.umask(2)
        try:
            p = tmp_path / 'proof.json'
            r.private_json(p, {'closed': False}, exclusive=True)
            assert stat.S_IMODE(p.stat().st_mode) == 384
            with self.assertRaises(FileExistsError):
                r.private_json(p, {'closed': True}, exclusive=True)
            assert json.loads(p.read_text()) == {'closed': False}
        finally:
            os.umask(before)
class ReleaseOutcomeTests(unittest.TestCase):
    def test_post_unlink_failure_does_not_claim_hold_retained(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'hold'
            fields=r.failure_fields({'positive_all_four_drain':True, 'hold_released':True},
                                    path,(1,2),OSError('fsync failed'))
            self.assertEqual(fields['phase'],'closed-stopped')
            self.assertTrue(fields['hold_released'])
            self.assertTrue(fields['hold_absence_observed'])
            self.assertFalse(fields['restored'])

    def test_foreign_replacement_is_unknown_not_retained(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'hold';path.write_text('foreign')
            fields=r.failure_fields({'positive_all_four_drain':True},path,(-1,-1),OSError())
            self.assertIsNone(fields['hold_released'])
            self.assertFalse(fields['hold_absence_observed'])

    def test_unknown_post_release_stat_is_not_absence(self):
        with mock.patch.object(Path,'lstat',side_effect=PermissionError('denied')):
            fields=r.failure_fields({'positive_all_four_drain':True},'/hold',(1,2),OSError())
            self.assertIsNone(fields['hold_released'])
            self.assertIsNone(fields['hold_absence_observed'])


class AdmissionFixtureTests(ExactWindowRecoveryTests):
    """Synthetic authority bytes exercise full admission without any device tool.

    Production constants are replaced only in this in-memory test instance.
    The separate actual-packet comparison verifies the release fingerprints.
    """
    # Do not duplicate inherited tests during unittest discovery.
    def setUp(self):
        super().setUp()
        self.isolated_ancestry()
        root = self.root
        (root / 'sessions').mkdir(mode=0o700)
        self.old = root / 'sessions/old'
        self.old.mkdir(mode=0o700)
        for name in ('attempts', 'controls', 'plans', 'restore-evidence'):
            (self.old / name).mkdir(mode=0o700)
        checkout = root / 'checkout'
        (checkout / 'scripts').mkdir(mode=0o700, parents=True)
        source = checkout / 'scripts/q03_window.py'
        source.write_text('# pinned fake source, never executed\n')
        source.chmod(0o600)
        self.source = source
        self.capsule = {
            'window_id':r.WINDOW, 'holder':'q03-isolated:' + r.WINDOW,
            'session_dir':str(self.old), 'hold_path':str(root/'hold.json'),
            'policy_path':str(root/'policy.json'), 'policy_sha256':'a'*64,
            'core_process':{'pid':90001, 'start_ticks':'1'},
            'recovery_script_sha256':r.digest(source.read_bytes()),
            'recovery_source_sha256':{'q03_window.py':r.digest(source.read_bytes())},
            'manifest':{'independent_root':str(root), 'preflight':{
                'window_id':r.WINDOW, 'holder':'q03-isolated:' + r.WINDOW,
                'hold_path':str(root/'hold.json'), 'policy_path':str(root/'policy.json'),
                'policy_sha256':'a'*64, 'original_service_disposition':'keep-stopped',
                'publication':{'sha':r.OLD_HEAD}, 'independent_checkout':str(checkout),
                'protected_roots':[str(root/'protected')]}}}
        state = {'phase':'recovery-failed', 'mutation_started':True,
            'held_sequence':0, 'active_attempt':None, 'active_process':None,
            'restored':False, 'transition_epochs':[{'action':'stop','epoch':r.OLD_EPOCH}],
            'held_candidates':[], 'candidate_containers':[], 'attempt_results':[],
            'attempt_receipt_sha256':{}, 'job_results':[]}
        cap_raw = json.dumps(self.capsule).encode()
        cap_sha = r.digest(cap_raw)
        ready = {'window_id':r.WINDOW, 'capsule_sha256':cap_sha,
                 'process':{'pid':90002,'start_ticks':'2'}}
        values = {'capsule.json':cap_raw, 'capsule.sha256':(cap_sha+'\n').encode(),
                  'state.json':json.dumps(state).encode(),
                  'watchdog.ready.json':json.dumps(ready).encode(),
                  'session.lock':b'', 'watchdog.lock':b''}
        files = {}
        for name, raw in values.items():
            path = self.old/name; path.write_bytes(raw)
            path.chmod(0o664 if name in ('capsule.sha256','watchdog.ready.json') else 0o600)
            files[name] = fingerprint(path)
        self.setattr(r, 'verify_witness', lambda: None)
        self.setattr(r, '_WITNESS', None)
        self.setattr(r, 'OLD_FILES', files)
        self.setattr(r, 'CAPSULE_SHA256', cap_sha)
        self.setattr(r, 'process_state', lambda record:'dead')
        self.hold = {'holder':self.capsule['holder'], 'window':r.WINDOW,
                     'heartbeat':'2026-10-22T01:01:40.517477+00:00',
                     'session_dir':str(self.old), 'since':'example'}
        hold_path = root/'hold.json'
        hold_path.write_text(json.dumps(self.hold)); hold_path.chmod(0o600)
        self.setattr(r, 'HOLD_INVARIANTS_SHA256', r.hold_invariant_digest(self.hold))
        self.destination = root/'sessions'/('recovery-'+r.WINDOW+'-neverstart')

    def test_admission_plan_is_read_only_and_not_live_drain(self):
        before = {str(p): (p.read_bytes(), p.stat().st_mode) for p in self.old.rglob('*') if p.is_file()}
        result = r.recover(self.old, self.destination)
        self.assertEqual(result['status'], 'plan-only')
        self.assertFalse(result['live_drain_established'])
        self.assertFalse(self.destination.exists())
        self.assertEqual(before, {str(p):(p.read_bytes(),p.stat().st_mode) for p in self.old.rglob('*') if p.is_file()})

    def test_every_historical_control_drift_refuses(self):
        for name in r.OLD_FILES:
            with self.subTest(name=name):
                path = self.old/name; raw = path.read_bytes()
                path.write_bytes(raw + b'!')
                with self.assertRaises(RuntimeError): r.verify_old(self.old)
                path.write_bytes(raw)

    def test_source_drift_refuses(self):
        self.source.write_text('changed')
        with self.assertRaisesRegex(RuntimeError, 'source changed'): r.verify_old(self.old)

    def test_unexpected_history_or_directory_refuses(self):
        extra = self.old/'attempts/unexpected'; extra.mkdir(mode=0o700)
        with self.assertRaisesRegex(RuntimeError, 'directory inventory'): r.verify_old(self.old)
        extra.rmdir(); extra.write_text('unexpected')
        with self.assertRaisesRegex(RuntimeError, 'inventory'): r.verify_old(self.old)

    def test_foreign_hold_or_heartbeat_change_refuses(self):
        path = self.root/'hold.json'
        for key, value in [('holder','foreign'), ('session_dir','/foreign'),
                           ('heartbeat','2026-10-22T01:02:40.517477+00:00')]:
            with self.subTest(key=key):
                changed=dict(self.hold); changed[key]=value
                path.write_text(json.dumps(changed))
                with self.assertRaises(RuntimeError): r.recover(self.old,self.destination)
                path.write_text(json.dumps(self.hold))

    def test_same_content_replacement_hold_refuses(self):
        identity=r.verify_hold(self.capsule)
        path=self.root/'hold.json'; raw=path.read_bytes()
        # Keep the original inode allocated to make replacement deterministic.
        path.rename(self.root/'retained-hold.json')
        path.write_bytes(raw);path.chmod(0o600)
        with self.assertRaisesRegex(RuntimeError,'inode'): r.verify_hold(self.capsule,identity=identity)

    def test_existing_or_alternate_journal_refuses(self):
        with self.assertRaisesRegex(RuntimeError,'one fixed'): r.recover(self.old,self.destination.with_name('alternate'))
        self.destination.mkdir(mode=0o700)
        with self.assertRaisesRegex(RuntimeError,'fresh journal'): r.recover(self.old,self.destination)


# This class reuses setup utilities, not the base class's test cases.
for _name in list(ExactWindowRecoveryTests.__dict__):
    if _name.startswith('test_') and _name not in AdmissionFixtureTests.__dict__:
        setattr(AdmissionFixtureTests, _name, None)

class HistoricalOperationInventoryTests(__import__('unittest').TestCase):
    def test_missing_historical_operation_refuses_even_if_fresh_empty_seal_is_safe(self):
        proof=report(r.OLD_EPOCH)
        expected={'spark0'+str(i):['operation-'+str(i)] for i in range(1,5)}
        with self.assertRaisesRegex(RuntimeError,'Historical registered operation inventory changed'):
            r.verify_seal(proof,r.OLD_EPOCH,list(expected),'example-memguard.service',expected)


class ApplyBoundaryTests(AdmissionFixtureTests):
    """Whole recover() effects use fakes: no subprocess/device/SSH is executed."""
    def setup_apply(self, fault=None):
        import types
        self.capsule['recovery_timeout_s']=7200
        self.capsule['deadline_epoch']=10**12
        pre=self.capsule['manifest']['preflight']
        pre['nodes']=[{'name':'spark0'+str(i)} for i in range(1,5)]
        pre['registered_policy']={'container':'example'}
        self.setattr(r,'verify_old',lambda directory:(self.capsule,{}, {}, {}))
        self.setattr(r,'process_state',lambda record:'alive' if record['pid']==os.getpid() else 'dead')
        self.setattr(r,'_WITNESS',{'path':'/private-witness','roles':{
            'head_hold':{'sha256':r.digest((self.root/'hold.json').read_bytes())}}})
        self.setattr(r,'witness_metadata',lambda *args: None)
        actual_read=r.read
        self.setattr(r,'read',lambda path,**kwargs:b'entry' if Path(path)==PATH else actual_read(path,**kwargs))
        gpu=self.root/'gpu.lock';gpu.touch(mode=0o600)
        original_exists,original_lstat=Path.exists,Path.lstat
        self.setattr(Path,'exists',lambda p:True if str(p)=='/tmp/spark-lab-gpu.lock' else original_exists(p))
        self.setattr(Path,'lstat',lambda p:original_lstat(gpu) if str(p)=='/tmp/spark-lab-gpu.lock' else original_lstat(p))
        self.events=[]
        events=self.events
        class Ops:
            def acquire_gpu_lock(self): return gpu.open('r+')
            def execute(self,session,argv,**kwargs):
                action=argv[argv.index('--adapter-step')+1]
                epoch=argv[argv.index('--epoch')+1]
                events.append(action+':'+epoch)
                if fault=='stop' and action=='stop': raise RuntimeError('STOP failed')
                path=session.directory/('command-'+str(len(events))+'.log')
                r.private_json(path,report(epoch),exclusive=True)
                return {'log_path':str(path)}
            def check(self,phase,manifest):
                events.append(phase)
                if fault==phase: raise RuntimeError('probe failed')
                return {'phase':phase,'positive':True}
            def run(self,*args,**kwargs): return types.SimpleNamespace(returncode=0,stdout='',stderr='')
        @contextlib.contextmanager
        def owned(path,window,holder,**kwargs):
            with Path(path).open('r+') as f:
                yield f.fileno(),json.loads(Path(path).read_text()),Path(path).read_bytes()
        old=types.SimpleNamespace(RuntimeOps=Ops,process_identity=lambda:{'pid':os.getpid(),'start_ticks':'1'})
        watchdog=types.SimpleNamespace(refresh_hold=lambda *args:None,_owned_fd=owned,_same_file=lambda *args:None)
        preflight=types.SimpleNamespace(_node_argv=lambda spec,node,argv:argv)
        self.setattr(r,'load_modules',lambda *args:{'q03_window':old,'q03_window_watchdog':watchdog,
                                                 'q03_window_preflight':preflight})
        if fault=='unlink':
            original_unlink=os.unlink
            self.setattr(os,'unlink',lambda path,*a,**kw:(_ for _ in ()).throw(OSError('unlink failed'))
                         if Path(path)==self.root/'hold.json' else original_unlink(path,*a,**kw))
        elif fault=='fsync':
            original_fsync=os.fsync
            def fsync(fd):
                if Path(os.readlink('/proc/self/fd/'+str(fd)))==self.root:
                    raise OSError('post-unlink fsync failed')
                return original_fsync(fd)
            self.setattr(os,'fsync',fsync)
        elif fault=='final_write':
            original_write=r.private_json
            failed=[]
            def write(path,value,**kwargs):
                if Path(path).name=='state.json' and value.get('hold_released') is True and not failed:
                    failed.append(True);raise OSError('final write failed')
                return original_write(path,value,**kwargs)
            self.setattr(r,'private_json',write)

    def test_whole_apply_success_only_new_journal_changes(self):
        self.setup_apply()
        before={str(p):p.read_bytes() for p in self.old.rglob('*') if p.is_file()}
        result=r.recover(self.old,self.destination,apply=True)
        self.assertEqual(result['phase'],'closed-stopped');self.assertTrue(result['hold_released'])
        self.assertFalse((self.root/'hold.json').exists())
        self.assertTrue((self.destination/'release-intent.json').exists())
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.old.rglob('*') if p.is_file()})
        self.assertFalse(any('start:' in e for e in self.events))
        intent=json.loads((self.destination/'intent.json').read_text())
        self.assertGreater(intent['deadline_epoch']-__import__('time').time(),7195)

    def test_whole_apply_stop_failure_fences_and_keeps_hold(self):
        self.setup_apply('stop')
        with self.assertRaisesRegex(RuntimeError,'STOP failed'):r.recover(self.old,self.destination,apply=True)
        self.assertTrue((self.root/'hold.json').exists())
        self.assertTrue(self.events[-1].startswith('quiesce:'))
        self.assertFalse((self.destination/'release-intent.json').exists())

    def test_whole_apply_drain_failure_never_releases(self):
        self.setup_apply('restore_drained')
        with self.assertRaises(RuntimeError):r.recover(self.old,self.destination,apply=True)
        self.assertTrue((self.root/'hold.json').exists())
        self.assertFalse((self.destination/'release-intent.json').exists())

    def test_whole_apply_unlink_failure_preserves_hold_and_closure_proof(self):
        self.setup_apply('unlink')
        with self.assertRaises(OSError):r.recover(self.old,self.destination,apply=True)
        state=json.loads((self.destination/'state.json').read_text())
        self.assertTrue((self.root/'hold.json').exists())
        self.assertEqual(state['phase'],'closed-stopped');self.assertFalse(state['hold_released'])

    def test_whole_apply_post_unlink_fsync_failure_reports_absent(self):
        self.setup_apply('fsync')
        with self.assertRaises(OSError):r.recover(self.old,self.destination,apply=True)
        state=json.loads((self.destination/'state.json').read_text())
        self.assertFalse((self.root/'hold.json').exists());self.assertTrue(state['hold_released'])
        self.assertTrue(state['hold_absence_observed']);self.assertFalse(state['restored'])

    def test_whole_apply_final_write_failure_does_not_recreate_hold(self):
        self.setup_apply('final_write')
        with self.assertRaises(OSError):r.recover(self.old,self.destination,apply=True)
        state=json.loads((self.destination/'state.json').read_text())
        self.assertFalse((self.root/'hold.json').exists());self.assertTrue(state['hold_released'])
        self.assertEqual(state['outcome'],'release-outcome-uncertain')


for _name in dir(AdmissionFixtureTests):
    if _name.startswith('test_') and _name not in ApplyBoundaryTests.__dict__:
        setattr(ApplyBoundaryTests,_name,None)


class WitnessBindingTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='exact-witness-cpu-')
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()
        # Test-only trust boundary for the outer temporary fixture ancestors.
        original_lstat=Path.lstat
        outer=set(self.root.parents)
        def sandbox_lstat(path,*args,**kwargs):
            value=original_lstat(path,*args,**kwargs)
            if path in outer:
                fields=list(value);fields[0]=(fields[0]&~0o7777)|0o755
                return os.stat_result(fields)
            return value
        patcher=mock.patch.object(Path,'lstat',sandbox_lstat);patcher.start();self.addCleanup(patcher.stop)
        self.parent=self.root/'sessions';self.parent.mkdir(mode=0o700)
        self.old=self.parent/'old';self.old.mkdir(mode=0o700)
        self.restore=self.old/'restore-evidence';self.restore.mkdir(mode=0o775);self.restore.chmod(0o775)
        rows=[]
        def record(path,role):
            st=path.lstat();raw=path.read_bytes() if path.is_file() else None
            row={'path':str(path),'resolved_path':str(path),'exists':True,'role':role,
                 'lstat':{'type':'regular' if raw is not None else 'directory',
                          'mode':format(stat.S_IMODE(st.st_mode),'04o'),'uid':st.st_uid,
                          'gid':st.st_gid,'dev':st.st_dev,'ino':st.st_ino,'nlink':st.st_nlink,
                          'size':st.st_size,'mtime_ns':st.st_mtime_ns,'ctime_ns':st.st_ctime_ns}}
            if raw is not None: row['sha256']=r.digest(raw)
            return row
        file_roles=['capsule_sha256','watchdog_ready','session_lock','watchdog_lock']
        files=[]
        for i,role in enumerate(file_roles+['receipt'+str(n) for n in range(8)]):
            p=(self.old if i<4 else self.restore)/('file'+str(i))
            p.write_text(str(i));p.chmod(0o664);files.append((p,role))
        self.leaf=files[-1][0]
        hold=self.root/'hold';hold.write_text('hold');hold.chmod(0o600)
        rows=[record(self.parent,'session_parent'),record(self.old,'session'),
              record(self.restore,'restore_evidence'),record(hold,'head_hold')]
        rows.extend(record(p,role) for p,role in files)
        self.witness=self.root/'witness.json'
        raw=json.dumps({'window_id':r.WINDOW,'schema_version':1,'records':rows}).encode()
        self.witness.write_bytes(raw);self.witness.chmod(0o600)
        for name,value in [('_WITNESS',None),('WITNESS_SHA256',r.digest(raw))]:
            patcher=mock.patch.object(r,name,value);patcher.start();self.addCleanup(patcher.stop)

    def test_only_witnessed_0775_directory_is_allowed(self):
        with self.assertRaisesRegex(RuntimeError,'ancestry'): r.read(self.leaf,fingerprint=fingerprint(self.leaf))
        r.load_witness(self.witness,self.old)
        self.assertEqual(r.read(self.leaf,fingerprint=fingerprint(self.leaf)),self.leaf.read_bytes())
        foreign=self.root/'foreign';foreign.mkdir(mode=0o775);foreign.chmod(0o775)
        p=foreign/'file';p.write_text('x');p.chmod(0o664)
        with self.assertRaisesRegex(RuntimeError,'ancestry'): r.read(p,fingerprint=fingerprint(p))

    def test_witness_bytes_drift_refuses_before_exception(self):
        self.witness.write_bytes(self.witness.read_bytes()+b' ')
        with self.assertRaisesRegex(RuntimeError,'witness hash'): r.load_witness(self.witness,self.old)
        self.assertIsNone(r._WITNESS)

    def test_witnessed_directory_metadata_drift_refuses(self):
        r.load_witness(self.witness,self.old)
        self.restore.chmod(0o700)
        with self.assertRaisesRegex(RuntimeError,'metadata'): r.verify_witness()

    def test_witnessed_directory_replacement_refuses(self):
        r.load_witness(self.witness,self.old)
        self.restore.rename(self.old/'old-evidence')
        self.restore.mkdir(mode=0o775);self.restore.chmod(0o775)
        with self.assertRaises(RuntimeError): r.ancestry(self.restore)

    def test_parent_journal_creation_and_hold_refresh_are_not_historical_drift(self):
        r.load_witness(self.witness,self.old)
        (self.parent/'new-journal').mkdir(mode=0o700)
        (self.root/'hold').write_text('new!')
        r.verify_witness()

    def test_enclosing_session_must_remain_private(self):
        r.load_witness(self.witness,self.old)
        self.old.chmod(0o750)
        with self.assertRaises(RuntimeError): r.ancestry(self.restore)


if __name__ == '__main__':
    unittest.main()
