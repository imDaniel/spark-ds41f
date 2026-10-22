"""Synthetic bookkeeping only: no answers, model, reference or candidate code."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import q03_quality_data_score as S


class PlannedScopeCounts(unittest.TestCase):
    def report(self, arm, steering):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prompts = b'{"id":"synthetic","text":"synthetic"}\n'
            files = dict(S.MATERIAL_FILES)
            files['prompts.jsonl'] = (len(prompts), S.digest(prompts))
            modes = ['strict-json-equality'] * 36 + ['exact-text'] * 8 + ['python-unit-assertions'] * 8 + ['blind-rubric'] * 8
            cases = [{'id': f'synthetic-{i}', 'case_record_sha256': '1' * 64,
                      'prompt_utf8_sha256': '2' * 64, 'gold': {'mode': mode}}
                     for i, mode in enumerate(modes)]
            # Missing sidecars mean evidence errors, never successful model results.
            records = [{'index': i, 'case_id': case['id']} for i, case in enumerate(cases)]
            bounds = {'response_bytes': 65536, 'header_bytes': 8192}
            identity = {'window_id': 'synthetic', 'attempt_id': 'primary', 'lane_id': 'lane',
                        'plan_sha256': '3' * 64, 'material_sha256': S.MATERIAL_SHA256,
                        'prompts_sha256': S.digest(prompts), 'transport_bounds': bounds,
                        'selection': {'arm': arm, 'steering': steering}, 'context': {}}
            capture = {'schema': 'r6x-quality-capture/1', 'identity': identity,
                       'transport_bounds': bounds, 'records': records}
            for name, value in [('identity.json', identity), ('quality-capture.json', capture),
                                ('case-index.json', records)]:
                (root / name).write_bytes(S.canonical(value))
            (root / 'prompts.jsonl').write_bytes(prompts)
            with patch.object(S, 'load_material', return_value=cases), patch.object(S, 'MATERIAL_FILES', files):
                return S.evaluate(root, root, max_response_bytes=65536)

    def check_report(self, report, planned):
        self.assertEqual(report['planned_pilot_slots'], planned)
        self.assertEqual(report['planned_questions'], 60)
        self.assertEqual(report['this_capture_planned_slots'], 60)
        self.assertEqual(report['counts']['planned'], 60)
        self.assertEqual(report['counts']['evidence_error'], 60)
        self.assertEqual(report['counts']['evaluated'], 0)
        self.assertEqual(report['counts']['comparison_eligible'], 0)
        self.assertEqual(report['overall_quality'], 'NOT_EVALUATED')
        self.assertEqual(report['native_internal_final_extraction']['status'], 'UNBOUND')
        self.assertEqual(len(report['records']), 60)

    def test_revised_seven_cell_inventory_is_420_not_historical_720(self):
        for arm, states in [('r6x-unpatched', ['absent']),
                            ('r6x-steer', ['unloaded', 'loaded0', 'loaded1', 'loaded2', 'loaded3', 'loaded4'])]:
            for state in states:
                with self.subTest(arm=arm, state=state):
                    self.check_report(self.report(arm, state), 420)

    def test_historical_twelve_cell_inventory_remains_720(self):
        for arm in ['r6x188', 'native197-off']:
            for state in ['unloaded', 'loaded0', 'loaded1', 'loaded2', 'loaded3', 'loaded4']:
                with self.subTest(arm=arm, state=state):
                    self.check_report(self.report(arm, state), 720)

    def test_capability_state_mismatch_still_rejected(self):
        for arm, state in [('r6x-unpatched', 'unloaded'), ('r6x-unpatched', 'loaded0'),
                           ('r6x-steer', 'absent'), ('r6x188', 'absent'), ('unknown', 'unloaded')]:
            with self.subTest(arm=arm, state=state):
                with self.assertRaisesRegex(S.EvidenceError, 'unknown arm/state'):
                    self.report(arm, state)


if __name__ == '__main__':
    unittest.main()
