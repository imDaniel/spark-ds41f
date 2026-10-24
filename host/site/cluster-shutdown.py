#!/usr/bin/python3 -I
"""Fixed four-node 1022 power coordinator. No power action without --execute.

Installed root-owned; sudo permits spark-monitor to run only fixed modes AS admin.
No command-line host, command, profile, unit or executable override is accepted.
"""
import concurrent.futures
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import stat
import subprocess
import sys
import time
import uuid

POLICY = Path('/etc/ai-cluster/cluster-power.json')
STATE = Path('/var/lib/ai-cluster/cluster-power')
HOME = Path('/home/admin')
REPO = Path('/srv/ai-cluster/runtimes/1022-screen-20261023/source/spark-ds41f')
PROFILE = 'config/cluster.json'
NODES = (1, 2, 3, 4)
WORKERS = (2, 3, 4)
EXPECTED_HOSTS = {1: 'fusionxparkgb10-e4d3', 2: 'fusionxparkgb10-e70f',
                  3: 'fusionxparkgb10-e620', 4: 'fusionxparkgb10-e5b9'}
WORKER_DELAY = 60
HEAD_DELAY = 120
ENV = {'HOME': str(HOME), 'USER': 'admin', 'LOGNAME': 'admin',
       'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}

SNAPSHOT = r'''import json,os,subprocess
r={'hostname':os.uname().nodename}
p=subprocess.run(['/usr/bin/docker','inspect','ds41f-1022-parser-rc3'],text=True,capture_output=True)
if p.returncode:raise SystemExit('Expected deepseek-1022 container is missing')
d=json.loads(p.stdout)[0]
r.update(state=d['State'],id=d['Id'],command=d['Config']['Cmd'],restart_count=d['RestartCount'])
p=subprocess.run(['/usr/bin/systemctl','is-active','ds41f-1022-parser-rc3-memguard.service'],text=True,capture_output=True)
r['memguard']=p.stdout.strip()
p=subprocess.run(['/usr/bin/sudo','-n','-l','/usr/bin/systemctl','poweroff'],text=True,capture_output=True)
r['admin_poweroff_authorized']=p.returncode==0
print(json.dumps(r))'''


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def atomic_json(path, data):
    tmp = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    with tmp.open('x') as f:
        os.chmod(tmp, 0o600)
        json.dump(data, f, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def trusted_file(path):
    if path.is_symlink():
        raise RuntimeError('Refusing symlink: ' + str(path))
    for p in (path, *path.parents):
        s = p.stat()
        if s.st_uid != 0 or stat.S_IMODE(s.st_mode) & 0o022:
            raise RuntimeError('Power program/policy must be root-owned and not group/world writable: ' + str(p))


class Executor:
    """All effects are behind this boundary; tests supply an inert executor."""
    def run(self, node, args, timeout=25):
        if node == 1:
            command = list(args)
        else:
            command = ['/usr/bin/ssh', '-F', '/dev/null', '-i', str(HOME / '.ssh/ai-cluster_ed25519'),
                       '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                       '-o', 'UserKnownHostsFile=' + str(HOME / '.ssh/known_hosts'),
                       '-o', 'ConnectTimeout=5', '-o', 'ConnectionAttempts=1',
                       '-o', 'ClearAllForwardings=yes', 'admin@10.42.0.' + str(node), shlex.join(args)]
        p = subprocess.run(command, env=ENV, cwd=REPO, text=True, capture_output=True, timeout=timeout)
        if p.returncode:
            raise RuntimeError(f'Spark0{node} command failed ({p.returncode}): {(p.stderr or p.stdout).strip()[-1800:]}')
        return p.stdout

    def snapshot(self, node):
        return json.loads(self.run(node, ['/usr/bin/python3', '-I', '-c', SNAPSHOT]))

    def queue(self, node, unit, delay):
        self.run(node, ['/usr/bin/sudo', '-n', '/usr/bin/systemd-run', '--unit=' + unit,
                        '--on-active=' + str(delay) + 's', '--timer-property=AccuracySec=1s',
                        '/usr/bin/systemctl', 'poweroff'], timeout=10)

    def timer_active(self, node, unit):
        return self.run(node, ['/usr/bin/systemctl', 'is-active', unit + '.timer'], timeout=5).strip() == 'active'

    def cancel(self, node, unit):
        self.run(node, ['/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'stop', unit + '.timer', unit + '.service'], timeout=8)


def validate_snapshot(node, row, stopped=False):
    if row['hostname'] != EXPECTED_HOSTS[node]:
        raise RuntimeError('Wrong host identity for Spark0' + str(node))
    if not row['admin_poweroff_authorized']:
        raise RuntimeError('Admin poweroff authorization missing on Spark0' + str(node))
    cmd = row['command']
    for flag in ('--nnodes', '--tensor-parallel-size'):
        if flag not in cmd or cmd[cmd.index(flag) + 1] != '4':
            raise RuntimeError('Expected four-node TP4 1022 shape on Spark0' + str(node))
    if row['state'].get('Paused') or row['state'].get('Restarting'):
        raise RuntimeError('Engine has ambiguous state on Spark0' + str(node))
    if stopped and (row['state']['Running'] or row['memguard'] == 'active'):
        raise RuntimeError('1022/guard did not stop on Spark0' + str(node))


def inspect_fleet(executor, stopped=False):
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        rows = dict(zip(NODES, pool.map(executor.snapshot, NODES)))
    for n, row in rows.items():
        validate_snapshot(n, row, stopped)
    return rows


def stop_args(apply=False):
    args = [str(REPO / 'bin/spark'), '--cluster-config', PROFILE, 'cluster', 'stop', '--parallel']
    return args + (['--apply'] if apply else [])


def plan(executor):
    rows = inspect_fleet(executor)
    text = executor.run(1, stop_args(), timeout=40)
    if '--apply would refuse:' in text:
        raise RuntimeError('Original 1022 coordinator refused this stop plan')
    for n in NODES:
        if f'spark0{n}: stop exact container ds41f-1022-parser-rc3' not in text:
            raise RuntimeError('Stop plan does not cover all four configured nodes')
    return {'passed': True, 'phase': 'checked', 'nodes': rows, 'stop_plan': text,
            'shutdown_order': ['spark02', 'spark03', 'spark04', 'spark01'],
            'worker_delay_seconds': WORKER_DELAY, 'head_delay_seconds': HEAD_DELAY}


def execute(executor, report):
    """Stop and verify EVERY rank before queuing ANY poweroff; head queued LAST."""
    unit = 'ai-cluster-poweroff-' + uuid.uuid4().hex
    report.update(unit=unit, phase='stopping-1022', started_utc=utc(), queued=[], rollback_errors=[])
    try:
        report['stop_output'] = executor.run(1, stop_args(apply=True), timeout=160)
        report['stopped_fleet'] = inspect_fleet(executor, stopped=True)
        report['phase'] = 'syncing-disks'
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda n: executor.run(n, ['/usr/bin/sync']), NODES))
        report['phase'] = 'queuing-workers'
        began = time.monotonic()
        errors = []
        # Collect every result even when one dispatch fails, so rollback includes
        # every remotely armed timer. Never use map's early exception exit here.
        def worker(n):
            try:
                executor.queue(n, unit, WORKER_DELAY)
                return n, None
            except Exception as e:
                return n, str(e)
        report['queued'].extend(WORKERS)
        with concurrent.futures.ThreadPoolExecutor(3) as pool:
            for n, error in pool.map(worker, WORKERS):
                # Also cancel a failed dispatch: the SSH response may have been
                # lost AFTER systemd accepted the request.
                if error:
                    errors.append(f'Spark0{n}: {error}')
        if errors:
            raise RuntimeError('; '.join(errors))
        with concurrent.futures.ThreadPoolExecutor(3) as pool:
            active = list(pool.map(lambda n: executor.timer_active(n, unit), WORKERS))
        if not all(active):
            raise RuntimeError('One or more worker timers did not arm')
        if time.monotonic() - began > 20:
            raise RuntimeError('Worker dispatch took too long; cancelling before shutdown deadline')
        report['phase'] = 'queuing-head'
        # Record this before requesting too: on lost response, cancel the head as well.
        report['queued'].append(1)
        executor.queue(1, unit, HEAD_DELAY)
        if not executor.timer_active(1, unit):
            raise RuntimeError('Head timer did not arm')
        report.update(passed=True, phase='scheduled', scheduled_utc=utc(),
                      message='1022 stopped and verified; Spark02-04 shutdown scheduled before Spark01.')
        return report
    except BaseException as e:
        def cancel(n):
            try:
                executor.cancel(n, unit)
                return None
            except Exception as rollback:
                return {'node': n, 'error': str(rollback)}
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            report['rollback_errors'] = [r for r in pool.map(cancel, report['queued']) if r]
        report.update(passed=False, phase='failed', error=str(e), finished_utc=utc())
        raise


def pending(executor):
    path = STATE / 'latest.json'
    if not path.exists():
        return None
    row = json.loads(path.read_text())
    if row.get('phase') != 'scheduled':
        return None
    unit = row.get('unit', '')
    if not re.fullmatch(r'ai-cluster-poweroff-[0-9a-f]{32}', unit):
        raise RuntimeError('Invalid stored shutdown job identity')
    try:
        if executor.timer_active(1, unit):
            return row
    except RuntimeError:
        pass
    return None


def validate_installation():
    trusted_file(Path(__file__))
    trusted_file(POLICY)
    policy = json.loads(POLICY.read_text())
    if os.geteuid() != 1000 or os.uname().nodename != EXPECTED_HOSTS[1]:
        raise RuntimeError('Coordinator must run as admin on Spark01')
    if policy.get('schema_version') != 1 or policy.get('nodes') != ['spark01', 'spark02', 'spark03', 'spark04']:
        raise RuntimeError('Invalid fixed cluster power policy')
    for path, expected in policy['pinned_files'].items():
        file = Path(path)
        if file.is_symlink() or not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != expected:
            raise RuntimeError('Pinned cluster shutdown input changed; review policy: ' + path)
    key = HOME / '.ssh/ai-cluster_ed25519'
    if not key.is_file() or key.stat().st_mode & 0o077:
        raise RuntimeError('Admin cluster SSH identity missing or too permissive')


def main(argv=None):
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    def interrupted(signum, frame):
        raise RuntimeError('Shutdown request interrupted; cancelling queued timers')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    argv = sys.argv[1:] if argv is None else argv
    if argv not in (['--check'], ['--dry-run'], ['--execute']):
        raise ValueError('Only --check, --dry-run or --execute is accepted; no overrides')
    validate_installation()
    executor = Executor()
    # A maintenance owner takes precedence; dry-run refuses too, without mutation.
    hold = HOME / 'spark-hold.json'
    if hold.exists() or (HOME / 'spark3-hold.json').exists():
        raise RuntimeError('Another maintenance window is active; no shutdown actions taken')
    if argv != ['--execute']:
        result = plan(executor)
        result.update(phase='dry-run' if argv == ['--dry-run'] else 'checked',
                      dry_run=True, power_actions_taken=False)
        print(json.dumps(result))
        return
    with (STATE / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = pending(executor)
        if previous:
            print(json.dumps(dict(previous, already_scheduled=True)))
            return
        report = plan(executor)
        report['job_id'] = uuid.uuid4().hex
        owned = {'holder': 'sparkdash-cluster-shutdown', 'since': utc(), 'heartbeat': utc(),
                 'expected_end': None, 'rule': 'Coordinated four-rank 1022 stop and shutdown scheduling'}
        with hold.open('x') as f:
            json.dump(owned, f)
        try:
            execute(executor, report)
        finally:
            atomic_json(STATE / (report['job_id'] + '.json'), report)
            atomic_json(STATE / 'latest.json', report)
            # No hold may survive into the next boot or suppress health detection.
            if hold.exists() and json.loads(hold.read_text()).get('holder') == owned['holder']:
                hold.unlink()
        print(json.dumps(report))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'passed': False, 'error': str(error), 'automatic_retry': False}), file=sys.stderr)
        sys.exit(1)
