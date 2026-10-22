"""CPU-only finite qualification, exact token maps and immutable receipt faults."""
import copy
import hashlib
import json
import stat
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import q03_qualification as Q
import q03_http_service as H


class QualificationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        identity = {key: 'a' * 64 for key in Q.IDENTITY_FIELDS}
        identity.update(model_repository='example/model', model_revision='b' * 40,
                        tokenizer_revision='c' * 40, tokenizer_assets_sha256={'tokenizer.json': 'd' * 64},
                        transformers_version='TEST-ONLY', tokenizers_version='TEST-ONLY')
        identity.update(encoding_source_sha256=Q.NATIVE_SOURCES['deepseek_v41_encoding.py'],
                        chat_template_sha256=Q.TEMPLATE_SHA, special_tokens_sha256='d' * 64)
        artifacts = {'model_root': str(self.root / 'model'),
                     'weights_manifest': {'path': str(self.root / 'weights.json'), 'sha256': 'a' * 64},
                     'runtime_metadata': {'path': str(self.root / 'runtime.json'), 'sha256': 'e' * 64},
                     'native_sources': {name: {'path': str(self.root / name), 'sha256': sha}
                                        for name, sha in Q.NATIVE_SOURCES.items()},
                     'special_tokens_file': 'tokenizer.json'}
        self.export = {'schema_version': 1, 'tokenizer_mode': 'deepseek_v41', 'identity': identity, 'artifacts': artifacts,
                       'request': Q.EXTRA, 'rows': [{'id': i, 'text': f'Synthetic CPU fixture {i}',
                       'prompt_ids': [i] + list(range(2047))} for i in range(16)]}
        export_path = self.root / 'export.json'; export_path.write_bytes(Q.canonical(self.export))
        # Explicit synthetic corpus fixtures; not real-tokenizer asset evidence.
        with patch.object(Q, 'verify_artifacts', return_value=[]):
            self.pin = Q.prepare(export_path, self.root / 'prepared')
            self.corpus = Q.validate_binding({'workload': Q.WORKLOAD, 'corpus': self.pin})
        self.plan = self.make_plan('off', '1' * 32)

    def make_plan(self, mode, attempt_id):
        q = {'workload': Q.WORKLOAD, 'corpus': self.corpus, 'corpus_sha256': self.pin['sha256'],
             'runs': ['off_control1', 'off_control2'] if mode == 'off' else ['dual'],
             'state_identity': {'native': 'same', 'steering': 'off'}}
        plan = {'window_id': '0' * 32, 'attempt_id': attempt_id, 'url': 'http://127.0.0.1:18000',
                'selection': {'mode': mode, 'steering': 'off'},
                'client': {'qualification': q, 'toolbox_head': 'f' * 40,
                           'output': str(self.root / attempt_id / 'lean.json')}}
        Path(plan['client']['output']).parent.mkdir()
        plan['plan_sha256'] = Q.digest(Q.canonical(plan))
        return plan

    def generation(self, plan, label):
        return {'schema': 'servebench-model/1', 'label': label, 'server': {'url': plan['url'], 'model': Q.MODEL},
                'workload': {'prompts': self.corpus['prompts']['path'], 'prompt_count': 16,
                             'prompts_sha256': self.corpus['prompts_sha256'], 'concurrency': 1,
                             'max_tokens': 16, 'fixed_length': True, 'top': 20, 'extra_body': Q.EXTRA},
                'generation': {'sequences': [{'id': r['id'], 'prompt_ids': r['prompt_ids'],
                                             'output_ids': list(range(16)), 'finish_reason': 'length'}
                                            for r in self.corpus['rows']]},
                'scores': None, 'score_seconds': None, 'self_agreement': None}

    def fake_main(self, plan, *, code=0, mismatch=None):
        def main(argv):
            label = argv[argv.index('--label') + 1]
            value = self.generation(plan, label)
            if label == mismatch:
                value['generation']['sequences'][3]['output_ids'][7] = 99
            Path(argv[argv.index('--output') + 1]).write_text(json.dumps(value, indent=1))
            return code
        return main

    def run_result(self, plan=None, **kwargs):
        plan = plan or self.plan
        code = Q.run_client(plan, self.fake_main(plan, **kwargs))
        return code, Q.json_bytes(Path(plan['client']['output']).read_bytes())

    def test_preparation_is_deterministic_and_not_tokenization(self):
        self.assertEqual(self.corpus['rows'], self.export['rows'])
        self.assertEqual(self.corpus['source_export_sha256'], Q.digest(Q.canonical(self.export)))
        self.assertEqual(Q.validate_corpus(self.corpus), self.corpus)
        with patch.object(Q, 'verify_artifacts', return_value=[]), self.assertRaises(FileExistsError):
            Q.prepare(self.root / 'export.json', self.root / 'prepared')

    def test_export_missing_identity_duplicate_or_inexact_ids_rejected(self):
        mutations = [lambda v: v['identity'].pop('weights_manifest_sha256'),
                     lambda v: v['identity'].update(tokenizer_revision='HEAD'),
                     lambda v: v.update(tokenizer_mode='auto'),
                     lambda v: v['request'].update(seed=1),
                     lambda v: v['rows'].pop(),
                     lambda v: v['rows'][1].update(text=v['rows'][0]['text']),
                     lambda v: v['rows'][1].update(prompt_ids=v['rows'][0]['prompt_ids']),
                     lambda v: v['rows'][1].update(id=True),
                     lambda v: v['rows'][1]['prompt_ids'].pop(),
                     lambda v: v['rows'][1]['prompt_ids'].__setitem__(0, True)]
        for mutation in mutations:
            value = copy.deepcopy(self.export); mutation(value)
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError): Q.validate_export(value)

    def test_raw_prompt_drift_and_placeholder_workloads_rejected(self):
        with self.assertRaises(RuntimeError): Q.validate_binding({'workload': 'perf', 'corpus': self.pin})
        path = Path(self.corpus['prompts']['path']); path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaises(RuntimeError): Q.validate_corpus(self.corpus)

    def test_closed_argv_has_no_score_fixed_meaning_and_no_free_args(self):
        argv = Q.argv(self.plan, 'off_control1', '/unused')
        self.assertIn('--no-score', argv); self.assertIn('--fixed-length', argv)
        for key, value in (('--max-tokens', '16'), ('--concurrency', '1'), ('--limit', '16')):
            self.assertEqual(argv[argv.index(key)+1], value)
        self.assertEqual(json.loads(argv[argv.index('--extra-body')+1]), Q.EXTRA)
        with self.assertRaises(RuntimeError): Q.argv(self.plan, 'injected', '/unused')

    def test_complete_controls_pass_and_semantic_mismatch_is_not_failure_retry(self):
        code, value = self.run_result(mismatch='off_control2')
        self.assertEqual(code, 0)
        verdict = Q.validate_output(value, self.plan)
        self.assertEqual(verdict['qualification_status'], 'INCONCLUSIVE_UNSTABLE_CONTROL')
        self.assertEqual(verdict['first_divergence'], {'id': 3, 'output_offset': 7, 'left': 7, 'right': 99})
        self.assertEqual(len(value['runs']), 2)

    def test_cli_failure_retains_raw_record_and_stops_without_second_call(self):
        code, value = self.run_result(code=2)
        self.assertEqual(code, 1); self.assertEqual(len(value['runs']), 1)
        self.assertTrue(Path(self.plan['client']['output']).with_name('off_control1.json').is_file())
        with self.assertRaises(RuntimeError): Q.validate_output(value, self.plan)

    def test_generation_rejects_incomplete_wrong_input_and_false_output_ids(self):
        mutations = [lambda v: v['generation']['sequences'].pop(),
                     lambda v: v['generation']['sequences'][1].update(id=0),
                     lambda v: v['generation']['sequences'][1].update(error='timeout'),
                     lambda v: v['generation']['sequences'][1].update(finish_reason='stop'),
                     lambda v: v['generation']['sequences'][1]['output_ids'].pop(),
                     lambda v: v['generation']['sequences'][1]['output_ids'].__setitem__(0, True),
                     lambda v: v['generation']['sequences'][1]['prompt_ids'].__setitem__(0, 100),
                     lambda v: v['workload'].update(concurrency=8),
                     lambda v: v.update(scores=[]),
                     lambda v: v['server'].update(model='other')]
        for mutation in mutations:
            value = copy.deepcopy(self.generation(self.plan, 'off_control1')); mutation(value)
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError):
                Q.validate_generation(value, self.plan, 'off_control1')

    def retain_attempt(self, plan):
        _, value = self.run_result(plan)
        path = Path(plan['client']['output'])
        receipt = {'window_id': plan['window_id'], 'attempt_id': plan['attempt_id'],
                   'plan_sha256': plan['plan_sha256'], 'exit_code': 0, 'error': None,
                   'output_sha256': Q.digest(path.read_bytes())}
        path.with_name('client-receipt.json').write_bytes(Q.canonical(receipt))
        directory = self.root / 'session' / 'attempts' / plan['attempt_id']; (directory / 'http').mkdir(parents=True)
        import q03_window as core
        root = directory.parent.parent
        capsule_path = root / 'capsule.json'
        job = {'kind': 'http_service', 'timeout_seconds': 7200, 'selection': {'mode': 'off', 'steering': 'off'}}
        capsule = {'window_id': plan['window_id'], 'session_dir': str(root),
                   'recovery_source_sha256': core.RuntimeOps().recovery_sources(),
                   'manifest': {'held_window': {'schema_version': 1}, 'window_seconds': 10800,
                       'preflight': {'hold_path': str(root / 'hold.json'), 'original_service_disposition': 'keep-stopped'},
                       'jobs': [job], 'http_service': {'schema_version': 2}}}
        if capsule_path.exists():
            capsule = json.loads(capsule_path.read_text())
        capsule_path.write_bytes(Q.canonical(capsule))
        (root / 'capsule.sha256').write_text(Q.digest(Q.canonical(capsule)))
        state_path = root / 'state.json'
        state = json.loads(state_path.read_text()) if state_path.exists() else {
            'attempt_results': [], 'attempt_receipt_sha256': {}, 'last_attempt_receipt_sha256': None}
        descriptor = {'attempt_id': plan['attempt_id'], 'window_id': plan['window_id'],
                      'capsule_sha256': Q.digest(Q.canonical(capsule)),
                      'previous_receipt_sha256': state['last_attempt_receipt_sha256'],
                      'jobs': [dict(job, selection=plan['selection'])],
                      'command_plan_sha256': Q.digest(Q.canonical([['http_service', plan]]))}
        descriptor_sha = Q.digest(Q.canonical(descriptor))
        terminal = {'outcome': 'passed', 'passed_gates': ['http_service'], 'attempt_id': plan['attempt_id'],
                    'window_id': plan['window_id'], 'descriptor_sha256': descriptor_sha,
                    'command_plan_sha256': descriptor['command_plan_sha256'],
                    'quiescence': {'quiescent': True, 'node_count': 4, 'attempt_id': plan['attempt_id'], 'phase': 'stopped'}}
        admitted = {'descriptor_sha256': descriptor_sha, 'command_plan_sha256': descriptor['command_plan_sha256']}
        for relative, obj in (('http/plan.json', plan), ('descriptor.json', descriptor), ('terminal.json', terminal),
                              ('admitted.json', admitted), ('http/result.json', Q.validate_output(value, plan))):
            (directory / relative).write_bytes(Q.canonical(obj))
        state['attempt_results'].append(terminal)
        state['attempt_receipt_sha256'][plan['attempt_id']] = Q.digest(Q.canonical(terminal))
        state['last_attempt_receipt_sha256'] = Q.digest(Q.canonical(terminal))
        state_path.write_bytes(Q.canonical(state))
        return directory

    def test_comparison_requires_full_drained_identity_chain(self):
        off = self.retain_attempt(self.plan)
        dual_plan = self.make_plan('dual', '2' * 32); dual = self.retain_attempt(dual_plan)
        verdict = Q.compare_drained(off, dual)
        self.assertEqual(verdict['status'], 'TOKEN_ID_MATCH')
        self.assertEqual(verdict['performance'], 'NOT_EVALUATED')
        terminal = json.loads((dual / 'terminal.json').read_text()); terminal['quiescence']['node_count'] = True
        (dual / 'terminal.json').write_bytes(Q.canonical(terminal))
        with self.assertRaises((RuntimeError, ValueError)): Q.compare_drained(off, dual)

    def test_reversed_valid_history_is_not_a_qualification_comparison(self):
        dual_plan = self.make_plan('dual', '2' * 32)
        dual = self.retain_attempt(dual_plan)
        off = self.retain_attempt(self.plan)
        with self.assertRaisesRegex(RuntimeError, 'attempt order'):
            Q.compare_drained(off, dual)

    def test_failed_attempt_is_retained_without_forbidding_operational_retry(self):
        first = self.retain_attempt(self.plan)
        terminal_path = first / 'terminal.json'; terminal = json.loads(terminal_path.read_text())
        terminal.update(outcome='failed', failure='after-client infrastructure failure')
        terminal_path.write_bytes(Q.canonical(terminal))
        state_path = first.parent.parent / 'state.json'; state = json.loads(state_path.read_text())
        state['attempt_results'][0] = terminal
        state['attempt_receipt_sha256'][self.plan['attempt_id']] = Q.digest(Q.canonical(terminal))
        state['last_attempt_receipt_sha256'] = Q.digest(Q.canonical(terminal))
        state_path.write_bytes(Q.canonical(state))
        session = SimpleNamespace(state=state, directory=first.parent.parent)
        self.assertEqual(Q.next_selection(session), {'mode': 'off', 'steering': 'off'})
        off_plan = self.make_plan('off', '3' * 32); off = self.retain_attempt(off_plan)
        dual_plan = self.make_plan('dual', '2' * 32); dual = self.retain_attempt(dual_plan)
        verdict = Q.compare_drained(off, dual)
        self.assertEqual(verdict['status'], 'INCONCLUSIVE_PRIOR_FAILED_ATTEMPTS')
        self.assertEqual(verdict['selected_complete_attempt_status'], 'TOKEN_ID_MATCH')
        self.assertEqual(verdict['failure_lineage'][0]['attempt_id'], self.plan['attempt_id'])
        self.assertEqual(verdict['failure_lineage'][0]['terminal_sha256'], Q.digest(Q.canonical(terminal)))

    def test_next_sequence_uses_two_controls_in_first_off_boot(self):
        session = SimpleNamespace(state={'attempt_results': []}, directory=self.root / 'session')
        self.assertEqual(Q.next_selection(session), {'mode': 'off', 'steering': 'off'})
        self.retain_attempt(self.plan)
        session.state['attempt_results'] = [{'attempt_id': self.plan['attempt_id'], 'outcome': 'passed', 'passed_gates': ['http_service']}]
        self.assertEqual(Q.next_selection(session), {'mode': 'dual', 'steering': 'off'})
        path = Path(self.plan['client']['output'])
        value = json.loads(path.read_text()); value['runs'][1]['value']['generation']['sequences'][0]['output_ids'][0] = 99
        path.write_bytes(Q.canonical(value))
        with self.assertRaises(RuntimeError): Q.next_selection(session)

    def test_old_http_smoke_sequence_is_unchanged(self):
        session = SimpleNamespace(manifest={'http_service': {'schema_version': 1}, 'jobs': [{'selection': {'steering': 'active'}}]},
                                  state={'attempt_results': []})
        self.assertEqual(H.next_selection(session), {'mode': 'off', 'steering': 'active'})

    def test_embedded_corpus_hash_is_not_a_self_declared_label(self):
        _, value = self.run_result()
        self.plan['client']['qualification']['corpus_sha256'] = 'f' * 64
        value['corpus_sha256'] = 'f' * 64
        with self.assertRaisesRegex(RuntimeError, 'embedded corpus'):
            Q.validate_output(value, self.plan)

    def test_provenance_requires_real_files_and_registered_model_root(self):
        with self.assertRaises(FileNotFoundError): Q.verify_artifacts(self.export)
        value = copy.deepcopy(self.export); artifacts = value['artifacts']; identity = value['identity']
        model = Path(artifacts['model_root']); model.mkdir()
        for name, content in [('config.json', b'config'), ('tokenizer.json', b'tokenizer')]:
            (model / name).write_bytes(content)
        identity['model_config_sha256'] = Q.digest(b'config')
        identity['tokenizer_assets_sha256'] = {'tokenizer.json': Q.digest(b'tokenizer')}
        identity['special_tokens_sha256'] = Q.digest(b'tokenizer')
        Path(artifacts['weights_manifest']['path']).write_bytes(b'manifest')
        artifacts['weights_manifest']['sha256'] = identity['weights_manifest_sha256'] = Q.digest(b'manifest')
        native = {}
        for name, pin in artifacts['native_sources'].items():
            raw = ('CPU ONLY ' + name).encode(); Path(pin['path']).write_bytes(raw)
            native[name] = pin['sha256'] = Q.digest(raw)
        metadata = {key: identity[key] for key in ('model_repository', 'model_revision', 'tokenizer_revision',
                                                  'transformers_version', 'tokenizers_version')}
        metadata.update(image=Q.owner.IMAGE, source_head=Q.owner.SOURCE_HEAD,
                        runtime_manifest_sha256=Q.owner.RUNTIME_SHA, model_root=str(model))
        raw = Q.canonical(metadata); Path(artifacts['runtime_metadata']['path']).write_bytes(raw)
        artifacts['runtime_metadata']['sha256'] = Q.digest(raw)
        with patch.dict(Q.NATIVE_SOURCES, native, clear=True):
            files = Q.verify_artifacts(value)
            probe = subprocess.run([sys.executable, '-I', '-B', '-c', Q.ASSET_PROBE, Q.canonical(files).decode()],
                                   capture_output=True, text=True, timeout=10)
            self.assertEqual(probe.returncode, 0, probe.stderr)
            self.assertEqual(json.loads(probe.stdout), {'verified_files': 2})
            with self.assertRaisesRegex(RuntimeError, 'registered'):
                Q.verify_artifacts(value, {'profile_value': {'host': {'home': '/unused'},
                    'container': {'mounts': [['/wrong', '/models', 'ro']]}}})
            (model / 'tokenizer.json').write_bytes(b'changed')
            with self.assertRaisesRegex(RuntimeError, 'artifact changed'): Q.verify_artifacts(value)

    def test_controller_source_loader_ignores_poisoned_timestamp_pyc(self):
        import os
        import py_compile
        import shutil
        source_dir = self.root / 'controller'; source_dir.mkdir()
        for name in ('q03_http_service.py', 'q03_owner_model.py', 'q03_held_window.py', 'q03_qualification.py'):
            shutil.copyfile(ROOT / 'scripts' / name, source_dir / name)
        target = source_dir / 'q03_qualification.py'
        good = target.read_bytes(); stamp = target.stat().st_mtime
        bad = b"raise RuntimeError('POISONED TIMESTAMP BYTECODE')\n"
        target.write_bytes(bad + b' ' * (len(good) - len(bad)))
        os.utime(target, (stamp, stamp)); py_compile.compile(str(target), doraise=True)
        target.write_bytes(good); os.utime(target, (stamp, stamp))
        code = "import sys;sys.path.insert(0,sys.argv[1]);import q03_http_service as h;print(h.qualification_module().WORKLOAD)"
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(source_dir)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), Q.WORKLOAD)

    def test_real_isolated_client_imports_frozen_qualification_closure(self):
        toolbox = self.root / 'toolbox' / 'servebench'; toolbox.mkdir(parents=True)
        (toolbox / '__init__.py').write_text('')
        # CPU fake toolbox; the actual production inventory remains pinned to PR8.
        source = 'import json\nfrom pathlib import Path\nVALUES=' + repr({label: self.generation(self.plan, label) for label in ('off_control1','off_control2')}) + '\ndef main(argv):\n label=argv[argv.index("--label")+1]\n Path(argv[argv.index("--output")+1]).write_text(json.dumps(VALUES[label]))\n return 0\n'
        (toolbox / '__main__.py').write_text(source)
        self.plan['client']['qualification_sources'] = {name: hashlib.sha256(
            (ROOT / 'scripts' / (name + '.py')).read_bytes()).hexdigest()
            for name in ('q03_qualification', 'q03_owner_model', 'q03_held_window')}
        self.plan['client'].update(toolbox_path=str(toolbox.parent), toolbox_files={
            'servebench/' + p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in toolbox.iterdir()})
        self.plan.pop('plan_sha256'); self.plan['plan_sha256'] = Q.digest(Q.canonical(self.plan))
        toolbox_before = {p: p.read_bytes() for p in toolbox.iterdir()}
        parent_umask = next(line for line in Path('/proc/self/status').read_text().splitlines()
                            if line.startswith('Umask:'))
        for mask in (0o000, 0o002, 0o022, 0o077):
            with self.subTest(umask=oct(mask)):
                output_dir = self.root / str(mask)
                output_dir.mkdir(mode=0o700)
                plan = copy.deepcopy(self.plan)
                plan['client']['output'] = str(output_dir / 'lean.json')
                plan.pop('plan_sha256'); plan['plan_sha256'] = Q.digest(Q.canonical(plan))
                path = self.root / ('plan-' + str(mask) + '.json')
                path.write_bytes(Q.canonical(plan))
                result = subprocess.run([sys.executable, '-I', '-B', str(ROOT / 'scripts/q03_http_client.py'),
                                         '--plan', str(path), '--sha256', Q.digest(path.read_bytes())],
                                        capture_output=True, text=True, timeout=15, umask=mask)
                self.assertEqual(result.returncode, 0, result.stderr)
                receipt = json.loads(result.stdout)
                self.assertEqual(receipt['exit_code'], 0, receipt)
                value = json.loads(Q.read_owned_bytes(Path(plan['client']['output'])))
                self.assertEqual(Q.validate_output(value, plan)['qualification_status'], 'CONTROL_STABLE')
                for label in ('off_control1', 'off_control2'):
                    self.assertEqual(json.loads(Q.read_owned_bytes(output_dir / (label + '.json'))),
                                     self.generation(plan, label))
                for artifact in output_dir.iterdir():
                    Q.read_owned_bytes(artifact)
                    self.assertEqual(stat.S_IMODE(artifact.stat().st_mode), 0o600, artifact.name)
                self.assertEqual({p: p.read_bytes() for p in toolbox.iterdir()}, toolbox_before)
        self.assertIn(parent_umask, Path('/proc/self/status').read_text().splitlines())


if __name__ == '__main__':
    unittest.main()
