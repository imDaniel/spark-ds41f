"""Tiny CPU-only repositories; no Mac, Spark, SSH, Docker or network execution."""
import argparse
import ast
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import q03_source_delivery as source
import q03_window_preflight as preflight


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='.source-test-', dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'checkout'
        self.repo.mkdir()
        self.git('init', '--quiet', '--template=')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (self.repo / 'a').write_text('frozen\n')
        self.git('add', 'a')
        self.git('commit', '--quiet', '-m', 'fixture')
        self.url = 'https://github.com/imDaniel/spark-ds41f.git'
        self.git('remote', 'add', 'origin', self.url)
        self.sha = self.git('rev-parse', 'HEAD')
        self.tree = self.git('rev-parse', 'HEAD^{tree}')
        self.config_sha = source.digest(self.repo / '.git/config')
        self.bundle = self.root / 'controller.bundle'
        self.git('bundle', 'create', '--version=2', str(self.bundle), 'HEAD')
        self.bundle.chmod(0o600)
        self.receipt = self.root / 'controller.receipt.json'
        self.nodes = [{'name': 'node' + str(rank), 'rank': rank} for rank in range(1, 5)]
        self.pub = {'url': self.url, 'ref': 'refs/heads/frozen', 'sha': self.sha,
            'source_bundle': {'tree': self.tree, 'checkout': str(self.repo),
                'git_config_sha256': self.config_sha,
                'node_git_config_sha256': {n['name']: self.config_sha for n in self.nodes},
                'bundle_path': str(self.bundle), 'bundle_sha256': source.digest(self.bundle),
                'receipt_path': str(self.receipt), 'receipt_sha256': '0' * 64}}
        self.manifest = {'source_delivery': {'mode': source.MODE, 'preparation_id': '1' * 32,
                'session_dir': str(self.root / 'new-session'),
                'sync': {'cluster_config': 'config.json', 'cluster_config_sha256': '7' * 64,
                         'shape': None, 'site_files': {'nodes.json': '8' * 64}}},
            'independent_root': str(self.root), 'preflight': {'publication': self.pub,
                'independent_checkout': str(self.repo), 'protected_roots': [str(self.root / 'original')],
                'nodes': self.nodes}, 'candidate': {'output_path': str(self.root / 'results')}}
        self.seal()

    def git(self, *args):
        return source.git(self.repo, *args)

    def seal(self):
        self.proof = {'schema_version': 1, 'mode': source.MODE, 'preparation_id': '1' * 32,
            'session_dir': self.manifest['source_delivery']['session_dir'],
            'manifest_sha256': source.projection(self.manifest),
            'publication': {k: self.pub[k] for k in ('url', 'ref', 'sha')}, 'tree': self.tree,
            'bundle_sha256': self.pub['source_bundle']['bundle_sha256'],
            'bundle_bytes': self.bundle.stat().st_size,
            'observation': self.sha + '\trefs/heads/frozen', 'producer_system': 'Darwin',
            'observed_at': '2026-10-22T00:00:00+00:00', 'producer_hostname': 'authorized-mac',
            'source_git_config_sha256': self.config_sha}
        self.receipt.write_bytes(source.encoded(self.proof) + b'\n')
        self.receipt.chmod(0o600)
        self.pub['source_bundle']['receipt_sha256'] = source.digest(self.receipt)

    def test_valid_offline_evidence_and_empty_closure(self):
        source.verify_publication(self.pub, self.manifest)
        source.verify_closure(self.bundle, self.pub, self.root)
        with patch.object(preflight, '_run', side_effect=AssertionError('network forbidden')):
            preflight.verify_build_publication(self.pub, object(), self.manifest)

    def test_default_online_contract_unchanged(self):
        pub = {k: self.pub[k] for k in ('url', 'ref', 'sha')}
        with patch.object(preflight, '_run', return_value=self.sha + '\t' + pub['ref']) as call:
            preflight.verify_build_publication(pub, object())
        self.assertIn('ls-remote', call.call_args.args[1])

    def test_projection_omits_only_runtime_fields_and_known_receipt_pins(self):
        expected = source.projection(self.manifest)
        altered = copy.deepcopy(self.manifest)
        altered['preflight'].update(window_id='new', holder='new', glm_identity={'live': True})
        altered['preflight']['publication']['source_bundle']['receipt_sha256'] = '9' * 64
        self.assertEqual(expected, source.projection(altered))
        altered['candidate']['receipt_sha256'] = '9' * 64
        self.assertNotEqual(expected, source.projection(altered))

    def test_manifest_session_or_root_mutation_rejected(self):
        for section, key, value in [('source_delivery', 'preparation_id', '2' * 32),
                                    ('source_delivery', 'session_dir', str(self.root / 'other')),
                                    ('candidate', 'output_path', str(self.root / 'other-output'))]:
            altered = copy.deepcopy(self.manifest)
            altered[section][key] = value
            with self.assertRaises(ValueError):
                source.verify_evidence(altered['preflight']['publication'], altered)
        with self.assertRaises(ValueError):
            source.validate_manifest(self.manifest, self.root / 'other')

    def test_url_branch_tree_and_bundle_mutation_rejected(self):
        for key, value in [('url', 'https://example.invalid/evil.git'), ('ref', 'refs/heads/other')]:
            altered = copy.deepcopy(self.manifest)
            altered['preflight']['publication'][key] = value
            with self.assertRaises(ValueError):
                source.verify_evidence(altered['preflight']['publication'], altered)
        self.bundle.write_bytes(self.bundle.read_bytes() + b'bad')
        with self.assertRaises(ValueError):
            source.verify_evidence(self.pub, self.manifest)

    def test_wrong_computed_tree_and_noncommit_rejected(self):
        bad = copy.deepcopy(self.pub)
        bad['source_bundle']['tree'] = 'a' * 40
        with self.assertRaises(ValueError):
            source.verify_closure(self.bundle, bad, self.root)
        with self.assertRaises(ValueError):
            source.bundle_header(self.bundle, self.tree)

    def test_prerequisites_and_extra_refs_rejected(self):
        original = self.bundle.read_bytes()
        for header in [b'-' + self.sha.encode() + b' required\n', self.sha.encode() + b' refs/heads/extra\n']:
            self.bundle.write_bytes(original.replace(b'\n\n', b'\n' + header + b'\n', 1))
            with self.assertRaises(ValueError):
                source.bundle_header(self.bundle, self.sha)
        self.bundle.write_bytes(original[:-10])
        with self.assertRaises(subprocess.CalledProcessError):
            source.verify_closure(self.bundle, self.pub, self.root)

    def test_object_directory_alias_rejected(self):
        objects = self.repo / '.git/objects'
        outside = self.root / 'external-objects'
        objects.rename(outside)
        objects.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            source.metadata(self.repo, self.config_sha)

    def test_shallow_alternates_and_replace_rejected(self):
        for name in ['shallow', 'objects/info/alternates', 'refs/replace']:
            path = self.repo / '.git' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('bad')
            with self.assertRaises(ValueError):
                source.metadata(self.repo, self.config_sha)
            path.unlink()

    def test_hardlink_and_output_overlap_rejected(self):
        link = self.root / 'receipt-link'
        os.link(self.receipt, link)
        with self.assertRaises(ValueError):
            source.verify_evidence(self.pub, self.manifest)
        link.unlink()
        self.manifest['candidate']['output_path'] = str(self.root)
        with self.assertRaises(ValueError):
            source.validate_manifest(self.manifest)

    def test_evidence_cannot_overlap_another_selected_checkout(self):
        build = copy.deepcopy(self.pub)
        build_root = self.root / 'build-checkout'
        build['source_bundle'].update(checkout=str(build_root),
            bundle_path=str(self.root / 'build.bundle'), receipt_path=str(self.root / 'build.receipt'))
        self.manifest['candidate']['build_publication'] = build
        for path in (build_root, build_root / 'source.bundle'):
            self.pub['source_bundle']['bundle_path'] = str(path)
            with self.assertRaises(ValueError):
                source.validate_manifest(self.manifest)

    def test_assume_unchanged_and_stat_cache_drift_rejected(self):
        self.git('update-index', '--assume-unchanged', 'a')
        (self.repo / 'a').write_text('drift!\n')
        with self.assertRaises(ValueError):
            source.inspect_checkout(self.repo, self.pub, self.config_sha)
        self.git('update-index', '--no-assume-unchanged', 'a')
        with self.assertRaises(ValueError):
            source.verify_worktree(self.repo, self.sha)

    def test_git_environment_is_sanitized(self):
        with patch.dict(os.environ, {'GIT_CONFIG_COUNT': '1', 'GIT_SSH_COMMAND': 'evil',
                                     'GIT_DIR': '/evil', 'LD_PRELOAD': '/evil'}):
            env = source.clean_environment()
            self.assertNotIn('GIT_SSH_COMMAND', env)
            self.assertNotIn('GIT_CONFIG_COUNT', env)
            self.assertNotIn('GIT_DIR', env)
            self.assertNotIn('LD_PRELOAD', env)
            with source.sanitized_environment():
                self.assertEqual(os.environ['GIT_CONFIG_GLOBAL'], '/dev/null')
                self.assertNotIn('GIT_CONFIG_COUNT', os.environ)
            self.assertEqual(os.environ['GIT_CONFIG_COUNT'], '1')

    def test_mac_preparation_uses_lexical_spark_paths(self):
        with patch.object(source, 'canonical', side_effect=AssertionError('Mac resolved a Spark path')):
            source.validate_manifest(self.manifest, target_filesystem=False)
        with self.assertRaises(ValueError):
            source.prepare(self.manifest, {'controller': str(self.repo)}, self.root / 'export')

    def test_unsafe_existing_ancestry_refuses_before_mkdir(self):
        unsafe = self.root / 'unsafe'
        unsafe.mkdir(mode=0o777)
        unsafe.chmod(0o777)
        with self.assertRaises(ValueError):
            source._owned_parent(unsafe / 'new', self.root)
        self.assertFalse((unsafe / 'new').exists())

    def test_prepare_observes_exact_mac_ref_without_exporting_config(self):
        original = source.git
        calls = []
        def git(root, *args, network=False):
            if network:
                calls.append(args)
                if args[0] == 'ls-remote':
                    return self.sha + '\t' + self.pub['ref']
                return 'credential.helper\nosxkeychain\0'
            return original(root, *args, network=network)
        export = self.root / 'export'
        with patch.object(source.platform, 'system', return_value='Darwin'), patch.object(source, 'git', side_effect=git):
            result = source.prepare(copy.deepcopy(self.manifest), {'controller': str(self.repo)}, export)
        self.assertIn(('ls-remote', '--exit-code', self.url, self.pub['ref']), calls)
        receipt = json.loads((export / 'controller.receipt.json').read_text())
        self.assertEqual(receipt['manifest_sha256'], source.projection(result))
        self.assertNotIn('osxkeychain', (export / 'controller.receipt.json').read_text())
        self.assertEqual({p.name for p in export.iterdir()},
                         {'controller.bundle', 'controller.receipt.json', 'prepared-manifest.json'})
        self.bundle.write_bytes((export / 'controller.bundle').read_bytes())
        self.receipt.write_bytes((export / 'controller.receipt.json').read_bytes())
        prepared_pub = result['preflight']['publication']
        source.verify_publication(prepared_pub, result)
        source.verify_closure(self.bundle, prepared_pub, self.root)

    def test_producer_cli_rejects_short_ref_before_output_or_git(self):
        self.pub['ref'] = 'freeze/example'
        manifest = self.root / 'planned.json'
        checkouts = self.root / 'checkouts.json'
        output = self.root / 'must-not-exist'
        manifest.write_text(json.dumps(self.manifest))
        checkouts.write_text(json.dumps({'controller': str(self.repo)}))
        argv = ['q03_source_delivery.py', 'prepare', '--manifest', str(manifest),
                '--checkouts', str(checkouts), '--output', str(output)]
        with patch.object(sys, 'argv', argv), \
             patch.object(source.platform, 'system', return_value='Darwin'), \
             patch.object(source, 'git') as git:
            with self.assertRaisesRegex(ValueError, 'fully qualified: refs/heads/'):
                source.main()
            git.assert_not_called()
        self.assertFalse(output.exists())

    def test_mac_wrong_ref_and_transport_rewrite_rejected(self):
        original = source.git
        for config, observed in [('url.evil.insteadof\nhttps://github.com\0', self.sha + '\t' + self.pub['ref']),
                                  ('', self.sha + '\trefs/heads/wrong')]:
            def git(root, *args, network=False):
                if network:
                    return config if args[0] == 'config' else observed
                return original(root, *args)
            export = self.root / ('export-' + str(len(list(self.root.glob('export-*')))))
            with patch.object(source.platform, 'system', return_value='Darwin'), patch.object(source, 'git', side_effect=git):
                with self.assertRaises(ValueError):
                    source.prepare(copy.deepcopy(self.manifest), {'controller': str(self.repo)}, export)
            self.assertFalse((export / 'prepared-manifest.json').exists())

    def test_mac_local_helper_stays_on_mac_and_receiver_refuses_it(self):
        self.git('config', 'credential.helper', 'osxkeychain')
        pin = source.digest(self.repo / '.git/config')
        source.inspect_checkout(self.repo, self.pub, pin, mac_source=True)
        with self.assertRaises(ValueError):
            source.inspect_checkout(self.repo, self.pub, pin)

    def test_receipt_mutation_and_unsafe_git_descendant_rejected(self):
        self.receipt.write_bytes(self.receipt.read_bytes() + b' ')
        with self.assertRaises(ValueError):
            source.verify_evidence(self.pub, self.manifest)
        (self.repo / '.git/refs').chmod(0o777)
        with self.assertRaises(ValueError):
            source.metadata(self.repo, self.config_sha)

    def test_site_copy_creates_only_pinned_regular_files(self):
        text = '{"nodes": []}\r\n'
        self.manifest['source_delivery']['sync']['site_files'] = {
            'site/nodes.json': hashlib.sha256(text.encode()).hexdigest()}
        self.seal()
        source.copy_site_files(self.manifest, {'site/nodes.json': text})
        destination = self.repo / 'site/nodes.json'
        self.assertEqual(destination.read_bytes(), text.encode())
        self.assertEqual(destination.stat().st_nlink, 1)

    def test_wrong_host_is_rejected_before_write(self):
        with patch.object(source.platform, 'node', return_value='wrong-node'):
            with self.assertRaises(ValueError):
                source.remote_action('stage', self.manifest, 'node4', 'node4', self.sha, self.receipt.read_text())
        self.assertFalse((self.root / 'new-session').exists())

    def test_legacy_rejects_unbound_bundle(self):
        with self.assertRaises(preflight.PreflightError):
            preflight.verify_build_publication(self.pub, object())
        altered = copy.deepcopy(self.manifest)
        del altered['source_delivery']
        with self.assertRaises(ValueError):
            source.validate_manifest(altered)

    def sync_fixture(self):
        (self.repo / '.git/info').mkdir(exist_ok=True)
        (self.repo / '.git/info/exclude').write_text('config.json\nnodes.json\n')
        (self.repo / 'config.json').write_text('{}\n')
        (self.repo / 'nodes.json').write_text('{}\n')
        for node in self.nodes:
            node['local'] = node['rank'] == 1
            inventory = self.root / (node['name'] + '.json')
            inventory.write_text(json.dumps({'node': node['name'], 'exit_code': 0,
                                            'inventory': {'hostname': node['name']}}))
            node['inventory_path'] = str(inventory)
            node['inventory_sha256'] = source.digest(inventory)
        sync = self.manifest['source_delivery']['sync']
        sync['cluster_config_sha256'] = source.digest(self.repo / 'config.json')
        sync['site_files'] = {'nodes.json': source.digest(self.repo / 'nodes.json')}
        self.seal()
        manifest_path = self.root / 'prepared.json'
        manifest_path.write_text(json.dumps(self.manifest))
        spark = types.SimpleNamespace(ROOT=self.repo, NODES_FILE='nodes.json',
            repository_path=lambda c: str(self.repo), cluster_config_path=lambda a: (None, 'config.json'),
            cluster_recipe=types.SimpleNamespace(SITE_FILE='site.json'))
        args = argparse.Namespace(source_manifest=str(manifest_path), revision=None, apply=False, shape=None)
        cluster = {'deployment': {'repository': self.url}}
        nodes = {'ssh_user': 'fixture', 'nodes': [dict(name=n['name'], rank=n['rank'] - 1,
                  head=n['local']) for n in self.nodes]}
        return spark, args, cluster, nodes

    def test_sync_rejects_swapped_rank_and_changed_ignored_site_map(self):
        spark, args, cluster, nodes = self.sync_fixture()
        source.Sync(spark, args, cluster, nodes)
        nodes['nodes'][0]['rank'], nodes['nodes'][1]['rank'] = 1, 0
        with self.assertRaises(ValueError):
            source.Sync(spark, args, cluster, nodes)
        nodes['nodes'][0]['rank'], nodes['nodes'][1]['rank'] = 0, 1
        (self.repo / 'nodes.json').write_text('{"changed":true}\n')
        with self.assertRaises(ValueError):
            source.Sync(spark, args, cluster, nodes)

    def test_remote_profile_alias_and_drift_refuse_before_checkout(self):
        self.sync_fixture()
        profile = self.repo / 'config.json'
        profile.write_text('{"changed":true}')
        with patch.object(source.platform, 'node', return_value='node1'):
            with self.assertRaises(ValueError):
                source.remote_action('probe', self.manifest, 'node1', 'node1')
        profile.unlink()
        profile.symlink_to(self.repo / 'nodes.json')
        with patch.object(source.platform, 'node', return_value='node1'):
            with self.assertRaises(ValueError):
                source.remote_action('probe', self.manifest, 'node1', 'node1')
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.sha)

    def test_sync_accepts_old_clean_head_without_fetch_or_manual_checkout(self):
        old = self.sha
        (self.repo / 'a').write_text('next version\n')
        self.git('commit', '-am', 'next', '--quiet')
        self.sha = self.pub['sha'] = self.git('rev-parse', 'HEAD')
        self.tree = self.pub['source_bundle']['tree'] = self.git('rev-parse', 'HEAD^{tree}')
        self.bundle.unlink()
        self.git('bundle', 'create', '--version=2', str(self.bundle), 'HEAD')
        self.pub['source_bundle']['bundle_sha256'] = source.digest(self.bundle)
        self.git('checkout', '--quiet', '--detach', old)
        spark, args, cluster, nodes = self.sync_fixture()
        args.apply = True
        adapter = source.Sync(spark, args, cluster, nodes)
        self.assertEqual(adapter.revision, self.sha)
        self.assertEqual(self.git('rev-parse', 'HEAD'), old)
        frozen = adapter.command('probe', nodes['nodes'][0])
        with patch.object(Path, 'read_text', side_effect=AssertionError('helper hot-reload forbidden')):
            self.assertEqual(adapter.command('probe', nodes['nodes'][0]), frozen)

    def test_site_symlink_cannot_write_through_to_protected_file(self):
        protected = self.root / 'original'
        protected.mkdir()
        secret = protected / 'preserve'
        secret.write_text('unchanged')
        (self.repo / 'nodes.json').symlink_to(secret)
        with self.assertRaises(ValueError):
            source.copy_site_files(self.manifest, {'nodes.json': '{}'})
        self.assertEqual(secret.read_text(), 'unchanged')
        with self.assertRaises(ValueError):
            source.site_destination(self.repo, 'nodes.json')

    def test_runtime_watchdog_exact_source_closure(self):
        import importlib.util
        dependency_root = Path(os.environ.get('Q03_SOURCE_TEST_BASE_ROOT', ROOT))
        sys.path.append(str(dependency_root / 'scripts'))
        self.addCleanup(sys.path.remove, str(dependency_root / 'scripts'))
        def load(name):
            spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        core = load('q03_window')
        watchdog = load('q03_window_watchdog')
        digest = lambda path: hashlib.sha256(str(path).encode()).hexdigest()
        ops = core.RuntimeOps()
        ops.sha256 = digest
        sources = ops.recovery_sources()
        self.assertIn('q03_source_delivery.py', sources)
        directory = self.root / 'closure-session'
        directory.mkdir()
        core_path = ROOT / 'scripts/q03_window.py'
        capsule = {'session_dir': str(directory), 'deadline_epoch': 1,
            'heartbeat_timeout_s': 90, 'recovery_timeout_s': 7200,
            'recovery_command': [sys.executable, str(core_path), 'recover', '--session-dir', str(directory)],
            'recovery_script_sha256': digest(core_path), 'recovery_source_sha256': sources}
        raw = json.dumps(capsule).encode()
        (directory / 'capsule.json').write_bytes(raw)
        (directory / 'capsule.sha256').write_text(hashlib.sha256(raw).hexdigest())
        with patch.object(watchdog, 'digest', side_effect=digest):
            self.assertEqual(watchdog.load_capsule(directory)[0], capsule)

    def test_actual_checkout_then_postcheck_failure_rolls_back_same_node(self):
        old = self.sha
        (self.repo / 'a').write_text('new source\n')
        self.git('commit', '-am', 'next', '--quiet')
        self.sha = self.pub['sha'] = self.git('rev-parse', 'HEAD')
        self.tree = self.pub['source_bundle']['tree'] = self.git('rev-parse', 'HEAD^{tree}')
        self.bundle.unlink()
        self.git('bundle', 'create', '--version=2', str(self.bundle), 'HEAD')
        self.pub['source_bundle']['bundle_sha256'] = source.digest(self.bundle)
        self.git('checkout', '--quiet', '--detach', old)
        self.sync_fixture()
        tree = ast.parse((ROOT / 'bin/spark').read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'command_cluster_sync')
        events = []
        config_file = self.root / 'nodes.json'
        config_file.write_bytes(b'{}\r\n')
        env = {'argparse': argparse, 'Path': Path, 'sys': sys, 'shlex': __import__('shlex'),
            'ROOT': self.repo, 'NODES_FILE': 'nodes.json',
            'cluster_recipe': types.SimpleNamespace(SITE_FILE='site.json'),
            'require_named_profile': lambda a: None,
            'configuration': lambda a: ({'deployment': {'repository': self.url}}, {'nodes': self.nodes}, {}),
            'cluster_config_path': lambda a: (None, 'config.json'),
            'repository_path': lambda c: str(self.repo),
            'require_local_deployment_state': lambda *a: old,
            'normalized_repository_url': lambda s: s,
            'repository_config_path': lambda p: config_file}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), '<sync-fixture>', 'exec'), env)
        test = self
        original = source.inspect_checkout
        def inspect(*args, at_target=True, **kwargs):
            if at_target:
                test.assertEqual(test.git('rev-parse', 'HEAD'), test.sha)
                raise ValueError('injected failure AFTER actual Git checkout')
            return original(*args, at_target=False, **kwargs)
        class Delivery:
            revision = test.sha
            def inspect(self, node):
                return old, test.url, ''
            def validate_site_texts(self, texts):
                test.assertEqual(texts['nodes.json'], '{}\r\n')
            def stage(self, previous):
                pass
            def invoke(self, action, node, previous=None):
                events.append((action, node['name']))
                try:
                    with patch.object(source.platform, 'node', return_value=node['name']):
                        source.remote_action(action, test.manifest, node['name'], node['name'], previous)
                    return types.SimpleNamespace(returncode=0, stdout='', stderr='')
                except ValueError as exc:
                    return types.SimpleNamespace(returncode=1, stdout='', stderr=str(exc))
        with patch.object(source, 'inspect_checkout', side_effect=inspect), \
             patch('sys.stdout', new=io.StringIO()), patch('sys.stderr', new=io.StringIO()):
            self.assertEqual(env['command_cluster_sync'](argparse.Namespace(revision=None, apply=True), Delivery()), 1)
        self.assertEqual(events, [('checkout', 'node1'), ('rollback', 'node1')])
        self.assertEqual(self.git('rev-parse', 'HEAD'), old)

    def test_all_four_prevalidate_before_checkout_and_rollback(self):
        tree = ast.parse((ROOT / 'bin/spark').read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'command_cluster_sync')
        events = []
        nodes = {'nodes': self.nodes}
        config_file = self.root / 'nodes.json'
        config_file.write_text('{}')
        success = types.SimpleNamespace(returncode=0, stdout='', stderr='')
        env = {'argparse': argparse, 'Path': Path, 'sys': sys, 'shlex': __import__('shlex'),
            'ROOT': self.repo, 'NODES_FILE': 'nodes.json',
            'cluster_recipe': types.SimpleNamespace(SITE_FILE='site.json'),
            'require_named_profile': lambda a: None,
            'configuration': lambda a: ({'deployment': {'repository': self.url}}, nodes, {}),
            'cluster_config_path': lambda a: (None, 'config.json'),
            'repository_path': lambda c: str(self.repo),
            'require_local_deployment_state': lambda *a: self.sha,
            'normalized_repository_url': lambda s: s,
            'repository_config_path': lambda p: config_file,
            'write_remote_file': lambda *a: success,
            'run_ssh': lambda _, node, *a: types.SimpleNamespace(returncode=int(node['name'] == 'node4'), stdout='', stderr='doctor fixture failure')}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), '<sync-fixture>', 'exec'), env)
        test = self
        class Delivery:
            revision = test.sha
            def inspect(self, node):
                events.append(('probe', node['name']))
                if getattr(self, 'reject_four', False) and node['name'] == 'node4':
                    raise ValueError('fourth node invalid')
                return test.sha, test.url, ''
            def copy_site(self, node, texts):
                pass
            def validate_site_texts(self, texts):
                pass
            def stage(self, old):
                events.append(('stage', len(old)))
                test.assertEqual([('probe', n['name']) for n in test.nodes], events[:4])
            def invoke(self, action, node, old=None):
                events.append((action, node['name']))
                return success
            def finish(self):
                events.append(('finish', None))
        args = argparse.Namespace(revision=None, apply=True)
        delivery = Delivery()
        delivery.reject_four = True
        with self.assertRaises(ValueError), patch('sys.stdout', new=io.StringIO()):
            env['command_cluster_sync'](args, delivery)
        self.assertEqual(len(events), 4)
        events.clear()
        delivery.reject_four = False
        with patch('sys.stdout', new=io.StringIO()), patch('sys.stderr', new=io.StringIO()):
            self.assertEqual(env['command_cluster_sync'](args, delivery), 1)
        self.assertEqual(events[-4:], [('rollback', n['name']) for n in reversed(self.nodes)])
        self.assertNotIn(('finish', None), events)


if __name__ == '__main__':
    unittest.main()
