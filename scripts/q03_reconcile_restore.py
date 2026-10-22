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


def verify_sealed_epoch_proof(proof, transition, window_id, intent_sha256,
                              journal_dir, expected_nodes):
    """A permanent epoch barrier remains valid after service state changes."""
    require(isinstance(proof, dict) and proof.get('window_id') == window_id
            and proof.get('epoch') == transition['epoch']
            and proof.get('action') == transition['action']
            and proof.get('intent_sha256') == intent_sha256,
            'Cached epoch proof identity differs')
    path = _path(proof['receipt_path'])
    require(path.parent == _path(journal_dir) and path.name.startswith('command-')
            and path.suffix == '.log', 'Cached epoch receipt escapes journal')
    raw = _read(path)
    require(hashlib.sha256(raw).hexdigest() == _digest(proof['receipt_sha256']),
            'Cached epoch receipt bytes changed')
    report = _json(raw)
    require(report.get('window_id') == window_id
            and report.get('epoch') == transition['epoch']
            and report.get('safe_to_overlap') is True,
            'Cached epoch receipt is not a successful barrier')
    nodes = report.get('sealed_epochs')
    require(isinstance(nodes, list) and len(nodes) == len(expected_nodes)
            and {item.get('node') for item in nodes if isinstance(item, dict)} == set(expected_nodes)
            and all(isinstance(item, dict) and item.get('window') == window_id
                    and item.get('epoch') == transition['epoch']
                    and item.get('sealed') is True and item.get('safe_to_overlap') is True
                    for item in nodes), 'Cached epoch receipt lacks all-node sealed proof')
    return report


