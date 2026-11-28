"""A promotion writes and rolls back the same window's control journal."""
import contextlib
from pathlib import Path
import runpy
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PROMOTION = runpy.run_path(str(ROOT / "host/site/install_controls.py"))
WINDOW = "a5ca5627-7f38-414b-8ed5-3513c8902f71"


class PromotionInstallerTests(unittest.TestCase):
    def test_prepare_install_and_rollback_use_the_same_scoped_transaction(self):
        transact = PROMOTION["transact"]
        controls = mock.Mock()
        controls.locks.side_effect = contextlib.nullcontext
        with mock.patch.object(PROMOTION["importlib"].util, "spec_from_file_location") as spec, \
                mock.patch.object(PROMOTION["importlib"].util, "module_from_spec", return_value=controls), \
                mock.patch.object(PROMOTION["os"], "geteuid", return_value=0):
            for action in ("prepare", "install", "rollback"):
                transact(action, WINDOW)
                self.assertEqual(controls.ROOT, ROOT)
                self.assertEqual(controls.OWNER, "pr60-tp4-promotion")
                self.assertEqual(controls.OP, Path("/srv/ai-cluster/operations/pr60-tp4-promotion") / WINDOW)
            self.assertEqual(spec.call_args.args[-1], PROMOTION["TRANSACTION"])
            controls.prepare.assert_called_once_with(WINDOW)
            controls.install.assert_called_once_with(WINDOW)
            controls.record_for.assert_called_once_with(WINDOW)
            controls.restore.assert_called_once_with(controls.record_for.return_value)
            self.assertEqual(controls.locks.call_count, 3)

    def test_noncanonical_window_is_refused_before_loading_the_transaction(self):
        with mock.patch.object(PROMOTION["importlib"].util, "spec_from_file_location") as spec:
            for invalid in (WINDOW.upper(), "../" + WINDOW):
                with self.subTest(window=invalid), self.assertRaises(ValueError):
                    PROMOTION["transact"]("prepare", invalid)
            spec.assert_not_called()


if __name__ == "__main__":
    unittest.main()
