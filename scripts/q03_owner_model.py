#!/usr/bin/env python3
"""Fixed Q03 four-rank owner observation. Import and planning are inert.

Not a general argv executor, model service, benchmark, or controller handoff.
Only a NEW immutable held capsule may select this job. All daemon mutation is
journalled, remotely supervised and fenced before candidate/original overlap.
"""
from __future__ import annotations
import argparse
import copy
import fcntl
import hashlib
import importlib.machinery
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

from q03_held_window import exclusive_json, read_owned_bytes

SOURCE_HEAD = "154159dc3e38ca8b2321c01a153a1b2ccaf8663b"
SOURCE_TREE = "ae15047b376c7792062a2607c4ef1dfbc78df18a"
IMAGE = "sha256:90bd2bbc4709b14a2210a6752c707d9d16661180248949e183fa0c25b6691ff6"
RUNTIME_SHA = "24315da1b6138aef8acccb7244000feceef71b74486eeddce31bec25264919b9"
TEST_TREE = "0990e860d8c3c621f2889176cd38ff1600600646"
TEST_FILE = "tests/compile/fullgraph/test_deepseek_v41_q03_owner.py"
TEST_SHA = "6b33c8026bfc2b5e18274235473cf1c5c6620981ddf2f433817c40408c05f5ad"
WORKER_EXTENSION_CLS = "tests.compile.fullgraph.test_deepseek_v41_q03_owner.Q03OwnerWorkerExtension"
# One fixed tests-only bootstrap is shared by leader and followers. Runtime
# vLLM stays on the baked image path; no test source vLLM overlay is mounted.
OWNER_BOOTSTRAP = r"""
import hashlib, importlib, os, pathlib, runpy, sys
if os.environ.get('PYTHONPATH') != os.environ['Q03_EXPECTED_PYTHONPATH']:
    raise RuntimeError('image PYTHONPATH binding mismatch')
if (os.environ.get('PYTHONPYCACHEPREFIX') != '/cache/owner-bytecode'
        or sys.pycache_prefix != '/cache/owner-bytecode'
        or not sys.dont_write_bytecode
        or pathlib.Path('/cache/owner-bytecode').exists()):
    raise RuntimeError('owner imports require a fresh isolated bytecode prefix')
if os.environ.get('VLLM_ALLOW_INSECURE_SERIALIZATION', '0') != '0':
    raise RuntimeError('owner RPC requires native safe serialization')
root = pathlib.Path('/validation')
if root.resolve() != root or not (root / 'tests').is_dir() or (root / 'vllm').exists():
    raise RuntimeError('owner bootstrap requires a tests-only validation root')
fixture = root / 'tests/compile/fullgraph/test_deepseek_v41_q03_owner.py'
if fixture.resolve() != fixture or hashlib.sha256(fixture.read_bytes()).hexdigest() != '__FIXTURE_SHA__':
    raise RuntimeError('owner fixture source differs before import')
wheel = pathlib.Path('/pytest-dependency/tblib-3.1.0-py3-none-any.whl')
if hashlib.sha256(wheel.read_bytes()).hexdigest() != '670bb4582578134b3d81a84afa1b016128b429f3d48e6cbbaecc9d15675e984e':
    raise RuntimeError('tblib wheel hash mismatch')
sys.path.insert(0, str(root))
sys.path.append(str(wheel))
import tblib
if tblib.__version__ != '3.1.0' or tblib.__file__ != str(wheel) + '/tblib/__init__.py':
    raise RuntimeError('tblib version or import origin mismatch')
module = importlib.import_module('tests.compile.fullgraph.test_deepseek_v41_q03_owner')
if pathlib.Path(module.__file__).resolve() != fixture:
    raise RuntimeError('owner extension import origin differs')
if not isinstance(module.Q03OwnerWorkerExtension, type):
    raise RuntimeError('owner extension class is missing')
mode = sys.argv[1]
if mode == 'pytest':
    import pytest
    sys.exit(pytest.main(sys.argv[2:]))
if mode != 'serve':
    raise RuntimeError('unknown fixed owner bootstrap role')
sys.argv = ['vllm.entrypoints.cli.main', *sys.argv[1:]]
runpy.run_module('vllm.entrypoints.cli.main', run_name='__main__', alter_sys=True)
""".replace('__FIXTURE_SHA__', TEST_SHA)
PROFILE_SHA = "e935b492d1f76eeae09fe5a57b094c3bfbf8e63e83b37fe1be0e42fa46170a36"
MODES = ("off", "dual")
STEERING = ("off", "loaded_zero", "active")
PHASES = ("admitted", "prepared4", "created4", "startup_guards4", "started4",
          "config_ready", "steady_guards4", "acked", "leader_terminal", "drained")
SOURCE_FILES = ("q03_owner_model.py", "q03_rank_logs.py", "memguard-start.sh", "memguard.sh", "../bin/spark",
                "mesh_fabric.py", "kernel_backend.py", "kernel_readiness.py", "topology.py",
                "transport_profiles.py", "model_layout.py", "recipe.py")

FIXED_ENGINE_KWARGS = {'additional_config': {'ds41_steering': {'operator_version': 'attn-writer-project-bf16-rank-order-v1',
                                         'profile_path': 'hf:default',
                                         'profile_sha256': 'd7f0b997ea2748b03a353188583d494b2bfe7c40838aff98945941032da6a093',
                                         'schema_version': 1}},
 'async_scheduling': True,
 'attention_backend': 'TILELANG',
 'block_size': 256,
 'compilation_config': {'cudagraph_capture_sizes': [1,
                                                    2,
                                                    3,
                                                    4,
                                                    6,
                                                    8,
                                                    12,
                                                    16,
                                                    20,
                                                    24,
                                                    28,
                                                    32,
                                                    40,
                                                    48,
                                                    56,
                                                    64,
                                                    72,
                                                    80,
                                                    88,
                                                    96],
                        'cudagraph_mode': 'FULL_AND_PIECEWISE',
                        'custom_ops': ['all']},
 'disable_log_stats': False,
 'distributed_executor_backend': 'mp',
 'dtype': 'bfloat16',
 'enable_chunked_prefill': True,
 'enable_flashinfer_autotune': False,
 'enable_prefix_caching': True,
 'enforce_eager': False,
 'engram_config': {'disk_resident_scales': False, 'projection_tp': True, 'table_memory': 'disk'},
 'generation_config': 'vllm',
 'gpu_memory_utilization': 0.82,
 'jit_monitor_mode': 'warn',
 'kv_cache_memory_bytes': 11274289152,
 'limit_mm_per_prompt': {'image': 999},
 'linear_backend': 'tilelang',
 'load_format': 'b12x',
 'long_prefill_token_threshold': 8096,
 'max_cudagraph_capture_size': 96,
 'max_model_len': 1048576,
 'max_num_batched_tokens': 8192,
 'max_num_seqs': 16,
 'max_parallel_prefills': 1,
 'mm_processor_cache_gb': 1,
 'moe_backend': 'tilelang',
 'nnodes': 4,
 'node_rank': 0,
 'override_generation_config': {'temperature': 1.0, 'top_p': 1.0},
 'prefix_cache_retention_interval': 8192,
 'scheduler_reserve_full_isl': False,
 'seed': 0,
 'served_model_name': 'deepseek-v4.1-flash',
 'speculative_config': {'adaptive_verification_row_cost': 0.035,
                        'attention_backend': 'TILELANG',
                        'draft_sample_method': 'greedy',
                        'draft_tensor_parallel_size': 4,
                        'enable_adaptive_verification': True,
                        'method': 'dspark',
                        'num_speculative_tokens': 5,
                        'rejection_sample_method': 'block'},
 'tensor_parallel_size': 4,
 'tokenizer_mode': 'deepseek_v41',
 'tokenizer_name': '/models',
 'trust_remote_code': False}
REQUESTS = [['Finish the sentence: The sky is'],
 ['Finish the sentence: The sky is',
  'Name one primary color.',
  'What is two plus three?',
  'Complete: A triangle has',
  'Name a common garden flower.',
  'What comes after Monday?'],
 ['Finish the sentence: The sky is',
  'Name one primary color.',
  'What is two plus three?',
  'Complete: A triangle has',
  'Name a common garden flower.',
  'What comes after Monday?',
  'Name one planet.',
  'Complete: Water freezes into',
  'Name a small household object.',
  'What is the opposite of hot?',
  'Name a fruit that grows on trees.',
  'Complete: A book contains',
  'Name one musical instrument.',
  'What comes before three?',
  'Name a common shape.',
  'Complete: A clock tells']]
