"""CPU-only BuildKit planning and fake Docker tests; no Docker execution."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('q03_buildkit', Path(__file__).resolve().parents[1] / 'scripts/q03_buildkit_plan.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
OFFICIAL_BUILDX_SHA256 = M.BUILDX_SHA256


class BuildkitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.staged = self.root / "staged-buildx"
        self.staged.write_bytes(b"inert synthetic plugin bytes; never executable in tests")
        digest = hashlib.sha256(self.staged.read_bytes()).hexdigest()
        pin = patch.object(M, "BUILDX_SHA256", digest)
        pin.start()
        self.addCleanup(pin.stop)
        self.p = M.make_plan('q03-cpu-test', self.root / 'new', 'moby/buildkit@sha256:' + 'a' * 64, independent_root=self.root)
        self.builder = {'Name': self.p['identity'], 'Driver': 'docker-container',
                        'Nodes': [{'Name': self.p['node'], 'Endpoint': M.ENDPOINT, 'Status': 'running'}]}
        self.container = {'Name': '/' + self.p['container'], 'Id': 'cpu-fixture-only',
                          'Config': {'Image': self.p['image'], 'Env': ['NVIDIA_VISIBLE_DEVICES=void']},
                          'State': {'Running': True}, 'Created': '2026-01-01T00:00:00Z',
                          'HostConfig': {'NetworkMode': 'bridge', 'RestartPolicy': {'Name': 'no'}},
                          'Mounts': [{'Type': 'volume', 'Name': self.p['volume'], 'Destination': '/var/lib/buildkit', 'RW': True}]}
        self.volume = {'Name': self.p['volume'], 'Driver': 'local', 'Options': {}}
        self.calls = []

    def fake(self, argv):
        self.calls.append(argv)
        if 'inspect' not in argv or '--bootstrap' in argv:
            return ''
        if 'buildx' in argv:
            return f"Name: {self.builder['Name']}\nDriver: {self.builder['Driver']}\nNodes:\nName: {self.p['node']}\nEndpoint: {M.ENDPOINT}\nStatus: running\n"
        if 'container' in argv:
            return json.dumps([self.container])
        return json.dumps([self.volume])

    def test_provision_and_readonly_reverify(self):
        r = M.provision(self.p, self.fake, buildx_binary=self.staged)
        self.assertTrue(r['verified'])
        self.assertTrue((Path(self.p['root']) / 'provision-receipt.json').exists())
        self.calls.clear()
        self.assertEqual(r, M.verify_live(self.p, self.fake))
        self.assertTrue(all('inspect' in c and '--bootstrap' not in c for c in self.calls))

    def test_no_use_delete_or_global_change(self):
        M.provision(self.p, self.fake, buildx_binary=self.staged)
        flat = [x for c in self.calls for x in c]
        self.assertFalse(set(flat) & {'--use', 'rm', 'prune', '--append', '--leave'})
        self.assertIn('network=bridge', flat)
        self.assertIn('restart-policy=no', flat)
        self.assertIn('--oci-worker-net bridge --allow-insecure-entitlement network.host', flat)

    def test_reuse_refused_before_commands(self):
        Path(self.p['root']).mkdir()
        with self.assertRaises(ValueError):
            M.provision(self.p, self.fake, buildx_binary=self.staged)
        self.assertEqual(self.calls, [])

    def test_existing_docker_resource_refused(self):
        for name in (self.p['container'], self.p['volume']):
            with self.subTest(name=name), self.assertRaises(ValueError):
                M.provision(self.p, lambda argv: name + '\n', buildx_binary=self.staged)
        self.assertFalse(Path(self.p['root']).exists())

    def test_failed_inventory_not_absence(self):
        def fail(argv):
            raise RuntimeError('daemon unavailable fixture')
        with self.assertRaises(RuntimeError):
            M.provision(self.p, fail, buildx_binary=self.staged)
        self.assertFalse(Path(self.p['root']).exists())

    def test_protected_mount_or_gpu_refused(self):
        for key, value in [('Binds', ['/protected-fixture:/cache']), ('DeviceRequests', [{'Count': -1}]), ('Devices', ['/dev/nvidia0']), ('VolumesFrom', ['original'])]:
            c = copy.deepcopy(self.container)
            c['HostConfig'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                M.verify_state(self.p, self.builder, c, self.volume)

    def test_wrong_driver_endpoint_and_volume(self):
        for obj, key, val in [(self.builder, 'Driver', 'docker'), (self.volume, 'Options', {'device': '/protected-fixture'}), (self.volume, 'Name', 'shared')]:
            old = obj[key]
            obj[key] = val
            with self.assertRaises(ValueError):
                M.verify_state(self.p, self.builder, self.container, self.volume)
            obj[key] = old
        self.builder['Nodes'][0]['Endpoint'] = 'ssh://production'
        with self.assertRaises(ValueError):
            M.verify_state(self.p, self.builder, self.container, self.volume)

    def test_environment_overrides_removed(self):
        with patch.dict(M.os.environ, {'DOCKER_CONTEXT': 'original', 'BUILDKIT_HOST': 'tcp://elsewhere', 'BUILDX_BUILDER': 'default'}):
            env = M.clean_environment(self.p)
        self.assertNotIn('DOCKER_CONTEXT', env)
        self.assertNotIn('BUILDKIT_HOST', env)
        self.assertEqual(env['BUILDX_BUILDER'], self.p['identity'])
        self.assertEqual(env['DOCKER_HOST'], M.ENDPOINT)

    def test_input_and_tampered_plan_refused(self):
        for identity, root, image in [('default', self.root / 'new', self.p['image']), ('q03-test', '/protected-fixture', self.p['image']), ('q03-test', self.root / 'new', 'moby/buildkit:latest')]:
            with self.assertRaises(ValueError):
                M.make_plan(identity, root, image, independent_root=self.root)
        self.p['create'].append('--use')
        with self.assertRaises(ValueError):
            M.provision(self.p, self.fake, buildx_binary=self.staged)
        self.assertEqual(self.calls, [])

    def test_inspect_parser_fails_closed(self):
        for text in ('', 'Name: one\nDriver: docker-container\nNodes:\nName: one\nName: two\nEndpoint: x\nStatus: running'):
            with self.assertRaises(ValueError):
                M.parse_builder_inspect(text)

    def test_labels_are_observed_not_invented(self):
        result = M.verify_state(self.p, self.builder, self.container, self.volume)
        self.assertEqual(result['ownership_labels'], {})
        self.container['Config']['Labels'] = {'fixture.owner': 'fixture-value'}
        result = M.verify_state(self.p, self.builder, self.container, self.volume)
        self.assertEqual(result['ownership_labels'], {'fixture.owner': 'fixture-value'})

    def test_stopped_cleanup_identity_and_drift(self):
        expected = M.verify_state(self.p, self.builder, self.container, self.volume)
        self.container['State']['Running'] = False
        self.assertEqual(M.verify_owned_state(self.p, self.container, self.volume, expected), expected)
        with self.assertRaises(ValueError):
            M.verify_state(self.p, self.builder, self.container, self.volume)
        self.container['Created'] = '2026-01-02T00:00:00Z'
        with self.assertRaises(ValueError):
            M.verify_owned_state(self.p, self.container, self.volume, expected)
        self.container['Created'] = expected['created']
        self.container['Config']['Env'].append('UNEXPECTED=value')
        with self.assertRaises(ValueError):
            M.verify_owned_state(self.p, self.container, self.volume, expected)

    def test_changed_daemon_config_refused(self):
        M.provision(self.p, self.fake, buildx_binary=self.staged)
        (Path(self.p['root']) / 'buildkitd.toml').write_text('changed')
        with self.assertRaises(ValueError):
            M.verify_live(self.p, self.fake)


if __name__ == '__main__':
    unittest.main()
