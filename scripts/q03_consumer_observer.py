"""Closed binding for a separate tests-only Q03 consumer-observer owner lane.

No native execution on import, no automatic qualification, and no hot swapping.
The existing owner controller retains all admission, guard and drain operations.
"""
from __future__ import annotations
import copy
import hashlib
from pathlib import Path

LANE = "consumer_observer"
COMMIT = "8dd783aad9d52252816102511f36f4e6ee1f912a"
TREE = "461e76abd09203824588e8d809d42bf5a050b1a9"
TESTS_SUBTREE = "6d6088b3d791c37a49c85094360c251208d16944"
REVIEW_RECEIPT_SHA256 = "508707240bde121b25c5a4f06e41a3e49bf370895e4f6cd6ca1bcecb95b1d964"
OWNER_REFERENCE = "141f8647e885812095060ff9e7f8b991316ae538"
TEST_FILE = "tests/compile/fullgraph/q03_use_owner_fixture.py"
TEST_CASE = "test_real_model_owner_and_observed_use"
EXTENSION = "q03_use_owner_worker.Q03UseOwnerWorkerExtension"
SITE_FILE = "q03_consumer_site.py"
TEST_SOURCES = {
    'tests/__init__.py':
        'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    'tests/compile/__init__.py':
        'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    'tests/compile/fullgraph/__init__.py':
        'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    'tests/compile/fullgraph/q03_use_observer.py':
        '286c3c0e31537651125bfed28142b1dd8639107cc04fb1e6c3aa44007ca3abf1',
    'tests/compile/fullgraph/q03_use_owner_fixture.py':
        'c0ef7a2ff7eb57e7b6a1b5c19212ce3c49f27aed78f8a818202d5c35592661a9',
    'tests/compile/fullgraph/q03_use_owner_worker.py':
        'fe455bfae6f725ddcdbc08bf60beda56d8ec7ba822986f3c25a3c27c1360af18',
    'tests/compile/fullgraph/test_deepseek_v41_q03_owner.py':
        '6b33c8026bfc2b5e18274235473cf1c5c6620981ddf2f433817c40408c05f5ad',
}
RUNTIME_SOURCES = {
    '__init__.py':
        'fd3708e5a13abe98c566afd79a6b1987cef70c0548bb19c1218ff6a7f9d43346',
    'compilation/__init__.py':
        'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    'compilation/breakable_cudagraph.py':
        'a7b53a403c776df8195881c98fdba72e0e2662c15adc047cd5c79736247d19e2',
    'envs.py':
        '90760d268818ad20fd471ebc40c26ed65ca44bfd39e88b75e8e88d16b6146f66',
    'forward_context.py':
        '0af788757c1c1a7f390c20f611b61f13192ef50667bd2f3ef79c903e6b29721f',
    'model_executor/__init__.py':
        'adfaa8781e490a0e7bc2c0cd28bbeb0f8243594926df35227a056141a0f75ad2',
    'model_executor/layers/__init__.py':
        'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    'model_executor/layers/fused_moe/__init__.py':
        '64a32665be6d5afcfbaf7a0915717267278b66b880b908b0263ea58f08e51b5e',
    'model_executor/layers/fused_moe/runner/__init__.py':
        'e3c55cbb0d7a8bd47df6f3378835644645f5d3bc89b45793b5d5a02d013e5c58',
    'model_executor/layers/fused_moe/runner/moe_runner.py':
        'b2c4ed09f08d5599542822ab8c43b8dbef21075038767bf85bfddc81dc8412f4',
    'model_executor/layers/fused_moe/tilelang.py':
        '2f94d20ef384d1eed263dbf339e478d06e0a5de541e811cd19167e48c5f798e5',
    'models/__init__.py':
        'e3c55cbb0d7a8bd47df6f3378835644645f5d3bc89b45793b5d5a02d013e5c58',
    'models/deepseek_v4_1/__init__.py':
        'f2121743a038eb3c2c0d1d2ed6444675c6e54bd937d69887ead6bba4f9bcaa99',
    'models/deepseek_v4_1/nvidia/__init__.py':
        'e3c55cbb0d7a8bd47df6f3378835644645f5d3bc89b45793b5d5a02d013e5c58',
    'models/deepseek_v4_1/nvidia/model.py':
        'a05a7d48cd2283478b751f473f848374fa8e2a688d9f4b11276454b7881e597b',
    'models/deepseek_v4_1/tilelang/__init__.py':
        'afdbea47ed487105deb0a74a25bb4763382521ff8a6b7b0762ad0ecd9d74003a',
    'models/deepseek_v4_1/tilelang/input_quant_arena.py':
        '4a7bc4abe4524f10cce9d102ee385dfc07f8494b138c832555329e81689e6f88',
    'models/deepseek_v4_1/tilelang/mhc.py':
        '38b16afd2098b8c1a1ede620e9e943843e00390943dbe3b7626c4e2921bcacbb',
    'models/deepseek_v4_1/tilelang/moe.py':
        '5917c28f1e135cebdf3fbc4de1399a1dc77009cb995b38fe8790ddffc3e79460',
    'models/deepseek_v4_1/tilelang/routed_input.py':
        '37089de479455734fd77eb3a1c0f2808ea8fa4bea45170c6739c43f0f0fe1bf6',
    'plugins/__init__.py':
        'fb6e6ee432c5a4ae207aaffe4b546aca9381f56ee8498b0a3e0d9af2d289d023',
    'utils/__init__.py':
        'c58b3f45deeb98bccca22c945526fd15d483a2f5eb8a3c62cf7df218b081885f',
    'utils/import_utils.py':
        '3236d7a25f93d4989f977d7146f497cdf377ce5d40386baedd1b45f7bfa85f08',
    'utils/system_utils.py':
        '68ac51e07dbac9feb55bf1837a2240cea703018e20ce4219ad05abb1f9c204e5',
    'v1/__init__.py':
        'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    'v1/executor/__init__.py':
        '570534a51c0a8ef702717208ffb376148613da8363d5faae2e3bd44beaaa1365',
    'v1/executor/multiproc_executor.py':
        'dfcdc45f9d438ce21ad940de944cf68db40875925c88c36d73787c7116673a37',
    'v1/serial_utils.py':
        'd75c17a098231b2398cbdb7da6f9983e5b9229c0ba138b2874934d45fafd1f28',
    'v1/worker/__init__.py':
        'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    'v1/worker/gpu_worker.py':
        '1dfdf4fd410947bfd32d69b53224a4aea26d2767693596481aea4a14339e4bb3',
    'v1/worker/worker_base.py':
        '3a0ca44daf0b00fe626ca21a5b71d1a0e0e506759449a7fb8967292edccbcf35',
    'v1/worker/workspace.py':
        '18334e399b5d7749a0733d64fe6214906d0f9d276fc7c65806603de4c912449c',
}
DELTA_FILES = sorted([
    "tests/compile/fullgraph/q03_use_fixture.py",
    "tests/compile/fullgraph/q03_use_observer.py",
    "tests/compile/fullgraph/q03_use_owner_fixture.py",
    "tests/compile/fullgraph/q03_use_owner_worker.py",
    "tests/compile/fullgraph/q03_use_worker.py",
    "tests/compile/fullgraph/test_deepseek_v41_q03_owner.py",
    "tests/models/test_deepseek_v41_q03_use_observer.py",
    "tests/models/test_deepseek_v41_q03_use_owner.py",
    "tests/models/test_deepseek_v41_q03_use_worker.py",
])


