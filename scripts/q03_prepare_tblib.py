#!/usr/bin/env python3
"""Copy an official pinned tblib wheel, without installing or executing its code."""
import argparse
import base64
import csv
from email.parser import BytesParser
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import zipfile

WHEEL = 'tblib-3.1.0-py3-none-any.whl'
WHEEL_SHA256 = '670bb4582578134b3d81a84afa1b016128b429f3d48e6cbbaecc9d15675e984e'
URL = 'https://files.pythonhosted.org/packages/27/44/aa5c8b10b2cce7a053018e0d132bd58e27527a0243c4985383d5b6fd93e9/' + WHEEL
DIST = 'tblib-3.1.0.dist-info/'
MEMBERS = {'tblib/__init__.py', 'tblib/decorators.py', 'tblib/pickling_support.py',
           *(DIST + p for p in ('licenses/AUTHORS.rst', 'licenses/LICENSE', 'METADATA',
                                'WHEEL', 'top_level.txt', 'RECORD'))}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def validate_wheel(raw):
    require(digest(raw) == WHEEL_SHA256, 'official wheel hash differs')
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        require(len(z.namelist()) == len(MEMBERS) and set(z.namelist()) == MEMBERS,
                'wheel closed inventory differs')
        payload = {name: z.read(name) for name in MEMBERS}
        meta = BytesParser().parsebytes(payload[DIST + 'METADATA'])
        require(meta['Name'] == 'tblib' and meta['Version'] == '3.1.0'
                and meta['Requires-Python'] == '>=3.9' and not meta.get_all('Requires-Dist')
                and meta['License'] == 'BSD-2-Clause', 'wheel metadata differs')
        wheel = BytesParser().parsebytes(payload[DIST + 'WHEEL'])
        require(wheel['Root-Is-Purelib'] == 'true' and wheel.get_all('Tag') == ['py3-none-any'],
                'wheel is not reviewed pure Python artifact')
        rows = list(csv.reader(io.StringIO(payload[DIST + 'RECORD'].decode())))
        require(len(rows) == len(MEMBERS) and {r[0] for r in rows} == MEMBERS, 'RECORD inventory differs')
        for name, checksum, size in rows:
            if name == DIST + 'RECORD':
                require(not checksum and not size, 'RECORD self-entry differs')
            else:
                expected = base64.urlsafe_b64encode(hashlib.sha256(payload[name]).digest()).rstrip(b'=').decode()
                require(checksum == 'sha256=' + expected and int(size) == len(payload[name]), 'RECORD digest differs')
    return {name: {'sha256': digest(data), 'size': len(data)} for name, data in sorted(payload.items())}


def read_owned(path):
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path, 'input path must be canonical')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as f:
        st = os.fstat(f.fileno())
        require(stat.S_ISREG(st.st_mode) and st.st_uid in (0, os.getuid())
                and not st.st_mode & 0o022, 'input owner/type/mode differs')
        raw = f.read()
    return raw


def validate_destination(output, authority_path, authority_sha256):
    raw = read_owned(authority_path)
    require(digest(raw) == authority_sha256, 'authority hash differs')
    authority = json.loads(raw)
    require(set(authority) == {'independent_root', 'protected_roots'}, 'authority schema differs')
    root = Path(authority['independent_root'])
    require(root.is_absolute() and root.resolve() == root and root.is_dir(), 'independent root differs')
    require(Path(authority_path).is_relative_to(root), 'authority outside independent root')
    output = Path(output)
    require(output.is_absolute() and output.resolve() == output and output != root
            and output.is_relative_to(root), 'output must be canonical independent descendant')
    require(not output.exists() and not output.is_symlink() and output.parent.is_dir(), 'output must be fresh leaf with existing parent')
    for parent in (output.parent, *output.parent.parents):
        st = parent.stat()
        require(st.st_uid in (0, os.getuid()) and not st.st_mode & 0o022, 'output ancestry is shared-writable or foreign')
    protected = authority['protected_roots']
    require(isinstance(protected, list) and protected, 'missing protected roots')
    for entry in protected:
        p = Path(entry)
        require(p.is_absolute(), 'protected root not absolute')
        for boundary in (p, p.resolve()):
            require(not root.is_relative_to(boundary) and not boundary.is_relative_to(root), 'protected namespace overlaps')
    return output


def prepare(wheel_path, output, authority_path, authority_sha256, apply=False):
    raw = read_owned(wheel_path)
    inventory = validate_wheel(raw)
    destination = validate_destination(output, authority_path, authority_sha256)
    manifest = {'schema_version': 1, 'package': 'tblib', 'version': '3.1.0',
                'wheel': WHEEL, 'wheel_sha256': WHEEL_SHA256, 'wheel_size': len(raw),
                'official_url': URL, 'requires_dist': [], 'license': 'BSD-2-Clause',
                'members': inventory}
    serialized = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
    if apply:
        destination = validate_destination(output, authority_path, authority_sha256)
        destination.mkdir(mode=0o700)
        for name, payload in ((WHEEL, raw), ('manifest.json', serialized)):
            with (destination / name).open('xb') as f:
                f.write(payload)
            (destination / name).chmod(0o444)
        destination.chmod(0o555)
    return {'applied': apply, 'output': str(destination), 'manifest_sha256': digest(serialized), **manifest}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('wheel', 'output', 'authority-path', 'authority-sha256'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--apply', action='store_true')
    a = p.parse_args()
    print(json.dumps(prepare(a.wheel, a.output, a.authority_path, a.authority_sha256, a.apply), sort_keys=True))


if __name__ == '__main__':
    main()
