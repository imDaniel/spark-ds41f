"""Synthetic CPU retained batch, with real renderer/client/reporter/log validators.

Only identity provenance, registered fixed-profile hashes and physical daemon
observations are synthetic. Both full original clients run against localhost
fake HTTP. No GPU, model, remote command, field result or publication.
"""
import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
import q03_legacy_pair_report as P
import q03_baseline_service as B
import q03_baseline_batch as Batch
import q03_legacy_case_replay as L
import q03_remote_operation as Remote
from q03_window import RuntimeOps
import test_q03_legacy_case_replay as Legacy
import test_q03_legacy_pair_report as Report
import test_q03_baseline_service as Bridge
import test_q03_baseline_identity as Identity
import test_q03_http_independent_faults as Faults


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(P.canonical(value))


class RetainedBatchTests(Report.TrustedFixtureTests):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        self = cls()
        cls.fixture = self
        cls.addClassCleanup(self.doCleanups)
        self.legacy = Legacy.LegacyCaseTest(); self.legacy.corpus = Legacy.fixture_corpus()
        self.legacy.setUp(); self.addCleanup(self.legacy.doCleanups)
        server = self.legacy.server()
        self.temp = tempfile.TemporaryDirectory(prefix='legacy-batch-intake-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.session = SimpleNamespace(directory=root / 'session')
        self.session.directory.mkdir()
        helper = Bridge.Tests(); helper.setUp(); self.addCleanup(helper.doCleanups)
        binding = helper.binding('r6x188', 'loaded0')
        checked = Identity.checked('r6x188')
        binding['baseline_identity'] = dict(checked, image_observation={'image_labels': {}})
        binding['candidate'].update(image=checked['identity']['image'], component_head=checked['identity']['source_head'])
        binding['independent_root'] = str(root / 'independent')
        binding['http'].update(api_port=server.server_port, case_sequence=list(Batch.CASES),
            legacy_case={'case_id': 'warm4k_c1'})
        binding['http']['toolbox']['path'] = str(Legacy.TOOLBOX)
        binding['toolbox_inventory'].update(head=L.TOOLBOX_HEAD, files=L.toolbox_inventory()['files'])
        for node in binding['nodes_value']['nodes']:
            node['roce_peer_hcas'] = dict(sorted(node['roce_peer_hcas'].items(), reverse=True))
        fixed = {}
        for key, projection in (('profile', 'profile_value'), ('nodes', 'nodes_value'), ('effective_config', 'effective_value')):
            path = root / (key + '.json'); raw = json.dumps(binding[projection], indent=2).encode(); path.write_bytes(raw)
            binding[key] = fixed[key] = {'path': str(path), 'sha256': P.digest(raw)}
        binding['http']['baseline_inputs'] = fixed
        manifest = helper.manifest()
        manifest['jobs'][0]['selection'] = copy.deepcopy(binding['selection'])
        manifest.update(candidate=copy.deepcopy(binding['candidate']), http_service=copy.deepcopy(binding['http']),
            held_window={'schema_version': 1})
        manifest['preflight'].update(holder='fixture', hold_path='/unused/hold')
        self.session.manifest = manifest
        capsule = dict(window_id='a' * 32, session_dir=str(self.session.directory), manifest=manifest,
                       recovery_source_sha256=RuntimeOps().recovery_sources())
        self.session.capsule = capsule
        write(self.session.directory / 'capsule.json', capsule)
        (self.session.directory / 'capsule.sha256').write_text(P.digest(P.canonical(capsule)))
        descriptor = dict(schema_version=1, window_id='a' * 32, attempt_id='b' * 32,
            capsule_sha256=P.digest(P.canonical(capsule)), previous_receipt_sha256=None,
            candidate_updates={}, jobs=copy.deepcopy(manifest['jobs']))
        derived = P.derive_attempt_manifest(self.session, 'b' * 32, descriptor['jobs'], {})
        binding['candidate'] = derived['candidate']
        q = copy.deepcopy(self.legacy.plan['client']['legacy_case'])
        identity = SimpleNamespace(checked_identity=Identity.B.checked_identity, validate_selection=Identity.B.validate_selection,
            validate_binding=lambda *args: checked, validate_ready=Identity.B.validate_ready)
        legacy = SimpleNamespace(verify_toolbox=L.verify_toolbox, client_plan=lambda b, s: copy.deepcopy(q), validate_output=L.validate_output)
        for target, name, value in ((B, 'identity_module', lambda: identity), (B, 'legacy_module', lambda: legacy),
            (P.H, 'baseline_module', lambda: B), (B, 'PROFILE_SHA256', fixed['profile']['sha256']),
            (B, 'EFFECTIVE_SHA256', fixed['effective_config']['sha256'])):
            guard = patch.object(target, name, value); guard.start(); self.addCleanup(guard.stop)
        self.plan = P.json_bytes(P.canonical(B.http_plan(binding, 'a' * 32, 'b' * 32, binding['selection'])))
        plan, session, H = self.plan, self.session, P.H
        descriptor['command_plan_sha256'] = P.digest(P.canonical([['http_service', plan]]))
        attempt = session.directory / 'attempts' / plan['attempt_id']; attempt.mkdir(parents=True)
        self.attempt = attempt
        write(attempt / 'descriptor.json', descriptor)
        write(attempt / 'admitted.json', dict(descriptor_sha256=P.digest(P.canonical(descriptor)),
              command_plan_sha256=descriptor['command_plan_sha256']))
        _, ready = Identity.plan_and_ready('r6x188', 'loaded0')
        ready['spec_sha256'] = plan['spec_sha256']
        for row in ready['reports']:
            row.update(spec_sha256=plan['spec_sha256'], effective_config=plan['spec']['expected_effective_config'],
                       runner_config=plan['spec']['expected_effective_config'])
        ready['reports_sha256'] = P.digest(P.canonical(ready['reports']))
        owner = self
        class Ops(Faults.FakeHttpOps):
            def container(self, rank, row):
                item = super().container(rank, row); item['Image'] = plan['image']; return item
            def read_ready(self):return P.canonical(ready)
            def client_scope(self, index):return Batch.client_scope(self.plan, self.journal.directory, index)
            def run_client(self, index):
                child, directory = self.client_scope(index)
                # The fake daemon boundary stands in for remote prepare.
                Path(child['client']['output']).parent.parent.mkdir(parents=True, exist_ok=True)
                def admission(client_plan):
                    owner.legacy.plan = client_plan; owner.legacy.write_context()
                    context = owner.legacy.context
                    # Let the real producer exclusively persist this synthetic
                    # physical admission instead of replacing a retained file.
                    Path(client_plan['client']['runtime_context']).unlink()
                    for sample in context['admission']['thermal']['samples']:
                        sample['sampled_utc'] = time.time()
                    context['admission']['prefix_reset']['completed_utc'] = time.time()
                    self.journal.event('baseline-prefix-reset', receipt=context['admission']['prefix_reset'])
                    context['ranks'] = [{k: r[k] for k in ('rank', 'container_id', 'config_sha256')} for r in self.journal.state['ranks']]
                    context['ready_sha256'] = P.digest(P.canonical(ready))
                    return context
                def launch(rank, action_kind, argv, timeout):
                    # Only the remote/process boundary is synthetic. Do not
                    # reconstruct the producer's argv or either plan hash here.
                    assert rank == 0 and action_kind == 'http_client'
                    assert timeout == child['client']['timeout_seconds']
                    command = Remote.bound_command(argv)
                    epoch = dict(rank=0, action_kind='http_client', epoch=str(index + 1) * 32,
                        operation=str(index + 3) * 32, argv_sha256=P.digest(P.canonical(command)))
                    self.journal.state['epochs'].append(epoch)
                    self.journal.event('remote-intent', **epoch)
                    operation_dir = Path(plan['binding']['independent_root']) / 'remote-operations' / plan['window_id'] / epoch['operation']
                    manifest = dict(root=str(operation_dir.parent.parent), window=plan['window_id'], epoch=epoch['epoch'],
                        operation=epoch['operation'], argv=command, cwd=plan['binding']['independent_root'],
                        timeout_seconds=600, created_epoch=time.time(),
                        helper_sha256=P.digest((ROOT / 'scripts/q03_remote_operation.py').read_bytes()))
                    write(operation_dir / 'manifest.json', manifest)
                    manifest_sha = P.digest(P.canonical(manifest))
                    (operation_dir / 'manifest.sha256').write_text(manifest_sha)
                    assert owner.legacy.execute(Batch.CASES[index]) == 0
                    output = Path(child['client']['output'])
                    write(output.with_name('client-receipt.json'), {k: child[k] for k in ('window_id', 'attempt_id', 'plan_sha256')} |
                          dict(exit_code=0, error=None, output_sha256=P.digest(output.read_bytes())))
                    receipt = dict(window=plan['window_id'], operation=epoch['operation'], manifest_sha256=manifest_sha, finished_epoch=time.time(),
                        status='terminal', action_succeeded=True, group_quiescent=True, cancelled=False,
                        timed_out=False, exit_code=0, epoch_sealed=False, safe_to_overlap=False)
                    write(operation_dir / 'receipt.json', {k: receipt[k] for k in ('window', 'operation', 'manifest_sha256',
                        'group_quiescent', 'exit_code', 'cancelled', 'timed_out', 'finished_epoch')})
                    self.journal.event('remote-terminal', rank=0, operation=epoch['operation'], receipt=receipt)
                    for rank in range(4):
                        sealed = dict(receipt, epoch_sealed=True, safe_to_overlap=True)
                        self.journal.event('http-client-epoch-sealed', rank=rank, epoch=epoch['epoch'], receipt=dict(
                            window=plan['window_id'], epoch=epoch['epoch'], sealed=True, safe_to_overlap=True,
                            status='quiescent', operations=[sealed] if rank == 0 else []))
                    self.journal.state.setdefault('sealed_epochs', []).append(epoch['epoch']); self.journal.save()
                producer = SimpleNamespace(plan=self.plan, binding=self.plan['binding'],
                    journal=self.journal, check_authority=self.check_authority,
                    client_scope=self.client_scope, legacy_runtime_context=admission,
                    end=time.time() + child['client']['timeout_seconds'] + 60, mutate=launch)
                H.HttpOps.run_client(producer, index)
            def collect_result(self, index):return H.HttpOps.collect_result(self, index)
        ops = Ops(H, session, plan)
        result = H.run_http(session, plan, ops=ops)
        H.quiesce_http(session, plan['attempt_id'], ops=ops)
        write(attempt / 'http_service-result.json', result)
        terminal = dict(window_id=plan['window_id'], attempt_id=plan['attempt_id'],
            descriptor_sha256=P.digest(P.canonical(descriptor)), command_plan_sha256=descriptor['command_plan_sha256'],
            outcome='passed', failure=None, passed_gates=['http_service'], quiescence=dict(quiescent=True,
                node_count=4, attempt_id=plan['attempt_id'], phase='stopped'))
        write(attempt / 'terminal.json', terminal)
        sha = P.digest(P.canonical(terminal))
        write(session.directory / 'state.json', dict(phase='closed-stopped', original_kept_stopped=True,
            restored=False, hold_released=True, active_attempt=None, active_process=None, active_gate=None,
            attempt_results=[terminal], attempt_receipt_sha256={plan['attempt_id']: sha}, last_attempt_receipt_sha256=sha))
        closure = Report.ClosureTests(); closure.setUp(); self.addCleanup(closure.doCleanups)
        ended = P.read_json(Path(owner.legacy.plan['client']['output']).with_name('legacy-evidence.json'))['ended_utc']
        closure.observed['now'] = datetime.fromtimestamp(ended + 1, tz=timezone.utc).isoformat()
        write(session.directory / ('preflight-restore_drained-' + 'a' * 32 + '.json'), closure.observed)
        cls.result = P.read_drained(attempt)

    def setUp(self):
        self.f = self.fixture
        self.attempt, self.plan = self.f.attempt, self.f.plan

    def mutate_file(self, path, mutate):
        original = path.read_bytes()
        value = P.json_bytes(original); mutate(value); write(path, value)
        try:
            with self.assertRaises((RuntimeError, ValueError, KeyError, OSError, TypeError)):
                P.read_drained(self.attempt)
        finally:path.write_bytes(original)

    def event_path(self, kind, index=0):
        return [p for p in sorted((self.attempt / 'http').glob('[0-9]*.json'))
                if P.read_json(p).get('kind') == kind][index]

    def test_complete_canonical_non_sorted_original_reader_is_readonly(self):
        before = {str(p): P.digest(p.read_bytes()) for p in self.f.session.directory.rglob('*') if p.is_file()}
        with patch.object(subprocess, 'run', side_effect=AssertionError('no process')), \
             patch.object(socket, 'create_connection', side_effect=AssertionError('no network')):
            result = P.read_drained(self.attempt)
        self.assertEqual(result['scope'], 'FIXED_C1_THEN_C4_SAME_BOOT')
        self.assertEqual([r['plan']['client']['legacy_case']['case_id'] for r in result['cases']], list(Batch.CASES))
        self.assertEqual(result['cases'][0]['context']['ranks'], result['cases'][1]['context']['ranks'])
        original = P.H.owner.pinned_json(self.plan['binding']['nodes'])
        self.assertNotEqual(list(original['nodes'][0]['roce_peer_hcas']), list(self.plan['binding']['nodes_value']['nodes'][0]['roce_peer_hcas']))
        self.assertEqual(before, {str(p): P.digest(p.read_bytes()) for p in self.f.session.directory.rglob('*') if p.is_file()})

    def test_real_producer_preserves_distinct_file_and_semantic_plan_pins(self):
        state = P.read_json(self.attempt / 'http/state.json')
        epochs = [e for e in state['epochs'] if e['action_kind'] == 'http_client']
        for index, case in enumerate(self.result['cases']):
            child = case['plan']
            scope = self.attempt / 'http' / ('case-' + child['client']['legacy_case']['case_id'])
            raw = (scope / 'client-plan.json').read_bytes()
            semantic = copy.deepcopy(child); semantic.pop('plan_sha256')
            self.assertEqual(child['plan_sha256'], P.digest(P.canonical(semantic)))
            self.assertNotEqual(P.digest(raw), child['plan_sha256'])
            root = Path(self.plan['binding']['independent_root']) / 'remote-operations' / self.plan['window_id']
            manifest = P.read_json(root / epochs[index]['operation'] / 'manifest.json')
            command = manifest['argv']
            self.assertEqual(command[command.index('--sha256') + 1], P.digest(raw))
            self.assertEqual(epochs[index]['argv_sha256'], P.digest(P.canonical(command)))
            receipt = P.read_json(Path(child['client']['output']).with_name('client-receipt.json'))
            self.assertEqual(receipt['plan_sha256'], child['plan_sha256'])

    def test_same_json_with_different_retained_bytes_is_not_normalized(self):
        for case in self.result['cases']:
            case_id = case['plan']['client']['legacy_case']['case_id']
            path = self.attempt / 'http' / ('case-' + case_id) / 'client-plan.json'
            raw = path.read_bytes()
            try:
                path.write_bytes(raw + b'\n')
                self.assertEqual(P.json_bytes(path.read_bytes()), P.json_bytes(raw))
                with self.assertRaisesRegex(RuntimeError, 'retained batch client plan differs'):
                    P.read_drained(self.attempt)
            finally:path.write_bytes(raw)

    @contextmanager
    def rebind_command(self, index, pin_value, *, bind_epoch=True):
        # Deliberately coherent synthetic tampering: do not rely on a stale
        # manifest sidecar/receipt to reject the wrong command pin.
        originals = {}
        def save(path, value):
            originals.setdefault(path, path.read_bytes()); write(path, value)
        try:
            state_path = self.attempt / 'http/state.json'
            state = P.read_json(state_path)
            epoch = [e for e in state['epochs'] if e['action_kind'] == 'http_client'][index]
            root = Path(self.plan['binding']['independent_root']) / 'remote-operations' / self.plan['window_id'] / epoch['operation']
            manifest = P.read_json(root / 'manifest.json')
            command = manifest['argv']; command[command.index('--sha256') + 1] = pin_value
            save(root / 'manifest.json', manifest)
            sha = P.digest(P.canonical(manifest))
            sidecar = root / 'manifest.sha256'; originals[sidecar] = sidecar.read_bytes(); sidecar.write_text(sha)
            receipt = P.read_json(root / 'receipt.json'); receipt['manifest_sha256'] = sha
            save(root / 'receipt.json', receipt)
            if bind_epoch:
                epoch['argv_sha256'] = P.digest(P.canonical(command)); save(state_path, state)
                path = self.event_path('remote-intent', index)
                event = P.read_json(path); event['argv_sha256'] = epoch['argv_sha256']; save(path, event)
            path = self.event_path('remote-terminal', index)
            event = P.read_json(path); event['receipt']['manifest_sha256'] = sha; save(path, event)
            path = self.event_path('http-client-epoch-sealed', index * 4)
            event = P.read_json(path); event['receipt']['operations'][0]['manifest_sha256'] = sha; save(path, event)
            yield
        finally:
            for path, raw in originals.items():path.write_bytes(raw)

    def test_rebound_embedded_or_wrong_file_pin_still_rejects(self):
        for index, case in enumerate(self.result['cases']):
            for pin in (case['plan']['plan_sha256'], 'f' * 64):
                with self.subTest(index=index, pin=pin), self.rebind_command(index, pin):
                    with self.assertRaisesRegex(RuntimeError, 'batch client command/epoch binding differs'):
                        P.read_drained(self.attempt)
            with self.rebind_command(index, case['plan']['plan_sha256'], bind_epoch=False):
                with self.assertRaisesRegex(RuntimeError, 'batch operation manifest does not bind'):
                    P.read_drained(self.attempt)

    def test_epoch_argv_hash_remains_required(self):
        for index in (0, 1):
            def change(state):
                clients = [e for e in state['epochs'] if e['action_kind'] == 'http_client']
                clients[index]['argv_sha256'] = 'f' * 64
            self.mutate_file(self.attempt / 'http/state.json', change)

    def test_whole_batch_cannot_pass_one_case_or_wrong_parent_result(self):
        for name in ('http/result.json', 'http_service-result.json'):
            path = self.attempt / name
            for mutation in (lambda x: x['cases'].pop(), lambda x: x['cases'].reverse(),
                             lambda x: x['cases'].append(x['cases'][0]), lambda x: x.update(result='failed'),
                             lambda x: x['cases'][1].update(result=x['cases'][0]['result'])):
                with self.subTest(name=name, mutation=mutation):self.mutate_file(path, mutation)

    def test_case_plans_contexts_and_results_cannot_swap_or_change(self):
        directory = self.attempt / 'http/case-warm4k_c4'
        changes = [('client-plan.json', lambda x: x['batch_case'].update(index=0)),
            ('client-plan.json', lambda x: x['selection'].update(steering='loaded1')),
            ('client-plan.json', lambda x: x['client'].update(output=self.result['cases'][0]['plan']['client']['output'])),
            ('baseline-runtime-context.json', lambda x: x['ranks'][3].update(container_id='f' * 64)),
            ('result.json', lambda x: x.update(case_id='warm4k_c1')),
            ('result.json', lambda x: x.update(result='failed'))]
        for name, mutate in changes:
            with self.subTest(name=name):self.mutate_file(directory / name, mutate)

    def test_missing_extra_alias_case_directories_fail(self):
        journal = self.attempt / 'http'
        extra = journal / 'case-extra'; extra.mkdir()
        try:
            with self.assertRaises(RuntimeError):P.read_drained(self.attempt)
        finally:extra.rmdir()
        path = journal / 'case-warm4k_c4'; renamed = journal / 'saved-case'; path.rename(renamed)
        try:
            with self.assertRaises(RuntimeError):P.read_drained(self.attempt)
            path.symlink_to(renamed, target_is_directory=True)
            with self.assertRaises(RuntimeError):P.read_drained(self.attempt)
            path.unlink()
        finally:renamed.rename(path)

    def test_case_order_missing_pins_and_predecessor_links_fail(self):
        changes = [('baseline-case-intent', 1, lambda x: x.update(case_index=0)),
            ('baseline-case-intent', 1, lambda x: x.update(previous_result_sha256='f' * 64)),
            ('baseline-case-complete', 1, lambda x: x.update(case_id='warm4k_c1')),
            ('baseline-runtime-context-pinned', 1, lambda x: x.update(context_sha256='f' * 64)),
            ('baseline-runtime-context-pinned', 0, lambda x: x.update(case_index=False))]
        for kind, index, mutate in changes:
            with self.subTest(kind=kind):self.mutate_file(self.event_path(kind, index), mutate)
        path = self.event_path('baseline-case-complete', 1); raw = path.read_bytes(); path.unlink()
        try:
            with self.assertRaises(RuntimeError):P.read_drained(self.attempt)
        finally:path.write_bytes(raw)

    def test_failed_unknown_cancelled_unsealed_and_wrong_command_epochs_fail(self):
        terminal = self.event_path('remote-terminal', 1)
        for mutation in (lambda x: x['receipt'].update(status='uncertain'),
                         lambda x: x['receipt'].update(cancelled=True),
                         lambda x: x['receipt'].update(timed_out=True),
                         lambda x: x['receipt'].update(action_succeeded=False),
                         lambda x: x['receipt'].update(exit_code=False)):
            self.mutate_file(terminal, mutation)
        seal = self.event_path('http-client-epoch-sealed', 7)
        for mutation in (lambda x: x['receipt'].update(sealed=False),
                         lambda x: x['receipt'].update(safe_to_overlap=False),
                         lambda x: x.update(rank=0), lambda x: x.update(epoch='f' * 32)):
            self.mutate_file(seal, mutation)
        state = self.attempt / 'http/state.json'
        for mutation in (lambda x: x['epochs'][1].update(argv_sha256='f' * 64),
                         lambda x: x['epochs'].append(copy.deepcopy(x['epochs'][0])),
                         lambda x: x['sealed_epochs'].pop()):
            self.mutate_file(state, mutation)

    @contextmanager
    def rebind_admission(self, index, mutate, *, reset_event=True):
        """Rehash every dependent artifact so rejection tests chronology itself."""
        run = self.result['cases'][index]
        child = run['plan']; output = Path(child['client']['output'])
        scope = self.attempt / 'http' / ('case-' + Batch.CASES[index])
        originals = {}
        def save(path, value):
            originals.setdefault(path, path.read_bytes()); write(path, value)
        try:
            context = P.read_json(scope / 'baseline-runtime-context.json'); mutate(context)
            context_sha = P.digest(P.canonical(context))
            for path in (scope / 'baseline-runtime-context.json', Path(child['client']['runtime_context'])):
                save(path, context)
            evidence = P.read_json(output.with_name('legacy-evidence.json')); evidence['context_sha256'] = context_sha
            save(output.with_name('legacy-evidence.json'), evidence)
            value = P.read_json(output)
            value.update(runtime_context_sha256=context_sha, evidence_sha256=P.digest(P.canonical(evidence)))
            save(output, value)
            client_receipt = P.read_json(output.with_name('client-receipt.json'))
            client_receipt['output_sha256'] = P.digest(P.canonical(value))
            save(output.with_name('client-receipt.json'), client_receipt)
            result = P.read_json(scope / 'result.json'); result['result_sha256'] = P.digest(P.canonical(value))
            save(scope / 'result.json', result)
            result_sha = P.digest(P.canonical(result))
            for path in (self.attempt / 'http/result.json', self.attempt / 'http_service-result.json'):
                parent = P.read_json(path); parent['cases'][index].update(result=result, result_sha256=result_sha)
                save(path, parent)
            pin_path = self.event_path('baseline-runtime-context-pinned', index)
            pin = P.read_json(pin_path); pin['context_sha256'] = context_sha; save(pin_path, pin)
            complete_path = self.event_path('baseline-case-complete', index)
            complete = P.read_json(complete_path); complete['result_sha256'] = result_sha; save(complete_path, complete)
            if index == 0:
                path = self.event_path('baseline-case-intent', 1)
                intent = P.read_json(path); intent['previous_result_sha256'] = result_sha; save(path, intent)
            if reset_event:
                path = self.event_path('baseline-prefix-reset', index)
                event = P.read_json(path); event['receipt'] = context['admission']['prefix_reset']; save(path, event)
            yield
        finally:
            for path, raw in originals.items():path.write_bytes(raw)

    def test_exactly_one_reset_between_case_intent_and_pin(self):
        for index in (0, 1):
            path = self.event_path('baseline-prefix-reset', index)
            self.mutate_file(path, lambda x: x.update(kind='missing-reset'))
            self.mutate_file(path, lambda x: x['receipt'].update(plan_sha256='f' * 64))
            self.mutate_file(path, lambda x: x['receipt'].update(status=True))
            # Preserve a complete journal while moving the event outside its
            # case admission interval, both before intent and after context pin.
            for kind in ('baseline-case-intent', 'baseline-runtime-context-pinned'):
                other = self.event_path(kind, index)
                raw, other_raw = path.read_bytes(), other.read_bytes()
                a, b = P.json_bytes(raw), P.json_bytes(other_raw)
                a['sequence'], b['sequence'] = b['sequence'], a['sequence']
                write(path, b); write(other, a)
                try:
                    with self.assertRaises(RuntimeError):P.read_drained(self.attempt)
                finally:path.write_bytes(raw); other.write_bytes(other_raw)
        state_path = self.attempt / 'http/state.json'; state_raw = state_path.read_bytes()
        state = P.json_bytes(state_raw); n = state['sequence']
        last_path = self.attempt / 'http' / f'{n:06d}.json'
        extra_path = self.attempt / 'http' / f'{n + 1:06d}.json'
        last_raw = last_path.read_bytes(); last = P.json_bytes(last_raw)
        duplicate = P.read_json(self.event_path('baseline-prefix-reset', 1)); duplicate['sequence'] = n
        last['sequence'] = n + 1
        write(last_path, duplicate); write(extra_path, last); state['sequence'] = n + 1; write(state_path, state)
        try:
            with self.assertRaisesRegex(RuntimeError, 'missing or extra batch case event'):P.read_drained(self.attempt)
        finally:
            state_path.write_bytes(state_raw); last_path.write_bytes(last_raw); extra_path.unlink()

    def test_rehashed_admission_cannot_precede_prior_completion_or_follow_launch(self):
        root = Path(self.plan['binding']['independent_root']) / 'remote-operations' / self.plan['window_id']
        finished = P.read_json(root / ('3' * 32) / 'receipt.json')['finished_epoch']
        created = P.read_json(root / ('4' * 32) / 'manifest.json')['created_epoch']
        evidence = self.result['cases'][1]['evidence']
        def stale(context):
            for sample in context['admission']['thermal']['samples']:sample['sampled_utc'] = finished - .02
            context['admission']['prefix_reset']['completed_utc'] = finished - .01
        def stale_thermal_only(context):
            context['admission']['thermal']['samples'][0]['sampled_utc'] = finished - .01
        def late_reset(context):
            context['admission']['prefix_reset']['completed_utc'] = (created + evidence['started_utc']) / 2
        for mutate in (stale, stale_thermal_only, late_reset):
            with self.subTest(mutate=mutate), self.rebind_admission(1, mutate):
                with self.assertRaisesRegex(RuntimeError, 'final thermal/reset admission'):P.read_drained(self.attempt)
        # Changing and rehashing the context does not permit a different retained
        # controller reset receipt, even when the timestamps remain valid.
        def different_receipt(context):
            context['admission']['prefix_reset']['response_utf8'] = '{"success": true}'
            context['admission']['prefix_reset']['response_sha256'] = P.digest(b'{"success": true}')
        with self.rebind_admission(1, different_receipt, reset_event=False):
            with self.assertRaisesRegex(RuntimeError, 'reset event'):P.read_drained(self.attempt)

    def test_rank_zero_seal_must_match_full_immutable_terminal_receipt(self):
        path = self.event_path('http-client-epoch-sealed', 4)
        for mutate in (lambda x: x['receipt']['operations'][0].update(finished_epoch=0),
                       lambda x: x['receipt']['operations'][0].update(manifest_sha256='f' * 64)):
            with self.subTest(mutate=mutate):self.mutate_file(path, mutate)

    def test_operation_manifests_receipts_and_parent_failure_fail_closed(self):
        root = Path(self.plan['binding']['independent_root']) / 'remote-operations' / self.plan['window_id'] / ('4' * 32)
        for name, mutate in (('manifest.json', lambda x: x['argv'].append('--unapproved')),
            ('manifest.json', lambda x: x.update(epoch='f' * 32)),
            ('receipt.json', lambda x: x.update(cancelled=True)),
            ('receipt.json', lambda x: x.update(finished_epoch=0))):
            with self.subTest(name=name):self.mutate_file(root / name, mutate)
        self.mutate_file(self.event_path('baseline-case-complete', 1), lambda x: x.update(kind='failure'))
        self.mutate_file(self.event_path('phase', 7), lambda x: x.update(phase='client_running'))
        state = self.f.session.directory / 'state.json'
        for mutate in (lambda x: x.update(phase='held-ready'), lambda x: x.update(hold_released=False),
                       lambda x: x['attempt_results'][0].update(outcome='cancelled')):
            self.mutate_file(state, mutate)
        case = self.result['cases'][1]
        self.mutate_file(Path(case['plan']['client']['output']).with_name('client-receipt.json'),
                         lambda x: x.update(exit_code=1))

    def test_old_inventory_and_unknown_missing_extra_sources_fail_without_relaxing(self):
        path = self.f.session.directory / 'capsule.json'
        for mutation in (lambda x: x['recovery_source_sha256'].pop('q03_baseline_batch.py'),
                         lambda x: x['recovery_source_sha256'].pop('q03_http_client.py'),
                         lambda x: x['recovery_source_sha256'].update(extra='f' * 64),
                         lambda x: x['recovery_source_sha256'].update(q03_baseline_batch_py='f' * 64)):
            self.mutate_file(path, mutation)

    def test_explicit_old_inventory_diagnostic_and_current_hash_guard(self):
        directory = self.f.session.directory
        path, sha_path = directory / 'capsule.json', directory / 'capsule.sha256'
        raw, sha = path.read_bytes(), sha_path.read_bytes()
        try:
            value = P.json_bytes(raw); value['recovery_source_sha256'].pop('q03_baseline_batch.py')
            write(path, value); sha_path.write_text(P.digest(path.read_bytes()))
            with self.assertRaisesRegex(RuntimeError, 'owned-http-single/1.*matching archived'):
                P._closed_session(directory)
            value = P.json_bytes(raw); value['recovery_source_sha256']['q03_baseline_batch.py'] = 'f' * 64
            write(path, value); sha_path.write_text(P.digest(path.read_bytes()))
            with self.assertRaisesRegex(RuntimeError, 'pinned recovery dependencies changed'):
                P._closed_session(directory)
        finally:
            path.write_bytes(raw); sha_path.write_bytes(sha)

    def test_single_case_batch_mix_rejected_and_two_cases_are_one_boot_pair(self):
        batch = self.result
        other = copy.deepcopy(batch)
        other['cases'] = [Report.numerical_run('native197-off', offset=4), Report.numerical_run('native197-off', offset=4)]
        left = copy.deepcopy(batch)
        left['cases'] = [Report.numerical_run('r6x188'), Report.numerical_run('r6x188')]
        for result in (left, other):result['cases'][1]['plan']['client']['legacy_case']['case_id'] = 'warm4k_c4'
        with patch.object(P, 'read_drained', side_effect=[left, other]):
            value = P.compare_pair('/a', '/b')
        self.assertEqual(value['independent_boot_pairs'], 1)
        self.assertEqual(value['same_boot_cases'], 2)
        self.assertIsNone(value['paired_boot_ci95'])
        with patch.object(P, 'read_drained', side_effect=[left, other['cases'][0]]), self.assertRaises(RuntimeError):
            P.compare_pair('/a', '/b')


if __name__ == '__main__':unittest.main()
