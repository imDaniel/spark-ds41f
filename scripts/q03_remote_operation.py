#!/usr/bin/env python3
"""Durable remote-operation receipts. Import is inert; launch is a site action.

Only trusted original wrapper argv may be submitted; this is not an argv sandbox.
The site CLI fixes root/cwd to the independent namespace. Pure Python APIs accept
temporary roots for injected CPU tests. A terminal receipt proves
that the supervised local process group ended, not detached service readiness.
A missing/dead supervisor without a receipt is uncertain, never safe to restore.
"""
from __future__ import annotations
import argparse
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
import re
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid


REMOTE_ENV_EXEC = (
    'import os,sys; e={k:v for k,v in os.environ.items() '
    'if not k.startswith(("DOCKER_","BUILDX_","BUILDKIT_"))}; '
    'e.update(DOCKER_HOST="unix:///var/run/docker.sock",'
    'GIT_OPTIONAL_LOCKS="0",PYTHONDONTWRITEBYTECODE="1"); '
    'os.execvpe(sys.argv[1],sys.argv[1:],e)'
)


def operation_environment(inherited):
    env = {key: value for key, value in inherited.items()
           if not key.startswith(('DOCKER_', 'BUILDX_', 'BUILDKIT_'))}
    env.update(DOCKER_HOST='unix:///var/run/docker.sock',
               GIT_OPTIONAL_LOCKS='0', PYTHONDONTWRITEBYTECODE='1')
    return env


def bound_command(command):
    # Preserve original argv exactly, changing only its inherited environment.
    return ['python3', '-I', '-B', '-c', REMOTE_ENV_EXEC, *command]


LOG_STREAM_LIMIT = 4 * 1024 * 1024
LOG_TIMEOUT_SECONDS = 20


def log_capture_id(argv):
    """Only this closed, read-only Docker operation gets failure-safe fencing."""
    prefix = bound_command(['docker', 'logs', '--timestamps', '--'])
    if (isinstance(argv, list) and argv[:-1] == prefix and
            isinstance(argv[-1], str) and re.fullmatch(r'[0-9a-f]{64}', argv[-1])):
        return argv[-1]
    return None