def recover_normal_complete(old_dir, capsule_sha256, journal_dir, compat_path,
                            compat_sha256, *, apply=False, watchdog_retry=False):
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
                   for name in ('q03_reconcile_restore.py', 'q03_recovery_compat.py', 'q03_remote_operation.py',
                                'q03_recovery_supervisor.py')}
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
    # A retry flag is not authority: only the gated, recorded supervisor child
    # may use it, and only while its exact supervisor remains alive.
    if watchdog_retry:
        raw_intent = _read(destination / 'intent.json')
        retry_intent = _json(raw_intent)
        marker = _json(_read(destination / 'supervisor-retry.json'))
        prior_state = _json(_read(destination / 'state.json'))
        require(marker.get('intent_sha256') == hashlib.sha256(raw_intent).hexdigest()
                and retry_intent.get('old_session') == plan.session_dir
                and retry_intent.get('capsule_sha256') == plan.capsule_sha256
                and retry_intent.get('new_source_sha256') == new_sources,
                'Supervisor retry intent differs')
        require(marker['child']['pid'] == os.getpid() and recorded_process_alive(marker['child'])
                and recorded_process_alive(marker['supervisor'])
                and not recorded_process_alive(marker['main'])
                and prior_state.get('recovery_process') == marker['main'],
                'Supervisor retry process ownership differs')
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
        intent_path = destination / 'intent.json'
        prior_intent = _json(_read(intent_path)) if intent_path.exists() else None
        deadline_epoch = (prior_intent['deadline_epoch'] if prior_intent is not None
                          else time.time() + capsule['recovery_timeout_s'])
        require(isinstance(deadline_epoch, (float, int)) and time.time() < deadline_epoch
                <= time.time() + capsule['recovery_timeout_s'], 'Recovery deadline is expired or invalid')
        recovery_command = [sys.executable, '-I', '-B', str(Path(__file__).resolve()),
                            '--old-session', plan.session_dir, '--capsule-sha256', plan.capsule_sha256,
                            '--journal-dir', str(destination), '--compat-path', str(compat),
                            '--compat-sha256', compat_sha256, '--apply']
        intent = {'deadline_epoch': deadline_epoch, 'recovery_command': recovery_command, 'old_session': plan.session_dir, 'capsule_sha256': plan.capsule_sha256,
                  'window_id': plan.window_id, 'compat_path': str(compat),
                  'compat_sha256': compat_sha256, 'new_source_sha256': new_sources, 'entry_sha256': hashlib.sha256(_read(Path(__file__).resolve())).hexdigest()}
        if intent_path.exists():
            require(_json(_read(intent_path)) == intent, 'Reconciliation intent differs')
        else:
            fd = os.open(intent_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as file:
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
        main_identity = modules['q03_window_watchdog'].process_identity(os.getpid())
        require(main_identity is not None, 'Current recovery process identity unavailable')
        session.update(phase='restoring',
                       restoring_started_epoch=deadline_epoch - capsule['recovery_timeout_s'],
                       recovery_process=main_identity, build_proof=proof, restored=False, hold_released=False)
        session.beat()
        if not watchdog_retry:
            import types
            source_path = compat.with_name('q03_recovery_supervisor.py')
            raw = _read(source_path)
            require(hashlib.sha256(raw).hexdigest() == new_sources[source_path.name], 'Supervisor source changed')
            supervisor = types.ModuleType('_q03_verified_recovery_supervisor')
            supervisor.__file__ = str(source_path)
            sys.modules[supervisor.__name__] = supervisor
            exec(compile(raw, str(source_path), 'exec'), supervisor.__dict__)
            supervisor.start_watchdog(destination, main_identity, deadline_epoch)

        def command(action, epoch):
            for name, expected in new_sources.items():
                require(hashlib.sha256(_read(compat.with_name(name))).hexdigest() == expected,
                        'New recovery source changed: ' + name)
            return [sys.executable, '-I', '-B', str(compat), '--old-session', plan.session_dir,
                    '--capsule-sha256', plan.capsule_sha256, '--recovery-dir', str(destination),
                    *adapter_command_plan(plan, action, epoch)[4:]]
        intent_sha256 = hashlib.sha256(_read(intent_path)).hexdigest()
        def seal_one(transition):
            proofs = session.state.get('sealed_epoch_proofs', {})
            epoch = transition['epoch']
            if epoch in proofs:
                verify_sealed_epoch_proof(proofs[epoch], transition, plan.window_id,
                                          intent_sha256, destination, [node['name'] for node in session.manifest['preflight']['nodes']])
                return
            result = ops.execute(session, command('quiesce', epoch),
                                 cwd=str(destination), timeout=360)
            ops.require_receipt(result, {'window_id': plan.window_id,
                                        'epoch': epoch, 'safe_to_overlap': True})
            proof = {'window_id': plan.window_id, 'epoch': epoch,
                     'action': transition['action'], 'intent_sha256': intent_sha256,
                     'receipt_path': str(result['log_path']),
                     'receipt_sha256': hashlib.sha256(_read(Path(result['log_path']))).hexdigest()}
            verify_sealed_epoch_proof(proof, transition, plan.window_id,
                                      intent_sha256, destination, [node['name'] for node in session.manifest['preflight']['nodes']])
            # Durable before any later transition. A sealed registry rejects
            # future launches, so its barrier does not depend on live unit state.
            session.update(sealed_epoch_proofs={**proofs, epoch: proof})
        def seal_all():
            for prior in session.state.get('transition_epochs', []):
                seal_one(prior)
        def transition(action, timeout):
            epoch = uuid.uuid4().hex
            record = {'epoch': epoch, 'action': action}
            session.update(transition_epochs=session.state.get('transition_epochs', []) + [record])
            ops.execute(session, command(action, epoch), cwd=str(destination), timeout=timeout)
            seal_one(record)
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
    parser.add_argument('--watchdog-retry', action='store_true')
    args = parser.parse_args()
    print(json.dumps(recover_normal_complete(args.old_session, args.capsule_sha256,
          args.journal_dir, args.compat_path, args.compat_sha256, apply=args.apply,
          watchdog_retry=args.watchdog_retry), sort_keys=True))


if __name__ == '__main__':
    main()
