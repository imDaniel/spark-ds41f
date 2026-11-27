"""Run upstream GLP host tests with real JIT language imports on a CPU-only host."""

import os
import sys
from pathlib import Path


def main():
    source = Path(os.environ["STEERING_VLLM_SOURCE"]).resolve()
    os.chdir(source)
    sys.path.insert(0, str(source))
    import pytest
    import tilelang
    import tilelang.language
    import triton
    import triton.language
    import triton.language.extra.libdevice
    from triton.experimental import gluon
    from triton.experimental.gluon import language as gl
    from vllm import tilelang_utils, triton_utils

    # CPU platform detection normally leaves the language bindings empty. The
    # host tests need to import decorated functions; their GPU launches are
    # already replaced at the appropriate boundaries by the upstream fixtures.
    tilelang_utils.tilelang, tilelang_utils.T = tilelang, tilelang.language
    triton_utils.triton, triton_utils.tl = triton, triton.language
    triton_utils.tldevice = triton.language.extra.libdevice
    triton_utils.gluon, triton_utils.gl = gluon, gl
    triton_utils.aggregate = triton.language.core._aggregate
    return pytest.main([
        "--noconftest", "-p", "no:cacheprovider", "-o", "addopts=", "-q",
        "tests/models/test_deepseek_v4_1_glp.py", "tests/test_weightless.py",
    ])


if __name__ == "__main__":
    raise SystemExit(main())
