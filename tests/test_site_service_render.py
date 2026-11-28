"""Rendering a new release preserves the site's other services and launcher identity."""
import copy
import hashlib
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
RENDERER = runpy.run_path(str(ROOT / "host/site/render_controls.py"))


class SiteServiceRenderTests(unittest.TestCase):
    def test_current_and_legacy_service_adoption(self):
        for previous in (RENDERER["SERVICE"], RENDERER["OLD_SERVICE"]):
            with self.subTest(previous=previous), tempfile.TemporaryDirectory() as temporary:
                self.render_and_check(Path(temporary), previous)

    def render_and_check(self, temporary, previous):
        source, existing, output = (temporary / name for name in ("source", "existing", "output"))

        def put(root, name, value):
            path = root / name.lstrip("/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value) if isinstance(value, dict) else value)

        service = RENDERER["SERVICE"]
        resources = [f"spark{n:02}/gpu0" for n in range(1, 5)]
        steering = dict(profile_path="/steering/profile", profile_sha256="profile-sha", operator_version="operator")
        cluster = dict(nodes_config="config/nodes.local.json", promoted_baseline="test-promoted",
                       upstreams_config="releases/test-promoted/upstreams.lock.json",
                       deployment={"path": "/fixed/deployment"},
                       host={"home": "/home/admin", "kernel_policy": "config/kernel.json"},
                       serve_args=["--additional-config", json.dumps({"ds41_steering": steering})],
                       container={"name": "ds41f-1126-attention-fixes", "mounts": [["/profiles", "/steering", "ro"]],
                                  "docker_run_args": []})
        put(source, "config/cluster.json", cluster)
        put(source, "config/nodes.local.json", {"nodes": [{"rank": n} for n in range(4)]})
        put(source, "manifests/baselines/test-promoted.json", {"image_id": "sha256:qualified"})
        for name in ["config/kernel.json", "upstreams.lock.json", "releases/test-promoted/upstreams.lock.json",
                     "scripts/helper.py", "bin/spark",
                     "spark_ds41f/cluster.py", "spark_ds41f/nested/module.py",
                     "host/site/deepseek-model-control.py", "host/site/cluster-shutdown.py"]:
            put(source, name, "# fixed input\n")
        unaffected = {"glm": {"name": "GLM", "backend": {"preserve": True}},
                      "monarch-comfy": {"name": "ComfyUI", "resources": ["other"]}}
        registry = dict(services={**copy.deepcopy(unaffected), previous: {
                            "name": "previous", "group": "tp4-llm", "resources": resources,
                            "launcher": {"dir": "~/sparkdash-launchers/" + previous, "port": 8000},
                            "backend": {"timeout": 3900, "api_model": "deepseek-v4.1-flash"}}},
                        modes={"glm": {"name": "GLM", "services": ["glm"]},
                               "deepseek-1022" if previous == service else "previous": {
                                   "name": "previous", "services": [previous]}}, pinned_files={})
        put(existing, "/etc/ai-cluster/engine-control.json", registry)
        put(existing, "/etc/ai-cluster/cluster-power.json", {"schema_version": 1, "pinned_files": {}})
        for name in ["engine-control.py", "engine-power.py", "model-control.py"]:
            put(existing, "/usr/local/libexec/ai-cluster/" + name, "# installed input\n")
        row = dict(id="existing-launcher-id", name="previous", dir="~/sparkdash-launchers/" + previous)
        unrelated_launcher = dict(id="other", name="other", dir="~/other")
        put(existing, "/srv/ai-cluster/config/sparkdash/state/llm-launchers.json",
            {"launchers": {"spark01": [row, unrelated_launcher]}})
        spark = dict(read_cluster=lambda *args: copy.deepcopy(cluster),
                     expected_command=lambda config, node: ["rank", str(node["rank"])],
                     expected_environment=lambda config, node: {"RANK": str(node["rank"])})
        render = RENDERER["render"]
        git = lambda args, **kwargs: "a" * 40 + "\n" if "rev-parse" in args else ""
        with mock.patch.dict(render.__globals__, ROOT=source), \
                mock.patch.object(RENDERER["runpy"], "run_path", return_value=spark), \
                mock.patch.object(RENDERER["subprocess"], "check_output", side_effect=git):
            manifest = render(existing, output)

        def read(name):
            return json.loads((output / name.lstrip("/")).read_text())

        updated = read("/etc/ai-cluster/engine-control.json")
        for key, value in unaffected.items():
            self.assertEqual(updated["services"][key], value)
        self.assertEqual(updated["modes"]["glm"], registry["modes"]["glm"])
        self.assertEqual(updated["modes"]["deepseek-1022"]["name"], "DeepSeek v4.1 Flash 1017")
        if previous != service:
            self.assertNotIn(previous, updated["services"])
            self.assertNotIn("previous", updated["modes"])
        policy = read(RENDERER["POLICY"])
        self.assertEqual(policy["image_id"], "sha256:qualified")
        self.assertEqual(policy["container"], "ds41f-1126-attention-fixes")
        power = read("/etc/ai-cluster/cluster-power.json")
        self.assertEqual(power["pinned_files"][RENDERER["POLICY"]], manifest["files"][RENDERER["POLICY"]]["sha256"])
        for name in ["spark_ds41f/cluster.py", "spark_ds41f/nested/module.py",
                     "releases/test-promoted/upstreams.lock.json"]:
            self.assertEqual(policy["repository_files"][name], hashlib.sha256((source / name).read_bytes()).hexdigest())
        launchers = read("/srv/ai-cluster/config/sparkdash/state/llm-launchers.json")["launchers"]["spark01"]
        self.assertEqual(launchers[1], unrelated_launcher)
        self.assertEqual(launchers[0]["name"], "DeepSeek v4.1 Flash 1017")
        self.assertEqual(launchers[0]["id"], "existing-launcher-id" if previous == service else service)
        if previous == service:
            self.assertEqual(manifest["retire_after_verification"], [])


if __name__ == "__main__":
    unittest.main()
