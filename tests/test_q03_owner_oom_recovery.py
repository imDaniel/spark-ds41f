"""Exercise narrow legacy recovery admission with the actual owner validator."""
import copy
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import q03_window as core
import q03_owner_oom_recovery as recovery
from test_q03_owner_model import OwnerPlanFixture, FakeOwnerOps


class CompatibilityTests(OwnerPlanFixture):
    def setUp(self):
        super().setUp()
        self.value = self.plan()
        self.row = {'rank': 0, 'container_id': '1' * 64}
        self.witness = FakeOwnerOps(self.owner, None, self.value).container(0, self.row)
        self.witness['HostConfig']['OomKillDisable'] = False
        self.row['config_sha256'] = core.held_container_sha256(self.witness)
        self.live = copy.deepcopy(self.witness)
        self.live['State']['Running'] = True
        self.live['HostConfig']['OomKillDisable'] = None

    def validate(self, live=None, witness=None):
        recovery.compatible_validate(self.owner, core, self.live if live is None else live,
            self.value, self.row, self.witness if witness is None else witness)

    def test_real_validator_rejects_nullable_default_but_compat_accepts(self):
        with self.assertRaisesRegex(RuntimeError, 'immutable config drift'):
            self.owner.validate_container(self.live, self.value, self.row)
        original = copy.deepcopy(self.live)
        self.validate()
        self.assertEqual(self.live, original)
        self.assertIsNone(self.live['HostConfig']['OomKillDisable'])

    def test_explicit_false_and_stopped_alias_accept(self):
        for value in (False, None):
            live = copy.deepcopy(self.live)
            live['HostConfig']['OomKillDisable'] = value
            live['State']['Running'] = False
            self.validate(live)

    def test_true_missing_numeric_and_string_are_not_aliases(self):
        for value in (True, 0, 1, '', 'false', [], {}):
            live = copy.deepcopy(self.live)
            live['HostConfig']['OomKillDisable'] = value
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                self.validate(live)
        live = copy.deepcopy(self.live)
        del live['HostConfig']['OomKillDisable']
        with self.assertRaises(RuntimeError):
            self.validate(live)

    def test_no_nullable_or_foreign_witness_can_authorize(self):
        for value in (None, True, 0):
            witness = copy.deepcopy(self.witness)
            witness['HostConfig']['OomKillDisable'] = value
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                self.validate(witness=witness)
        witness = copy.deepcopy(self.witness)
        witness['State']['Running'] = True
        with self.assertRaises(RuntimeError):
            self.validate(witness=witness)

    def test_other_config_and_identity_drift_still_rejected(self):
        for field, value in (('Privileged', True), ('Memory', 0), ('OomScoreAdj', -1000),
                             ('CapAdd', ['SYS_ADMIN']), ('Runtime', 'other')):
            live = copy.deepcopy(self.live)
            live['HostConfig'][field] = value
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                self.validate(live)
        for field, value in (('Id', '2' * 64), ('Image', 'sha256:'+'0'*64)):
            live = copy.deepcopy(self.live)
            live[field] = value
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                self.validate(live)
        live = copy.deepcopy(self.live)
        live['Config']['Labels']['q03.attempt'] = 'x'*32
        with self.assertRaises(RuntimeError):
            self.validate(live)

    def test_altered_recorded_hash_is_not_learned_from_current(self):
        self.row['config_sha256'] = core.held_container_sha256(self.live)
        with self.assertRaises(RuntimeError):
            self.validate()


class DrainIntegrationTests(OwnerPlanFixture):
    def scenario(self, name):
        from types import SimpleNamespace
        plan = self.plan()
        directory = self.directory / name
        (directory / 'attempts' / plan['attempt_id']).mkdir(parents=True)
        session = SimpleNamespace(directory=directory, capsule={'window_id': plan['window_id']},
                                  check=lambda phase: None)
        ops = FakeOwnerOps(self.owner, session, plan)
        inspect = ops.inspect
        action = ops.action
        def retained_inspect(rank, row):
            item = inspect(rank, row)
            ops.journal.event('daemon-inspect', rank=rank, container=item)
            return item
        def docker_default_action(rank, kind, row):
            result = action(rank, kind, row)
            if kind == 'start':
                ops.items[rank]['HostConfig']['OomKillDisable'] = None
            return result
        ops.inspect = retained_inspect
        ops.action = docker_default_action
        ops.mutate = lambda rank, item: item['HostConfig'].update(OomKillDisable=False)
        with self.assertRaisesRegex(RuntimeError, 'immutable config drift'):
            self.owner.run_owner(session, plan, ops=ops)
        self.assertTrue(all(item['State']['Running'] for item in ops.items.values()))
        return session, plan, ops

    def test_exact_four_daemons_stop_remove_and_guards_drain_without_start(self):
        session, plan, ops = self.scenario('drain')
        start = len(ops.events)
        proof = recovery.quiesce(session, plan['attempt_id'], self.owner, core, ops=ops)
        self.assertTrue(proof['daemon_quiescent'])
        self.assertFalse(ops.items)
        self.assertTrue(all(not guard['active'] for guard in ops.guards.values()))
        self.assertEqual([rank for kind, rank in ops.events[start:] if kind == 'stop'], list(range(4)))
        self.assertFalse(any(kind in ('create', 'start', 'startup_guard') for kind, rank in ops.events[start:]))

    def test_fourth_rank_non_alias_drift_refuses_before_any_stop(self):
        session, plan, ops = self.scenario('foreign')
        ops.items[3]['HostConfig']['Memory'] = 0
        start = len(ops.events)
        with self.assertRaisesRegex(RuntimeError, 'immutable config drift'):
            recovery.quiesce(session, plan['attempt_id'], self.owner, core, ops=ops)
        self.assertFalse(any(kind in ('stop', 'remove', 'stop_guard') for kind, rank in ops.events[start:]))

    def test_stubborn_daemon_does_not_produce_false_drained_proof(self):
        session, plan, ops = self.scenario('stubborn')
        ops.stubborn = True
        with self.assertRaisesRegex(RuntimeError, 'still running'):
            recovery.quiesce(session, plan['attempt_id'], self.owner, core, ops=ops)
        self.assertFalse((session.directory/'attempts'/plan['attempt_id']/'owner'/'drained.json').exists())
