"""Explicit recovery for an old64 normal-completed, already-quiesced build.

No UNKNOWN build admission. Original capsule, sources, state and operation
receipts remain unchanged; new recovery state and derived proofs are separate.
Default CLI is file-verification/plan only. Explicit --apply performs the
registered coordinated restoration using the narrowly reviewed compatibility
adapter and original protected/readiness checks.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat

OLD_DEPLOYMENT = "64a00fc78995b0659864b767e40132643314acfd"
SOURCE_NAMES = (
    "q03_window.py", "q03_window_preflight.py", "q03_window_watchdog.py",
    "q03_original_spark_adapter.py", "q03_remote_operation.py",
    "q03_build_adapter.py", "q03_window_build.py", "q03_buildkit_plan.py",
)
MAX_BYTES = 4 * 1024 * 1024


class RestoreSupportError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise RestoreSupportError(message)


def _digest(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value),
            "Expected SHA256 must be explicit")
    return value


def _path(value):
    require(isinstance(value, (str, Path)), "Expected absolute path")
    path = Path(value)
    require(path.is_absolute() and ".." not in path.parts
            and not any(c in str(path) for c in ("\n", "\x00"))
            and path.resolve() == path, "Path must be canonical without aliases")
    return path


def _read(path):
    """Bounded no-follow read; reject concurrent replacement or modification."""
    path = _path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_size <= MAX_BYTES,
                "Evidence must be a bounded regular file")
        with os.fdopen(os.dup(fd), "rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        after = os.fstat(fd)
        current = path.stat(follow_symlinks=False)
        key = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        require(key(before) == key(after) == key(current) and len(raw) <= MAX_BYTES,
                "Evidence changed while reading")
        return raw
    finally:
        os.close(fd)


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "Duplicate evidence field")
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique)
    require(isinstance(value, dict), "Evidence must be an object")
    return value


@dataclass(frozen=True)
class RestorePlan:
    """Verified immutable-file references, explicitly NOT a live restore proof."""
    session_dir: str
    capsule_sha256: str
    source_directory: str
    source_hashes: tuple[tuple[str, str], ...]
    window_id: str
    holder: str
    hold_path: str
    policy_path: str
    policy_sha256: str
    spark_sha256: str
    original_repository: str
    restore_config: str
    restore_config_sha256: str


def verify_restore_plan(session_dir, expected_capsule_sha256):
    """Read pinned old authority/source bytes only. Never establish quiescence."""
    expected = _digest(expected_capsule_sha256)
    directory = _path(session_dir)
    raw = _read(directory / "capsule.json")
    require(hashlib.sha256(raw).hexdigest() == expected, "External capsule pin differs")
    require(_read(directory / "capsule.sha256").decode().strip() == expected,
            "Capsule sidecar differs")
    capsule = _json(raw)
    require(_path(capsule["session_dir"]) == directory, "Old session identity differs")
    manifest = capsule["manifest"]
    root = _path(manifest["independent_root"])
    require(directory != root and directory.is_relative_to(root), "Session escapes independent root")
    pre = manifest["preflight"]
    require(pre["publication"]["sha"] == OLD_DEPLOYMENT, "Only exact old64 sessions supported")
    require(pre["publication"]["url"] in (
        "https://github.com/imDaniel/spark-ds41f", "https://github.com/imDaniel/spark-ds41f.git"),
        "Old session does not name the authorized fork")
    checkout = _path(pre["independent_checkout"])
    require(checkout != root and checkout.is_relative_to(root), "Old checkout escapes root")
    core = checkout / "scripts/q03_window.py"
    command = capsule["recovery_command"]
    require(isinstance(command, list) and len(command) == 5
            and command[1:] == [str(core), "recover", "--session-dir", str(directory)],
            "Old recovery command differs from pinned layout")
    sources = capsule["recovery_source_sha256"]
    require(isinstance(sources, dict) and set(sources) == set(SOURCE_NAMES),
            "Old eight-source inventory differs")
    for name in SOURCE_NAMES:
        require(hashlib.sha256(_read(checkout / "scripts" / name)).hexdigest() == _digest(sources[name]),
                "Old pinned source changed: " + name)
    require(capsule["recovery_script_sha256"] == sources["q03_window.py"],
            "Old recovery script pin differs")
    require(capsule["hold_path"] == pre["hold_path"]
            and capsule["policy_path"] == pre["policy_path"]
            and capsule["policy_sha256"] == pre["policy_sha256"], "Capsule authority differs")
    window, holder = capsule["window_id"], capsule["holder"]
    require(isinstance(window, str) and re.fullmatch(r"[0-9a-f]{32}", window)
            and pre["window_id"] == window and pre["holder"] == holder
            and holder == "q03-isolated:" + window, "Old hold identity differs")
    policy_path = _path(pre["policy_path"])
    policy_raw = _read(policy_path)
    require(hashlib.sha256(policy_raw).hexdigest() == _digest(pre["policy_sha256"]),
            "Registered policy bytes differ")
    policy = _json(policy_raw)
    expected_policy = pre["registered_policy"]
    required = {"repository", "cluster_config", "config_sha256", "revision", "image_id", "container"}
    require(required <= set(expected_policy)
            and all(policy.get(key) == value for key, value in expected_policy.items()),
            "Registered policy identity differs")
    repository = _path(policy["repository"])
    config = _path(repository / policy["cluster_config"])
    require(config != repository and config.is_relative_to(repository), "Restore config escapes original repository")
    require(hashlib.sha256(_read(config)).hexdigest() == _digest(policy["config_sha256"]),
            "Registered restore configuration differs")
    require(hashlib.sha256(_read(repository / "bin/spark")).hexdigest() == _digest(pre["spark_sha256"]),
            "Original spark source differs")
    return RestorePlan(str(directory), expected, str(checkout / "scripts"),
                       tuple(sorted(sources.items())), window, holder, str(_path(pre["hold_path"])),
                       str(policy_path), pre["policy_sha256"], pre["spark_sha256"],
                       str(repository), str(config), policy["config_sha256"])


def adapter_command_plan(plan, action, epoch):
    """Exact argv reference only; no launch, epoch admission or ownership claim."""
    require(type(plan) is RestorePlan, "Expected verified-file plan")
    require(action in ("stop", "start", "quiesce"), "Unknown registered action")
    require(isinstance(epoch, str) and re.fullmatch(r"[0-9a-f]{32}", epoch), "Explicit epoch required")
    return ["python3", "-I", "-B", plan.source_directory + "/q03_original_spark_adapter.py",
            "--policy", plan.policy_path, "--policy-sha256", plan.policy_sha256,
            "--spark-sha256", plan.spark_sha256,
            "--evidence-dir", plan.session_dir + "/restore-evidence",
            "--hold", plan.hold_path, "--window-id", plan.window_id,
            "--epoch", epoch, "--action", action, "--apply"]


def blocked_report(plan):
    require(type(plan) is RestorePlan, "Expected verified-file plan")
    return {"status": "verified-files-only", "safe_to_restore": False,
            "old_capsule_sha256": plan.capsule_sha256, "window_id": plan.window_id,
            "execution_enabled": False,
            "missing_admission": ["reviewed live daemon-proof engine",
                "terminal old core and watchdog", "old session/GPU/new journal lock ownership",
                "owned hold and live heartbeat", "durable old/new epoch sealing",
                "staged protected/GLM/readiness verification"]}


def apply_reconciled_restore(*args, **kwargs):
    """Intentionally unavailable until proof/executor review is complete."""
    raise RestoreSupportError("Restore disabled: no reviewed live reconciliation proof executor exists")


def load_old_modules(plan):
    """Execute only same-read, hash-verified old sources, never a path overlay."""
    import sys
    import types
    sys.dont_write_bytecode = True
    modules = {}
    for name, digest in plan.source_hashes:
        path = Path(plan.source_directory) / name
        raw = _read(path)
        require(hashlib.sha256(raw).hexdigest() == digest, 'Old module changed')
        module = types.ModuleType(path.stem)
        module.__file__ = str(path)
        modules[path.stem] = module
        sys.modules[path.stem] = module
    for name, _ in plan.source_hashes:
        module = modules[Path(name).stem]
        raw = _read(module.__file__)
        require(hashlib.sha256(raw).hexdigest() == dict(plan.source_hashes)[name], 'Old module changed')
        exec(compile(raw, module.__file__, 'exec'), module.__dict__)
    return modules


def recorded_process_alive(identity):
    require(type(identity.get('pid')) is int and identity['pid'] > 0
            and isinstance(identity.get('start_ticks'), str) and identity['start_ticks'].isdigit(),
            'Recorded process identity is malformed')
    try:
        raw = Path(f"/proc/{identity['pid']}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError):
        return False
    # Permission/IO failures and malformed records are unknown, never dead.
    fields = raw.rsplit(')', 1)[1].split()
    require(len(fields) > 19 and fields[19].isdigit(), 'Process status is malformed')
    return fields[0] != 'Z' and fields[19] == identity['start_ticks']


def require_terminal(modules, capsule, directory):
    require(not recorded_process_alive(capsule['core_process']), 'Old core is still alive')
    ready = _json(_read(Path(directory) / 'watchdog.ready.json'))
    require(ready.get('window_id') == capsule['window_id']
            and ready.get('capsule_sha256') == hashlib.sha256(_read(Path(directory) / 'capsule.json')).hexdigest(),
            'Old watchdog identity differs')
    require(not recorded_process_alive(ready['process']), 'Old watchdog is still alive')


def verify_completed_build(plan, modules, ops):
    """No cleanup override: admit only already completed AND already quiesced."""
    adapter = modules['q03_build_adapter'].Adapter(plan.session_dir)
    journal = adapter.journal
    require(journal.get('official_build_completed') is True
            and journal.get('quiesced') is True, 'Only normal completed, quiesced builds are supported')
    seal = _json(_read(adapter.fence))
    require(seal == {'sealed': True, 'window_id': plan.window_id}, 'Build launch fence differs')
    require(not modules['q03_build_adapter'].group_members(journal['build_group']['pid']),
            'Old build group is not empty')
    require(journal.get('builder_id') == adapter.builder_id
            and re.fullmatch(r'[0-9a-f]{64}', journal.get('smoke_id', '')),
            'Completed build container identities are missing')
    docker = ['docker', '--host', 'unix:///var/run/docker.sock']
    names = ops.run(docker + ['ps', '-a', '--format', '{{.Names}}']).stdout.splitlines()
    ids = ops.run(docker + ['ps', '-a', '--no-trunc', '--format', '{{.ID}}']).stdout.splitlines()
    require(adapter.plan['container'] not in names and adapter.name not in names
            and adapter.builder_id not in ids and journal['smoke_id'] not in ids,
            'Already quiesced build containers are not absent')
    volumes = json.loads(ops.run(docker + ['volume', 'inspect', adapter.plan['volume']]).stdout)
    require(len(volumes) == 1 and volumes[0].get('Name') == adapter.plan['volume']
            and volumes[0].get('Driver') == 'local' and volumes[0].get('Options') in (None, {}),
            'Preserved builder volume identity differs')
    return {'official_build_completed': True, 'already_quiesced': True,
            'build_journal_sha256': hashlib.sha256(_read(adapter.journal_path)).hexdigest(),
            'build_fence_sha256': hashlib.sha256(_read(adapter.fence)).hexdigest(),
            'builder_id': adapter.builder_id, 'smoke_id': journal['smoke_id'],
            'containers_absent': True, 'volume': adapter.plan['volume']}


def recover_normal_complete(old_dir, capsule_sha256, journal_dir, compat_path,
                            compat_sha256, *, apply=False):
    """Explicit foreground recovery. Never alters old session state or markers."""
    import contextlib
    import fcntl
    import sys
    import time
    import uuid
    plan = verify_restore_plan(old_dir, capsule_sha256)
    compat = _path(compat_path)
    require(compat == Path(__file__).resolve().with_name('q03_recovery_compat.py'),
            'Compatibility adapter must be the released sibling')
    require(hashlib.sha256(_read(compat)).hexdigest() == _digest(compat_sha256), 'Compatibility source differs')
    new_sources = {name: hashlib.sha256(_read(compat.with_name(name))).hexdigest()
                   for name in ('q03_reconcile_restore.py', 'q03_recovery_compat.py', 'q03_remote_operation.py')}
    destination = _path(journal_dir)
    capsule = _json(_read(Path(old_dir) / 'capsule.json'))
    root = _path(capsule['manifest']['independent_root'])
    require(destination != root and destination.is_relative_to(root)
            and not destination.is_relative_to(Path(old_dir))
            and not Path(old_dir).is_relative_to(destination), 'New journal overlaps old session')
    for protected in capsule['manifest']['preflight']['protected_roots']:
        protected = _path(protected)
        require(not destination.is_relative_to(protected) and not protected.is_relative_to(destination),
                'New journal overlaps protected paths')
    if not apply:
        return {'status': 'plan-only', 'execution_enabled': False,
                'old_session': plan.session_dir, 'journal_dir': str(destination),
                'scope': 'normal-completed-already-quiesced-build-only'}
    # No device or service operations occur before explicit apply.
    modules = load_old_modules(plan)
    old = modules['q03_window']
    ops = old.RuntimeOps()
    require_terminal(modules, capsule, old_dir)
    require(destination.parent.is_dir(), 'New journal parent must already exist')
    for parent in (destination.parent, *destination.parent.parents):
        info = parent.stat()
        require(info.st_uid in (0, os.getuid()) and not info.st_mode & 0o022,
                'New journal ancestry is not owner controlled')
    with contextlib.ExitStack() as locks:
        for name in ('session.lock', 'watchdog.lock'):
            fd = os.open(Path(old_dir) / name, os.O_RDWR | os.O_NOFOLLOW)
            require(stat.S_ISREG(os.fstat(fd).st_mode), 'Old lock must be regular')
            stream = locks.enter_context(os.fdopen(fd, 'r+'))
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locks.enter_context(ops.acquire_gpu_lock())
        require_terminal(modules, capsule, old_dir)
        original = old.Session(old_dir, ops)
        require(original.owns_hold(), 'Old shared hold is not owned')
        require(original.state.get('mutation_started') is True, 'Old session never mutated')
        # Do not terminate old core/watchdog or take over a healthy operation.
        ops.terminate_owned_process(original.state.get('active_process'))
        proof = verify_completed_build(plan, modules, ops)
        existed = destination.exists()
        if existed:
            require((destination / 'intent.json').is_file(), 'Existing journal lacks recovery intent')
        destination.mkdir(mode=0o700, exist_ok=True)
        info = destination.stat()
        require(info.st_uid == os.getuid() and not info.st_mode & 0o077,
                'Recovery journal must be private and owner controlled')
        require(destination.is_dir() and not destination.is_symlink(), 'Unsafe new journal')
        fd = os.open(destination / 'reconcile.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        require(stat.S_ISREG(os.fstat(fd).st_mode), 'Recovery lock must be regular')
        stream = locks.enter_context(os.fdopen(fd, 'r+'))
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        intent = {'old_session': plan.session_dir, 'capsule_sha256': plan.capsule_sha256,
                  'window_id': plan.window_id, 'compat_path': str(compat),
                  'compat_sha256': compat_sha256, 'new_source_sha256': new_sources, 'entry_sha256': hashlib.sha256(_read(Path(__file__).resolve())).hexdigest()}
        intent_path = destination / 'intent.json'
        if intent_path.exists():
            require(_json(_read(intent_path)) == intent, 'Reconciliation intent differs')
        else:
            with intent_path.open('x') as file:
                json.dump(intent, file, sort_keys=True); file.flush(); os.fsync(file.fileno())
            directory_fd = os.open(destination, os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)

        class JournalSession(old.Session):
            def __init__(self):
                self.directory, self.ops = destination, ops
                self.capsule, self.manifest, self.hold = original.capsule, original.manifest, original.hold
                path = destination / 'state.json'
                self.state = _json(_read(path)) if path.exists() else dict(original.state)
                self.state.pop('restoring_started_epoch', None)
            def check(self, phase):
                return super().check(phase)
        session = JournalSession()
        ops.terminate_owned_process(session.state.get('active_process'))
        if session.state.get('restored') is True:
            session.check('restored')
            session.release()
            session.update(phase='restored', hold_released=True)
            return session.state
        session.update(phase='restoring', restoring_started_epoch=time.time(),
                       build_proof=proof, restored=False, hold_released=False)

        def command(action, epoch):
            for name, expected in new_sources.items():
                require(hashlib.sha256(_read(compat.with_name(name))).hexdigest() == expected,
                        'New recovery source changed: ' + name)
            return [sys.executable, '-I', '-B', str(compat), '--old-session', plan.session_dir,
                    '--capsule-sha256', plan.capsule_sha256, '--recovery-dir', str(destination),
                    *adapter_command_plan(plan, action, epoch)[4:]]
        def seal_all():
            for transition in session.state.get('transition_epochs', []):
                result = ops.execute(session, command('quiesce', transition['epoch']),
                                     cwd=str(destination), timeout=360)
                ops.require_receipt(result, {'window_id': plan.window_id,
                                            'epoch': transition['epoch'], 'safe_to_overlap': True})
        def transition(action, timeout):
            epoch = uuid.uuid4().hex
            session.update(transition_epochs=session.state.get('transition_epochs', []) +
                           [{'epoch': epoch, 'action': action}])
            ops.execute(session, command(action, epoch), cwd=str(destination), timeout=timeout)
            seal_all()
        import threading
        stop_heartbeat = threading.Event()
        heartbeat_errors = []
        def heartbeat():
            while not stop_heartbeat.wait(2):
                try:
                    session.beat()
                except BaseException as error:
                    heartbeat_errors.append(error)
                    return
        session.beat()
        heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        heartbeat_thread.start()
        def assert_heartbeat():
            require(not heartbeat_errors, 'Owned heartbeat refresh failed')
            require(session.owns_hold(), 'Shared hold ownership changed')
        try:
            assert_heartbeat()
            seal_all()
            ops.cleanup_candidates(session)
            session.check('before_restore')
            assert_heartbeat()
            transition('stop', 210)
            session.check('restore_drained')
            assert_heartbeat()
            transition('start', 3650)
            session.check('restored')
            assert_heartbeat()
            stop_heartbeat.set()
            heartbeat_thread.join(timeout=5)
            session.update(phase='restoration-verified', restored=True)
            session.release()
            session.update(phase='restored', hold_released=True)
        except BaseException as error:
            session.update(phase='recovery-failed', failure=str(error), outcome='failed')
            raise
        finally:
            stop_heartbeat.set()
            heartbeat_thread.join(timeout=5)
        return session.state


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Recover an already completed old64 build without rewriting its evidence')
    parser.add_argument('--old-session', required=True)
    parser.add_argument('--capsule-sha256', required=True)
    parser.add_argument('--journal-dir', required=True)
    parser.add_argument('--compat-path', required=True)
    parser.add_argument('--compat-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(recover_normal_complete(args.old_session, args.capsule_sha256,
          args.journal_dir, args.compat_path, args.compat_sha256, apply=args.apply), sort_keys=True))


if __name__ == '__main__':
    main()
