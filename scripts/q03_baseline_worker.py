#!/usr/bin/env python3
"""Source-only, read-only observer for both fixed baseline images.

Normal vLLM CLI startup and safe string RPC remain in use. No owner fixture,
model modification, request execution, private graph reset or arena mutation.
"""
from enum import Enum
import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import sys
import types


def need(ok, message):
    if not ok:
        raise RuntimeError(message)


def file_sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def process_starttime_ticks(raw, pid):
    """Read Linux stat field 22 without splitting the parenthesized comm.

    comm can itself contain spaces, newlines and parentheses. The final `) `
    precedes state (field 3); starttime is therefore tail index 19. These ticks
    identify a sampled process epoch, not its loaded weights or applied scale.
    """
    need(type(pid) is int and 0 < pid < 2**31, 'baseline worker PID is missing or invalid')
    need(type(raw) is bytes and 0 < len(raw) <= 65536 and raw.endswith(b'\n'),
         'baseline worker /proc/self/stat size/type/termination differs')
    prefix = str(pid).encode('ascii') + b' ('
    end = raw.rfind(b') ')
    need(raw.startswith(prefix) and end >= len(prefix),
         'baseline worker /proc/self/stat PID/comm differs')
    fields = raw[end + 2:].split()
    # Require the complete supported Linux record through field 52. Accepting
    # just field 22 could turn a read cut within starttime into shorter ticks.
    need(len(fields) >= 50 and fields[0] in (b'R', b'S', b'D', b'T', b't', b'W', b'K', b'P', b'I')
         and all(re.fullmatch(rb'-?[0-9]+', field) for field in fields[1:])
         and len(fields[19]) <= 20 and re.fullmatch(rb'[1-9][0-9]*', fields[19]) is not None,
         'baseline worker /proc/self/stat is missing, dead or malformed')
    ticks = int(fields[19])
    need(ticks < 2**64, 'baseline worker starttime exceeds Linux field width')
    return ticks


def observe_worker_process():
    """Observe this actual worker only; no caller PID, subprocess or signaling."""
    need(sys.platform == 'linux', 'baseline worker process observation requires Linux')
    pid = os.getpid()
    try:
        with Path('/proc/self/stat').open('rb') as stream:
            raw = stream.read(65537)
    except OSError as exc:
        raise RuntimeError('baseline worker /proc/self/stat unavailable') from exc
    return {'worker_pid': pid, 'worker_starttime_ticks': process_starttime_ticks(raw, pid)}


def verify_bytecode_policy():
    # -B alone does not stop Python reading a valid timestamp/hash .pyc. A
    # fresh, absent prefix redirects every ordinary import away from baked
    # cache files while retaining the image's original PYTHONPATH.
    expected = '/cache/baseline-bytecode'
    prefix = Path(expected)
    need(os.environ.get('PYTHONPYCACHEPREFIX') == expected
         and sys.pycache_prefix == expected and sys.dont_write_bytecode
         and not prefix.exists() and not prefix.is_symlink(),
         'baseline imports require a fresh isolated bytecode prefix')


def load_identity():
    path = Path('/q03-http/q03_baseline_identity.py')
    raw = path.read_bytes()
    need(hashlib.sha256(raw).hexdigest() == os.environ['Q03_BASELINE_IDENTITY_SHA256'],
         'baseline identity source changed')
    module = types.ModuleType('_q03_baseline_identity_source')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


EFFECTIVE_FIELDS = {
    'model_config': ('model', 'tokenizer', 'tokenizer_mode', 'dtype', 'max_model_len',
                     'enforce_eager', 'seed', 'trust_remote_code', 'generation_config',
                     'override_generation_config', 'served_model_name'),
    'load_config': ('load_format',),
    'cache_config': ('block_size', 'cache_dtype', 'enable_prefix_caching', 'gpu_memory_utilization',
                     'kv_cache_memory_bytes', 'prefix_cache_retention_interval'),
    'scheduler_config': ('max_num_seqs', 'max_num_batched_tokens', 'enable_chunked_prefill',
                         'max_parallel_prefills', 'long_prefill_token_threshold', 'async_scheduling',
                         'scheduler_reserve_full_isl'),
    'parallel_config': ('distributed_executor_backend', 'tensor_parallel_size', 'pipeline_parallel_size',
                        'data_parallel_size', 'nnodes', 'world_size', 'local_world_size', 'use_ubatching'),
    'compilation_config': ('mode', 'cudagraph_mode', 'cudagraph_capture_sizes',
                           'max_cudagraph_capture_size', 'custom_ops'),
    'speculative_config': ('method', 'num_speculative_tokens', 'draft_tensor_parallel_size',
                           'draft_sample_method', 'rejection_sample_method', 'enable_adaptive_verification',
                           'adaptive_verification_row_cost'),
    'attention_config': ('backend',),
    'kernel_config': ('linear_backend', 'moe_backend', 'enable_flashinfer_autotune'),
    'engram_config': ('table_memory', 'disk_resident_scales', 'projection_tp'),
    'observability_config': ('jit_monitor_mode',),
}


