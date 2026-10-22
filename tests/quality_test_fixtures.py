"""Portable public test inputs, never genuine site/runtime/model evidence.

The normal CI path uses committed R2 data and exact Git objects already fetched
by validate.yml. Explicit external fixtures remain useful for independent review;
their content is verified by the same consumer checks, never inferred from paths.
"""
import atexit
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MATERIAL = Path(os.environ.get('Q03_TEST_QUALITY_MATERIAL',
                             ROOT / 'tests/fixtures/r6x-quality-pilot-r2'))


@lru_cache(maxsize=1)
def toolbox():
    supplied = os.environ.get('Q03_TEST_TOOLBOX')
    inventory = json.loads((ROOT / 'scripts/q03_http_toolbox.json').read_bytes())
    if supplied:
        root = Path(supplied)
        for relative, expected in inventory['files'].items():
            if hashlib.sha256((root / relative).read_bytes()).hexdigest() != expected:
                raise RuntimeError('quality test toolbox source differs: ' + relative)
        return root
    # Extract only the immutable required source files. Never fetch, execute a
    # generator, use current servebench bytes, or silently skip absent objects.
    temporary = tempfile.TemporaryDirectory(prefix='quality-pr8-toolbox-')
    atexit.register(temporary.cleanup)
    root = Path(temporary.name)
    for relative, expected in inventory['files'].items():
        raw = subprocess.check_output(
            ['git', 'show', inventory['head'] + ':' + relative], cwd=ROOT)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise RuntimeError('quality test toolbox source differs: ' + relative)
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    return root
