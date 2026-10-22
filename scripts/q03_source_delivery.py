#!/usr/bin/env python3
"""Opt-in, frozen Mac publication evidence and credentialless Git delivery.

The operator's independently delivered manifest is authority. A bundle and its
adjacent receipt do NOT authenticate themselves. Evidence describes publication
at Mac preparation time, not the current remote branch tip. No import-time I/O.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import zlib

MODE = 'mac-git-bundle-v1'
URLS = {'https://github.com/imDaniel/spark-ds41f.git',
        'git@github.com:imDaniel/spark-ds41f.git'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def lexical(value):
    path = Path(value)
    require(path.is_absolute() and '..' not in path.parts and str(path) == str(value),
            'source target path must be normalized and absolute')
    return path


def canonical(value):
    path = lexical(value)
    require(path.is_absolute() and path.resolve() == path and '..' not in path.parts,
            'source delivery path must be absolute and canonical')
    return path


def owned_ancestry(path):
    for parent in (path, *path.parents):
        info = parent.stat()
        require(info.st_uid in (0, os.getuid()) and not info.st_mode & 0o022,
                'source path ancestry is not owner-controlled')


def regular(value):
    path = canonical(value)
    owned_ancestry(path.parent)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            and info.st_uid == os.getuid() and not info.st_mode & 0o022,
            'source delivery file type, owner, links or permissions differ')
    return path


def overlap(left, right):
    return left == right or left in right.parents or right in left.parents


def publications(manifest):
    rows = {'controller': manifest['preflight']['publication']}
    candidate = manifest.get('candidate', {})
    if 'build_publication' in candidate:
        rows['build'] = candidate['build_publication']
    if 'g1_tests' in candidate:
        rows['g1'] = candidate['g1_tests']['artifact']['publication']
    return rows


def projection(manifest):
    """Only known receipt pins and three runtime-assigned fields are omitted."""
    value = json.loads(json.dumps(manifest))
    for key in ('window_id', 'holder', 'glm_identity'):
        value['preflight'].pop(key, None)
    for pub in publications(value).values():
        pub.get('source_bundle', {}).pop('receipt_sha256', None)
    return hashlib.sha256(encoded(value)).hexdigest()


def validate_manifest(manifest, session_dir=None, *, target_filesystem=True):
    target = canonical if target_filesystem else lexical
    authority = manifest.get('source_delivery')
    pubs = (publications(manifest) if 'publication' in manifest.get('preflight', {}) else {})
    if authority is None:
        require(not any('source_bundle' in pub for pub in pubs.values()),
                'bundle publication requires frozen source delivery authority')
        return False
    require(set(authority) == {'mode', 'preparation_id', 'session_dir', 'sync'}
            and authority['mode'] == MODE
            and re.fullmatch('[0-9a-f]{32}', authority['preparation_id']) is not None,
            'invalid source delivery authority')
    sync = authority['sync']
    require(isinstance(sync, dict) and set(sync) == {
        'cluster_config', 'cluster_config_sha256', 'shape', 'site_files'},
        'source delivery requires the selected sync profile and site-file pins')
    require(sync['shape'] is None or isinstance(sync['shape'], str), 'invalid sync shape binding')
    for relative, sha in {sync['cluster_config']: sync['cluster_config_sha256'], **sync['site_files']}.items():
        path = Path(relative)
        require(not path.is_absolute() and path.parts and '..' not in path.parts
                and path.parts[0] != '.git' and path.as_posix() == relative
                and re.fullmatch('[0-9a-f]{64}', sha), 'invalid sync input binding')
    planned = target(authority['session_dir'])
    if session_dir is not None:
        require(target(session_dir) == planned, 'source receipt belongs to another session')
    root = target(manifest['independent_root'])
    protected = [target(p) for p in manifest['preflight']['protected_roots']]
    writable = [planned]
    for section in ('candidate', 'build'):
        for key in ('output_path', 'cache_path', 'output_directory'):
            value = manifest.get(section, {}).get(key)
            if value is not None:
                writable.append(target(value))
    require(planned != root and root in planned.parents
            and not any(overlap(planned, p) for p in protected),
            'planned source session overlaps protected roots')
    nodes = manifest['preflight']['nodes']
    require(len(nodes) == 4 and len({n['name'] for n in nodes}) == 4
            and {n['rank'] for n in nodes} == {1, 2, 3, 4}, 'source delivery requires all four nodes')
    checkout_roots = [target(pub['source_bundle']['checkout']) for pub in pubs.values()]
    for pub in pubs.values():
        require(pub['url'] in URLS and re.fullmatch('[0-9a-f]{40}', pub['sha']),
                'source publication must pin the exact own-fork URL and commit')
        require(re.fullmatch(r'refs/heads/[A-Za-z0-9_./-]+', pub['ref']),
                'source publication.ref must be fully qualified: refs/heads/<branch>')
        binding = pub['source_bundle']
        require(set(binding) == {'tree', 'checkout', 'git_config_sha256', 'node_git_config_sha256',
                                'bundle_path', 'bundle_sha256', 'receipt_path', 'receipt_sha256'},
                'incomplete source bundle binding')
        require(re.fullmatch('[0-9a-f]{40}', binding['tree']), 'source tree is unpinned')
        for key in ('git_config_sha256', 'bundle_sha256', 'receipt_sha256'):
            require(re.fullmatch('[0-9a-f]{64}', binding[key]), 'source digest is unpinned: ' + key)
        require(set(binding['node_git_config_sha256']) == {n['name'] for n in nodes}
                and all(re.fullmatch('[0-9a-f]{64}', h)
                        for h in binding['node_git_config_sha256'].values()),
                'source delivery lacks all-four Git configuration pins')
        checkout = target(binding['checkout'])
        paths = [target(binding[key]) for key in ('bundle_path', 'receipt_path')]
        require(paths[0] != paths[1], 'source receipt and bundle overlap')
        for path in (checkout, *paths):
            require(path != root and root in path.parents
                    and not any(overlap(path, p) for p in protected),
                    'source delivery overlaps a protected root')
        for path in paths:
            require(not any(overlap(path, source) for source in checkout_roots)
                    and not any(overlap(path, p) for p in writable),
                    'source evidence overlaps checkout or writable output')
    require(pubs['controller']['source_bundle']['checkout'] ==
            manifest['preflight']['independent_checkout'], 'controller source checkout differs')
    return True


def clean_environment(network=False):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('GIT_', 'LD_', 'DYLD_'))}
    env.update(GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0', GIT_CONFIG_NOSYSTEM='1',
               GIT_CONFIG_SYSTEM='/dev/null', GIT_ATTR_NOSYSTEM='1')
    if not network:
        env['GIT_CONFIG_GLOBAL'] = '/dev/null'
    return env


@contextlib.contextmanager
def sanitized_environment():
    previous = dict(os.environ)
    os.environ.clear()
    os.environ.update(clean_environment_from(previous))
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


def clean_environment_from(previous):
    # Evaluate before replacing the process environment, preserving normal PATH.
    env = {k: v for k, v in previous.items() if not k.startswith(('GIT_', 'LD_', 'DYLD_'))}
    env.update(GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0', GIT_CONFIG_NOSYSTEM='1',
               GIT_CONFIG_SYSTEM='/dev/null', GIT_CONFIG_GLOBAL='/dev/null', GIT_ATTR_NOSYSTEM='1')
    return env


def git(root, *args, network=False):
    return subprocess.run(['git', '--no-replace-objects', '-c', 'core.fsmonitor=false',
        '-c', 'core.hooksPath=/dev/null', '-c', 'core.untrackedCache=false',
        '-c', 'http.sslVerify=true',
        '-C', str(root), *args], env=clean_environment(network), check=True,
        capture_output=True, text=True).stdout.strip()


def metadata(root, expected, *, mac_source=False):
    from q03_git_toolchain import validate_git_metadata
    root = canonical(root)
    validate_git_metadata(root, expected)
    regular(root / '.git/config')
    regular(root / '.git/HEAD')
    if (root / '.git/index').exists():
        regular(root / '.git/index')
    for directory in (root / '.git', root / '.git/objects', root / '.git/refs'):
        require(canonical(directory).is_dir(), 'external Git metadata directory rejected')
        owned_ancestry(directory)
    for name in ('shallow', 'info/grafts', 'refs/replace', 'objects/info/alternates',
                 'objects/info/http-alternates'):
        require(not (root / '.git' / name).exists(), 'non-self-contained Git metadata: ' + name)
    config = git(root, 'config', '--local', '--null', '--list')
    for record in config.split('\x00'):
        key = record.partition('\n')[0].lower()
        require(not key.startswith(('url.', 'include.', 'includeif.', 'filter.'))
                and (mac_source or not key.startswith('credential.'))
                and not key.endswith(('.promisor', '.partialclonefilter'))
                and key not in ('extensions.partialclone', 'core.sshcommand', 'core.sparsecheckout',
                                'core.sparsecheckoutcone', 'index.sparse'),
                'source Git transport/filter/credential override rejected')
    for path in (root / '.git').rglob('*'):
        require(not path.is_symlink(), 'external Git metadata alias rejected')
        if path.is_file():
            require(path.stat().st_nlink == 1, 'linked Git metadata rejected')
        info = path.stat()
        require(info.st_uid == os.getuid() and not info.st_mode & 0o022,
                'unsafe Git metadata descendant')
    require(not any((root / '.git/objects/pack').glob('*.promisor')),
            'promisor pack rejected')


def inspect_checkout(root, pub, config_sha256, *, at_target=True, mac_source=False):
    metadata(root, config_sha256, mac_source=mac_source)
    require(git(root, 'remote', 'get-url', 'origin') == pub['url'], 'exact source origin differs')
    require(not git(root, 'status', '--porcelain', '--untracked-files=all'), 'source checkout is dirty')
    require(all(row.startswith('H ') for row in git(root, 'ls-files', '-v', '-z').split('\x00') if row),
            'assume-unchanged or sparse index flags rejected')
    head = git(root, 'rev-parse', 'HEAD')
    if at_target:
        require(head == pub['sha'], 'source HEAD differs')
    require(git(root, 'cat-file', '-t', head) == 'commit', 'source HEAD is not a commit')
    git(root, 'fsck', '--full', '--strict', '--no-reflogs', '--no-dangling', head)
    verify_worktree(root, head)
    if at_target:
        require(git(root, 'rev-parse', head + '^{tree}') == pub['source_bundle']['tree'],
                'source tree differs')
    return head


def verify_worktree(root, head):
    """Hash actual tracked bytes, not merely Git's stat cache or index flags."""
    root = Path(root)
    for row in git(root, 'ls-tree', '-r', '-z', head).split('\x00'):
        if not row:
            continue
        metadata_row, name = row.split('\t', 1)
        mode, kind, oid = metadata_row.split()
        require(kind == 'blob' and mode in ('100644', '100755', '120000'),
                'source bundle does not close over submodule/special entries')
        path = root / name
        require(path.parent.resolve() == path.parent, 'tracked parent alias rejected')
        info = path.lstat()
        require(info.st_nlink == 1, 'tracked hardlink rejected')
        if mode == '120000':
            require(path.is_symlink(), 'tracked symlink mode differs')
            data = os.readlink(path).encode()
            actual = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        else:
            require(stat.S_ISREG(info.st_mode) and bool(info.st_mode & 0o111) == (mode == '100755'),
                    'tracked source type or executable mode differs')
            h = hashlib.sha1(b'blob ' + str(info.st_size).encode() + b'\0')
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    h.update(chunk)
            actual = h.hexdigest()
        after = path.lstat()
        fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_mode', 'st_nlink')
        require(actual == oid and all(getattr(after, key) == getattr(info, key) for key in fields),
                'tracked source bytes changed')