EFFECTIVE_KEYS = frozenset(['additional_config.ds41_steering',
 'attention_config.backend',
 'cache_config.block_size',
 'cache_config.cache_dtype',
 'cache_config.enable_prefix_caching',
 'cache_config.gpu_memory_utilization',
 'cache_config.kv_cache_memory_bytes',
 'cache_config.prefix_cache_retention_interval',
 'compilation_config.cudagraph_capture_sizes',
 'compilation_config.cudagraph_mode',
 'compilation_config.custom_ops',
 'compilation_config.max_cudagraph_capture_size',
 'compilation_config.mode',
 'compilation_config.pass_config.enable_sp',
 'engram_config.disk_resident_scales',
 'engram_config.projection_tp',
 'engram_config.table_memory',
 'kernel_config.enable_flashinfer_autotune',
 'kernel_config.linear_backend',
 'kernel_config.moe_backend',
 'load_config.load_format',
 'model_config.dtype',
 'model_config.enforce_eager',
 'model_config.generation_config',
 'model_config.max_model_len',
 'model_config.model',
 'model_config.multimodal_config.image_limit',
 'model_config.multimodal_config.mm_processor_cache_gb',
 'model_config.override_generation_config',
 'model_config.seed',
 'model_config.served_model_name',
 'model_config.tokenizer',
 'model_config.tokenizer_mode',
 'model_config.trust_remote_code',
 'observability_config.jit_monitor_mode',
 'parallel_config.data_parallel_size',
 'parallel_config.distributed_executor_backend',
 'parallel_config.local_world_size',
 'parallel_config.nnodes',
 'parallel_config.pipeline_parallel_size',
 'parallel_config.tensor_parallel_size',
 'parallel_config.use_ubatching',
 'parallel_config.world_size',
 'scheduler_config.async_scheduling',
 'scheduler_config.enable_chunked_prefill',
 'scheduler_config.long_prefill_token_threshold',
 'scheduler_config.max_num_batched_tokens',
 'scheduler_config.max_num_seqs',
 'scheduler_config.max_parallel_prefills',
 'scheduler_config.scheduler_reserve_full_isl',
 'speculative_config.adaptive_verification_row_cost',
 'speculative_config.draft_parallel_config.tensor_parallel_size',
 'speculative_config.draft_sample_method',
 'speculative_config.draft_tensor_parallel_size',
 'speculative_config.enable_adaptive_verification',
 'speculative_config.method',
 'speculative_config.num_speculative_tokens',
 'speculative_config.rejection_sample_method'])


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def json_bytes(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, "duplicate JSON field")
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))


def pinned_json(binding):
    require(isinstance(binding, dict) and set(binding) == {"path", "sha256"},
            "exact path/hash binding required")
    raw = read_owned_bytes(binding["path"])
    require(digest(raw) == binding["sha256"], "immutable input hash differs")
    return json_bytes(raw)


def is_owner(manifest):
    return any(j.get("kind") == "owner_model" for j in manifest.get("jobs", []))


def validate_selection(selection):
    require(isinstance(selection, dict) and set(selection) == {"mode", "steering"}
            and selection["mode"] in MODES and selection["steering"] in STEERING,
            "owner selection must be one fixed mode and steering state")


def validate_jobs(manifest):
    jobs = manifest.get("jobs")
    require(isinstance(jobs, list) and len(jobs) == 1, "owner attempt has one four-rank job")
    job = jobs[0]
    require(isinstance(job, dict) and set(job) == {"kind", "timeout_seconds", "selection"}
            and job["kind"] == "owner_model" and type(job["timeout_seconds"]) is int
            and 1 <= job["timeout_seconds"] <= 7200, "invalid bounded owner job")
    validate_selection(job["selection"])
    require(type(manifest.get("window_seconds")) is int and manifest["window_seconds"] == 10800, "owner outer lease must remain 10800 seconds")


def validate_paths(manifest, directory):
    """Pure path protection, also applied before an attempt directory is created."""
    from q03_original_spark_adapter import evidence_path
    binding = manifest["owner_model"]
    candidate = manifest["candidate"]
    immutable = [candidate[k] for k in ("tests_path", "runtime_manifest_path", "tblib_path",
                "build_receipt_path", "build_inputs_path", "git_toolchain_path")]
    immutable.append(manifest["preflight"]["independent_checkout"])
    immutable += [binding[k]["path"] for k in ("tests", "profile", "nodes", "effective_config")]
    immutable += [binding["prerequisites"][k]["path"] for k in ("terminal", "descriptor", "capsule")]
    for path in immutable:
        require(Path(path).is_absolute() and Path(path).resolve() == Path(path)
                and ".." not in Path(path).parts, "owner immutable path aliases another input")
    writable = [evidence_path(candidate[k], manifest) for k in ("cache_path", "output_path")]
    writable.append(evidence_path(directory, manifest))
    writable.append(evidence_path(str(Path(manifest["independent_root"]) / "owner-resources"), manifest))
    for path in writable:
        require(not any(path == Path(p) or path in Path(p).parents or Path(p) in path.parents
                        for p in immutable), "owner writable path overlaps immutable input")
    require(not any(a == b or a in b.parents or b in a.parents
                    for i, a in enumerate(writable) for b in writable[i+1:]),
            "owner writable/session roots overlap")


def load_spark():
    """The immutable controller inventory pins this module and its import closure."""
    source = Path(__file__).resolve().parents[1] / "bin/spark"
    loader = importlib.machinery.SourceFileLoader("_q03_owner_spark", str(source))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


def validate_prerequisites(binding, candidate):
    evidence = binding["prerequisites"]
    require(set(evidence) == {"terminal", "descriptor", "capsule"}, "missing prior gate chain")
    terminal, descriptor, capsule = (pinned_json(evidence[k]) for k in ("terminal", "descriptor", "capsule"))
    require(terminal.get("outcome") == "passed" and terminal.get("passed_gates") == ["p0", "g1", "g2"]
            and terminal.get("quiescence", {}).get("quiescent") is True
            and type(terminal["quiescence"].get("node_count")) is int
            and terminal["quiescence"]["node_count"] == 4,
            "actual prior P0/G1/G2 and all-four drain required")
    require(terminal["descriptor_sha256"] == digest(canonical(descriptor))
            and terminal["attempt_id"] == descriptor["attempt_id"]
            and terminal["window_id"] == descriptor["window_id"] == capsule["window_id"]
            and descriptor["capsule_sha256"] == evidence["capsule"]["sha256"]
            and [j["kind"] for j in descriptor["jobs"]] == ["p0", "g1", "g2"],
            "prior gate chain identity mismatch")
    old = capsule["manifest"]["candidate"]
    require(all(old[k] == candidate[k] for k in ("image", "component_head", "runtime_manifest_sha256")),
            "prior gates used a different native image/runtime")


def validate_owner_binding(manifest, ops):
    """Read-only admission. No model import, GPU use, writes or image distribution."""
    validate_jobs(manifest)
    runtime_raw = read_owned_bytes(manifest["candidate"]["runtime_manifest_path"])
    require(digest(runtime_raw) == RUNTIME_SHA and json_bytes(runtime_raw).get("source_head") == SOURCE_HEAD,
            "actual runtime bytes/source differ before owner admission")
    binding = manifest.get("owner_model")
    require(isinstance(binding, dict) and set(binding) == {
        "schema_version", "tests", "profile", "nodes", "effective_config", "prerequisites",
        "master_port", "handoff_seconds"}, "owner binding fields differ")
    require(type(binding["schema_version"]) is int and binding["schema_version"] == 1,
            "unsupported owner binding schema")
    require(type(binding["master_port"]) is int and 1024 <= binding["master_port"] <= 65535,
            "explicit candidate rendezvous port required")
    require(type(binding["handoff_seconds"]) is int and 1 <= binding["handoff_seconds"] <= 600,
            "finite reviewed ready/steady handoff bound required")
    candidate = manifest["candidate"]
    require(candidate["image"] == IMAGE and candidate["component_head"] == SOURCE_HEAD
            and candidate["runtime_manifest_sha256"] == RUNTIME_SHA,
            "owner adapter is fixed to native197/image90bd")
    tests = binding["tests"]
    require(set(tests) == {"path", "head", "tree", "fixture_sha256", "git_config_sha256"}
            and tests["tree"] == TEST_TREE and tests["fixture_sha256"] == TEST_SHA
            and re.fullmatch(r"[0-9a-f]{40}", tests["head"]), "reviewed owner test tree required")
    from q03_git_toolchain import validate_git_metadata
    validate_git_metadata(tests["path"], tests["git_config_sha256"])
    git = ["git", "--no-replace-objects", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
           "-C", tests["path"]]
    def read(*args):
        return ops.run([*git, *args]).stdout.strip()
    require(Path(tests["path"], ".git").is_dir()
            and read("rev-parse", "HEAD") == tests["head"]
            and read("rev-parse", "HEAD^{tree}") == TEST_TREE and not read("status", "--porcelain")
            and read("rev-parse", SOURCE_HEAD + "^{tree}") == SOURCE_TREE
            and read("diff", "--name-only", "--no-renames", SOURCE_HEAD, "HEAD", "--").splitlines() == [TEST_FILE]
            and ops.sha256(Path(tests["path"]) / TEST_FILE) == TEST_SHA,
            "owner test checkout is not the exact clean one-file delta from197")
    profile = pinned_json(binding["profile"])
    require(binding["profile"]["sha256"] == PROFILE_SHA and profile["shape"] == "tp4-ring"
            and profile["fabric"]["transport"] == "oneshot-ring4", "reviewed TP4 ring profile required")
    nodes = pinned_json(binding["nodes"])
    ranks = nodes.get("nodes")
    require(isinstance(ranks, list) and len(ranks) == 4
            and all(type(n.get("rank")) is int for n in ranks)
            and {n["rank"] for n in ranks} == set(range(4))
            and len({n["name"] for n in ranks}) == 4
            and [n["rank"] for n in ranks if n.get("head") is True] == [0],
            "exact head0 and follower1/2/3 node map required")
    effective = pinned_json(binding["effective_config"])
    require(set(effective) == set(STEERING) and all(isinstance(effective[s], dict)
            and set(effective[s]) == EFFECTIVE_KEYS for s in STEERING),
            "complete independently reviewed effective config for each steering state required")
    # Never learn the expected configuration from the first observed worker.
    for expected in effective.values():
        fixed = {"model_config.max_model_len": 1048576, "parallel_config.tensor_parallel_size": 4,
                 "parallel_config.pipeline_parallel_size": 1, "parallel_config.data_parallel_size": 1,
                 "parallel_config.nnodes": 4, "parallel_config.world_size": 4,
                 "parallel_config.local_world_size": 1, "parallel_config.distributed_executor_backend": "mp",
                 "scheduler_config.max_num_seqs": 16, "scheduler_config.max_num_batched_tokens": 8192,
                 "cache_config.kv_cache_memory_bytes": 11274289152, "cache_config.block_size": 256,
                 "speculative_config.method": "dspark", "speculative_config.num_speculative_tokens": 5,
                 "compilation_config.max_cudagraph_capture_size": 96,
                 "compilation_config.pass_config.enable_sp": False}
        require(all(canonical(expected[k]) == canonical(v) for k, v in fixed.items()),
                "effective config changes fixed capacity/topology/sequence parallelism")
    validate_prerequisites(binding, candidate)
    spark = load_spark()
    errors = spark.topology.problems(profile, nodes)
    require(not errors, "invalid ring node map: " + "; ".join(errors))
    result = copy.deepcopy(binding)
    result.update(candidate=copy.deepcopy(candidate), independent_root=manifest["independent_root"],
                  profile_value=profile, nodes_value=nodes, effective_value=effective,
                  checkout=manifest["preflight"]["independent_checkout"],
                  preflight=copy.deepcopy(manifest["preflight"]))
    return result