def selected(binding):
    if "test_lane" not in binding:
        return False
    if binding["test_lane"] != LANE:
        raise RuntimeError("unknown separately bound owner test lane")
    return True


def closure():
    return dict(lane=LANE, commit=COMMIT, tree=TREE,
                tests_subtree=TESTS_SUBTREE, owner_reference=OWNER_REFERENCE,
                review_receipt_sha256=REVIEW_RECEIPT_SHA256,
                test_sources=copy.deepcopy(TEST_SOURCES),
                runtime_sources=copy.deepcopy(RUNTIME_SOURCES))


# An isolated no-site first exec checks the prefix BEFORE Python can look up a
# cached sitecustomize. The second interpreter and its spawn children inherit
# -B and the fresh never-written prefix, and run the same source-only guard.
INITIAL_BOOTSTRAP = r'''
import hashlib, importlib.machinery, os, pathlib, sys
p = pathlib.Path('/binding/sitecustomize.py')
if p.resolve() != p or hashlib.sha256(p.read_bytes()).hexdigest() != os.environ['Q03_CONSUMER_SITE_SHA256']:
    raise RuntimeError('consumer site source differs before startup')
resolved = importlib.machinery.PathFinder.find_spec('sitecustomize', os.environ['PYTHONPATH'].split(os.pathsep))
if resolved is None or resolved.origin != str(p) or os.environ.get('PYTHONSAFEPATH') != '1':
    raise RuntimeError('consumer site import origin differs before startup')
prefix = pathlib.Path('/cache/consumer-bytecode')
if prefix.exists() or prefix.is_symlink() or not sys.dont_write_bytecode:
    raise RuntimeError('consumer bytecode prefix is not fresh')
os.execv(sys.executable, [sys.executable, '-B', '-P', '-c', __ROLE__, *sys.argv[1:]])
'''
ROLE_BOOTSTRAP = r'''
import importlib, os, pathlib, runpy, sys
site = sys.modules.get('sitecustomize')
if site is None or site.__file__ != '/binding/sitecustomize.py':
    raise RuntimeError('consumer source guard did not run')
import tblib
if tblib.__version__ != '3.1.0' or tblib.__file__ != '/pytest-dependency/tblib-3.1.0-py3-none-any.whl/tblib/__init__.py':
    raise RuntimeError('tblib version or origin differs')
module = importlib.import_module('q03_use_owner_worker')
if pathlib.Path(module.__file__) != pathlib.Path('/validation/tests/compile/fullgraph/q03_use_owner_worker.py'):
    raise RuntimeError('combined worker origin differs')
mode = sys.argv[1]
if mode == 'pytest':
    import pytest
    sys.exit(pytest.main(sys.argv[2:]))
if mode != 'serve':
    raise RuntimeError('unknown consumer bootstrap role')
sys.argv = ['vllm.entrypoints.cli.main', *sys.argv[1:]]
runpy.run_module('vllm.entrypoints.cli.main', run_name='__main__', alter_sys=True)
'''
BOOTSTRAP = INITIAL_BOOTSTRAP.replace("__ROLE__", repr(ROLE_BOOTSTRAP))


