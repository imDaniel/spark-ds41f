#!/usr/bin/env python3
"""Validate and launch the reviewed real, read-only P0 Git toolchain.

No Git emulation or target imports. Loader/library selection affects only this
Git process; the serving Python process retains its original environment.
"""
from __future__ import annotations
import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

GIT_SHA256 = 'aa6540695d076182256dd6e96c8b302e4d56381e3000bbfd5c71bbdfe94a4942'
SHIM = '#!/bin/sh\nexec python3 -I -B /git-toolchain/launcher.py --launch "$@"\n'
ELF_FILES = ('real/git', 'lib/ld-linux-aarch64.so.1', 'lib/libc.so.6',
             'lib/libpcre2-8.so.0', 'lib/libz.so.1')
LICENSE_FILES = tuple('licenses/' + name + '/copyright' for name in
                      ('git', 'libc6', 'libpcre2-8-0', 'zlib1g')) + (
                          'licenses/common/GPL-2', 'licenses/common/LGPL-2.1')
FILES = frozenset((*ELF_FILES, *LICENSE_FILES, 'bin/git', 'launcher.py'))
ALLOWED_ARGV = (('rev-parse', 'HEAD'), ('status', '--porcelain'))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def canonical(path):
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path,
            'toolchain path must be absolute and canonical, without symlinks')
    return path


def checked_file(path, owner):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == owner and
            not info.st_mode & 0o022, 'toolchain file ownership/type/permissions changed')
    require(path.resolve() == path, 'toolchain file alias rejected')
    return path.read_bytes(), info


def validate_bundle(root, expected_sha256, *, expected_owner=None):
    root = canonical(root)
    require(isinstance(expected_sha256, str) and
            re.fullmatch('[0-9a-f]{64}', expected_sha256) is not None,
            'missing pinned toolchain manifest digest')
    info = root.lstat()
    owner = info.st_uid if expected_owner is None else expected_owner
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == owner and
            not info.st_mode & 0o022, 'unsafe toolchain root')
    raw, _ = checked_file(root / 'manifest.json', owner)
    require(sha256(raw) == expected_sha256, 'toolchain manifest digest changed')
    manifest = json.loads(raw)
    require(manifest.get('schema_version') == 1 and
            manifest.get('architecture') == 'aarch64' and
            manifest.get('git_version') == '2.43.0' and
            manifest.get('package_version') == '1:2.43.0-1ubuntu7.3',
            'unreviewed Git toolchain version/architecture')
    records = manifest.get('files')
    require(isinstance(records, dict) and set(records) == FILES,
            'toolchain file inventory differs from reviewed closure')
    actual = set()
    for path in root.rglob('*'):
        relative = path.relative_to(root).as_posix()
        st = path.lstat()
        require(not stat.S_ISLNK(st.st_mode) and st.st_uid == owner and
                not st.st_mode & 0o022, 'unsafe toolchain descendant')
        if stat.S_ISDIR(st.st_mode):
            require(any(name.startswith(relative + '/') for name in FILES),
                    'extra toolchain directory')
            continue
        require(stat.S_ISREG(st.st_mode), 'nonregular toolchain member')
        actual.add(relative)
    require(actual == FILES | {'manifest.json'}, 'extra/missing toolchain files')
    for relative, record in records.items():
        require(isinstance(record, dict), 'invalid toolchain file record')
        data, st = checked_file(root / relative, owner)
        require(sha256(data) == record.get('sha256') and
                type(record.get('size')) is int and len(data) == record['size'] and
                type(record.get('mode')) is int and
                stat.S_IMODE(st.st_mode) == record['mode'],
                'toolchain member bytes/size/mode changed: ' + relative)
        if relative in ELF_FILES:
            require(data[:6] == b'\x7fELF\x02\x01' and len(data) >= 20 and
                    int.from_bytes(data[18:20], 'little') == 183,
                    'toolchain ELF is not 64-bit little-endian AArch64')
        if relative in ('bin/git', 'real/git', 'lib/ld-linux-aarch64.so.1'):
            require(st.st_mode & 0o111, 'toolchain executable permission missing')
    require(records['real/git']['sha256'] == GIT_SHA256,
            'Git binary is not the reviewed Ubuntu artifact')
    require((root / 'bin/git').read_text() == SHIM, 'Git shim changed')
    require((root / 'launcher.py').read_bytes() == Path(__file__).read_bytes(),
            'Git launcher differs from pinned controller source')
    return manifest