def engine_kwargs(binding, selection):
    value = copy.deepcopy(FIXED_ENGINE_KWARGS)
    value["worker_extension_cls"] = WORKER_EXTENSION_CLS
    value.update(master_addr=binding["profile_value"]["distributed"]["master_addr"],
                 master_port=binding["master_port"])
    if selection["steering"] == "off":
        value.pop("additional_config")
    return value


def follower_arguments(kwargs, rank):
    args = ["-B", "-c", OWNER_BOOTSTRAP, "serve", "/models"]
    values = dict(kwargs, node_rank=rank)
    for key, value in values.items():
        key = "tokenizer" if key == "tokenizer_name" else key.replace("_", "-")
        if key == "disable-log-stats":
            require(value is False, "owner fixture must retain statistics")
            continue  # store_true, not BooleanOptionalAction in native197.
        if type(value) is bool:
            args.append("--" + ("" if value else "no-") + key)
        else:
            args += ["--" + key, canonical(value).decode() if isinstance(value, (dict, list)) else str(value)]
    return [*args, "--headless"]


def owner_plan(binding, window_id, attempt_id, selection):
    """Closed pure derivation. No caller-supplied commands, environments or mounts."""
    validate_selection(selection)
    for value in (window_id, attempt_id):
        require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value), "canonical owner UUID required")
    spark = load_spark()
    profile = copy.deepcopy(binding["profile_value"])
    profile["container"]["image"] = IMAGE
    runtime_raw = read_owned_bytes(binding["candidate"]["runtime_manifest_path"])
    require(digest(runtime_raw) == RUNTIME_SHA, "runtime bytes changed before planning")
    runtime = json_bytes(runtime_raw)
    # Native image source trees, never the owner test tree or original188 labels.
    require(runtime.get("source_head") == SOURCE_HEAD
            and runtime.get("source_tree", SOURCE_TREE) == SOURCE_TREE,
            "runtime source identity differs from pinned197 evidence")
    receipt_raw = read_owned_bytes(binding["candidate"]["build_receipt_path"])
    require(digest(receipt_raw) == binding["candidate"]["build_receipt_sha256"], "build receipt changed before planning")
    labels = json_bytes(receipt_raw)["image_labels"]
    require(labels.get("local.spark3.vllm.tree") == SOURCE_TREE, "native197 image labels differ")
    profile["container"]["expected_labels"] = labels
    kwargs = engine_kwargs(binding, selection)
    steering = {"state": selection["steering"]}
    if selection["steering"] != "off":
        steering.update(profile_name="heretic-1017-attn-v1", scale=0 if selection["steering"] == "loaded_zero" else 1)
    spec = dict(source_head=SOURCE_HEAD, mode=selection["mode"], model_path="/models",
                runtime_manifest_path="/runtime/runtime-manifest.json", runtime_manifest_sha256=RUNTIME_SHA,
                output_path="/results/owner.json", requests=copy.deepcopy(REQUESTS), max_tokens=16,
                engine_kwargs=kwargs, steering=steering,
                expected_effective_config=copy.deepcopy(binding["effective_value"][selection["steering"]]),
                handshake=dict(window_id=window_id, attempt_id=attempt_id,
                               ready_path="/results/owner-ready.json", ack_path="/results/owner-steady-ack.json",
                               timeout_seconds=binding["handoff_seconds"]))
    candidate = binding["candidate"]
    resources = str(Path(binding["independent_root"]) / "owner-resources" / window_id / attempt_id)
    ranks = []
    for node in sorted(binding["nodes_value"]["nodes"], key=lambda n: n["rank"]):
        rank = node["rank"]
        env = spark.expected_environment(profile, node)
        require(env.get("PYTHONPATH") == candidate["pytest_base_pythonpath"], "profile changes baked Python path")
        env.pop("PYTHONPATH")  # Preserve the baked image setting, never overlay it.
        env.update(VLLM_Q03_PRODUCTION_OWNER_TEST="1", VLLM_Q03_PRODUCTION_OWNER_SPEC="/runtime/owner-spec.json",
                   VLLM_DS41_Q03_MHC_MXFP8="1" if selection["mode"] == "dual" else "0",
                   PYTHONDONTWRITEBYTECODE="1", PYTHONPYCACHEPREFIX="/cache/owner-bytecode",
                   Q03_EXPECTED_PYTHONPATH=candidate["pytest_base_pythonpath"])
        root = str(Path(resources) / str(rank))
        mounts = [[s.format(home=profile["host"]["home"]), d, m] for s, d, m in profile["container"]["mounts"] if d != "/cache"]
        require(all(m == "ro" for _, _, m in mounts), "profile contains unapproved writable mount")
        mounts += [[str(Path(binding["tests"]["path"]) / "tests"), "/validation/tests", "ro"],
                   [candidate["runtime_manifest_path"], "/runtime/runtime-manifest.json", "ro"],
                   [root + "/spec.json", "/runtime/owner-spec.json", "ro"],
                   [candidate["tblib_path"], "/pytest-dependency", "ro"],
                   [root + "/cache", "/cache", "rw"], [root + "/results", "/results", "rw"]]
        python = (["-B", "-c", OWNER_BOOTSTRAP, "pytest", "-p", "no:cacheprovider", "-q",
                   TEST_FILE + "::test_real_model_owner_and_capture_retention", "--junitxml=/results/owner.xml"]
                  if rank == 0 else follower_arguments(kwargs, rank))
        name = "q03-owner-" + window_id + "-" + attempt_id + "-" + str(rank)
        ranks.append(dict(rank=rank, node=node, name=name, root=root, environment=env,
                          mounts=mounts, python_argv=python))
    plan = dict(schema_version=1, window_id=window_id, attempt_id=attempt_id, selection=copy.deepcopy(selection),
                image=IMAGE, fixture_sha256=TEST_SHA, test_tree=TEST_TREE, runtime_sha256=RUNTIME_SHA,
                spec=spec, spec_sha256=digest(canonical(spec)), ranks=ranks,
                docker_args=profile["container"]["docker_run_args"], binding=copy.deepcopy(binding))
    plan["plan_sha256"] = digest(canonical(plan))
    return plan


class Journal:
    """One fenced writer. Intent survives process death independently of core state."""
    def __init__(self, session, plan, *, create=False):
        self.session, self.plan = session, plan
        self.directory = session.directory / "attempts" / plan["attempt_id"] / "owner"
        if create:
            self.directory.mkdir(mode=0o700, exist_ok=False)
            exclusive_json(self.directory / "plan.json", plan)
            exclusive_json(self.directory / "state.json", dict(
                phase="admitted", sequence=0, epochs=[], ranks=[
                    dict(rank=i, container_id=None, config_sha256=None, guard_intent=False,
                         guard_phase=None, removed=False) for i in range(4)]))
        raw = read_owned_bytes(self.directory / "plan.json")
        require(json_bytes(raw) == plan, "retained owner plan changed")
        self.state = json_bytes(read_owned_bytes(self.directory / "state.json"))
        self.lock = open(self.directory / "owner.lock", "a")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def close(self):
        self.lock.close()

    def save(self):
        from q03_window import atomic
        atomic(self.directory / "state.json", self.state)

    def event(self, kind, **fields):
        sequence = self.state["sequence"] + 1
        exclusive_json(self.directory / (f"{sequence:06d}.json"), dict(
            sequence=sequence, kind=kind, window_id=self.plan["window_id"],
            attempt_id=self.plan["attempt_id"], **fields))
        self.state["sequence"] = sequence
        self.save()

    def phase(self, phase):
        require(phase in PHASES, "unknown owner phase")
        self.event("phase", phase=phase)
        self.state["phase"] = phase
        self.save()


