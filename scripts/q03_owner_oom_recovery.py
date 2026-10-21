#!/usr/bin/env python3
"""Narrow, separately frozen recovery for a b8 owner false->null OOM default.

Loads the unchanged legacy capsule's own verified recovery implementation.
Does not rewrite its source, capsule, plan, recorded hashes, or raw inspect.
No launch/start/resume interface is provided. The legacy final all-four drain
and keep-stopped policy remain authoritative.
"""
from __future__ import annotations
import argparse
import copy
import contextlib
import fcntl
import hashlib
import importlib
import importlib.abc
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
import time
import uuid

LEGACY_HEAD = "b8b6886b2a21d198b2be6f9a9fd6d8a35f8b1a55"
LEGACY_TREE = "574cd6a85e0c1b1eb57c5f48c5223fa0b360eeb6"


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def compatible_validate(owner, core, current, plan, row, witness):
    """Only an actual retained false witness authorizes the nullable alias."""
    require(witness["HostConfig"].get("OomKillDisable") is False,
            "recovery lacks explicit false OOM witness")
    require(owner.validate_container(witness, plan, row, initial=True) == row["config_sha256"],
            "original inert witness/hash differs")
    host = current["HostConfig"]
    require("OomKillDisable" in host and
            (host["OomKillDisable"] is None or host["OomKillDisable"] is False),
            "unexpected OOM setting; no recovery alias")
    normalized = copy.deepcopy(current)
    normalized["HostConfig"]["OomKillDisable"] = False
    # This still checks exact ID/image/labels and every original config field.
    owner.validate_container(normalized, plan, row)
    require(core.held_container_sha256(normalized) == row["config_sha256"],
            "non-OOM config drift; recovery refuses")


def quiesce(session, attempt_id, owner, core, ops=None, journal=None):
    path = session.directory / "attempts" / attempt_id / "owner" / "plan.json"
    plan = owner.json_bytes(owner.read_owned_bytes(path))
    expected = dict(plan)
    wanted = expected.pop("plan_sha256")
    require(owner.digest(owner.canonical(expected)) == wanted, "retained plan digest differs")
    require(plan["attempt_id"] == attempt_id and plan["window_id"] == session.capsule["window_id"],
            "foreign owner plan")
    own_journal = journal is None
    journal = owner.Journal(session, plan) if own_journal else journal
    require(journal.plan == plan, "locked owner plan differs")
    try:
        ops = owner.OwnerOps(session, plan, journal, recovering=True) if ops is None else ops
        ops.journal = journal
        ops.check_authority()
        ops.seal_all()
        rows = journal.state["ranks"]
        require(len(rows) == 4 and [r["rank"] for r in rows] == list(range(4)), "four durable ranks required")
        events = [owner.json_bytes(owner.read_owned_bytes(p))
                  for p in sorted(journal.directory.glob("[0-9]*.json"))]
        witnesses = {}
        # Admit every present rank before the first stop; retain original raw data.
        for row in rows:
            rank = row["rank"]
            candidates = [e["container"] for e in events if e["kind"] == "daemon-inspect"
                          and e.get("rank") == rank and e.get("container") is not None
                          and e["container"].get("State", {}).get("Running") is False
                          and core.held_container_sha256(e["container"]) == row["config_sha256"]]
            require(candidates, "missing original pre-start config witness")
            witnesses[rank] = candidates[0]
            item = ops.inspect(rank, row)
            if item is not None:
                compatible_validate(owner, core, item, plan, row, witnesses[rank])
        for row in rows:
            rank = row["rank"]
            item = ops.inspect(rank, row)
            if item is not None:
                compatible_validate(owner, core, item, plan, row, witnesses[rank])
                if item["State"].get("Running") is True:
                    ops.action(rank, "stop", row)
                    item = ops.inspect(rank, row)
                    if item is not None:
                        compatible_validate(owner, core, item, plan, row, witnesses[rank])
                        require(item["State"].get("Running") is False, "owned daemon still running")
            if row["guard_intent"]:
                state = ops.guard_state(rank, row)
                if state.get("absent") is not True:
                    require(state.get("owned") is True, "foreign/unknown guard")
                    if state.get("active") is True:
                        ops.action(rank, "stop_guard", row)
                state = ops.guard_state(rank, row)
                require(state.get("absent") is True or
                        (state.get("owned") is True and state.get("active") is False),
                        "guard not proven inactive")
            if item is not None:
                ops.action(rank, "remove", row)
            require(ops.inspect(rank, row) is None, "exact owned ID still present")
            row["removed"] = True
            journal.save()
        ops.seal_all()
        journal.phase("drained")
        proof = dict(window_id=plan["window_id"], attempt_id=attempt_id, node_count=4,
                     daemon_quiescent=True, gpu_drain_required_by_caller=True,
                     plan_sha256=plan["plan_sha256"], compatibility="explicit-false-to-null-only")
        if not (journal.directory / "drained.json").exists():
            owner.exclusive_json(journal.directory / "drained.json", proof)
        return proof
    finally:
        if own_journal:
            journal.close()


