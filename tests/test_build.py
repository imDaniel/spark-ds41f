"""Host-independent tests for the build directory identity."""

from __future__ import annotations

import copy
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("spark", str(ROOT / "bin" / "spark"))
spec = importlib.util.spec_from_loader("spark", loader)
spark = importlib.util.module_from_spec(spec)
loader.exec_module(spark)


class BuildDirectoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.inputs = spark.build_inputs(spark.read_json("upstreams.lock.json"))

    def test_inputs_come_from_the_lock_and_source_manifest(self) -> None:
        upstreams = spark.read_json("upstreams.lock.json")
        manifest = spark.read_json(upstreams["source_manifest"])
        for name in spark.BUILD_PROJECTS:
            self.assertEqual(self.inputs[name]["revision"], upstreams["sources"][name]["revision"])
            self.assertEqual(self.inputs[name]["expected_tree"], manifest[name]["expected_tree"])
            self.assertEqual(self.inputs[name]["patch_head"], manifest[name]["patch_head"])

    def test_directory_is_stable_and_follows_every_input(self) -> None:
        directory = spark.build_directory(self.inputs)
        self.assertEqual(directory, spark.build_directory(copy.deepcopy(self.inputs)))
        self.assertEqual(directory.parent, ROOT / ".work/build")
        self.assertTrue(directory.name.startswith(
            f"vllm-{self.inputs['vllm']['revision'][:12]}-b12x-{self.inputs['b12x']['revision'][:12]}-"
        ))
        changed = copy.deepcopy(self.inputs)
        changed["cutlass"]["revision"] = "0" * 40
        self.assertNotEqual(directory, spark.build_directory(changed))

    def test_tree_digest_ignores_git_metadata(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "a.py").write_text("x = 1\n")
            before = spark.tree_digest(root)
            (root / ".git").mkdir()
            (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
            self.assertEqual(before, spark.tree_digest(root))
            (root / "a.py").write_text("x = 2\n")
            self.assertNotEqual(before, spark.tree_digest(root))

    def test_normalize_modes_uses_git_modes(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "d").mkdir(mode=0o775)
            plain = root / "d" / "plain.txt"
            plain.write_text("x")
            plain.chmod(0o664)
            tool = root / "tool.sh"
            tool.write_text("#!/bin/sh\n")
            tool.chmod(0o775)
            spark.normalize_modes(root)
            self.assertEqual(plain.stat().st_mode & 0o777, 0o644)
            self.assertEqual(tool.stat().st_mode & 0o777, 0o755)
            self.assertEqual((root / "d").stat().st_mode & 0o777, 0o755)


class PatchReplayTest(unittest.TestCase):
    def test_replay_and_repair_without_automatic_maintenance(self) -> None:
        """A real local upstream reproduces its head despite host auto-GC settings."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            upstream = root / "upstream"
            upstream.mkdir()

            def git(*args: str) -> str:
                return subprocess.check_output(
                    ["git", "-c", "maintenance.auto=false", "-c", "gc.auto=0", *args],
                    cwd=upstream, text=True, stderr=subprocess.PIPE,
                ).strip()

            git("init", "--quiet")
            git("config", "user.name", spark.PATCH_COMMITTER[0])
            git("config", "user.email", spark.PATCH_COMMITTER[1])
            (upstream / "base.txt").write_text("base\n")
            git("add", ".")
            git("commit", "--quiet", "-m", "Base")
            base = git("rev-parse", "HEAD")
            patches = root / "patches"
            patches.mkdir()
            for number in (1, 2):
                (upstream / f"added-{number}.txt").write_text(f"patch {number}\n")
                git("add", ".")
                # Both identities and dates match prepare_project's replay contract.
                date = f"2020-01-0{number}T00:00:00+00:00"
                with patch.dict(os.environ, {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}):
                    git("commit", "--quiet", "-m", f"Add file {number}")
                (patches / f"{number}.patch").write_text(git("format-patch", "-1", "--stdout") + "\n")
            (patches / "series").write_text("1.patch\n2.patch\n")
            record = {
                "upstream": upstream.as_uri(), "revision": base,
                "patch_series": "patches/series",
                "patch_head": git("rev-parse", "HEAD"),
                "expected_tree": git("rev-parse", "HEAD^{tree}"),
            }
            host_config = root / "host.gitconfig"
            host_config.write_text("[maintenance]\n\tauto = true\n[gc]\n\tauto = 1\n")
            trace = root / "trace.jsonl"
            env = {
                "GIT_CONFIG_GLOBAL": str(host_config), "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TRACE2_EVENT": str(trace),
            }
            directory = root / ".work/build/test"
            with patch.object(spark, "ROOT", root), patch.dict(os.environ, env):
                spark.prepare_project(directory, "fixture", record)
                source = directory / "src/fixture"
                self.assertEqual(spark.project_problems(source, record), [])
                self.assertEqual(spark.git_output(source, "config", "--local", "maintenance.auto"), "false")
                self.assertEqual(spark.git_output(source, "config", "--local", "gc.auto"), "0")
                (source / "added-1.txt").write_text("damaged\n")
                spark.prepare_project(directory, "fixture", record)
                self.assertEqual(spark.project_problems(source, record), [])
                self.assertEqual((source / "added-1.txt").read_text(), "patch 1\n")
                exported = directory / "context/fixture-source"
                spark.export_source(source, exported)
                self.assertFalse((exported / ".git").exists())
                self.assertEqual((exported / "added-2.txt").read_text(), "patch 2\n")
            children = [json.loads(line).get("argv", []) for line in trace.read_text().splitlines()
                        if json.loads(line).get("event") == "child_start"]
            self.assertFalse(any("maintenance" in argv or "gc" in argv for argv in children))
            self.assertEqual(host_config.read_text(), "[maintenance]\n\tauto = true\n[gc]\n\tauto = 1\n")


if __name__ == "__main__":
    unittest.main()
