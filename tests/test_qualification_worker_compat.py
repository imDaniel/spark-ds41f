"""The test-only adapter must recognize old dummy batches without accepting real ones."""

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "experiments/2026-10-19-steering-gpu/qualification_worker.py"


def load_guard():
    # Exercise the actual host guard without importing CUDA or Torch into the
    # deployment suite. Image validation separately runs real InputBatch makers.
    tree = ast.parse(SOURCE.read_text())
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_is_dummy_batch"
    )
    module = ast.Module(body=[function], type_ignores=[])
    namespace = {}
    exec(compile(module, str(SOURCE), "exec"), namespace)  # noqa: S102 - trusted source AST
    return namespace["_is_dummy_batch"]


class QualificationCompatibilityTest(unittest.TestCase):
    def setUp(self):
        self.guard = load_guard()
        self.states = SimpleNamespace(req_id_to_index={})

    def test_current_marker_is_authoritative(self):
        self.assertTrue(self.guard(SimpleNamespace(is_dummy=True), self.states))
        self.assertFalse(self.guard(
            SimpleNamespace(is_dummy=False, req_ids=["req_0_abcdef"]), self.states
        ))

    def test_legacy_dummy_ids_must_be_unregistered_and_indexed(self):
        batch = SimpleNamespace(req_ids=["req_0_abcdef", "req_1_fedcba"])
        self.assertTrue(self.guard(batch, self.states))
        self.states.req_id_to_index["req_0_abcdef"] = 0
        self.assertFalse(self.guard(batch, self.states))
        for ids in ([], ["cmpl-real"], ["req_0_"], ["req_1_wrong_index"]):
            with self.subTest(ids=ids):
                self.assertFalse(self.guard(SimpleNamespace(req_ids=ids), self.states))
