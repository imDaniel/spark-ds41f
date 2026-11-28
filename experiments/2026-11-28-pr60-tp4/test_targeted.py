"""Check that client concurrency and prefix correctness cannot mask a failed scheduler gate."""
import contextlib
import threading
import unittest
from unittest.mock import patch

import targeted


class MixedCacheTest(unittest.TestCase):
    def simulate(self, peak, corrupt=False):
        self.rows = []
        seen = set()
        released = threading.Event()

        def complete(base, tokens, scale, salt, barrier=None, length=targeted.PROBE_TOKENS):
            key = (scale or 0, salt)
            cached = 8192 if key in seen else 0
            seen.add(key)
            ids = [key[0]] * length
            if barrier:
                barrier.wait(timeout=3)
                self.assertTrue(released.wait(timeout=3))
                if corrupt and scale == 4:
                    ids[0] += 1
            return dict(scale=scale, salt=salt, token_ids=ids, cached=cached, started=1, ended=2)

        def sample(base):
            released.set()
            return {"num_requests_running": peak, "num_requests_waiting": 4 - peak}

        with contextlib.ExitStack() as stack:
            for name, fake in (("post", lambda *a: {"tokens": [42] * 32768}), ("complete", complete),
                               ("request_counts", sample), ("wait_requests", lambda *a: {}),
                               ("cancel_and_drain", lambda *a: None)):
                stack.enter_context(patch.object(targeted, name, fake))
            targeted.check_cache("unused", lambda kind, **kw: self.rows.append((kind, kw)))

    def test_real_overlap_and_first_ids_are_both_required(self):
        self.simulate(4)
        four = [row for kind, row in self.rows if kind == "mixed-concurrency"]
        self.assertEqual(len(four), 4)
        self.assertTrue(all(len(r["token_ids"]) == targeted.MIXED_TOKENS and r["cached"] >= 8192 for r in four))
        with self.assertRaisesRegex(ValueError, "expected four running"):
            self.simulate(1)
        self.assertEqual(sum(kind == "mixed-concurrency" for kind, _ in self.rows), 4)
        with self.assertRaisesRegex(ValueError, "Generated IDs changed"):
            self.simulate(4, corrupt=True)


if __name__ == "__main__":
    unittest.main()
