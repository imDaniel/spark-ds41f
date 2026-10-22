"""CPU process-epoch checks only. No models, devices or live service operations."""
import copy
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import q03_baseline_identity as I
import q03_baseline_worker as W
import q03_baseline_service as B
import q03_held_window as H
import test_q03_baseline_identity as fixtures
import test_q03_http_independent_faults as lifecycle


def stat(pid=123, comm=b'python', ticks=b'987654'):
    # pid/comm are fields 1/2, then state and fields 4 through 52.
    fields = [b'R'] + [b'0'] * 49
    fields[19] = ticks
    return str(pid).encode() + b' (' + comm + b') ' + b' '.join(fields) + b'\n'


class ProcessReaderTests(unittest.TestCase):
    def test_field_22_survives_spaces_newlines_and_parentheses_in_comm(self):
        for comm in (b'python', b'a b', b'left(right)', b'a) R 1 2 (x) ', b'a\nb) c'):
            with self.subTest(comm=comm):
                self.assertEqual(W.process_starttime_ticks(stat(comm=comm), 123), 987654)

    def test_incomplete_record_within_or_after_field_22_is_rejected(self):
        full = stat()
        start = full.index(b'987654')
        # Every truncated prefix from inside starttime through the missing
        # terminal newline must fail, never return shorter positive ticks.
        for cut in range(start + 1, len(full)):
            with self.subTest(cut=cut), self.assertRaises(RuntimeError):
                W.process_starttime_ticks(full[:cut], 123)
        # A newline alone cannot make a record ending at/inside field 22 valid.
        for digits in range(1, 7):
            with self.subTest(digits=digits), self.assertRaises(RuntimeError):
                W.process_starttime_ticks(full[:start + digits] + b'\n', 123)
        prefix, suffix = full.rsplit(b') ', 1)
        fields = suffix.split()
        self.assertEqual(len(fields), 50)
        for count in range(20, 50):
            with self.subTest(fields=count), self.assertRaises(RuntimeError):
                W.process_starttime_ticks(prefix + b') ' + b' '.join(fields[:count]) + b'\n', 123)
        # Future appended numeric fields do not move the existing field 22.
        self.assertEqual(W.process_starttime_ticks(full[:-1] + b' 0\n', 123), 987654)

    def test_missing_unknown_truncated_malformed_or_dead_stat_is_rejected(self):
        invalid = [None, True, '', b'', b'x' * 65537, stat(pid=124),
                   stat().replace(b'123 (', b'123 ', 1),
                   b'123 (no closing delimiter', b'123 (python) R 1 2',
                   stat().replace(b') R ', b') ? ', 1),
                   stat().replace(b') R ', b') Z ', 1),
                   stat().replace(b') R ', b') X ', 1),
                   stat() + b'garbage']
        invalid += [stat(ticks=t) for t in (b'0', b'-1', b'1.0', b'True', b'unknown', b'1e3', b'+1',
                                          str(2**64).encode(), b'9' * 5000)]
        for raw in invalid:
            with self.subTest(raw=repr(raw)[:80]), self.assertRaises(RuntimeError):
                W.process_starttime_ticks(raw, 123)
        for pid in (True, False, None, 0, -1, '123', 123.0, 2**31):
            with self.subTest(pid=pid), self.assertRaises(RuntimeError):
                W.process_starttime_ticks(stat(), pid)

    def test_reader_uses_only_bounded_actual_self_stat_and_fails_closed(self):
        stream = mock.mock_open(read_data=stat())
        with mock.patch.object(W.sys, 'platform', 'linux'), \
             mock.patch.object(W.os, 'getpid', return_value=123), \
             mock.patch.object(W.Path, 'open', stream):
            self.assertEqual(W.observe_worker_process(), {'worker_pid': 123, 'worker_starttime_ticks': 987654})
        stream.assert_called_once_with('rb')
        stream().read.assert_called_once_with(65537)
        with mock.patch.object(W.sys, 'platform', 'linux'), \
             mock.patch.object(W.Path, 'open', side_effect=FileNotFoundError):
            with self.assertRaisesRegex(RuntimeError, 'unavailable'):
                W.observe_worker_process()
        with mock.patch.object(W.sys, 'platform', 'darwin'), mock.patch.object(W.Path, 'open') as opened:
            with self.assertRaisesRegex(RuntimeError, 'requires Linux'):
                W.observe_worker_process()
            opened.assert_not_called()

    @unittest.skipUnless(sys.platform == 'linux', 'actual self-process reader requires Linux')
    def test_actual_linux_self_process_identity_is_stable(self):
        first = W.observe_worker_process()
        self.assertEqual(set(first), {'worker_pid', 'worker_starttime_ticks'})
        self.assertEqual(first['worker_pid'], os.getpid())
        self.assertIs(type(first['worker_pid']), int)
        self.assertIs(type(first['worker_starttime_ticks']), int)
        self.assertGreater(first['worker_starttime_ticks'], 0)
        self.assertEqual(first, W.observe_worker_process())
        # Independent comparison to the already established watchdog observer;
        # no kill, wait, child process, model import or external command.
        from q03_window_watchdog import process_identity
        legacy = process_identity(os.getpid())
        self.assertEqual(first, {'worker_pid': legacy['pid'], 'worker_starttime_ticks': int(legacy['start_ticks'])})


