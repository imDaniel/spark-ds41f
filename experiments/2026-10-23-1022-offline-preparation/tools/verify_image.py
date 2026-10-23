"""CPU-only source/native audit inside the isolated ARM64 image build."""
import hashlib
import importlib.metadata
import json
import pathlib
import sys

ROOT = pathlib.Path('/opt/spark3/candidate/vllm')
OUT = pathlib.Path('/opt/spark3/1022-offline')


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


if sys.argv[1] == 'before':
    paths = [p for p in ROOT.rglob('*') if p.is_file() and '.so' in p.name]
    site = pathlib.Path('/usr/local/lib/python3.12/dist-packages')
    for package in ('tilelang', 'tile_kernels', 'b12x', 'nvidia/nccl'):
        paths += [p for p in (site / package).rglob('*') if p.is_file() and '.so' in p.name]
    if len(paths) < 4:
        raise RuntimeError('Native library inventory is incomplete')
    (OUT / 'native-parent.json').write_text(json.dumps({str(p): sha(p) for p in paths}))
    for name in json.loads((OUT / 'deletions.json').read_text()):
        path = ROOT / name
        if not name.endswith('.py') or not path.resolve().is_relative_to(ROOT):
            raise RuntimeError('Refuse unexpected source deletion: ' + name)
        path.unlink(missing_ok=True)
else:
    sources = json.loads((OUT / 'source-hashes.json').read_text())
    for name, expected in sources.items():
        if sha(ROOT / name) != expected:
            raise RuntimeError('Source mismatch: ' + name)
    native = json.loads((OUT / 'native-parent.json').read_text())
    for name, expected in native.items():
        if sha(pathlib.Path(name)) != expected:
            raise RuntimeError('Native library changed: ' + name)
    for name in json.loads((OUT / 'deletions.json').read_text()):
        if (ROOT / name).exists():
            raise RuntimeError('Deleted source remains: ' + name)
    if importlib.metadata.version('dgx-spark-networking') != '0.5.0':
        raise RuntimeError('Wrong sparknet version')
    receipt = dict(source_files_verified=len(sources), native_libraries_unchanged=len(native),
                   sparknet_version='0.5.0', gpu_verified=False)
    (OUT / 'verification.json').write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt), flush=True)
