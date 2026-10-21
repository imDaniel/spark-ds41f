#!/usr/bin/env python3
"""Package the reviewed installed Ubuntu ARM64 Git into a fresh private directory.

Host-only preparation: never runs Git, ldd, the copied loader, or container code.
No downloads/install/config changes. A partial failed output is retained as evidence.
Review the emitted manifest hash before admitting the bundle to a STOP-gated job.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import subprocess

GIT_SHA256 = 'aa6540695d076182256dd6e96c8b302e4d56381e3000bbfd5c71bbdfe94a4942'
PACKAGES = {'git': '1:2.43.0-1ubuntu7.3', 'libc6': '2.39-0ubuntu8.9',
            'libpcre2-8-0': '10.42-4ubuntu2.1', 'zlib1g': '1:1.3.dfsg-3.1ubuntu2.2'}
LIBRARIES = {'ld-linux-aarch64.so.1': 'libc6', 'libc.so.6': 'libc6',
             'libpcre2-8.so.0': 'libpcre2-8-0', 'libz.so.1': 'zlib1g'}
SHIM = b'#!/bin/sh\nexec python3 -I -B /git-toolchain/launcher.py --launch "$@"\n'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def elf_dependencies(raw):
    """Read ELF64 little-endian DT_NEEDED without loading any executable bytes."""
    require(len(raw) >= 64 and raw[:6] == b'\x7fELF\x02\x01', 'not ELF64 little-endian')
    require(struct.unpack_from('<H', raw, 18)[0] == 183, 'not AArch64 ELF')
    off = struct.unpack_from('<Q', raw, 40)[0]
    size, count = struct.unpack_from('<HH', raw, 58)
    require(size == 64 and count > 0 and off + size * count <= len(raw), 'invalid ELF sections')
    sections = [struct.unpack_from('<IIQQQQIIQQ', raw, off + i * size) for i in range(count)]
    needed = []
    for sec in sections:
        if sec[1] != 6:  # SHT_DYNAMIC, with linked string table
            continue
        require(sec[6] < count and sec[5] % 16 == 0, 'invalid ELF dynamic table')
        strings = sections[sec[6]]
        require(sec[4] + sec[5] <= len(raw) and strings[4] + strings[5] <= len(raw), 'ELF table outside file')
        table = raw[strings[4]:strings[4] + strings[5]]
        for pos in range(sec[4], sec[4] + sec[5], 16):
            tag, val = struct.unpack_from('<qQ', raw, pos)
            if tag == 1:
                require(val < len(table) and b'\0' in table[val:], 'invalid ELF dependency')
                needed.append(table[val:table.index(b'\0', val)].decode('ascii'))
    require(set(needed) <= set(LIBRARIES), 'unexpected ELF dependency: ' + repr(needed))
    return needed


def read_source(path, root_owned=True):
    path = Path(path).resolve(strict=True)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and not before.st_mode & 0o022,
                'source must be regular and not shared-writable')
        require(before.st_uid == (0 if root_owned else os.getuid()), 'source owner differs')
        raw = stream.read()
        after = os.fstat(stream.fileno())
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), 'source changed while reading')
    return path, raw


def source_inputs(path, expected_sha256):
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path, 'source facts path must be canonical')
    _, raw = read_source(path, root_owned=False)
    require(digest(raw) == expected_sha256, 'source facts hash differs')
    facts = json.loads(raw)['files']
    expected = {'/usr/bin/git', '/lib/ld-linux-aarch64.so.1',
                '/lib/aarch64-linux-gnu/libpcre2-8.so.0', '/lib/aarch64-linux-gnu/libz.so.1',
                '/lib/aarch64-linux-gnu/libc.so.6',
                *('/usr/share/doc/' + p + '/copyright' for p in PACKAGES),
                '/usr/share/common-licenses/GPL-2', '/usr/share/common-licenses/LGPL-2.1'}
    require(isinstance(facts, list) and len(facts) == len(expected)
            and {x['requested'] for x in facts} == expected, 'source facts inventory differs')
    snapshots = {}
    for item in facts:
        requested, resolved = Path(item['requested']), Path(item['resolved'])
        require(resolved.is_absolute() and requested.resolve(strict=True) == resolved
                and resolved.resolve() == resolved, 'source alias differs from pinned facts')
        fd = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_uid == item['uid'] == 0
                    and not before.st_mode & 0o022
                    and oct(stat.S_IMODE(before.st_mode)) == item['mode'], 'source metadata differs')
            payload = stream.read()
            after = os.fstat(stream.fileno())
        require(before.st_size == item['bytes'] == len(payload)
                and digest(payload) == item['sha256']
                and (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns),
                'source bytes changed or differ from pinned facts')
        require(resolved not in snapshots, 'duplicate resolved source')
        snapshots[resolved] = payload
    return snapshots


def query(argv):
    return subprocess.run(['/usr/bin/dpkg-query', *argv], check=True, text=True,
                          capture_output=True, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}).stdout


def inventory(query_fn=query):
    result = {}
    for package, expected in PACKAGES.items():
        row = query_fn(['-W', '-f=${Version}\t${Architecture}\t${Status}', package]).strip()
        require(row == expected + '\tarm64\tinstall ok installed', 'installed package differs: ' + package)
        paths = [Path(p) for p in query_fn(['-L', package]).splitlines() if p.startswith('/')]
        result[package] = paths
    return result


def collect(query_fn=query, reader=read_source, launcher=None):
    packages = inventory(query_fn)
    files = {}
    def add(relative, path, package, executable=False):
        canonical, raw = reader(path)
        owned = {p.resolve() for p in packages[package] if p.exists()}
        require(canonical in owned, 'source not in installed package file list: ' + str(path))
        deps = elf_dependencies(raw) if executable or relative.startswith('lib/') else None
        files[relative] = (raw, {'source_path': str(canonical), 'package': package,
                               'mode': 0o555 if executable else 0o444,
                               **({'needed': deps} if deps is not None else {})})
    require(stat.S_IMODE(Path('/usr/bin/git').stat().st_mode) == 0o755, 'host Git mode differs')
    add('real/git', Path('/usr/bin/git'), 'git', True)
    require(digest(files['real/git'][0]) == GIT_SHA256, 'reviewed Git SHA256 differs')
    for name, package in LIBRARIES.items():
        matches = {p.resolve() for p in packages[package] if p.name == name and p.exists()}
        require(len(matches) == 1, 'ambiguous/missing installed library: ' + name)
        add('lib/' + name, matches.pop(), package, name.startswith('ld-linux'))
    for package in PACKAGES:
        add('licenses/' + package + '/copyright', Path('/usr/share/doc') / package / 'copyright', package)
    for name in ('GPL-2', 'LGPL-2.1'):
        canonical, raw = reader(Path('/usr/share/common-licenses') / name)
        files['licenses/common/' + name] = (raw, {'source_path': str(canonical), 'mode': 0o444})
    launcher = Path(launcher) if launcher else Path(__file__).with_name('q03_git_toolchain.py')
    canonical, raw = reader(launcher, root_owned=False)
    files['launcher.py'] = (raw, {'source_path': str(canonical), 'mode': 0o444})
    files['bin/git'] = (SHIM, {'source_path': 'fixed-reviewed-shim', 'mode': 0o555})
    return files


def validate_destination(output):
    path = Path(output)
    require(path.is_absolute() and '..' not in path.parts and path.resolve() == path,
            'output must be absolute and canonical')
    require(not path.exists() and not path.is_symlink(), 'output must be a fresh absent leaf')
    require(path.parent.is_dir(), 'output parent must already exist')
    for parent in (path.parent, *path.parent.parents):
        info = parent.stat()
        require(info.st_uid in (0, os.getuid()) and not info.st_mode & 0o022,
                'output ancestry is not owner-controlled')
    return path


def validate_authority(output, authority, expected_sha256):
    path = Path(authority)
    require(path.is_absolute() and path.resolve() == path, 'authority path must be canonical')
    canonical, raw = read_source(path, root_owned=False)
    require(digest(raw) == expected_sha256, 'private authority hash differs')
    value = json.loads(raw)
    require(set(value) == {'independent_root', 'protected_roots'}, 'private authority schema differs')
    root = Path(value['independent_root'])
    require(root.is_absolute() and root.resolve() == root and root.is_dir(), 'invalid independent root')
    require(path.is_relative_to(root) and path != root, 'authority outside independent root')
    destination = validate_destination(output)
    require(destination.is_relative_to(root) and destination != root, 'output outside independent root')
    protected = value['protected_roots']
    require(isinstance(protected, list) and protected, 'missing protected roots')
    for item in protected:
        boundary = Path(item)
        require(boundary.is_absolute(), 'protected root must be absolute')
        for target in (boundary, boundary.resolve()):
            require(not root.is_relative_to(target) and not target.is_relative_to(root),
                    'independent root overlaps protected namespace')
    return destination


def prepare(output, files, source_inputs_sha256=None):
    output = validate_destination(output)
    output.mkdir(mode=0o700)  # exclusive; partial failure remains visible
    entries = {}
    for relative, (raw, metadata) in sorted(files.items()):
        path = output / relative
        require(not Path(relative).is_absolute() and '..' not in Path(relative).parts,
                'invalid relative bundle file')
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        path.chmod(metadata['mode'])
        entries[relative] = dict(metadata, sha256=digest(raw), size=len(raw))
    manifest = {'schema_version': 1, 'architecture': 'aarch64', 'git_version': '2.43.0',
                'package_version': PACKAGES['git'], 'packages': PACKAGES, 'files': entries,
                'source_inputs_sha256': source_inputs_sha256}
    raw = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    with (output / 'manifest.json').open('xb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    (output / 'manifest.json').chmod(0o444)
    for path in sorted((p for p in output.rglob('*') if p.is_dir()), reverse=True):
        path.chmod(0o555)
    output.chmod(0o555)
    for directory in [*(p for p in output.rglob('*') if p.is_dir()), output, output.parent]:
        fd = os.open(directory, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return {'manifest_path': str(output / 'manifest.json'), 'manifest_sha256': digest(raw),
            'status': 'prepared-unreviewed', 'container_execution': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--authority-path', '--authority', dest='authority', required=True)
    parser.add_argument('--authority-sha256', required=True)
    parser.add_argument('--source-inputs', required=True)
    parser.add_argument('--source-inputs-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    validate_authority(args.output, args.authority, args.authority_sha256)
    snapshots = source_inputs(args.source_inputs, args.source_inputs_sha256)
    def pinned_reader(path, root_owned=True):
        if not root_owned:
            return read_source(path, root_owned=False)
        canonical = Path(path).resolve(strict=True)
        require(canonical in snapshots, 'source absent from reviewed input facts')
        return canonical, snapshots[canonical]
    files = collect(reader=pinned_reader)
    if not args.apply:
        print(json.dumps({'status': 'plan-only', 'files': sorted(files), 'packages': PACKAGES,
                          'copy_performed': False, 'container_execution': False}))
        return
    # Recheck private boundary after source collection, before the exclusive write.
    validate_authority(args.output, args.authority, args.authority_sha256)
    print(json.dumps(prepare(args.output, files, args.source_inputs_sha256), sort_keys=True))


if __name__ == '__main__':
    main()
