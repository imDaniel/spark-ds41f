"""CPU-only recovery lifecycle fault cases. Never use Docker, SSH or a GPU."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import q03_owner_oom_recovery as recovery
from test_q03_owner_model import OwnerPlanFixture

MAIN = {'pid': 11, 'start_ticks': '12'}
CHILD = {'pid': 21, 'start_ticks': '22'}
SUPERVISOR = {'pid': 31, 'start_ticks': '32'}


def proc(pid=11, start='12', state='S'):
    return f'{pid} (a name with ) parens) ' + ' '.join([state] + ['0'] * 18 + [start])


class IdentityTests(unittest.TestCase):
    def test_dead_reused_alive_zombie_distinct(self):
        for raw, expected in [(proc(), 'alive'), (proc(state='Z'), 'dead'),
                              (proc(start='13'), 'reused'), ('bad', 'unknown'),
                              (proc(pid=99), 'unknown'), (proc(state='?'), 'unknown')]:
            with self.subTest(expected=expected), mock.patch.object(Path, 'read_text', return_value=raw):
                self.assertEqual(recovery.process_state(MAIN), expected)
        for error, expected in [(FileNotFoundError(), 'dead'), (ProcessLookupError(), 'dead'),
                                (PermissionError(), 'unknown'), (OSError(), 'unknown')]:
            with self.subTest(error=error), mock.patch.object(Path, 'read_text', side_effect=error):
                self.assertEqual(recovery.process_state(MAIN), expected)

    def test_bad_identity_never_reads_proc_or_becomes_death(self):
        for record in [None, {}, {'pid': True, 'start_ticks': '12'},
                       {'pid': -1, 'start_ticks': '12'}, {'pid': '11', 'start_ticks': '12'},
                       {'pid': 11, 'start_ticks': 12}, dict(MAIN, extra=True)]:
            with self.subTest(record=record), mock.patch.object(Path, 'read_text') as read:
                self.assertEqual(recovery.process_state(record), 'unknown')
                read.assert_not_called()

    def test_required_old_ready_is_bound_to_window_and_capsule(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp).resolve()
            capsule = {'window_id': 'window', 'core_process': MAIN}
            with self.assertRaises(FileNotFoundError):
                recovery.require_terminal(directory, capsule, 'pin')
            for ready in [{'process': CHILD}, {'process': CHILD, 'window_id': 'foreign', 'capsule_sha256': 'pin'},
                          {'process': CHILD, 'window_id': 'window', 'capsule_sha256': 'wrong'}]:
                (directory / 'watchdog.ready.json').write_text(json.dumps(ready))
                with mock.patch.object(recovery, 'process_state', return_value='dead'), self.assertRaises(RuntimeError):
                    recovery.require_terminal(directory, capsule, 'pin')
            ready = {'process': CHILD, 'window_id': 'window', 'capsule_sha256': 'pin'}
            (directory / 'watchdog.ready.json').write_text(json.dumps(ready))
            for state in ('alive', 'unknown', 'reused'):
                with self.subTest(state=state), mock.patch.object(recovery, 'process_state', side_effect=['dead', state]), \
                        self.assertRaisesRegex(RuntimeError, 'watchdog'):
                    recovery.require_terminal(directory, capsule, 'pin')
            with mock.patch.object(recovery, 'process_state', return_value='dead'):
                self.assertEqual(recovery.require_terminal(directory, capsule, 'pin'), ready)

    def test_lifetime_locks_refuse_symlink_and_second_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp).resolve()
            target = directory / 'lock'
            target.touch()
            alias = directory / 'alias'; alias.symlink_to(target)
            with self.assertRaises(OSError):
                with recovery.locked(alias): pass
            with recovery.locked(target):
                with self.assertRaises(BlockingIOError):
                    with recovery.locked(target): pass


class FakeMonitor:
    def __init__(self, health, done_at=None):
        self.time, self.health, self.launches = 0, list(health), []
        self.done_at, self.target = done_at, MAIN
        self.hold_missing, self.foreign, self.changed, self.stale = False, False, False, False
        self.closed_tail = False
        self.heartbeat_delay = 0
    def now(self): return self.time
    def pulse(self, directory, pin): pass
    def sleep(self, delay): self.time += delay
    def process_state(self, _): return self.health.pop(0) if self.health else 'alive'
    def launch_retry(self, directory, intent, main):
        self.launches.append(main)
        self.target = CHILD
        return CHILD
    def snapshot(self, _):
        done = self.done_at is not None and self.time >= self.done_at
        state = {'phase': 'closed-stopped' if done or self.closed_tail else 'restoring',
                 'original_kept_stopped': done or self.closed_tail, 'restored': False,
                 'hold_released': done}
        return ({'main': MAIN, 'deadline_epoch': 10}, {'heartbeat_timeout_s': 5},
                'changed' if self.changed and self.time else 'pin', state,
                None if done or self.hold_missing else {'owned': True},
                {'process': MAIN if self.time < self.heartbeat_delay else self.target,
                 'intent_sha256': 'pin', 'epoch': -20 if self.stale else self.time})


class MonitorTests(unittest.TestCase):
    def run_monitor(self, ops): return recovery.monitor(Path('/unused'), ops)
    def test_live_main_and_complete_closure_never_retried(self):
        ops = FakeMonitor(['alive'], done_at=3)
        self.assertFalse(self.run_monitor(ops)['retry_started'])
        self.assertFalse(ops.launches)
    def test_dead_main_retries_exactly_once(self):
        ops = FakeMonitor(['dead', 'alive'], done_at=3)
        self.assertTrue(self.run_monitor(ops)['retry_started'])
        self.assertEqual(ops.launches, [MAIN])
    def test_dead_retry_is_unknown_not_recursive(self):
        ops = FakeMonitor(['dead', 'dead'])
        with self.assertRaisesRegex(RuntimeError, 'single retry'):
            self.run_monitor(ops)
        self.assertEqual(len(ops.launches), 1)
    def test_unknown_and_reused_never_signal_or_retry(self):
        for state in ('unknown', 'reused'):
            ops = FakeMonitor([state])
            with self.subTest(state=state), self.assertRaisesRegex(RuntimeError, 'identity uncertain'):
                self.run_monitor(ops)
            self.assertFalse(ops.launches)
    def test_changed_intent_and_stale_heartbeat_fail_closed(self):
        for field, message in [('changed', 'intent changed'), ('stale', 'heartbeat stale')]:
            ops = FakeMonitor(['alive'] * 4); setattr(ops, field, True)
            with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, message):
                self.run_monitor(ops)
            self.assertFalse(ops.launches)
    def test_deadline_does_not_refresh_or_retry_live_main(self):
        ops = FakeMonitor(['alive'] * 20)
        with self.assertRaisesRegex(RuntimeError, 'deadline reached'):
            self.run_monitor(ops)
        self.assertEqual(ops.time, 10)
        self.assertFalse(ops.launches)
    def test_retry_has_bounded_heartbeat_startup_grace(self):
        ops = FakeMonitor(['dead'] + ['alive'] * 20, done_at=4)
        ops.heartbeat_delay = 3
        self.assertTrue(self.run_monitor(ops)['retry_started'])
        self.assertEqual(len(ops.launches), 1)
    def test_death_between_unlink_and_final_state_write_finishes_same_intent(self):
        ops = FakeMonitor(['dead', 'alive'], done_at=2)
        ops.closed_tail = ops.hold_missing = True
        self.assertTrue(self.run_monitor(ops)['retry_started'])
        self.assertEqual(ops.launches, [MAIN])
    def test_already_completed_closure_never_relaunches(self):
        ops = FakeMonitor(['dead'], done_at=0)
        self.assertFalse(self.run_monitor(ops)['retry_started'])
        self.assertFalse(ops.launches)


class RetryAuthorityTests(unittest.TestCase):
    def test_flag_alone_and_foreign_retry_never_authorize(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp).resolve(); journal = directory / recovery.JOURNAL_NAME; journal.mkdir()
            intent = {'main': MAIN}
            with self.assertRaises(FileNotFoundError): recovery.validate_retry(directory, intent, 'pin')
            ready = {'main': MAIN, 'supervisor': SUPERVISOR, 'intent_sha256': 'pin'}
            retry = {'main': MAIN, 'supervisor': SUPERVISOR, 'child': CHILD, 'intent_sha256': 'pin'}
            for name, item in [('ready.json', ready), ('retry.json', retry)]:
                (journal / name).write_text(json.dumps(item))
            with mock.patch.object(recovery, 'identity', return_value=CHILD), \
                    mock.patch.object(recovery, 'process_state', side_effect=['alive', 'dead']):
                self.assertEqual(recovery.validate_retry(directory, intent, 'pin'), ready)
            for change in ({'child': MAIN}, {'main': CHILD}, {'intent_sha256': 'wrong'}, {'supervisor': CHILD}):
                (journal / 'retry.json').write_text(json.dumps(dict(retry, **change)))
                with self.subTest(change=change), mock.patch.object(recovery, 'identity', return_value=CHILD), \
                        mock.patch.object(recovery, 'process_state', return_value='alive'), self.assertRaises(RuntimeError):
                    recovery.validate_retry(directory, intent, 'pin')
    def test_command_is_closed_and_contains_no_user_argv(self):
        intent = {'old_session': '/retained/session', 'capsule_sha256': 'a' * 64,
                  'command': ['malicious', 'ignored']}
        command = recovery.fixed_command(intent, '--watchdog-retry')
        self.assertNotIn('malicious', command)
        self.assertEqual(command[-1], '--watchdog-retry')
        for action in ('start', '--argv', '--watch; command'):
            with self.assertRaises(RuntimeError): recovery.fixed_command(intent, action)
    def test_missing_or_foreign_hold_is_not_absence_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp).resolve() / 'hold.json'
            capsule = {'hold_path': str(path), 'window_id': 'window', 'holder': 'owner'}
            with self.assertRaises(RuntimeError): recovery.owned_or_closed_hold(capsule, lambda: {'phase': 'restoring'})
            state = {'phase': 'closed-stopped', 'original_kept_stopped': True, 'restored': False}
            self.assertIsNone(recovery.owned_or_closed_hold(capsule, lambda: state)[0])
            path.write_text(json.dumps({'window': 'other', 'holder': 'owner'}))
            with self.assertRaisesRegex(RuntimeError, 'foreign'):
                recovery.owned_or_closed_hold(capsule, lambda: state)


class OwnerInventoryTests(OwnerPlanFixture):
    def fixture(self):
        from types import SimpleNamespace
        plan = self.plan()
        directory = self.directory / 'session'; path = directory / 'attempts' / plan['attempt_id'] / 'owner'
        path.mkdir(parents=True)
        self.owner.exclusive_json(path / 'plan.json', plan)
        session = SimpleNamespace(directory=directory, state={'active_attempt': None},
                                  capsule={'window_id': plan['window_id']})
        return session, plan, path
    def test_cleared_active_pointer_still_identifies_exact_plan(self):
        session, plan, path = self.fixture()
        actual, pin = recovery.owner_inventory(session, self.owner)
        self.assertEqual(actual, plan)
        self.assertEqual(pin, hashlib.sha256((path / 'plan.json').read_bytes()).hexdigest())
    def test_second_plan_or_foreign_active_pointer_refuses(self):
        session, plan, path = self.fixture()
        session.state['active_attempt'] = {'attempt_id': 'foreign'}
        with self.assertRaisesRegex(RuntimeError, 'active owner'):
            recovery.owner_inventory(session, self.owner)
        session.state['active_attempt'] = None
        second = session.directory / 'attempts' / ('f' * 32) / 'owner'
        second.mkdir(parents=True); (second / 'plan.json').write_bytes((path / 'plan.json').read_bytes())
        with self.assertRaisesRegex(RuntimeError, 'exactly one'):
            recovery.owner_inventory(session, self.owner)
    def test_complete_event_tail_replayed_without_rewriting_evidence(self):
        import contextlib
        session, plan, path = self.fixture()
        (path / 'owner.lock').touch()
        self.owner.exclusive_json(path / 'state.json', {'sequence': 0, 'ranks': [], 'epochs': []})
        event = {'sequence': 1, 'kind': 'remote-intent', 'window_id': plan['window_id'],
                 'attempt_id': plan['attempt_id']}
        self.owner.exclusive_json(path / '000001.json', event)
        before = (path / '000001.json').read_bytes()
        with contextlib.ExitStack() as locks:
            journal = recovery.open_owner_journal(session, plan, self.owner, locks)
            self.assertEqual(journal.state['sequence'], 1)
        self.assertEqual((path / '000001.json').read_bytes(), before)
        self.assertEqual(json.loads((path / 'state.json').read_text())['sequence'], 0)
    def test_noncontiguous_or_foreign_event_tail_refuses(self):
        import contextlib
        session, plan, path = self.fixture()
        (path / 'owner.lock').touch()
        self.owner.exclusive_json(path / 'state.json', {'sequence': 0})
        self.owner.exclusive_json(path / '000002.json', {'sequence': 2, 'window_id': plan['window_id'],
                                                       'attempt_id': plan['attempt_id']})
        with contextlib.ExitStack() as locks, self.assertRaisesRegex(RuntimeError, 'event tail'):
            recovery.open_owner_journal(session, plan, self.owner, locks)


class RecoveryFlowTests(OwnerPlanFixture):
    """Exercise real old restore ordering with all external boundaries faked."""
    def fixture(self):
        import contextlib
        from types import SimpleNamespace
        import q03_window as old
        plan = self.plan()
        directory = self.directory / 'session'; directory.mkdir()
        owner_path = directory / 'attempts' / plan['attempt_id'] / 'owner'; owner_path.mkdir(parents=True)
        self.owner.exclusive_json(owner_path / 'plan.json', plan)
        self.owner.exclusive_json(owner_path / 'state.json', {'sequence': 0, 'ranks': [], 'epochs': []})
        for path in (owner_path / 'owner.lock', directory / 'session.lock', directory / 'watchdog.lock'): path.touch()
        hold = directory / 'hold.json'
        capsule = {'window_id': plan['window_id'], 'holder': 'owner', 'core_process': {'pid': 41, 'start_ticks': '42'},
                   'hold_path': str(hold), 'recovery_timeout_s': 7200, 'heartbeat_timeout_s': 90,
                   'manifest': {'jobs': [{'kind': 'owner_model'}],
                                'preflight': {'original_service_disposition': 'keep-stopped'}}}
        hold.write_text(json.dumps({'window': plan['window_id'], 'holder': 'owner'}))
        raw = json.dumps(capsule).encode(); (directory / 'capsule.json').write_bytes(raw)
        pin = hashlib.sha256(raw).hexdigest(); (directory / 'capsule.sha256').write_text(pin)
        (directory / 'watchdog.ready.json').write_text(json.dumps({'window_id': plan['window_id'],
            'capsule_sha256': pin, 'process': {'pid': 51, 'start_ticks': '52'}}))
        initial = {'phase': 'recovery-failed', 'mutation_started': True,
                   'restoring_started_epoch': recovery.time.time(), 'active_process': None,
                   'active_attempt': None, 'transition_epochs': []}
        (directory / 'state.json').write_text(json.dumps(initial))
        events, failure = [], [None]
        class Ops:
            def acquire_gpu_lock(self): return contextlib.nullcontext()
            def terminate_owned_process(self, record): events.append('terminate')
            def cleanup_candidates(self, session): events.append('cleanup-candidates')
            def quiesce_original(self, session): events.append('seal-original')
            def execute(self, session, argv, **kw):
                action = argv[argv.index('--action') + 1]
                events.append(action)
                if failure[0] == action: raise RuntimeError('injected ' + action)
            def check(self, phase, manifest):
                events.append(phase)
                if failure[0] == phase: raise RuntimeError('injected ' + phase)
        class Session:
            def __init__(self, directory, ops):
                self.directory, self.ops, self.capsule = directory, ops, capsule
                self.manifest, self.hold = capsule['manifest'], hold
                self.state = json.loads((directory / 'state.json').read_text())
            def update(self, **kw):
                self.state.update(kw); old.atomic(directory / 'state.json', self.state)
            def owns_hold(self): return hold.exists()
            def hold_status(self): return 'owned' if hold.exists() else 'absent'
            def beat(self): pass
            def check(self, phase): return self.ops.check(phase, self.manifest)
            def release(self):
                events.append('release')
                if failure[0] == 'before-unlink': raise RuntimeError('injected before-unlink')
                hold.unlink()
                if failure[0] == 'after-unlink': raise RuntimeError('injected after-unlink')
        capsule['manifest']['preflight'].update(policy_path='/policy', policy_sha256='p', spark_sha256='s')
        # Keep capsule bytes and the injectable Session view aligned.
        raw = json.dumps(capsule).encode(); (directory / 'capsule.json').write_bytes(raw)
        pin = hashlib.sha256(raw).hexdigest(); (directory / 'capsule.sha256').write_text(pin)
        (directory / 'watchdog.ready.json').write_text(json.dumps({'window_id': plan['window_id'],
            'capsule_sha256': pin, 'process': {'pid': 51, 'start_ticks': '52'}}))
        fake_core = SimpleNamespace(RuntimeOps=Ops, Session=Session, restore=old.restore, atomic=old.atomic)
        def launch(directory, intent):
            events.append('supervisor-ready')
            ready = {'main': MAIN, 'supervisor': SUPERVISOR,
                     'intent_sha256': recovery.digest(directory / recovery.JOURNAL_NAME / 'intent.json')}
            recovery.exclusive(directory / recovery.JOURNAL_NAME / 'ready.json', ready)
            recovery.exclusive(directory / recovery.JOURNAL_NAME / 'supervisor-heartbeat.json',
                {'process': SUPERVISOR, 'intent_sha256': ready['intent_sha256'], 'epoch': recovery.time.time()})
            return ready
        def cleanup(*args, **kw):
            events.append('cleanup-owner')
            if failure[0] == 'cleanup-owner': raise RuntimeError('injected cleanup-owner')
        return directory, pin, capsule, fake_core, events, failure, launch, cleanup

    def invoke(self, fixture, retry=False, launch_override=None):
        directory, pin, capsule, core, events, failure, launch, cleanup = fixture
        with mock.patch.object(recovery, 'load_legacy', return_value=(capsule, core, self.owner)), \
                mock.patch.object(recovery, 'identity', return_value=CHILD if retry else MAIN), \
                mock.patch.object(recovery, 'process_state', side_effect=lambda r: 'alive' if r == SUPERVISOR else 'dead'), \
                mock.patch.object(recovery, 'start_watchdog', side_effect=launch_override or launch), \
                mock.patch.object(recovery, 'quiesce', side_effect=cleanup):
            return recovery.run_recovery(directory, pin, retry=retry)

    def retry_marker(self, fixture):
        directory = fixture[0]; journal = directory / recovery.JOURNAL_NAME
        recovery.exclusive(journal / 'retry.json', {'main': MAIN, 'child': CHILD,
            'supervisor': SUPERVISOR, 'intent_sha256': recovery.digest(journal / 'intent.json')})

    def test_ready_precedes_mutation_and_no_start_or_closed_reuse(self):
        f = self.fixture(); state = self.invoke(f)
        self.assertTrue(state['hold_released']); self.assertTrue(state['original_kept_stopped'])
        self.assertIs(state['restored'], False)
        self.assertLess(f[4].index('supervisor-ready'), f[4].index('cleanup-owner'))
        self.assertNotIn('start', f[4])
        before = list(f[4])
        with self.assertRaisesRegex(RuntimeError, 'closed sessions cannot be reused'): self.invoke(f)
        self.assertEqual(f[4], before)

    def test_supervisor_loss_before_release_keeps_exact_hold(self):
        f = self.fixture()
        directory, pin, capsule, core, events, failure, launch, cleanup = f
        def health(record):
            if record == SUPERVISOR:
                return 'dead' if 'restore_drained' in events else 'alive'
            return 'dead'
        with mock.patch.object(recovery, 'load_legacy', return_value=(capsule, core, self.owner)), \
                mock.patch.object(recovery, 'identity', return_value=MAIN), \
                mock.patch.object(recovery, 'process_state', side_effect=health), \
                mock.patch.object(recovery, 'start_watchdog', side_effect=launch), \
                mock.patch.object(recovery, 'quiesce', side_effect=cleanup):
            with self.assertRaisesRegex(RuntimeError, 'supervisor lost'):
                recovery.run_recovery(directory, pin)
        self.assertTrue((directory / 'hold.json').exists())
        self.assertNotIn('release', events)

    def test_failed_handshake_has_no_cleanup_or_restore_mutation(self):
        f = self.fixture()
        with self.assertRaisesRegex(RuntimeError, 'handshake'):
            self.invoke(f, launch_override=lambda *a: (_ for _ in ()).throw(RuntimeError('handshake failed')))
        self.assertEqual(f[4], [])
        self.assertTrue((f[0] / 'hold.json').exists())

    def test_interrupted_restoring_same_bound_retry_and_original_budget(self):
        for point in ('cleanup-owner', 'stop', 'restore_drained'):
            with self.subTest(point=point):
                # Each counterexample gets an independent old session fixture.
                prior = self.directory; self.directory = prior / point; self.directory.mkdir()
                f = self.fixture(); f[5][0] = point
                with self.assertRaisesRegex(RuntimeError, 'injected'): self.invoke(f)
                state_path = f[0] / 'state.json'; state = json.loads(state_path.read_text())
                started = state['restoring_started_epoch']; state['phase'] = 'restoring'
                state_path.write_text(json.dumps(state))  # Persisted boundary after abrupt process death.
                self.retry_marker(f); f[5][0] = None
                result = self.invoke(f, retry=True)
                self.assertTrue(result['hold_released'])
                self.assertEqual(result['restoring_started_epoch'], started)
                self.assertNotIn('start', f[4])
                self.directory = prior

    def test_closure_crash_before_and_after_unlink_is_only_finalized(self):
        for point in ('before-unlink', 'after-unlink'):
            with self.subTest(point=point):
                prior = self.directory; self.directory = prior / point; self.directory.mkdir()
                f = self.fixture(); f[5][0] = point
                with self.assertRaisesRegex(RuntimeError, 'injected'): self.invoke(f)
                self.retry_marker(f); f[5][0] = None; before = len(f[4])
                state = self.invoke(f, retry=True)
                self.assertTrue(state['hold_released'])
                self.assertNotIn('cleanup-owner', f[4][before:])
                self.assertNotIn('stop', f[4][before:])
                self.assertNotIn('start', f[4])
                self.directory = prior


class MutableSnapshotTests(unittest.TestCase):
    def test_atomic_replacement_is_a_valid_mutable_snapshot_not_immutable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp).resolve() / 'state.json'
            def probe(reader):
                path.write_text(json.dumps({'epoch': 1}))
                native, changed = os.fstat, [False]
                def racing_stat(fd):
                    result = native(fd)
                    if not changed[0]:
                        changed[0] = True
                        new = path.with_name('new.json'); new.write_text(json.dumps({'epoch': 2}))
                        os.replace(new, path)
                    return result
                with mock.patch.object(recovery.os, 'fstat', side_effect=racing_stat):
                    return reader(path)
            self.assertEqual(probe(recovery.mutable_value), {'epoch': 1})
            with self.assertRaisesRegex(RuntimeError, 'file changed'):
                probe(recovery.value)

    def test_mutable_inplace_instability_is_bounded_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp).resolve() / 'heartbeat.json'; path.write_text('{"epoch": 1}')
            native, calls = os.fstat, [0]
            from types import SimpleNamespace
            def unstable(fd):
                info = native(fd); calls[0] += 1
                return SimpleNamespace(st_mode=info.st_mode, st_dev=info.st_dev,
                    st_ino=info.st_ino, st_size=info.st_size, st_mtime_ns=calls[0], st_ctime_ns=info.st_ctime_ns)
            with mock.patch.object(recovery.os, 'fstat', side_effect=unstable), \
                    self.assertRaisesRegex(RuntimeError, 'did not stabilize'):
                recovery.mutable_value(path)
            self.assertEqual(calls[0], 8)

    def test_hold_read_takes_shared_lock_before_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp).resolve() / 'hold.json'; path.write_text('{"window": "w"}')
            native, calls = recovery.fcntl.flock, []
            def flock(stream, operation):
                calls.append(operation)
                return native(stream, operation)
            with mock.patch.object(recovery.fcntl, 'flock', side_effect=flock):
                self.assertEqual(recovery.mutable_value(path, hold=True), {'window': 'w'})
            self.assertEqual(calls, [recovery.fcntl.LOCK_SH])

    def test_release_during_snapshot_reconciles_latest_state_without_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp).resolve() / 'hold.json'
            path.write_text(json.dumps({'window': 'w', 'holder': 'h'}))
            capsule = {'hold_path': str(path), 'window_id': 'w', 'holder': 'h'}
            complete = {'phase': 'closed-stopped', 'restored': False,
                        'original_kept_stopped': True, 'hold_released': True}
            calls = []
            def state_after_hold():
                calls.append('state')
                if path.exists(): path.unlink()
                return complete
            hold, state = recovery.owned_or_closed_hold(capsule, state_after_hold)
            self.assertIsNone(hold); self.assertEqual(state, complete)
            self.assertEqual(calls, ['state', 'state'])
            # Reverse ordering: missing hold is compared with a state obtained
            # after unlink, never an earlier restoring snapshot.
            hold, state = recovery.owned_or_closed_hold(capsule, lambda: complete)
            self.assertIsNone(hold)

    def test_missing_unclosed_or_replaced_foreign_hold_still_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp).resolve() / 'hold.json'
            capsule = {'hold_path': str(path), 'window_id': 'w', 'holder': 'h'}
            with self.assertRaisesRegex(RuntimeError, 'missing hold'):
                recovery.owned_or_closed_hold(capsule, lambda: {'phase': 'restoring'})
            path.write_text(json.dumps({'window': 'w', 'holder': 'h'}))
            def foreign_after_state():
                path.write_text(json.dumps({'window': 'foreign', 'holder': 'h'}))
                return {'phase': 'closed-stopped', 'restored': False,
                        'original_kept_stopped': True, 'hold_released': True}
            with self.assertRaisesRegex(RuntimeError, 'foreign hold'):
                recovery.owned_or_closed_hold(capsule, foreign_after_state)


class VerifiedSourceLoaderTests(unittest.TestCase):
    def source_fixture(self, root):
        import py_compile
        pins = {}
        for name in recovery.SOURCE_NAMES:
            path = (root / 'scripts' / name).resolve(); path.parent.mkdir(parents=True, exist_ok=True)
            raw = b'VALUE = 1\n' if name.endswith('.py') else b'preserved source\n'
            if name == 'q03_owner_model.py':
                raw += b'import q03_window\nimport q03_held_window\nCORE = q03_window\n'
            path.write_bytes(raw); pins[name] = hashlib.sha256(raw).hexdigest()
        source = root / 'scripts/q03_window.py'
        source.write_bytes(b'VALUE = 2\n'); stamp = source.stat().st_mtime
        py_compile.compile(str(source), doraise=True)
        source.write_bytes(b'VALUE = 1\n'); os.utime(source, (stamp, stamp))
        return pins

    def test_isolated_interpreter_ignores_valid_unpinned_cached_bytecode(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); pins = self.source_fixture(root)
            program = '''import importlib,importlib.util,json,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('tested_recovery',sys.argv[1])
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
root=Path(sys.argv[2]);pins=json.loads(sys.argv[3])
r.install_legacy_sources(root,pins)
owner=importlib.import_module('q03_owner_model');core=importlib.import_module('q03_window')
assert core.VALUE == 1
assert owner.CORE is core and owner.q03_held_window.VALUE == 1
assert core.__name__ == 'q03_window'
assert core.__file__ == str(root/'scripts/q03_window.py')
assert isinstance(core.__loader__,r.PinnedLegacySources)
print('same verified source; canonical sibling modules; no pyc execution')
'''
            result = subprocess.run([sys.executable, '-I', '-B', '-c', program,
                recovery.__file__, str(root), json.dumps(pins)], check=True, text=True,
                capture_output=True, timeout=10)
            self.assertIn('no pyc execution', result.stdout)

    def test_missing_inventory_or_changed_source_refuses_before_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); pins = self.source_fixture(root)
            with self.assertRaisesRegex(RuntimeError, '22-source inventory'):
                recovery.install_legacy_sources(root, {})
            (root / 'scripts/q03_window.py').write_text('VALUE = 3\n')
            with self.assertRaisesRegex(RuntimeError, 'source differs'):
                recovery.install_legacy_sources(root, pins)

    def test_preloaded_same_name_is_not_accepted_as_verified_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); pins = self.source_fixture(root)
            with mock.patch.dict(sys.modules, {'q03_window': object()}), \
                    self.assertRaisesRegex(RuntimeError, 'already loaded outside'):
                recovery.install_legacy_sources(root, pins)


if __name__ == '__main__': unittest.main()
