"""Real local CPU child/filesystem checks; the executable named docker is a stub.

No Docker binary, daemon, network, GPU, SSH, service or original asset is used.
The stub remains under the per-test temporary directory and receives only the
closed log argv. Existing lifetime-fault tests cover lost/unknown supervisors.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import q03_remote_operation as R


class LogProcessBounds(unittest.TestCase):
    def exercise(self, script, timeout=2):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stub = root / 'docker'
            stub.write_text('#!' + sys.executable + '\n' + script)
            stub.chmod(0o700)
            window, operation, epoch = [uuid.uuid4().hex for _ in range(3)]
            directory = R.create_operation(root, window, operation,
                R.bound_command(['docker', 'logs', '--timestamps', '--', '1' * 64]),
                str(root), timeout, epoch)
            with mock.patch.dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH']):
                terminal = R.supervise(directory, R.LocalOps())
            result = R.seal_epoch(root, window, epoch)
            self.assertTrue(terminal['group_quiescent'])
            self.assertTrue(result['safe_to_overlap'])
            self.assertLessEqual((directory / 'output.log').stat().st_size, R.LOG_STREAM_LIMIT)
            self.assertLessEqual((directory / 'error.log').stat().st_size, R.LOG_STREAM_LIMIT)
            return result['operations'][0]

    def test_real_child_stdout_cap_is_incomplete_even_at_zero_exit(self):
        result = self.exercise('import os\nos.write(1,b"x"*' + str(R.LOG_STREAM_LIMIT) + ')\n')
        self.assertEqual(result['exit_code'], 0)
        self.assertTrue(result['action_succeeded'])
        self.assertTrue(result['log_capture']['truncated'])
        self.assertFalse(result['log_capture']['complete'])

    def test_real_child_stderr_cannot_exceed_kernel_file_limit(self):
        result = self.exercise('import os\nos.write(2,b"x"*' + str(R.LOG_STREAM_LIMIT + 512) +
                               ')\nos.write(2,b"overflow")\n')
        self.assertNotEqual(result['exit_code'], 0)
        self.assertFalse(result['action_succeeded'])
        self.assertTrue(result['log_capture']['truncated'])
        self.assertFalse(result['log_capture']['complete'])

    def test_stalled_reader_has_bounded_group_kill_and_preserved_partial_logs(self):
        result = self.exercise('import os,time\nos.write(1,b"partial stdout")\n'
                               'os.write(2,b"partial stderr")\ntime.sleep(60)\n', timeout=.3)
        self.assertTrue(result['timed_out'])
        self.assertFalse(result['action_succeeded'])
        self.assertFalse(result['log_capture']['complete'])
        self.assertEqual(result['log_capture']['streams']['stdout']['bytes'], len(b'partial stdout'))
        self.assertEqual(result['log_capture']['streams']['stderr']['bytes'], len(b'partial stderr'))


if __name__ == '__main__':
    unittest.main()
