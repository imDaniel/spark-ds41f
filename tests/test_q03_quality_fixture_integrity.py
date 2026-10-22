"""Public source/data fixture integrity, not tokenizer or model execution."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import q03_quality_data_score as S
from quality_test_fixtures import MATERIAL, isolated_servebench_modules, toolbox


class QualityFixtureTests(unittest.TestCase):
    def test_failed_pilot_setup_restores_finders_and_ambient_modules(self):
        import test_q03_quality_pilot as pilot
        selected = lambda name: name == 'servebench' or name.startswith('servebench.')
        original = {k: v for k, v in sys.modules.items() if selected(k)}
        finders = list(sys.meta_path)
        helper = pilot.PilotTests('test_all_sixty_live_tokens_then_natural_generation_and_full_rehash')
        try:
            with patch.object(pilot.importlib, 'import_module',
                              side_effect=ImportError('synthetic fixture import failure')):
                with self.assertRaisesRegex(ImportError, 'synthetic fixture import failure'):
                    helper.setUp()
        finally:
            self.assertTrue(helper.doCleanups())
        self.assertEqual(sys.meta_path, finders)
        self.assertEqual({k: v for k, v in sys.modules.items() if selected(k)}, original)

    def test_module_scope_restores_ambient_objects_after_success_and_failure(self):
        selected = lambda name: name == 'servebench' or name.startswith('servebench.')
        for fail in (False, True):
            with self.subTest(fail=fail), patch.dict(sys.modules, {
                    'servebench': ModuleType('servebench'),
                    'servebench.client': ModuleType('servebench.client')}):
                expected = {k: v for k, v in sys.modules.items() if selected(k)}
                try:
                    with isolated_servebench_modules():
                        self.assertFalse(any(selected(k) for k in sys.modules))
                        sys.modules['servebench.fixture_only'] = ModuleType('servebench.fixture_only')
                        if fail:
                            raise RuntimeError('synthetic fixture failure')
                except RuntimeError as error:
                    self.assertTrue(fail)
                    self.assertEqual(str(error), 'synthetic fixture failure')
                self.assertEqual({k: v for k, v in sys.modules.items() if selected(k)}, expected)

    def test_all_seven_consumed_material_files_are_exact(self):
        for name, (size, pin) in S.MATERIAL_FILES.items():
            with self.subTest(name=name):
                raw = (MATERIAL / name).read_bytes()
                self.assertEqual(len(raw), size)
                self.assertEqual(hashlib.sha256(raw).hexdigest(), pin)
        self.assertEqual(len(S.load_material(MATERIAL)), 60)

    def test_committed_subset_is_not_claimed_as_complete_producer_material(self):
        material = ROOT / 'tests/fixtures/r6x-quality-pilot-r2'
        self.assertEqual({p.name for p in material.iterdir()},
                         set(S.MATERIAL_FILES) | {'LICENSE', 'README.md'})
        manifest = (material / 'SHA256SUMS').read_text().splitlines()
        self.assertEqual(len(manifest), 34)
        self.assertFalse((material / 'reference_code.py').exists())
        self.assertFalse((material / 'portable-preparation.json').exists())

    def test_local_git_object_toolbox_matches_frozen_source_inventory(self):
        root = toolbox()
        inventory = json.loads((ROOT / 'scripts/q03_http_toolbox.json').read_bytes())
        for name, pin in inventory['files'].items():
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), pin)

    def test_explicit_toolbox_override_rejects_changed_source_before_import(self):
        original = toolbox()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'toolbox'
            inventory = json.loads((ROOT / 'scripts/q03_http_toolbox.json').read_bytes())
            for relative in inventory['files']:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((original / relative).read_bytes())
            (root / 'servebench/__init__.py').write_bytes(b'CHANGED TEST SOURCE')
            toolbox.cache_clear()
            try:
                with patch.dict(os.environ, {'Q03_TEST_TOOLBOX': str(root)}), \
                        self.assertRaisesRegex(RuntimeError, 'toolbox source differs'):
                    toolbox()
            finally:
                toolbox.cache_clear()


if __name__ == '__main__':
    unittest.main()
