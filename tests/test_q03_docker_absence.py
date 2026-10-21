"""Actual three absence call paths with all process/filesystem I/O mocked."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preflight = load('q03_window_preflight')
window = load('q03_window')
NAME = 'Owned.Target-1'


class AbsenceTests(unittest.TestCase):
    def exercise(self, kind, stderr, stdout='[]\n', code=1):
        result = SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)
        output = io.StringIO()
        def run(argv, **kwargs):
            if 'inspect' in argv:
                return result
            if argv[:2] == ['/usr/bin/systemctl', 'is-active']:
                return SimpleNamespace(returncode=3, stdout='inactive\n', stderr='')
            raise AssertionError('Unexpected subprocess: ' + repr(argv))
        def check_output(argv, **kwargs):
            if argv[-2:] == ['rev-parse', 'HEAD']:
                return 'head\n'
            if argv[-2:] == ['status', '--porcelain']:
                return b''
            raise AssertionError('Unexpected check_output: ' + repr(argv))
        with patch.dict(os.environ, {}, clear=True), patch('subprocess.run', side_effect=run), patch('subprocess.check_output', side_effect=check_output), contextlib.redirect_stdout(output):
            if kind == 'glm':
                exec(preflight._GLM_PROBE, {'NAME': NAME})
                self.assertEqual(json.loads(output.getvalue()), {'exists': False})
            elif kind == 'ds':
                digest = hashlib.sha256(b'fixture').hexdigest()
                policy = dict(repository='/fixture/repo', container=NAME,
                              profile_directory='/fixture/profile', profile_manifest='manifest.json',
                              cluster_config='config.json', config_sha256=digest,
                              manifest_sha256=digest, directions_sha256=digest, revision='head')
                with patch.object(Path, 'read_bytes', return_value=b'fixture'):
                    exec(preflight._DS_NODE_PROBE, {'P': policy, 'NODE': 1})
                row = json.loads(output.getvalue())
                self.assertFalse(row['present'])
                self.assertFalse(row['running'])
                self.assertTrue(row['valid'])
                self.assertEqual(row['errors'], [])
            else:
                state = {'candidate_containers': [NAME], 'candidate_container_ids': {NAME: 'durable-id'}}
                session = SimpleNamespace(state=state, capsule={'window_id': 'window'}, update=lambda **kw: state.update(kw))
                window.RuntimeOps().cleanup_candidates(session)
                self.assertNotIn('possible_inert_candidate_leftovers', state)

    def test_known_case_variants_all_three_paths(self):
        for kind in ('glm', 'ds', 'cleanup'):
            for prefix in ('', 'error: ', 'Error response from daemon: '):
                for phrase in ('No such object: ', 'no such object: ', 'No such container: ', 'no such container: '):
                    for stdout in ('', '[]\n'):
                        with self.subTest(kind=kind, prefix=prefix, phrase=phrase, stdout=stdout):
                            self.exercise(kind, prefix + phrase + NAME + '\n', stdout)

    def test_unknown_and_unrelated_errors_all_three_paths(self):
        errors = ['permission denied', 'Cannot connect to the Docker daemon',
                  'network timeout', 'error: no such object: unrelated',
                  'error: no such object: ' + NAME.lower(),
                  'error: no such object: ' + NAME + '-other',
                  'error: no such object: ' + NAME + '\npermission denied',
                  'warning: no such object: ' + NAME,
                  'error: no such object: ' + NAME.replace('.', 'x')]
        for kind in ('glm', 'ds', 'cleanup'):
            for error in errors:
                with self.subTest(kind=kind, error=error), self.assertRaises(RuntimeError):
                    self.exercise(kind, error)

    def test_invalid_status_or_stdout_all_three_paths(self):
        for kind in ('glm', 'ds', 'cleanup'):
            for code, stdout in ((2, '[]'), (125, ''), (1, '[{}]'), (1, 'not-json')):
                with self.subTest(kind=kind, code=code, stdout=stdout), self.assertRaises(RuntimeError):
                    self.exercise(kind, 'error: no such object: ' + NAME, stdout, code)


if __name__ == '__main__':
    unittest.main()
