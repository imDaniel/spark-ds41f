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
        self.profile = {'shape': 'tp4-ring', 'host': {'example': 'preserved'},
                        'serve_args': ['--port', '8000', '--max-num-seqs', '16'],
                        'container': {'mounts': [['/fixture/model', '/model', 'ro'], ['/fixture/cache', '/cache', 'rw'], ['/fixture/profile', '/profile', 'ro']], 'expected_labels': {}},
                        'deployment': {}, 'environment': {}, 'distributed': {}}
        self.kw = dict(checkout=Path('/candidate-fixture/deployment'), cache=Path('/candidate-fixture/cache'), image='q03-validation:test-fresh', tree='a' * 40, port=18003, master_port=19993, protected_ports=[8000, 8100, 9999])

    def test_preserves_original_and_site_contract(self):
        before = json.dumps(self.profile, sort_keys=True)
        out = MODULE.derive(self.profile, **self.kw)
        self.assertEqual(json.dumps(self.profile, sort_keys=True), before)
        self.assertEqual(out['host'], self.profile['host'])
        self.assertEqual(out['container']['mounts'][0], self.profile['container']['mounts'][0])
        self.assertEqual(out['container']['mounts'][2], self.profile['container']['mounts'][2])
        self.assertFalse(out['deployment']['launch_enabled'])
        self.assertEqual(out['environment']['VLLM_DS41_Q03_MHC_MXFP8'], '0')
        self.assertEqual(out['container']['mounts'][1][0], str(self.kw['cache']))

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