def bundle_header(path, sha):
    with Path(path).open('rb') as stream:
        require(stream.readline() == b'# v2 git bundle\n', 'source bundle format differs')
        require(stream.readline() == (sha + ' HEAD\n').encode(),
                'source bundle has prerequisites, extra refs or a wrong commit')
        require(stream.readline() == b'\n', 'source bundle has extra refs or prerequisites')


def verify_closure(path, pub, directory):
    """Unbundle into EMPTY storage; existing objects cannot satisfy omissions."""
    bundle_header(path, pub['sha'])
    with tempfile.TemporaryDirectory(prefix='.source-verify-', dir=directory) as temporary:
        git(temporary, 'init', '--bare', '--quiet', '--template=')
        git(temporary, 'bundle', 'unbundle', str(path))
        require(git(temporary, 'cat-file', '-t', pub['sha']) == 'commit', 'bundle target is not a commit')
        git(temporary, 'fsck', '--full', '--strict', '--no-reflogs', '--no-dangling', pub['sha'])
        require(git(temporary, 'rev-parse', pub['sha'] + '^{tree}') == pub['source_bundle']['tree'],
                'bundle source tree differs')


def verify_evidence(pub, manifest):
    require(validate_manifest(manifest), 'missing frozen source delivery authority')
    require(pub in publications(manifest).values(), 'publication is outside the frozen manifest')
    binding = pub['source_bundle']
    bundle = regular(binding['bundle_path'])
    receipt_file = regular(binding['receipt_path'])
    require(digest(bundle) == binding['bundle_sha256'], 'source bundle digest differs')
    require(digest(receipt_file) == binding['receipt_sha256'], 'source receipt digest differs')
    bundle_header(bundle, pub['sha'])
    receipt = json.loads(receipt_file.read_text())
    expected = {'schema_version': 1, 'mode': MODE,
        'preparation_id': manifest['source_delivery']['preparation_id'],
        'session_dir': manifest['source_delivery']['session_dir'],
        'manifest_sha256': projection(manifest),
        'publication': {key: pub[key] for key in ('url', 'ref', 'sha')},
        'tree': binding['tree'], 'bundle_sha256': binding['bundle_sha256'],
        'bundle_bytes': bundle.stat().st_size,
        'observation': pub['sha'] + '\t' + pub['ref'], 'producer_system': 'Darwin'}
    require(set(receipt) == set(expected) | {'observed_at', 'producer_hostname', 'source_git_config_sha256'}
            and all(receipt.get(key) == value for key, value in expected.items()),
            'Mac receipt does not bind this exact prepared manifest and artifact')
    when = dt.datetime.fromisoformat(receipt['observed_at'])
    require(when.tzinfo is not None and isinstance(receipt['producer_hostname'], str)
            and bool(receipt['producer_hostname'])
            and re.fullmatch('[0-9a-f]{64}', receipt['source_git_config_sha256']),
            'incomplete Mac observation evidence')
    return receipt


