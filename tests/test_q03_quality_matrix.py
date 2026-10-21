"""CPU-only Stage 1b finite execution and fail-closed inventory. Never field data."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import q03_quality_matrix as M
import q03_qualification as Q
import q03_http_service as H
import test_q03_qualification as legacy_tests


class MatrixTests(unittest.TestCase):
    def setUp(self):
        self.fixture = legacy_tests.QualificationTest(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.serial = 10

    def plan(self, mode='off', steering='off'):
        self.serial += 1
        plan = self.fixture.make_plan(mode, f'{self.serial:032x}')
        plan['selection'] = {'mode': mode, 'steering': steering}
        q = plan['client']['qualification']
        q.update(workload=M.WORKLOAD, runs=M.runs(plan['selection']),
                 state_identity={'native': 'same', 'steering': steering},
                 reference_rows=self.fixture.generation(plan, 'unused')['generation']['sequences'],
                 reference_origin={'stage1a_capsule_sha256': 'a' * 64})
        if steering == 'active' and mode == 'off':
            q.update(reference_rows=None, reference_origin=None)
        elif steering == 'active':
            q['reference_origin'] = {'plan_sha256': 'b' * 64, 'result_sha256': 'c' * 64}
        plan['plan_sha256'] = Q.digest(Q.canonical(plan))
        return plan

    def generation(self, plan, run):
        value = self.fixture.generation(plan, run['label'])
        value['workload'].update(concurrency=run['concurrency'], extra_body=M.extra_body(plan['selection']))
        return value

    def execute(self, plan, *, mutate=None, code=0):
        called = []
        def main(argv):
            label = argv[argv.index('--label') + 1]
            run = next(r for r in plan['client']['qualification']['runs'] if r['label'] == label)
            value = self.generation(plan, run)
            if mutate:
                mutate(value, len(called))
            called.append(argv)
            Path(argv[argv.index('--output') + 1]).write_text(json.dumps(value))
            return code
        result = M.run_client(plan, main)
        return result, Q.json_bytes(Path(plan['client']['output']).read_bytes()), called

    def test_exact_six_boot_eighteen_new_call_inventory(self):
        inventories = [M.runs(s) for s in M.SELECTIONS]
        self.assertEqual([len(r) for r in inventories], [2, 2, 4, 3, 4, 3])
        self.assertEqual(sum(map(len, inventories)), 18)
        self.assertEqual([r['concurrency'] for r in inventories[0]], [8, 16])
        self.assertEqual([r['concurrency'] for r in inventories[2]], [1, 1, 8, 16])
        self.assertEqual([r['concurrency'] for r in inventories[3]], [1, 8, 16])
        self.assertEqual((18 + 3) * 16, 336)
        self.assertEqual((18 + 3) * 16 * 16, 5376)

    def test_every_cell_executes_full_distinct_corpus_and_closed_argv(self):
        for selection in M.SELECTIONS:
            with self.subTest(selection=selection):
                plan = self.plan(**selection)
                code, value, calls = self.execute(plan)
                self.assertEqual(code, 0)
                result = M.validate_output(value, plan)
                self.assertEqual(result['completed_requests'], len(calls) * 16)
                for argv, run in zip(calls, M.runs(selection)):
                    self.assertEqual(argv[:2], ['model', plan['url']])
                    for flag, expected in (('--limit', '16'), ('--max-tokens', '16'),
                                           ('--concurrency', str(run['concurrency']))):
                        self.assertEqual(argv[argv.index(flag) + 1], expected)
                    self.assertIn('--fixed-length', argv); self.assertIn('--no-score', argv)
                    self.assertNotIn('--corpus', argv)
                    self.assertEqual(json.loads(argv[argv.index('--extra-body') + 1]), M.extra_body(selection))

    def test_loaded_zero_and_active_request_scale_are_bound(self):
        self.assertNotIn('vllm_xargs', M.extra_body({'mode': 'off', 'steering': 'off'}))
        for steering, scale in (('loaded_zero', 0), ('active', 1)):
            plan = self.plan('dual', steering); run = M.runs(plan['selection'])[0]
            value = self.generation(plan, run)
            self.assertEqual(value['workload']['extra_body']['vllm_xargs']['steering_scale'], scale)
            value['workload']['extra_body']['vllm_xargs']['steering_scale'] = 4
            with self.assertRaisesRegex(RuntimeError, 'steering'): M.validate_generation(value, plan, run)

    def test_missing_duplicate_foreign_boolean_or_inexact_ids_stop_first_call(self):
        mutations = [lambda v: v['generation']['sequences'].pop(),
                     lambda v: v['generation']['sequences'][1].update(id=0),
                     lambda v: v['generation']['sequences'][1].update(id=True),
                     lambda v: v['generation']['sequences'][1].update(error='timeout'),
                     lambda v: v['generation']['sequences'][1]['output_ids'].pop(),
                     lambda v: v['generation']['sequences'][1]['prompt_ids'].__setitem__(0, True)]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                plan = self.plan('off', 'loaded_zero')
                code, result, called = self.execute(plan, mutate=lambda v, i: mutation(v))
                self.assertEqual(code, 1); self.assertEqual(len(called), 1)
                self.assertEqual(len(result['runs']), 1)
                self.assertIsNotNone(result['stopped'])
                self.assertTrue(Path(called[0][-1]).exists())
                with self.assertRaises(RuntimeError): M.validate_output(result, plan)

    def test_active_unstable_second_control_stops_before_c8(self):
        def mutation(value, index):
            if index == 1: value['generation']['sequences'][0]['output_ids'][3] = 99
        plan = self.plan('off', 'active')
        code, result, called = self.execute(plan, mutate=mutation)
        self.assertEqual(code, 1); self.assertEqual(len(called), 2)
        self.assertEqual(result['stopped']['reason'], 'INCONCLUSIVE_UNSTABLE_CONTROL')
        self.assertEqual(result['stopped']['first_divergence']['output_offset'], 3)

    def test_noop_mismatch_and_dual_active_mismatch_stop_before_later_calls(self):
        for mode, state in (('off', 'off'), ('off', 'loaded_zero'), ('dual', 'active')):
            plan = self.plan(mode, state)
            code, result, called = self.execute(plan, mutate=lambda v, i:
                v['generation']['sequences'][0]['output_ids'].__setitem__(0, 99))
            self.assertEqual(code, 1); self.assertEqual(len(called), 1)
            self.assertEqual(result['stopped']['reason'], 'TOKEN_ID_MISMATCH')

    def test_active_only_compares_with_active_reference(self):
        plan = self.plan('off', 'active')
        code, result, _ = self.execute(plan, mutate=lambda v, i:
            v['generation']['sequences'][0]['output_ids'].__setitem__(0, 99))
        self.assertEqual(code, 0); M.validate_output(result, plan)
        dual = self.plan('dual', 'active')
        dual['client']['qualification']['reference_rows'] = result['runs'][0]['value']['generation']['sequences']
        code, result, _ = self.execute(dual, mutate=lambda v, i:
            v['generation']['sequences'][0]['output_ids'].__setitem__(0, 99))
        self.assertEqual(code, 0); M.validate_output(result, dual)

    def test_cli_failure_stops_and_retains_nonzero_exit(self):
        code, result, called = self.execute(self.plan('off', 'loaded_zero'), code=2)
        self.assertEqual(code, 1); self.assertEqual(len(called), 1)
        self.assertEqual(result['runs'][0]['exit_code'], 2)

    def test_schema3_rejects_inventory_concurrency_mode_state_and_free_args(self):
        mutations = [lambda p: p['selection'].update(mode='custom'),
                     lambda p: p['selection'].update(steering='active4'),
                     lambda p: p['client']['qualification']['runs'].pop(),
                     lambda p: p['client']['qualification']['runs'][0].update(concurrency=2),
                     lambda p: p['client']['qualification']['runs'][0].update(argv=['custom']),
                     lambda p: p['client']['qualification'].update(workload=Q.WORKLOAD),
                     lambda p: p['client']['qualification']['state_identity'].update(steering='active')]
        for mutation in mutations:
            plan = self.plan(); mutation(plan)
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError): M.validate_plan(plan)

    def test_schema3_no_retry_to_green_or_incomplete_predecessor(self):
        session = SimpleNamespace(state={'attempt_results': []}, directory=self.root)
        self.assertEqual(M.next_selection(session), M.SELECTIONS[0])
        session.state['attempt_results'] = [{'outcome': 'failed', 'passed_gates': []}]
        with self.assertRaisesRegex(RuntimeError, 'retained'): M.next_selection(session)
        session.state['attempt_results'] = [{'outcome': 'passed', 'passed_gates': ['http_service'], 'attempt_id': 'a' * 32}]
        with patch.object(M, 'read_drained', return_value=({'selection': M.SELECTIONS[1]}, {}, None)):
            with self.assertRaisesRegex(RuntimeError, 'predecessor'): M.next_selection(session)

    def test_legacy_source_bytes_and_matrix_source_inventory_are_closed(self):
        self.assertEqual(hashlib.sha256((ROOT / 'scripts/q03_qualification.py').read_bytes()).hexdigest(),
                         M.STAGE1A_SOURCES['q03_qualification.py'])
        self.assertEqual(H.qualification_module().WORKLOAD, Q.WORKLOAD)
        self.assertEqual(H.qualification_module(3).WORKLOAD, M.WORKLOAD)
        import q03_window
        self.assertIn('q03_quality_matrix.py', q03_window.RuntimeOps().recovery_sources())
        self.assertIn('q03_quality_matrix.py', H.SOURCE_FILES)
        with self.assertRaises(RuntimeError): H.qualification_module(4)

    def test_historical_version_or_raw_source_inventory_drift_is_rejected(self):
        value = {'stage1a': {'source_head': 'f' * 40, 'source_tree': M.STAGE1A_TREE,
                 'capsule': {}, 'off_attempt': '/bad', 'dual_attempt': '/bad'}}
        with self.assertRaisesRegex(RuntimeError, 'publication'): M.stage1a(value)
        directory = self.root / 'old' / 'attempts' / ('f' * 32); directory.mkdir(parents=True)
        (directory.parent.parent / 'capsule.json').write_bytes(Q.canonical({'recovery_source_sha256': {}}))
        with self.assertRaisesRegex(RuntimeError, 'unsupported'): M.read_drained(directory, historical=True)


    def test_real_isolated_client_loads_only_four_pinned_modules(self):
        plan = self.plan('off', 'loaded_zero')
        toolbox = self.root / 'toolbox' / 'servebench'; toolbox.mkdir(parents=True)
        (toolbox / '__init__.py').write_text('')
        values = {run['label']: self.generation(plan, run) for run in M.runs(plan['selection'])}
        # This fake PR8 producer is explicitly a CPU test, never the production toolbox.
        source = ('import json\nfrom pathlib import Path\nVALUES=' + repr(values)
                  + '\ndef main(argv):\n label=argv[argv.index("--label")+1]\n'
                    ' Path(argv[argv.index("--output")+1]).write_text(json.dumps(VALUES[label]))\n return 0\n')
        (toolbox / '__main__.py').write_text(source)
        plan['client'].update(toolbox_path=str(toolbox.parent), toolbox_files={
            'servebench/' + path.name: Q.digest(path.read_bytes()) for path in toolbox.iterdir()},
            qualification_sources={name: Q.digest((ROOT / 'scripts' / (name + '.py')).read_bytes())
                for name in ('q03_qualification', 'q03_owner_model', 'q03_held_window', 'q03_quality_matrix')})
        plan.pop('plan_sha256'); plan['plan_sha256'] = Q.digest(Q.canonical(plan))
        path = self.root / 'isolated-plan.json'; path.write_bytes(Q.canonical(plan))
        result = subprocess.run([sys.executable, '-I', '-B', str(ROOT / 'scripts/q03_http_client.py'),
                                 '--plan', str(path), '--sha256', Q.digest(path.read_bytes())],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout); self.assertEqual(receipt['exit_code'], 0, receipt)
        value = Q.json_bytes(Path(plan['client']['output']).read_bytes())
        self.assertEqual(M.validate_output(value, plan)['completed_requests'], 64)

    def test_matrix_controller_and_legacy_dependency_ignore_poisoned_bytecode(self):
        import os
        import py_compile
        import shutil
        directory = self.root / 'source-only'; directory.mkdir()
        for name in ('q03_http_service', 'q03_quality_matrix', 'q03_qualification', 'q03_owner_model', 'q03_held_window'):
            shutil.copyfile(ROOT / 'scripts' / (name + '.py'), directory / (name + '.py'))
        for name in ('q03_quality_matrix', 'q03_qualification'):
            target = directory / (name + '.py'); raw = target.read_bytes(); stamp = target.stat().st_mtime
            poison = b"raise RuntimeError('POISONED BYTECODE')\n"
            target.write_bytes(poison + b' ' * (len(raw) - len(poison))); os.utime(target, (stamp, stamp))
            py_compile.compile(str(target), doraise=True)
            target.write_bytes(raw); os.utime(target, (stamp, stamp))
        code = ('import sys;sys.path.insert(0,sys.argv[1]);import q03_http_service as h;'
                'm=h.quality_module();print(m.WORKLOAD,m.Q.WORKLOAD)')
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(directory)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), M.WORKLOAD + ' ' + Q.WORKLOAD)


    def state_binding(self):
        profile = {'synthetic_cpu_profile': True}
        effective = {state: {'synthetic_cpu_state': state} for state in Q.owner.STEERING}
        pins = {}
        for key, value in (('profile', profile), ('effective_config', effective)):
            path = self.root / (key + '.json'); raw = Q.canonical(value); path.write_bytes(raw)
            pins[key] = {'path': str(path), 'sha256': Q.digest(raw)}
        return dict(pins, http={'owner_inputs': copy.deepcopy(pins)},
                    profile_value=profile, effective_value=effective)

    def test_decoded_state_is_checked_against_actual_pinned_owner_artifacts(self):
        binding = self.state_binding()
        for state in Q.owner.STEERING:
            identity = M.state_identity(binding, state)
            self.assertEqual(identity['effective_config_sha256'], Q.digest(Q.canonical(binding['effective_value'][state])))
        for key in ('profile_value', 'effective_value'):
            changed = copy.deepcopy(binding); changed[key]['foreign'] = True
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, 'pinned owner input'):
                M.state_identity(changed, 'active')
        changed = copy.deepcopy(binding); changed['profile']['sha256'] = '0' * 64
        with self.assertRaisesRegex(RuntimeError, 'pinned owner input'): M.state_identity(changed, 'off')
        Path(binding['effective_config']['path']).write_bytes(b'{}')
        with self.assertRaises(RuntimeError): M.state_identity(binding, 'active')

    def test_active_dual_refuses_other_effective_state_before_client(self):
        binding = self.state_binding()
        binding['http']['qualification'] = {'corpus': self.fixture.pin}
        prior = {'rows': [], 'capsule_sha256': 'a' * 64, 'plan_sha256': []}
        session = SimpleNamespace(state={'attempt_results': [{'attempt_id': str(i) * 32} for i in range(5)]},
                                  directory=self.root)
        wrong = {'selection': {'mode': 'off', 'steering': 'active'}}
        value = {'state_identity': dict(M.state_identity(binding, 'active'), effective_config_sha256='f' * 64)}
        with patch.object(M, 'validate_binding', return_value=self.fixture.corpus), \
             patch.object(M, 'stage1a', return_value=prior), \
             patch.object(M, 'read_drained', return_value=(wrong, value, None)):
            with self.assertRaisesRegex(RuntimeError, 'reference state identity'):
                M.client_plan(binding, {'mode': 'dual', 'steering': 'active'}, session)


if __name__ == '__main__': unittest.main()