def validate_ready(raw, plan):
    require(isinstance(raw, bytes) and len(raw) <= 4 * 1024 * 1024, "invalid ready receipt size/type")
    value = json_bytes(raw)
    require(isinstance(value, dict) and set(value) == {
        "schema_version", "window_id", "attempt_id", "spec_sha256", "reports", "reports_sha256"}
        and type(value["schema_version"]) is int and value["schema_version"] == 1
        and all(value[k] == plan[k] for k in ("window_id", "attempt_id", "spec_sha256")),
        "ready receipt authority mismatch")
    reports = value["reports"]
    require(isinstance(reports, list) and len(reports) == 4
            and digest(canonical(reports)) == value["reports_sha256"], "ready reports incomplete/corrupt")
    seen = set()
    for report in reports:
        rank = report["worker_rank"]
        require(type(rank) is int and rank in range(4) and rank not in seen, "duplicate/foreign worker rank")
        seen.add(rank)
        require(all(type(report[k]) is int and report[k] == v for k, v in
                    (("tp_rank", rank), ("config_rank", rank), ("node_rank", rank),
                     ("node_rank_within_dp", rank), ("local_rank", 0))), "worker rank mapping mismatch")
        for key in ("effective_config", "runner_config"):
            require(canonical(report[key]) == canonical(plan["spec"]["expected_effective_config"]),
                    "actual worker/runner configuration differs")
        owner = report["owner"]
        require(type(owner["rank"]) is int and owner["rank"] == rank and owner["sm"] == [12, 1]
                and type(owner["tp"]) is int and owner["tp"] == 4
                and owner["sequence_parallel"] is False
                and owner["owner_present"] is (plan["selection"]["mode"] == "dual"),
                "actual owner/SM121/TP4 evidence differs")
        steering = owner["steering_profile"]
        if plan["selection"]["steering"] == "off":
            require(steering is None, "steering off unexpectedly loaded")
        else:
            expected = plan["spec"]["engine_kwargs"]["additional_config"]["ds41_steering"]
            require(isinstance(steering, dict) and steering["name"] == plan["spec"]["steering"]["profile_name"]
                    and steering["artifact_sha256"] == expected["profile_sha256"], "loaded steering identity differs")
    return value


def validate_container(item, plan, record, *, initial=False):
    from q03_window import held_container_sha256
    rank = record["rank"]
    wanted = plan["ranks"][rank]
    require(isinstance(item, dict) and re.fullmatch(r"[0-9a-f]{64}", item.get("Id", ""))
            and item["Id"] == record["container_id"] and item.get("Image") == IMAGE,
            "owner exact container/image identity differs")
    config, host = item["Config"], item["HostConfig"]
    labels = config.get("Labels", {})
    require(all(labels.get(k) == v for k, v in {
        "q03.window": plan["window_id"], "q03.attempt": plan["attempt_id"], "q03.job": "owner_model",
        "q03.rank": str(rank), "q03.plan": plan["plan_sha256"]}.items()), "owner container labels differ")
    if initial:
        require("OomKillDisable" in host and
                (host["OomKillDisable"] is None or host["OomKillDisable"] is False),
                "owner OOM killing must remain enabled")
        require(config.get("Entrypoint") == ["python3"] and config.get("Cmd") == wanted["python_argv"]
                and config.get("WorkingDir") == "/validation" and host.get("NetworkMode") == "host"
                and host.get("IpcMode") == "host" and host.get("Privileged") is False
                and host.get("AutoRemove") is False and host.get("RestartPolicy", {}).get("Name") in ("", "no")
                and host.get("Memory") == 112 * 1024**3 and host.get("MemorySwap") == 112 * 1024**3
                and host.get("ShmSize") == 16 * 1024**3 and host.get("CpusetCpus") == "5-9,15-19",
                "owner transport/resource/command configuration differs")
        expected_env = dict(wanted["environment"], PYTHONPATH=plan["binding"]["candidate"]["pytest_base_pythonpath"])
        environment = config.get("Env", [])
        require(all([v for v in environment if v.startswith(k + "=")] == [k + "=" + str(val)]
                    for k, val in expected_env.items()), "owner per-node environment differs")
        actual = sorted((m.get("Source"), m.get("Destination"), m.get("RW")) for m in item.get("Mounts", []))
        # The one DRM mount is part of the fixed existing profile's Docker arguments.
        expected_mounts = [(s, d, m == "rw") for s, d, m in wanted["mounts"]]
        expected_mounts.append(("/dev/dri/by-path/pci-000f:01:00.0-card", "/dev/dri/card0", True))
        require(actual == sorted(expected_mounts), "owner mount plan differs")
        requests = host.get("DeviceRequests")
        require(isinstance(requests, list) and len(requests) == 1 and requests[0].get("Count") == -1
                and requests[0].get("Capabilities") == [["gpu"]], "owner GPU request differs")
        devices = host.get("Devices")
        require(devices == [{"PathOnHost": "/dev/infiniband", "PathInContainer": "/dev/infiniband",
                             "CgroupPermissions": "rwm"}]
                # Docker may return the canonical CAP_ spelling in inspect.
                # Accept only this single capability, with no extras/duplicates.
                and host.get("CapAdd") in (["IPC_LOCK"], ["CAP_IPC_LOCK"])
                and sorted(host.get("SecurityOpt") or []) == ["label=disable", "seccomp=unconfined"]
                and host.get("DeviceCgroupRules") == ["c 226:* rw"],
                "owner RDMA/device/capability/security profile differs")
        limits = {v["Name"]: (v["Soft"], v["Hard"]) for v in host.get("Ulimits", [])}
        require(len(limits) == len(host.get("Ulimits", [])) and
                all(limits.get(k) == v for k, v in {"memlock": (-1, -1),
                    "stack": (67108864, 67108864), "core": (0, 0)}.items()),
                "owner memlock/stack/core limits differ")
        require(item["State"].get("Running") is False, "owner container must be inert before start")
    else:
        require(held_container_sha256(item) == record["config_sha256"], "owner immutable config drift")
    return held_container_sha256(item)


def require_guard(state, plan, record, phase):
    require(isinstance(state, dict) and state.get("owned") is True
            and state.get("active") is True and state.get("phase") == phase
            and state.get("container_id") == record["container_id"],
            "owned exact-ID " + phase + " guard not proven active")


def run_owner(session, plan, poll=None, ops=None):
    """Real state machine. Every boundary is injectable, never replaced by a mock loop."""
    journal = Journal(session, plan, create=True)
    ops = OwnerOps(session, plan, journal) if ops is None else ops
    ops.journal = journal
    def check():
        if poll is not None:
            poll()
        ops.check_authority()
    records = journal.state["ranks"]
    try:
        check()
        for row in records:
            check()
            journal.event("prepare-intent", rank=row["rank"])
            ops.action(row["rank"], "prepare", row)
        ops.seal_all()
        journal.phase("prepared4")
        for row in records:
            check()
            journal.event("create-intent", rank=row["rank"])
            container_id = ops.action(row["rank"], "create", row)
            require(isinstance(container_id, str) and re.fullmatch(r"[0-9a-f]{64}", container_id),
                    "create returned no exact ID; outcome UNKNOWN")
            row["container_id"] = container_id
            journal.save()  # Before inspect or ANY start, including another rank.
            item = ops.inspect(row["rank"], row)
            # Retain exact config even when the inert create fails validation.
            from q03_window import held_container_sha256
            row["config_sha256"] = held_container_sha256(item)
            journal.save()
            validate_container(item, plan, row, initial=True)
        ops.seal_all()
        journal.phase("created4")
        for row in records:
            check()
            guard = ops.guard_state(row["rank"], row)
            require(guard.get("absent") is True, "refusing an existing guard unit")
            row["guard_intent"] = True
            journal.save()
            journal.event("startup-guard-intent", rank=row["rank"], container_id=row["container_id"])
            ops.action(row["rank"], "startup_guard", row)
            require_guard(ops.guard_state(row["rank"], row), plan, row, "startup")
            row["guard_phase"] = "startup"
            journal.save()
        ops.seal_all()
        journal.phase("startup_guards4")
        for row in records:
            check()
            require_guard(ops.guard_state(row["rank"], row), plan, row, "startup")
            journal.event("start-intent", rank=row["rank"], container_id=row["container_id"])
            ops.action(row["rank"], "start", row)
        ops.seal_all()
        journal.phase("started4")
        while True:
            check()
            for row in records:
                item = ops.inspect(row["rank"], row)
                validate_container(item, plan, row)
                require(item["State"].get("Running") is True, "owner rank exited before configuration readiness")
                require_guard(ops.guard_state(row["rank"], row), plan, row, "startup")
            raw = ops.read_ready()
            if raw is not None:
                ready = validate_ready(raw, plan)
                exclusive_json(journal.directory / "ready-observed.json", ready)
                break
            ops.sleep(.2)
        journal.phase("config_ready")
        for row in records:
            check()
            validate_container(ops.inspect(row["rank"], row), plan, row)
            require_guard(ops.guard_state(row["rank"], row), plan, row, "startup")
            journal.event("steady-guard-intent", rank=row["rank"], container_id=row["container_id"])
            ops.action(row["rank"], "steady_guard", row)
            require_guard(ops.guard_state(row["rank"], row), plan, row, "steady")
            row["guard_phase"] = "steady"
            journal.save()
        ops.seal_all()
        journal.phase("steady_guards4")
        check()
        for row in records:
            item = ops.inspect(row["rank"], row)
            validate_container(item, plan, row)
            require(item["State"].get("Running") is True, "rank exited before steady acknowledgment")
            require_guard(ops.guard_state(row["rank"], row), plan, row, "steady")
        check()
        ack = dict(schema_version=1, window_id=plan["window_id"], attempt_id=plan["attempt_id"],
                   spec_sha256=plan["spec_sha256"], ready_sha256=digest(raw), steady_guard_ready=True)
        journal.event("ack-intent", ack_sha256=digest(canonical(ack)))
        ops.publish_ack(ack)
        journal.phase("acked")
        while True:
            check()
            items = [ops.inspect(row["rank"], row) for row in records]
            for row, item in zip(records, items):
                validate_container(item, plan, row)
                require(item["State"].get("OOMKilled") is False, "owner rank was OOM killed")
            leader = items[0]["State"]
            if leader.get("Running") is False:
                require(type(leader.get("ExitCode")) is int and leader["ExitCode"] == 0,
                        "owner leader exited unsuccessfully")
                break
            require(all(item["State"].get("Running") is True for item in items), "owner follower exited early")
            for row in records:
                require_guard(ops.guard_state(row["rank"], row), plan, row, "steady")
            ops.sleep(.2)
        result = ops.collect_result()
        exclusive_json(journal.directory / "result.json", result)
        journal.event("leader-result", result=result)
        journal.phase("leader_terminal")
        return result
    finally:
        # Parent's existing supervised-process cleanup and the same journalled
        # quiesce path run even if this subprocess is terminated or disappears.
        journal.close()


