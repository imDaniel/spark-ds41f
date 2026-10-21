"""Independent CPU faults for the actual Q03 HTTP lifecycle adapter.

Only filesystem journals are real. All command, network, Docker, guard and clock
boundaries are synthetic; these tests cannot start a service or reach a device.
Set Q03_HTTP_ROOT to review an author worktree without copying or changing it.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
HTTP_ROOT = Path(os.environ.get("Q03_HTTP_ROOT", ROOT)).resolve()
sys.path.insert(0, str(HTTP_ROOT / "scripts"))


def load_module(filename="q03_http_service.py"):
    spec = importlib.util.spec_from_file_location(
        "q03_http_independent_" + filename.replace(".", "_"), HTTP_ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class NoExternalOperations(unittest.TestCase):
    module_file = "q03_http_service.py"
    def setUp(self):
        saved_path = list(sys.path)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), saved_path))
        saved_meta_path = list(sys.meta_path)
        self.addCleanup(lambda: sys.meta_path.__setitem__(slice(None), saved_meta_path))
        saved_modules = {k: v for k, v in sys.modules.items() if k == "servebench" or k.startswith("servebench.")}
        for key in saved_modules: del sys.modules[key]
        def restore_modules():
            for key in list(sys.modules):
                if key == "servebench" or key.startswith("servebench."): del sys.modules[key]
            sys.modules.update(saved_modules)
        self.addCleanup(restore_modules)
        for name in ("Popen", "run", "check_call", "check_output"):
            guard = mock.patch.object(subprocess, name,
                side_effect=AssertionError("real process forbidden in HTTP CPU fault fixture"))
            guard.start()
            self.addCleanup(guard.stop)
        guard = mock.patch.object(socket, "create_connection",
            side_effect=AssertionError("network forbidden in HTTP CPU fault fixture"))
        guard.start()
        self.addCleanup(guard.stop)
        self.http = load_module(self.module_file)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()


class WorkerObservationFaults(NoExternalOperations):
    module_file = "q03_http_worker.py"

    def fixture(self):
        runtime = b'{"source_head":"synthetic-pinned-source"}'
        fixture = (b'def _check_runtime(worker, value):\n'
                   b'    _probe_calls.append((worker, value))\n'
                   b'    return {"worker_rank": 0, "effective_config": {"same": "state"}}\n')
        observer = self.http.HttpObservation()
        result = {"worker_rank": 0, "effective_config": {"same": "state"}}
        handshake = {"window_id": "a" * 32, "attempt_id": "b" * 32}
        spec_raw = json.dumps({"handshake": handshake}).encode()
        source = {"/runtime/runtime-manifest.json": runtime,
                  self.http.FIXTURE: fixture, "/runtime/http-spec.json": spec_raw}
        environment = mock.patch.dict(os.environ, {
            "Q03_HTTP_WINDOW": handshake["window_id"], "Q03_HTTP_ATTEMPT": handshake["attempt_id"],
            "Q03_HTTP_SPEC_SHA256": hashlib.sha256(spec_raw).hexdigest()})
        environment.start(); self.addCleanup(environment.stop)
        self.http.RUNTIME_SHA = hashlib.sha256(runtime).hexdigest()
        self.http.FIXTURE_SHA = hashlib.sha256(fixture).hexdigest()
        calls = []
        spec = None
        return observer, source, calls, spec

    def test_changed_runtime_or_fixture_bytes_refuse_before_worker_probe(self):
        for which in ("/runtime/runtime-manifest.json", self.http.FIXTURE):
            observer, source, calls, spec = self.fixture()
            source[which] += b'changed'
            with self.subTest(which=which), \
                 mock.patch.object(Path, "read_bytes", lambda path: source[str(path)]), \
                 self.assertRaisesRegex(RuntimeError, "changed"):
                observer.q03_http_observe()
            self.assertEqual(calls, [])

    def test_verified_worker_probe_retains_actual_report_and_source_pins(self):
        observer, source, calls, spec = self.fixture()
        with mock.patch.object(Path, "read_bytes", lambda path: source[str(path)]), \
             mock.patch.object(self.http.types, "ModuleType", return_value=SimpleNamespace(_probe_calls=calls)):
            result = observer.q03_http_observe()
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0][0], observer)
        self.assertEqual(result["worker_rank"], 0)
        self.assertEqual(result["http_runtime_sha256"], self.http.RUNTIME_SHA)
        self.assertEqual(result["http_fixture_sha256"], self.http.FIXTURE_SHA)


class FixedClientFaults(NoExternalOperations):
    module_file = "q03_http_client.py"

    def plan(self, steering="off"):
        root = self.directory / "toolbox"
        root.mkdir(exist_ok=True)
        files = {"servebench/__init__.py": b'# pinned package\n',
                 "servebench/__main__.py": b'# pinned CLI\n'}
        for relative, raw in files.items():
            path = root / relative
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(raw)
        return {"window_id": "a" * 32, "attempt_id": "b" * 32,
                "plan_sha256": "c" * 64, "url": "http://127.0.0.1:18973",
                "selection": {"mode": "off", "steering": steering},
                "client": {"toolbox_path": str(root), "toolbox_files": {
                    relative: hashlib.sha256(raw).hexdigest() for relative, raw in files.items()},
                    "output": str(self.directory / "burst.json")}}

    def run_client(self, plan, code=0, error=None):
        calls = []
        self.client_calls = calls
        def fake_main(argv):
            calls.append(argv)
            if error is not None:
                raise error
            Path(plan["client"]["output"]).write_bytes(b'{"synthetic":"burst"}')
            return code
        original = self.http.FrozenToolbox.exec_module
        def fake_load(loader, module):
            if module.__name__ == "servebench.__main__": module.main = fake_main
            else: original(loader, module)
        for key in list(sys.modules):
            if key == "servebench" or key.startswith("servebench."): del sys.modules[key]
        with mock.patch.object(self.http.FrozenToolbox, "exec_module", fake_load):
            receipt = self.http.run(plan)
        return calls, receipt

    def test_client_argv_is_fixed_and_each_steering_body_is_typed(self):
        for steering, expected in (("off", None), ("loaded_zero", 0), ("active", 1)):
            with self.subTest(steering=steering):
                subdir = self.directory / steering
                subdir.mkdir()
                old, self.directory = self.directory, subdir
                try:
                    plan = self.plan(steering)
                    plan["client"]["argv"] = ["unapproved", "--concurrency", "999"]
                    calls, receipt = self.run_client(plan)
                    argv = calls[0]
                    self.assertEqual(len(calls), 1)
                    self.assertNotIn("unapproved", argv)
                    self.assertEqual(argv[argv.index("--concurrency") + 1], "1")
                    self.assertEqual(argv[argv.index("--max-tokens") + 1], "16")
                    self.assertEqual(argv[argv.index("--batches") + 1], "1")
                    self.assertEqual(argv[argv.index("--model") + 1], "deepseek-v4.1-flash")
                    if expected is None:
                        self.assertNotIn("--extra-body", argv)
                    else:
                        body = json.loads(argv[argv.index("--extra-body") + 1])
                        self.assertEqual(body, {"vllm_xargs": {
                            "steering_profile": "heretic-1017-attn-v1", "steering_scale": expected}})
                        self.assertIs(type(body["vllm_xargs"]["steering_scale"]), int)
                    self.assertEqual(receipt["plan_sha256"], plan["plan_sha256"])
                finally:
                    self.directory = old

    def test_toolbox_source_mutation_refuses_before_any_client_call(self):
        plan = self.plan()
        path = Path(plan["client"]["toolbox_path"]) / "servebench/__main__.py"
        path.write_bytes(path.read_bytes() + b'changed')
        with self.assertRaisesRegex(RuntimeError, "toolbox file changed"):
            self.run_client(plan)
        self.assertFalse(Path(plan["client"]["output"]).exists())
        self.assertFalse((self.directory / "client-receipt.json").exists())

    def test_failed_client_is_recorded_not_promoted_to_success(self):
        plan = self.plan()
        calls, receipt = self.run_client(plan, error=TimeoutError("bounded HTTP deadline"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(receipt["exit_code"], 1)
        self.assertIn("TimeoutError", receipt["error"])
        self.assertIsNone(receipt["output_sha256"])
        saved = json.loads((self.directory / "client-receipt.json").read_bytes())
        self.assertEqual(saved, receipt)

    def test_preexisting_output_or_receipt_never_overwrites_evidence(self):
        for target in ("burst.json", "client-receipt.json"):
            with self.subTest(target=target):
                subdir = self.directory / target.replace(".", "_")
                subdir.mkdir()
                old, self.directory = self.directory, subdir
                try:
                    plan = self.plan()
                    path = self.directory / target
                    path.write_bytes(b'prior immutable evidence')
                    with self.assertRaises((RuntimeError, FileExistsError)):
                        self.run_client(plan)
                    self.assertEqual(path.read_bytes(), b'prior immutable evidence')
                    self.assertEqual(self.client_calls, [], "prior evidence must refuse before sending a burst")
                finally:
                    self.directory = old


class AdmissionAndReadyFaults(NoExternalOperations):
    def manifest(self):
        return {"window_seconds": 10800,
                "preflight": {"original_service_disposition": "keep-stopped"},
                "jobs": [{"kind": "http_service", "timeout_seconds": 120,
                          "selection": {"mode": "off", "steering": "off"}}]}

    def ready(self):
        expected = {"fixed_capacity": 1048576}
        plan = {"window_id": "a" * 32, "attempt_id": "b" * 32,
                "spec_sha256": "c" * 64, "selection": {"mode": "off", "steering": "off"},
                "spec": {"expected_effective_config": expected}}
        reports = []
        for rank in range(4):
            reports.append({"worker_rank": rank, "tp_rank": rank, "config_rank": rank,
                "node_rank": rank, "node_rank_within_dp": rank, "local_rank": 0,
                "effective_config": copy.deepcopy(expected), "runner_config": copy.deepcopy(expected),
                "owner": {"rank": rank, "sm": [12, 1], "tp": 4,
                          "sequence_parallel": False, "owner_present": False, "steering_profile": None},
                "http_runtime_sha256": self.http.RUNTIME_SHA,
                "http_fixture_sha256": self.http.owner.TEST_SHA,
                "http_window_id": plan["window_id"], "http_attempt_id": plan["attempt_id"],
                "http_spec_sha256": plan["spec_sha256"]})
        value = {"schema_version": 1, **{key: plan[key] for key in
                 ("window_id", "attempt_id", "spec_sha256")}, "reports": reports}
        return plan, value

    def encoded(self, value):
        value["reports_sha256"] = self.http.digest(self.http.canonical(value["reports"]))
        return self.http.canonical(value)

    def test_only_closed_bounded_http_job_and_keep_stopped_are_admitted(self):
        valid = self.manifest()
        self.http.validate_jobs(valid)
        mutations = []
        for timeout in (0, 7201, True, 1.0, "120", None, float("inf"), float("nan")):
            changed = copy.deepcopy(valid); changed["jobs"][0]["timeout_seconds"] = timeout
            mutations.append(changed)
        for seconds in (10800.0, True, 10799, 10801, "10800", None):
            changed = copy.deepcopy(valid); changed["window_seconds"] = seconds
            mutations.append(changed)
        for disposition in ("restore", "unknown", None):
            changed = copy.deepcopy(valid); changed["preflight"]["original_service_disposition"] = disposition
            mutations.append(changed)
        for field in ("argv", "command", "environment", "url", "extra_body"):
            changed = copy.deepcopy(valid); changed["jobs"][0][field] = "unreviewed"
            mutations.append(changed)
        changed = copy.deepcopy(valid); changed["jobs"] *= 2; mutations.append(changed)
        for changed in mutations:
            with self.subTest(changed=changed), self.assertRaises((RuntimeError, ValueError)):
                self.http.validate_jobs(changed)

    def test_failed_attempt_does_not_advance_fixed_off_off_dual_sequence(self):
        state = {"attempt_results": []}
        session = SimpleNamespace(directory=self.directory, state=state, manifest=self.manifest())
        self.assertEqual(self.http.next_selection(session), {"mode": "off", "steering": "off"})
        state["attempt_results"].append({"outcome": "failed", "passed_gates": [], "attempt_id": "f" * 32})
        self.assertEqual(self.http.next_selection(session)["mode"], "off")
        for index, mode in enumerate(("off", "off", "dual")):
            attempt_id = str(index + 1) * 32
            folder = self.directory / "attempts" / attempt_id
            folder.mkdir(parents=True)
            descriptor = {"jobs": [{"kind": "http_service", "selection": {"mode": mode, "steering": "off"}}]}
            (folder / "descriptor.json").write_bytes(self.http.canonical(descriptor))
            state["attempt_results"].append({"outcome": "passed", "passed_gates": ["http_service"],
                                             "attempt_id": attempt_id})
            if index < 2:
                self.assertEqual(self.http.next_selection(session)["mode"], ("off", "dual")[index])
        with self.assertRaisesRegex(RuntimeError, "complete"):
            self.http.next_selection(session)
        state["attempt_results"].pop()
        path = self.directory / "attempts" / ("1" * 32) / "descriptor.json"
        bad = json.loads(path.read_bytes()); bad["jobs"][0]["selection"]["mode"] = "dual"
        path.write_bytes(self.http.canonical(bad))
        with self.assertRaisesRegex(RuntimeError, "predecessor"):
            self.http.next_selection(session)

    def test_ready_rejects_each_rank_source_image_projection_and_topology_drift(self):
        plan, value = self.ready()
        self.http.validate_ready(self.encoded(value), plan)
        for rank in range(4):
            for key, mutation in (("http_runtime_sha256", "d" * 64),
                    ("http_fixture_sha256", "e" * 64), ("worker_rank", True),
                    ("node_rank", 9), ("tp_rank", False),
                    ("effective_config", {"fixed_capacity": 4096}),
                    ("runner_config", {"fixed_capacity": 4096})):
                bad = copy.deepcopy(value); bad["reports"][rank][key] = mutation
                with self.subTest(rank=rank, key=key), self.assertRaises((RuntimeError, ValueError)):
                    self.http.validate_ready(self.encoded(bad), plan)
        for count in (0, 1, 3):
            bad = copy.deepcopy(value); bad["reports"] = bad["reports"][:count]
            with self.subTest(count=count), self.assertRaises(RuntimeError):
                self.http.validate_ready(self.encoded(bad), plan)
        bad = copy.deepcopy(value); bad["reports"][3] = copy.deepcopy(bad["reports"][2])
        with self.assertRaises(RuntimeError):
            self.http.validate_ready(self.encoded(bad), plan)

    def test_worker_report_must_bind_actual_window_attempt_and_spec_not_just_wrapper(self):
        plan, value = self.ready()
        for rank in range(4):
            for key in ("http_window_id", "http_attempt_id", "http_spec_sha256"):
                for altered in (None, "f" * len(value["reports"][rank][key])):
                    bad = copy.deepcopy(value)
                    if altered is None:
                        del bad["reports"][rank][key]
                    else:
                        bad["reports"][rank][key] = altered
                    with self.subTest(rank=rank, key=key, altered=altered), self.assertRaises(RuntimeError):
                        self.http.validate_ready(self.encoded(bad), plan)


class FakeHttpOps:
    """Injected daemon/epoch boundary, not a replacement controller loop."""
    def __init__(self, owner, session, plan):
        self.owner, self.session, self.plan = owner, session, plan
        self.events = []
        self.items, self.guards = {}, {}
        self.bad_id = None
        self.mutate = None
        self.fail_action = None
        self.bad_guard = None
        self.ready_mutate = None
        self.ready_missing = False
        self.authority_failure = None
        self.abort_after = None
        self.unknown_epoch = False
        self.stubborn = False
        self.ack_error = False
        self.sleeps = 0
        self.acked = False

    def durable(self):
        return json.loads((self.journal.directory / 'state.json').read_text())

    def container(self, rank, row):
        target = self.plan['ranks'][rank]
        env = dict(target['environment'], PYTHONPATH=self.plan['binding']['candidate']['pytest_base_pythonpath'])
        return {'Id': row['container_id'], 'Image': self.owner.IMAGE,
                'State': {'Running': False, 'ExitCode': 0, 'OOMKilled': False},
                'Config': {'Labels': {'q03.window': self.plan['window_id'],
                           'q03.attempt': self.plan['attempt_id'], 'q03.job': 'http_service',
                           'q03.rank': str(rank), 'q03.plan': self.plan['plan_sha256']},
                           'Entrypoint': ['python3'], 'Cmd': target['python_argv'],
                           'WorkingDir': '/validation',
                           'Env': [f'{key}={value}' for key, value in env.items()]},
                'HostConfig': {'NetworkMode': 'host', 'IpcMode': 'host',
                               'Privileged': False, 'AutoRemove': False, 'OomKillDisable': False,
                               'RestartPolicy': {'Name': 'no'},
                               'Memory': 112 * 1024**3, 'MemorySwap': 112 * 1024**3,
                               'ShmSize': 16 * 1024**3, 'CpusetCpus': '5-9,15-19',
                               'DeviceRequests': [{'Count': -1, 'Capabilities': [['gpu']]}],
                               'Devices': [{'PathOnHost': '/dev/infiniband', 'PathInContainer': '/dev/infiniband', 'CgroupPermissions': 'rwm'}],
                               'CapAdd': ['IPC_LOCK'], 'SecurityOpt': ['label=disable', 'seccomp=unconfined'],
                               'DeviceCgroupRules': ['c 226:* rw'],
                               'Ulimits': [{'Name': name, 'Soft': value, 'Hard': value} for name, value in
                                           [('memlock', -1), ('stack', 67108864), ('core', 0)]]},
                'Mounts': [{'Source': s, 'Destination': d, 'RW': m == 'rw'}
                           for s, d, m in target['mounts']] + [
                           {'Source': '/dev/dri/by-path/pci-000f:01:00.0-card',
                            'Destination': '/dev/dri/card0', 'RW': True}]}

    def action(self, rank, kind, row):
        state = self.durable()
        if kind in ('prepare', 'create', 'startup_guard', 'start', 'steady_guard'):
            last = json.loads((self.journal.directory / f"{state['sequence']:06d}.json").read_text())
            wanted = {'startup_guard': 'startup-guard', 'steady_guard': 'steady-guard'}.get(kind, kind)
            assert last['kind'] == wanted + '-intent' and last['rank'] == rank
        self.events.append((kind, rank))
        if self.fail_action == (kind, rank):
            raise RuntimeError(f'injected {kind} rank{rank}')
        if kind == 'create':
            cid = f'{rank + 1:x}' * 64
            self.items[rank] = self.container(rank, dict(row, container_id=cid))
            if self.mutate is not None:
                self.mutate(rank, self.items[rank])
            if self.bad_id is not None and self.bad_id[0] == rank:
                return self.bad_id[1]
            return cid
        if kind == 'startup_guard':
            self.guards[rank] = {'owned': True, 'active': True, 'phase': 'startup',
                                 'container_id': row['container_id']}
        elif kind == 'steady_guard':
            self.guards[rank]['phase'] = 'steady'
        elif kind == 'start':
            assert len(self.items) == 4
            assert all(r['container_id'] and len(r['container_id']) == 64 for r in state['ranks'])
            assert len(self.guards) == 4 and all(g['active'] for g in self.guards.values())
            self.items[rank]['State']['Running'] = True
        elif kind == 'stop':
            if not self.stubborn:
                self.items[rank]['State']['Running'] = False
        elif kind == 'stop_guard':
            self.guards[rank]['active'] = False
        elif kind == 'remove':
            if not self.stubborn:
                self.items.pop(rank, None)
        return None

    def capture_logs(self, rank, row):
        from q03_log_fixture import capture_logs
        return capture_logs(self, rank, row)

    def inspect(self, rank, row):
        self.events.append(('inspect', rank))
        return copy.deepcopy(self.items.get(rank))

    def guard_state(self, rank, row):
        self.events.append(('guard', rank))
        state = copy.deepcopy(self.guards.get(rank, {'absent': True}))
        if self.bad_guard is not None and self.bad_guard == (rank, state.get('phase')):
            state['active'] = False
        return state

    def read_ready(self):
        self.events.append(('ready', None))
        assert all(item['State']['Running'] for item in self.items.values()) and len(self.items) == 4
        if self.ready_missing:
            return None
        expected = self.plan['spec']['expected_effective_config']
        reports = []
        for rank in range(4):
            reports.append({'worker_rank': rank, 'tp_rank': rank, 'config_rank': rank,
                            'node_rank': rank, 'node_rank_within_dp': rank, 'local_rank': 0,
                            'effective_config': expected, 'runner_config': expected,
                            'http_runtime_sha256': self.owner.RUNTIME_SHA,
                            'http_fixture_sha256': self.owner.owner.TEST_SHA,
                            'http_window_id': self.plan['window_id'],
                            'http_attempt_id': self.plan['attempt_id'],
                            'http_spec_sha256': self.plan['spec_sha256'],
                            'owner': {'rank': rank, 'sm': [12, 1], 'tp': 4,
                                      'sequence_parallel': False,
                                      'owner_present': self.plan['selection']['mode'] == 'dual',
                                      'steering_profile': None}})
        value = {'schema_version': 1, 'window_id': self.plan['window_id'],
                 'attempt_id': self.plan['attempt_id'], 'spec_sha256': self.plan['spec_sha256'],
                 'reports': reports, 'reports_sha256': self.owner.digest(self.owner.canonical(reports))}
        if self.ready_mutate:
            self.ready_mutate(value)
            value['reports_sha256'] = self.owner.digest(self.owner.canonical(value['reports']))
        return self.owner.canonical(value)

    def collect_result(self):
        self.events.append(('collect', None))
        return {'result': 'passed', 'attempt_id': self.plan['attempt_id']}

    def check_authority(self):
        if self.authority_failure and (self.abort_after is None or self.abort_after in self.events):
            raise self.authority_failure

    def seal_all(self):
        self.events.append(('seal', None))
        if self.unknown_epoch:
            raise RuntimeError('UNKNOWN remote epoch')

    def sleep(self, seconds):
        self.sleeps += 1
        if self.sleeps >= 2:
            raise TimeoutError('injected bounded readiness timeout')


    def run_client(self):
        self.events.append(('client', None))
        assert self.durable()['phase'] == 'client_running'
        assert len(self.items) == 4 and all(i['State']['Running'] for i in self.items.values())
        assert len(self.guards) == 4 and all(g['active'] and g['phase'] == 'steady' for g in self.guards.values())
        if getattr(self, 'client_failure', None):
            raise self.client_failure
        if getattr(self, 'after_client', None):
            self.after_client()


class ActualHttpLifecycleFaults(NoExternalOperations):
    def setUp(self):
        super().setUp()
        patch = mock.patch.object(self.http, "read_owned_bytes", lambda path: Path(path).read_bytes())
        patch.start(); self.addCleanup(patch.stop)
        self.counter = 0

    def scenario(self):
        self.counter += 1
        directory = self.directory / str(self.counter)
        attempt_id, window_id = "b" * 32, "a" * 32
        attempt = directory / "attempts" / attempt_id
        attempt.mkdir(parents=True)
        plan = {"window_id": window_id, "attempt_id": attempt_id,
                "selection": {"mode": "off", "steering": "off"},
                "spec": {"expected_effective_config": {"fixed_capacity": 1048576}},
                "binding": {"candidate": {"pytest_base_pythonpath": "/image/base"},
                            "http": {"startup_seconds": 30}},
                "ranks": [{"rank": rank, "root": str(directory / ("rank" + str(rank))),
                           "environment": {"NODE_RANK": str(rank)},
                           "python_argv": ["-B", "/q03-http/q03_http_worker.py", "serve", "/models"],
                           "mounts": [["/pinned/model", "/models", "ro"]]}
                          for rank in range(4)]}
        plan["spec_sha256"] = self.http.digest(self.http.canonical(plan["spec"]))
        plan["plan_sha256"] = self.http.digest(self.http.canonical(plan))
        capsule = {"window_id": window_id}
        raw = self.http.canonical(capsule)
        (directory / "capsule.json").write_bytes(raw)
        descriptor = {"window_id": window_id, "attempt_id": attempt_id,
                      "capsule_sha256": self.http.digest(raw),
                      "command_plan_sha256": self.http.digest(self.http.canonical([["http_service", plan]]))}
        (attempt / "descriptor.json").write_bytes(self.http.canonical(descriptor))
        (attempt / "admitted.json").write_bytes(self.http.canonical({
            "descriptor_sha256": self.http.digest(self.http.canonical(descriptor)),
            "command_plan_sha256": descriptor["command_plan_sha256"]}))
        session = SimpleNamespace(directory=directory, capsule=capsule)
        ops = FakeHttpOps(self.http, session, plan)
        return session, plan, ops

    def drain(self, session, plan, ops):
        ops.authority_failure = None
        return self.http.quiesce_http(session, plan["attempt_id"], ops=ops)

    def test_positive_four_rank_http_and_client_then_exact_daemon_drain(self):
        session, plan, ops = self.scenario()
        result = self.http.run_http(session, plan, ops=ops)
        self.assertEqual(result["result"], "passed")
        events = ops.events
        self.assertLess(max(events.index(("create", r)) for r in range(4)),
                        min(events.index(("startup_guard", r)) for r in range(4)))
        self.assertLess(max(events.index(("startup_guard", r)) for r in range(4)),
                        min(events.index(("start", r)) for r in range(4)))
        self.assertLess(max(events.index(("start", r)) for r in range(4)), events.index(("ready", None)))
        self.assertLess(max(events.index(("steady_guard", r)) for r in range(4)), events.index(("client", None)))
        self.assertEqual(events.count(("client", None)), 1)
        proof = self.drain(session, plan, ops)
        self.assertTrue(proof["daemon_quiescent"])
        self.assertTrue(proof["gpu_drain_required_by_caller"])
        self.assertEqual(ops.items, {})
        self.assertTrue(all(not g["active"] for g in ops.guards.values()))
        self.assertEqual(ops.durable()["phase"], "drained")

    def test_every_partial_start_or_guard_fault_blocks_client(self):
        for action in ("startup_guard", "start", "steady_guard"):
            for rank in range(4):
                session, plan, ops = self.scenario()
                ops.fail_action = (action, rank)
                with self.subTest(action=action, rank=rank), self.assertRaises(RuntimeError):
                    self.http.run_http(session, plan, ops=ops)
                self.assertNotIn(("client", None), ops.events)
                ops.fail_action = None
                self.drain(session, plan, ops)
                self.assertEqual(ops.items, {})

    def test_each_rank_wrong_image_environment_or_identity_blocks_all_starts(self):
        for rank in range(4):
            for fault in ("image", "environment", "identity"):
                session, plan, ops = self.scenario()
                def mutate(actual_rank, item, rank=rank, fault=fault):
                    if actual_rank != rank:
                        return
                    if fault == "image": item["Image"] = "sha256:" + "f" * 64
                    elif fault == "environment": item["Config"]["Env"] += ["NODE_RANK=99"]
                    else: item["Config"]["Labels"]["q03.attempt"] = "c" * 32
                ops.mutate = mutate
                with self.subTest(rank=rank, fault=fault), self.assertRaises(RuntimeError):
                    self.http.run_http(session, plan, ops=ops)
                self.assertFalse(any(event[0] == "start" for event in ops.events))
                self.assertNotIn(("client", None), ops.events)

    def test_foreign_partial_and_wrong_resolved_worker_ready_never_launch_client(self):
        for rank in range(4):
            for field, value in (("http_attempt_id", "f" * 32),
                    ("http_runtime_sha256", "e" * 64),
                    ("effective_config", {"fixed_capacity": 4096}),
                    ("runner_config", {"fixed_capacity": 4096})):
                session, plan, ops = self.scenario()
                ops.ready_mutate = lambda ready, rank=rank, field=field, value=value: ready["reports"][rank].__setitem__(field, value)
                with self.subTest(rank=rank, field=field), self.assertRaises(RuntimeError):
                    self.http.run_http(session, plan, ops=ops)
                self.assertNotIn(("client", None), ops.events)
                self.drain(session, plan, ops)

    def test_startup_timeout_and_client_timeout_drain_before_any_reuse(self):
        for target in ("startup", "client"):
            session, plan, ops = self.scenario()
            if target == "startup": ops.ready_missing = True
            else: ops.client_failure = TimeoutError("bounded client descendants still owned")
            with self.subTest(target=target), self.assertRaises(TimeoutError):
                self.http.run_http(session, plan, ops=ops)
            self.assertFalse((ops.journal.directory / "result.json").exists())
            self.assertNotEqual(ops.durable()["phase"], "drained")
            self.drain(session, plan, ops)
            self.assertEqual(ops.items, {})

    def test_client_return_cannot_hide_rank_loss_or_changed_worker_report(self):
        for rank in range(4):
            session, plan, ops = self.scenario()
            ops.after_client = lambda rank=rank: ops.items[rank]["State"].__setitem__("Running", False)
            with self.subTest(rank=rank), self.assertRaisesRegex(RuntimeError, "not running"):
                self.http.run_http(session, plan, ops=ops)
            self.assertFalse((ops.journal.directory / "result.json").exists())
            self.drain(session, plan, ops)

    def test_unknown_epoch_missing_create_id_and_surviving_daemon_never_prove_drain(self):
        for fault in ("epoch", "missing-id", "surviving-daemon"):
            session, plan, ops = self.scenario()
            if fault == "missing-id":
                ops.bad_id = (2, None)
                with self.assertRaises(RuntimeError): self.http.run_http(session, plan, ops=ops)
            else:
                self.http.run_http(session, plan, ops=ops)
                if fault == "epoch": ops.unknown_epoch = True
                else: ops.stubborn = True
            with self.subTest(fault=fault), self.assertRaises(RuntimeError):
                self.drain(session, plan, ops)
            self.assertNotEqual(ops.durable()["phase"], "drained")
            self.assertFalse((ops.journal.directory / "drained.json").exists())

    def test_controller_death_or_pause_stops_progress_before_client(self):
        for trigger in (("start", 2), ("steady_guard", 3)):
            session, plan, ops = self.scenario()
            ops.authority_failure = RuntimeError("core dead or operator pause")
            ops.abort_after = trigger
            with self.subTest(trigger=trigger), self.assertRaisesRegex(RuntimeError, "core dead"):
                self.http.run_http(session, plan, ops=ops)
            self.assertNotIn(("client", None), ops.events)
            self.drain(session, plan, ops)

    def test_tampered_retained_plan_blocks_watchdog_quiesce_before_mutation(self):
        session, plan, ops = self.scenario()
        self.http.run_http(session, plan, ops=ops)
        path = ops.journal.directory / "plan.json"
        value = json.loads(path.read_bytes()); value["selection"]["mode"] = "dual"
        path.write_bytes(self.http.canonical(value))
        before = list(ops.events)
        with self.assertRaisesRegex(RuntimeError, "digest"):
            self.drain(session, plan, ops)
        self.assertEqual(ops.events, before)


class ClientEpochIntegrationFaults(NoExternalOperations):
    def setup_epoch(self, state="timed-out"):
        import q03_remote_operation as remote
        self.remote = remote
        window, epoch, operation = "a" * 32, "b" * 32, "c" * 32
        roots = [self.directory / str(rank) for rank in range(4)]
        for root in roots: root.mkdir()
        journal_directory = self.directory / "journal"
        journal_directory.mkdir()
        plan = {"window_id": window, "attempt_id": "e" * 32,
                "binding": {"checkout": "/pinned/checkout"}}
        (journal_directory / "client-plan.json").write_bytes(self.http.canonical(plan))
        command = remote.bound_command(["python3", "-I", "-B", "/pinned/checkout/scripts/q03_http_client.py",
            "--plan", str(journal_directory / "client-plan.json"),
            "--sha256", self.http.digest(self.http.canonical(plan))])
        target = remote.create_operation(roots[0], window, operation,
                                         command, str(roots[0]), 1, epoch)
        if state == "timed-out":
            from test_q03_remote_operation import ChildOps
            child = ChildOps(target); child.alive = True
            receipt = remote.supervise(target, child)
            self.assertTrue(receipt["group_quiescent"])
            self.assertTrue(receipt["timed_out"])
        else:
            remote.atomic(target / "state.json", {"phase": "running", "supervisor": {
                "pid": 987654321, "start_ticks": "lost-http-client-supervisor"}})
        events = []
        journal = SimpleNamespace(directory=journal_directory, state={"epochs": [{"epoch": epoch, "operation": operation,
            "rank": 0, "action_kind": "http_client", "argv_sha256": self.http.digest(self.http.canonical(command))}]},
            event=lambda kind, **fields: events.append((kind, fields)), save=lambda: events.append(("saved", {})))
        ops = self.http.HttpOps.__new__(self.http.HttpOps)
        ops.journal = journal
        ops.plan = plan
        ops.binding = plan["binding"]
        ops.helper = remote
        ops.sleep = lambda _: None
        def helper(rank, action, actual_epoch, actual_operation=None, extra=()):
            self.assertEqual(actual_epoch, epoch)
            if action == "seal": return remote.seal_epoch(roots[rank], window, epoch)
            if action == "cancel": return remote.request_cancel(roots[rank], window, actual_operation)
            raise AssertionError("unexpected mutation: " + action)
        ops.helper_call = helper
        return ops, roots, window, epoch, operation, events

    def test_actual_timed_out_client_receipt_seals_all_four_and_rejects_late_launch(self):
        ops, roots, window, epoch, operation, events = self.setup_epoch()
        self.http.HttpOps.seal_epoch(ops, epoch)
        self.assertEqual(ops.journal.state["sealed_epochs"], [epoch])
        self.assertEqual([e[1]["rank"] for e in events if e[0] == "http-client-epoch-sealed"], list(range(4)))
        self.assertFalse(self.remote.status(roots[0], window, operation)["action_succeeded"])
        for rank, root in enumerate(roots):
            with self.subTest(rank=rank), self.assertRaisesRegex(RuntimeError, "permanently sealed"):
                self.remote.create_operation(root, window, "d" * 32,
                    ["inert-delayed-client"], str(root), 1, epoch)

    def test_dead_client_supervisor_without_terminal_cannot_seal_or_release(self):
        ops, roots, window, epoch, operation, events = self.setup_epoch("unknown")
        with self.assertRaisesRegex(RuntimeError, "UNKNOWN"):
            self.http.HttpOps.seal_epoch(ops, epoch)
        self.assertNotIn(epoch, ops.journal.state.get("sealed_epochs", []))
        self.assertFalse(any(kind == "saved" for kind, _ in events))

    def test_nonquiescent_terminal_receipt_never_becomes_client_drain(self):
        ops, roots, window, epoch, operation, events = self.setup_epoch()
        path = self.remote.operation_dir(roots[0], window, operation) / "receipt.json"
        value = json.loads(path.read_bytes()); value["group_quiescent"] = False
        self.remote.atomic(path, value)
        with self.assertRaises((RuntimeError, ValueError)):
            self.http.HttpOps.seal_epoch(ops, epoch)
        self.assertNotIn(epoch, ops.journal.state.get("sealed_epochs", []))


class RecoveryRouteAndInventoryFaults(NoExternalOperations):
    def setUp(self):
        super().setUp()
        import q03_window as core
        import q03_window_watchdog as watchdog
        import q03_held_window as held
        self.core, self.watchdog, self.held = core, watchdog, held

    def test_exact_recovery_inventory_covers_every_new_executable_and_toolbox_pin(self):
        source = (HTTP_ROOT / "scripts/q03_owner_model.py").read_text()
        run = source[source.index('def run_owner('):source.index('\ndef quiesce_owner(')]
        self.assertEqual(hashlib.sha256(run.encode()).hexdigest(),
            "203de0598931c483c4aec61cb0a7749e06e197e49f04aab9ededf0819543d594", "owner run protocol remains the reviewed native-RPC baseline")
        expected = {"q03_window.py", "q03_window_preflight.py", "q03_window_watchdog.py",
            "q03_original_spark_adapter.py", "q03_remote_operation.py", "q03_build_adapter.py",
            "q03_window_build.py", "q03_buildkit_plan.py", "q03_git_toolchain.py", "q03_held_window.py",
            "q03_owner_model.py", "q03_rank_logs.py", "q03_image_preparation.py", "memguard-start.sh", "memguard.sh",
            "../bin/spark", "mesh_fabric.py", "kernel_backend.py", "kernel_readiness.py", "topology.py",
            "transport_profiles.py", "model_layout.py", "recipe.py",
            "q03_http_service.py", "q03_http_worker.py", "q03_http_client.py", "q03_http_toolbox.json"}
        actual = self.core.RuntimeOps().recovery_sources()
        self.assertEqual(set(actual), expected)
        for name, value in actual.items():
            self.assertEqual(value, hashlib.sha256((HTTP_ROOT / "scripts" / name).read_bytes()).hexdigest())
        root = self.directory
        capsule = {"session_dir": str(root), "deadline_epoch": 100,
            "heartbeat_timeout_s": 20, "recovery_timeout_s": 30,
            "recovery_command": [sys.executable, str(HTTP_ROOT / "scripts/q03_window.py"),
                                 "recover", "--session-dir", str(root)],
            "recovery_script_sha256": actual["q03_window.py"], "recovery_source_sha256": actual}
        def save(value):
            raw = self.http.canonical(value)
            (root / "capsule.json").write_bytes(raw)
            (root / "capsule.sha256").write_text(self.http.digest(raw))
        save(capsule)
        self.watchdog.load_capsule(root)
        for name in self.http.SOURCE_FILES:
            for mutation in ("missing", "changed"):
                bad = copy.deepcopy(capsule)
                if mutation == "missing": del bad["recovery_source_sha256"][name]
                else: bad["recovery_source_sha256"][name] = "f" * 64
                save(bad)
                with self.subTest(name=name, mutation=mutation), self.assertRaises(RuntimeError):
                    self.watchdog.load_capsule(root)

    def scenario(self, unknown=False):
        events = []
        manifest = {"held_window": {"schema_version": 1}, "jobs": [{"kind": "http_service"}],
                    "preflight": {"original_service_disposition": "keep-stopped"}}
        state = {"phase": "attempt-running", "active_process": {"pid": 123, "start_ticks": "fixed"},
                 "active_attempt": {"attempt_id": "b" * 32}, "hold_released": False}
        capsule = {"window_id": "a" * 32, "manifest": manifest,
                   "deadline_epoch": 100, "heartbeat_timeout_s": 20, "recovery_timeout_s": 30}
        session = SimpleNamespace(manifest=manifest, capsule=capsule, state=state,
            owns_hold=lambda: True, requested=lambda: False,
            update=lambda **values: state.update(values),
            check=lambda phase: events.append("check:" + phase),
            release=lambda: events.append("release"))
        def cleanup_http(s, attempt_id=None):
            events.append("http-drain")
            if unknown: raise RuntimeError("HTTP daemon UNKNOWN")
        session.ops = SimpleNamespace(terminate_owned_process=lambda record: events.append("client-stop"),
            cleanup_http=cleanup_http, cleanup_held_candidates=lambda s: events.append("held-drain"),
            quiesce_original=lambda s: events.append("original-quiesce"),
            cleanup_candidates=lambda s: events.append("other-drain"))
        return session, events

    def test_independent_watchdog_death_runs_http_drain_before_positive_keep_stopped_closure(self):
        session, events = self.scenario()
        monitor_ops = SimpleNamespace(verify_capsule=lambda: None, state=lambda: session.state,
            owned_hold=lambda: {"heartbeat": self.watchdog.utc(1)}, now=lambda: 10,
            core_alive=lambda: False, journal=lambda kind, **fields: events.append("watchdog:" + kind),
            recovery_attempted=lambda: False, mark_recovery_attempted=lambda: events.append("recovery-intent"),
            recover=lambda: self.core.restore(session))
        with mock.patch.object(self.held, "verify_outer", return_value="pinned"), \
             mock.patch.object(self.core, "transition_original", side_effect=lambda s, action, timeout: events.append("original:" + action)):
            result = self.watchdog.Watchdog(session.capsule, monitor_ops).step()
        self.assertEqual(result, "stopped")
        self.assertLess(events.index("http-drain"), events.index("original:stop"))
        self.assertLess(events.index("check:restore_drained"), events.index("release"))
        self.assertNotIn("original:start", events)
        self.assertTrue(session.state["original_kept_stopped"])
        self.assertFalse(session.state["restored"])
        self.assertTrue(session.state["hold_released"])

    def test_watchdog_unknown_http_drain_never_starts_original_or_releases_hold(self):
        session, events = self.scenario(unknown=True)
        monitor_ops = SimpleNamespace(verify_capsule=lambda: None, state=lambda: session.state,
            owned_hold=lambda: {"heartbeat": self.watchdog.utc(1)}, now=lambda: 10,
            core_alive=lambda: False, journal=lambda kind, **fields: events.append("watchdog:" + kind),
            recovery_attempted=lambda: False, mark_recovery_attempted=lambda: events.append("recovery-intent"),
            recover=lambda: self.core.restore(session))
        with mock.patch.object(self.held, "verify_outer", return_value="pinned"), \
             mock.patch.object(self.core, "transition_original", side_effect=lambda s, action, timeout: events.append("original:" + action)):
            result = self.watchdog.Watchdog(session.capsule, monitor_ops).step()
        self.assertEqual(result, "failed")
        self.assertNotIn("release", events)
        self.assertFalse(any(event.startswith("original:") for event in events))
        self.assertFalse(session.state["hold_released"])
        self.assertIsNotNone(session.state["active_attempt"])


class FrozenImportFaults(NoExternalOperations):
    module_file = "q03_http_client.py"

    def test_ignored_timestamp_bytecode_cannot_override_pinned_client_source(self):
        import importlib._bootstrap_external as bootstrap
        root = self.directory / "toolbox"
        package = root / "servebench"; package.mkdir(parents=True)
        sources = {"servebench/__init__.py": b"# pinned package\n",
                   "servebench/__main__.py": b"def main(argv):\n    return 0\n"}
        for relative, raw in sources.items(): (root / relative).write_bytes(raw)
        source = root / "servebench/__main__.py"
        info = source.stat()
        cache = Path(importlib.util.cache_from_source(str(source)))
        cache.parent.mkdir()
        code = compile('def main(argv):\n    raise RuntimeError("UNPINNED_BYTECODE_EXECUTED")\n', str(source), 'exec')
        cache.write_bytes(bootstrap._code_to_timestamp_pyc(code, int(info.st_mtime), info.st_size))
        plan = {"window_id": "a" * 32, "attempt_id": "b" * 32, "plan_sha256": "c" * 64,
            "url": "http://127.0.0.1:18973", "selection": {"mode": "off", "steering": "off"},
            "client": {"toolbox_path": str(root), "toolbox_files": {
                relative: hashlib.sha256(raw).hexdigest() for relative, raw in sources.items()},
                "output": str(self.directory / "smoke.json")}}
        old = {k: v for k, v in sys.modules.items() if k == "servebench" or k.startswith("servebench.")}
        for key in old: del sys.modules[key]
        try:
            result = self.http.run(plan)
            self.assertEqual(result["exit_code"], 0, result)
            self.assertIsNone(result["error"])
        finally:
            for key in list(sys.modules):
                if key == "servebench" or key.startswith("servebench."): del sys.modules[key]
            sys.modules.update(old)


class ClientReceiptBindingFaults(NoExternalOperations):
    def setUp(self):
        super().setUp()
        patch = mock.patch.object(self.http, "read_owned_bytes", lambda path: Path(path).read_bytes())
        patch.start(); self.addCleanup(patch.stop)

    def evidence(self):
        plan = {"window_id": "a" * 32, "attempt_id": "b" * 32, "plan_sha256": "c" * 64,
                "url": "http://127.0.0.1:18973", "selection": {"mode": "off", "steering": "off"},
                "client": {"output": str(self.directory / "smoke.json"), "toolbox_head": "fixed",
                           "prompts": {"name": "default", "count": 256, "sha256": "f" * 64}}}
        output = {"label": plan["attempt_id"], "server": {"url": plan["url"], "model": "deepseek-v4.1-flash"},
                  "workload": {"mode": "burst", "concurrency": 1, "rate": 1.0,
                               "warmup_s": 0.0, "duration_s": 1.0, "batches": 1,
                               "max_tokens": 16, "fixed_length": True, "seed": 0,
                               "max_inflight": 512, "context_tokens": 0,
                               "prompts": "default", "prompt_count": 256,
                               "prompts_sha256": "f" * 64, "extra_body": {}},
                  "summary": {"requests": {"failed": 0, "foreign": 0, "sent": 1, "rejected": 0}},
                  "requests": [{"error": None, "completion_tokens": 16}]}
        return plan, output

    def collect(self, plan, output):
        raw = self.http.canonical(output)
        path = Path(plan["client"]["output"]); path.write_bytes(raw)
        receipt = {"window_id": plan["window_id"], "attempt_id": plan["attempt_id"],
                   "plan_sha256": plan["plan_sha256"], "exit_code": 0, "error": None,
                   "output_sha256": self.http.digest(raw)}
        path.with_name("client-receipt.json").write_bytes(self.http.canonical(receipt))
        ops = self.http.HttpOps.__new__(self.http.HttpOps); ops.plan = plan
        return ops.collect_result()

    def test_boolean_numeric_aliases_cannot_satisfy_fixed_request_parameters(self):
        for field, value in (("concurrency", True), ("batches", True), ("seed", False)):
            plan, output = self.evidence()
            self.assertEqual(self.collect(plan, output)["result"], "passed")
            output["workload"][field] = value
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                self.collect(plan, output)

    def test_wrong_steering_body_cannot_be_reported_as_same_frozen_client(self):
        plan, output = self.evidence()
        self.assertEqual(self.collect(plan, output)["result"], "passed")
        output["workload"]["extra_body"] = {"vllm_xargs": {"steering_profile": "unreviewed", "steering_scale": 9}}
        with self.assertRaises(RuntimeError):
            self.collect(plan, output)


class AllFourGpuDrainFaults(NoExternalOperations):
    def test_http_daemon_cleanup_alone_cannot_clear_attempt_when_gpu_drain_unknown(self):
        import q03_held_window as held
        events = []
        state = {"active_process": {"pid": 1, "start_ticks": "owned"},
                 "active_attempt": {"attempt_id": "b" * 32}}
        session = SimpleNamespace(manifest={"jobs": [{"kind": "http_service"}]}, state=state,
            update=lambda **values: state.update(values))
        session.ops = SimpleNamespace(
            terminate_owned_process=lambda record: events.append("client-stop"),
            cleanup_http=lambda s, attempt_id=None: events.append("http-drain"),
            cleanup_held_candidates=lambda s, attempt_id=None: events.append("other-drain"))
        def gpu_check(phase):
            events.append("check:" + phase)
            raise RuntimeError("all-four GPU drain UNKNOWN")
        session.check = gpu_check
        with self.assertRaisesRegex(RuntimeError, "GPU drain UNKNOWN"):
            held.drain_attempt(session, {"descriptor": {"attempt_id": "b" * 32}}, None)
        self.assertEqual(events, ["client-stop", "http-drain", "other-drain", "check:stopped"])
        self.assertEqual(state["phase"], "attempt-draining")
        self.assertIsNotNone(state["active_attempt"])
        self.assertIsNotNone(state["active_process"])


class WorkerBootstrapSourceFaults(NoExternalOperations):
    module_file = "q03_http_worker.py"

    def test_main_and_spawn_preload_exact_worker_source_not_poisoned_import(self):
        import runpy
        import types
        import importlib._bootstrap_external as bootstrap
        script = HTTP_ROOT / "scripts/q03_http_worker.py"
        source = script.read_bytes()
        for role in ("__main__", "__mp_main__"):
            with self.subTest(role=role):
                poison = self.directory / "q03_http_worker.py"
                poison.write_text('raise RuntimeError("UNPINNED_WORKER_IMPORTED")\n')
                cache = Path(importlib.util.cache_from_source(str(poison)))
                cache.parent.mkdir(exist_ok=True)
                info = poison.stat()
                cache.write_bytes(bootstrap._code_to_timestamp_pyc(
                    compile(poison.read_bytes(), str(poison), 'exec'), int(info.st_mtime), info.st_size))
                called = []
                fake_cli = types.ModuleType("vllm.entrypoints.cli.main")
                def serve():
                    called.append(True)
                    self.assertTrue(hasattr(sys.modules["q03_http_worker"], "HttpObservation"))
                fake_cli.main = serve
                replacement = types.ModuleType("q03_http_worker")
                replacement.poisoned = True
                with mock.patch.dict(os.environ, {"Q03_HTTP_WORKER_SHA256": hashlib.sha256(source).hexdigest(),
                    "VLLM_ALLOW_INSECURE_SERIALIZATION": "0"}), \
                     mock.patch.dict(sys.modules, {"q03_http_worker": replacement,
                         "vllm.entrypoints.cli.main": fake_cli}), \
                     mock.patch.object(sys, "path", [str(self.directory), *sys.path]), \
                     mock.patch.object(sys, "argv", [str(script), "serve", "/models"]):
                    runpy.run_path(str(script), run_name=role)
                    actual = sys.modules["q03_http_worker"]
                    self.assertFalse(hasattr(actual, "poisoned"))
                    self.assertEqual(Path(actual.__file__), script)
                    self.assertEqual(actual.HttpObservation.__module__, "q03_http_worker")
                    self.assertEqual(called, [True] if role == "__main__" else [])

    def test_worker_hash_or_insecure_serialization_refuses_before_registration(self):
        source = Path(self.http.__file__).read_bytes()
        expected = hashlib.sha256(source).hexdigest()
        for sha, serialization in (("f" * 64, "0"), (expected, "1"), (expected, "")):
            with self.subTest(sha=sha, serialization=serialization), \
                 mock.patch.dict(os.environ, {"Q03_HTTP_WORKER_SHA256": sha,
                     "VLLM_ALLOW_INSECURE_SERIALIZATION": serialization}), \
                 mock.patch.dict(sys.modules, {"q03_http_worker": "unchanged-sentinel"}):
                with self.assertRaises(RuntimeError):
                    self.http.install_worker_source()
                self.assertEqual(sys.modules["q03_http_worker"], "unchanged-sentinel")
