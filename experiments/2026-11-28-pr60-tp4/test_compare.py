"""Offline checks. Archived cohorts used as fixtures never qualify a new image."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("pr60_comparison", Path(__file__).with_name("compare.py"))
comparison = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)


class ComparisonTests(unittest.TestCase):
    def test_published_four_cells_reproduce_exactly(self):
        manifest = json.loads(comparison.REFERENCE.read_text())
        cohorts = [comparison.read_cohort(comparison.ROOT / directory, manifest["identity"]["sonar"])
                   for directory in manifest["native_receipts"]]
        self.assertEqual(cohorts[0]["workloads"], cohorts[1]["workloads"])
        report = comparison.sonar.module("report")
        stats = comparison.sonar.module("stats")
        for published in manifest["comparison"]["comparisons"]:
            target = published["scale"], published["concurrency"]
            actual = comparison.cell_summary(cohorts, target, report, stats)
            self.assertEqual(actual["pooled"], published["candidate"])
            self.assertEqual(actual["boots"], published["candidate_boots"])
            contrast = comparison.metric_comparison("output_tps", actual["boots"], actual["boots"], stats)
            self.assertEqual(len(contrast["boot_pairings"]), 4)
            self.assertEqual(contrast["verdict"], "no-proven-change")

    def test_reject_reused_rank_or_changed_image(self):
        with tempfile.TemporaryDirectory() as top:
            dirs, starts = [], []
            for boot in [1, 2]:
                path = Path(top, str(boot))
                path.mkdir()
                dirs.append(path)
                ranks = {f"spark{i:02}": {"container_id": f"{boot}{i}".zfill(64),
                                          "started_at": f"2026-11-28T00:00:0{boot}.000000000Z"} for i in range(1, 5)}
                starts.append({"deployment_commit": "1" * 40, "vllm_tree": "2" * 40, "profile_sha256": "3" * 64,
                               "image_id": "sha256:" + "4" * 64, "ranks": ranks})
                (path / "engine-start.json").write_text(json.dumps(starts[-1]))
            original = "sha256:" + "5" * 64
            self.assertEqual(comparison.independent_starts(dirs, original), starts)
            with self.assertRaisesRegex(ValueError, "reference image"):
                comparison.independent_starts(dirs, starts[0]["image_id"])
            starts[1]["ranks"]["spark03"]["container_id"] = starts[0]["ranks"]["spark03"]["container_id"]
            (dirs[1] / "engine-start.json").write_text(json.dumps(starts[1]))
            with self.assertRaisesRegex(ValueError, "reused container"):
                comparison.independent_starts(dirs, original)
            del starts[1]["ranks"]["spark04"]
            (dirs[1] / "engine-start.json").write_text(json.dumps(starts[1]))
            with self.assertRaisesRegex(ValueError, "rank identity"):
                comparison.independent_starts(dirs, original)


if __name__ == "__main__":
    unittest.main()