def quiesce_owner(session, attempt_id, ops=None):
    """Same fenced daemon drain for normal completion, failure and watchdog."""
    path = session.directory / "attempts" / attempt_id / "owner" / "plan.json"
    if not path.exists():
        require(not path.parent.exists(), "partial owner journal: UNKNOWN")
        return dict(attempt_id=attempt_id, node_count=4, quiescent=True, no_owner_intent=True)
    plan = json_bytes(read_owned_bytes(path))
    require(plan["attempt_id"] == attempt_id and plan["window_id"] == session.capsule["window_id"],
            "foreign owner journal")
    expected = dict(plan)
    wanted = expected.pop("plan_sha256")
    require(digest(canonical(expected)) == wanted, "owner retained plan digest differs")
    journal = Journal(session, plan)
    ops = OwnerOps(session, plan, journal, recovering=True) if ops is None else ops
    ops.journal = journal
    try:
        from q03_rank_logs import quiesce_with_logs
        return quiesce_with_logs(journal, ops, validate_container)
    finally:
        journal.close()


# Fixed filesystem preparation only. Inputs are derived by owner_plan, not an
# operator-supplied Python/argv payload. Remote epoch supervision owns this action.
PREPARE = r'''
import hashlib,json,os,pathlib,stat,sys
v=json.loads(sys.argv[1]); root=pathlib.Path(v['root']); base=pathlib.Path(v['independent_root'])
os.umask(0o077)
assert root.is_absolute() and root.resolve()==root and root.is_relative_to(base) and root!=base
for p in (base,*base.parents):
 s=p.stat(); assert s.st_uid in (0,os.getuid()) and not s.st_mode&0o022
for item in v['files']:
 p=pathlib.Path(item['path']); assert p.resolve()==p and p.is_file()
 assert hashlib.sha256(p.read_bytes()).hexdigest()==item['sha256']
for p in v['directories']:
 assert pathlib.Path(p).is_dir()
root.mkdir(mode=0o700,parents=True,exist_ok=False)
for name in ('cache','results'): (root/name).mkdir(mode=0o700)
raw=v['spec'].encode(); assert hashlib.sha256(raw).hexdigest()==v['spec_sha256']
with (root/'spec.json').open('xb') as f:
 f.write(raw); f.flush(); os.fsync(f.fileno())
fd=os.open(root,os.O_DIRECTORY); os.fsync(fd); os.close(fd)
print(json.dumps({'root':str(root),'spec_sha256':v['spec_sha256']}))
'''

GUARD_EXEC = r'''
import hashlib,json,os,pathlib,stat,sys
v=json.loads(sys.argv[1]); p=pathlib.Path(v['script'])
for item in v['files']:
 q=pathlib.Path(item['path']); assert q.resolve()==q and q.is_file()
 s=q.stat(); assert s.st_uid in (0,os.getuid()) and not s.st_mode&0o022
 assert hashlib.sha256(q.read_bytes()).hexdigest()==item['sha256']
os.execv(str(p),[str(p),*v['arguments']])
'''

INPUT_PROBE = r'''
import hashlib,json,pathlib,subprocess,sys
v=json.loads(sys.argv[1]); files={}
for item in v['files']:
 p=pathlib.Path(item['path']); assert p.resolve()==p and p.is_file()
 h=hashlib.sha256(p.read_bytes()).hexdigest(); assert h==item['sha256']; files[str(p)]=h
for p in v['directories']: assert pathlib.Path(p).is_dir()
g=['git','--no-replace-objects','-c','core.fsmonitor=false','-c','core.hooksPath=/dev/null','-C',v['tests']['path']]
def git(*a): return subprocess.check_output(g+list(a),text=True,timeout=30).strip()
assert git('rev-parse','HEAD')==v['tests']['head'] and git('rev-parse','HEAD^{tree}')==v['tests']['tree']
assert pathlib.Path(v['tests']['path'],'.git').is_dir()
for relative in ('commondir','config.worktree','objects/info/alternates','objects/info/http-alternates'):
 p=pathlib.Path(v['tests']['path'],'.git',relative); assert not p.exists() and not p.is_symlink()
assert not git('status','--porcelain')
assert git('rev-parse',v['base_head']+'^{tree}')==v['base_tree']
assert git('diff','--name-only','--no-renames',v['base_head'],'HEAD','--').splitlines()==[v['fixture']]
deployment=v['deployment']
g=['git','--no-replace-objects','-c','core.fsmonitor=false','-c','core.hooksPath=/dev/null','-C',deployment['path']]
assert pathlib.Path(deployment['path'],'.git').is_dir()
assert git('rev-parse','HEAD')==deployment['head'] and not git('status','--porcelain')
assert git('config','--get','remote.origin.url').rstrip('/').removesuffix('.git')==deployment['repository'].rstrip('/').removesuffix('.git')
image=json.loads(subprocess.check_output(['docker','image','inspect',v['image']],text=True,timeout=30))
assert len(image)==1 and image[0]['Id']==v['image'] and image[0]['Config']['Labels']==v['image_labels']
assert [s for s in image[0]['Config']['Env'] if s.startswith('PYTHONPATH=')]==['PYTHONPATH='+v['pythonpath']]
print(json.dumps({'image':image[0]['Id'],'files':files,'test_tree':v['tests']['tree'],'deployment_head':deployment['head']}))
'''


def input_contract(binding):
    from q03_window import TBLIB_WHEEL, TBLIB_SHA256
    candidate = binding["candidate"]
    files = [{"path": str(Path(binding["tests"]["path"]) / TEST_FILE), "sha256": TEST_SHA},
             {"path": str(Path(binding["tests"]["path"]) / ".git/config"), "sha256": binding["tests"]["git_config_sha256"]},
             {"path": candidate["runtime_manifest_path"], "sha256": RUNTIME_SHA},
             {"path": str(Path(candidate["tblib_path"]) / TBLIB_WHEEL), "sha256": TBLIB_SHA256}]
    for name in SOURCE_FILES:
        path = Path(binding["checkout"]) / "scripts" / name
        files.append({"path": str(path.resolve()), "sha256": digest(read_owned_bytes(path.resolve()))})
    receipt_raw = read_owned_bytes(candidate["build_receipt_path"])
    require(digest(receipt_raw) == candidate["build_receipt_sha256"], "build evidence changed")
    receipt = json_bytes(receipt_raw)
    publication = binding["preflight"]["publication"]
    require(re.fullmatch(r"[0-9a-f]{40}", publication.get("sha", "")), "pinned deployment head required")
    return dict(files=files, directories=[s.format(home=binding["profile_value"]["host"]["home"])
                 for s, d, m in binding["profile_value"]["container"]["mounts"] if m == "ro"],
                tests=binding["tests"], base_head=SOURCE_HEAD, base_tree=SOURCE_TREE, fixture=TEST_FILE,
                image=IMAGE, image_labels=receipt["image_labels"], pythonpath=candidate["pytest_base_pythonpath"],
                deployment=dict(path=binding["checkout"], head=publication["sha"], repository=publication["url"]))


