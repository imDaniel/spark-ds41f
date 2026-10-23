import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


s = module("scale_only_requests", HERE / "scale_requests.py")
w = module("scale_only_window", HERE / "run_window.py")


class RequestContracts(unittest.TestCase):
    def test_unscaled_generation_never_reaches_network(self):
        with patch.object(s.client, "request") as network:
            with self.assertRaises(ValueError):
                s.guarded_request("/v1/chat/completions", {"model": "x"})
            network.assert_not_called()

    def test_outside_plan_scale_is_rejected(self):
        for scale in (True, -1, 5, float("nan")):
            with self.subTest(scale=scale), self.assertRaises(ValueError):
                s.guarded_request("/v1/completions", {"steering_profile": s.client.PROFILE, "steering_scale": scale})

    def test_tool_literal_mutation_is_rejected(self):
        answer = {"choices": [{"message": {"tool_calls": [{"id": "call-1", "function": {
            "name": "record_value", "arguments": '{"role":"changed"}'}}]}}]}
        with self.assertRaisesRegex(ValueError, "spelling"):
            s.check_tool(answer)

    def scenario(self, bad_tool=False):
        calls = []
        completions = 0

        def network(path, body, stream=False, cancel=False):
            nonlocal completions
            if path == "/tokenize":
                return {"tokens": list(range(32768))}
            calls.append((path, body))
            scale = body["steering_scale"]
            if not stream:
                value = {"bad": True} if bad_tool else s.TOOL_VALUE
                return {"choices": [{"message": {"role": "assistant", "tool_calls": [{
                    "id": "call-1", "type": "function", "function": {"name": "record_value", "arguments": json.dumps(value)}}]}}]}
            if path == "/v1/completions":
                index = completions; completions += 1
                if cancel:
                    return {"cancelled": True}
                return {"text_sha256": str(scale), "usage": {"prompt_tokens": 32768,
                    "prompt_tokens_details": {"cached_tokens": 0 if index in (0, 2, 4) else 32512}}}
            text = "42" if "19 + 23" in body["messages"][0]["content"] else "fixture response"
            return {"text": text, "text_sha256": s.digest(text)}

        with tempfile.TemporaryDirectory() as directory, patch.object(s.client, "request", side_effect=network):
            out = Path(directory) / "result"
            if bad_tool:
                with self.assertRaises(ValueError):
                    s.run(out)
                report = json.loads((out / "requests.json").read_text())
                self.assertEqual(report["status"], "failed")
                self.assertEqual(report["rows"][-1]["kind"], "tool-call")
            else:
                s.run(out)
                self.assertEqual(json.loads((out / "requests.json").read_text())["status"], "passed")
                self.assertEqual(len(calls), 29)
                self.assertTrue(all(b["steering_profile"] == s.client.PROFILE and b["steering_scale"] in (0, 1, 4) for _, b in calls))

    def test_all_29_generations_are_explicitly_scaled(self):
        self.scenario()

    def test_failing_tool_output_is_preserved(self):
        self.scenario(bad_tool=True)

    def test_missing_window_refuses_execution(self):
        with patch.dict(s.os.environ, {}, clear=True), self.assertRaises(KeyError):
            s.require_window()

    def test_default_commands_only_print_plan(self):
        for script in ("run_window.py", "scale_requests.py"):
            result = subprocess.run([sys.executable, str(HERE / script)], capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout)["generation_requests"], 29)

    def test_wrong_candidate_is_rejected_before_window_open(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        fake = SimpleNamespace(m={"services": {"s-loaded": {"identity": {}}}}, open=Mock())
        with patch.object(w, "Executor", return_value=fake), patch.object(sys, "argv", ["run", "--execute"]):
            with self.assertRaisesRegex(ValueError, "Candidate image"):
                w.main()
        fake.open.assert_not_called()

    def test_runner_restores_after_request_failure_and_never_selects_old_arms(self):
        from contextlib import nullcontext
        with tempfile.TemporaryDirectory() as directory:
            class Fake:
                def __init__(self):
                    self.m = {"window_seconds": 2700, "restore_reserve_seconds": 900, "implementation_files": {}}
                    self.m["services"] = {"s-loaded": {"identity": w.read(HERE / "base.json")["candidate"]}}
                    paths = [HERE / p for p in ("run_window.py", "scale_requests.py", "base.json", "plan.json")]
                    paths += [HERE.parent / "2026-10-23-1022-window" / p for p in
                              ("request_screen.py", "cache_screen.py", "site_adapter.py")]
                    paths += [HERE.parent / "2026-10-23-1022-recovery-executor" / p for p in
                              ("window_executor.py", "command_worker.py")]
                    paths.append(Path("/usr/bin/python3"))
                    self.m["implementation_files"] = {str(p): w.sha(p) for p in paths}
                    self.directory = Path(directory)
                    self.hold = self.directory / "hold"
                    self.state_path = self.directory / "state.json"
                    self.arms = []; self.restored = False
                def check_files(self, files): pass
                def open(self):
                    self.hold.touch(); self.state_path.write_text('{"phase":"testing"}')
                def switch(self, arm): self.arms.append(arm)
                def lock(self): return nullcontext()
                def load_owned(self): pass
                def run(self, *args, **kwargs): raise RuntimeError("injected request failure")
                def recover(self):
                    self.restored = True; self.hold.unlink(); self.state_path.write_text('{"phase":"closed"}')
            fake = Fake()
            with patch.object(w, "Executor", return_value=fake), patch.object(sys, "argv", ["run", "--execute"]):
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    w.main()
            self.assertEqual(fake.arms, ["s-loaded"])
            self.assertTrue(fake.restored)


if __name__ == "__main__":
    unittest.main()
