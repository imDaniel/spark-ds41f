"""Control adoption failures touch only temporary files and mocked host commands."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import install_controls as controls


class ControlAdoptionTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.op = self.root / "operation"; self.op.mkdir()
        self.hold = self.root / "hold"
        self.targets = [self.root / "first.py", self.root / "second.json"]
        self.extra = self.root / "shared-helper.py"
        self.inputs = [*self.targets, self.extra]
        for i, path in enumerate(self.inputs):
            path.write_bytes(f"old-{i}".encode()); path.chmod(0o640)
        self.original = {str(p): controls.file_state(p) for p in self.inputs}
        self.enterContext(patch.object(controls, "OP", self.op))
        self.enterContext(patch.object(controls, "ROOT", self.root))
        self.enterContext(patch.object(controls, "HOLD", self.hold))
        self.enterContext(patch.object(controls, "TARGETS", frozenset(map(str, self.targets))))
        self.enterContext(patch.object(controls, "INPUTS", frozenset(map(str, self.inputs))))
        self.ready = True

        def output(args, **kwargs):
            if args[0] == "git":
                return "published\n" if "rev-parse" in args else b""
            self.assertEqual(args, [controls.BACKEND, "probe"])
            return json.dumps({"ready": self.ready, "failed": not self.ready})
        self.command = self.enterContext(patch.object(controls.subprocess, "check_output", side_effect=output))
        self.enterContext(patch.object(controls.subprocess, "run"))
        self.set_hold()
        self.stage = self.op / "controls-rendered"
        files = {}
        for i, target in enumerate(self.targets):
            path = self.stage / str(target).lstrip("/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"new-{i}".encode())
            files[str(target)] = {"sha256": controls.sha(path), "mode": 0o644}
        self.manifest = dict(revision="published", baseline="qualified", files=files, retire_after_verification=[])
        self.write_manifest()

    def set_hold(self, **changes):
        record = dict(holder=controls.OWNER, window_id="mine", phase="adopting", beat_monotonic=time.monotonic(),
                      boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip())
        self.hold.write_text(json.dumps({**record, **changes}))

    def write_manifest(self):
        (self.stage / "install-manifest.json").write_text(json.dumps(self.manifest))

    def assert_original(self):
        self.assertEqual({str(p): controls.file_state(p) for p in self.inputs}, self.original)

    def test_install_and_repeated_rollback_preserve_original_identity(self):
        controls.prepare("mine")
        self.assertEqual(controls.install("mine")["status"], "installed")
        for target, entry in self.manifest["files"].items():
            self.assertEqual(controls.sha(target), entry["sha256"])
        for _ in range(2):
            self.assertEqual(controls.restore(controls.record_for("mine"))["status"], "rolled_back")
            self.assert_original()

    def test_failed_readiness_rolls_back_every_file(self):
        controls.prepare("mine"); self.ready = False
        with self.assertRaisesRegex(RuntimeError, "not ready"):
            controls.install("mine")
        self.assert_original()
        self.assertEqual(controls.record_for("mine")["status"], "rolled_back")

    def test_failure_midway_through_replacement_rolls_back(self):
        controls.prepare("mine")
        original_atomic = controls.atomic
        replacements = 0

        def replace(path, *args):
            nonlocal replacements
            if Path(path) in self.targets:
                replacements += 1
                if replacements == 2:
                    raise OSError("injected replacement failure")
            return original_atomic(path, *args)
        with patch.object(controls, "atomic", side_effect=replace):
            with self.assertRaisesRegex(OSError, "injected"):
                controls.install("mine")
        self.assert_original()

    def test_crashed_install_is_recovered_from_its_journal(self):
        record = controls.prepare("mine")
        record.update(status="installing", after=self.manifest["files"])
        controls.journal(record)
        target = self.targets[0]
        old = self.original[str(target)]
        controls.atomic(target, b"new-0", 0o644, old["uid"], old["gid"])
        self.set_hold(phase="recovering", beat_monotonic=0, boot_id="previous-boot")
        controls.restore(controls.record_for("mine"))
        self.assert_original()

    def test_unknown_later_edit_is_preserved_before_any_rollback(self):
        controls.prepare("mine"); controls.install("mine")
        self.targets[0].write_bytes(b"unrelated later edit")
        with self.assertRaisesRegex(RuntimeError, "outside this transaction"):
            controls.restore(controls.record_for("mine"))
        self.assertEqual(self.targets[0].read_bytes(), b"unrelated later edit")
        self.assertEqual(self.targets[1].read_bytes(), b"new-1")

    def test_backup_tampering_prevents_all_replacements(self):
        controls.prepare("mine"); controls.install("mine")
        backup = self.op / "controls-backup" / str(self.targets[0]).lstrip("/")
        backup.write_bytes(b"wrong backup")
        with self.assertRaisesRegex(RuntimeError, "backup changed"):
            controls.restore(controls.record_for("mine"))
        self.assertEqual(self.targets[0].read_bytes(), b"new-0")
        self.assertEqual(self.targets[1].read_bytes(), b"new-1")

    def test_permission_drift_is_preserved(self):
        controls.prepare("mine"); controls.install("mine")
        self.targets[0].chmod(0o600)
        with self.assertRaisesRegex(RuntimeError, "outside this transaction"):
            controls.restore(controls.record_for("mine"))
        self.assertEqual(self.targets[0].stat().st_mode & 0o777, 0o600)

    def test_foreign_lease_and_captured_input_drift_refuse_install(self):
        controls.prepare("mine")
        self.set_hold(holder="other")
        with self.assertRaisesRegex(RuntimeError, "Another window"):
            controls.install("mine")
        self.assert_original()
        self.set_hold(); self.extra.write_bytes(b"external helper update")
        with self.assertRaisesRegex(RuntimeError, "captured host input changed"):
            controls.install("mine")
        self.assertEqual(self.targets[0].read_bytes(), b"old-0")

    def test_unpublished_source_and_extra_targets_refuse_install(self):
        controls.prepare("mine")
        self.manifest["revision"] = "unpublished"; self.write_manifest()
        with self.assertRaisesRegex(RuntimeError, "exact control source"):
            controls.install("mine")
        self.assert_original()
        self.manifest["files"][str(self.root / "unrelated")] = {"sha256": "other", "mode": 0o644}
        self.write_manifest()
        with self.assertRaisesRegex(RuntimeError, "installation scope"):
            controls.install("mine")
        self.assert_original()

    def test_expired_or_rebooted_lease_allows_only_rollback(self):
        controls.prepare("mine")
        for changes in ({"beat_monotonic": time.monotonic() - 1000}, {"boot_id": "previous-boot"}):
            self.set_hold(**changes)
            with self.assertRaisesRegex(RuntimeError, "Recovery has priority"):
                controls.install("mine")
        controls.restore(controls.record_for("mine"))
        self.assert_original()


if __name__ == "__main__":
    unittest.main()