def remote_nodes(binding):
    """Bind logical ranks to the already registered physical inventory/transport."""
    pre = binding["preflight"]
    nodes = sorted(pre["nodes"], key=lambda n: n["rank"])
    require(len(nodes) == 4 and all(type(n["rank"]) is int for n in nodes)
            and [n["rank"] for n in nodes] == [1, 2, 3, 4]
            and [n["rank"] for n in nodes if n.get("local") is True] == [1],
            "preflight physical node ranks/local head differ")
    desired = sorted(binding["nodes_value"]["nodes"], key=lambda n: n["rank"])
    require(len(desired) == 4 and all(type(n["rank"]) is int for n in desired)
            and [n["rank"] for n in desired] == [0, 1, 2, 3]
            and [n["name"] for n in nodes] == [n["name"] for n in desired]
            and len({n["name"] for n in nodes}) == 4, "owner/preflight node map differs")
    user = binding["nodes_value"]["ssh_user"]
    def endpoint(value):
        require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.@:-]+", value)
                and not value.startswith("-") and value.count("@") <= 1, "invalid physical SSH endpoint")
        value = value if "@" in value else user + "@" + value
        require(value.split("@", 1)[0] == user, "foreign SSH user")
        return value
    targets, hostnames = [], []
    result = []
    for node, logical in zip(nodes, desired):
        # Exact endpoint equality intentionally rejects hostname/IP aliases.
        target = endpoint(node["ssh_host"])
        require(target == endpoint(logical.get("ssh_host", logical["name"])),
                "logical rank uses another physical SSH endpoint/alias")
        raw = read_owned_bytes(node["inventory_path"])
        require(digest(raw) == node["inventory_sha256"], "physical inventory identity changed")
        receipt = json_bytes(raw)
        hostname = receipt["inventory"]["hostname"]
        require(receipt.get("exit_code") == 0 and receipt.get("node") == node["name"]
                and not receipt["inventory"].get("pin_errors")
                and isinstance(hostname, str) and hostname, "invalid registered physical inventory")
        targets.append(target)
        hostnames.append(hostname)
        result.append(dict(node, expected_hostname=hostname, bound_ssh_target=target))
    require(len(set(targets)) == len(set(hostnames)) == 4, "duplicate/aliased physical node")
    return result


REMOTE_BOUNDARY = r'''
import hashlib,json,os,pathlib,platform,stat,sys
v=P if 'P' in globals() else json.loads(sys.argv[1])
def need(ok,message):
 if not ok: raise RuntimeError(message)
root=pathlib.Path(v['independent_root']); path=pathlib.Path(v['resource_path'])
need(root.is_absolute() and root.resolve()==root and '..' not in root.parts,'independent root alias')
need(path.is_absolute() and path.resolve()==path and '..' not in path.parts,'derived owner resource alias')
need(path!=root and path.is_relative_to(root),'derived resource escaped independent root')
for value in (root,path):
 for parent in (value,*value.parents):
  need(not parent.is_symlink(),'resource ancestor symlink')
  if parent.exists():
   s=parent.stat(); need(stat.S_ISDIR(s.st_mode) and s.st_uid in (0,os.getuid()) and not s.st_mode&0o022,'resource ancestry ownership/mode')
for text in v['protected_roots']+v['immutable_paths']:
 protected=pathlib.Path(text); need(protected.is_absolute(),'relative protected path')
 for target in (protected,protected.resolve()):
  need(not path.is_relative_to(target) and not target.is_relative_to(path),'derived resource overlaps protected/immutable path')
site_roots=[pathlib.Path(text) for text in v.get('site_readonly_roots',[])]
for site in site_roots:
 need(site.is_absolute() and site.resolve()==site and any(site.is_relative_to(pathlib.Path(p)) for p in v['authority_protected_roots']),'unregistered readonly site input')
strict_directories=[pathlib.Path(text) for text in v.get('strict_input_directories',[])]
for text in v['input_directories']+[item['path'] for item in v['input_files']]:
 p=pathlib.Path(text); need(p.is_absolute() and p.resolve()==p and '..' not in p.parts,'immutable input alias')
 need(p.exists(),'immutable input missing')
 # A code/dependency/file role wins even when placed under a data mount.
 # Only the exact two registered data-directory inputs receive the exception.
 data_directory=(text in v['input_directories'] and not any(item['path']==text for item in v['input_files'])
                 and p in site_roots and p not in strict_directories)
 for parent in (p,*p.parents):
  need(not parent.is_symlink(),'immutable input ancestor symlink')
  s=parent.stat()
  # Registered model/profile assets retain their existing operator-group modes.
  # New code, test, dependency and writable namespaces remain strict. Never
  # chmod an original asset to satisfy an experimental guard.
  site_parent=data_directory
  authorized_group=(site_parent and any(parent.is_relative_to(pathlib.Path(q)) for q in v['authority_protected_roots'])
                    and s.st_uid==os.getuid() and s.st_gid==os.getgid())
  need(s.st_uid in (0,os.getuid()) and not s.st_mode&0o002 and (not s.st_mode&0o020 or authorized_group),'immutable input ownership/mode')
for text in v['input_directories']: need(pathlib.Path(text).is_dir(),'immutable input directory type')
for item in v['input_files']:
 p=pathlib.Path(item['path']); need(p.is_file(),'immutable input file type')
 need(hashlib.sha256(p.read_bytes()).hexdigest()==item['sha256'],'immutable input hash drift')
need(platform.node()==v['hostname'],'foreign physical hostname')
a=pathlib.Path(v['authority_path']); need(a.is_absolute() and a.resolve()==a,'authority alias')
fd=os.open(a,os.O_RDONLY|os.O_NOFOLLOW)
with os.fdopen(fd,'rb') as stream:
 s=os.fstat(stream.fileno()); need(stat.S_ISREG(s.st_mode) and s.st_uid==os.getuid() and not s.st_mode&0o022,'authority ownership/mode')
 raw=stream.read()
need(hashlib.sha256(raw).hexdigest()==v['authority_sha256'],'remote authority hash drift')
need(json.loads(raw)=={'independent_root':str(root),'protected_roots':v['authority_protected_roots']},'remote authority differs')
RESULT={'hostname':v['hostname'],'authority_sha256':v['authority_sha256'],'resource_path':str(path),'paths_safe':True}
if __name__=='__main__': print(json.dumps(RESULT))
'''


def validate_boundary(contract):
    """Actual fixed remote guard body exposed for filesystem-only CPU faults."""
    scope = {"P": contract, "__name__": "_q03_boundary"}
    exec(compile(REMOTE_BOUNDARY, "<fixed-owner-boundary>", "exec"), scope)
    return scope["RESULT"]


def boundary_contract(binding, node, resource_path):
    pre, candidate = binding["preflight"], binding["candidate"]
    protected = pre["protected_roots"] + [pre[k] for k in (
        "hold_path", "request_path", "legacy_hold_path", "legacy_request_path", "policy_path")]
    protected += [pre["registered_policy"]["repository"]]
    immutable = [candidate[k] for k in ("tests_path", "runtime_manifest_path", "tblib_path",
                 "build_receipt_path", "build_inputs_path", "git_toolchain_path")]
    immutable += [binding["tests"]["path"], binding["checkout"]]
    inputs = input_contract(binding)
    directories = inputs["directories"] + [binding["tests"]["path"], candidate["tblib_path"]]
    return dict(independent_root=binding["independent_root"], resource_path=resource_path,
                input_files=inputs["files"], input_directories=directories,
                site_readonly_roots=[s.format(home=binding["profile_value"]["host"]["home"])
                    for s,d,m in binding["profile_value"]["container"]["mounts"]
                    if m == "ro" and d in ("/models", "/steering")],
                strict_input_directories=[binding["tests"]["path"], candidate["tblib_path"]] + [
                    s.format(home=binding["profile_value"]["host"]["home"])
                    for s,d,m in binding["profile_value"]["container"]["mounts"]
                    if m == "ro" and d not in ("/models", "/steering")],
                hostname=node["expected_hostname"], authority_path=pre["remote_authority_path"],
                authority_sha256=pre["remote_authority_sha256"],
                authority_protected_roots=pre["protected_roots"], protected_roots=protected,
                immutable_paths=immutable)


def validate_remote_inputs(manifest, binding, ops):
    """All-four readonly admission before STOP; absence is a preparation blocker."""
    from q03_window_preflight import _node_argv
    from q03_remote_operation import bound_command
    payload = canonical(input_contract(binding)).decode()
    reports = []
    for node in remote_nodes(binding):
        boundary = boundary_contract(binding, node, str(Path(binding["independent_root"]) / "owner-resources"))
        checked = ops.run(_node_argv(manifest["preflight"], node, bound_command(
            ["python3", "-I", "-B", "-c", REMOTE_BOUNDARY, canonical(boundary).decode()])), timeout=30)
        require(checked.returncode == 0 and json_bytes(checked.stdout).get("paths_safe") is True,
                "remote owner boundary not proven before admission")
        result = ops.run(_node_argv(manifest["preflight"], node,
                         bound_command(["python3", "-I", "-B", "-c", INPUT_PROBE, payload])), timeout=90)
        require(result.returncode == 0, "owner all-four input/image admission failed")
        report = json_bytes(result.stdout)
        require(report.get("image") == IMAGE and report.get("test_tree") == TEST_TREE
                and report.get("deployment_head") == manifest["preflight"]["publication"]["sha"],
                "remote candidate image/test identity differs")
        reports.append(report)
    return reports