# One fixed journal per old session excludes alternate-journal takeover. These
# helpers are private to this recovery, not a general command/retry framework.
JOURNAL_NAME = 'owner-oom-recovery'
SOURCE_NAMES = {'q03_window.py', 'q03_window_preflight.py', 'q03_window_watchdog.py',
                'q03_original_spark_adapter.py', 'q03_remote_operation.py',
                'q03_build_adapter.py', 'q03_window_build.py', 'q03_buildkit_plan.py',
                'q03_git_toolchain.py', 'q03_held_window.py', 'q03_owner_model.py',
                'q03_image_preparation.py', 'memguard-start.sh', 'memguard.sh', '../bin/spark',
                'mesh_fabric.py', 'kernel_backend.py', 'kernel_readiness.py', 'topology.py',
                'transport_profiles.py', 'model_layout.py', 'recipe.py'}


def read(path):
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path, 'path alias')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_size <= 4 * 1024 * 1024,
                'invalid bounded regular file')
        raw = stream.read(4 * 1024 * 1024 + 1)
        key = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        require(key(before) == key(os.fstat(stream.fileno())) == key(path.lstat())
                and len(raw) <= 4 * 1024 * 1024, 'file changed while reading')
        return raw


def json_value(raw):
    def unique(pairs):
        result = {}
        for key, item in pairs:
            require(key not in result, 'duplicate JSON field')
            result[key] = item
        return result
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: require(False, 'nonfinite JSON'))


def value(path):
    return json_value(read(path))


def mutable_value(path, *, hold=False):
    """Read one complete mutable snapshot, not an immutable path-version pin.

    State/heartbeat writers publish via atomic replacement: an opened old inode
    is a legitimate complete snapshot. Hold writers instead use in-place pwrite
    under flock, so hold snapshots share that lock and revalidate the inode.
    Unknown reads still fail closed after a bounded number of unstable reads.
    """
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path, 'mutable path alias')
    for _ in range(4):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            if hold:
                fcntl.flock(stream, fcntl.LOCK_SH)
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_size <= 4 * 1024 * 1024,
                    'invalid bounded mutable file')
            if hold:
                try:
                    current = path.lstat()
                except FileNotFoundError:
                    continue
                if (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino):
                    continue
            raw = stream.read(4 * 1024 * 1024 + 1)
            after = os.fstat(stream.fileno())
            # Atomic replacement unlinks the opened old inode, which can change
            # only ctime/link count without changing its complete data snapshot.
            key = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns,
                              st.st_ctime_ns if hold else None)
            if key(before) != key(after):
                continue
            if hold:
                try:
                    current = path.lstat()
                except FileNotFoundError:
                    continue
                if key(after) != key(current):
                    continue
            require(len(raw) <= 4 * 1024 * 1024, 'mutable file exceeds bound')
            return json_value(raw)
    raise RuntimeError('mutable snapshot did not stabilize; UNKNOWN')


def digest(path):
    return hashlib.sha256(read(path)).hexdigest()


def exclusive(path, data):
    raw = json.dumps(data, sort_keys=True, allow_nan=False).encode()
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(raw); out.flush(); os.fsync(out.fileno())
        os.link(temporary, path, follow_symlinks=False)
        fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink()


@contextlib.contextmanager
def locked(path, *, create=False):
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | (os.O_CREAT if create else 0), 0o600)
    with os.fdopen(fd, 'r+') as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), 'lock must be regular')
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        opened, current = os.fstat(stream.fileno()), Path(path).lstat()
        require((opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino), 'lock replaced')
        yield stream


