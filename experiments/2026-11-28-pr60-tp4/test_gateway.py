"""Offline exercises for restoration receipts and the two-alias smoke client."""

import copy
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("pr60_gateway", Path(__file__).with_name("gateway.py"))
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)
WINDOW = "9793f4a9-3871-4c74-877a-7fef01af5cac"
CHALLENGE = "1e8e2cd61c144861977a47deaa27a204"


class GatewayTests(unittest.TestCase):
    def request(self, _client, _key, path, body=None, request_id=None):
        self.calls.append((path, body))
        if path == "/v1/models":
            return {"data": [{"id": alias} for alias in gateway.ALIASES]}
        tag = gateway.request_tag(WINDOW, CHALLENGE, body["model"])
        self.assertIn(tag, body["messages"][0]["content"])
        self.assertEqual(request_id, f"spark-{tag}")
        return {"id": "chatcmpl-" + tag, "model": body["model"], "choices": [
            {"finish_reason": "stop", "message": {"content": "42 " + tag}}]}

    def setUp(self):
        self.calls = []
        cred = patch.object(gateway, "credential", return_value="offline")
        net = patch.object(gateway, "request", side_effect=self.request)
        cred.start()
        net.start()
        self.addCleanup(cred.stop)
        self.addCleanup(net.stop)

    def test_fresh_inference_on_each_alias(self):
        report = gateway.verify(None, window=WINDOW, challenge=CHALLENGE)
        self.assertEqual(len(self.calls), 3)
        self.assertTrue(gateway.validate_receipt(report, WINDOW, CHALLENGE, report["checked_at"]))

    def test_catalog_does_not_satisfy_recovery(self):
        report = gateway.verify(None, catalog_only=True)
        self.assertEqual(len(self.calls), 1)
        with self.assertRaises(ValueError):
            gateway.validate_receipt(report, WINDOW, CHALLENGE, 0)

    def test_rejects_replay_or_wrong_response(self):
        report = gateway.verify(None, window=WINDOW, challenge=CHALLENGE)
        mutations = (
            ("another nonce", lambda r: r.update(challenge="0" * 32)),
            ("stale", lambda r: r.update(checked_at=0)),
            ("wrong model", lambda r: r["rows"][0].update(response_model=gateway.ALIASES[1])),
            ("wrong content", lambda r: r["rows"][0].update(response_text="42 old")),
            ("same id", lambda r: r["rows"][0].update(id=r["rows"][1]["id"])),
            ("truncated", lambda r: r["rows"][0].update(finish_reason="length")),
            ("incomplete", lambda r: r["rows"].pop()),
        )
        for case, mutate in mutations:
            with self.subTest(case=case):
                invalid = copy.deepcopy(report)
                mutate(invalid)
                with self.assertRaises(ValueError):
                    gateway.validate_receipt(invalid, WINDOW, CHALLENGE, report["checked_at"])
