"""Narrow memguard lifecycle CPU fakes, not service or quiescence proof."""
import copy
import json
from types import SimpleNamespace
import unittest
from unittest import mock

import test_q03_remote_operation as sentinel_fixtures
import test_q03_original_spark_adapter as adapter_fixtures


class LifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sentinel_fixtures.RemoteOperationFaults.setUpClass()
        adapter_fixtures.RemoteAdapterContracts.setUpClass()

    def setUp(self):
        self.fixture = sentinel_fixtures.RemoteOperationFaults()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.remote = self.fixture.remote
        self.unit = 'registered-memguard.service'
        self.argv = self.remote.bound_command(['sudo', '-n', 'systemctl', 'stop', self.unit])
        self.receipt = {'exit_code': 5, 'group_quiescent': True, 'cancelled': False, 'timed_out': False}
        self.error = f'Failed to stop {self.unit}: Unit {self.unit} not loaded.\n'

    def test_classifier_accepts_only_bound_exact_lifecycle(self):
        classify = self.remote.acknowledged_memguard_absence
        self.assertTrue(classify(self.argv, self.receipt, '', self.error, self.unit))
        for key, value in [('exit_code', 1), ('exit_code', 137), ('exit_code', True),
                           ('group_quiescent', False), ('group_quiescent', 1),
                           ('cancelled', True), ('timed_out', True)]:
            with self.subTest(key=key, value=value):
                self.assertFalse(classify(self.argv, dict(self.receipt, **{key: value}), '', self.error, self.unit))
        for argv in ([*self.argv[:-1], 'other-memguard.service'], ['sudo', '-n', 'systemctl', 'stop', self.unit],
                     self.remote.bound_command(['docker', 'stop', 'registered']),
                     self.remote.bound_command(['sudo', '-n', 'systemctl', 'stop', 'unrelated.service'])):
            self.assertFalse(classify(argv, self.receipt, '', self.error, self.unit))
        for stdout, stderr in [('unexpected', self.error), ('', 'permission denied'), ('', 'Cannot connect to daemon'),
                               ('', self.error + 'extra error'), ('', self.error.replace(self.unit, 'foreign.service'))]:
            self.assertFalse(classify(self.argv, self.receipt, stdout, stderr, self.unit))

    def test_terminal_status_acknowledges_without_rewriting_failed_receipt(self):
        f = self.fixture
        directory = self.remote.create_operation(f.root, f.window, f.operation, self.argv, str(f.root), 1, f.epoch)
        ops = sentinel_fixtures.ChildOps(directory)
        ops.returncode = 5
        self.remote.supervise(directory, ops)
        (directory / 'error.log').write_text(self.error)
        before = (directory / 'receipt.json').read_bytes()
        status = f.status()
        self.assertTrue(status['lifecycle_acknowledged'])
        self.assertFalse(status['action_succeeded'])
        self.assertFalse(status['safe_to_overlap'])
        sealed = self.remote.seal_epoch(f.root, f.window, f.epoch)
        self.assertTrue(sealed['safe_to_overlap'])
        self.assertTrue(f.status()['safe_to_overlap'])
        self.assertEqual(f.status()['exit_code'], 5)
        self.assertEqual((directory / 'receipt.json').read_bytes(), before)

    def test_unknown_or_nonquiescent_receipt_never_becomes_acknowledged(self):
        f = self.fixture
        directory = self.remote.create_operation(f.root, f.window, f.operation, self.argv, str(f.root), 1, f.epoch)
        ops = sentinel_fixtures.ChildOps(directory)
        ops.returncode = 5
        self.remote.supervise(directory, ops)
        (directory / 'error.log').write_text('permission denied')
        self.assertFalse(f.status()['lifecycle_acknowledged'])
        self.assertFalse(self.remote.seal_epoch(f.root, f.window, f.epoch)['safe_to_overlap'])
        result = json.loads((directory / 'receipt.json').read_text())
        result['group_quiescent'] = False
        self.remote.atomic(directory / 'receipt.json', result)
        with self.assertRaisesRegex(RuntimeError, 'invalid terminal'):
            f.status()

    def test_adapter_returns_exit5_and_allows_original_inactive_probe(self):
        f = adapter_fixtures.RemoteAdapterContracts()
        f.setUp()
        self.addCleanup(f.doCleanups)
        row = dict(self.receipt, status='terminal', stdout='', stderr=self.error, action_succeeded=False)
        f.remote.helper_call = mock.Mock(return_value=row)
        node = f.nodes['nodes'][0]
        result = f.remote.run_ssh(f.nodes, node, 'sudo', '-n', 'systemctl', 'stop', self.unit)
        self.assertEqual(result.returncode, 5)
        self.assertEqual(result.stderr, self.error)
        self.assertFalse(f.remote.blocked)
        # The adapter preserves rc5; original controller must check inactivity.
        self.assertTrue(f.adapter.readonly_probe(('systemctl', 'is-active', '--quiet', self.unit), f.cluster))
        f.remote.helper_call.return_value = dict(row, stderr='permission denied')
        f.remote.run_ssh(f.nodes, node, 'sudo', '-n', 'systemctl', 'stop', self.unit)
        self.assertTrue(f.remote.blocked)
        with self.assertRaisesRegex(RuntimeError, 'prior remote operation'):
            f.remote.run_ssh(f.nodes, node, 'docker', 'start', 'registered')

class CompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import importlib.util
        from pathlib import Path
        path = Path(__file__).resolve().parents[1] / 'scripts/q03_recovery_compat.py'
        spec = importlib.util.spec_from_file_location('recovery_compat_test', path)
        cls.compat = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.compat)
        cls.remote = sentinel_fixtures.load_sentinel()

    def bundle(self):
        import hashlib
        self.window, self.epoch, self.operation = 'a' * 32, 'b' * 32, 'c' * 32
        self.unit = 'registered-memguard.service'
        manifest = {'window': self.window, 'epoch': self.epoch, 'operation': self.operation,
                    'helper_sha256': 'd' * 64,
                    'argv': self.remote.bound_command(['sudo', '-n', 'systemctl', 'stop', self.unit])}
        text = json.dumps(manifest)
        digest = hashlib.sha256(text.encode()).hexdigest()
        receipt = {'window': self.window, 'operation': self.operation, 'manifest_sha256': digest,
                   'exit_code': 5, 'group_quiescent': True, 'cancelled': False, 'timed_out': False}
        error = f'Failed to stop {self.unit}: Unit {self.unit} not loaded.\n'
        raw = {'manifest.json': text, 'receipt.json': json.dumps(receipt), 'output.log': '', 'error.log': error}
        return {'manifest_sha256': digest,
                'raw': {k: {'text': v, 'sha256': hashlib.sha256(v.encode()).hexdigest()} for k, v in raw.items()},
                'status': dict(receipt, status='terminal', action_succeeded=False, epoch_sealed=True,
                               safe_to_overlap=False, stdout='', stderr=error),
                'unit': self.unit, 'unit_show': {'returncode': 0,
                    'stdout': 'LoadState=not-found\nActiveState=inactive\nSubState=dead\n', 'stderr': ''}}

    def derive(self, bundle):
        return self.compat.derive_acknowledgment(bundle, self.unit, self.window, self.epoch,
                                               self.operation, self.remote.acknowledged_memguard_absence)

    def test_separate_ack_preserves_original_failed_terminal_evidence(self):
        bundle = self.bundle()
        before = copy.deepcopy(bundle)
        proof = self.derive(bundle)
        self.assertEqual(proof['original_exit_code'], 5)
        self.assertIs(proof['action_succeeded'], False)
        self.assertTrue(proof['epoch_sealed'])
        self.assertEqual(bundle, before)
        self.assertIs(bundle['status']['safe_to_overlap'], False)

    def test_unknown_manifest_live_unit_or_changed_raw_evidence_rejected(self):
        original = self.bundle()
        for section, key, value in [('unit_show', 'stdout', 'LoadState=loaded\nActiveState=active\nSubState=running\n'),
                                    ('unit_show', 'returncode', 1), ('unit_show', 'stderr', 'permission denied'),
                                    ('status', 'manifest_sha256', '0' * 64), ('status', 'stdout', 'changed')]:
            bundle = copy.deepcopy(original)
            bundle[section][key] = value
            with self.subTest(section=section, key=key), self.assertRaises(RuntimeError):
                self.derive(bundle)
        original['raw']['manifest.json']['text'] += ' '
        with self.assertRaisesRegex(RuntimeError, 'hash'):
            self.derive(original)
        bundle = self.bundle()
        with self.assertRaises(RuntimeError):
            self.compat.derive_acknowledgment(bundle, 'foreign-memguard.service', self.window,
                self.epoch, self.operation, self.remote.acknowledged_memguard_absence)

    def test_head_failed_plus_three_empty_epochs_gets_separate_ack_only(self):
        import tempfile
        from pathlib import Path
        bundle = self.bundle()
        before = copy.deepcopy(bundle)
        helper = self.remote
        class OldRemote:
            def helper_call(inner, node, action, operation=None, extra=()):
                return {'sealed': True, 'safe_to_overlap': node['name'] != 'head',
                        'operations': [copy.deepcopy(bundle['status'])] if node['name'] == 'head' else []}
        old = SimpleNamespace(RemoteOperations=OldRemote)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            cls = self.compat.install_overlay(old, helper.acknowledged_memguard_absence, directory)
            remote = cls()
            remote.cluster = {'container': {'name': 'registered'}}
            remote.nodes = {'nodes': []}
            remote.helper, remote.helper_sha = '/synthetic/helper.py', 'd' * 64
            remote.authority, remote.authority_sha = '/synthetic/authority.json', 'e' * 64
            remote.root, remote.window, remote.epoch = directory, self.window, self.epoch
            remote.transport = mock.Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps(bundle), stderr=''))
            results = [remote.helper_call({'name': name}, 'seal') for name in ('head', 'rank2', 'rank3', 'rank4')]
            self.assertTrue(all(result['safe_to_overlap'] for result in results))
            self.assertIs(results[0]['operations'][0]['action_succeeded'], False)
            self.assertEqual(results[0]['operations'][0]['exit_code'], 5)
            self.assertEqual(len(list(directory.glob('memguard-ack-*.json'))), 1)
            self.assertEqual(bundle, before)

    def test_compat_cli_accepts_exact_four_pins_and_rejects_missing_supervisor(self):
        import hashlib
        import tempfile
        from pathlib import Path
        here = Path(self.compat.__file__).resolve().parent
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            old_dir, recovery = root / 'old', root / 'recovery'
            old_dir.mkdir(); recovery.mkdir()
            pins = {name: hashlib.sha256((here / name).read_bytes()).hexdigest() for name in
                    ('q03_reconcile_restore.py', 'q03_recovery_compat.py', 'q03_remote_operation.py', 'q03_recovery_supervisor.py')}
            intent = {'old_session': str(old_dir), 'capsule_sha256': 'a' * 64, 'new_source_sha256': pins}
            (recovery / 'intent.json').write_text(json.dumps(intent))
            (old_dir / 'capsule.json').write_text(json.dumps({'manifest': {'independent_root': str(root),
                'preflight': {'protected_roots': [str(root / 'protected')]}}}))
            tail = ['--policy', str(root / 'policy'), '--policy-sha256', 'b' * 64,
                    '--spark-sha256', 'c' * 64, '--evidence-dir', str(root / 'evidence'),
                    '--hold', str(root / 'hold'), '--window-id', 'd' * 32,
                    '--epoch', 'e' * 32, '--action', 'quiesce', '--apply']
            argv = ['--old-session', str(old_dir), '--capsule-sha256', 'a' * 64,
                    '--recovery-dir', str(recovery)] + tail
            plan = SimpleNamespace(source_directory=str(old_dir), source_hashes=[('q03_original_spark_adapter.py', 'f' * 64)])
            support = SimpleNamespace(verify_restore_plan=lambda *a: plan,
                                      adapter_command_plan=lambda *a: ['unused'] * 4 + tail)
            old = SimpleNamespace(run=mock.Mock(return_value=0))
            def load(path, name, expected=None):
                return support if name == '_q03_compat_support' else old if name == '_q03_compat_verified_old_adapter' else self.remote
            with mock.patch.object(self.compat, 'load_source', side_effect=load), mock.patch.object(self.compat, 'install_overlay'):
                self.assertEqual(self.compat.main(argv), 0)
                old.run.assert_called_once()
                pins.pop('q03_recovery_supervisor.py')
                (recovery / 'intent.json').write_text(json.dumps(intent))
                with self.assertRaisesRegex(RuntimeError, 'inventory'):
                    self.compat.main(argv)
                old.run.assert_called_once()


if __name__ == '__main__':
    unittest.main()