class OwnerOps:
    """Closed action mapping over existing hash-pinned remote epoch protocol."""
    sleep = staticmethod(time.sleep)

    def __init__(self, session, plan, journal, *, recovering=False):
        self.session, self.plan, self.journal, self.recovering = session, plan, journal, recovering
        self.binding = plan["binding"]
        self.nodes = remote_nodes(self.binding)
        self.pre = session.manifest["preflight"]
        from q03_original_spark_adapter import load_remote_helper
        self.helper = load_remote_helper(self.pre["remote_helper_sha256"])
        require(self.pre["remote_helper_sha256"] == session.capsule["recovery_source_sha256"]["q03_remote_operation.py"],
                "remote helper differs from capsule")
        self.end = session.capsule["deadline_epoch"]
        if recovering:
            state = session.ops.read_json(session.directory / "state.json")
            # Recovery remains bounded even after the original lease expires.
            self.end = state.get("restoring_started_epoch", time.time()) + session.capsule["recovery_timeout_s"]
        else:
            admitted = json_bytes(read_owned_bytes(session.directory / "attempts" / plan["attempt_id"] / "admitted.json"))
            self.end = min(self.end, admitted["owner_deadline_epoch"])
        for rank in range(4):
            self.verify_boundary(rank)

    def verify_boundary(self, rank):
        contract = boundary_contract(self.binding, self.nodes[rank], self.plan["ranks"][rank]["root"])
        result = self.transport(rank, self.helper.bound_command(
            ["python3", "-I", "-B", "-c", REMOTE_BOUNDARY, canonical(contract).decode()]))
        require(result.returncode == 0, "remote physical/resource authority UNKNOWN: " + result.stderr)
        require(json_bytes(result.stdout) == {"hostname": contract["hostname"],
                "authority_sha256": contract["authority_sha256"],
                "resource_path": contract["resource_path"], "paths_safe": True},
                "remote physical/resource proof differs")

    def check_authority(self):
        require(self.session.owns_hold(), "owner adapter lost hold")
        require(self.session.capsule["recovery_source_sha256"] == self.session.ops.recovery_sources(),
                "owner controller/helper sources changed")
        require(time.time() < self.end, "bounded owner/recovery deadline reached")
        require(json_bytes(read_owned_bytes(self.journal.directory / "plan.json")) == self.plan,
                "owner plan bytes changed")
        verify_plan_identity(self.session, self.plan)
        if not self.recovering:
            from q03_window_watchdog import process_alive
            require(process_alive(self.session.capsule["core_process"]),
                    "owner core PID/start-ticks is dead or reused")
            require(not self.session.requested(), "cluster requested during owner job")
            core = self.session.ops.read_json(self.session.directory / "state.json")
            require(core.get("active_attempt", {}).get("attempt_id") == self.plan["attempt_id"]
                    and core.get("active_gate") == "owner_model", "owner attempt lost core authority")
            # The existing parent poll consumes controls. Refuse any pending
            # pause/close before publishing a generation acknowledgment as well.
            control = self.session.directory / "controls" / (f"{core['held_sequence'] + 1:08d}.request.json")
            require(not control.exists(), "pending operator control; stop owner progress")

    def transport(self, rank, argv, timeout=30):
        from q03_window_preflight import _node_argv
        remaining = self.end - time.time()
        require(remaining > 0, "owner transport deadline reached")
        return self.session.ops.run(_node_argv(self.pre, self.nodes[rank], argv),
                                    timeout=min(timeout, remaining), check=False)

    def helper_call(self, rank, action, epoch, operation=None, extra=()):
        if action in ("launch", "seal", "cancel"):
            self.verify_boundary(rank)
        argv = [action, "--root", str(Path(self.binding["independent_root"]) / "remote-operations"),
                "--window", self.plan["window_id"], "--epoch", epoch,
                "--authority", self.pre["remote_authority_path"],
                "--authority-sha256", self.pre["remote_authority_sha256"]]
        if operation:
            argv += ["--operation", operation]
        wire = self.helper.verified_helper_command(self.pre["remote_helper_path"], self.pre["remote_helper_sha256"],
                                                   [*argv, *extra])
        wire[0] = "python3"
        result = self.transport(rank, wire)
        require(result.returncode == 0, "owner remote operation outcome UNKNOWN: " + result.stderr)
        value = json_bytes(result.stdout)
        require(value.get("window") == self.plan["window_id"], "remote receipt window differs")
        require(value.get("operation") == operation if operation else value.get("epoch") == epoch,
                "remote operation/epoch differs")
        return value

    def mutate(self, rank, kind, argv, timeout):
        self.check_authority()
        epoch, operation = uuid.uuid4().hex, uuid.uuid4().hex
        command = self.helper.bound_command(argv)
        record = dict(rank=rank, action_kind=kind, epoch=epoch, operation=operation,
                      argv_sha256=digest(canonical(command)))
        self.journal.state["epochs"].append(record)
        self.journal.save()  # Durable BEFORE helper launch, including delayed SSH.
        self.journal.event("remote-intent", **record)
        value = self.helper_call(rank, "launch", epoch, operation, (
            "--argv-json", canonical(command).decode(), "--cwd", self.binding["independent_root"],
            "--timeout-seconds", str(min(timeout, max(1, self.end - time.time())))))
        while value.get("status") != "terminal":
            self.check_authority()
            require(value.get("status") != "uncertain" or not value.get("state", {}).get("supervisor"),
                    "remote supervisor lost without terminal receipt")
            self.sleep(.1)
            value = self.helper_call(rank, "status", epoch, operation)
        self.journal.event("remote-terminal", rank=rank, operation=operation, receipt=value)
        require(value.get("action_succeeded") is True, "remote action failed/uncertain; no overlap authorization")
        self.seal_epoch(epoch)
        return value["stdout"]

    def capture_logs(self, rank, record):
        self.check_authority()
        ident = record['container_id']
        command = self.helper.bound_command(['docker', 'logs', '--timestamps', '--', ident])
        require(self.helper.log_capture_id(command) == ident, 'invalid fixed log capture')
        epoch, operation = uuid.uuid4().hex, uuid.uuid4().hex
        intent = dict(rank=rank, action_kind='capture_logs', epoch=epoch, operation=operation,
                      argv_sha256=digest(canonical(command)))
        self.journal.state['epochs'].append(intent)
        self.journal.save()
        self.journal.event('remote-intent', **intent)
        value = self.helper_call(rank, 'launch', epoch, operation, (
            '--argv-json', canonical(command).decode(), '--cwd', self.binding['independent_root'],
            '--timeout-seconds', str(min(self.helper.LOG_TIMEOUT_SECONDS, max(1, self.end - time.time())))))
        while value.get('status') != 'terminal':
            self.check_authority()
            require(value.get('status') != 'uncertain' or not value.get('state', {}).get('supervisor'),
                    'log supervisor lost without terminal receipt')
            self.sleep(.1)
            value = self.helper_call(rank, 'status', epoch, operation)
        # Do not duplicate raw log bytes in every journal/epoch event.
        receipt = copy.deepcopy(value)
        for stream in receipt.get('log_capture', {}).get('streams', {}).values():
            stream.pop('data_base64', None)
        self.journal.event('log-read-terminal', rank=rank, operation=operation, receipt=receipt)
        self.seal_epoch(epoch)
        value = self.helper_call(rank, 'status', epoch, operation)
        require(value.get('status') == 'terminal' and value.get('epoch_sealed') is True
                and value.get('safe_to_overlap') is True, 'log read fence receipt differs')
        value['epoch'] = epoch
        return value

    def seal_epoch(self, epoch):
        # Fence the generation on every node. A late dispatch cannot land on an
        # apparently idle rank after the local client/supervisor has gone away.
        for rank in range(4):
            value = self.helper_call(rank, "seal", epoch)
            while value.get("safe_to_overlap") is not True:
                require(value.get("sealed") is True, "owner epoch did not seal")
                pending = value.get("operations", [])
                require(pending and all(row.get("safe_to_overlap") is True or row.get("status") == "running"
                                        for row in pending), "owner epoch has UNKNOWN/failed terminal state")
                for row in pending:
                    if row.get("status") == "running":
                        self.helper_call(rank, "cancel", epoch, row["operation"])
                self.sleep(.1)
                value = self.helper_call(rank, "seal", epoch)
            receipt = copy.deepcopy(value)
            for operation in receipt.get('operations', []):
                for stream in operation.get('log_capture', {}).get('streams', {}).values():
                    stream.pop('data_base64', None)
            self.journal.event("epoch-sealed", rank=rank, epoch=epoch, receipt=receipt)
        # A sealed remote epoch permanently refuses late launches. Retain the
        # all-four completion only AFTER every positive receipt is journalled.
        # Recovery retries incomplete epochs, but does not re-poll the entire
        # completed history before every later barrier (quadratic SSH traffic).
        sealed = self.journal.state.setdefault("sealed_epochs", [])
        if epoch not in sealed:
            sealed.append(epoch)
            self.journal.save()

    def seal_all(self):
        for epoch in dict.fromkeys(row["epoch"] for row in self.journal.state["epochs"]):
            if epoch not in self.journal.state.get("sealed_epochs", []):
                self.seal_epoch(epoch)

    def action(self, rank, kind, record):
        row = self.plan["ranks"][rank]
        ident = record.get("container_id")
        if kind == "prepare":
            contract = input_contract(self.binding)
            contract.update(root=row["root"], independent_root=self.binding["independent_root"],
                            spec=canonical(self.plan["spec"]).decode(), spec_sha256=self.plan["spec_sha256"])
            argv, timeout = ["python3", "-I", "-B", "-c", PREPARE, canonical(contract).decode()], 60
        elif kind == "create":
            argv = ["docker", "create", "--name", row["name"], *self.plan["docker_args"], "--workdir", "/validation"]
            for key, value in {"q03.window": self.plan["window_id"], "q03.attempt": self.plan["attempt_id"],
                               "q03.job": "owner_model", "q03.rank": str(rank), "q03.plan": self.plan["plan_sha256"]}.items():
                argv += ["--label", key + "=" + value]
            for key, value in sorted(row["environment"].items()):
                argv += ["--env", key + "=" + str(value)]
            for source, destination, mode in row["mounts"]:
                argv += ["--mount", "type=bind,src=" + source + ",dst=" + destination + (",readonly" if mode == "ro" else "")]
            argv += ["--entrypoint", "python3", IMAGE, *row["python_argv"]]
            timeout = 90
        else:
            require(re.fullmatch(r"[0-9a-f]{64}", ident or ""), "mutation needs durable exact container ID")
            if kind in ("startup_guard", "steady_guard"):
                phase = "startup" if kind == "startup_guard" else "steady"
                settings = ["5", "0.25", "120", "prelaunch"] if phase == "startup" else ["3", "2", "0", "running"]
                # Both script bytes are independently checked by preparation and
                # the immutable recovery inventory; no original checkout script.
                script = str(Path(self.binding["checkout"]) / "scripts/memguard-start.sh")
                files = [v for v in input_contract(self.binding)["files"]
                         if Path(v["path"]).name in ("memguard-start.sh", "memguard.sh")]
                payload = dict(script=script, files=files, arguments=[ident, phase, *settings])
                argv = ["python3", "-I", "-B", "-c", GUARD_EXEC, canonical(payload).decode()]
                timeout = 30
            elif kind == "start":
                argv, timeout = ["docker", "start", ident], 90
            elif kind == "stop":
                argv, timeout = ["docker", "stop", "--time", "60", ident], 75
            elif kind == "stop_guard":
                argv, timeout = ["sudo", "-n", "systemctl", "stop", ident + "-memguard.service"], 30
            elif kind == "remove":
                argv, timeout = ["docker", "rm", ident], 30
            else:
                raise RuntimeError("unknown fixed owner action")
        output = self.mutate(rank, kind, argv, timeout)
        return output.strip() if kind == "create" else None

    def inspect(self, rank, record):
        ident = record["container_id"]
        result = self.transport(rank, self.helper.bound_command(["docker", "container", "inspect", ident]))
        if result.returncode:
            missing = r'(?i:(?:error: |error response from daemon: )?no such (?:object|container): )' + re.escape(ident)
            require(result.returncode == 1 and result.stdout.strip() in ("", "[]")
                    and re.fullmatch(missing, result.stderr.strip()), "owner daemon state UNKNOWN")
            return None
        values = json_bytes(result.stdout)
        require(isinstance(values, list) and len(values) == 1, "owner inspect ambiguous")
        self.journal.event("daemon-inspect", rank=rank, container=values[0])
        return values[0]

    def guard_state(self, rank, record):
        ident = record["container_id"]
        unit = ident + "-memguard.service"
        properties = "LoadState,ActiveState,SubState,User,WorkingDirectory,Environment,ExecStart,MainPID"
        result = self.transport(rank, self.helper.bound_command(["systemctl", "show", unit, "--property=" + properties]))
        require(result.returncode == 0, "guard daemon state UNKNOWN")
        values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        self.journal.event("guard-inspect", rank=rank, unit=unit, properties=values)
        if values.get("LoadState") == "not-found":
            require(values.get("ActiveState") == "inactive", "unloaded guard has unknown state")
            return {"absent": True}
        env = shlex.split(values.get("Environment", ""))
        pairs = [item.split("=", 1) for item in env]
        require(all(len(pair) == 2 for pair in pairs) and len({pair[0] for pair in pairs}) == len(pairs),
                "guard environment malformed")
        environment = dict(pairs)
        phase = environment.get("MEMGUARD_PHASE")
        expected = dict(CONTAINER_NAME=ident, MEMGUARD_PHASE=phase,
                        THRESHOLD_GIB="5" if phase == "startup" else "3",
                        INTERVAL="0.25" if phase == "startup" else "2",
                        WAIT_FOR_CONTAINER_SECONDS="120" if phase == "startup" else "0")
        checkout = self.binding["checkout"]
        script = str(Path(checkout) / "scripts/memguard.sh")
        owned = (phase in ("startup", "steady") and values.get("LoadState") == "loaded"
                 and values.get("User") == self.binding["nodes_value"]["ssh_user"]
                 and values.get("WorkingDirectory") == checkout
                 and all(environment.get(k) == v for k, v in expected.items())
                 and set(environment) == set(expected) | {"READY_FILE"}
                 and Path(environment.get("READY_FILE", "")).name.startswith(ident + "-memguard.")
                 and ("path=" + script + " ; argv[]=" + script + " ;") in values.get("ExecStart", ""))
        active = values.get("ActiveState") == "active" and values.get("SubState") == "running"
        require(owned and values.get("ActiveState") in ("active", "inactive", "failed"), "guard identity/state UNKNOWN")
        if active:
            require(int(values.get("MainPID", "0")) > 0, "active guard lacks live main PID")
        return dict(owned=True, active=active, phase=phase, container_id=ident)

    def read_ready(self):
        path = Path(self.plan["ranks"][0]["root"]) / "results/owner-ready.json"
        try:
            return read_owned_bytes(path)
        except FileNotFoundError:
            return None

    def publish_ack(self, ack):
        self.check_authority()
        path = Path(self.plan["ranks"][0]["root"]) / "results/owner-steady-ack.json"
        exclusive_json(path, ack)

    def collect_result(self):
        root = Path(self.plan["ranks"][0]["root"]) / "results"
        raw, xml_raw = read_owned_bytes(root / "owner.json"), read_owned_bytes(root / "owner.xml")
        value = json_bytes(raw)
        require(value.get("source_head") == SOURCE_HEAD and value.get("mode") == self.plan["selection"]["mode"]
                and value.get("steering") == self.plan["spec"]["steering"], "owner final result identity differs")
        ready_raw = self.read_ready()
        ready = validate_ready(ready_raw, self.plan)
        require(value.get("resolved_workers") == ready["reports"], "owner result changed resolved workers")
        ack_raw = read_owned_bytes(root / "owner-steady-ack.json")
        require(value.get("readiness") == {"ready_sha256": digest(ready_raw), "ack_sha256": digest(ack_raw)},
                "owner result handshake differs")
        xml = ET.fromstring(xml_raw)
        cases = list(xml.iter("testcase"))
        require(len(cases) == 1 and cases[0].get("name") == "test_real_model_owner_and_capture_retention"
                and not any(cases[0].find(tag) is not None for tag in ("failure", "error", "skipped"))
                and not list(xml.iter("failure")) and not list(xml.iter("error")) and not list(xml.iter("skipped")),
                "owner selected pytest case did not pass exactly once")
        require(len(value.get("outputs", [])) == 6 and [len(b) for b in value["outputs"]] == [1, 6, 16] * 2,
                "owner fixed observation requests incomplete")
        return dict(result="passed", qualification="owner-and-collector-retention-only", image=IMAGE,
                    fixture_sha256=TEST_SHA, spec_sha256=self.plan["spec_sha256"],
                    result_sha256=digest(raw), junit_sha256=digest(xml_raw))


