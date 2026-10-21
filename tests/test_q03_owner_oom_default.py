"""Docker OOM default lifecycle normalization with all-rank runtime state machine."""
import copy
from types import SimpleNamespace
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import q03_window as core
from test_q03_owner_model import OwnerPlanFixture, FakeOwnerOps


class OomLifecycleTests(OwnerPlanFixture):
    def test_all_four_nullable_post_start_defaults_complete_and_drain(self):
        plan = self.plan()
        directory = self.directory / 'nullable'
        (directory / 'attempts' / plan['attempt_id']).mkdir(parents=True)
        session = SimpleNamespace(directory=directory, capsule={'window_id':plan['window_id']},
                                  check=lambda phase:None)
        ops = FakeOwnerOps(self.owner, session, plan)
        original = ops.action
        def docker_action(rank, kind, row):
            result = original(rank, kind, row)
            if kind == 'start':
                ops.items[rank]['HostConfig']['OomKillDisable'] = None
            return result
        ops.action = docker_action
        result = self.owner.run_owner(session, plan, ops=ops)
        self.assertEqual(result['status'], 'observed-only')
        proof = self.owner.quiesce_owner(session, plan['attempt_id'], ops=ops)
        self.assertTrue(proof['daemon_quiescent'])
        self.assertFalse(ops.items)

    def test_projection_normalizes_only_present_null_without_mutating_input(self):
        a = {'Image':'x','Config':{},'HostConfig':{'OomKillDisable':False},'Mounts':[]}
        b = copy.deepcopy(a)
        b['HostConfig']['OomKillDisable'] = None
        raw = copy.deepcopy(b)
        self.assertEqual(core.held_container_sha256(a), core.held_container_sha256(b))
        self.assertEqual(b, raw)
        for value in (True, 0, 1, 'false', []):
            bad = copy.deepcopy(a)
            bad['HostConfig']['OomKillDisable'] = value
            with self.subTest(value=value):
                self.assertNotEqual(core.held_container_sha256(a), core.held_container_sha256(bad))
        del b['HostConfig']['OomKillDisable']
        self.assertNotEqual(core.held_container_sha256(a), core.held_container_sha256(b))

    def test_initial_admission_rejects_disabled_missing_or_numeric_oom_setting(self):
        plan = self.plan()
        row = {'rank':0,'container_id':'1'*64}
        good = FakeOwnerOps(self.owner,None,plan).container(0,row)
        for value in (False,None):
            item = copy.deepcopy(good)
            item['HostConfig']['OomKillDisable'] = value
            self.owner.validate_container(item,plan,row,initial=True)
        for value in (True,0,1,'false'):
            item = copy.deepcopy(good)
            item['HostConfig']['OomKillDisable'] = value
            with self.subTest(value=value),self.assertRaisesRegex(RuntimeError,'OOM killing'):
                self.owner.validate_container(item,plan,row,initial=True)
        del good['HostConfig']['OomKillDisable']
        with self.assertRaisesRegex(RuntimeError,'OOM killing'):
            self.owner.validate_container(good,plan,row,initial=True)