def verify_publication(pub, manifest):
    verify_evidence(pub, manifest)
    binding = pub['source_bundle']
    inspect_checkout(binding['checkout'], pub, binding['git_config_sha256'])


def write_new(path, data):
    path = Path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def prepare(manifest, checkouts, output):
    """Mac only. Existing authentication is used, never exported or configured."""
    require(platform.system() == 'Darwin', 'publication preparation must run on the authenticated Mac')
    require(validate_manifest(manifest, target_filesystem=False), 'missing source delivery mode')
    pubs = publications(manifest)
    require(set(checkouts) == set(pubs), 'provide exactly the selected source checkouts')
    output = canonical(output)
    owned_ancestry(output.parent)
    sources = {name: canonical(path) for name, path in checkouts.items()}
    require(not any(overlap(output, source) for source in sources.values()),
            'Mac source output overlaps an input checkout')
    output.mkdir(mode=0o700, exist_ok=False)
    observations = {}
    for name, pub in pubs.items():
        source = canonical(checkouts[name])
        config_sha256 = digest(regular(source / '.git/config'))
        inspect_checkout(source, pub, config_sha256, mac_source=True)
        # Keep existing Mac credential helpers, but reject effective transport
        # rewrites. Never copy this configuration into a receipt or to a Spark.
        effective = git(source, 'config', '--null', '--list', network=True)
        for record in effective.split('\x00'):
            key = record.partition('\n')[0].lower()
            require(not key.startswith('url.') and key != 'core.sshcommand'
                    and not key.endswith('.uploadpack')
                    and not (key.startswith('http.') and key.endswith('.sslverify')
                             and record.partition('\n')[2].lower() in ('false', 'no', 'off', '0')), 'Mac Git transport override rejected')
        observed = git(source, 'ls-remote', '--exit-code', pub['url'], pub['ref'], network=True)
        require(observed.split() == [pub['sha'], pub['ref']], 'exact Mac branch publication differs')
        observed_at = dt.datetime.now(dt.timezone.utc).isoformat()
        bundle = output / (name + '.bundle')
        git(source, 'bundle', 'create', '--version=2', str(bundle), 'HEAD')
        bundle.chmod(0o600)
        verify_closure(bundle, pub, output)
        inspect_checkout(source, pub, config_sha256, mac_source=True)
        pub['source_bundle']['bundle_sha256'] = digest(bundle)
        observations[name] = (observed_at, config_sha256, bundle.stat().st_size)
    for name, pub in pubs.items():
        observed_at, config_sha256, size = observations[name]
        receipt = {'schema_version': 1, 'mode': MODE,
            'preparation_id': manifest['source_delivery']['preparation_id'],
            'session_dir': manifest['source_delivery']['session_dir'],
            'manifest_sha256': projection(manifest),
            'publication': {key: pub[key] for key in ('url', 'ref', 'sha')},
            'tree': pub['source_bundle']['tree'],
            'bundle_sha256': pub['source_bundle']['bundle_sha256'], 'bundle_bytes': size,
            'observation': pub['sha'] + '\t' + pub['ref'], 'producer_system': 'Darwin',
            'producer_hostname': platform.node(), 'observed_at': observed_at,
            'source_git_config_sha256': config_sha256}
        path = output / (name + '.receipt.json')
        write_new(path, encoded(receipt) + b'\n')
        pub['source_bundle']['receipt_sha256'] = digest(path)
    write_new(output / 'prepared-manifest.json', encoded(manifest) + b'\n')
    return manifest