def verify_plan_identity(session, plan):
    directory = session.directory / "attempts" / plan["attempt_id"]
    descriptor = json_bytes(read_owned_bytes(directory / "descriptor.json"))
    admitted = json_bytes(read_owned_bytes(directory / "admitted.json"))
    require(descriptor["window_id"] == plan["window_id"] == session.capsule["window_id"]
            and descriptor["capsule_sha256"] == digest(read_owned_bytes(session.directory / "capsule.json"))
            and admitted["descriptor_sha256"] == digest(canonical(descriptor))
            and descriptor["command_plan_sha256"] == digest(canonical([["owner_model", plan]]))
            and admitted["command_plan_sha256"] == descriptor["command_plan_sha256"],
            "owner plan is not bound to the immutable admitted attempt")


def cleanup_all(session):
    """No dependence on active_attempt: enumerate all immutable admitted attempts."""
    for directory in sorted((session.directory / "attempts").glob("*")):
        if (directory / "owner").exists():
            quiesce_owner(session, directory.name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "quiesce"))
    parser.add_argument("--session-dir", required=True)
    parser.add_argument("--attempt-id", required=True)
    args = parser.parse_args()
    from q03_window import Session, RuntimeOps
    session = Session(args.session_dir, RuntimeOps())
    require(re.fullmatch(r"[0-9a-f]{32}", args.attempt_id), "canonical attempt UUID required")
    directory = session.directory / "attempts" / args.attempt_id
    if args.action == "quiesce":
        value = quiesce_owner(session, args.attempt_id)
    else:
        raw = read_owned_bytes(directory / "owner-plan.json")
        plan = json_bytes(raw)
        verify_plan_identity(session, plan)
        value = run_owner(session, plan)
    print(json.dumps(value, sort_keys=True))


if __name__ == "__main__":
    main()