def bind_plan(plan):
    """Derive one separate immutable plan; retain owner safety/state machine."""
    from q03_owner_model import canonical, digest, require
    require(plan["selection"]["mode"] == "dual", "consumer lane requires dual")
    binding, spec = plan["binding"], plan["spec"]
    spec["engine_kwargs"]["worker_extension_cls"] = EXTENSION
    spec["use_fixture"] = dict(
        owner_sha256=TEST_SOURCES[
            "tests/compile/fullgraph/test_deepseek_v41_q03_owner.py"],
        observer_sha256=TEST_SOURCES["tests/compile/fullgraph/q03_use_observer.py"],
        worker_sha256=TEST_SOURCES["tests/compile/fullgraph/q03_use_owner_worker.py"],
        fixture_sha256=TEST_SOURCES[TEST_FILE])
    spec["consumer_binding"] = closure()
    spec_sha = digest(canonical(spec))
    guard_path = Path(__file__).with_name(SITE_FILE)
    guard_sha = hashlib.sha256(guard_path.read_bytes()).hexdigest()
    for row in plan["ranks"]:
        environment = row["environment"]
        environment.pop("VLLM_Q03_PRODUCTION_OWNER_TEST")
        environment.pop("VLLM_Q03_PRODUCTION_OWNER_SPEC")
        environment.update(
            VLLM_Q03_USE_OWNER_TEST="1",
            VLLM_Q03_USE_OWNER_SPEC="/runtime/owner-spec.json",
            VLLM_WORKER_MULTIPROC_METHOD="spawn",
            PYTHONPYCACHEPREFIX="/cache/consumer-bytecode",
            PYTHONSAFEPATH="1",
            PYTHONPATH="/binding:" + binding["candidate"]["pytest_base_pythonpath"],
            Q03_CONSUMER_SITE_SHA256=guard_sha,
            Q03_CONSUMER_SPEC_SHA256=spec_sha)
        environment["Q03_EXPECTED_PYTHONPATH"] = environment["PYTHONPATH"]
        row["mounts"].append([
            str(Path(binding["checkout"]) / "scripts" / SITE_FILE),
            "/binding/sitecustomize.py", "ro"])
        previous = row["python_argv"]
        args = previous[3:]
        if row["rank"] == 0:
            old = args.index("--junitxml=/results/owner.xml") - 1
            args[old] = TEST_FILE + "::" + TEST_CASE
        else:
            args[args.index("--worker-extension-cls") + 1] = EXTENSION
        row["python_argv"] = ["-I", "-B", "-S", "-c", BOOTSTRAP, *args]
        row["name"] = row["name"].replace("q03-owner-", "q03-consumer-", 1)
    plan.update(test_lane=LANE, fixture_sha256=TEST_SOURCES[TEST_FILE],
                test_tree=TREE, spec_sha256=spec_sha)
    return plan


