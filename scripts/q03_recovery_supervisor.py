#!/usr/bin/env python3
"""Independent, one-retry supervisor for the narrowly admitted recovery entry.

Never edits the old capsule, kills a numeric PID, or treats main death as daemon
quiescence. The retry entry acquires existing locks and drains its journalled
command supervisor before sealing epochs and restoring registered services.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import uuid


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def read(path):
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path, 'path alias')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_size <= 4 * 1024 * 1024,
                'invalid bounded journal file')
        return stream.read()


def exclusive(path, value):
    # Publish only complete fsynced bytes; link refuses an existing final record.
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(value, out, sort_keys=True)
            out.flush(); os.fsync(out.fileno())
        os.link(temporary, path, follow_symlinks=False)
    finally:
        temporary.unlink()
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def process_state(record):
    """Unknown proc errors refuse takeover; PID reuse never signals a stranger."""
    try:
        fields = Path(f"/proc/{int(record['pid'])}/stat").read_text().rsplit(')', 1)[1].split()
        state, start = fields[0], fields[19]
    except (FileNotFoundError, ProcessLookupError):
        return 'dead'
    except (OSError, ValueError, IndexError):
        return 'unknown'
    if start != str(record['start_ticks']):
        return 'reused'
    return 'dead' if state == 'Z' else 'alive'


def identity(pid):
    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    require(fields[0] != 'Z', 'process already terminal')
    return {'pid': pid, 'start_ticks': fields[19]}


def verify_sources(directory):
    raw = read(directory / 'intent.json')
    intent = json.loads(raw)
    here = Path(__file__).resolve().parent
    pins = intent['new_source_sha256']
    require(set(pins) == {'q03_reconcile_restore.py', 'q03_recovery_compat.py',
                         'q03_remote_operation.py', 'q03_recovery_supervisor.py'},
            'recovery source inventory differs')
    for name, expected in pins.items():
        require(hashlib.sha256(read(here / name)).hexdigest() == expected,
                'recovery source changed: ' + name)
    capsule_raw = read(Path(intent['old_session']) / 'capsule.json')
    require(hashlib.sha256(capsule_raw).hexdigest() == intent['capsule_sha256'],
            'old capsule changed')
    capsule = json.loads(capsule_raw)
    require(capsule['window_id'] == intent['window_id'], 'window differs')
    return intent, capsule, hashlib.sha256(raw).hexdigest()


def retry_command(intent, directory):
    here = Path(__file__).resolve().parent
    command = [sys.executable, '-I', '-B', str(here / 'q03_reconcile_restore.py'),
            '--old-session', intent['old_session'], '--capsule-sha256', intent['capsule_sha256'],
            '--journal-dir', str(directory), '--compat-path', str(here / 'q03_recovery_compat.py'),
            '--compat-sha256', intent['compat_sha256'], '--apply']
    require(command == intent['recovery_command'], 'retry command differs from immutable intent')
    return [*command, '--watchdog-retry']


class Ops:
    now = staticmethod(time.time)
    sleep = staticmethod(time.sleep)
    process_state = staticmethod(process_state)

    def snapshot(self, directory):
        intent, capsule, digest = verify_sources(directory)
        state = json.loads(read(directory / 'state.json'))
        try:
            hold = json.loads(read(capsule['hold_path']))
        except FileNotFoundError:
            hold = None
        return intent, capsule, digest, state, hold

    def launch_retry(self, directory, intent, main):
        # A durable child identity precedes gate release and any entry execution.
        r, w = os.pipe()
        gate = ('import os,sys; f=int(sys.argv[1]); b=os.read(f,1); os.close(f); '
                'os.execvpe(sys.argv[2],sys.argv[2:],os.environ) if b==b"1" else sys.exit(125)')
        with (directory / 'supervisor-retry.log').open('xb') as log:
            child = subprocess.Popen([sys.executable, '-I', '-B', '-c', gate, str(r),
                                      *retry_command(intent, directory)],
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                     pass_fds=(r,), start_new_session=True)
        os.close(r)
        try:
            record = identity(child.pid)
            exclusive(directory / 'supervisor-retry.json',
                      {'main': main, 'child': record, 'supervisor': identity(os.getpid()),
                       'intent_sha256': hashlib.sha256(read(directory / 'intent.json')).hexdigest()})
            os.write(w, b'1')
        finally:
            os.close(w)
        return record


def monitor(directory, main, deadline_epoch, ops=None):
    ops = ops or Ops()
    initial, _, digest, _, _ = ops.snapshot(directory)
    require(initial['deadline_epoch'] == deadline_epoch,
            'supervisor deadline differs from durable intent')
    target, retried = main, False
    while True:
        intent, capsule, current_digest, state, hold = ops.snapshot(directory)
        require(current_digest == digest, 'recovery intent changed')
        if state.get('restored') is True and hold is None:
            return {'status': 'restored-marker-observed', 'window_id': intent['window_id'],
                    'retry_started': retried, 'hold_absent_observed': True}
        require(isinstance(hold, dict) and hold.get('window') == intent['window_id']
                and hold.get('holder') == capsule['holder'], 'owned hold lost')
        require(ops.now() < deadline_epoch, 'recovery deadline reached; UNKNOWN, retain hold')
        health = ops.process_state(target)
        require(health in ('alive', 'dead'), 'process identity uncertain; UNKNOWN, retain hold')
        if health == 'dead':
            require(not retried, 'single recovery retry ended without restoration; UNKNOWN, retain hold')
            target = ops.launch_retry(directory, intent, main)
            retried = True
        ops.sleep(1)


def start_watchdog(journal_dir, main_identity, deadline_epoch):
    directory = Path(journal_dir)
    intent, _, digest = verify_sources(directory)
    require(intent['deadline_epoch'] == deadline_epoch and
            time.time() < deadline_epoch <= time.time() + 7200, 'invalid bounded deadline')
    require(process_state(main_identity) == 'alive', 'main identity is not alive')
    command = [sys.executable, '-I', '-B', str(Path(__file__).resolve()),
               '--journal-dir', str(directory), '--main-pid', str(main_identity['pid']),
               '--main-start', str(main_identity['start_ticks']),
               '--deadline-epoch', str(deadline_epoch)]
    with (directory / 'supervisor.log').open('xb') as log:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
    expected = identity(process.pid)
    end = time.monotonic() + 10
    while time.monotonic() < end:
        path = directory / 'supervisor-ready.json'
        if path.exists():
            ready = json.loads(read(path))
            require(ready['supervisor'] == expected and ready['main'] == main_identity
                    and ready['intent_sha256'] == digest and process_state(expected) == 'alive',
                    'independent supervisor readiness mismatch')
            return ready
        require(process.poll() is None, 'independent supervisor exited before readiness')
        time.sleep(.05)
    raise RuntimeError('independent supervisor readiness timed out; no new mutation admitted')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journal-dir', required=True)
    parser.add_argument('--main-pid', required=True, type=int)
    parser.add_argument('--main-start', required=True)
    parser.add_argument('--deadline-epoch', required=True, type=float)
    args = parser.parse_args()
    directory = Path(args.journal_dir)
    fd = os.open(directory / 'supervisor.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        intent, _, digest = verify_sources(directory)
        require(intent['deadline_epoch'] == args.deadline_epoch, 'deadline mismatch')
        main_record = {'pid': args.main_pid, 'start_ticks': args.main_start}
        _, capsule, _, state, hold = Ops().snapshot(directory)
        require(state.get('recovery_process') == main_record and
                process_state(main_record) == 'alive', 'main identity differs before readiness')
        require(isinstance(hold, dict) and hold.get('window') == intent['window_id']
                and hold.get('holder') == capsule['holder'], 'owned hold lost before readiness')
        require(time.time() < args.deadline_epoch, 'deadline elapsed before readiness')
        exclusive(directory / 'supervisor-ready.json', {'supervisor': identity(os.getpid()),
                  'main': main_record, 'intent_sha256': digest})
        try:
            result = monitor(directory, main_record, args.deadline_epoch)
        except BaseException as error:
            exclusive(directory / 'supervisor-result.json',
                      {'status': 'UNKNOWN', 'error': str(error), 'hold_state': 'unverified'})
            raise
        exclusive(directory / 'supervisor-result.json', result)


if __name__ == '__main__':
    main()