def project_effective_config(config):
    """Same fixed resolved fields, independently read from each actual worker."""
    result = {}
    for section, fields in EFFECTIVE_FIELDS.items():
        obj = getattr(config, section)
        for field in fields:
            value = getattr(obj, field)
            if isinstance(value, Enum):
                value = value.name
            if section == 'model_config' and field == 'dtype':
                value = str(value)
            result[section + '.' + field] = value
    result['compilation_config.pass_config.enable_sp'] = config.compilation_config.pass_config.enable_sp
    result['speculative_config.draft_parallel_config.tensor_parallel_size'] = (
        config.speculative_config.draft_parallel_config.tensor_parallel_size)
    mm = config.model_config.multimodal_config
    result['model_config.multimodal_config.image_limit'] = None if mm is None else mm.get_limit_per_prompt('image')
    result['model_config.multimodal_config.mm_processor_cache_gb'] = None if mm is None else mm.mm_processor_cache_gb
    additional = config.additional_config
    result['additional_config.ds41_steering'] = {
        'present': 'ds41_steering' in additional, 'value': additional.get('ds41_steering')}
    return json.loads(json.dumps(result, sort_keys=True, allow_nan=False))


class HttpObservation:
    def q03_http_observe(self):
        verify_bytecode_policy()
        identity = load_identity()
        spec_raw = Path('/runtime/http-spec.json').read_bytes()
        spec_sha = hashlib.sha256(spec_raw).hexdigest()
        spec = identity.json_bytes(spec_raw)
        handshake = spec['handshake']
        need(spec_sha == os.environ['Q03_HTTP_SPEC_SHA256']
             and handshake['window_id'] == os.environ['Q03_HTTP_WINDOW']
             and handshake['attempt_id'] == os.environ['Q03_HTTP_ATTEMPT'],
             'baseline worker attempt identity changed')
        checked = spec['baseline_identity']
        arm = checked['identity']['arm']
        need(checked['identity'] == identity.checked_identity(arm), 'baseline worker arm differs')
        runtime_raw = Path('/runtime/runtime-manifest.json').read_bytes()
        runtime_sha = hashlib.sha256(runtime_raw).hexdigest()
        need(runtime_sha == checked['provenance']['sha256'], 'baseline explicit runtime provenance changed')
        runtime = identity.validate_runtime(identity.json_bytes(runtime_raw), arm)
        need(runtime == checked['runtime'], 'baseline runtime spec binding differs')
        need(os.environ.get('VLLM_DS41_Q03_MHC_MXFP8') == '0', 'baseline Q03 must remain OFF')
        need(os.environ.get('VLLM_ALLOW_INSECURE_SERIALIZATION') == '0', 'baseline safe RPC disabled')
        # Imports are confined to the already running, authorized model worker,
        # never the static preparation capture.
        import torch
        import vllm
        from vllm.distributed import get_tensor_model_parallel_rank
        from vllm.models.deepseek_v4_1.nvidia.model import DeepseekV4Model
        root = Path(vllm.__file__).resolve().parent
        need(str(root) == runtime['installation']['package_root'], 'baseline imported package root changed')
        package_origins, package_hashes = {}, {}
        for name in ('torch', 'tilelang', 'tile_kernels'):
            module = importlib.import_module(name)
            path = Path(module.__file__).resolve()
            need(str(path) == runtime['package_origins'][name], 'baseline imported package origin changed')
            actual = file_sha(path)
            need(actual == runtime['package_sha256'][name], 'baseline package source changed')
            package_origins[name], package_hashes[name] = str(path), actual
        version_path = Path(package_origins['torch']).with_name('version.py')
        version_hash = file_sha(version_path)
        need(version_hash == runtime['torch_version_sha256']
             and str(torch.__version__) == runtime['torch_version'], 'baseline actual Torch version changed')
        sources = {}
        for relative, expected in identity.SOURCE_SHA256[arm].items():
            path = root / relative
            need(path.resolve() == path and file_sha(path) == expected, 'baseline loaded source bytes differ')
            sources[relative] = expected
        # Reject imported critical modules from another mount/path as well as
        # checking the baked files. The observer does not add a vLLM overlay.
        for name, module in list(sys.modules.items()):
            if name == 'vllm' or name.startswith('vllm.'):
                path = getattr(module, '__file__', None)
                if path is not None:
                    resolved = Path(path).resolve()
                    need(resolved.is_relative_to(root), 'baseline imported vLLM module escapes package root')
        native_paths, native_hashes = {}, {}
        for name in identity.NATIVE_NAMES:
            path = Path(importlib.import_module(name).__file__).resolve()
            need(str(path) == runtime['native_paths'][name], 'baseline imported native path changed')
            actual = file_sha(path)
            need(actual == runtime['native_sha256'][name], 'baseline imported native ELF changed')
            native_paths[name], native_hashes[name] = str(path), actual
        targets = [m for m in self.model_runner.get_model().modules() if isinstance(m, DeepseekV4Model)]
        need(len(targets) == 1, 'baseline actual model target missing or ambiguous')
        target = targets[0]
        # A188 predates q03_input_arena. Its absence is an observed A188 fact,
        # not a fabricated native197 owner/collector qualification.
        arena = getattr(target, 'q03_input_arena', None)
        need(arena is None, 'baseline OFF model unexpectedly has Q03 owner')
        profile = target.steering_profile
        steering = None if profile is None else {
            'name': profile.profile_id, 'artifact_sha256': profile.artifact_sha256,
            'identity': profile.identity, 'schedule_sha256': profile.schedule_sha256,
            'layers': list(profile.layers), 'alphas': list(profile.alphas)}
        parallel = self.parallel_config
        return {**observe_worker_process(), 'worker_rank': self.rank, 'tp_rank': get_tensor_model_parallel_rank(),
            'config_rank': parallel.rank, 'node_rank': parallel.node_rank,
            'node_rank_within_dp': parallel.node_rank_within_dp, 'local_rank': self.local_rank,
            'runner_type': type(self.model_runner).__name__, 'identity': checked['identity'],
            'runtime_sha256': runtime_sha, 'window_id': handshake['window_id'],
            'attempt_id': handshake['attempt_id'], 'spec_sha256': spec_sha,
            'package_root': str(root), 'source_sha256': sources,
            'native_paths': native_paths, 'native_sha256': native_hashes,
            'actual_versions': {'python': platform.python_version(), 'torch': str(torch.__version__)},
            'metadata_versions': {name: importlib.metadata.version(name) for name in ('torch', 'tilelang', 'tile_kernels')},
            'package_origins': package_origins, 'package_sha256': package_hashes,
            'torch_version_sha256': version_hash,
            'sm': list(torch.cuda.get_device_capability()), 'q03_switch': os.environ['VLLM_DS41_Q03_MHC_MXFP8'],
            'q03_owner_present': arena is not None, 'sequence_parallel': target.use_sequence_parallel,
            'steering_profile': steering, 'effective_config': project_effective_config(self.vllm_config),
            'runner_config': project_effective_config(self.model_runner.vllm_config)}


def install_worker_source():
    verify_bytecode_policy()
    raw = Path(__file__).read_bytes()
    need(hashlib.sha256(raw).hexdigest() == os.environ['Q03_HTTP_WORKER_SHA256'], 'baseline worker source changed')
    need(os.environ.get('VLLM_ALLOW_INSECURE_SERIALIZATION') == '0', 'baseline requires native safe serialization')
    load_identity()  # Verify the read-only dependency before CLI/model imports.
    module = types.ModuleType('q03_baseline_worker')
    module.__file__ = __file__
    exec(compile(raw, __file__, 'exec'), module.__dict__)
    sys.modules['q03_baseline_worker'] = module


def main():
    sys.path.insert(0, '/q03-http')
    sys.argv[0] = 'vllm'
    from vllm.entrypoints.cli.main import main as serve
    serve()


if __name__ in ('__main__', '__mp_main__'):
    install_worker_source()
if __name__ == '__main__':
    main()
