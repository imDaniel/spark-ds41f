"""Concurrent first callers must receive one fully initialized fabric package."""

import concurrent.futures
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sparknet_topology


class SparknetImportTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.package = self.root / "sparknet/topology"
        self.package.mkdir(parents=True)
        (self.package / "__init__.py").write_text("")
        for name in ("nodes", "render"):
            (self.package / (name + ".py")).write_text("ready = True\n")
        self.revision = "a" * 40
        self.name = "_sparknet_topology_" + self.revision[:12]
        self.lock = {"sources": {"sparknet": {"upstream": "fixture", "revision": self.revision}}}
        self.cache = mock.patch.object(sparknet_topology, "_LOADED", {})
        self.cache.start()
        self.addCleanup(self.cache.stop)
        self.addCleanup(self.clear_modules)

    def clear_modules(self):
        for name in list(sys.modules):
            if name == self.name or name.startswith(self.name + "."):
                del sys.modules[name]

    def test_concurrent_first_callers_share_a_complete_package(self):
        entered, release, second_called = (threading.Event() for _ in range(3))
        calls = []

        def checkout(*_):
            calls.append(threading.get_ident())
            if len(calls) == 1:
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("Fixture release timed out")
            return self.root

        def second():
            second_called.set()
            return sparknet_topology.package(self.lock)

        with mock.patch.object(sparknet_topology, "checkout", side_effect=checkout):
            with concurrent.futures.ThreadPoolExecutor(2) as pool:
                first = pool.submit(sparknet_topology.package, self.lock)
                try:
                    self.assertTrue(entered.wait(2))
                    other = pool.submit(second)
                    self.assertTrue(second_called.wait(2))
                    with self.assertRaises(concurrent.futures.TimeoutError):
                        other.result(timeout=0.1)
                finally:
                    release.set()
                a, b = first.result(timeout=2), other.result(timeout=2)
        self.assertIs(a, b)
        self.assertEqual(len(calls), 1)
        self.assertTrue(a.nodes.ready and a.render.ready)

    def test_failed_import_is_removed_before_retry(self):
        (self.package / "render.py").write_text("raise RuntimeError('fixture failure')\n")
        with mock.patch.object(sparknet_topology, "checkout", return_value=self.root):
            with self.assertRaisesRegex(RuntimeError, "fixture failure"):
                sparknet_topology.package(self.lock)
            self.assertNotIn(self.revision, sparknet_topology._LOADED)
            self.assertFalse(any(n == self.name or n.startswith(self.name + ".") for n in sys.modules))
            (self.package / "render.py").write_text("ready = True\n")
            package = sparknet_topology.package(self.lock)
            self.assertTrue(package.nodes.ready and package.render.ready)


if __name__ == "__main__":
    unittest.main()
