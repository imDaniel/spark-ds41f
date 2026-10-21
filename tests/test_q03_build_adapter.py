"""Pure CPU ownership/fence tests; no Docker, SSH, processes or GPUs launched."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


def module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / 'scripts' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


adapter = module('q03_build_adapter')
preflight = module('q03_window_preflight')


class OwnershipTests(unittest.TestCase):
    def test_smoke_preserves_official_recipe(self):
        command = ['docker', 'run', '--rm', '-i', '--gpus', 'all', '--memory', '48g', '--entrypoint', '/usr/bin/python3', 'q03-validation:unique', '-']
        result = adapter.smoke_create_command(command, 'owned', 'window')
        self.assertEqual(result[-len(command[3:]):], command[3:])
        self.assertEqual(result[:4], ['docker', 'create', '--name', 'owned'])
        self.assertNotIn('--rm', result)

    def test_smoke_foreign_identity_refused(self):
        row = {'Name': '/owned', 'Id': 'a' * 64, 'Config': {'Labels': {'q03.window': 'w', 'q03.role': 'build-smoke'}}}
        self.assertEqual(adapter.owned_smoke(row, 'owned', 'w'), 'a' * 64)
        for args in [('foreign', 'w', None), ('owned', 'other', None), ('owned', 'w', 'b' * 64)]:
            with self.assertRaises(RuntimeError):
                adapter.owned_smoke(row, *args)

    def fixture(self, directory):
        obj = object.__new__(adapter.Adapter)
        obj.directory = Path(directory)
        obj.fence = obj.directory / 'sealed.json'
        obj.window = 'w'
        obj.name = 'smoke-owned'
        obj.builder_id = 'a' * 64
        obj.plan = {'container': 'builder-owned', 'volume': 'state-owned'}
        obj.journal = {}
        obj.lock = contextlib.nullcontext
        obj.save = lambda: None
        return obj

    def test_nested_child_cannot_create_new_session(self):
        with tempfile.TemporaryDirectory() as directory:
            obj = self.fixture(directory)
            obj.journal.update(commands=[], build_group={'pid': 100})
            obj.authorize_launch = lambda: None
            process = SimpleNamespace(pid=101)
            with patch.object(adapter, 'REAL_POPEN', return_value=process) as popen, patch.object(adapter, 'identity', return_value={'pid': 101, 'pgid': 100, 'start_ticks': '1'}):
                obj.spawn(['docker', 'buildx', 'build'], {'start_new_session': True})
            self.assertFalse(popen.call_args.kwargs['start_new_session'])
            self.assertEqual(obj.journal['commands'][0]['state'], 'launched')
            for kwargs in ({'shell': True}, {'process_group': 0}, {'preexec_fn': lambda: None}):
                with self.assertRaises(RuntimeError):
                    obj.spawn(['docker'], kwargs)

    def test_fence_blocks_every_future_spawn(self):
        with tempfile.TemporaryDirectory() as directory:
            obj = self.fixture(directory)
            obj.fence.write_text('{}')
            with patch.object(adapter, 'REAL_POPEN') as popen:
                with self.assertRaisesRegex(RuntimeError, 'fenced'):
                    obj.spawn(['docker'], {})
                popen.assert_not_called()

    def test_pending_create_is_unknown_not_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            obj = self.fixture(directory)
            obj.journal['smoke_create_intent'] = True
            with self.assertRaisesRegex(RuntimeError, 'Unresolved smoke'):
                obj.quiesce(lambda argv: '')
            self.assertTrue(obj.fence.exists())

    def test_failed_or_interrupted_recipe_retains_hold_before_docker(self):
        with tempfile.TemporaryDirectory() as directory:
            obj = self.fixture(directory)
            obj.journal['recipe_launch_intent'] = True
            with self.assertRaisesRegex(RuntimeError, 'manual daemon reconciliation'):
                obj.quiesce(lambda argv: self.fail('Unknown recipe cannot authorize cleanup'))

    def test_completed_recipe_missing_builder_retains_hold(self):
        with tempfile.TemporaryDirectory() as directory:
            obj = self.fixture(directory)
            obj.journal.update(recipe_launch_intent=True, official_build_completed=True)
            with self.assertRaisesRegex(RuntimeError, 'daemon disappeared'):
                obj.quiesce(lambda argv: '')

    def test_live_group_blocks_before_docker(self):
        with tempfile.TemporaryDirectory() as directory:
            obj = self.fixture(directory)
            obj.journal['build_group'] = {'pid': 123}
            with patch.object(adapter, 'group_members', return_value=[456]):
                with self.assertRaisesRegex(RuntimeError, 'not drained'):
                    obj.quiesce(lambda argv: self.fail('Docker must not be queried'))

    def test_absent_containers_idempotent_with_preserved_volume(self):
        with tempfile.TemporaryDirectory() as directory:
            obj = self.fixture(directory)
            calls = []
            def run(argv):
                calls.append(argv)
                if 'volume' in argv:
                    return json.dumps([{'Name': 'state-owned', 'Driver': 'local', 'Options': {}}])
                return ''
            for _ in range(2):
                self.assertTrue(obj.quiesce(run)['safe_to_restore'])
            self.assertFalse(any('rm' in argv for argv in calls))

    def test_builder_cleanup_uses_immutable_verifier_not_running_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            obj = self.fixture(directory)
            obj.receipt = {'result': {'container_id': obj.builder_id}}
            obj.journal.update(recipe_launch_intent=True, official_build_completed=True)
            present = [True]
            removals = []
            row = {'Id': obj.builder_id, 'State': {'Running': False}}
            def verify(plan, container, volume, expected):
                self.assertIs(container['State']['Running'], False)
                self.assertEqual(expected, obj.receipt['result'])
                return expected
            def run(argv):
                if 'volume' in argv:
                    return json.dumps([{'Name': 'state-owned', 'Driver': 'local', 'Options': {}}])
                if 'inspect' in argv:
                    return json.dumps([row])
                if 'rm' in argv:
                    removals.append(argv[-1]); present[0] = False
                    return ''
                if present[0]:
                    return obj.builder_id if '{{.ID}}' in argv else 'builder-owned'
                return ''
            with patch.dict(sys.modules, {'q03_buildkit_plan': SimpleNamespace(verify_owned_state=verify)}):
                self.assertTrue(obj.quiesce(run)['safe_to_restore'])
            self.assertEqual(removals, [obj.builder_id])

    def test_missing_volume_cannot_claim_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            obj = self.fixture(directory)
            with self.assertRaises(RuntimeError):
                obj.quiesce(lambda argv: '[]' if 'volume' in argv else '')

    def test_glm_probe_known_absence_and_unknown_errors(self):
        for error in ('No such object: owned', 'No such container: owned'):
            output = io.StringIO()
            with patch('subprocess.run', return_value=SimpleNamespace(returncode=1, stderr=error)), contextlib.redirect_stdout(output):
                exec(preflight._GLM_PROBE, {'NAME': 'owned'})
            self.assertEqual(json.loads(output.getvalue()), {'exists': False})
        with patch('subprocess.run', return_value=SimpleNamespace(returncode=1, stderr='permission denied')):
            with self.assertRaises(RuntimeError):
                exec(preflight._GLM_PROBE, {'NAME': 'owned'})

    def test_glm_probe_preserves_actual_present_identity(self):
        row = {'Id': 'real-id', 'Image': 'real-image', 'State': {'Running': False}}
        output = io.StringIO()
        with patch('subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps([row]))), contextlib.redirect_stdout(output):
            exec(preflight._GLM_PROBE, {'NAME': 'owned'})
        self.assertEqual(json.loads(output.getvalue()), {'exists': True, 'id': 'real-id', 'image': 'real-image', 'state': {'Running': False}})


if __name__ == '__main__':
    unittest.main()
