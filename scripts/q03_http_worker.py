#!/usr/bin/env python3
"""Frozen HTTP observation extension and normal vLLM CLI bootstrap.

No engine is constructed on import. This adds one read-only worker RPC, not a
model patch, alternate EngineArgs implementation or owner-use instrumentation.
"""
import hashlib
import types
import json
import os
from pathlib import Path
import sys

FIXTURE = '/validation/tests/compile/fullgraph/test_deepseek_v41_q03_owner.py'
FIXTURE_SHA = '6b33c8026bfc2b5e18274235473cf1c5c6620981ddf2f433817c40408c05f5ad'
RUNTIME_SHA = '24315da1b6138aef8acccb7244000feceef71b74486eeddce31bec25264919b9'


class HttpObservation:
    def q03_http_observe(self):
        raw = Path('/runtime/runtime-manifest.json').read_bytes()
        if hashlib.sha256(raw).hexdigest() != RUNTIME_SHA:
            raise RuntimeError('HTTP native runtime manifest changed')
        fixture = Path(FIXTURE)
        fixture_raw = fixture.read_bytes()
        if hashlib.sha256(fixture_raw).hexdigest() != FIXTURE_SHA:
            raise RuntimeError('HTTP observation fixture changed')
        module = types.ModuleType('_q03_http_observation')
        module.__file__ = str(fixture)
        exec(compile(fixture_raw, str(fixture), 'exec'), module.__dict__)
        result = module._check_runtime(self, json.loads(raw))
        result['http_runtime_sha256'] = RUNTIME_SHA
        spec = Path('/runtime/http-spec.json').read_bytes()
        spec_sha = hashlib.sha256(spec).hexdigest()
        identity = json.loads(spec)['handshake']
        if (spec_sha != os.environ['Q03_HTTP_SPEC_SHA256']
                or identity['window_id'] != os.environ['Q03_HTTP_WINDOW']
                or identity['attempt_id'] != os.environ['Q03_HTTP_ATTEMPT']):
            raise RuntimeError('HTTP worker attempt identity changed')
        result['http_fixture_sha256'] = FIXTURE_SHA
        result['http_window_id'] = identity['window_id']
        result['http_attempt_id'] = identity['attempt_id']
        result['http_spec_sha256'] = spec_sha
        return result


def main():
    # Spawned workers inherit this exact path through normal multiprocessing;
    # the image's baked PYTHONPATH and installed vLLM source remain unchanged.
    sys.path.insert(0, '/q03-http')
    sys.argv[0] = 'vllm'
    from vllm.entrypoints.cli.main import main as serve
    serve()


def install_worker_source():
    # The CLI and multiprocessing spawn bootstrap both load this script as a
    # main module. Preload the extension from verified source bytes so ignored
    # pyc/native shadow files cannot replace it during worker class resolution.
    raw = Path(__file__).read_bytes()
    if hashlib.sha256(raw).hexdigest() != os.environ['Q03_HTTP_WORKER_SHA256']:
        raise RuntimeError('HTTP worker bootstrap source changed')
    if os.environ.get('VLLM_ALLOW_INSECURE_SERIALIZATION') != '0':
        raise RuntimeError('HTTP lane requires native safe serialization')
    module = types.ModuleType('q03_http_worker')
    module.__file__ = __file__
    exec(compile(raw, __file__, 'exec'), module.__dict__)
    sys.modules['q03_http_worker'] = module


if __name__ in ('__main__', '__mp_main__'):
    install_worker_source()
if __name__ == '__main__':
    main()