class ProcessReadyTests(unittest.TestCase):
    def test_missing_unknown_boolean_and_malformed_identity_are_rejected_on_every_rank(self):
        for rank in range(4):
            for key in ('worker_pid', 'worker_starttime_ticks'):
                for invalid in (None, 'unknown', '', '123', 123.0, True, False, 0, -1, 2**64, {}, []):
                    plan, ready = fixtures.plan_and_ready()
                    ready['reports'][rank][key] = invalid
                    ready['reports_sha256'] = I.digest(I.canonical(ready['reports']))
                    with self.subTest(rank=rank, key=key, invalid=invalid), \
                         self.assertRaisesRegex(ValueError, 'process identity'):
                        I.validate_ready(I.canonical(ready), plan)
                plan, ready = fixtures.plan_and_ready()
                del ready['reports'][rank][key]
                ready['reports_sha256'] = I.digest(I.canonical(ready['reports']))
                with self.assertRaisesRegex(ValueError, 'process identity'):
                    I.validate_ready(I.canonical(ready), plan)

    def test_pid_field_width_is_not_starttime_field_width(self):
        plan, ready = fixtures.plan_and_ready()
        ready['reports'][0]['worker_pid'] = 2**31
        ready['reports_sha256'] = I.digest(I.canonical(ready['reports']))
        with self.assertRaisesRegex(ValueError, 'process identity'):
            I.validate_ready(I.canonical(ready), plan)

    def test_same_pid_on_different_container_ranks_is_valid_not_global_identity(self):
        plan, ready = fixtures.plan_and_ready()
        self.assertEqual({r['worker_pid'] for r in ready['reports']}, {101})
        self.assertEqual(I.validate_ready(I.canonical(ready), plan), ready)

    def test_nonobject_worker_report_fails_closed(self):
        for invalid in (None, [], True, 123):
            plan, ready = fixtures.plan_and_ready()
            ready['reports'][0] = invalid
            ready['reports_sha256'] = I.digest(I.canonical(ready['reports']))
            with self.assertRaisesRegex(ValueError, 'not an object'):
                I.validate_ready(I.canonical(ready), plan)


class ExistingLifecycleTests(lifecycle.NoExternalOperations):
    """Real owned HTTP loop and validators; injected external observations only."""
    def setUp(self):
        super().setUp()
        self.counter = 0
        for patch in (mock.patch.object(self.http, 'read_owned_bytes', lambda p: Path(p).read_bytes()),
                      mock.patch.object(H, 'read_owned_bytes', lambda p: Path(p).read_bytes())):
            patch.start(); self.addCleanup(patch.stop)

    def scenario(self, change=None):
        session, plan, ops = lifecycle.ActualHttpLifecycleFaults.scenario(self)
        baseline, ready = fixtures.plan_and_ready()
        plan.update(selection=baseline['selection'], spec=baseline['spec'],
                    image=I.IDENTITIES['r6x188']['image'])
        plan['binding']['http']['schema_version'] = 6
        plan['spec_sha256'] = I.digest(I.canonical(plan['spec']))
        ready['spec_sha256'] = plan['spec_sha256']
        for report in ready['reports']:
            report['spec_sha256'] = plan['spec_sha256']
        ready['reports_sha256'] = I.digest(I.canonical(ready['reports']))
        before_hash = dict(plan); before_hash.pop('plan_sha256')
        plan['plan_sha256'] = I.digest(I.canonical(before_hash))
        container = ops.container
        def observed_container(rank, row):
            item = container(rank, row)
            item['Image'] = plan['image']
            return item
        ops.container = observed_container
        final = copy.deepcopy(ready)
        if change:
            change(final['reports'])
            final['reports_sha256'] = I.digest(I.canonical(final['reports']))
        ops.read_ready = mock.Mock(side_effect=[I.canonical(ready), I.canonical(final)])
        return session, plan, ops

    def test_same_actual_epoch_passes_existing_pre_post_comparison(self):
        session, plan, ops = self.scenario()
        self.assertEqual(self.http.run_http(session, plan, ops=ops)['result'], 'passed')
        self.assertEqual(ops.read_ready.call_count, 2)
        self.assertIn(('collect', None), ops.events)

    def test_pid_or_reused_pid_new_starttime_refuses_result_before_collection(self):
        for rank in range(4):
            for key in ('worker_pid', 'worker_starttime_ticks'):
                with self.subTest(rank=rank, key=key):
                    session, plan, ops = self.scenario(lambda rows: rows[rank].update({key: rows[rank][key] + 1}))
                    with self.assertRaisesRegex(RuntimeError, 'workers changed during client'):
                        self.http.run_http(session, plan, ops=ops)
                    self.assertNotIn(('collect', None), ops.events)
                    self.assertEqual(ops.durable()['phase'], 'client_running')
                    events = [I.json_bytes(p.read_bytes()) for p in ops.journal.directory.glob('[0-9]*.json')]
                    self.assertTrue(any(e['kind'] == 'failure' for e in events))

    def test_finite_source_pins_match_unmodified_loader_chain(self):
        self.assertEqual(B.IDENTITY_SOURCE_SHA256,
                         I.digest((ROOT / 'scripts/q03_baseline_identity.py').read_bytes()))
        self.assertEqual(self.http.BASELINE_SOURCE_SHA256,
                         I.digest((ROOT / 'scripts/q03_baseline_service.py').read_bytes()))
        self.assertEqual(self.http.baseline_module().identity_module().checked_identity('r6x188'),
                         I.checked_identity('r6x188'))


if __name__ == '__main__':
    unittest.main()
