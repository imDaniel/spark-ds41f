"""Pure profile derivation tests; no device, Docker or service calls."""
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('q03_profile', ROOT / 'experiments/2026-10-21-mhc-input-window/create_build_profile.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BuildProfileTest(unittest.TestCase):
    def setUp(self):
        self.profile = {'promoted_baseline': 'original-live-baseline', 'shape': 'tp4-ring', 'host': {'example': 'preserved'},
                        'serve_args': ['--port', '8000', '--max-num-seqs', '16'],
                        'container': {'mounts': [['/fixture/model', '/model', 'ro'], ['/fixture/cache', '/cache', 'rw'], ['/fixture/profile', '/profile', 'ro']], 'expected_labels': {}},
                        'deployment': {}, 'environment': {}, 'distributed': {}}
        lock = json.loads((ROOT / 'experiments/2026-10-21-mhc-input-validation/upstreams.lock.json').read_text())
        self.kw = dict(baseline=lock['generated_from_baseline'], checkout=Path('/candidate-fixture/deployment'), cache=Path('/candidate-fixture/cache'), image='q03-validation:test-fresh', tree='a' * 40, port=18003, master_port=19993, protected_ports=[8000, 8100, 9999])

    def test_preserves_original_and_site_contract(self):
        before = json.dumps(self.profile, sort_keys=True)
        out = MODULE.derive(self.profile, **self.kw)
        self.assertEqual(json.dumps(self.profile, sort_keys=True), before)
        self.assertEqual(out['promoted_baseline'], self.kw['baseline'])
        self.assertEqual(self.profile['promoted_baseline'], 'original-live-baseline')
        self.assertEqual(out['container']['expected_labels']['local.spark3.vllm.tree'], self.kw['tree'])
        self.assertEqual(out['deployment']['path'], str(self.kw['checkout']))
        self.assertEqual(out['host'], self.profile['host'])
        self.assertEqual(out['container']['mounts'][0], self.profile['container']['mounts'][0])
        self.assertEqual(out['container']['mounts'][2], self.profile['container']['mounts'][2])
        self.assertFalse(out['deployment']['launch_enabled'])
        self.assertEqual(out['environment']['VLLM_DS41_Q03_MHC_MXFP8'], '0')
        self.assertEqual(out['container']['mounts'][1][0], str(self.kw['cache']))

    def test_baseline_is_required_not_silently_inherited(self):
        missing = dict(self.kw)
        missing.pop('baseline')
        with self.assertRaises(TypeError):
            MODULE.derive(self.profile, **missing)
        self.assertEqual(self.profile['promoted_baseline'], 'original-live-baseline')

    def test_main_reads_selected_lock_and_preserves_source_profile(self):
        import contextlib
        import hashlib
        import io
        import subprocess
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            checkout = root / 'deployment'
            experiment = checkout / 'experiments/2026-10-21-mhc-input-validation'
            experiment.mkdir(parents=True)
            (checkout / 'config').mkdir()
            public = ROOT / 'experiments/2026-10-21-mhc-input-validation'
            for name in ('source.json', 'upstreams.lock.json'):
                (experiment / name).write_bytes((public / name).read_bytes())
            source = json.loads((public / 'source.json').read_text())
            profile = root / 'original-profile.json'
            profile.write_text(json.dumps(self.profile))
            before = profile.read_bytes()
            node_map = root / 'synthetic-map.json'
            node_map.write_text(json.dumps({'nodes': [{'rank': i} for i in range(4)]}))
            argv = ['create_build_profile.py', '--source-profile', str(profile),
                    '--source-map', str(node_map), '--checkout', str(checkout),
                    '--cache', str(root / 'cache'), '--independent-root', str(root),
                    '--profile-sha256', hashlib.sha256(before).hexdigest(),
                    '--map-sha256', hashlib.sha256(node_map.read_bytes()).hexdigest(),
                    '--image-tag', self.kw['image'], '--source-tree', source['vllm']['expected_tree'],
                    '--protected-root', '/synthetic/protected', '--protected-port', '8000',
                    '--http-port', '18003', '--master-port', '19993']
            with mock.patch('sys.argv', argv), contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch.object(subprocess, 'run', side_effect=AssertionError('external process forbidden')), \
                    mock.patch.object(subprocess, 'Popen', side_effect=AssertionError('external process forbidden')):
                MODULE.main()
            output = json.loads((checkout / 'config/q03-window.local.json').read_text())
            self.assertEqual(output['promoted_baseline'], self.kw['baseline'])
            self.assertEqual(profile.read_bytes(), before)
            self.assertEqual(output['container']['expected_labels']['local.spark3.vllm.tree'], source['vllm']['expected_tree'])
            self.assertEqual(output['deployment']['path'], str(checkout))

    def test_known_port_collision_rejected(self):
        for key, value in [(key, value) for key in ('port', 'master_port') for value in (8000, 8100, 9999)]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                MODULE.derive(self.profile, **{**self.kw, key: value})

    def test_owner_baseline_capacity_preserved(self):
        self.profile['serve_args'][self.profile['serve_args'].index('--max-num-seqs')+1] = '8'
        result = MODULE.derive(self.profile, **self.kw)
        self.assertEqual(result['serve_args'][result['serve_args'].index('--max-num-seqs')+1], '8')

    def test_extra_writable_mount_rejected(self):
        self.profile['container']['mounts'].append(['/protected', '/other', 'rw'])
        with self.assertRaises(ValueError):
            MODULE.derive(self.profile, **self.kw)

    def test_non_candidate_tag_rejected(self):
        with self.assertRaises(ValueError):
            MODULE.derive(self.profile, **{**self.kw, 'image': 'production:latest'})


if __name__ == '__main__':
    unittest.main()