def process_state(record):
    """Only an exact absent PID or matching zombie proves death; errors do not."""
    if not (isinstance(record, dict) and set(record) == {'pid', 'start_ticks'}
            and type(record['pid']) is int and record['pid'] > 0
            and isinstance(record['start_ticks'], str)
            and re.fullmatch(r'[0-9]+', record['start_ticks'])):
        return 'unknown'
    try:
        raw = Path(f"/proc/{record['pid']}/stat").read_text()
        prefix, tail = raw.rsplit(')', 1)
        fields = tail.split()
        if (int(prefix.split('(', 1)[0]) != record['pid'] or len(fields) < 20
                or fields[0] not in ('R', 'S', 'D', 'Z', 'T', 't', 'X', 'x', 'K', 'W', 'P', 'I')
                or not fields[19].isdigit()):
            return 'unknown'
    except (FileNotFoundError, ProcessLookupError):
        return 'dead'
    except (OSError, ValueError, IndexError, UnicodeError):
        return 'unknown'
    if fields[19] != record['start_ticks']:
        return 'reused'
    return 'dead' if fields[0] == 'Z' else 'alive'


def identity(pid=None):
    pid = os.getpid() if pid is None else pid
    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    record = {'pid': pid, 'start_ticks': fields[19]}
    require(process_state(record) == 'alive', 'current process identity unavailable')
    return record


def require_terminal(directory, capsule, capsule_hash):
    ready = value(directory / 'watchdog.ready.json')  # Required, never optional.
    require(ready.get('window_id') == capsule['window_id']
            and ready.get('capsule_sha256') == capsule_hash, 'old watchdog identity differs')
    for label, record in [('core', capsule['core_process']), ('watchdog', ready.get('process'))]:
        require(process_state(record) == 'dead', 'old ' + label + ' is live/reused/UNKNOWN')
    return ready


