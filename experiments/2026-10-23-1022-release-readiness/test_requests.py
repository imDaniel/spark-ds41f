import io
import json
import unittest
from unittest.mock import patch

import qualification_requests as q


class StreamTests(unittest.TestCase):
    def reply(self, chunks, *, done=True):
        stream = b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks)
        if done:
            stream += b"data: [DONE]\n\n"
        with patch.object(q.urllib.request, "urlopen", return_value=io.BytesIO(stream)):
            return q.request("/v1/chat/completions", q.client.body("test", 1), True)

    def test_fragmented_tool_only_stream_is_assembled(self):
        rows = [
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "record_value", "arguments": '{"value":'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"literal"}'}}]}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"completion_tokens": 10}},
        ]
        result = self.reply(rows)
        self.assertEqual(result["message"]["tool_calls"][0]["function"]["arguments"], '{"value":"literal"}')
        self.assertEqual(result["message"]["tool_calls"][0]["id"], "call_1")

    def test_missing_usage_cannot_pass(self):
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            self.reply([{"choices": [{"delta": {"content": "42"}, "finish_reason": "stop"}]}])

    def test_missing_done_cannot_pass(self):
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            self.reply([{"choices": [{"delta": {"content": "42"}, "finish_reason": "stop"}], "usage": {"completion_tokens": 1}}], done=False)

    def test_sse_error_cannot_pass(self):
        with self.assertRaisesRegex(ValueError, "SSE error"):
            self.reply([{"error": {"message": "failed"}}])

    def test_implicit_scale_cannot_reach_network(self):
        with patch.object(q.urllib.request, "urlopen") as call:
            with self.assertRaisesRegex(ValueError, "explicit scale"):
                q.request("/v1/chat/completions", q.client.body("test"))
            call.assert_not_called()

    def test_truncated_quality_cannot_pass(self):
        with self.assertRaisesRegex(ValueError, "did not finish"):
            q.check_quality("arithmetic", {"text": "42", "finish_reason": "length"})


if __name__ == "__main__":
    unittest.main()
