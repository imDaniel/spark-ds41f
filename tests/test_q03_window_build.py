"""CPU-only build receipt/plan tests. Never invoke Docker, SSH or a GPU."""
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

FILE = Path(__file__).resolve().parents[1] / "scripts/q03_window_build.py"
spec = importlib.util.spec_from_file_location("window_build_under_test", FILE)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class BuildReceiptTests(unittest.TestCase):
    def setUp(self):
        self.sha = "a" * 40
        self.digest = "sha256:" + "b" * 64
        self.tag = "q03-validation:test-unique"
        self.inputs = {"vllm": {"patch_head": build.SOURCE_HEAD, "expected_tree": build.SOURCE_TREE},
                       "tilelang": {"patch_head": "c" * 40, "expected_tree": "d" * 40}}
        labels = {"org.opencontainers.image.revision": self.sha,
                  "org.opencontainers.image.base.digest": "sha256:" + "e" * 64}
        for name, record in self.inputs.items():
            labels["local.spark3." + name + ".patch-head"] = record["patch_head"]
            labels["local.spark3." + name + ".tree"] = record["expected_tree"]
        self.inputs["base_image"] = "official/base@sha256:" + "e" * 64
        self.image = {"Id": self.digest, "Config": {"Labels": labels}}
        self.receipt = {"tag": self.tag, "image_id": self.digest,
                        "deployment_commit": self.sha, "compile_jobs": 4,
                        "min_available_gib": 24.0, "built_utc": "2026-10-21T00:00:00Z"}

    def verify(self):
        return build.verify_image(self.image, self.receipt, self.inputs, self.tag, self.sha)

    def test_exact_receipt(self):
        self.assertEqual(self.verify(), self.digest)

    def test_every_identity_field_refuses_drift(self):
        for key in ("tag", "image_id", "deployment_commit"):
            with self.subTest(key=key):
                original = self.receipt[key]
                self.receipt[key] = "wrong"
                with self.assertRaises(ValueError):
                    self.verify()
                self.receipt[key] = original

    def test_all_project_labels_required(self):
        labels = self.image["Config"]["Labels"]
        for key in tuple(labels):
            with self.subTest(key=key):
                original = labels.pop(key)
                with self.assertRaises(ValueError):
                    self.verify()
                labels[key] = original

    def test_digest_not_tag(self):
        self.image["Id"] = "latest"
        with self.assertRaises(ValueError):
            self.verify()

    def test_unknown_or_insufficient_memory(self):
        for value in (True, None, "24", 23.9, float("nan"), float("inf")):
            with self.subTest(value=value):
                self.receipt["min_available_gib"] = value
                with self.assertRaises(ValueError):
                    self.verify()

    def test_missing_smoke_complete_receipt(self):
        self.receipt["built_utc"] = ""
        with self.assertRaises(ValueError):
            self.verify()

    def test_invalid_compile_jobs(self):
        for jobs in (False, 0, -1, "4"):
            self.receipt["compile_jobs"] = jobs
            with self.assertRaises(ValueError):
                self.verify()

    def test_docker_selectors_cleared(self):
        approved = {"BUILDX_BUILDER": "q03-unique", "DOCKER_HOST": "unix:///var/run/docker.sock"}
        with patch.dict(os.environ, {"BUILDKIT_HOST": "untrusted", "DOCKER_CONTEXT": "production",
                                    "BUILDX_FUTURE_SELECTOR": "other"}, clear=True):
            command = build.environment_prefix(approved)
        self.assertNotIn("untrusted", command)
        self.assertNotIn("production", command)
        self.assertIn("BUILDX_BUILDER=q03-unique", command)
        for key in ("BUILDKIT_HOST", "DOCKER_CONTEXT", "BUILDX_FUTURE_SELECTOR"):
            self.assertEqual(command[command.index(key) - 1], "-u")

    def test_paths_stay_inside_root(self):
        class Ops:
            resolve = staticmethod(lambda path: str(Path(path).resolve()))
        for path in ("/srv/q03-independent", "/srv/protected", "/srv/q03-independent" + "/../escape"):
            with self.assertRaises(ValueError):
                build.inside(path, "/srv/q03-independent", Ops())
        self.assertEqual(build.inside("/srv/q03-independent" + "/checkout/.work/out", "/srv/q03-independent", Ops()),
                         "/srv/q03-independent" + "/checkout/.work/out")

    def test_build_only_before_any_io(self):
        class NoIO:
            def __getattr__(self, name):
                raise AssertionError("No real I/O allowed")
        for kinds in (["p0"], ["build", "g1"], []):
            with self.assertRaises(ValueError):
                build.validate_build({"jobs": [{"kind": kind} for kind in kinds]}, NoIO())

    def test_profile_namespace_and_nested_work_output(self):
        import hashlib
        import json
        checkout = "/srv/q03-independent" + "/checkout"
        inputs = {"vllm": {"revision": "a" * 40, "patch_head": build.SOURCE_HEAD,
                            "expected_tree": build.SOURCE_TREE},
                  "b12x": {"revision": "b" * 40}}
        digest = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
        directory = checkout + "/.work/build/vllm-" + "a" * 12 + "-b12x-" + "b" * 12 + "-" + digest[:8]
        profile = {"upstreams_config": build.EXPERIMENT + "/upstreams.lock.json",
                   "container": {"name": "q03-validation-isolated", "image": self.tag,
                       "expected_labels": {"local.spark3.vllm.tree": build.SOURCE_TREE}}}
        records = {checkout + "/" + build.PROFILE: profile,
                   checkout + "/" + build.EXPERIMENT + "/validation-selection.json":
                       {"source_head": build.SOURCE_HEAD, "source_tree": build.SOURCE_TREE},
                   checkout + "/" + build.EXPERIMENT + "/source.json":
                       {"vllm": {"patch_head": build.SOURCE_HEAD, "expected_tree": build.SOURCE_TREE}},
                   directory + "/inputs.json": {"inputs": inputs}}
        class Ops:
            resolve = staticmethod(lambda path: str(Path(path).resolve()))
            sha256 = staticmethod(lambda path: "0" * 64)
            read_json = staticmethod(lambda path: records[path])
        manifest = {"independent_root": "/srv/q03-independent", "preflight": {"independent_checkout": checkout}, "build": {
            "build_directory": directory, "output_path": checkout + "/.work/results",
            "profile_sha256": "0" * 64, "inputs_sha256": "0" * 64, "tag": self.tag}}
        self.assertEqual(build._context(manifest, Ops())[3], checkout + "/.work/results")
        for output in (directory + "/src/logs", checkout + "/config/logs"):
            manifest["build"]["output_path"] = output
            with self.assertRaises(ValueError):
                build._context(manifest, Ops())

    def test_capture_plan_uses_native_image_not_overlay(self):
        # Parse the fixed capture program without executing any imports.
        import ast
        ast.parse(build._CAPTURE)
        self.assertIn("native_sha256", build._CAPTURE)
        self.assertNotIn("sys.path", build._CAPTURE)
        self.assertNotIn("PYTHONPATH", build._CAPTURE)


if __name__ == "__main__":
    unittest.main()
