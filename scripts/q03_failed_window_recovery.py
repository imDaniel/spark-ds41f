#!/usr/bin/env python3
"""One exact failed-window closure, NEVERSTART; no historical evidence repair.

Default is read-only admission. --apply requires a new independent journal.
Only an actively locked recovery refreshes its owned hold. Pre-release failures
retain the hold; post-unlink failures report uncertain release separately.
The new journal is retained for review; there is no automatic retry or resume.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import threading
import time
import types
import uuid

WINDOW = "bd3fceb06763454fa67d5712fdea4bbf"
CAPSULE_SHA256 = "2a01a8dc02ea1673a0bf7b460c8d90761ba5a6a6c86ac9805720b069ab1d7586"
OLD_HEAD = "652d5fc9b3957158348c598dedc9948c47c14221"
OLD_EPOCH = "5c05ebb2083745dda93dc1cfa066b70b"
PACKET_SHA256 = "bc1b4db3d53363db99e29451bf83bb8986c735e1d88e876f947d23e0cffedf16"
MAX_BYTES = 4 * 1024 * 1024
WITNESS_SHA256 = "837504ffffe5ec94f08dd8e76870e25802c2f87186270efdd47b16aabfd854d2"
_WITNESS = None
# Child execution compiles the SAME nofollow/hash-verified bytes; never a path
# reopen or a bytecode cache after the parent validated the source.
ENTRY_BOOTSTRAP = r"""
import hashlib,os,pathlib,stat,sys
path,expected=sys.argv[1:3]
p=pathlib.Path(path)
assert p.is_absolute() and p.resolve()==p
for q in (p.parent,*p.parent.parents):
 s=q.lstat(); assert stat.S_ISDIR(s.st_mode) and s.st_uid in (0,os.getuid()) and not s.st_mode&0o022
fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
with os.fdopen(fd,'rb') as f:
 s=os.fstat(f.fileno()); assert stat.S_ISREG(s.st_mode) and s.st_nlink==1 and s.st_uid in (0,os.getuid()) and not s.st_mode&0o022 and s.st_size<=4194304
 raw=f.read(4194305); after=os.fstat(f.fileno()); current=p.lstat()
 key=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
 assert key(s)==key(after)==key(current) and len(raw)==s.st_size