def validate_git_metadata(root, expected_sha256):
    """Bound the two P0 reads to a reviewed, self-contained local repository."""
    root = canonical(root)
    git = root / '.git'
    require(git.is_dir() and git.resolve() == git, 'P0 needs a self-contained .git directory')
    require(isinstance(expected_sha256, str) and
            re.fullmatch('[0-9a-f]{64}', expected_sha256) is not None,
            'missing pinned local Git config digest')
    for relative in ('commondir', 'config.worktree', 'objects/info/alternates',
                     'objects/info/http-alternates'):
        require(not (git / relative).exists() and not (git / relative).is_symlink(),
                'unvalidated external/worktree Git metadata: ' + relative)
    raw, _ = checked_file(git / 'config', git.stat().st_uid)
    require(sha256(raw) == expected_sha256, 'local Git config digest changed')
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        parser.read_string(raw.decode('utf-8'))
    except (UnicodeError, configparser.Error) as exc:
        raise ValueError('local Git config is not the bounded reviewed format') from exc
    require(not parser.defaults(), 'Git config DEFAULT indirection is not supported')
    for section in parser.sections():
        family = section.strip().lower().split(None, 1)[0].split('.', 1)[0]
        require(family not in ('include', 'includeif'), 'external Git config includes rejected')
        keys = {key.lower() for key in parser[section]}
        require(not (family == 'filter' and keys & {'clean', 'smudge', 'process'}),
                'external Git filter commands rejected')
        require(not (family == 'core' and keys & {'worktree', 'fsmonitor', 'hookspath'}),
                'Git worktree or external monitor/hook redirection rejected')
        require(not (family == 'extensions' and 'worktreeconfig' in keys),
                'unvalidated per-worktree configuration rejected')
    return {'config_sha256': expected_sha256, 'self_contained': True}


def validate_toolchain(candidate, ops):
    """Prehold host validation; no subprocess, Git execution or target imports."""
    del ops  # Files are validated directly, allowing real temporary-file CPU fixtures.
    root = candidate.get('git_toolchain_path')
    require(isinstance(root, str), 'missing independent Git toolchain path')
    digest = candidate.get('git_toolchain_manifest_sha256')
    manifest = validate_bundle(root, digest, expected_owner=os.getuid())
    return {'path': root, 'manifest_sha256': digest, 'manifest': manifest}


def git_command(root, argv, inherited, cwd):
    """Construct only the exact two unchanged Git calls made by frozen197 P0."""
    require(tuple(argv) in ALLOWED_ARGV, 'unreviewed Git command')
    require(str(Path(cwd).resolve()) == '/validation', 'Git cwd must be /validation')
    root = str(canonical(root))
    env = {key: value for key, value in inherited.items()
           if not key.startswith(('GIT_', 'LD_'))}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_SYSTEM='/dev/null',
               GIT_CONFIG_GLOBAL='/dev/null', GIT_OPTIONAL_LOCKS='0',
               GIT_TERMINAL_PROMPT='0', GIT_ATTR_NOSYSTEM='1')
    command = [root + '/lib/ld-linux-aarch64.so.1', '--library-path', root + '/lib',
               root + '/real/git', '--no-pager', '-c', 'safe.directory=/validation',
               '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null',
               '-c', 'core.untrackedCache=false', *argv]
    return command, env


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    require(argv and argv.pop(0) == '--launch', 'only fixed Git launch is supported')
    root = Path('/git-toolchain')
    validate_bundle(root, os.environ.get('Q03_GIT_TOOLCHAIN_MANIFEST_SHA256'))
    validate_git_metadata(Path('/validation'), os.environ.get('Q03_GIT_CONFIG_SHA256'))
    command, env = git_command(root, argv, os.environ, os.getcwd())
    os.execve(command[0], command, env)


if __name__ == '__main__':
    main()