def _owned_parent(path, root):
    path = canonical(path)
    root = canonical(root)
    require(root.is_dir() and (path == root or root in path.parents),
            'source output leaves the existing independent root')
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    # Refuse unsafe existing ancestry BEFORE making even the first directory.
    canonical(current)
    owned_ancestry(current)
    for directory in reversed(missing):
        directory.mkdir(mode=0o700, exist_ok=False)
        canonical(directory)
        owned_ancestry(directory)


def _receive_bundle(path, expected):
    """Publish immutable bytes exclusively, or verify an already identical file."""
    path = Path(path)
    existing = path.exists()
    if existing:
        require(digest(regular(path)) == expected, 'existing source bundle differs')
        temporary = None
        stream = None
    else:
        fd, name = tempfile.mkstemp(prefix='.source-incoming-', dir=path.parent)
        temporary = Path(name)
        stream = os.fdopen(fd, 'wb')
    try:
        h = hashlib.sha256()
        for chunk in iter(lambda: sys.stdin.buffer.read(1024 * 1024), b''):
            h.update(chunk)
            if stream is not None:
                stream.write(chunk)
        require(h.hexdigest() == expected, 'received source bundle digest differs')
        if stream is not None:
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
            stream = None
            os.link(temporary, path)  # Exclusive publication; never replace a raced path.
    finally:
        if stream is not None:
            stream.close()
        if temporary is not None:
            temporary.unlink()