class PinnedLegacySources(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Only the fixed legacy Python import inventory; no bytecode-cache reads.

    Compile the exact bytes already read and hash-verified, retaining normal
    module names and canonical filenames for the legacy sibling imports.
    This does not alter loaders inside the unchanged adapter subprocesses.
    """
    def __init__(self, sources):
        self.sources = sources

    def find_spec(self, fullname, path=None, target=None):
        if fullname not in self.sources:
            return None
        source, _ = self.sources[fullname]
        spec = importlib.util.spec_from_loader(fullname, self, origin=str(source))
        spec.has_location = True
        return spec

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        source, raw = self.sources[module.__name__]
        exec(compile(raw, str(source), 'exec', dont_inherit=True), module.__dict__)


def install_legacy_sources(legacy, pins):
    require(set(pins) == SOURCE_NAMES, 'old exact 22-source inventory differs')
    sources = {}
    for name, expected in pins.items():
        path = (legacy / 'scripts' / name).resolve()
        raw = read(path)
        require(hashlib.sha256(raw).hexdigest() == expected, 'old recovery source differs: ' + name)
        if name.endswith('.py'):
            sources[Path(name).stem] = (path, raw)
    require(not any(name in sys.modules for name in sources),
            'legacy module already loaded outside the pinned source loader')
    finder = PinnedLegacySources(sources)
    sys.meta_path.insert(0, finder)
    return finder


def load_legacy(directory, capsule_hash):
    require(re.fullmatch(r'[0-9a-f]{64}', capsule_hash) is not None, 'invalid capsule pin')
    require(digest(directory / 'capsule.json') == capsule_hash
            and read(directory / 'capsule.sha256').decode().strip() == capsule_hash,
            'capsule identity differs')
    capsule = value(directory / 'capsule.json')
    require(capsule['session_dir'] == str(directory), 'old session identity differs')
    manifest = capsule['manifest']
    require(capsule['hold_path'] == manifest['preflight']['hold_path'], 'capsule hold identity differs')
    require(manifest['preflight'].get('original_service_disposition') == 'keep-stopped',
            'recovery only permits keep-stopped')
    root = Path(manifest['independent_root'])
    require(root.is_absolute() and root.resolve() == root and root in directory.parents,
            'session escapes independent root')
    legacy = Path(manifest['preflight']['independent_checkout'])
    require(legacy.is_absolute() and legacy.resolve() == legacy, 'legacy checkout alias')
    def git(*arguments):
        return subprocess.check_output(['git', '--no-replace-objects', '-c', 'core.fsmonitor=false',
            '-c', 'core.hooksPath=/dev/null', '-C', str(legacy), *arguments], text=True).strip()
    require(git('rev-parse', 'HEAD') == LEGACY_HEAD and git('rev-parse', 'HEAD^{tree}') == LEGACY_TREE
            and not git('status', '--porcelain'), 'legacy b8 source not exact and clean')
    pins = capsule['recovery_source_sha256']
    install_legacy_sources(legacy, pins)
    sys.dont_write_bytecode = True
    core = importlib.import_module('q03_window')
    owner = importlib.import_module('q03_owner_model')
    require(Path(core.__file__).resolve() == legacy / 'scripts/q03_window.py'
            and Path(owner.__file__).resolve() == legacy / 'scripts/q03_owner_model.py',
            'imported legacy module is not pinned source')
    return capsule, core, owner


def owner_inventory(session, owner):
    paths = sorted((session.directory / 'attempts').glob('*/owner/plan.json'))
    require(len(paths) == 1, 'exactly one retained owner plan required')
    path = paths[0]
    require(re.fullmatch(r'[0-9a-f]{32}', path.parents[1].name) is not None, 'invalid owner attempt')
    plan = owner.json_bytes(owner.read_owned_bytes(path))
    expected = dict(plan)
    wanted = expected.pop('plan_sha256')
    require(owner.digest(owner.canonical(expected)) == wanted
            and plan['attempt_id'] == path.parents[1].name
            and plan['window_id'] == session.capsule['window_id'], 'owner plan identity differs')
    active = session.state.get('active_attempt')
    require(active is None or active.get('attempt_id') == plan['attempt_id'], 'active owner differs')
    return plan, digest(path)


def fixed_command(intent, action):
    require(action in ('--watch', '--watchdog-retry'), 'unknown internal recovery action')
    return [sys.executable, '-I', '-B', str(Path(__file__).resolve()),
            '--session-dir', intent['old_session'], '--capsule-sha256', intent['capsule_sha256'], action]


def verify_intent(directory):
    journal = directory / JOURNAL_NAME
    intent = value(journal / 'intent.json')
    require(intent['old_session'] == str(directory)
            and intent['entry_path'] == str(Path(__file__).resolve())
            and intent['entry_sha256'] == digest(Path(__file__).resolve()), 'recovery entry/intent differs')
    require(digest(directory / 'capsule.json') == intent['capsule_sha256']
            and read(directory / 'capsule.sha256').decode().strip() == intent['capsule_sha256'],
            'old capsule changed')
    capsule = value(directory / 'capsule.json')
    require(capsule['window_id'] == intent['window_id'], 'recovery window differs')
    require(digest(directory / 'watchdog.ready.json') == intent['old_watchdog_sha256'],
            'old watchdog evidence changed')
    require(digest(directory / 'attempts' / intent['attempt_id'] / 'owner/plan.json')
            == intent['owner_plan_sha256'], 'retained owner plan changed')
    started = intent['restoring_started_epoch']
    require(type(started) in (int, float) and math.isfinite(started)
            and intent['deadline_epoch'] == started + capsule['recovery_timeout_s'],
            'recovery budget differs')
    return intent, capsule, digest(journal / 'intent.json')


def closure(state):
    return (state.get('phase') == 'closed-stopped' and state.get('original_kept_stopped') is True
            and state.get('restored') is False)


def owned_or_closed_hold(capsule, read_state):
    # Observe hold first, then state. A completed unlink must not be compared
    # against a stale restoring state fetched before the closure write.
    def observe_hold():
        try:
            hold = mutable_value(Path(capsule['hold_path']), hold=True)
        except FileNotFoundError:
            return None
        require(hold.get('window') == capsule['window_id'] and hold.get('holder') == capsule['holder'],
                'foreign hold')
        return hold
    hold = observe_hold()
    state = read_state()
    if closure(state) and state.get('hold_released') is True and hold is not None:
        # The first hold snapshot may precede unlink while state follows it.
        # Reconcile that observation before treating a dead main as retryable.
        hold = observe_hold()
        state = read_state()
        require(hold is None, 'released closure still has a hold; UNKNOWN')
    require(hold is not None or closure(state), 'missing hold without verified closure')
    return hold, state


def launch_gated_retry(directory, intent, main):
    journal = directory / JOURNAL_NAME
    r, w = os.pipe()
    gate = ('import os,sys; f=int(sys.argv[1]); b=os.read(f,1); os.close(f); '
            'os.execvpe(sys.argv[2],sys.argv[2:],os.environ) if b==b"1" else sys.exit(125)')
    try:
        with (journal / 'retry.log').open('xb') as log:
            child = subprocess.Popen([sys.executable, '-I', '-B', '-c', gate, str(r),
                                      *fixed_command(intent, '--watchdog-retry')],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                pass_fds=(r,), close_fds=True, start_new_session=True)
        record = identity(child.pid)
        exclusive(journal / 'retry.json', {'main': main, 'child': record,
                  'supervisor': identity(), 'intent_sha256': digest(journal / 'intent.json')})
        os.write(w, b'1')
        return record
    finally:
        os.close(r); os.close(w)


class MonitorOps:
    now = staticmethod(time.time)
    sleep = staticmethod(time.sleep)
    process_state = staticmethod(process_state)
    launch_retry = staticmethod(launch_gated_retry)

    def pulse(self, directory, pin):
        journal = directory / JOURNAL_NAME
        temporary = journal / ('supervisor-heartbeat.' + uuid.uuid4().hex + '.json')
        exclusive(temporary, {'process': identity(), 'intent_sha256': pin, 'epoch': self.now()})
        os.replace(temporary, journal / 'supervisor-heartbeat.json')

    def snapshot(self, directory):
        intent, capsule, pin = verify_intent(directory)
        hold, state = owned_or_closed_hold(capsule, lambda: mutable_value(directory / 'state.json'))
        heartbeat = mutable_value(directory / JOURNAL_NAME / 'heartbeat.json')
        return intent, capsule, pin, state, hold, heartbeat


def monitor(directory, ops=None):
    ops = ops or MonitorOps()
    intent, _, pin, _, _, _ = ops.snapshot(directory)
    target, retried, retry_ready_until = intent['main'], False, None
    while True:
        current, capsule, current_pin, state, hold, heartbeat = ops.snapshot(directory)
        require(current_pin == pin, 'recovery intent changed')
        ops.pulse(directory, pin)
        if closure(state) and state.get('hold_released') is True and hold is None:
            return {'status': 'closed-stopped-observed', 'retry_started': retried}
        require(ops.now() < intent['deadline_epoch'], 'recovery deadline reached; UNKNOWN, retain hold')
        health = ops.process_state(target)
        require(health in ('alive', 'dead'), 'process identity uncertain; UNKNOWN, retain hold')
        if health == 'dead':
            require(not retried, 'single retry ended without closure; UNKNOWN, retain hold')
            target = ops.launch_retry(directory, intent, intent['main'])
            retried = True
            retry_ready_until = min(intent['deadline_epoch'], ops.now() + 10)
        else:
            if (retried and heartbeat.get('process') != target
                    and ops.now() < retry_ready_until):
                ops.sleep(.1)
                continue
            require(heartbeat.get('process') == target and heartbeat.get('intent_sha256') == pin,
                    'recovery heartbeat identity differs')
            age = ops.now() - heartbeat['epoch']
            require(math.isfinite(age) and -5 <= age <= capsule['heartbeat_timeout_s'],
                    'recovery heartbeat stale; UNKNOWN, retain hold')
        ops.sleep(1)


def watch(directory):
    journal = directory / JOURNAL_NAME
    with locked(journal / 'supervisor.lock', create=True):
        intent, capsule, pin = verify_intent(directory)
        require(process_state(intent['main']) == 'alive', 'main not alive before readiness')
        owned_or_closed_hold(capsule, lambda: mutable_value(directory / 'state.json'))
        require(time.time() < intent['deadline_epoch'], 'deadline elapsed before readiness')
        MonitorOps().pulse(directory, pin)
        exclusive(journal / 'ready.json', {'supervisor': identity(), 'main': intent['main'],
                  'intent_sha256': pin})
        try:
            result = monitor(directory)
        except BaseException as error:
            exclusive(journal / 'result.json', {'status': 'UNKNOWN', 'error': str(error),
                                               'hold_state': 'unverified'})
            raise
        exclusive(journal / 'result.json', result)


def start_watchdog(directory, intent):
    journal = directory / JOURNAL_NAME
    with (journal / 'supervisor.log').open('xb') as log:
        child = subprocess.Popen(fixed_command(intent, '--watch'), stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, close_fds=True, start_new_session=True)
    expected = identity(child.pid)
    end = time.monotonic() + 10
    while time.monotonic() < end:
        try:
            ready = value(journal / 'ready.json')
        except FileNotFoundError:
            ready = None
        if ready is not None:
            require(ready == {'supervisor': expected, 'main': intent['main'],
                    'intent_sha256': digest(journal / 'intent.json')}
                    and process_state(expected) == 'alive', 'supervisor readiness mismatch')
            return ready
        require(child.poll() is None, 'supervisor exited before readiness')
        time.sleep(.05)
    raise RuntimeError('supervisor readiness timed out; no recovery mutation admitted')


def validate_retry(directory, intent, pin):
    journal = directory / JOURNAL_NAME
    retry, ready = value(journal / 'retry.json'), value(journal / 'ready.json')
    require(retry['intent_sha256'] == pin and retry['main'] == intent['main']
            and retry['child'] == identity() and retry['supervisor'] == ready['supervisor']
            and ready['intent_sha256'] == pin and ready['main'] == intent['main']
            and process_state(retry['supervisor']) == 'alive'
            and process_state(intent['main']) == 'dead', 'retry process/intent ownership differs')
    return ready


def open_owner_journal(session, plan, owner, locks):
    directory = session.directory / 'attempts' / plan['attempt_id'] / 'owner'
    lock = locks.enter_context(locked(directory / 'owner.lock'))
    journal = object.__new__(owner.Journal)
    journal.session, journal.plan, journal.directory, journal.lock = session, plan, directory, lock
    require(owner.json_bytes(owner.read_owned_bytes(directory / 'plan.json')) == plan, 'locked plan changed')
    journal.state = owner.json_bytes(owner.read_owned_bytes(directory / 'state.json'))
    # event() publishes an immutable event before advancing mutable sequence.
    # Recover only a complete contiguous tail; never alter historical events.
    events = sorted(directory.glob('[0-9]*.json'))
    for sequence, path in enumerate(events, 1):
        event = owner.json_bytes(owner.read_owned_bytes(path))
        require(path.name == f'{sequence:06d}.json' and event.get('sequence') == sequence
                and event.get('window_id') == plan['window_id']
                and event.get('attempt_id') == plan['attempt_id'], 'owner event tail is ambiguous')
    require(type(journal.state['sequence']) is int and 0 <= journal.state['sequence'] <= len(events),
            'owner event sequence differs')
    journal.state['sequence'] = len(events)
    return journal


def run_recovery(directory, capsule_hash, *, retry=False):
    capsule, core, owner = load_legacy(directory, capsule_hash)
    plain = core.RuntimeOps()
    core.Session(directory, plain)  # Exact legacy inventory before locking.
    with contextlib.ExitStack() as locks:
        for name in ('session.lock', 'watchdog.lock'):
            locks.enter_context(locked(directory / name))
        locks.enter_context(plain.acquire_gpu_lock())
        require_terminal(directory, capsule, capsule_hash)
        session = core.Session(directory, plain)
        require(session.state.get('mutation_started') is True, 'old session never mutated')
        plan, plan_hash = owner_inventory(session, owner)
        journal = directory / JOURNAL_NAME
        if retry:
            intent, _, pin = verify_intent(directory)
            ready = validate_retry(directory, intent, pin)
            require(session.state.get('phase') in ('restoring', 'recovery-failed', 'closed-stopped'),
                    'interrupted recovery phase differs')
        else:
            require(session.state.get('phase') == 'recovery-failed' and session.owns_hold(),
                    'only failed owned recovery admission; closed sessions cannot be reused')
            started = session.state.get('restoring_started_epoch')
            require(type(started) in (int, float) and math.isfinite(started)
                    and started <= time.time(), 'old recovery start missing or in future')
            intent = {'old_session': str(directory), 'capsule_sha256': capsule_hash,
                      'window_id': capsule['window_id'], 'attempt_id': plan['attempt_id'],
                      'owner_plan_sha256': plan_hash, 'old_watchdog_sha256': digest(directory / 'watchdog.ready.json'),
                      'entry_path': str(Path(__file__).resolve()), 'entry_sha256': digest(Path(__file__).resolve()),
                      'restoring_started_epoch': started, 'deadline_epoch': started + capsule['recovery_timeout_s'],
                      'main': identity()}
            require(time.time() < intent['deadline_epoch'], 'old recovery deadline elapsed')
            journal.mkdir(mode=0o700, exist_ok=False)
            exclusive(journal / 'intent.json', intent)
            pin = digest(journal / 'intent.json')
        require(intent['attempt_id'] == plan['attempt_id'] and intent['owner_plan_sha256'] == plan_hash
                and session.state.get('restoring_started_epoch') == intent['restoring_started_epoch'],
                'interrupted recovery authority differs')
        owner_journal = open_owner_journal(session, plan, owner, locks)
        current = identity()
        stop_heartbeat, heartbeat_errors = threading.Event(), []
        def beat():
            if session.owns_hold():
                session.beat()
            else:
                require(closure(session.state), 'hold lost before closure')
            core.atomic(journal / 'heartbeat.json', {'process': current, 'intent_sha256': pin, 'epoch': time.time()})
        beat()
        if not retry:
            ready = start_watchdog(directory, intent)
        def check():
            require(not heartbeat_errors, 'owned heartbeat failed')
            require(time.time() < intent['deadline_epoch'], 'recovery deadline reached')
            verify_intent(directory)
            require(process_state(ready['supervisor']) == 'alive' and not (journal / 'result.json').exists(),
                    'independent supervisor lost; UNKNOWN, retain hold')
            pulse = mutable_value(journal / 'supervisor-heartbeat.json')
            age = time.time() - pulse['epoch']
            require(pulse.get('process') == ready['supervisor'] and pulse.get('intent_sha256') == pin
                    and math.isfinite(age) and -5 <= age <= capsule['heartbeat_timeout_s'],
                    'independent supervisor heartbeat stale/foreign; UNKNOWN, retain hold')
        def heartbeat():
            while not stop_heartbeat.wait(2):
                try:
                    check(); beat()
                except BaseException as error:
                    heartbeat_errors.append(error)
                    return
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        class RecoveryOps(core.RuntimeOps):
            def run(self, *args, **kwargs):
                check()
                return super().run(*args, **kwargs)
            def execute(self, *args, **kwargs):
                check()
                return super().execute(*args, **kwargs)
            def check(self, *args, **kwargs):
                check()
                return super().check(*args, **kwargs)
            def terminate_owned_process(self, record):
                if record is not None:
                    require(process_state(record) in ('alive', 'dead'), 'local command identity UNKNOWN/reused')
                return super().terminate_owned_process(record)
            def cleanup_owner(self, active_session, attempt_id=None):
                check()
                require(attempt_id in (None, plan['attempt_id']), 'unexpected cleanup attempt')
                return quiesce(active_session, plan['attempt_id'], owner, core, journal=owner_journal)
        session.ops = RecoveryOps()
        original_release = session.release
        def guarded_release():
            check()
            original_release()
        session.release = guarded_release
        try:
            check()
            if closure(session.state):
                # Only the same supervisor's gated retry can finish the tiny
                # proof/release/state-write tail; never reopen a closed session.
                require(retry, 'closed recovery cannot be reused')
                if session.owns_hold():
                    session.check('restore_drained')
                    check()
                    session.release()
                else:
                    require(session.hold_status() == 'absent', 'foreign final hold')
                session.update(hold_released=True)
            else:
                require(session.owns_hold(), 'owned hold lost')
                core.restore(session)
            return session.state
        except BaseException as error:
            if not closure(session.state):
                session.update(phase='recovery-failed', failure=str(error), outcome='failed')
            raise
        finally:
            stop_heartbeat.set(); thread.join(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session-dir', required=True)
    parser.add_argument('--capsule-sha256', required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--watch', action='store_true', help=argparse.SUPPRESS)
    mode.add_argument('--watchdog-retry', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    directory = Path(args.session_dir)
    require(directory.is_absolute() and directory.resolve() == directory, 'session alias')
    if args.watch:
        intent, _, _ = verify_intent(directory)
        require(intent['capsule_sha256'] == args.capsule_sha256, 'watch capsule pin differs')
        watch(directory)
    else:
        print(json.dumps(run_recovery(directory, args.capsule_sha256, retry=args.watchdog_retry)), flush=True)


if __name__ == '__main__':
    main()