assert hashlib.sha256(raw).hexdigest()==expected
sys.argv=[path,*sys.argv[3:]]
exec(compile(raw,path,'exec'),{'__name__':'__main__','__file__':path,'_VERIFIED_ENTRY_SHA256':expected})
"""
# Fingerprints from raw spark01.json session_files, NOT normalized ZIP modes.
OLD_FILES = {'capsule.json': {'bytes': 12662,
                  'mode': '0o600',
                  'sha256': '2a01a8dc02ea1673a0bf7b460c8d90761ba5a6a6c86ac9805720b069ab1d7586',
                  'uid': 1000},
 'capsule.sha256': {'bytes': 65,
                    'mode': '0o664',
                    'sha256': '6b927cb5109054c9621e04515715c4b45b8f3d13fb8857315a1b3568f0068079',
                    'uid': 1000},
 'command-227020721ba64070ab8486a826968f6b.log': {'bytes': 5632,
                                                  'mode': '0o664',
                                                  'sha256': '791cb09cfc28e504d72c4bc46263a31a0dd37489a6f84dfd5e7c71e3d8575ce8',
                                                  'uid': 1000},
 'command-a9fc92d6b8614c2c8b6f8e763d9ac979.log': {'bytes': 514,
                                                  'mode': '0o664',
                                                  'sha256': '4c208d3f3deec7ee7e3b499506bdd9c473f7ad00a7a24132afb11247ae9bee9e',
                                                  'uid': 1000},
 'preflight-before_hold.json': {'bytes': 3887,
                                'mode': '0o600',
                                'sha256': '473a685ca259e7ab1ef6ce576d4605080d4f969c1d169cd91f4e6feb07945589',
                                'uid': 1000},
 'preflight-before_stop-626550204bf24b5799dfb2152510ba2f.json': {'bytes': 4327,
                                                                 'mode': '0o600',
                                                                 'sha256': 'c2ad29bbed4ec77e65aee991120591c25b4356b31b3bced0e7972cdcde11c442',
                                                                 'uid': 1000},
 'preflight-stopped-a7aa61d5c02e4e26901a0e1f7cb76e61.json': {'bytes': 4323,
                                                             'mode': '0o600',
                                                             'sha256': 'a181a48d31393f02eb753fcad6d84de7ba6243b77c3de89c5bd5ad98e1beeddc',
                                                             'uid': 1000},
 'restore-evidence/remote-operation-0ea05555b75c49efb9cf43bc43b4be76.json': {'bytes': 242,
                                                                             'mode': '0o664',
                                                                             'sha256': 'df284ce140fb6eab9c3e0aa35a209c05cd5bfd7fc340710c9202dfca2a2083ba',
                                                                             'uid': 1000},
 'restore-evidence/remote-operation-0fd2614557f44d088d1985cbcbd0c5d3.json': {'bytes': 242,
                                                                             'mode': '0o664',
                                                                             'sha256': 'f6f8355477d3519c8f4a934513e89e41936a9cb4940a6e9c72691720e172fa80',
                                                                             'uid': 1000},
 'restore-evidence/remote-operation-130a60cb63a64bf288cea5a1a8580168.json': {'bytes': 242,
                                                                             'mode': '0o664',
                                                                             'sha256': 'aca93d72b5c8e22455f918b45ed86cac9152b0a38cd66fefc45fb4de1dd37441',
                                                                             'uid': 1000},
 'restore-evidence/remote-operation-1d9ef92905bb46118fa379da855960df.json': {'bytes': 242,
                                                                             'mode': '0o664',
                                                                             'sha256': 'd3aee6d7ccdae32158573584b1126a24d14ce64b3d0ea137ca3fdd63a35fea55',
                                                                             'uid': 1000},
 'restore-evidence/remote-operation-217ec52baab4486e8bcd72990c4e3e84.json': {'bytes': 242,
                                                                             'mode': '0o664',
                                                                             'sha256': '56f288ba53e2c76337ab5c70e8ec87018f14033b8434b296416bb0d41455a0da',
                                                                             'uid': 1000},
 'restore-evidence/remote-operation-57ee5c9a0f884916875b8498d5cac6ba.json': {'bytes': 242,
                                                                             'mode': '0o664',
                                                                             'sha256': '0bef6a6320677d92bc2ff47ec9c3fe75d5de86798a2a442b6c03662d75a8e7ed',
                                                                             'uid': 1000},
 'restore-evidence/remote-operation-997ec873c0ec4939bfec7508af2a94f5.json': {'bytes': 242,
                                                                             'mode': '0o664',
                                                                             'sha256': '4e16b85f7fea8af5ccb4352058c3ef91ace558397d35bc8b9eeab58261cdffdf',
                                                                             'uid': 1000},
 'restore-evidence/remote-operation-d4460a77f59b4df0b188d4c2b8a83658.json': {'bytes': 242,
                                                                             'mode': '0o664',
                                                                             'sha256': 'b77e654e0c3c1184d85366363e09183c3804443424ee68c598203e3a46bcf8e4',
                                                                             'uid': 1000},
 'session.lock': {'bytes': 0,
                  'mode': '0o664',
                  'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
                  'uid': 1000},
 'state.json': {'bytes': 3153,
                'mode': '0o600',
                'sha256': '6d4cd48d363467729719f9d97f62f8e05550b3476fef5455d4df649549083167',
                'uid': 1000},
 'watchdog-journal.jsonl': {'bytes': 591,
                            'mode': '0o664',
                            'sha256': '228f2cb95d085a8bd6f494455b52826237e8ca074e96341c8e1694deff98b94d',
                            'uid': 1000},
 'watchdog-recovery-attempt.json': {'bytes': 93,
                                    'mode': '0o664',
                                    'sha256': '8661733f6e19dc8cab21386b47fdd9a2217dc4f0ba87b90fc3668b0dfe8c7896',
                                    'uid': 1000},
 'watchdog-recovery.log': {'bytes': 1645,
                           'mode': '0o664',
                           'sha256': '6ab0d0d393276d73d05c59c07309717e9d7607b9478231d2f1a1b03a6b461989',
                           'uid': 1000},
 'watchdog.lock': {'bytes': 0,
                   'mode': '0o664',
                   'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
                   'uid': 1000},
 'watchdog.log': {'bytes': 0,
                  'mode': '0o664',
                  'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
                  'uid': 1000},
 'watchdog.ready.json': {'bytes': 191,
                         'mode': '0o664',
                         'sha256': '4e8591cb00f44f66105ee33c48cad158661719e839e47928665df095fd5e0dc6',
                         'uid': 1000}}
HOLD_INVARIANTS_SHA256 = "2d4c4aaf4640085038dafc89c37f982201a06dd1190a2858d53b40fad932ced3"
INITIAL_HEARTBEAT_SHA256 = "76a708de1bdbc795f0922dfb249fec232fa8fcb34f4ab22b9437268a4a20d122"


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def decode(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'Duplicate JSON key')
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique)
    require(isinstance(value, dict), 'Expected JSON object')
    return value


def canonical(path):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts and path.resolve() == path,
            'Path must be canonical without aliases')
    return path


def witness_metadata(path, row):
    info = path.lstat()
    wanted = row['lstat']
    require(path == canonical(row['path']) and row['resolved_path'] == str(path)
            and row['exists'] is True, 'Witness path identity differs')
    observed_type = ('directory' if stat.S_ISDIR(info.st_mode)
                     else 'regular' if stat.S_ISREG(info.st_mode) else 'other')
    require(observed_type == wanted['type'] and stat.S_IMODE(info.st_mode) == int(wanted['mode'], 8)
            and (info.st_uid, info.st_gid, info.st_dev, info.st_ino) ==
                (wanted['uid'], wanted['gid'], wanted['dev'], wanted['ino']),
            'Witnessed live metadata changed: ' + str(path))
    # The parent directory gains the one new journal. Every historical leaf
    # remains single-link and the original session/subdirectory unchanged.
    if row['role'] != 'session_parent':
        require(info.st_nlink == wanted['nlink'] and info.st_size == wanted['size'],
                'Witnessed link count/size changed')
    if row['role'] not in ('session_parent', 'head_hold'):
        require(info.st_mtime_ns == wanted['mtime_ns'] and info.st_ctime_ns == wanted['ctime_ns'],
                'Historical witnessed timestamps changed')
    return info


def ancestry(path):
    for parent in (path, *path.parents):
        info = parent.lstat()
        exception = (_WITNESS is not None and
                     str(parent) == _WITNESS['roles']['restore_evidence']['path'])
        if exception:
            row = _WITNESS['roles']['restore_evidence']
            witness_metadata(parent, row)
            session_row = _WITNESS['roles']['session']
            enclosing = witness_metadata(parent.parent, session_row)
            require(int(row['lstat']['mode'], 8) == 0o775 and info.st_uid == os.getuid()
                    and stat.S_IMODE(enclosing.st_mode) == 0o700
                    and enclosing.st_uid == os.getuid(), 'Unsafe historical directory exception')
        else:
            require(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, os.getuid())
                    and not info.st_mode & 0o022, 'Untrusted directory ancestry: ' + str(parent))


def load_witness(path, directory):
    global _WITNESS
    raw = read(path)
    require(digest(raw) == WITNESS_SHA256, 'Private metadata witness hash differs')
    value = decode(raw)
    require(value['window_id'] == WINDOW and value['schema_version'] == 1,
            'Private witness window/schema differs')
    rows = value['records']
    roles = {row['role']: row for row in rows}
    require(len(rows) == len(roles) == 16 and len({row['path'] for row in rows}) == 16,
            'Private witness inventory differs')
    directory = canonical(directory)
    require(roles['session']['path'] == str(directory)
            and roles['session_parent']['path'] == str(directory.parent)
            and roles['restore_evidence']['path'] == str(directory / 'restore-evidence')
            and roles['session']['lstat']['mode'] == '0700'
            and roles['restore_evidence']['lstat']['mode'] == '0775',
            'Private witness directory scope differs')
    _WITNESS = {'path': str(canonical(path)), 'roles': roles, 'records': rows}
    verify_witness()


def verify_witness():
    require(_WITNESS is not None, 'Exact private metadata witness is required')
    require(digest(read(_WITNESS['path'])) == WITNESS_SHA256, 'Private witness changed')
    for row in _WITNESS['records']:
        witness_metadata(Path(row['path']), row)



def read(path, *, fingerprint=None):
    path = canonical(path)
    ancestry(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                and before.st_uid in (0, os.getuid()) and before.st_size <= MAX_BYTES,
                'Untrusted regular single-link file: ' + str(path))
        if fingerprint is None:
            require(not before.st_mode & 0o022, 'Unsafe file mode: ' + str(path))
        else:
            require(before.st_uid == fingerprint['uid'] == os.getuid()
                    and stat.S_IMODE(before.st_mode) == int(fingerprint['mode'], 8)
                    and before.st_size == fingerprint['bytes'], 'Historical metadata drift: ' + str(path))
        raw = os.read(fd, MAX_BYTES + 1)
        after, current = os.fstat(fd), path.lstat()
        key = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid,
                         s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        require(key(before) == key(after) == key(current) and len(raw) == before.st_size,
                'File changed during read: ' + str(path))
        if fingerprint is not None:
            require(digest(raw) == fingerprint['sha256'], 'Historical bytes drift: ' + str(path))
        return raw
    finally:
        os.close(fd)


def process_state(record):
    require(isinstance(record, dict) and type(record.get('pid')) is int and record['pid'] > 0
            and isinstance(record.get('start_ticks'), str) and record['start_ticks'].isdigit(),
            'Invalid recorded process identity')
    try:
        fields = Path(f"/proc/{record['pid']}/stat").read_text().rsplit(')', 1)[1].split()
    except (FileNotFoundError, ProcessLookupError):
        return 'dead'
    # Permission, malformed and IO errors are UNKNOWN, not proof of death.
    require(len(fields) > 19 and fields[19].isdigit(), 'Unknown process status')
    return 'dead' if fields[0] == 'Z' or fields[19] != record['start_ticks'] else 'alive'


def verify_terminal(capsule, ready):
    require(ready['window_id'] == WINDOW and ready['capsule_sha256'] == CAPSULE_SHA256,
            'Watchdog binding differs')
    require(process_state(capsule['core_process']) == 'dead', 'Old core is alive')
    require(process_state(ready['process']) == 'dead', 'Old watchdog is alive')


def verify_old(directory):
    verify_witness()
    directory = canonical(directory)
    ancestry(directory)
    observed, directories = {}, set()
    for path in directory.rglob('*'):
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            directories.add(str(path.relative_to(directory)))
            ancestry(path)
        else:
            observed[str(path.relative_to(directory))] = path
    require(directories == {'attempts', 'controls', 'plans', 'restore-evidence'},
            'Historical directory inventory changed')
    require(set(observed) == set(OLD_FILES), 'Historical session inventory changed')
    raw = {name: read(observed[name], fingerprint=fp) for name, fp in OLD_FILES.items()}
    require(digest(raw['capsule.json']) == CAPSULE_SHA256
            and raw['capsule.sha256'] == (CAPSULE_SHA256 + '\n').encode(), 'Exact capsule pin differs')
    capsule, state, ready = (decode(raw[name]) for name in
                              ('capsule.json', 'state.json', 'watchdog.ready.json'))
    pre = capsule['manifest']['preflight']
    require(capsule['window_id'] == WINDOW and capsule['holder'] == 'q03-isolated:' + WINDOW
            and capsule['session_dir'] == str(directory)
            and pre['window_id'] == WINDOW and pre['holder'] == capsule['holder']
            and pre['original_service_disposition'] == 'keep-stopped'
            and capsule['hold_path'] == pre['hold_path']
            and capsule['policy_path'] == pre['policy_path']
            and capsule['policy_sha256'] == pre['policy_sha256']
            and pre['publication']['sha'] == OLD_HEAD, 'Exact recovery authority differs')
    require(state['phase'] == 'recovery-failed' and state['mutation_started'] is True
            and state['held_sequence'] == 0 and state['active_attempt'] is None
            and state['active_process'] is None and state['restored'] is False
            and state['transition_epochs'] == [{'action': 'stop', 'epoch': OLD_EPOCH}]
            and not any(state[k] for k in ('held_candidates', 'candidate_containers',
                      'attempt_results', 'attempt_receipt_sha256', 'job_results')),
            'Only exact pre-attempt STOP failure is admitted')
    checkout = canonical(pre['independent_checkout'])
    sources = {}
    for name, expected in capsule['recovery_source_sha256'].items():
        path = canonical(checkout / 'bin/spark' if name == '../bin/spark'
                         else checkout / 'scripts' / name)
        require(path.is_relative_to(checkout), 'Pinned source escapes checkout')
        value = read(path)
        require(digest(value) == expected, 'Pinned old source changed: ' + name)
        sources[name] = value
    require(capsule['recovery_script_sha256'] == digest(sources['q03_window.py']),
            'Old core source differs')
    verify_terminal(capsule, ready)
    return capsule, state, ready, sources


def hold_invariant_digest(value):
    return digest(json.dumps({k: v for k, v in value.items() if k != 'heartbeat'},
                             sort_keys=True, separators=(',', ':')).encode())


def verify_hold(capsule, *, identity=None, initial=False):
    path = canonical(capsule['hold_path'])
    raw = read(path)
    value = decode(raw)
    if _WITNESS is not None:
        hold_row = _WITNESS['roles']['head_hold']
        witness_metadata(path, hold_row)
        if initial:
            require(digest(raw) == hold_row['sha256'], 'Original hold bytes differ from witness')
    require(hold_invariant_digest(value) == HOLD_INVARIANTS_SHA256,
            'Owned hold invariant fields changed')
    from datetime import datetime
    heartbeat = datetime.fromisoformat(value['heartbeat'])
    require(heartbeat.tzinfo is not None, 'Invalid hold heartbeat')
    if initial:
        require(digest(value['heartbeat'].encode()) == INITIAL_HEARTBEAT_SHA256,
                'Historical hold heartbeat unexpectedly changed')
    info = path.lstat()
    current = (info.st_dev, info.st_ino)
    require(identity is None or current == identity, 'Hold inode changed')
    return current


def load_modules(capsule, sources):
    # Same-read verified source only. Never import mutable pycache or replace
    # sources on disk; no general read_owned_bytes override exists here.
    modules = {}
    checkout = Path(capsule['manifest']['preflight']['independent_checkout'])
    for name in ('q03_window_watchdog', 'q03_window_preflight', 'q03_window',
                 'q03_original_spark_adapter'):
        module = types.ModuleType(name)
        module.__file__ = str(checkout / 'scripts' / (name + '.py'))
        modules[name] = module
        sys.modules[name] = module
        exec(compile(sources[name + '.py'], module.__file__, 'exec'), module.__dict__)
    return modules


def private_json(path, value, *, exclusive=False):
    path = Path(path)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(value, out, sort_keys=True)
            out.flush()
            os.fsync(out.fileno())
        if exclusive:
            os.link(temporary, path, follow_symlinks=False)
        else:
            os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def lock_existing(stack, path, fingerprint=None):
    read(path, fingerprint=fingerprint)
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    stream = stack.enter_context(os.fdopen(fd, 'r+'))
    info, current = os.fstat(fd), Path(path).lstat()
    require((info.st_dev, info.st_ino) == (current.st_dev, current.st_ino), 'Lock path changed')
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def verify_seal(report, epoch, nodes, expected_unit, expected_operations=None):
    require(report.get('window_id') == WINDOW and report.get('epoch') == epoch
            and report.get('safe_to_overlap') is True, 'Epoch seal identity/terminal proof differs')
    rows = report.get('sealed_epochs')
    require(isinstance(rows, list) and len(rows) == 4
            and {r.get('node') for r in rows} == set(nodes)
            and all(r.get('window') == WINDOW and r.get('epoch') == epoch
                    and r.get('sealed') is True and r.get('safe_to_overlap') is True
                    and isinstance(r.get('operations'), list)
                    and all(op.get('safe_to_overlap') is True
                            and op.get('window') == WINDOW and op.get('status') == 'terminal'
                            and op.get('epoch_sealed') is True and op.get('group_quiescent') is True
                            and op.get('timed_out') is False and op.get('cancelled') is False
                            and (op.get('action_succeeded') is True or
                                 (op.get('lifecycle_acknowledged') is True
                                  and op.get('exit_code') == 5
                                  and op.get('lifecycle_unit') == expected_unit))
                            for op in r['operations'])
                    for r in rows), 'Missing positive all-four sealed epoch proof')
    if expected_operations is not None:
        require(all({op.get('operation') for op in row['operations']} ==
                    set(expected_operations.get(row['node'], []))
                    and len(row['operations']) == len(expected_operations.get(row['node'], []))
                    for row in rows), 'Historical registered operation inventory changed')


def validate_destination(destination, capsule):
    destination = canonical(destination)
    root = canonical(capsule['manifest']['independent_root'])
    old = Path(capsule['session_dir'])
    require(destination != root and destination.is_relative_to(root)
            and not destination.is_relative_to(old) and not old.is_relative_to(destination),
            'New journal overlaps old session or escapes independent root')
    require(destination == root / 'sessions' / ('recovery-' + WINDOW + '-neverstart'),
            'Exact window requires its one fixed recovery journal')
    for value in capsule['manifest']['preflight']['protected_roots']:
        p = Path(value)
        require(not destination.is_relative_to(p) and not p.is_relative_to(destination),
                'Journal overlaps protected namespace')
    ancestry(destination.parent)
    return destination


def adapter_step(directory, destination, action, epoch, witness):
    require(action in ('stop', 'quiesce'), 'NEVERSTART: unsupported adapter action')
    load_witness(witness, directory)
    os.umask(0o077)
    os.environ['GIT_OPTIONAL_LOCKS'] = '0'
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    capsule, _, _, sources = verify_old(directory)
    destination = validate_destination(destination, capsule)
    intent = decode(read(destination / 'intent.json'))
    state = decode(read(destination / 'state.json'))
    require(intent['entry_sha256'] == globals().get('_VERIFIED_ENTRY_SHA256')
            == digest(read(Path(__file__).resolve()))
            and intent['window_id'] == WINDOW and intent['old_capsule_sha256'] == CAPSULE_SHA256
            and intent['journal_dir'] == str(destination)
            and intent['private_witness_path'] == _WITNESS['path']
            and intent['private_witness_sha256'] == WITNESS_SHA256, 'Recovery child intent differs')
    parent = state['active_process']
    require(process_state(intent['recovery_process']) == 'alive'
            and parent['pid'] == os.getppid() and process_state(parent) == 'alive'
            and os.getpgid(0) == parent['pid'], 'Adapter is not the journalled supervised child')
    require(epoch in state['transition_epochs'] and
            (action == 'quiesce' or epoch != OLD_EPOCH), 'Unregistered transition epoch')
    verify_hold(capsule, identity=tuple(intent['hold_identity']))
    modules = load_modules(capsule, sources)
    adapter = modules['q03_original_spark_adapter']
    evidence = destination / 'restore-evidence'
    # Execute only same-read pinned source bytes, avoiding SourceFileLoader and
    # its second read/pycache route. Preserve the registered source's ROOT.
    pre = capsule['manifest']['preflight']
    policy_raw = read(pre['policy_path'])
    require(digest(policy_raw) == pre['policy_sha256'], 'Registered policy bytes differ')
    policy = decode(policy_raw)
    require(all(policy.get(k) == v for k, v in pre['registered_policy'].items()),
            'Registered policy identity differs')
    repository = canonical(policy['repository'])
    require(digest(read(repository / policy['cluster_config'])) == policy['config_sha256'],
            'Registered profile changed')
    runtime_helpers = ('topology', 'mesh_fabric', 'kernel_backend', 'kernel_readiness',
                       'transport_profiles', 'model_layout', 'recipe')
    for name in runtime_helpers:
        raw = read(repository / 'scripts' / (name + '.py'))
        require(digest(raw) == digest(sources[name + '.py']), 'Registered helper changed: ' + name)
        module = types.ModuleType(name)
        module.__file__ = str(repository / 'scripts' / (name + '.py'))
        sys.modules[name] = module
        exec(compile(raw, module.__file__, 'exec'), module.__dict__)
    source = read(repository / 'bin/spark')
    require(digest(source) == pre['spark_sha256'] == digest(sources['../bin/spark']),
            'Registered spark source changed')
    original = types.ModuleType('_q03_exact_recovery_spark')
    original.__file__ = str(repository / 'bin/spark')
    sys.modules[original.__name__] = original
    before_path = list(sys.path)
    try:
        exec(compile(source, original.__file__, 'exec'), original.__dict__)
    finally:
        sys.path[:] = before_path
    verify_hold(capsule, identity=tuple(intent['hold_identity']))
    require(digest(read(pre['policy_path'])) == pre['policy_sha256'], 'Policy changed after import')
    parsed = original.parser().parse_args(adapter.transition_args(policy, 'stop', True))
    cluster, nodes, _ = original.configuration(parsed)
    require(len(nodes['nodes']) == 4
            and {n['name'] for n in nodes['nodes']} == {n['name'] for n in pre['nodes']},
            'Registered all-four node identities differ')
    expected_nodes = {node['name']: node for node in pre['nodes']}
    require({node['rank'] for node in nodes['nodes']} == {0, 1, 2, 3}
            and all(node['rank'] + 1 == expected_nodes[node['name']]['rank']
                    and original.ssh_target(nodes, node) == expected_nodes[node['name']]['ssh_host']
                    for node in nodes['nodes']), 'Registered node target/rank binding differs')
    # Label each seal result at its actual configured transport target, not by
    # trusting a remotely supplied label or a four-element aggregate.
    class NodeBoundRemote(adapter.RemoteOperations):
        def helper_call(self, node, operation, *args, **kwargs):
            result = super().helper_call(node, operation, *args, **kwargs)
            if operation == 'seal':
                require(result.get('node', node['name']) == node['name'], 'Seal node mismatch')
                result = dict(result, node=node['name'])
            return result
    helper = types.ModuleType('_q03_exact_remote_helper')
    helper.__file__ = str(Path(adapter.__file__).with_name('q03_remote_operation.py'))
    sys.modules[helper.__name__] = helper
    exec(compile(sources['q03_remote_operation.py'], helper.__file__, 'exec'), helper.__dict__)
    adapter.load_remote_helper = lambda expected: helper if expected == digest(
        sources['q03_remote_operation.py']) else require(False, 'Remote helper pin differs')
    remote = NodeBoundRemote(original, cluster, nodes, capsule, WINDOW, epoch, evidence)
    remote.verify_helpers()
    if action == 'quiesce':
        print(json.dumps({'window_id': WINDOW, 'epoch': epoch,
                          'sealed_epochs': remote.quiesce(), 'safe_to_overlap': True}))
        return 0
    adapter.install_readonly_hooks(original, evidence, remote.run_ssh)
    result = parsed.func(parsed)
    return 0 if result is None else result



def run_protocol(session, barrier):
    """Small injectable sequence; no START branch and no fallback release."""
    barrier()
    session.seal(OLD_EPOCH)
    session.check('before_restore')
    barrier()
    epoch = uuid.uuid4().hex
    session.update(transition_epochs=[OLD_EPOCH, epoch])  # Durable BEFORE STOP.
    try:
        session.command('stop', epoch, 210)
    finally:
        # Even failed/timed-out STOP must fence any accepted remote launches.
        # A successful seal does NOT turn the failed STOP into success.
        session.seal(epoch)
    session.check('restore_drained')
    session.check_window_absence()
    barrier()
    session.update(phase='closed-stopped', original_kept_stopped=True, restored=False,
                   positive_all_four_drain=True)
    session.release()
    session.update(hold_released=True)
    return session.state


def failure_fields(state, hold_path, identity, error):
    if state.get('positive_all_four_drain') is not True:
        return dict(phase='recovery-failed', failure=str(error), outcome='unknown',
                    hold_released=False, restored=False, hold_retention='unverified')
    # Positive closure is durable before release. A failed fsync/final journal
    # write must not recast an already removed hold as retained.
    try:
        info = Path(hold_path).lstat()
        absent = False
        same = (info.st_dev, info.st_ino) == identity
    except FileNotFoundError:
        absent, same = True, False
    except OSError:
        absent, same = None, False
    released = (True if state.get('hold_released') is True and absent is True
                else False if same else None)
    return dict(phase='closed-stopped', failure=str(error),
                outcome='release-outcome-uncertain', hold_released=released,
                hold_absence_observed=absent, restored=False)


def recover(directory, destination, *, apply=False):
    capsule, historical, ready, sources = verify_old(directory)
    destination = validate_destination(destination, capsule)
    require(not destination.exists(), 'Recovery requires a fresh journal; no automatic resume')
    hold_identity = verify_hold(capsule, initial=True)
    if not apply:
        return {'status': 'plan-only', 'window_id': WINDOW, 'execution_enabled': False,
                'old_capsule_sha256': CAPSULE_SHA256, 'scope': 'exact-pre-attempt-NEVERSTART',
                'journal_dir': str(destination), 'live_drain_established': False}
    os.umask(0o077)  # Only newly created recovery outputs; never chmod old assets.
    modules = load_modules(capsule, sources)
    old, watchdog = modules['q03_window'], modules['q03_window_watchdog']
    ops = old.RuntimeOps()
    entry_sha = digest(read(Path(__file__).resolve()))
    with contextlib.ExitStack() as locks:
        for name in ('session.lock', 'watchdog.lock'):
            lock_existing(locks, Path(directory) / name, OLD_FILES[name])
        # Existing lock is checked before the unchanged GPU-lock acquisition.
        gpu_lock = Path('/tmp/spark-lab-gpu.lock')
        require(gpu_lock.exists(), 'Expected shared GPU lock missing')
        info = gpu_lock.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                and info.st_uid in (0, os.getuid()) and not info.st_mode & 0o022,
                'Shared GPU lock metadata differs')
        gpu = locks.enter_context(ops.acquire_gpu_lock())
        require((os.fstat(gpu.fileno()).st_dev, os.fstat(gpu.fileno()).st_ino)
                == (info.st_dev, info.st_ino), 'Shared GPU lock replaced')
        verify_old(directory)
        verify_hold(capsule, identity=hold_identity, initial=True)
        destination.mkdir(mode=0o700)
        fd = os.open(destination / 'reconcile.lock', os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        stream = locks.enter_context(os.fdopen(fd, 'r+'))
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        identity = old.process_identity()
        require(process_state(identity) == 'alive', 'Current recovery identity unknown')
        intent = {'window_id': WINDOW, 'old_capsule_sha256': CAPSULE_SHA256,
                  'old_session': str(directory), 'journal_dir': str(destination),
                  'entry_sha256': entry_sha, 'recovery_process': identity,
                  'private_witness_path': _WITNESS['path'], 'private_witness_sha256': WITNESS_SHA256,
                  'hold_identity': list(hold_identity), 'never_start': True,
                  'deadline_epoch': time.time() + capsule['recovery_timeout_s']}
        private_json(destination / 'intent.json', intent, exclusive=True)
        class RecoverySession:
            def __init__(self):
                self.directory, self.ops, self.capsule = destination, ops, capsule
                self.manifest, self.hold = capsule['manifest'], Path(capsule['hold_path'])
                self.state = {'phase': 'restoring', 'restored': False, 'hold_released': False,
                    'transition_epochs': [OLD_EPOCH], 'recovery_process': identity,
                    'restoring_started_epoch': intent['deadline_epoch'] - capsule['recovery_timeout_s']}
                self.update()
            def update(self, **fields):
                self.state.update(fields)
                private_json(destination / 'state.json', self.state)
            def beat(self):
                verify_hold(capsule, identity=hold_identity)
                require(time.time() < intent['deadline_epoch'], 'Bounded recovery deadline reached')
                watchdog.refresh_hold(self.hold, WINDOW, capsule['holder'])
            def check(self, phase):
                result = ops.check(phase, self.manifest)
                private_json(destination / (phase + '-' + uuid.uuid4().hex + '.json'),
                             json.loads(json.dumps(result, default=str)), exclusive=True)
            def command(self, action, epoch, timeout):
                require(action in ('stop', 'quiesce'), 'NEVERSTART: unsupported operation')
                barrier()
                argv = [sys.executable, '-I', '-B', '-c', ENTRY_BOOTSTRAP,
                    str(Path(__file__).resolve()), entry_sha,
                    '--old-session', str(directory), '--journal-dir', str(destination),
                    '--adapter-step', action, '--epoch', epoch,
                    '--live-witness', _WITNESS['path']]
                return ops.execute(self, argv, cwd=str(destination), timeout=timeout)
            def seal(self, epoch):
                result = self.command('quiesce', epoch, 360)
                report = decode(read(result['log_path']))
                expected_operations = None
                if epoch == OLD_EPOCH:
                    expected_operations = {}
                    for name, fingerprint in OLD_FILES.items():
                        if name.startswith('restore-evidence/remote-operation-'):
                            record = decode(read(Path(directory) / name, fingerprint=fingerprint))
                            require(record['window'] == WINDOW and record['epoch'] == OLD_EPOCH,
                                    'Historical operation authority differs')
                            expected_operations.setdefault(record['node'], []).append(record['operation'])
                verify_seal(report, epoch, [n['name'] for n in self.manifest['preflight']['nodes']],
                            self.manifest['preflight']['registered_policy']['container'] + '-memguard.service',
                            expected_operations=expected_operations)
                private_json(destination / ('seal-' + epoch + '.json'),
                             {'receipt': report, 'log_sha256': digest(read(result['log_path']))}, exclusive=True)
            def check_window_absence(self):
                preflight = modules['q03_window_preflight']
                rows = []
                spec = self.manifest['preflight']
                for node in spec['nodes']:
                    argv = ['sudo', '-n', 'docker', '--host', 'unix:///var/run/docker.sock',
                            'ps', '-a', '--no-trunc', '--filter', 'label=q03.window=' + WINDOW,
                            '--format', '{{json .}}']
                    result = ops.run(preflight._node_argv(spec, node, argv), timeout=30, check=False)
                    require(result.returncode == 0 and result.stdout.strip() == ''
                            and result.stderr == '', 'Window container absence unknown: ' + node['name'])
                    rows.append({'node': node['name'], 'window': WINDOW, 'containers_absent': True})
                private_json(destination / 'window-container-absence.json', {'nodes': rows}, exclusive=True)
            def release(self):
                # Stop the live heartbeat before the final same-inode ownership
                # transaction. A prior error cannot unlock the shared hold.
                stop_heartbeat.set()
                heartbeat_thread.join(timeout=5)
                require(not heartbeat_thread.is_alive() and not heartbeat_errors,
                        'Recovery heartbeat failed or did not stop')
                barrier()
                with watchdog._owned_fd(self.hold, WINDOW, capsule['holder'],
                                        writable=True, unlinking=True) as (fd, record, raw):
                    require(hold_invariant_digest(record) == HOLD_INVARIANTS_SHA256
                            and (os.fstat(fd).st_dev, os.fstat(fd).st_ino) == hold_identity,
                            'Hold changed before proven closure release')
                    private_json(destination / 'release-intent.json', {
                        'window_id': WINDOW, 'old_capsule_sha256': CAPSULE_SHA256,
                        'hold_identity': list(hold_identity), 'phase': 'closed-stopped',
                        'positive_all_four_drain': True, 'restored': False}, exclusive=True)
                    watchdog._same_file(self.hold, fd)
                    os.unlink(self.hold)
                    self.state['hold_released'] = True
                    parent_fd = os.open(self.hold.parent, os.O_DIRECTORY)
                    try:
                        os.fsync(parent_fd)
                    finally:
                        os.close(parent_fd)
        session = RecoverySession()
        stop_heartbeat, heartbeat_errors = threading.Event(), []
        def barrier():
            require(not heartbeat_errors, 'Active recovery heartbeat failed')
            require(time.time() < intent['deadline_epoch'], 'Recovery deadline expired')
            require(digest(read(Path(__file__).resolve())) == entry_sha, 'Recovery source changed')
            verify_old(directory)
            verify_hold(capsule, identity=hold_identity)
        def heartbeat():
            while not stop_heartbeat.wait(2):
                try:
                    session.beat()
                except BaseException as error:
                    heartbeat_errors.append(str(error))
                    return
        heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        session.beat()
        heartbeat_thread.start()
        try:
            return run_protocol(session, barrier)
        except BaseException as error:
            session.update(**failure_fields(session.state, capsule['hold_path'],
                                            hold_identity, error))
            raise
        finally:
            stop_heartbeat.set()
            heartbeat_thread.join(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old-session', required=True)
    parser.add_argument('--journal-dir', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--live-witness', required=True)
    parser.add_argument('--adapter-step', choices=('stop', 'quiesce'), help=argparse.SUPPRESS)
    parser.add_argument('--epoch', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.adapter_step is not None:
        require(not args.apply and re.fullmatch(r'[0-9a-f]{32}', args.epoch or ''),
                'Invalid internal adapter arguments')
        return adapter_step(args.old_session, args.journal_dir, args.adapter_step, args.epoch, args.live_witness)
    require(args.epoch is None, 'Epoch is internal-only')
    load_witness(args.live_witness, args.old_session)
    print(json.dumps(recover(args.old_session, args.journal_dir, apply=args.apply), sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    raise SystemExit(main())
