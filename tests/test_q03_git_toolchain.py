"""CPU contracts using inert ELF headers, never ARM execution or Docker."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


M = load('q03_git_toolchain')
CORE = load('q03_window')
PREPARE = load('q03_prepare_git_toolchain')
sha = lambda data: hashlib.sha256(data).hexdigest()


class BundleContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'bundle'
        self.root.mkdir()
        elf = bytearray(64); elf[:6] = b'\x7fELF\x02\x01'; elf[18:20] = (183).to_bytes(2, 'little')
        self.records = {}
        for name in M.FILES:
            data = bytes(elf) if name in M.ELF_FILES else b'copyright fixture\n'
            if name == 'bin/git': data = M.SHIM.encode()
            if name == 'launcher.py': data = Path(M.__file__).read_bytes()
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o755 if name in ('bin/git', 'real/git', 'lib/ld-linux-aarch64.so.1') else 0o644)
            self.records[name] = {'sha256': sha(data), 'size': len(data), 'mode': stat.S_IMODE(path.stat().st_mode)}
        self.manifest = {'schema_version': 1, 'architecture': 'aarch64', 'git_version': '2.43.0',
            'package_version': '1:2.43.0-1ubuntu7.3', 'files': self.records}
        self.pin()
        patch = mock.patch.object(M, 'GIT_SHA256', self.records['real/git']['sha256'])
        patch.start(); self.addCleanup(patch.stop)

    def pin(self):
        raw = json.dumps(self.manifest).encode()
        (self.root / 'manifest.json').write_bytes(raw); self.digest = sha(raw)

    def validate(self):
        return M.validate_bundle(self.root, self.digest, expected_owner=os.getuid())

    def test_complete_inert_fixture_and_official_pin_contract(self):
        self.assertEqual(self.validate(), self.manifest)
        self.assertEqual(M.validate_toolchain({'git_toolchain_path': str(self.root),
            'git_toolchain_manifest_sha256': self.digest}, None)['manifest'], self.manifest)
        source = Path(M.__file__).read_text()
        self.assertIn("GIT_SHA256 = 'aa6540695d076182256dd6e96c8b302e4d56381e3000bbfd5c71bbdfe94a4942'", source)

    def test_preparer_emits_validator_compatible_exclusive_bundle(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as temporary:
            output = Path(temporary).resolve() / 'emitted'
            files = {name: ((self.root / name).read_bytes(), {'mode': record['mode'],
                     'source_path': '/synthetic/' + name}) for name, record in self.records.items()}
            try:
                receipt = PREPARE.prepare(output, files)
                self.assertEqual(receipt['status'], 'prepared-unreviewed')
                self.assertFalse(receipt['container_execution'])
                M.validate_bundle(output, receipt['manifest_sha256'], expected_owner=os.getuid())
                before = (output / 'manifest.json').read_bytes()
                with self.assertRaises(ValueError): PREPARE.prepare(output, files)
                self.assertEqual((output / 'manifest.json').read_bytes(), before)
            finally:
                if output.exists():
                    output.chmod(0o700)
                    for path in output.rglob('*'):
                        path.chmod(0o700 if path.is_dir() else 0o600)

    def test_binary_library_manifest_and_launcher_tampering_refuse(self):
        for name in ('real/git', 'lib/libc.so.6', 'manifest.json', 'launcher.py'):
            path = self.root / name; before = path.read_bytes()
            path.write_bytes(before + b'changed')
            with self.subTest(name=name), self.assertRaises(ValueError): self.validate()
            path.write_bytes(before)

    def test_manifest_architecture_and_wrong_binary_even_when_rehashed_refuse(self):
        self.manifest['architecture'] = 'x86_64'; self.pin()
        with self.assertRaisesRegex(ValueError, 'architecture'): self.validate()
        self.manifest['architecture'] = 'aarch64'
        path = self.root / 'real/git'; data = path.read_bytes() + b'different host Git'
        path.write_bytes(data); self.records['real/git'].update(sha256=sha(data), size=len(data)); self.pin()
        with self.assertRaisesRegex(ValueError, 'reviewed Ubuntu'): self.validate()

    def test_alias_owner_permissions_and_extra_file_refuse(self):
        alias = self.root.parent / 'alias'; alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError): M.validate_bundle(alias, self.digest)
        with self.assertRaises(ValueError): M.validate_bundle(self.root, self.digest, expected_owner=os.getuid()+1)
        path = self.root / 'lib/libc.so.6'; path.chmod(0o666)
        with self.assertRaises(ValueError): self.validate()
        path.chmod(0o644)
        extra = self.root / 'unreviewed.so'; extra.write_text('extra')
        with self.assertRaisesRegex(ValueError, 'extra/missing'): self.validate()
        extra.unlink()
        target = self.root / 'licenses/git/copyright'; data = target.read_bytes(); target.unlink()
        target.symlink_to(self.root / 'licenses/libc6/copyright')
        with self.assertRaises(ValueError): self.validate()

    def test_rehashed_wrong_elf_and_launcher_source_refuse(self):
        for name, data in (('lib/libz.so.1', b'not an ELF binary'),
                           ('launcher.py', b'# different controller source\n')):
            path = self.root / name; old = path.read_bytes(); record = copy.deepcopy(self.records[name])
            path.write_bytes(data); self.records[name].update(sha256=sha(data), size=len(data)); self.pin()
            with self.subTest(name=name), self.assertRaises(ValueError): self.validate()
            path.write_bytes(old); self.records[name] = record; self.pin()


class GitMetadataContracts(unittest.TestCase):
    def setUp(self):
        binding=mock.patch.dict(sys.modules,{'q03_git_toolchain':M})
        binding.start();self.addCleanup(binding.stop)

    def test_config_hash_and_executable_redirections_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); git = root / '.git'; git.mkdir()
            config = git / 'config'
            good = '[core]\n repositoryformatversion = 0\n bare = false\n'
            config.write_text(good)
            self.assertTrue(M.validate_git_metadata(root, sha(config.read_bytes()))['self_contained'])
            with self.assertRaises(ValueError): M.validate_git_metadata(root, '0'*64)
            for content in ('[include]\n path = /foreign/config\n',
                            '[includeIf "gitdir:*"]\n path = /foreign/config\n',
                            '[filter "x"]\n process = external-command\n',
                            '[core]\n worktree = /foreign\n',
                            '[extensions]\n worktreeConfig = true\n',
                            '[core]\n fsmonitor = /external-command\n'):
                config.write_text(content)
                with self.subTest(content=content), self.assertRaises(ValueError):
                    M.validate_git_metadata(root, sha(config.read_bytes()))

    def test_external_metadata_and_config_aliases_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); git = root / '.git'; git.mkdir()
            config = git / 'config'; config.write_text('[core]\n bare = false\n')
            digest = sha(config.read_bytes())
            for relative in ('commondir', 'config.worktree', 'objects/info/alternates', 'objects/info/http-alternates'):
                path = git / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('/foreign')
                with self.subTest(relative=relative), self.assertRaises(ValueError):
                    M.validate_git_metadata(root, digest)
                path.unlink()
            source = root / 'other-config'; source.write_bytes(config.read_bytes()); config.unlink(); config.symlink_to(source)
            with self.assertRaises(ValueError): M.validate_git_metadata(root, digest)

    def test_host_metadata_rejection_precedes_any_git_command(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as temporary:
            root = Path(temporary).resolve()
            selection = json.loads((ROOT / 'experiments/2026-10-21-mhc-input-validation/validation-selection.json').read_text())
            candidate = {'component_head': selection['source_head'], 'git_config_sha256': 'a'*64}
            for key in ('tests_path', 'cache_path', 'output_path'):
                path = root / key; path.mkdir(); candidate[key] = str(path)
            runtime = root / 'runtime.json'; runtime.write_text('{}'); candidate['runtime_manifest_path'] = str(runtime)
            manifest = {'independent_root': str(root), 'preflight': {'independent_checkout': str(ROOT)}, 'candidate': candidate}
            ops = SimpleNamespace(run=mock.Mock(side_effect=AssertionError('Git must not execute')))
            with mock.patch.object(M, 'validate_git_metadata', side_effect=ValueError('unsafe config')):
                with self.assertRaisesRegex(ValueError, 'unsafe config'):
                    CORE.RuntimeOps.validate_host(ops, manifest)
            ops.run.assert_not_called()
            (Path(candidate['tests_path']) / '.git').mkdir()
            ops.run = mock.Mock(side_effect=[SimpleNamespace(stdout=selection['source_head']),
                SimpleNamespace(stdout=selection['source_tree']), SimpleNamespace(stdout='')])
            ops.validate_candidate_image = mock.Mock(); ops.validate_git_toolchain = mock.Mock()
            ops.validate_g1_test_source = lambda manifest, selection: CORE.RuntimeOps.validate_g1_test_source(ops, manifest, selection)
            with mock.patch.object(M, 'validate_git_metadata'):
                CORE.RuntimeOps.validate_host(ops, manifest)
            calls = [call.args[0] for call in ops.run.call_args_list]
            prefix = ['git', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null',
                      '-C', candidate['tests_path']]
            self.assertEqual(calls, [prefix + ['rev-parse', 'HEAD'],
                prefix + ['rev-parse', 'HEAD^{tree}'], prefix + ['status', '--porcelain']])


class PreparationContracts(unittest.TestCase):
    def test_source_facts_pin_inventory_alias_mode_size_and_bytes(self):
        import io
        requested = ['/usr/bin/git', '/lib/ld-linux-aarch64.so.1',
            '/lib/aarch64-linux-gnu/libpcre2-8.so.0', '/lib/aarch64-linux-gnu/libz.so.1',
            '/lib/aarch64-linux-gnu/libc.so.6',
            *('/usr/share/doc/' + name + '/copyright' for name in PREPARE.PACKAGES),
            '/usr/share/common-licenses/GPL-2', '/usr/share/common-licenses/LGPL-2.1']
        class InertFile(io.BytesIO):
            def fileno(self): return 123
        payload = b'inert source bytes'
        facts = {'files': [{'requested': name, 'resolved': '/synthetic/source/' + str(i),
            'uid': 0, 'mode': '0o644', 'bytes': len(payload), 'sha256': sha(payload)}
            for i, name in enumerate(requested)]}
        mapping = {Path(row['requested']): Path(row['resolved']) for row in facts['files']}
        info = SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_uid=0,
                               st_size=len(payload), st_mtime_ns=1)
        def check(value, expected=None):
            raw = json.dumps(value).encode()
            with mock.patch.object(PREPARE, 'read_source', return_value=(Path('/synthetic/facts'), raw)), \
                 mock.patch.object(Path, 'resolve', lambda path, strict=False: mapping.get(path, path)), \
                 mock.patch.object(PREPARE.os, 'open', return_value=123), \
                 mock.patch.object(PREPARE.os, 'fdopen', side_effect=lambda *args: InertFile(payload)), \
                 mock.patch.object(PREPARE.os, 'fstat', return_value=info):
                return PREPARE.source_inputs('/synthetic/facts', expected or sha(raw))
        self.assertEqual(len(check(facts)), 11)
        with self.assertRaisesRegex(ValueError, 'facts hash'): check(facts, '0'*64)
        for key, value in (('requested', '/foreign'), ('resolved', '/other'), ('uid', 1),
                           ('mode', '0o755'), ('bytes', 0), ('sha256', '0'*64)):
            changed = copy.deepcopy(facts); changed['files'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): check(changed)

    def test_private_authority_rejects_escape_alias_hash_and_protected_overlap(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as temporary:
            root = Path(temporary).resolve()
            authority = root / 'authority.json'
            value = {'independent_root': str(root), 'protected_roots': [str(root.parent / 'synthetic-protected')]}
            authority.write_text(json.dumps(value)); digest = sha(authority.read_bytes())
            output = root / 'bundle'
            self.assertEqual(PREPARE.validate_authority(output, authority, digest), output)
            for dest, path, checksum in ((root.parent / 'outside', authority, digest),
                                          (output, authority, '0' * 64)):
                with self.assertRaises(ValueError): PREPARE.validate_authority(dest, path, checksum)
            alias = root / 'alias'; alias.symlink_to(authority)
            with self.assertRaises(ValueError): PREPARE.validate_authority(output, alias, digest)
            value['protected_roots'] = [str(root / 'protected')]
            authority.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, 'overlaps protected'):
                PREPARE.validate_authority(output, authority, sha(authority.read_bytes()))

    def test_installed_package_version_architecture_and_status_must_match(self):
        def query(argv):
            package = argv[-1]
            return PREPARE.PACKAGES[package] + "\tarm64\tinstall ok installed" if argv[0] == '-W' else '/synthetic/file\n'
        self.assertEqual(set(PREPARE.inventory(query)), set(PREPARE.PACKAGES))
        for wrong in ('0.0\tarm64\tinstall ok installed',
                      '1:2.43.0-1ubuntu7.3\tamd64\tinstall ok installed',
                      '1:2.43.0-1ubuntu7.3\tarm64\tdeinstall ok config-files'):
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                PREPARE.inventory(lambda argv: wrong)

    def test_elf_parser_rejects_unlisted_dependency_and_wrong_architecture(self):
        import struct
        def elf(dependency):
            strings = b'\0' + dependency.encode() + b'\0'
            raw = bytearray(64 + 128 + 16 + len(strings))
            raw[:6] = b'\x7fELF\x02\x01'
            struct.pack_into('<H', raw, 18, 183)
            struct.pack_into('<Q', raw, 40, 64)
            struct.pack_into('<HH', raw, 58, 64, 2)
            struct.pack_into('<IIQQQQIIQQ', raw, 64, 0, 6, 0, 0, 192, 16, 1, 0, 0, 0)
            struct.pack_into('<IIQQQQIIQQ', raw, 128, 0, 3, 0, 0, 208, len(strings), 0, 0, 0, 0)
            struct.pack_into('<qQ', raw, 192, 1, 1)
            raw[208:] = strings
            return raw
        self.assertEqual(PREPARE.elf_dependencies(elf('libc.so.6')), ['libc.so.6'])
        with self.assertRaisesRegex(ValueError, 'unexpected ELF dependency'):
            PREPARE.elf_dependencies(elf('libunreviewed.so'))
        raw = elf('libc.so.6'); raw[18:20] = (62).to_bytes(2, 'little')
        with self.assertRaisesRegex(ValueError, 'AArch64'):
            PREPARE.elf_dependencies(raw)

    def test_real_cloud_git_head_and_dirty_checks_are_not_emulated(self):
        # x86 host Git protocol check only: substitute host executable for the
        # ARM loader/binary prefix; preserve constructed Git flags and environment.
        import subprocess
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null')
            def git(*args):
                return subprocess.check_output(['/usr/bin/git', *args], cwd=root, env=env, text=True)
            git('init', '-q')
            (root / 'tracked.txt').write_text('original')
            git('add', 'tracked.txt')
            git('-c', 'user.name=Synthetic Test', '-c', 'user.email=synthetic@example.invalid',
                'commit', '-qm', 'fixture')
            expected = git('rev-parse', 'HEAD').strip()
            def actual(argv):
                command, clean_env = M.git_command('/git-toolchain', argv, env, '/validation')
                return subprocess.check_output(['/usr/bin/git', *command[4:]],
                    cwd=root, env=clean_env, text=True)
            self.assertEqual(actual(['rev-parse', 'HEAD']).strip(), expected)
            self.assertEqual(actual(['status', '--porcelain']), '')
            (root / 'tracked.txt').write_text('changed')
            self.assertIn(' M tracked.txt', actual(['status', '--porcelain']))
            self.assertNotEqual(actual(['rev-parse', 'HEAD']).strip(), '0' * 40)


class LaunchContracts(unittest.TestCase):
    def test_only_real_p0_commands_and_exact_trust_with_private_library_path(self):
        inherited = {'PATH': '/usr/bin', 'LD_LIBRARY_PATH': '/torch-libs',
            'LD_PRELOAD': '/untrusted', 'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'safe.directory',
            'GIT_CONFIG_VALUE_0': '*', 'GIT_DIR': '/wrong', 'PYTHONPATH': '/image-native'}
        before = dict(inherited)
        for argv in M.ALLOWED_ARGV:
            command, env = M.git_command('/git-toolchain', argv, inherited, '/validation')
            self.assertEqual(command[:5], ['/git-toolchain/lib/ld-linux-aarch64.so.1',
                '--library-path', '/git-toolchain/lib', '/git-toolchain/real/git', '--no-pager'])
            self.assertIn('safe.directory=/validation', command)
            self.assertNotIn('safe.directory=*', command)
            self.assertFalse(any(k.startswith('LD_') for k in env))
            self.assertNotIn('GIT_CONFIG_COUNT', env); self.assertNotIn('GIT_DIR', env)
            self.assertEqual(env['GIT_OPTIONAL_LOCKS'], '0')
            self.assertEqual(env['PYTHONPATH'], '/image-native')
        self.assertEqual(inherited, before)
        for argv in (['show', 'HEAD:file'], ['status', '--porcelain', '--ignored'],
                     ['-c', 'safe.directory=*', 'status'], ['fetch'], ['rev-parse', 'foreign']):
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                M.git_command('/git-toolchain', argv, {}, '/validation')
        with self.assertRaises(ValueError): M.git_command('/git-toolchain', ['rev-parse', 'HEAD'], {}, '/wrong')

    def test_p0_mount_and_path_are_scoped_without_global_loader_environment(self):
        candidate = {'image': 'sha256:'+'a'*64, 'tests_path': '/independent/tests',
            'cache_path': '/independent/cache', 'output_path': '/independent/output',
            'runtime_manifest_path': '/independent/runtime.json', 'component_head': 'b'*40,
            'git_toolchain_path': '/independent/git', 'git_toolchain_manifest_sha256': 'c'*64,
            'git_toolchain_base_path': '/opt/bin:/usr/bin:/bin', 'git_config_sha256': 'e'*64,
            'tblib_path': '/independent/tblib', 'tblib_manifest_sha256': 'f'*64,
            'pytest_base_pythonpath': '/opt/native'}
        for kind in ('p0', 'g1', 'g2'):
            _, argv = CORE.job_command({'candidate': candidate}, kind, 'd'*32)
            env = [argv[i+1] for i, part in enumerate(argv[:-1]) if part == '--env']
            self.assertFalse(any(item.startswith('LD_') for item in env))
            git_mount = 'type=bind,src=/independent/git,dst=/git-toolchain,readonly'
            if kind == 'p0':
                self.assertIn(git_mount, argv)
                self.assertIn('PATH=/git-toolchain/bin:/opt/bin:/usr/bin:/bin', env)
            else:
                self.assertNotIn(git_mount, argv)
                self.assertFalse(any(item.startswith('PATH=') for item in env))

    def test_recovery_capsule_requires_exact_toolchain_and_held_source_pins(self):
        watchdog = load('q03_window_watchdog')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            sources = CORE.RuntimeOps().recovery_sources()
            expected = {'q03_window.py', 'q03_window_preflight.py', 'q03_window_watchdog.py',
                'q03_original_spark_adapter.py', 'q03_remote_operation.py', 'q03_build_adapter.py',
                'q03_window_build.py', 'q03_buildkit_plan.py', 'q03_git_toolchain.py',
                'q03_held_window.py', 'q03_source_delivery.py', 'q03_owner_model.py', 'q03_rank_logs.py', 'q03_image_preparation.py',
                'memguard-start.sh', 'memguard.sh', '../bin/spark', 'mesh_fabric.py',
                'kernel_backend.py', 'kernel_readiness.py', 'topology.py',
                'transport_profiles.py', 'model_layout.py', 'recipe.py',
                'q03_http_service.py', 'q03_http_worker.py', 'q03_http_client.py', 'q03_http_toolbox.json', 'q03_qualification.py', 'q03_quality_matrix.py', 'q03_performance.py', 'q03_baseline_service.py', 'q03_baseline_identity.py', 'q03_baseline_worker.py', 'q03_legacy_case_replay.py', 'q03_baseline_batch.py', 'q03_quality_capture.py', 'q03_quality_pilot.py'}
            self.assertEqual(set(sources), expected)
            for name, digest in sources.items():
                self.assertEqual(digest, hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest())
            capsule = {'session_dir': str(root), 'deadline_epoch': 100,
                'heartbeat_timeout_s': 20, 'recovery_timeout_s': 30,
                'recovery_command': [sys.executable, str(ROOT / 'scripts/q03_window.py'),
                                     'recover', '--session-dir', str(root)],
                'recovery_script_sha256': sources['q03_window.py'], 'recovery_source_sha256': sources}
            def save():
                raw = json.dumps(capsule).encode(); (root / 'capsule.json').write_bytes(raw)
                (root / 'capsule.sha256').write_text(sha(raw))
            save(); self.assertEqual(watchdog.load_capsule(root)[0], capsule)
            sources.pop('q03_git_toolchain.py'); save()
            with self.assertRaisesRegex(RuntimeError, 'inventory'):
                watchdog.load_capsule(root)

    def test_prehold_image_path_binding_refuses_ambiguous_or_changed_image(self):
        candidate = {'image': 'sha256:'+'a'*64, 'git_toolchain_base_path': '/usr/bin:/bin'}
        good = {'Id': candidate['image'], 'Config': {'Env': ['PATH=/usr/bin:/bin']}}
        ops = SimpleNamespace(run=mock.Mock(return_value=SimpleNamespace(stdout=json.dumps([good]))))
        with mock.patch.object(M, 'validate_toolchain') as validate:
            CORE.RuntimeOps.validate_git_toolchain(ops, {'candidate': candidate})
            validate.assert_called_once()
            for images in ([], [good, good], [dict(good, Id='sha256:'+'b'*64)],
                           [dict(good, Config={'Env': ['PATH=/other']})],
                           [dict(good, Config={'Env': ['PATH=/usr/bin:/bin']*2})]):
                ops.run.return_value.stdout = json.dumps(images)
                with self.subTest(images=images), self.assertRaises(ValueError):
                    CORE.RuntimeOps.validate_git_toolchain(ops, {'candidate': candidate})
            ops.run.reset_mock(); validate.side_effect = ValueError('bundle changed')
            with self.assertRaisesRegex(ValueError, 'bundle changed'):
                CORE.RuntimeOps.validate_git_toolchain(ops, {'candidate': candidate})
            ops.run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