def site_destination(root, relative):
    path = canonical(Path(root) / relative)
    require(path != Path(root) and Path(root) in path.parents, 'site destination leaves checkout')
    parent = path.parent
    while not parent.exists():
        parent = parent.parent
    owned_ancestry(canonical(parent))
    if path.exists() or path.is_symlink():
        regular(path)
    return path


def copy_site_files(manifest, texts):
    binding = manifest['source_delivery']['sync']['site_files']
    require({name: hashlib.sha256(text.encode()).hexdigest() for name, text in texts.items()} == binding,
            'site transfer bytes differ from the frozen manifest')
    root = manifest['preflight']['publication']['source_bundle']['checkout']
    for relative, text in texts.items():
        path = site_destination(root, relative)
        _owned_parent(path.parent, root)
        fd, name = tempfile.mkstemp(prefix='.source-site-', dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(text.encode())
                stream.flush()
                os.fsync(stream.fileno())
            site_destination(root, relative)
            os.replace(temporary, path)  # Never follows destination symlinks/hardlinks.
        finally:
            if temporary.exists():
                temporary.unlink()


def remote_action(action, manifest, name, hostname, previous=None, receipt_text=None, site_texts=None):
    """Fixed receiver operations, sent over the existing authenticated SSH link."""
    validate_manifest(manifest)
    require(platform.node() == hostname, 'source delivery reached the wrong host')
    require(name in {n['name'] for n in manifest['preflight']['nodes']}, 'unknown source node')
    pub = manifest['preflight']['publication']
    binding = pub['source_bundle']
    root = binding['checkout']
    head = inspect_checkout(root, pub, binding['node_git_config_sha256'][name], at_target=False)
    sync = manifest['source_delivery']['sync']
    def verify_profile():
        require(digest(regular(Path(root) / sync['cluster_config'])) == sync['cluster_config_sha256'],
                'selected node profile differs from prepared site')
    for relative in sync['site_files']:
        site_destination(root, relative)
    if action in ('probe', 'stage', 'site'):
        verify_profile()
    if action == 'site':
        require(head == pub['sha'], 'site transfer requires the pinned checked-out source')
        copy_site_files(manifest, site_texts)
        return {'copied': sorted(site_texts)}
    if action == 'probe':
        return {'head': head, 'origin': pub['url'], 'status': ''}
    if action == 'stage':
        require(head == previous, 'node HEAD changed before source staging')
        for key in ('bundle_path', 'receipt_path'):
            _owned_parent(Path(binding[key]).parent, manifest['independent_root'])
        require(hashlib.sha256(receipt_text.encode()).hexdigest() == binding['receipt_sha256'],
                'transferred source receipt differs')
        _receive_bundle(binding['bundle_path'], binding['bundle_sha256'])
        receipt = Path(binding['receipt_path'])
        if receipt.exists():
            require(digest(regular(receipt)) == binding['receipt_sha256'], 'existing receipt differs')
        else:
            write_new(receipt, receipt_text.encode())
        verify_evidence(pub, manifest)
        verify_closure(binding['bundle_path'], pub, Path(binding['bundle_path']).parent)
        git(root, 'bundle', 'unbundle', binding['bundle_path'])
        git(root, 'fsck', '--full', '--strict', '--no-reflogs', '--no-dangling', pub['sha'])
        require(git(root, 'rev-parse', pub['sha'] + '^{tree}') == binding['tree'], 'staged tree differs')
        return {'head': head, 'staged': pub['sha'], 'tree': binding['tree'],
                'receipt_sha256': binding['receipt_sha256'], 'bundle_sha256': binding['bundle_sha256']}
    if action == 'checkout':
        require(head == previous, 'node HEAD changed before source checkout')
        verify_evidence(pub, manifest)
        require(git(root, 'rev-parse', pub['sha'] + '^{tree}') == binding['tree'], 'staged tree differs')
        git(root, 'checkout', '--quiet', '--detach', pub['sha'])
    elif action == 'rollback':
        if head == previous:
            return {'rolled_back': previous}
        require(head == pub['sha'] and re.fullmatch('[0-9a-f]{40}', previous),
                'cannot roll back unowned source state')
        git(root, 'checkout', '--quiet', '--detach', previous)
        require(inspect_checkout(root, pub, binding['node_git_config_sha256'][name],
                                 at_target=False) == previous, 'rollback source state differs')
        return {'rolled_back': previous}
    else:
        require(action == 'verify', 'unknown source delivery action')
        verify_evidence(pub, manifest)
        sync = manifest['source_delivery']['sync']
        for relative, sha in {sync['cluster_config']: sync['cluster_config_sha256'], **sync['site_files']}.items():
            require(digest(regular(Path(root) / relative)) == sha, 'node sync input differs: ' + relative)
    verify_profile()  # Refuse changed/aliased profiles before the caller runs doctor.
    inspect_checkout(root, pub, binding['node_git_config_sha256'][name])
    return {'head': pub['sha'], 'tree': binding['tree']}


class Sync:
    """Adapter for the existing sync checkout, site-map, doctor and rollback loop."""
    def __init__(self, spark, args, cluster, nodes):
        self.spark, self.nodes = spark, nodes
        self.manifest = json.loads(regular(args.source_manifest).read_text())
        require(validate_manifest(self.manifest), 'source manifest is not bundle mode')
        require(not canonical(self.manifest['source_delivery']['session_dir']).exists(),
                'source delivery cannot mutate an already-created session')
        self.pub = self.manifest['preflight']['publication']
        self.binding = self.pub['source_bundle']
        self.revision = self.pub['sha']
        require(str(spark.ROOT.resolve()) == self.binding['checkout']
                and spark.repository_path(cluster) == self.binding['checkout'],
                'sync must use the frozen independent checkout')
        require(cluster['deployment']['repository'] == self.pub['url'], 'sync repository differs')
        branch = cluster['deployment'].get('branch')
        require(branch is None or self.pub['ref'] == 'refs/heads/' + branch,
                'source receipt is not for the configured deployment branch')
        require(args.revision in (None, self.revision), 'bundle sync requires its exact frozen revision')
        require(len(nodes['nodes']) == 4 and {n['name'] for n in nodes['nodes']} ==
                {n['name'] for n in self.manifest['preflight']['nodes']}, 'sync requires the same four nodes')
        frozen_nodes = {n['name']: n for n in self.manifest['preflight']['nodes']}
        require([n['rank'] for n in frozen_nodes.values() if n.get('local') is True] == [1],
                'source delivery requires the registered local head')
        for node in nodes['nodes']:
            frozen = frozen_nodes[node['name']]
            require(node['rank'] == frozen['rank'] - 1
                    and bool(node['head']) == (frozen.get('local') is True),
                    'sync node rank or head differs from the frozen inventory')
            if node['head']:
                require(self.binding['node_git_config_sha256'][node['name']] == self.binding['git_config_sha256'],
                        'head Git configuration pin differs')
        sync = self.manifest['source_delivery']['sync']
        require(spark.cluster_config_path(args)[1] == sync['cluster_config']
                and getattr(args, 'shape', None) == sync['shape'], 'selected sync profile or shape differs')
        require(digest(regular(spark.ROOT / sync['cluster_config'])) == sync['cluster_config_sha256'],
                'selected sync profile bytes differ')
        expected_site = {spark.NODES_FILE, cluster.get('nodes_config', spark.NODES_FILE)}
        if (spark.ROOT / spark.cluster_recipe.SITE_FILE).is_file():
            expected_site.add(spark.cluster_recipe.SITE_FILE)
        require(set(sync['site_files']) == expected_site, 'copied site-file selection differs')
        for relative, sha in sync['site_files'].items():
            require(digest(regular(spark.ROOT / relative)) == sha, 'selected site-file bytes differ')
        verify_evidence(self.pub, self.manifest)
        inspect_checkout(self.binding['checkout'], self.pub, self.binding['git_config_sha256'], at_target=False)
        if args.apply:
            verify_closure(self.binding['bundle_path'], self.pub, Path(self.binding['bundle_path']).parent)
        # A head checkout can change these files while this coordinator runs.
        # Freeze the already verified adapter bytes once, never hot-reload them.
        self.helper_source = Path(__file__).with_name('q03_git_toolchain.py').read_text()
        self.adapter_source = Path(__file__).read_text()
        self.hostnames = {}
        for node in self.manifest['preflight']['nodes']:
            require(digest(regular(node['inventory_path'])) == node['inventory_sha256'],
                    'source node inventory differs')
            receipt = json.loads(Path(node['inventory_path']).read_text())
            require(receipt['node'] == node['name'] and receipt.get('exit_code') == 0,
                    'source node inventory is not successful')
            self.hostnames[node['name']] = receipt['inventory']['hostname']
        require(len(set(self.hostnames.values())) == 4, 'source inventory names do not identify four hosts')

    def command(self, action, node, previous=None, site_texts=None):
        payload = {'action': action, 'manifest': self.manifest, 'name': node['name'],
                   'hostname': self.hostnames[node['name']], 'previous': previous, 'site_texts': site_texts}
        if action == 'stage':
            payload['receipt_text'] = regular(self.binding['receipt_path']).read_text()
        packed = base64.b85encode(zlib.compress(encoded([self.helper_source, self.adapter_source, payload]))).decode()
        script = ("import base64,json,sys,types,zlib\n"
                  "h,c,p=json.loads(zlib.decompress(base64.b85decode(" + repr(packed) + ")))\n"
                  "m=types.ModuleType('q03_git_toolchain');sys.modules[m.__name__]=m;exec(h,m.__dict__)\n"
                  "n={'__name__':'q03_source_receiver'};exec(c,n)\n"
                  "print(json.dumps(n['remote_action'](**p)))\n")
        require(len(shlex.join(['python3', '-I', '-B', '-c', script])) < 90000,
                'source transfer command exceeds bounded SSH argument size')
        return ['python3', '-I', '-B', '-c', script]

    def invoke(self, action, node, previous=None, transfer=False, site_texts=None):
        command = self.command(action, node, previous, site_texts)
        if transfer:
            with regular(self.binding['bundle_path']).open('rb') as stream:
                return subprocess.run(['ssh', *self.spark.ssh_options(),
                    self.spark.transfer_target(self.nodes, node), shlex.join(command)],
                    stdin=stream, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return self.spark.run_ssh(self.nodes, node, *command)

    def inspect(self, node):
        result = self.invoke('probe', node)
        require(result.returncode == 0, node['name'] + ': source prevalidation failed: ' + result.stderr.strip())
        row = json.loads(result.stdout)
        return row['head'], row['origin'], row['status']

    def copy_site(self, node, texts):
        result = self.invoke('site', node, site_texts=texts)
        require(result.returncode == 0 and json.loads(result.stdout) == {'copied': sorted(texts)},
                node['name'] + ': safe site-file transfer failed')

    def validate_site_texts(self, texts):
        require({name: hashlib.sha256(text.encode()).hexdigest() for name, text in texts.items()} ==
                self.manifest['source_delivery']['sync']['site_files'], 'site files changed before copy')

    def stage(self, previous):
        # Caller has already prevalidated ALL four nodes. No checkout happens
        # until each node independently verifies and stages the same closure.
        for node in self.nodes['nodes']:
            result = self.invoke('stage', node, previous[node['name']], transfer=True)
            require(result.returncode == 0, node['name'] + ': source staging failed: ' + result.stderr.strip())
            row = json.loads(result.stdout)
            require(row == {'head': previous[node['name']], 'staged': self.revision,
                            'tree': self.binding['tree'], 'receipt_sha256': self.binding['receipt_sha256'],
                            'bundle_sha256': self.binding['bundle_sha256']}, 'source staging receipt differs')

    def finish(self):
        for node in self.nodes['nodes']:
            result = self.invoke('verify', node)
            require(result.returncode == 0 and json.loads(result.stdout) ==
                    {'head': self.revision, 'tree': self.binding['tree']},
                    node['name'] + ': final source identity differs')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare'])
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--checkouts', required=True, help='JSON mapping controller/build/g1 to local Mac checkout paths')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    prepare(json.loads(regular(args.manifest).read_text()),
            json.loads(regular(args.checkouts).read_text()), args.output)


if __name__ == '__main__':
    main()
