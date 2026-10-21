# Pinned readonly tblib test dependency

The unchanged 197 test requirements pin `tblib==3.1.0`. Root `tests/conftest.py`
imports `tblib.pickling_support` and installs exception pickling before defining
other exception classes. Preserve the root and kernel conftests, their cleanup,
Dynamo reset, and configuration fixtures. Neither `--noconftest` nor a narrowed
`--confcutdir` is an equivalent validation environment.

Official metadata: https://pypi.org/pypi/tblib/3.1.0/json

Official artifact:
https://files.pythonhosted.org/packages/27/44/aa5c8b10b2cce7a053018e0d132bd58e27527a0243c4985383d5b6fd93e9/tblib-3.1.0-py3-none-any.whl

SHA256: `670bb4582578134b3d81a84afa1b016128b429f3d48e6cbbaecc9d15675e984e`

Metadata declares Python >=3.9, purelib true, `py3-none-any`, BSD-2-Clause,
and no Requires-Dist. This closes this package's dependency set only; it does
not assert that every other root-conftest dependency is installed.

## Preparation

Download the official wheel to an independent location. Do not install it into
the host or image. Use the published preparer with the already reviewed private
authority, a canonical input wheel, and a new absent output leaf beneath the
independent root (outside every protected root):

```sh
python3 -I -B "$CONTROLLER/scripts/q03_prepare_tblib.py" \
  --wheel "$DOWNLOADED_WHEEL" --output "$NEW_BUNDLE" \
  --authority-path "$AUTHORITY" --authority-sha256 "$AUTHORITY_SHA256"
# After inspecting the plan, repeat exactly with --apply.
```

Plan validates bytes, archive metadata/RECORD, authority and destination without
executing package code. Apply copies the same verified wheel bytes and writes
`manifest.json` into an exclusively created directory. It does not extract,
install, invoke Git, launch a container, or execute tblib. A partial failed leaf
is retained, never overwritten. Return the raw manifest, its SHA256, and the
preparation receipt before admitting the bundle to validation.

The wheel has exactly nine members: `tblib/__init__.py`,
`tblib/decorators.py`, `tblib/pickling_support.py`, and under
`tblib-3.1.0.dist-info/`: `METADATA`, `WHEEL`, `RECORD`, `top_level.txt`,
`licenses/AUTHORS.rst`, `licenses/LICENSE`. All license bytes remain in the wheel.
The bundle has exactly two files: this unchanged wheel and `manifest.json`.

The separately reviewed validation launcher must verify the manifest and wheel
bytes, mount the bundle readonly, and add only the verified wheel to Python's
import path before loading the unchanged pytest/conftests. No global PYTHONPATH,
image mutation, replacement fixtures or stub package is needed. Package import
and kernel/native tests remain within the stopped validation window. Preparation
alone does not qualify the target ARM runtime or prove G1/G2 collection succeeds.