def log_streams(directory):
    import stat
    streams, errors = {}, {}
    for key, name in (('stdout', 'output.log'), ('stderr', 'error.log')):
        path = directory / name
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as source:
                before = os.fstat(source.fileno())
                if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                        or before.st_mode & 0o022 or before.st_size > LOG_STREAM_LIMIT):
                    raise RuntimeError('log artifact type/owner/mode/size differs')
                raw = source.read(LOG_STREAM_LIMIT + 1)
                after = os.fstat(source.fileno())
                current = path.lstat()
            if (len(raw) != before.st_size or
                    (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                    (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or
                    (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) !=
                    (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)):
                raise RuntimeError('log artifact changed while reading')
            streams[key] = dict(data_base64=base64.b64encode(raw).decode('ascii'),
                                bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        except (OSError, RuntimeError) as error:
            # Evidence loss is not process liveness. Keep sealed read-only
            # failures retryable without ever claiming that missing bytes exist.
            errors[key] = type(error).__name__ + ': ' + str(error)
    return streams, errors


def acknowledged_memguard_absence(argv, receipt, stdout, stderr, expected_unit=None):
    """Recognize only the original stop's benign, already-unloaded memguard case.

    This does not turn the action into success or rewrite its terminal receipt.
    The adapter additionally binds the unit to its registered original service.
    """
    prefix = bound_command(['sudo', '-n', 'systemctl', 'stop'])
    if not isinstance(argv, list) or argv[:-1] != prefix:
        return False
    unit = argv[-1]
    if (not isinstance(unit, str) or
            re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*-memguard\.service', unit) is None or
            (expected_unit is not None and unit != expected_unit)):
        return False
    return (type(receipt.get('exit_code')) is int and receipt['exit_code'] == 5 and
            receipt.get('group_quiescent') is True and
            receipt.get('cancelled') is False and receipt.get('timed_out') is False and
            stdout == '' and isinstance(stderr, str) and
            stderr in (f'Failed to stop {unit}: Unit {unit} not loaded.',
                       f'Failed to stop {unit}: Unit {unit} not loaded.\n'))


def atomic(path, value):
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with tmp.open('x') as out:
        json.dump(value, out, sort_keys=True)
        out.flush()
        os.fsync(out.fileno())
    os.replace(tmp, path)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def identity(pid):
    try:
        fields = Path(f'/proc/{int(pid)}/stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] == 'Z':
            return None
        return {'pid': int(pid), 'start_ticks': fields[19]}
    except (OSError, ValueError, IndexError):
        return None


def read_authority(path, expected_sha256):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError('authority path must be absolute without symlink aliases')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        import stat
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or before.st_mode & 0o022):
            raise RuntimeError('authority must be an owned, non-shared-writable regular file')
        with os.fdopen(os.dup(fd), 'rb') as stream:
            raw = stream.read()
        after = path.lstat()
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise RuntimeError('authority file changed while reading')
    finally:
        os.close(fd)
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise RuntimeError('private authority digest mismatch')
    value = json.loads(raw)
    root = Path(value['independent_root'])
    protected = value['protected_roots']
    if not root.is_absolute() or root.resolve() != root or not protected:
        raise ValueError('authority root/protected boundaries are incomplete')
    if not path.is_relative_to(root) or path == root:
        raise ValueError('private authority must remain inside independent namespace')
    for item in protected:
        target = Path(item)
        if not target.is_absolute():
            raise ValueError('protected authority boundary must be absolute')
        for candidate in (target, target.resolve()):
            if root.is_relative_to(candidate) or candidate.is_relative_to(root):
                raise ValueError('independent authority overlaps protected namespace')
    return value


def verified_helper_command(path, expected_sha256, arguments):
    """Read, verify and execute the same helper bytes; never hash then reopen."""
    bootstrap = (
        'import hashlib,pathlib,sys,os,stat; p=sys.argv[1]; h=sys.argv[2]; '
        '(pathlib.Path(p).is_absolute() and str(pathlib.Path(p).resolve())==p) '
        'or sys.exit("helper path alias"); '
        'f=os.open(p,os.O_RDONLY|os.O_NOFOLLOW); s=os.fstat(f); '
        '(stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid() and not s.st_mode&0o022) '
        'or sys.exit("helper ownership drift"); '
        'b=os.fdopen(f,"rb").read(); '
        'hashlib.sha256(b).hexdigest()==h or sys.exit("remote helper source drift"); '
        'sys.argv=[p,*sys.argv[3:]]; '
        'exec(compile(b,p,"exec"),{"__name__":"__main__","__file__":p,'
        '"_Q03_VERIFIED_SOURCE_SHA256":h})'
    )
    return [sys.executable, '-I', '-B', '-c', bootstrap, str(path), expected_sha256, *arguments]


def operation_dir(root, window, operation):
    window, operation = uuid.UUID(window).hex, uuid.UUID(operation).hex
    root = Path(root)
    if not root.is_absolute() or root.resolve() != root:
        raise ValueError('operation root must be absolute, without symlink aliases')
    path = root / window / operation
    if path.resolve() != path:
        raise ValueError('operation path aliases another owner')
    return path


def read_manifest(directory):
    raw = (directory / 'manifest.json').read_bytes()
    expected = (directory / 'manifest.sha256').read_text().strip()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise RuntimeError('operation manifest digest mismatch')
    m = json.loads(raw)
    if operation_dir(m['root'], m['window'], m['operation']) != directory:
        raise RuntimeError('operation ownership mismatch')
    return m, expected


@contextmanager
def epoch_lock(root, window, epoch):
    window, epoch = uuid.UUID(window).hex, uuid.UUID(epoch).hex
    # A seal can create an empty epoch first; all delayed launches then fail.
    base = operation_dir(root, window, epoch).parent / 'epochs' / epoch
    if base.resolve() != base:
        raise ValueError('epoch path aliases another owner')
    base.mkdir(parents=True, exist_ok=True)
    fd = os.open(base / 'lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = base / 'epoch.json'
        record = (json.loads(path.read_text()) if path.exists() else
                  dict(window=window, epoch=epoch, sealed=False, operations=[]))
        if record['window'] != window or record['epoch'] != epoch:
            raise RuntimeError('foreign epoch record')
        yield path, record


def seal_epoch(root, window, epoch, alive=None):
    window, epoch = uuid.UUID(window).hex, uuid.UUID(epoch).hex
    with epoch_lock(root, window, epoch) as (path, record):
        record['sealed'] = True
        atomic(path, record)
        operations = list(record['operations'])
    results = [status(root, window, operation, alive) for operation in operations]
    safe = all(item.get('safe_to_overlap') is True for item in results)
    return dict(window=window, epoch=epoch, sealed=True, operations=results,
                safe_to_overlap=safe, status='quiescent' if safe else 'blocked')


def create_operation(root, window, operation, argv, cwd, timeout_seconds, epoch):
    window, operation, epoch = (uuid.UUID(x).hex for x in (window, operation, epoch))
    directory = operation_dir(root, window, operation)
    if (not isinstance(argv, list) or not argv or
            any(not isinstance(x, str) or '\0' in x for x in argv)):
        raise ValueError('explicit trusted argv required')
    if not Path(cwd).is_absolute():
        raise ValueError('absolute command working directory required')
    if (type(timeout_seconds) not in (int, float) or
            not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise ValueError('positive bounded command timeout required')
    m = dict(root=str(Path(root)), window=window, epoch=epoch, operation=operation, argv=argv,
             cwd=cwd, timeout_seconds=timeout_seconds, created_epoch=time.time(),
             helper_sha256=globals().get('_Q03_VERIFIED_SOURCE_SHA256',
                 hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))
    with epoch_lock(root, window, epoch) as (path, record):
        if record['sealed']:
            raise RuntimeError('transition epoch is permanently sealed')
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        atomic(directory / 'manifest.json', m)
        digest = hashlib.sha256((directory / 'manifest.json').read_bytes()).hexdigest()
        with (directory / 'manifest.sha256').open('x') as out:
            out.write(digest + '\n')
            out.flush()
            os.fsync(out.fileno())
        atomic(directory / 'state.json', {'phase': 'created'})
        record['operations'].append(operation)
        atomic(path, record)  # registered durably before any supervisor can fork
    return directory


def status(root, window, operation, alive=None):
    window, operation = uuid.UUID(window).hex, uuid.UUID(operation).hex
    directory = operation_dir(root, window, operation)
    m, digest = read_manifest(directory)
    state = json.loads((directory / 'state.json').read_text())
    receipt = directory / 'receipt.json'
    if receipt.exists():
        result = json.loads(receipt.read_text())
        if (result.get('manifest_sha256') != digest or
                result.get('window') != window or result.get('operation') != operation or
                result.get('group_quiescent') is not True):
            raise RuntimeError('invalid terminal receipt')
        safe = (result.get('cancelled') is False and result.get('timed_out') is False
                and type(result.get('exit_code')) is int and result['exit_code'] == 0)
        # A shell's normal nonzero exit can hide an interrupted Docker client.
        # Unknown/mutating errors never authorize overlap; the exact unloaded
        # memguard lifecycle case below is classified separately, not successful.
        with epoch_lock(root, window, m['epoch']) as (_, record):
            sealed = record['sealed'] and operation in record['operations']
        def output(name):
            path = directory / name
            return path.read_text(errors='replace') if path.exists() else ''
        captured_id = log_capture_id(m['argv'])
        if captured_id is not None:
            streams, artifact_errors = log_streams(directory)
            truncated = any(v['bytes'] >= LOG_STREAM_LIMIT for v in streams.values())
            # A failed READ is not evidence success. Once its exact process group
            # is gone and epoch is sealed it cannot mutate/start a daemon. This
            # exception never applies to inspect, rm, stop, or arbitrary argv.
            return dict(status='terminal', safe_to_overlap=sealed,
                        action_succeeded=safe, epoch_sealed=sealed,
                        stdout='', stderr='', log_capture=dict(
                            container_id=captured_id, timestamps=True,
                            stream_limit_bytes=LOG_STREAM_LIMIT, truncated=truncated,
                            complete=safe and not truncated and not artifact_errors,
                            artifact_errors=artifact_errors, streams=streams), **result)
        stdout, stderr = output('output.log'), output('error.log')
        acknowledged = acknowledged_memguard_absence(m['argv'], result, stdout, stderr)
        return dict(status='terminal', safe_to_overlap=((safe or acknowledged) and sealed),
                    action_succeeded=safe, epoch_sealed=sealed,
                    lifecycle_acknowledged=acknowledged,
                    lifecycle_unit=m['argv'][-1] if acknowledged else None,
                    stdout=stdout, stderr=stderr, **result)
    alive = alive or (lambda rec: identity(rec['pid']) == rec)
    supervisor = state.get('supervisor')
    health = 'running' if supervisor and alive(supervisor) else 'uncertain'
    return dict(status=health, window=window, operation=operation,
                manifest_sha256=digest, state=state)


def request_cancel(root, window, operation):
    window, operation = uuid.UUID(window).hex, uuid.UUID(operation).hex
    directory = operation_dir(root, window, operation)
    _, digest = read_manifest(directory)
    atomic(directory / 'cancel.json', dict(window=window, operation=operation,
                                          manifest_sha256=digest))
    return status(root, window, operation)


class LocalOps:
    now = staticmethod(time.monotonic)
    sleep = staticmethod(time.sleep)

    def launch_gated(self, manifest, log):
        r, w = os.pipe()
        gate = ('import os,sys; f=int(sys.argv[1]); b=os.read(f,1); os.close(f); '
                'os.execvpe(sys.argv[2],sys.argv[2:],os.environ) if b==b"1" else sys.exit(125)')
        if log_capture_id(manifest['argv']) is not None:
            # The kernel caps BOTH files, even if the client/supervisor stalls.
            # The limit is inherited only by this exact read-only log client.
            gate = ('import resource; resource.setrlimit(resource.RLIMIT_FSIZE, ('
                    + str(LOG_STREAM_LIMIT) + ',' + str(LOG_STREAM_LIMIT) + ')); ' + gate)
        self.stderr = Path(log.name).with_name('error.log').open('ab')
        try:
            child = subprocess.Popen([sys.executable, '-I', '-B', '-c', gate, str(r), *manifest['argv']],
                                     cwd=manifest['cwd'], stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=self.stderr,
                                     start_new_session=True, pass_fds=(r,),
                                     env=operation_environment(os.environ))
        except BaseException:
            self.stderr.close()
            os.close(w)
            raise
        finally:
            os.close(r)
        self.child, self.gate = child, w
        record = identity(child.pid)
        if record is None:
            raise RuntimeError('child identity unavailable before launch gate')
        return record

    def release_gate(self):
        os.write(self.gate, b'1')
        os.close(self.gate)
        self.gate = None

    def exited(self):
        # Do not reap yet: the group's leader PID stays reserved until all
        # members stop, preventing PID/PGID reuse during a cancellation signal.
        return os.waitid(os.P_PID, self.child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None

    def group_live(self):
        for path in Path('/proc').glob('[0-9]*/stat'):
            try:
                fields = path.read_text().rsplit(')', 1)[1].split()
                if int(fields[2]) == self.child.pid and fields[0] != 'Z':
                    return True
            except FileNotFoundError:
                continue
        return False

    def terminate_group(self):
        # Only this supervisor's unreaped child group, never a PID from a file.
        os.killpg(self.child.pid, signal.SIGKILL)

    def reap(self):
        result = self.child.wait(timeout=5)
        self.stderr.flush()
        os.fsync(self.stderr.fileno())
        self.stderr.close()
        return result


def supervise(directory, ops):
    """Injectable state machine; caller holds the exclusive supervisor lease."""
    directory = Path(directory)
    m, digest = read_manifest(directory)
    owner = identity(os.getpid())
    state = dict(phase='starting', supervisor=owner)
    atomic(directory / 'state.json', state)
    with (directory / 'output.log').open('ab') as log:
        child = ops.launch_gated(m, log)
        state.update(phase='running', child=child)
        atomic(directory / 'state.json', state)  # durable BEFORE execution gate
        ops.release_gate()
        deadline = ops.now() + m['timeout_seconds']
        cancelled = False
        while True:
            if (directory / 'cancel.json').exists():
                request = json.loads((directory / 'cancel.json').read_text())
                if request != dict(window=m['window'], operation=m['operation'], manifest_sha256=digest):
                    raise RuntimeError('foreign cancellation request')
                cancelled = True
            timed_out = ops.now() >= deadline
            if cancelled or timed_out:
                ops.terminate_group()
                end = ops.now() + 10
                while ops.group_live() and ops.now() < end:
                    ops.sleep(.1)
                if ops.group_live():
                    raise RuntimeError('owned process group failed to quiesce')
                code = ops.reap()
                break
            if ops.exited() and not ops.group_live():
                code = ops.reap()
                break
            ops.sleep(.1)
        log.flush()
        os.fsync(log.fileno())
        result = dict(window=m['window'], operation=m['operation'], manifest_sha256=digest,
                      group_quiescent=True, exit_code=code, cancelled=cancelled,
                      timed_out=timed_out, finished_epoch=time.time())
        atomic(directory / 'receipt.json', result)
        atomic(directory / 'state.json', dict(state, phase='terminal'))
        return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('launch', 'status', 'cancel', 'seal', 'verify', '_supervise'))
    p.add_argument('--root', required=True)
    p.add_argument('--authority', required=True)
    p.add_argument('--authority-sha256', required=True)
    p.add_argument('--window', required=True)
    p.add_argument('--operation')
    p.add_argument('--epoch', required=True)
    p.add_argument('--argv-json')
    p.add_argument('--cwd')
    p.add_argument('--timeout-seconds', type=float)
    a = p.parse_args()
    a.window, a.epoch = uuid.UUID(a.window).hex, uuid.UUID(a.epoch).hex
    if a.operation is not None:
        a.operation = uuid.UUID(a.operation).hex
    authority = read_authority(a.authority, a.authority_sha256)
    approved = Path(authority['independent_root'])
    if Path(a.root) != approved / 'remote-operations':
        raise ValueError('operation root differs from approved independent namespace')
    if a.action == 'launch' and (not a.cwd or
            (Path(a.cwd).resolve() != approved and approved not in Path(a.cwd).resolve().parents)):
        raise ValueError('helper cwd must be inside independent namespace')
    if a.action == 'verify':
        print(json.dumps(dict(window=a.window, epoch=a.epoch,
                              authority_sha256=a.authority_sha256,
                              helper_sha256=globals().get('_Q03_VERIFIED_SOURCE_SHA256',
                                  hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))))
        return 0
    if a.action == 'seal':
        print(json.dumps(seal_epoch(a.root, a.window, a.epoch), sort_keys=True))
        return 0
    directory = operation_dir(a.root, a.window, a.operation)
    if a.action == 'launch':
        directory = create_operation(a.root, a.window, a.operation,
                                     json.loads(a.argv_json), a.cwd, a.timeout_seconds, a.epoch)
        with (directory / 'supervisor.log').open('xb') as log:
            expected = read_manifest(directory)[0]['helper_sha256']
            subprocess.Popen(verified_helper_command(Path(__file__).resolve(), expected,
                              ['_supervise', '--root', a.root, '--window', a.window,
                               '--operation', a.operation, '--epoch', a.epoch,
                               '--authority', a.authority, '--authority-sha256', a.authority_sha256]),
                             stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                             start_new_session=True, close_fds=True)
        result = status(a.root, a.window, a.operation)
    elif a.action == '_supervise':
        m, _ = read_manifest(directory)
        if m['epoch'] != a.epoch:
            raise RuntimeError('operation epoch mismatch')
        with epoch_lock(a.root, a.window, a.epoch) as (_, record):
            if a.operation not in record['operations']:
                raise RuntimeError('operation was not durably registered')
        if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != m['helper_sha256']:
            raise RuntimeError('operation helper changed')
        with (directory / 'supervisor.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if (directory / 'receipt.json').exists():
                raise RuntimeError('terminal operation cannot be restarted')
            # Refuse restarting a previously launched/uncertain operation.
            if json.loads((directory / 'state.json').read_text())['phase'] != 'created':
                raise RuntimeError('operation supervisor cannot be taken over')
            try:
                result = supervise(directory, LocalOps())
            except BaseException as error:
                state = json.loads((directory / 'state.json').read_text())
                atomic(directory / 'state.json', dict(state, phase='uncertain', error=str(error)))
                raise
    elif a.action == 'cancel':
        if read_manifest(directory)[0]['epoch'] != a.epoch:
            raise RuntimeError('operation epoch mismatch')
        result = request_cancel(a.root, a.window, a.operation)
    else:
        if read_manifest(directory)[0]['epoch'] != a.epoch:
            raise RuntimeError('operation epoch mismatch')
        result = status(a.root, a.window, a.operation)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        print(json.dumps({'status': 'uncertain', 'error': str(error)}), file=sys.stderr)
        sys.exit(1)
