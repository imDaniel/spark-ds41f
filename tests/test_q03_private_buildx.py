"""CPU private-plugin contracts; synthetic bytes are never executed.

The fixture patches only the expected digest for inert bytes. Official version
and checksum selection are checked separately. No Docker/plugin subprocess is
permitted. These authored tests require explicit authorization before running.
"""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_q03_buildkit_plan as fixtures

M = fixtures.M
ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('q03_private_build_context', ROOT / 'scripts/q03_window_build.py')
B = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(B)


class PrivateBuildxTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.BuildkitTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.p = self.fixture.p
        self.root = self.fixture.root
        self.staged = self.fixture.staged
        for name in ('run', 'Popen', 'check_call', 'check_output'):
            blocked = patch.object(subprocess, name, side_effect=AssertionError('real process forbidden'))
            blocked.start()
            self.addCleanup(blocked.stop)

    def provision(self, run=None):
        return M.provision(self.p, run or self.fixture.fake, buildx_binary=self.staged)

    def test_official_pin_and_private_selection(self):
        # Digest override is scoped to inert fixture; read the unmodified source
        # constant via the fixture module's saved original, not a downloaded file.
        self.assertEqual(M.BUILDX_VERSION, 'v0.21.3')
        self.assertEqual(fixtures.OFFICIAL_BUILDX_SHA256,
                         '90157681a0a033b285e80d3350741452d7647994fb371c9ee3a423f2ca4f22cc')
        self.assertEqual(self.p['buildx_plugin']['version'], 'v0.21.3')
        self.assertEqual(self.p['buildx_plugin']['sha256'], hashlib.sha256(self.staged.read_bytes()).hexdigest())
        self.provision()
        private = Path(self.p['build_environment']['DOCKER_CONFIG'])
        self.assertEqual(Path(self.p['buildx_plugin']['path']), private / 'cli-plugins/docker-buildx')
        self.assertEqual((private / 'config.json').read_bytes(), b'{}\n')
        self.assertEqual(M.verify_plugin(self.p), self.p['buildx_plugin'])

    def test_bad_staged_hash_refuses_before_any_write_or_command(self):
        self.staged.write_bytes(b'wrong bytes')
        with self.assertRaises(ValueError):
            self.provision()
        self.assertEqual(self.fixture.calls, [])
        self.assertFalse(Path(self.p['root']).exists())

    def test_plugin_or_discovery_config_drift_refuses_before_reinspection(self):
        self.provision()
        plugin = Path(self.p['buildx_plugin']['path'])
        config = plugin.parent.parent / 'config.json'
        for target, replacement in ((plugin, b'different binary'),
                                    (config, b'{"cliPluginsExtraDirs":["/foreign/plugins"]}\n')):
            original = target.read_bytes()
            target.write_bytes(replacement)
            self.fixture.calls.clear()
            try:
                with self.subTest(target=target.name), self.assertRaises(ValueError):
                    M.verify_live(self.p, self.fixture.fake)
                self.assertEqual(self.fixture.calls, [])
            finally:
                target.write_bytes(original)
        tampered = copy.deepcopy(self.p)
        tampered['buildx_plugin']['version'] = 'v0.35.0'
        with self.assertRaises(ValueError):
            M.verify_plugin(tampered)

    def test_failed_builder_attempt_is_not_reused(self):
        def fail_create(argv):
            if argv == self.p['create']:
                raise RuntimeError('injected create failure')
            return self.fixture.fake(argv)
        with self.assertRaisesRegex(RuntimeError, 'create failure'):
            self.provision(fail_create)
        self.assertTrue(Path(self.p['root']).exists())
        self.assertFalse((Path(self.p['root']) / 'provision-receipt.json').exists())
        self.fixture.calls.clear()
        with self.assertRaises(ValueError):
            self.provision()
        self.assertEqual(self.fixture.calls, [])

    def test_device_requests_always_rejected_even_with_void_visibility(self):
        for request in ({'Count': -1}, {'Count': 0}, {'Driver': 'nvidia', 'DeviceIDs': ['0']}):
            container = copy.deepcopy(self.fixture.container)
            container['HostConfig']['DeviceRequests'] = [request]
            with self.subTest(request=request), self.assertRaises(ValueError):
                M.verify_state(self.p, self.fixture.builder, container, self.fixture.volume)

    def test_global_plugin_and_inherited_configuration_are_untouched(self):
        home = self.root / 'synthetic-home'
        global_plugin = home / '.docker/cli-plugins/docker-buildx'
        global_plugin.parent.mkdir(parents=True)
        global_plugin.write_bytes(b'global plugin sentinel')
        inherited = {'HOME': str(home), 'DOCKER_CONFIG': str(home / '.docker'),
                     'BUILDX_BUILDER': 'foreign', 'DOCKER_CONTEXT': 'foreign'}
        with patch.dict(M.os.environ, inherited):
            before = dict(M.os.environ)
            environment = M.clean_environment(self.p)
            self.provision()
            self.assertEqual(dict(M.os.environ), before)
        self.assertEqual(global_plugin.read_bytes(), b'global plugin sentinel')
        self.assertEqual(environment['DOCKER_CONFIG'], self.p['build_environment']['DOCKER_CONFIG'])
        self.assertNotIn('DOCKER_CONTEXT', environment)
        self.assertFalse(any('--use' in argv or 'prune' in argv for argv in self.fixture.calls))

    def test_official_build_revalidates_same_plugin_and_selects_private_config(self):
        self.provision()
        receipt = Path(self.p['root']) / 'provision-receipt.json'
        build = {'builder_receipt_path': str(receipt),
                 'builder_receipt_sha256': hashlib.sha256(receipt.read_bytes()).hexdigest(),
                 'tag': 'q03-validation:synthetic-only'}
        commands = []
        def run(argv, **kwargs):
            commands.append(argv)
            return SimpleNamespace(returncode=0, stdout=self.fixture.fake(argv))
        ops = SimpleNamespace(resolve=lambda path: str(Path(path).resolve()),
                              sha256=lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                              read_json=lambda path: json.loads(Path(path).read_text()), run=run)
        context = (build, str(self.root / 'checkout'), str(self.root / 'inputs'),
                   str(self.root / 'output'), {})
        manifest = {'jobs': [{'kind': 'build'}], 'independent_root': str(self.root)}
        with (patch.dict(sys.modules, {'q03_buildkit_plan': M}),
              patch.object(B, '_context', return_value=context)):
            result = B.validate_build(manifest, ops)
            setting = 'DOCKER_CONFIG=' + self.p['build_environment']['DOCKER_CONFIG']
            self.assertIn(setting, result['argv'])
            official = str(self.root / 'checkout/bin/spark')
            self.assertIn(official, result['argv'])
            self.assertEqual(result['argv'][-5:], ['build', 'image', '--tag', build['tag'], '--apply'])
            checks = [argv for argv in commands if official in argv]
            self.assertEqual(len(checks), 1)
            self.assertIn(setting, checks[0])
            self.assertEqual(checks[0][-2:], ['build', 'check'])
            # A changed private binary must stop the official build plan before
            # even an inspect/check command, not fall back to the global plugin.
            Path(self.p['buildx_plugin']['path']).write_bytes(b'drift')
            commands.clear()
            with self.assertRaises(ValueError):
                B.validate_build(manifest, ops)
            self.assertEqual(commands, [])


if __name__ == '__main__':
    unittest.main()
