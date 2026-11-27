import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.check_json import check


class JsonValidationTest(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(directory)
        self.receipt = self.root / "experiments/old/receipts/run.json"
        self.receipt.parent.mkdir(parents=True)
        self.raw = b'{"step":1}\n{"step":2}\n'
        self.receipt.write_bytes(self.raw)
        self.name = self.receipt.relative_to(self.root).as_posix()
        self.exceptions = {self.name: {
            "sha256": hashlib.sha256(self.raw).hexdigest(), "reason": "Recorded JSONL stdout",
        }}

    def test_reviewed_log_passes_and_unreviewed_jsonl_fails(self):
        self.assertEqual(check(self.root, self.exceptions), ([], 1))
        self.assertIn(self.name, check(self.root, {})[0][0])

    def test_changed_or_missing_receipt_fails(self):
        self.receipt.write_bytes(self.raw + b'{"step":3}\n')
        self.assertIn("SHA-256", check(self.root, self.exceptions)[0][0])
        self.receipt.unlink()
        self.assertIn("missing", check(self.root, self.exceptions)[0][0])

    def test_invalid_configuration_cannot_be_exempted(self):
        path = self.root / "config/cluster.json"
        path.parent.mkdir()
        path.write_bytes(self.raw)
        problems, _ = check(self.root, self.exceptions)
        self.assertIn("config/cluster.json", problems[0])
        exceptions = dict(self.exceptions)
        exceptions["config/cluster.json"] = self.exceptions[self.name]
        self.assertIn("recorded receipt", check(self.root, exceptions)[0][0])

    def test_valid_configuration_and_named_parse_error(self):
        path = self.root / "config/cluster.json"
        path.parent.mkdir()
        path.write_text(json.dumps({"shape": "tp4-ring"}))
        self.assertEqual(check(self.root, self.exceptions), ([], 2))
        path.write_text("{")
        self.assertIn("config/cluster.json", check(self.root, self.exceptions)[0][0])