def validate_observation(value, plan):
    """Require bound records without upgrading UNKNOWN to a consumer-use pass."""
    from q03_owner_model import require
    observation = value.get("use_observation")
    require(isinstance(observation, dict)
            and observation.get("source_head") == plan["spec"]["source_head"]
            and observation.get("fixture_sha256") == TEST_SOURCES[TEST_FILE]
            and observation.get("readiness") == value["readiness"]
            and observation.get("outputs") == value["outputs"],
            "consumer observation identity differs")
    requests = observation.get("requests")
    require(isinstance(requests, list) and len(requests) == 6,
            "consumer request observations incomplete")
    owner_sha = plan["spec"]["use_fixture"]["owner_sha256"]
    rows = [observation.get("initial"), observation.get("stopped")]
    for step, item in enumerate(requests):
        token = "q03-use-step-" + str(step)
        require(item.get("request_id") == token, "consumer request token differs")
        ranks = item.get("ranks")
        require(isinstance(ranks, list), "consumer rank observations missing")
        for row in ranks:
            require(row.get("request_id") == token
                    and row.get("replay_scope") == "current_request",
                    "consumer evidence reused outside its request")
            counts = row.get("accepted_replay_path_counts")
            require(isinstance(counts, dict)
                    and set(counts) == {"eligible", "selected", "use", "fallback"}
                    and all(count is None or type(count) is int and count > 0
                            for count in counts.values()),
                    "missing consumer events must remain UNKNOWN/null")
            paths = row.get("accepted_paths")
            require(isinstance(paths, list)
                    and row.get("replay_path_status")
                    == ("OBSERVED" if paths else "UNKNOWN")
                    and all(item[0] == token for item in paths),
                    "consumer path observation scope/status differs")
            expected_counts = dict.fromkeys(counts)
            for item in paths:
                record = item[1]
                require(record[0][:2] == row["binding"][:2],
                        "consumer path rank/runner differs")
                for event in record[2]:
                    require(event[2] in expected_counts, "unknown observer category")
                    expected_counts[event[2]] = (expected_counts[event[2]] or 0) + 1
            require(counts == expected_counts,
                    "consumer path counts differ from request records")
            require(type(row.get("accepted_path_replay_count")) is int
                    and row["accepted_path_replay_count"] == len(paths),
                    "consumer path count differs")
            accepted = row.get("accepted")
            require(isinstance(accepted, list)
                    and all(item[0] == token for item in accepted)
                    and type(row.get("accepted_replay_count")) is int
                    and row["accepted_replay_count"] == len(accepted),
                    "consumer replay records differ")
        rows.append(ranks)
    for ranks in rows:
        require(isinstance(ranks, list) and len(ranks) == 4
                and all(type(row.get("rank")) is int for row in ranks)
                and {row["rank"] for row in ranks} == set(range(4)),
                "consumer four-rank evidence incomplete")
        for row in ranks:
            binding = row.get("binding")
            require(isinstance(binding, list) and len(binding) == 5
                    and type(binding[0]) is int and binding[0] == row["rank"],
                    "consumer rank/runner binding differs")
            require(row.get("source_head") == plan["spec"]["source_head"]
                    and row.get("owner_fixture_sha256") == owner_sha
                    and row.get("worker_extension_cls") == EXTENSION
                    and row.get("worker_sha256")
                    == plan["spec"]["use_fixture"]["worker_sha256"]
                    and row.get("observer_sha256")
                    == plan["spec"]["use_fixture"]["observer_sha256"],
                    "consumer worker evidence identity differs")
    require(all(row.get("ready") is False and row.get("active_request") is None
                for row in observation["initial"]), "consumer workers were not fresh")
    require(all(row.get("restoration_complete") is True
                and row.get("active_request") is None
                for row in observation["stopped"]), "consumer hooks not restored")
    retention = value.get("retention")
    require(isinstance(retention, list) and len(retention) == 4
            and all(type(row.get("rank")) is int for row in retention)
            and {row["rank"] for row in retention} == set(range(4))
            and all(row.get("arena_unchanged") is True
                    and type(row.get("retained_view_objects")) is int
                    and row["retained_view_objects"] > 0 for row in retention),
            "original collector-retention evidence incomplete")
    return dict(qualification="owner-retention-and-observation-only",
                consumer_use="UNQUALIFIED", missing_events="UNKNOWN")
