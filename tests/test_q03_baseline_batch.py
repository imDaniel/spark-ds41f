"""CPU-only fixed-state batch faults; no model, network or device execution."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import q03_baseline_batch as B
import q03_baseline_service as service
import q03_http_service as H
import q03_legacy_case_replay as L
import test_q03_baseline_http_hooks as hooks
import test_q03_http_independent_faults as faults


def rehash(plan):
    plan.pop('plan_sha256', None)
    plan['plan_sha256'] = B.digest(B.canonical(plan))


class SourcePinTests(unittest.TestCase):
    def test_batch_loader_ignores_shadow_import_and_rejects_changed_source(self):
        path = ROOT / 'scripts' / 'q03_baseline_batch.py'
        raw = path.read_bytes()
        self.assertEqual(service.BATCH_SOURCE_SHA256, B.digest(raw))
        with mock.patch.dict(sys.modules, q03_baseline_batch=SimpleNamespace(CASES=('foreign',))), \
             mock.patch.object(service, 'read_owned_bytes', return_value=raw):
            self.assertEqual(service.batch_module().CASES, B.CASES)
        with mock.patch.object(service, 'read_owned_bytes', return_value=raw + b'\n# changed'), \
             self.assertRaisesRegex(RuntimeError, 'source pin differs'):
            service.batch_module()


class ScopedClientFaults(unittest.TestCase):
    def setUp(self):
        hooks.Hooks.setUp(self)
        H.baseline_module.return_value.batch_module = lambda: B
        self.plan['binding'] = self.ops.binding
        self.ops.binding['http'].update(case_sequence=list(B.CASES),
            legacy_case={'case_id': B.CASES[0]})
        self.ops.binding['selection'] = copy.deepcopy(self.plan['selection'])
        self.plan['client']['legacy_case'] = dict(case_id=B.CASES[0], selection=self.plan['selection'])
        rehash(self.plan)

    def run_both(self):
        with mock.patch.object(H.time, 'monotonic', side_effect=[10, 12, 20, 22]):
            self.ops.run_client(0)
            self.ops.run_client(1)

    def retained(self, index):
        plan, directory = self.ops.client_scope(index)
        raw = H.canonical({'synthetic_case': index})
        path = Path(plan['client']['output'])
        path.write_bytes(raw)
        receipt = {k: plan[k] for k in ('window_id', 'attempt_id', 'plan_sha256')}
        receipt.update(exit_code=0, error=None, output_sha256=H.digest(raw))
        path.with_name('client-receipt.json').write_bytes(H.canonical(receipt))
        return plan, directory

    def test_fixed_typed_sequence_rejects_skips_reorder_duplicates_and_custom_cases(self):
        for value in ([], [B.CASES[0]], list(reversed(B.CASES)), [B.CASES[0]] * 2,
                      list(B.CASES) + [B.CASES[0]], 'warm4k_c1,warm4k_c4', tuple(B.CASES),
                      [B.CASES[0], 'custom']):
            http = copy.deepcopy(self.ops.binding['http']); http['case_sequence'] = value
            with self.subTest(value=value), self.assertRaises(RuntimeError): B.validate_sequence(http)
        for value in (True, False, 0.0, -1, 2, None):
            with self.subTest(value=value), self.assertRaises(RuntimeError): self.ops.client_scope(value)

    def test_projection_binds_parent_state_and_disjoint_paths_without_mutating_boot(self):
        original = copy.deepcopy(self.plan)
        a, ad = self.ops.client_scope(0); b, bd = self.ops.client_scope(1)
        self.assertEqual(self.plan, original)
        self.assertEqual(a['selection'], b['selection'])
        self.assertEqual(a['spec'] if 'spec' in a else None, b.get('spec'))
        self.assertNotEqual(a['plan_sha256'], b['plan_sha256'])
        self.assertNotEqual(ad, bd)
        self.assertNotEqual(a['client']['output'], b['client']['output'])
        self.assertEqual(a['batch_case']['parent_plan_sha256'], original['plan_sha256'])
        for mutation in ('parent_hash', 'steering', 'arm'):
            plan = copy.deepcopy(original)
            if mutation == 'parent_hash': plan['plan_sha256'] = 'f' * 64
            else:
                plan['selection'][mutation] = 'loaded1' if mutation == 'steering' else 'native197-off'
                rehash(plan)
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError):
                B.client_scope(plan, self.ops.journal.directory, 0)

    def test_each_case_keeps_original_thermal_reset_scope_and_full_client_cap(self):
        self.run_both()
        self.assertEqual(self.ops.transport.call_count, 8)
        self.assertEqual(self.urlopen.call_count, 2)
        self.assertEqual(self.ops.mutate.call_count, 2)
        contexts = []
        for index, call in enumerate(self.ops.mutate.call_args_list):
            plan, directory = self.ops.client_scope(index)
            self.assertEqual(call.args[:2], (0, 'http_client'))
            self.assertEqual(call.args[3], 600)
            self.assertEqual(call.args[2][-3:], [str(directory / 'client-plan.json'), '--sha256',
                H.digest(H.canonical(plan))])
            context = json.loads((directory / 'baseline-runtime-context.json').read_bytes())
            self.assertEqual(context['plan_sha256'], plan['plan_sha256'])
            self.assertEqual(context['ranks'], self.ranks)
            self.assertEqual(context['admission']['prefix_reset']['plan_sha256'], plan['plan_sha256'])
            contexts.append(context)
        self.assertNotEqual(contexts[0], contexts[1])
        self.assertFalse((self.ops.journal.directory / 'client-plan.json').exists())

    def test_second_case_cannot_reuse_first_receipt_context_or_pin(self):
        self.run_both()
        a, ad = self.retained(0); b, bd = self.retained(1)
        with mock.patch.object(L, 'validate_output', return_value={'status': 'synthetic validated case'}):
            for index in (0, 1): self.assertEqual(self.ops.collect_result(index)['result'], 'passed')
            receipt_path = Path(b['client']['output']).with_name('client-receipt.json')
            original = receipt_path.read_bytes()
            receipt_path.write_bytes(Path(a['client']['output']).with_name('client-receipt.json').read_bytes())
            with self.assertRaisesRegex(RuntimeError, 'receipt failed or differs'): self.ops.collect_result(1)
            receipt_path.write_bytes(original)
            context_path = bd / 'baseline-runtime-context.json'
            context_path.write_bytes((ad / 'baseline-runtime-context.json').read_bytes())
            Path(b['client']['runtime_context']).write_bytes(context_path.read_bytes())
            with self.assertRaisesRegex(RuntimeError, 'durable controller event'): self.ops.collect_result(1)

    def test_duplicate_scope_and_short_remaining_budget_never_launch(self):
        self.ops.run_client(0)
        self.ops.mutate.reset_mock()
        with self.assertRaises(FileExistsError): self.ops.run_client(0)
        self.ops.mutate.assert_not_called()
        self.ops.end = 1599
        with mock.patch.object(H.time, 'monotonic', side_effect=[20, 22]):
            with self.assertRaisesRegex(RuntimeError, 'remaining original client budget'): self.ops.run_client(1)
        self.ops.mutate.assert_not_called()

    def test_recovery_seals_either_exact_case_command_and_rejects_unknown(self):
        self.run_both()
        self.ops.helper_call = mock.Mock(return_value={'sealed': True, 'safe_to_overlap': True})
        for index, call in enumerate(self.ops.mutate.call_args_list):
            epoch = str(index + 1) * 32
            row = dict(epoch=epoch, rank=0, action_kind='http_client', operation='d' * 32,
                       argv_sha256=H.digest(H.canonical(call.args[2])))
            self.ops.journal.state['epochs'] = [row]
            self.ops.seal_epoch(epoch)
            self.assertIn(epoch, self.ops.journal.state['sealed_epochs'])
        self.assertEqual(self.ops.helper_call.call_count, 8)
        row['argv_sha256'] = 'f' * 64
        with self.assertRaisesRegex(RuntimeError, 'fixed admitted command'): self.ops.seal_epoch(epoch)
        self.assertEqual(self.ops.helper_call.call_count, 8)

    def test_unknown_second_client_descendants_cannot_be_marked_sealed(self):
        self.run_both()
        call = self.ops.mutate.call_args_list[1]
        epoch, operation = 'e' * 32, 'd' * 32
        self.ops.journal.state['epochs'] = [dict(epoch=epoch, operation=operation, rank=0,
            action_kind='http_client', argv_sha256=H.digest(H.canonical(call.args[2])))]
        self.ops.helper_call = mock.Mock(return_value={'sealed': True, 'safe_to_overlap': False,
            'operations': [{'operation': operation, 'status': 'uncertain'}]})
        with self.assertRaisesRegex(RuntimeError, 'descendants UNKNOWN'): self.ops.seal_epoch(epoch)
        self.assertNotIn(epoch, self.ops.journal.state.get('sealed_epochs', []))


class BatchLifecycleFaults(faults.NoExternalOperations):
    def setUp(self):
        super().setUp()
        patch = mock.patch.object(self.http, 'read_owned_bytes', lambda path: Path(path).read_bytes())
        patch.start(); self.addCleanup(patch.stop)
        self.counter = 0

    def scenario(self):
        session, plan, ops = faults.ActualHttpLifecycleFaults.scenario(self)
        plan['selection'] = {'arm': 'native197-off', 'steering': 'unloaded'}
        plan['image'], plan['source_head'] = self.http.IMAGE, self.http.SOURCE_HEAD
        plan['binding']['selection'] = copy.deepcopy(plan['selection'])
        plan['binding']['http'].update(schema_version=6, case_sequence=list(B.CASES),
                                      legacy_case={'case_id': B.CASES[0]})
        plan['client'] = dict(output=str(session.directory / 'results' / 'legacy-case.json'),
            legacy_case=dict(case_id=B.CASES[0], selection=copy.deepcopy(plan['selection'])))
        rehash(plan)
        attempt = session.directory / 'attempts' / plan['attempt_id']
        descriptor = json.loads((attempt / 'descriptor.json').read_bytes())
        descriptor['command_plan_sha256'] = H.digest(H.canonical([['http_service', plan]]))
        (attempt / 'descriptor.json').write_bytes(H.canonical(descriptor))
        (attempt / 'admitted.json').write_bytes(H.canonical(dict(
            descriptor_sha256=H.digest(H.canonical(descriptor)),
            command_plan_sha256=descriptor['command_plan_sha256'])))
        ops.read_ready = lambda: H.canonical({'reports': ['same four boot observations']})
        def run_client(index):
            ops.events.append(('client', index))
            _, directory = B.client_scope(plan, ops.journal.directory, index)
            directory.mkdir()
            if getattr(ops, 'fail_case', None) == index: raise RuntimeError('injected client failure')
            if getattr(ops, 'unknown_case', None) == index: ops.unknown_epoch = True
        def collect(index):
            ops.events.append(('collect', index))
            return {'result': 'passed', 'case_id': B.CASES[index], 'synthetic': True}
        ops.run_client, ops.collect_result = run_client, collect
        patch = mock.patch.object(self.http, 'baseline_module', return_value=SimpleNamespace(
            batch_module=lambda: B, identity_module=lambda: __import__('q03_baseline_identity')))
        patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(self.http, 'validate_ready', side_effect=lambda raw, plan: json.loads(raw))
        patch.start(); self.addCleanup(patch.stop)
        return session, plan, ops

    def drain(self, session, plan, ops):
        return faults.ActualHttpLifecycleFaults.drain(self, session, plan, ops)

    def test_two_cases_have_only_one_all_four_start_and_existing_final_drain(self):
        session, plan, ops = self.scenario()
        result = self.http.run_http(session, plan, ops=ops)
        self.assertEqual([r['case_id'] for r in result['cases']], list(B.CASES))
        self.assertEqual([r for r in ops.events if r[0] == 'client'], [('client', 0), ('client', 1)])
        self.assertEqual([r for r in ops.events if r[0] == 'start'], [('start', i) for i in range(4)])
        self.assertFalse(any(r[0] == 'stop' for r in ops.events))
        self.assertTrue(self.drain(session, plan, ops)['daemon_quiescent'])
        self.assertEqual([r for r in ops.events if r[0] == 'stop'], [('stop', i) for i in range(4)])
        self.assertEqual(ops.items, {})

    def test_first_case_failure_never_starts_second_and_still_uses_final_drain(self):
        session, plan, ops = self.scenario(); ops.fail_case = 0
        with self.assertRaisesRegex(RuntimeError, 'client failure'): self.http.run_http(session, plan, ops=ops)
        self.assertEqual([r for r in ops.events if r[0] == 'client'], [('client', 0)])
        self.assertTrue(self.drain(session, plan, ops)['daemon_quiescent'])

    def test_first_case_bad_result_cannot_advance_even_after_client_exit(self):
        session, plan, ops = self.scenario()
        ops.collect_result = lambda index: {'result': 'failed'}
        with self.assertRaisesRegex(RuntimeError, 'case did not pass'):
            self.http.run_http(session, plan, ops=ops)
        self.assertEqual([r for r in ops.events if r[0] == 'client'], [('client', 0)])
        self.assertTrue(self.drain(session, plan, ops)['daemon_quiescent'])

    def test_request_or_cancel_after_first_case_prevents_second(self):
        for reason in ('cluster requested', 'operator cancel'):
            session, plan, ops = self.scenario()
            ops.authority_failure, ops.abort_after = RuntimeError(reason), ('collect', 0)
            with self.subTest(reason=reason), self.assertRaisesRegex(RuntimeError, reason):
                self.http.run_http(session, plan, ops=ops)
            self.assertEqual([r for r in ops.events if r[0] == 'client'], [('client', 0)])
            self.assertTrue((session.directory / 'attempts' / plan['attempt_id'] / 'http' /
                'case-warm4k_c1' / 'result.json').exists())
            self.assertTrue(self.drain(session, plan, ops)['daemon_quiescent'])

    def test_second_case_unknown_retains_first_without_pass_or_release(self):
        session, plan, ops = self.scenario(); ops.unknown_case = 1
        with self.assertRaisesRegex(RuntimeError, 'UNKNOWN'): self.http.run_http(session, plan, ops=ops)
        directory = session.directory / 'attempts' / plan['attempt_id'] / 'http'
        self.assertTrue((directory / 'case-warm4k_c1' / 'result.json').exists())
        self.assertFalse((directory / 'case-warm4k_c4' / 'result.json').exists())
        self.assertFalse((directory / 'result.json').exists())
        with self.assertRaisesRegex(RuntimeError, 'UNKNOWN'): self.drain(session, plan, ops)
        self.assertTrue(ops.items)


if __name__ == '__main__':
    unittest.main()
