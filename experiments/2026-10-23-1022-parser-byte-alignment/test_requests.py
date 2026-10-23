import io
import importlib.util
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import qualification_requests as q

spec = importlib.util.spec_from_file_location("release_window", Path(__file__).with_name("run_window.py"))
window = importlib.util.module_from_spec(spec)
spec.loader.exec_module(window)


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


class WindowPreflightTests(unittest.TestCase):
    def test_all_host_executables_are_absolute_and_required(self):
        paths = window.implementation_paths()
        for command in window.HOST_EXECUTABLES:
            self.assertTrue(Path(command).is_absolute())
            self.assertIn(Path(command), paths)

    def test_missing_docker_pin_is_rejected_before_open(self):
        files = {str(p): "expected" for p in window.implementation_paths()}
        del files["/usr/bin/docker"]
        x = SimpleNamespace(m={"implementation_files": files})
        with patch.object(window, "sha", return_value="expected"):
            with self.assertRaisesRegex(ValueError, "Incomplete"):
                window.validate_implementation(x)

    def test_verified_component_receipt_and_new_parser_gate(self):
        self.assertEqual(window.validate_component_reuse()["passed"], 1995)

    def test_non_parser_source_change_cannot_reuse_gpu_receipt(self):
        original = window.read
        def altered(path):
            data = original(path)
            if path.name == "component-reuse.json":
                data["source_changes"].append("vllm/models/deepseek_v4_1/model.py")
            return data
        with patch.object(window, "read", side_effect=altered):
            with self.assertRaisesRegex(ValueError, "scope"):
                window.validate_component_reuse()

    def test_changed_component_receipt_is_rejected(self):
        with patch.object(window, "sha", return_value="different"):
            with self.assertRaisesRegex(ValueError, "receipt changed"):
                window.validate_component_reuse()


if __name__ == "__main__":
    unittest.main()
