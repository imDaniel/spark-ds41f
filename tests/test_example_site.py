"""The example site: every shape of the recipe has an example node map, and writing
them never touches a site's own maps."""

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import example_site  # noqa: E402


class ExampleSiteTest(unittest.TestCase):
    def test_every_shape_has_an_example_node_map(self) -> None:
        # A new shape whose node map has no example would leave CI unable to resolve it.
        cluster = json.loads((ROOT / "config/cluster.json").read_text())
        for name in example_site.node_maps(cluster):
            self.assertIn(name, example_site.EXAMPLES)
            self.assertTrue((ROOT / example_site.EXAMPLES[name]).is_file(), example_site.EXAMPLES[name])

    def test_missing_maps_get_the_recipe_head_and_existing_maps_are_kept(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("config/cluster.json", *example_site.EXAMPLES.values()):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(ROOT / name, root / name)
            own = root / example_site.NODES_FILE
            own.write_text('{"site": "own"}\n')
            with mock.patch.object(example_site, "ROOT", root), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(example_site.main(), 0)
            self.assertEqual(own.read_text(), '{"site": "own"}\n')
            address = json.loads((root / "config/cluster.json").read_text())["distributed"]["master_addr"]
            ring = json.loads((root / "config/nodes-ring4.local.json").read_text())
            self.assertEqual([node["management_ip"] for node in ring["nodes"] if node.get("head")], [address])


if __name__ == "__main__":
    unittest.main()
