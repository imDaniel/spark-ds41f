#!/usr/bin/env python3
"""Independent, stop-only recovery of the exact frozen HTTP log-permission failure.

Old source, capsule, journal, helper and operation receipts are never rewritten.
Only a separately authorized bounded recovery intent permits new helper epochs.
Import and the default CLI are inert; --apply is an explicit site operation.
"""
from __future__ import annotations
import argparse
import base64
import contextlib
import copy
import fcntl
import hashlib
import json
import importlib.abc
import importlib.util
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
import time
import types
import uuid
import zlib

WINDOW = '4f58632f54964f109c3457fa17c57872'
CAPSULE_SHA = '738b66237ae65f4148b423af002b2b1f60c82b7de6bf986ffcd4408d598f844e'
ATTEMPT = 'beeba65b41c44f95ae23b049f70f2259'
OLD_SOURCE = '3a49389f7ba0e93f05bab1c9a52b4425d4dd7fde'
OLD_HELPER = '1ddb0c79d6e50fd71245cff1c53c32fe9bf862e1a57f4078e6fed1ecafd7484a'
NEW_HELPER = '7271c816f06c6f1bb600649b31c7e74941a075a502d958d0bf945e2b38ebd36e'
OLD_AUTHORITY = '3bafc8ef6d6898d764e3ff29cacf48c811788e759e023ac1475d5f589b6a8579'
EVIDENCE_SHA = 'ee93056f270cdc5286871eef02286919650773c3842df30808f6eb6bedf99e75'
HELPER_PUBLICATION = dict(repository='https://github.com/imDaniel/spark-ds41f.git',
    ref='refs/heads/fix/q03-private-log-creation-20261021',
    sha='40c4f68ccb1a13b251ae31d67ebc8bab5c944d11')
PURPOSE = 'recover-frozen-http-logs-drain-keep-originals-stopped'
ACTIONS = ['capture_exact_id_logs', 'stop_owned_candidates', 'stop_owned_guards',
           'remove_proven_candidates', 'verify_protection_and_gpu_drain', 'release_owned_hold']
PREFIX = 'attempts/' + ATTEMPT + '/'
OLD_FILES = {
    'state.json': 'c164ba7dac7ebee7ec2995dc4fcca6637f25c401785dca7fe8c01c53527dcaa1',
    'watchdog.ready.json': 'a9f873950e6b317d86aa62313d045087ac8ba200be42ba58b2e25628d55a5ada',
    PREFIX + 'descriptor.json': '8ab4a3b70908b5924a8b68a9178630ba461b33d08cff5f7c33fec04321f503ad',
    PREFIX + 'admitted.json': '1efee0182427ff515f2df7e2fad959354c5343a5ed50b4fdb74fcb8cc8dcff40',
    PREFIX + 'http/plan.json': 'eaf28265518cdf7642c03f739af7dc76e4d00792059bb857c1e90913275cc4f7',
    PREFIX + 'http/state.json': '30ce8a1db4d577b2d9f496c266ec8e806d788191743068022b5957c2796d71e9',
    PREFIX + 'http/client-plan.json': 'eaf28265518cdf7642c03f739af7dc76e4d00792059bb857c1e90913275cc4f7',
}
CONTAINERS = (
    '3679497c8414b571445c9c261a2eeffe1efd28e3379ffef9dfa209255624bc61',
    'e89d0714caa0082290cb36c2c93c05e32b2d3c48f748ef05360107f92a0fe665',
    '1fef561790f7014ba79724f860e354cdd0610954dd5d9d0766e6a5e589894019',
    '9a98cb017932edf9db8192fcfd55743b73f2065743b9043af23d35eb45901251',
)
MAX_BYTES = 8 * 1024 * 1024
_CHILDREN = []  # Retain/reap children instead of discarding a live Popen object.


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def path(value):
    p = Path(value)
    require(p.is_absolute() and '..' not in p.parts and p.resolve() == p,
            'absolute canonical path required')
    return p


def read(p, expected=None):
    p = path(p)
    fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_uid in (0, os.geteuid())
                and not before.st_mode & 0o022 and before.st_nlink == 1
                and before.st_size <= MAX_BYTES, 'unsafe bounded evidence file: ' + str(p))
        raw = stream.read(MAX_BYTES + 1)
        after, current = os.fstat(stream.fileno()), p.lstat()
    key = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    require(key(before) == key(after) == key(current) and len(raw) == before.st_size,
            'evidence changed during read')
    require(expected is None or digest(raw) == expected, 'pinned bytes changed: ' + str(p))
    return raw


def snapshot(p):
    """Read one complete owned inode while an atomic writer may replace its path."""
    p = path(p)
    for _ in range(3):
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
                    and not before.st_mode & 0o077 and before.st_size <= MAX_BYTES,
                    'unsafe mutable snapshot')
            raw = stream.read(MAX_BYTES + 1)
            after = os.fstat(stream.fileno())
        if (before.st_ino, before.st_size, before.st_mtime_ns) == (after.st_ino, after.st_size, after.st_mtime_ns) and len(raw) == before.st_size:
            return parse(raw)
    raise RuntimeError('mutable snapshot did not stabilize')


def parse(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def sync_dir(p):
    fd = os.open(p, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write(p, raw, *, replace=False):
    p = path(p)
    temporary = p.with_name('.' + p.name + '.' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(raw); out.flush(); os.fsync(out.fileno())
        if replace:
            if p.exists():
                read(p)
            os.replace(temporary, p)
        else:
            os.link(temporary, p, follow_symlinks=False)
    finally:
        if temporary.exists():
            temporary.unlink()
    sync_dir(p.parent)


def put(p, value, *, replace=False):
    write(p, canonical(value), replace=replace)


def private_dir(p):
    p = path(p)
    p.mkdir(mode=0o700, exist_ok=True)
    st = p.stat()
    require(stat.S_ISDIR(st.st_mode) and st.st_uid == os.geteuid()
            and stat.S_IMODE(st.st_mode) == 0o700, 'journal directory must be private')
    sync_dir(p.parent)


def identity(pid=None):
    pid = os.getpid() if pid is None else pid
    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    require(fields[0] != 'Z', 'process is terminal')
    return dict(pid=pid, start_ticks=fields[19])


def process_state(record):
    require(isinstance(record, dict) and type(record.get('pid')) is int and record['pid'] > 0
            and re.fullmatch(r'[0-9]+', str(record.get('start_ticks', ''))), 'invalid process identity')
    try:
        fields = Path(f"/proc/{record['pid']}/stat").read_text().rsplit(')', 1)[1].split()
        require(len(fields) > 19, 'unknown process stat')
    except (FileNotFoundError, ProcessLookupError):
        return 'dead'
    if fields[19] != str(record['start_ticks']):
        return 'reused'
    return 'dead' if fields[0] == 'Z' else 'alive'


def lock(stack, p, *, create=False):
    fd = os.open(path(p), os.O_RDWR | os.O_NOFOLLOW | (os.O_CREAT if create else 0), 0o600)
    try:
        s = os.fstat(fd)
        require(stat.S_ISREG(s.st_mode) and s.st_uid == os.geteuid() and s.st_nlink == 1,
                'unsafe lock file')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = Path(p).lstat()
        require((current.st_dev, current.st_ino) == (s.st_dev, s.st_ino), 'lock path changed while acquiring')
        stream = os.fdopen(fd, 'r+')
    except BaseException:
        os.close(fd)
        raise
    stack.enter_context(stream)
    return stream


def validate_authorization(value, *, now, journal, helper_path, helper_sha, entry_sha):
    expected = dict(schema_version=1, purpose=PURPOSE, window_id=WINDOW,
        capsule_sha256=CAPSULE_SHA, attempt_id=ATTEMPT, evidence_sha256=EVIDENCE_SHA,
        journal_dir=str(journal), helper_path=str(helper_path), helper_sha256=helper_sha,
        entry_sha256=entry_sha, allowed_actions=ACTIONS)
    require(isinstance(value, dict) and set(value) == set(expected) | {
        'issued_epoch', 'deadline_epoch', 'authorization_reference'}, 'recovery authorization schema differs')
    require(all(value[k] == v for k, v in expected.items()), 'recovery authorization bindings differ')
    issued, deadline = value['issued_epoch'], value['deadline_epoch']
    require(all(type(v) in (int, float) and math.isfinite(v) for v in (issued, deadline))
            and issued <= now < deadline and 0 < deadline - issued <= 7200,
            'separate bounded recovery authorization expired or invalid')
    require(isinstance(value['authorization_reference'], str)
            and 1 <= len(value['authorization_reference']) <= 512,
            'explicit recovery authorization reference required')
    return value


def load_module(name, p, expected):
    raw = read(p, expected)
    module = types.ModuleType(name)
    module.__file__ = str(p)
    sys.modules[name] = module
    exec(compile(raw, str(p), 'exec'), module.__dict__)
    return module


def verify_old(old_dir, evidence):
    old_dir = path(old_dir)
    capsule = parse(read(old_dir / 'capsule.json', CAPSULE_SHA))
    require(read(old_dir / 'capsule.sha256').decode().strip() == CAPSULE_SHA,
            'old capsule sidecar changed')
    require(capsule['session_dir'] == str(old_dir) and capsule['window_id'] == WINDOW
            and capsule['holder'] == 'q03-isolated:' + WINDOW,
            'not the exact old session')
    pre = capsule['manifest']['preflight']
    require(pre['original_service_disposition'] == 'keep-stopped'
            and pre['publication']['sha'] == OLD_SOURCE
            and pre['remote_helper_sha256'] == OLD_HELPER
            and pre['remote_authority_sha256'] == OLD_AUTHORITY,
            'old frozen recovery policy differs')
    require(evidence['window_id'] == WINDOW and evidence['attempt_id'] == ATTEMPT
            and evidence['capsule']['sha256'] == CAPSULE_SHA
            and evidence['old_helper_sha256'] == OLD_HELPER
            and evidence['recovery_source_sha256'] == capsule['recovery_source_sha256']
            and len(capsule['recovery_source_sha256']) == 28,
            'private archival bindings differ')
    for relative, expected in OLD_FILES.items():
        read(old_dir / relative, expected)
    checkout = path(pre['independent_checkout'])
    for name, expected in capsule['recovery_source_sha256'].items():
        read(Path(os.path.normpath(checkout / 'scripts' / name)), expected)
    read(path(pre['remote_helper_path']), OLD_HELPER)
    authority = parse(read(path(pre['remote_authority_path']), OLD_AUTHORITY))
    require(authority == dict(independent_root=capsule['manifest']['independent_root'],
                             protected_roots=pre['protected_roots']), 'old authority differs')
    state = parse(read(old_dir / 'state.json', OLD_FILES['state.json']))
    require(state['phase'] == 'recovery-failed' and state['active_process'] is None
            and state['mutation_started'] is True and not state['restored'],
            'old state is not the frozen failed recovery')
    http_dir = old_dir / PREFIX / 'http'
    http_state = parse(read(http_dir / 'state.json', OLD_FILES[PREFIX + 'http/state.json']))
    require(http_state['epochs'] == evidence['http_epochs']
            and {x['epoch'] for x in http_state['epochs']} == set(http_state['sealed_epochs'])
            and len(http_state['epochs']) == 38
            and tuple(x['container_id'] for x in http_state['ranks']) == CONTAINERS,
            'old HTTP IDs/epochs differ')
    return capsule, state, parse(read(http_dir / 'plan.json')), http_state


def load_old(capsule):
    scripts = path(capsule['manifest']['preflight']['independent_checkout']) / 'scripts'
    sources = {Path(n).stem: (scripts / n, h) for n, h in capsule['recovery_source_sha256'].items()
               if n.endswith('.py')}
    require(not any(name in sys.modules for name in sources), 'old modules already imported before pinned loading')
    class Loader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
        def find_spec(self, fullname, path=None, target=None):
            if fullname in sources:
                return importlib.util.spec_from_loader(fullname, self)
            return None
        def create_module(self, spec):
            return None
        def exec_module(self, module):
            source, sha = sources[module.__name__]
            raw = read(source, sha)
            module.__file__ = str(source)
            exec(compile(raw, str(source), 'exec'), module.__dict__)
    # Kept for the lifetime of the isolated recovery process: any deferred old
    # Python dependency also compiles same-read pinned bytes, never ignored pyc.
    sys.meta_path.insert(0, Loader())
    names = ('q03_remote_operation', 'q03_window_preflight', 'q03_window_watchdog',
             'q03_window', 'q03_held_window', 'q03_owner_model', 'q03_rank_logs', 'q03_http_service')
    return {name: __import__(name) for name in names}


def terminal_old(capsule, evidence):
    require(capsule['core_process'] == evidence['core_process'], 'old core identity differs')
    for record in (capsule['core_process'], evidence['watchdog']['process']):
        require(process_state(record) in ('dead', 'reused'), 'old controller is not terminal')


# This only observes old operation bytes and old terminal semantics. In particular
# it does NOT chmod/reopen/repair historic streams or turn artifact_errors into
# successful captures. Historical metadata permits its recorded mode only when
# its bytes match the independently pinned archive; the strict log reader is
# still the unchanged old helper. No launch, cancel or seal operation is called.
REMOTE_OLD_PROOF = r'''
import base64,contextlib,hashlib,json,os,pathlib,platform,stat,sys,zlib
v=json.loads(zlib.decompress(base64.b85decode(sys.argv[1])))
def need(x,m):
 if not x: raise RuntimeError(m)
def raw(p,h,mode=None):
 p=pathlib.Path(p);need(p.is_absolute() and p.resolve()==p,'old path alias')
 fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 with os.fdopen(fd,'rb') as f:
  a=os.fstat(f.fileno());need(stat.S_ISREG(a.st_mode) and a.st_uid==os.geteuid() and a.st_nlink==1 and a.st_size<=8388608,'old evidence type/owner/size')
  need((not a.st_mode&0o022) if mode is None else stat.S_IMODE(a.st_mode)==int(mode,8),'old evidence mode changed')
  b=f.read(8388609);c=os.fstat(f.fileno());d=p.lstat()
 key=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
 need(key(a)==key(c)==key(d) and len(b)==a.st_size and hashlib.sha256(b).hexdigest()==h,'old evidence bytes changed')
 return b
need(platform.node()==v['hostname'],'wrong physical host')
p=v['helper_path'];source=raw(p,v['helper_sha256'])
m={'__name__':'_old_helper_read_only','__file__':p,'_Q03_VERIFIED_SOURCE_SHA256':v['helper_sha256']}
exec(compile(source,p,'exec'),m)
a=m['read_authority'](v['authority_path'],v['authority_sha256'])
need(a==v['authority'],'old authority differs')
base=pathlib.Path(a['independent_root'])/'remote-operations'/v['window']
need(base.resolve()==base,'operation root alias')
old_epochs={x['epoch'] for x in v['epochs']};old_ops={x['operation'] for x in v['operations']}
need({p.name for p in (base/'epochs').iterdir()}<=old_epochs|set(v['new_epochs']),'unrecorded epoch')
need({p.name for p in base.iterdir()}<=old_ops|set(v['new_operations'])|{'epochs'},'unrecorded operation')
for e in v['epochs']:
 ep=base/'epochs'/e['epoch'];b=raw(ep/'epoch.json',e['sha256'],e['mode'])
 value=json.loads(b);need(value['sealed'] is True and value['window']==v['window'] and value['epoch']==e['epoch'],'old epoch not sealed')
 need((ep/'lock').is_file() and (ep/'lock').resolve()==ep/'lock','old epoch lock missing')
results=[]
for r in v['operations']:
 d=base/r['operation'];values={}
 for name,item in r['files'].items():
  b=raw(d/name,item['sha256'],item['mode'])
  if name in ('manifest.json','receipt.json','state.json'):values[name]=json.loads(b)
 manifest,receipt,state=(values[x] for x in ('manifest.json','receipt.json','state.json'))
 need(manifest['helper_sha256']==v['helper_sha256'] and manifest['epoch']==r['epoch'] and manifest['window']==v['window'] and manifest['operation']==r['operation'],'old manifest identity')
 need(state['phase']=='terminal','old operation not terminal')
 for record in (state['supervisor'],state['child']):
  try:
   f=pathlib.Path('/proc/%s/stat'%record['pid']).read_text().rsplit(')',1)[1].split()
  except (FileNotFoundError,ProcessLookupError):continue
  need(f[0]=='Z' or f[19]!=record['start_ticks'],'old operation process alive')
 # A terminal receipt is not enough if a surviving member still has its PGID.
 for p in pathlib.Path('/proc').glob('[0-9]*/stat'):
  try:f=p.read_text().rsplit(')',1)[1].split()
  except (FileNotFoundError,ProcessLookupError):continue
  need(f[0]=='Z' or int(f[2])!=state['child']['pid'],'old operation group remains or PGID reused')
 s=m['status'](str(base.parent),v['window'],r['operation'])
 need(s.get('manifest_sha256')==r['files']['manifest.json']['sha256'] and all(s.get(k)==value for k,value in receipt.items()),'old status no longer matches pinned receipt')
 need(s['status']=='terminal' and s['group_quiescent'] is True and s['epoch_sealed'] is True,'old terminal barrier differs')
 need(s.get('safe_to_overlap') is True or (r['operation']==v['client_operation'] and hashlib.sha256(json.dumps(manifest['argv'],sort_keys=True,separators=(',',':')).encode()).hexdigest()==v['client_argv_sha256']),'old mutating failure is UNKNOWN')
 results.append({'operation':r['operation'],'epoch':r['epoch'],'manifest_sha256':r['files']['manifest.json']['sha256'],'receipt_sha256':r['files']['receipt.json']['sha256'],'action_succeeded':s['action_succeeded'],'safe_to_overlap':s['safe_to_overlap'],'group_quiescent':True,'epoch_sealed':True,'artifact_errors':s.get('log_capture',{}).get('artifact_errors',{})})
print(json.dumps({'hostname':platform.node(),'window_id':v['window'],'old_helper_sha256':v['helper_sha256'],'authority_sha256':v['authority_sha256'],'epochs':v['epochs'],'operations':results,'old_bytes_unchanged':True}))
'''


def old_payload(capsule, evidence, rank, new_epochs):
    pre = capsule['manifest']['preflight']
    host = evidence['hosts'][rank]
    require(host['rank'] == rank and host['host'] == pre['nodes'][rank]['name'], 'physical rank map differs')
    client = next(x for x in evidence['http_epochs'] if x['action_kind'] == 'http_client')
    return dict(window=WINDOW, hostname=pre['nodes'][rank]['evidence_hostname'],
        helper_path=pre['remote_helper_path'], helper_sha256=OLD_HELPER,
        authority_path=pre['remote_authority_path'], authority_sha256=OLD_AUTHORITY,
        authority=dict(independent_root=capsule['manifest']['independent_root'], protected_roots=pre['protected_roots']),
        epochs=[dict(epoch=e['epoch'], sha256=e['sha256'], mode=e['original_archive_mode']) for e in host['epochs']],
        operations=[dict(operation=o['operation'], epoch=o['epoch'], files={name: dict(
            sha256=f['sha256'], mode=f['original_archive_mode']) for name, f in o['files'].items()})
            for o in host['operations']],
        new_epochs=[r['epoch'] for r in new_epochs],
        new_operations=[r['operation'] for r in new_epochs if r['rank'] == rank],
        client_operation=client['operation'], client_argv_sha256=client['argv_sha256'])


def mutation_terminal_ok(kind, value, ident, helper):
    successful = (value.get('action_succeeded') is True
        and type(value.get('exit_code')) is int and value['exit_code'] == 0
        and value.get('cancelled') is False and value.get('timed_out') is False)
    if successful:
        return True
    if kind != 'stop_guard':
        return False
    unit = ident + '-memguard.service'
    command = helper.bound_command(['sudo', '-n', 'systemctl', 'stop', unit])
    return (value.get('action_succeeded') is False and value.get('lifecycle_acknowledged') is True
        and value.get('lifecycle_unit') == unit
        and helper.acknowledged_memguard_absence(command, value, value.get('stdout'),
                                                value.get('stderr'), expected_unit=unit))


def validate_journal(state, exact_ranks, events, helper):
    require(isinstance(state, dict) and state.get('phase') in ('client_terminal', 'drained')
            and type(state.get('sequence')) is int and state['sequence'] >= 0, 'invalid recovery journal state')
    rows = state.get('ranks')
    require(isinstance(rows, list) and len(rows) == 4, 'recovery needs all four ranks')
    immutable = ('rank', 'container_id', 'config_sha256', 'guard_intent', 'guard_phase')
    for rank, (row, expected) in enumerate(zip(rows, exact_ranks)):
        require(all(row.get(k) == expected.get(k) for k in immutable)
                and type(row.get('rank')) is int and row['rank'] == rank
                and type(row.get('removed')) is bool
                and set(row) <= set(expected) | {'log_capture'}, 'recovery rank/config/guard binding differs')
        if rank == 0:
            require(row.get('log_capture') == expected['log_capture'], 'complete old rank0 proof changed')
    records = state.get('epochs')
    require(isinstance(records, list), 'new epoch inventory missing')
    epochs, operations = set(), set()
    for r in records:
        require(set(r) == {'rank', 'action_kind', 'epoch', 'operation', 'argv_sha256'}
                and type(r['rank']) is int and r['rank'] in range(4)
                and all(isinstance(r[k], str) and re.fullmatch('[0-9a-f]{32}', r[k]) for k in ('epoch', 'operation'))
                and r['epoch'] not in epochs and r['operation'] not in operations, 'invalid/duplicate recovery epoch')
        ident = rows[r['rank']]['container_id']
        commands = {'capture_logs': ['docker', 'logs', '--timestamps', '--', ident],
                    'stop': ['docker', 'stop', '--time', '60', ident],
                    'stop_guard': ['sudo', '-n', 'systemctl', 'stop', ident + '-memguard.service'],
                    'remove': ['docker', 'rm', ident]}
        require(r['action_kind'] in commands and r['argv_sha256'] == digest(canonical(
            helper.bound_command(commands[r['action_kind']]))), 'new epoch is not a fixed recovery command')
        epochs.add(r['epoch']); operations.add(r['operation'])
    sealed = state.get('sealed_epochs')
    require(isinstance(sealed, list) and len(sealed) == len(set(sealed)) and set(sealed) <= epochs,
            'new sealed cache inventory differs')
    for epoch in sealed:
        record = next(r for r in records if r['epoch'] == epoch)
        proofs = [e for e in events if e.get('kind') == 'epoch-sealed' and e.get('epoch') == epoch]
        require(all(e.get('window_id') == WINDOW and e.get('attempt_id') == ATTEMPT for e in proofs),
                'new sealed event window/attempt differs')
        for rank in range(4):
            matches = [e['receipt'] for e in proofs if e.get('rank') == rank]
            require(matches, 'new sealed cache lacks all-four immutable receipts')
            for receipt in matches:
                require(receipt.get('window') == WINDOW and receipt.get('epoch') == epoch
                        and receipt.get('sealed') is True and receipt.get('safe_to_overlap') is True,
                        'new sealed receipt differs')
                expected = [record['operation']] if rank == record['rank'] else []
                operations = receipt.get('operations')
                require(isinstance(operations, list) and [o.get('operation') for o in operations] == expected
                        and all(o.get('window') == WINDOW and o.get('status') == 'terminal' and o.get('group_quiescent') is True
                            and o.get('epoch_sealed') is True and o.get('safe_to_overlap') is True
                            and re.fullmatch('[0-9a-f]{64}', o.get('manifest_sha256', ''))
                            for o in operations), 'new sealed receipt operation binding differs')
                for operation in operations:
                    if record['action_kind'] == 'capture_logs':
                        capture = operation.get('log_capture', {})
                        require(capture.get('container_id') == rows[record['rank']]['container_id']
                                and capture.get('timestamps') is True
                                and type(capture.get('stream_limit_bytes')) is int
                                and capture['stream_limit_bytes'] == helper.LOG_STREAM_LIMIT,
                                'failed-read barrier is not the exact bounded log command')
                    else:
                        require(mutation_terminal_ok(record['action_kind'], operation,
                                rows[record['rank']]['container_id'], helper),
                                'cached mutation lacks success or exact unloaded-guard acknowledgement')
                        if operation.get('action_succeeded') is False:
                            acks = [e for e in events if e.get('kind') == 'guard-lifecycle-acknowledged'
                                and e.get('window_id') == WINDOW and e.get('attempt_id') == ATTEMPT
                                and e.get('rank') == rank and e.get('epoch') == epoch
                                and e.get('operation') == record['operation']
                                and e.get('manifest_sha256') == operation['manifest_sha256']
                                and e.get('unit') == rows[rank]['container_id'] + '-memguard.service'
                                and e.get('action_succeeded') is False and e.get('original_exit_code') == 5]
                            require(any(e.get('guard', {}).get('absent') is True or
                                (e.get('guard', {}).get('owned') is True and e['guard'].get('active') is False)
                                for e in acks), 'cached guard acknowledgement lacks separate bound inactive proof')


class Journal:
    def __init__(self, directory, plan, old_state, old_directory, logs, validate):
        self.directory, self.plan = directory, plan
        self.exact_ranks = copy.deepcopy(old_state['ranks'])
        private_dir(directory)
        if not (directory / 'plan.json').exists():
            put(directory / 'plan.json', plan)
        require(parse(read(directory / 'plan.json')) == plan, 'new journal plan differs')
        if not (directory / 'state.json').exists():
            # Reuse only the previously COMPLETE rank0 generation, never an
            # incomplete peer stream. Copy verified bytes; preserve the originals.
            old_journal = types.SimpleNamespace(directory=old_directory, plan=plan)
            first = old_state['ranks'][0]
            logs.verified_capture(old_journal, first, validate)
            rel = Path('rank-logs/0') / first['log_capture']['capture_id']
            for p in (directory / 'rank-logs', directory / 'rank-logs/0', directory / rel):
                private_dir(p)
            for name in (*logs.FILES, 'index.json'):
                target = directory / rel / name
                raw = logs.read_owned_bytes(old_directory / rel / name)
                if target.exists():
                    require(read(target) == raw, 'partially copied rank0 proof differs')
                else:
                    write(target, raw)
            put(directory / 'state.json', dict(phase='client_terminal', sequence=0,
                epochs=[], sealed_epochs=[], ranks=copy.deepcopy(old_state['ranks'])))
        self.state = snapshot(directory / 'state.json')
        require(tuple(x['container_id'] for x in self.state['ranks']) == CONTAINERS,
                'new journal exact IDs differ')
        logs.verified_capture(self, self.state['ranks'][0], validate)

    def save(self):
        put(self.directory / 'state.json', self.state, replace=True)

    def event(self, kind, **fields):
        sequence = self.state['sequence'] + 1
        # A crash after event fsync and before the sequence commit must never
        # overwrite an event. Advance past retained immutable records on retry.
        while (self.directory / f'{sequence:06d}.json').exists():
            sequence += 1
        put(self.directory / f'{sequence:06d}.json', dict(sequence=sequence,
            kind=kind, window_id=WINDOW, attempt_id=ATTEMPT, **fields))
        self.state['sequence'] = sequence
        self.save()

    def phase(self, phase):
        require(phase == 'drained', 'recovery cannot enter a launch phase')
        self.event('phase', phase=phase)
        self.state['phase'] = phase
        self.save()


def make_ops(old, session, journal, intent, evidence, helper, check):
    http = old['q03_http_service']
    class RecoveryOps(http.HttpOps):
        def __init__(self):
            self.session, self.plan, self.journal = session, journal.plan, journal
            self.recovering = True
            self.binding = self.plan['binding']
            self.nodes = old['q03_owner_model'].remote_nodes(self.binding)
            self.pre = session.manifest['preflight']
            self.helper = helper
            self.end = intent['authorization']['deadline_epoch']

        def check_authority(self):
            check()
            require(parse(read(self.journal.directory / 'plan.json')) == self.plan,
                    'new HTTP plan changed')
            http.verify_plan_identity(session, self.plan)
            require(snapshot(self.journal.directory / 'state.json') == self.journal.state, 'new journal state changed outside writer')
            require(not ({r['epoch'] for r in self.journal.state['epochs']} &
                        {r['epoch'] for r in evidence['hosts'][0]['epochs']})
                    and not ({r['operation'] for r in self.journal.state['epochs']} &
                        {r['operation'] for h in evidence['hosts'] for r in h['operations']}),
                    'new recovery may not reuse old epoch/operation identities')
            validate_journal(self.journal.state, intent['exact_ranks'],
                [parse(read(p)) for p in sorted(self.journal.directory.glob('[0-9]*.json'))], self.helper)

        def helper_call(self, rank, action, epoch, operation=None, extra=()):
            self.check_authority()
            require(epoch not in {e['epoch'] for e in evidence['hosts'][rank]['epochs']},
                    'new helper may not interpret or mutate an old epoch')
            records = [x for x in self.journal.state['epochs'] if x['epoch'] == epoch]
            require(len(records) == 1 and records[0]['action_kind'] in
                    ('capture_logs', 'stop', 'stop_guard', 'remove'), 'unregistered recovery action')
            if operation is not None:
                require(records[0]['rank'] == rank and records[0]['operation'] == operation,
                        'new operation identity differs')
            require(action in ('launch', 'status', 'seal', 'cancel'), 'invalid recovery helper verb')
            self.verify_boundary(rank)
            argv = [action, '--root', str(Path(self.binding['independent_root']) / 'remote-operations'),
                '--window', WINDOW, '--epoch', epoch, '--authority', self.pre['remote_authority_path'],
                '--authority-sha256', OLD_AUTHORITY]
            if operation:
                argv += ['--operation', operation]
            wire = self.helper.verified_helper_command(intent['new_helper_path'],
                intent['new_helper_sha256'], [*argv, *extra])
            wire[0] = 'python3'
            result = self.transport(rank, wire)
            require(result.returncode == 0, 'new recovery remote outcome UNKNOWN: ' + result.stderr)
            value = parse(result.stdout)
            require(value.get('window') == WINDOW and (value.get('operation') == operation
                    if operation else value.get('epoch') == epoch), 'new helper receipt differs')
            return value

        def mutate(self, rank, kind, argv, timeout):
            if kind != 'stop_guard':
                return super().mutate(rank, kind, argv, timeout)
            # Preserve the helper's existing narrowly bound unloaded-unit case.
            # This is lifecycle acknowledgement, never fabricated action success.
            self.check_authority()
            ident = self.journal.state['ranks'][rank]['container_id']
            require(argv == ['sudo', '-n', 'systemctl', 'stop', ident + '-memguard.service'],
                    'guard mutation is not the exact owned unit')
            epoch, operation = uuid.uuid4().hex, uuid.uuid4().hex
            command = self.helper.bound_command(argv)
            record = dict(rank=rank, action_kind=kind, epoch=epoch, operation=operation,
                          argv_sha256=digest(canonical(command)))
            self.journal.state['epochs'].append(record)
            self.journal.save()
            self.journal.event('remote-intent', **record)
            value = self.helper_call(rank, 'launch', epoch, operation, (
                '--argv-json', canonical(command).decode(), '--cwd', self.binding['independent_root'],
                '--timeout-seconds', str(min(timeout, max(1, self.end - time.time())))))
            while value.get('status') != 'terminal':
                self.check_authority()
                require(value.get('status') != 'uncertain' or not value.get('state', {}).get('supervisor'),
                        'guard supervisor lost without terminal receipt')
                self.sleep(.1)
                value = self.helper_call(rank, 'status', epoch, operation)
            self.journal.event('remote-terminal', rank=rank, operation=operation, receipt=value)
            require(value.get('group_quiescent') is True and mutation_terminal_ok(kind, value, ident, self.helper),
                    'guard action lacks success or exact unloaded-unit acknowledgement')
            if value['action_succeeded'] is False:
                guard = self.guard_state(rank, self.journal.state['ranks'][rank])
                require(guard.get('absent') is True or
                        (guard.get('owned') is True and guard.get('active') is False),
                        'unloaded-unit acknowledgement lacks fresh inactive guard proof')
                self.journal.event('guard-lifecycle-acknowledged', rank=rank, epoch=epoch,
                    operation=operation, manifest_sha256=value['manifest_sha256'],
                    unit=ident + '-memguard.service', action_succeeded=False, original_exit_code=5, guard=guard)
            self.seal_epoch(epoch)
            return value['stdout']

        def action(self, rank, kind, record):
            require(kind in ('stop', 'stop_guard', 'remove'), 'recovery forbids START/create/prepare')
            return super().action(rank, kind, record)

        def audit_old(self):
            for rank in range(4):
                self.check_authority()
                payload = old_payload(session.capsule, evidence, rank, self.journal.state['epochs'])
                encoded = base64.b85encode(zlib.compress(canonical(payload))).decode()
                require(len(encoded) < 90000, 'old proof request too large')
                result = self.transport(rank, self.helper.bound_command(
                    ['python3', '-I', '-B', '-c', REMOTE_OLD_PROOF, encoded]), timeout=180)
                require(result.returncode == 0, 'old remote proof UNKNOWN: ' + result.stderr)
                proof = parse(result.stdout)
                require(proof.get('old_bytes_unchanged') is True and proof['window_id'] == WINDOW
                        and proof['hostname'] == payload['hostname']
                        and proof['epochs'] == payload['epochs'], 'old remote proof binding differs')
                self.journal.event('old-operation-barriers', rank=rank, evidence_sha256=EVIDENCE_SHA, proof=proof)

        def verify_helpers(self):
            for rank in range(4):
                self.check_authority()
                self.verify_boundary(rank)
                wire = self.helper.verified_helper_command(intent['new_helper_path'],
                    intent['new_helper_sha256'], ['verify', '--root', str(Path(self.binding['independent_root']) / 'remote-operations'),
                    '--window', WINDOW, '--epoch', intent['recovery_id'], '--authority', self.pre['remote_authority_path'],
                    '--authority-sha256', OLD_AUTHORITY])
                wire[0] = 'python3'
                result = self.transport(rank, wire)
                require(result.returncode == 0 and parse(result.stdout) == dict(window=WINDOW,
                    epoch=intent['recovery_id'], authority_sha256=OLD_AUTHORITY,
                    helper_sha256=intent['new_helper_sha256']), 'all-four new helper verification failed')
                self.journal.event('new-helper-verified', rank=rank, sha256=intent['new_helper_sha256'])
    return RecoveryOps()


def check_paths(capsule, directory, helper_path):
    root = path(capsule['manifest']['independent_root'])
    old = path(capsule['session_dir'])
    pre = capsule['manifest']['preflight']
    protected = [path(p) for p in pre['protected_roots']]
    protected += [path(pre[k]) for k in ('hold_path', 'request_path', 'legacy_hold_path',
                                       'legacy_request_path', 'policy_path', 'independent_checkout')]
    protected += [path(pre['registered_policy']['repository']), old]
    candidate = capsule['manifest']['candidate']
    protected += [path(candidate[k]) for k in ('cache_path', 'output_path', 'tests_path',
        'runtime_manifest_path', 'tblib_path', 'build_receipt_path', 'build_inputs_path', 'git_toolchain_path')]
    require(helper_path == root / 'helpers' / ('q03_remote_operation-' + NEW_HELPER + '.py'),
            'new helper must use its distinct frozen hash-named path')
    for target in (directory, helper_path):
        require(target != root and target.is_relative_to(root), 'new recovery path escapes independent root')
        require(all(not target.is_relative_to(p) and not p.is_relative_to(target) for p in protected),
                'new recovery path overlaps old or protected namespace')
    require(not helper_path.is_relative_to(directory) and not directory.is_relative_to(helper_path),
            'new helper and journal overlap')
    for p in (directory.parent, *directory.parent.parents):
        info = p.stat()
        require(info.st_uid in (0, os.geteuid()) and not info.st_mode & 0o022,
                'new journal ancestry must be owner controlled')


def prepare(old_dir, evidence_file, directory, helper_path, helper_sha, reference, budget, *, apply=False):
    evidence_file, directory, helper_path = map(path, (evidence_file, directory, helper_path))
    evidence = parse(read(evidence_file, EVIDENCE_SHA))
    capsule, state, plan, http_state = verify_old(old_dir, evidence)
    require(helper_sha == NEW_HELPER, 'only the separately frozen 0600 helper is admitted')
    read(helper_path, helper_sha)
    check_paths(capsule, directory, helper_path)
    require(type(budget) is int and 0 < budget <= capsule['recovery_timeout_s'] == 7200,
            'recovery budget must be explicit and at most 7200 seconds')
    entry = path(Path(__file__).resolve())
    now = time.time()
    authorization = dict(schema_version=1, purpose=PURPOSE, window_id=WINDOW,
        capsule_sha256=CAPSULE_SHA, attempt_id=ATTEMPT, evidence_sha256=EVIDENCE_SHA,
        journal_dir=str(directory), helper_path=str(helper_path), helper_sha256=helper_sha,
        entry_sha256=digest(read(entry)), allowed_actions=ACTIONS,
        issued_epoch=now, deadline_epoch=now + budget, authorization_reference=reference)
    validate_authorization(authorization, now=now, journal=directory, helper_path=helper_path,
        helper_sha=helper_sha, entry_sha=digest(read(entry)))
    intent = dict(schema_version=1, recovery_id=uuid.uuid4().hex, authorization=authorization,
        old_session=str(path(old_dir)), capsule_sha256=CAPSULE_SHA, window_id=WINDOW, attempt_id=ATTEMPT,
        evidence_path=str(evidence_file), evidence_sha256=EVIDENCE_SHA,
        old_source_sha256=capsule['recovery_source_sha256'], old_session_file_sha256=OLD_FILES,
        old_http_epochs=http_state['epochs'], old_transition_epochs=state['transition_epochs'],
        exact_ranks=http_state['ranks'], old_helper_path=capsule['manifest']['preflight']['remote_helper_path'],
        old_helper_sha256=OLD_HELPER, old_authority_sha256=OLD_AUTHORITY,
        old_recovery_deadline_epoch=state['restoring_started_epoch'] + capsule['recovery_timeout_s'],
        new_helper_path=str(helper_path), new_helper_sha256=helper_sha,
        new_helper_publication=HELPER_PUBLICATION,
        entry_path=str(entry), entry_sha256=digest(read(entry)),
        recovery_command=[sys.executable, '-I', '-B', str(entry), '--journal-dir', str(directory), '--apply'])
    if not apply:
        return dict(status='plan-only', execution_enabled=False, intent=intent)
    require(not directory.exists(), 'new recovery journal must not already exist; use its frozen --apply')
    private_dir(directory)
    put(directory / 'intent.json', intent)
    return dict(status='prepared', journal_dir=str(directory), intent_sha256=digest(read(directory / 'intent.json')),
                deadline_epoch=authorization['deadline_epoch'], old_recovery_deadline_epoch=intent['old_recovery_deadline_epoch'],
                command=intent['recovery_command'], execution_enabled=False)


def load_intent(directory):
    directory = path(directory)
    raw = read(directory / 'intent.json')
    intent = parse(raw)
    entry = path(Path(__file__).resolve())
    require(intent.get('entry_path') == str(entry) and intent.get('entry_sha256') == digest(read(entry)),
            'new recovery source changed')
    require(intent.get('capsule_sha256') == CAPSULE_SHA and intent.get('window_id') == WINDOW
            and intent.get('attempt_id') == ATTEMPT and intent.get('evidence_sha256') == EVIDENCE_SHA,
            'immutable recovery intent differs')
    helper_path = path(intent['new_helper_path'])
    require(intent['new_helper_sha256'] == NEW_HELPER and intent['new_helper_publication'] == HELPER_PUBLICATION,
            'new helper is not the frozen 0600 repair')
    read(helper_path, NEW_HELPER)
    validate_authorization(intent['authorization'], now=time.time(), journal=directory,
        helper_path=helper_path, helper_sha=intent['new_helper_sha256'], entry_sha=intent['entry_sha256'])
    require(intent['recovery_command'] == [sys.executable, '-I', '-B', str(entry),
            '--journal-dir', str(directory), '--apply'], 'frozen recovery command differs')
    return intent, digest(raw)


def verify_retry(directory, intent_sha):
    marker = parse(read(directory / 'supervisor-retry.json'))
    state = snapshot(directory / 'state.json')
    require(marker.get('intent_sha256') == intent_sha and marker['child'] == identity()
            and process_state(marker['supervisor']) == 'alive'
            and process_state(marker['main']) in ('dead', 'reused')
            and state['recovery_process'] == marker['main'], 'retry is not the gated supervisor child')
    return marker


def start_supervisor(directory, main, intent_sha):
    entry = Path(__file__).resolve()
    fd = os.open(directory / 'supervisor.log', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as out:
        child = subprocess.Popen([sys.executable, '-I', '-B', str(entry), '--journal-dir', str(directory),
            '--supervise', '--main-pid', str(main['pid']), '--main-start', main['start_ticks'],
            '--intent-sha256', intent_sha], stdin=subprocess.DEVNULL, stdout=out,
            stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
    _CHILDREN.append(child)
    expected = identity(child.pid)
    end = time.monotonic() + 10
    while time.monotonic() < end:
        ready = directory / 'supervisor-ready.json'
        if ready.exists():
            try:
                value = parse(read(ready))
            except RuntimeError:
                require(child.poll() is None, 'supervisor died during ready publication')
                time.sleep(.01)
                continue
            require(value == dict(main=main, supervisor=expected, intent_sha256=intent_sha)
                    and process_state(expected) == 'alive', 'supervisor readiness differs')
            return value
        require(child.poll() is None, 'independent supervisor exited before readiness')
        time.sleep(.05)
    raise RuntimeError('supervisor readiness timeout; no remote operation admitted')


def launch_retry(directory, intent, digest_value, main):
    r, w = os.pipe()
    gate = ('import os,sys; f=int(sys.argv[1]); b=os.read(f,1); os.close(f); '
            'os.execvpe(sys.argv[2],sys.argv[2:],os.environ) if b==b"1" else sys.exit(125)')
    fd = os.open(directory / 'supervisor-retry.log', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as out:
            child = subprocess.Popen([sys.executable, '-I', '-B', '-c', gate, str(r),
                    *intent['recovery_command'], '--retry'], stdin=subprocess.DEVNULL, stdout=out,
                    stderr=subprocess.STDOUT, pass_fds=(r,), start_new_session=True)
        _CHILDREN.append(child)
        child_record = identity(child.pid)
        put(directory / 'supervisor-retry.json', dict(main=main, child=child_record,
            supervisor=identity(), intent_sha256=digest_value))
        os.write(w, b'1')
        return child_record
    finally:
        os.close(r); os.close(w)


def monitor_step(intent, state, hold, target_state, heartbeat, now, *, retried=False, target=None, intent_sha=None,
                 retry_previous=None, retry_ready_until=None):
    """Pure independent supervisor decision; UNKNOWN never grants mutation."""
    require(now < intent['authorization']['deadline_epoch'], 'recovery deadline reached; retain hold')
    if hold is None and state.get('phase') in ('release-ready', 'closed-stopped'):
        require(state.get('closure_proof_sha256'), 'closure marker lacks release proof')
        return 'done'
    require(state.get('phase') != 'UNKNOWN', 'main reported UNKNOWN; retain hold without retry')
    require(isinstance(hold, dict) and hold.get('window') == WINDOW
            and hold.get('holder') == 'q03-isolated:' + WINDOW, 'hold ownership UNKNOWN')
    require(target_state in ('alive', 'dead'), 'recovery process identity UNKNOWN')
    if target_state == 'dead':
        require(not retried, 'recovery retry ended without proof; retain hold')
        return 'retry'
    if (retried and target_state == 'alive' and retry_ready_until is not None and now < retry_ready_until
            and isinstance(heartbeat, dict) and heartbeat.get('process') == retry_previous
            and heartbeat.get('intent_sha256') == intent_sha):
        return 'wait-retry-ready'  # This is not child mutation authority.
    require(isinstance(heartbeat, dict) and heartbeat.get('process') == target
            and heartbeat.get('intent_sha256') == intent_sha, 'main heartbeat identity differs')
    require(type(heartbeat.get('epoch')) in (int, float)
            and 0 <= now - heartbeat['epoch'] <= 90, 'recovery heartbeat lost; retain hold')
    return 'wait'


def supervise(directory, main, expected_sha):
    directory = path(directory)
    with contextlib.ExitStack() as locks:
        lock(locks, directory / 'supervisor.lock', create=True)
        intent, sha = load_intent(directory)
        require(sha == expected_sha, 'supervisor intent differs')
        capsule = parse(read(path(intent['old_session']) / 'capsule.json', CAPSULE_SHA))
        require(process_state(main) == 'alive', 'main is not live before supervisor readiness')
        state = snapshot(directory / 'state.json')
        require(state['recovery_process'] == main, 'main journal identity differs')
        supervisor_identity = identity()
        def pulse():
            put(directory / 'supervisor-heartbeat.json', dict(epoch=time.time(),
                supervisor=supervisor_identity, intent_sha256=sha), replace=True)
        pulse()
        put(directory / 'supervisor-ready.json', dict(main=main, supervisor=supervisor_identity, intent_sha256=sha))
        target, retried, retry_ready_until = main, False, None
        try:
            while True:
                for child in _CHILDREN:
                    child.poll()
                pulse()
                fresh, current = load_intent(directory)
                require(current == sha, 'supervisor intent changed')
                state = snapshot(directory / 'state.json')
                try:
                    # The old hold reader locks against its fixed-width heartbeat write.
                    watchdog = load_module('_recovery_hold_reader',
                        path(capsule['manifest']['preflight']['independent_checkout']) / 'scripts/q03_window_watchdog.py',
                        capsule['recovery_source_sha256']['q03_window_watchdog.py'])
                    hold = watchdog.read_owned_hold(capsule['hold_path'], WINDOW, capsule['holder'])
                except FileNotFoundError:
                    hold = None
                decision = monitor_step(fresh, state, hold, process_state(target),
                    snapshot(directory / 'heartbeat.json'), time.time(), retried=retried,
                    target=target, intent_sha=sha, retry_previous=main, retry_ready_until=retry_ready_until)
                if decision == 'done':
                    proof = read(directory / 'closure-proof.json', state['closure_proof_sha256'])
                    require(parse(proof)['intent_sha256'] == sha, 'supervisor closure proof differs')
                    put(directory / 'supervisor-result.json', dict(status='closed-stopped-observed',
                        intent_sha256=sha, hold_absent_observed=True, retry_started=retried))
                    return
                if decision == 'retry':
                    target = launch_retry(directory, intent, sha, main)
                    retried = True
                    retry_ready_until = min(time.time() + 10, intent['authorization']['deadline_epoch'])
                time.sleep(1)
        except BaseException as error:
            put(directory / 'supervisor-result.json', dict(status='UNKNOWN', intent_sha256=sha,
                error=str(error), hold_released=False))
            raise


class ReleasedClosureError(RuntimeError):
    """The hold was released after proof; retain the durable release-ready record."""


def publish_release(directory, state, closure_path, release, guard, *, writer=put):
    proof_sha = digest(read(closure_path))
    state.update(phase='release-ready', closure_proof_sha256=proof_sha)
    writer(directory / 'state.json', state, replace=True)
    guard()
    release()
    state.update(phase='closed-stopped', original_kept_stopped=True, restored=False,
        hold_released=True, closure_proof_sha256=proof_sha, finished_epoch=time.time())
    try:
        writer(directory / 'state.json', state, replace=True)
    except BaseException as error:
        raise ReleasedClosureError('proved release completed; final record pending independent reconciliation') from error
    return state


def apply(directory, *, retry=False):
    directory = path(directory)
    intent, intent_sha = load_intent(directory)
    evidence = parse(read(path(intent['evidence_path']), EVIDENCE_SHA))
    capsule, old_state, plan, http_state = verify_old(intent['old_session'], evidence)
    check_paths(capsule, directory, path(intent['new_helper_path']))
    require(intent['old_source_sha256'] == capsule['recovery_source_sha256']
            and intent['old_session_file_sha256'] == OLD_FILES
            and intent['old_http_epochs'] == http_state['epochs']
            and intent['old_transition_epochs'] == old_state['transition_epochs']
            and intent['exact_ranks'] == http_state['ranks'], 'old intent bindings differ')
    old_dir = path(intent['old_session'])
    terminal_old(capsule, evidence)
    old = load_old(capsule)
    runtime = old['q03_window'].RuntimeOps()
    # These are the actual existing runtime locks, not private lookalike locks.
    with contextlib.ExitStack() as locks:
        held_locks = []
        for p in (old_dir / 'session.lock', old_dir / 'watchdog.lock', old_dir / PREFIX / 'http/http.lock'):
            held_locks.append((p, lock(locks, p)))
        gpu_lock = locks.enter_context(runtime.acquire_gpu_lock())
        held_locks.append((Path('/tmp/spark-lab-gpu.lock'), gpu_lock))
        held_locks.append((directory / 'recovery.lock', lock(locks, directory / 'recovery.lock', create=True)))
        terminal_old(capsule, evidence)
        session = old['q03_window'].Session(old_dir, runtime)
        require(session.owns_hold(), 'recovery requires the exact old owned hold')
        prior = parse(read(directory / 'state.json')) if (directory / 'state.json').exists() else None
        if retry:
            verify_retry(directory, intent_sha)
        else:
            require(prior is None, 'apply already attempted; only the independently gated retry may continue')
        main = identity()
        state = dict(prior or {}, phase='recovering', recovery_process=main,
                     intent_sha256=intent_sha, hold_released=False, original_kept_stopped=False)
        put(directory / 'state.json', state, replace=prior is not None)
        stop_heartbeat, heartbeat_errors = threading.Event(), []
        def beat():
            session.beat()
            put(directory / 'heartbeat.json', dict(epoch=time.time(), process=main, intent_sha256=intent_sha), replace=True)
        def keepalive():
            while not stop_heartbeat.wait(2):
                try:
                    beat()
                except BaseException as error:
                    heartbeat_errors.append(error)
                    return
        beat()
        thread = threading.Thread(target=keepalive, daemon=True)
        thread.start()
        released = False
        try:
            ready = (parse(read(directory / 'supervisor-ready.json')) if retry
                     else start_supervisor(directory, main, intent_sha))
            def check():
                current, sha = load_intent(directory)
                require(sha == intent_sha and current == intent, 'frozen recovery intent changed')
                require(not heartbeat_errors and thread.is_alive() and session.owns_hold(),
                        'owned recovery heartbeat failed')
                require(process_state(ready['supervisor']) == 'alive'
                        and not (directory / 'supervisor-result.json').exists(), 'independent supervisor lost')
                pulse = snapshot(directory / 'supervisor-heartbeat.json')
                require(pulse.get('supervisor') == ready['supervisor'] and pulse.get('intent_sha256') == intent_sha
                        and type(pulse.get('epoch')) in (int, float) and 0 <= time.time() - pulse['epoch'] <= 10,
                        'independent supervisor heartbeat lost')
                for lock_path, stream in held_locks:
                    current, opened = lock_path.lstat(), os.fstat(stream.fileno())
                    require((current.st_dev, current.st_ino) == (opened.st_dev, opened.st_ino)
                            and stat.S_ISREG(opened.st_mode) and opened.st_uid == os.geteuid()
                            and not opened.st_mode & 0o022 and opened.st_nlink == 1, 'held runtime lock replaced or unsafe')
                terminal_old(capsule, evidence)
                verify_old(old_dir, evidence)
            check()
            original_run = runtime.run
            def bounded_run(argv, *, cwd=None, timeout=60, check=True):
                authority_check()
                remaining = intent['authorization']['deadline_epoch'] - time.time()
                require(remaining > 0, 'recovery deadline reached before probe')
                result = original_run(argv, cwd=cwd, timeout=min(timeout, remaining), check=check)
                authority_check()
                return result
            authority_check = check
            runtime.run = bounded_run
            helper = load_module('_q03_new_recovery_helper', path(intent['new_helper_path']), intent['new_helper_sha256'])
            require(helper.LOG_STREAM_LIMIT == old['q03_remote_operation'].LOG_STREAM_LIMIT
                    and helper.LOG_TIMEOUT_SECONDS == old['q03_remote_operation'].LOG_TIMEOUT_SECONDS,
                    'new helper changed bounded log contract')
            journal = Journal(directory / 'http', plan, http_state, old_dir / PREFIX / 'http',
                              old['q03_rank_logs'], old['q03_http_service'].validate_container)
            ops = make_ops(old, session, journal, intent, evidence, helper, check)
            ops.verify_helpers()
            ops.audit_old()
            # Fresh immutable-protection/original/GLM admission, not archival booleans.
            before = runtime.check('before_restore', capsule['manifest'])
            require(before['deepseek'].get('running') is False
                    and all(row.get('running') is False and row.get('memguard') == 'inactive'
                            for row in before['deepseek']['nodes'].values()),
                    'registered originals are not already stopped; no new original-service authority')
            put(directory / ('protection-before-' + uuid.uuid4().hex + '.json'), json.loads(json.dumps(before, default=str)))
            check()
            proof = old['q03_rank_logs'].quiesce_with_logs(journal, ops, old['q03_http_service'].validate_container)
            # Every new epoch and old byte is rechecked before final release.
            ops.seal_all()
            ops.audit_old()
            for row in journal.state['ranks']:
                old['q03_rank_logs'].verified_capture(journal, row, old['q03_http_service'].validate_container)
                require(ops.inspect(row['rank'], row) is None, 'candidate ID not absent at release boundary')
                guard = ops.guard_state(row['rank'], row)
                require(guard.get('absent') is True or (guard.get('owned') is True and guard.get('active') is False),
                        'candidate guard not inactive at release boundary')
            check()
            drained = runtime.check('restore_drained', capsule['manifest'])
            observation = json.loads(json.dumps(drained, default=str))
            check()
            closure = dict(schema_version=1, purpose=PURPOSE, window_id=WINDOW, attempt_id=ATTEMPT,
                capsule_sha256=CAPSULE_SHA, intent_sha256=intent_sha, recovery_only=True,
                original_http_outcome='failed-preserved', original_kept_stopped=True,
                start_permitted=False, rank_log_proof=proof, drain_and_protection=observation,
                old_evidence_sha256=EVIDENCE_SHA, finished_epoch=time.time())
            # Preserve one complete proof before release. Replacing it after a
            # crash is forbidden; retry needs a fresh final observation record.
            closure_path = directory / 'closure-proof.json'
            if closure_path.exists():
                previous = parse(read(closure_path))
                require(previous['intent_sha256'] == intent_sha and previous['rank_log_proof'] == proof,
                        'prior closure proof differs')
            else:
                put(closure_path, closure)
            stop_heartbeat.set(); thread.join(timeout=5)
            require(not thread.is_alive() and not heartbeat_errors and session.owns_hold(),
                    'heartbeat stop/ownership uncertain before release')
            def release_guard():
                pulse = snapshot(directory / 'supervisor-heartbeat.json')
                require(session.owns_hold() and time.time() < intent['authorization']['deadline_epoch']
                        and process_state(ready['supervisor']) == 'alive'
                        and pulse.get('supervisor') == ready['supervisor'] and pulse.get('intent_sha256') == intent_sha
                        and 0 <= time.time() - pulse['epoch'] <= 10, 'authority/supervision ended before release')
            publish_release(directory, state, closure_path, session.release, release_guard)
            released = True
            return state
        except BaseException as error:
            if not released and not isinstance(error, ReleasedClosureError):
                state.update(phase='UNKNOWN', error=str(error), hold_released=False)
                put(directory / 'state.json', state, replace=True)
            # After a proved release, leave the fsynced release-ready record in
            # place if final publication failed. The supervisor independently
            # reconciles absent hold + that exact durable closure proof.
            raise
        finally:
            stop_heartbeat.set(); thread.join(timeout=5)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journal-dir', required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--prepare', action='store_true')
    modes.add_argument('--apply', action='store_true')
    modes.add_argument('--supervise', action='store_true')
    parser.add_argument('--old-session')
    parser.add_argument('--evidence-file')
    parser.add_argument('--new-helper')
    parser.add_argument('--new-helper-sha256')
    parser.add_argument('--authorization-reference')
    parser.add_argument('--budget-seconds', type=int, default=7200)
    parser.add_argument('--retry', action='store_true')
    parser.add_argument('--main-pid', type=int)
    parser.add_argument('--main-start')
    parser.add_argument('--intent-sha256')
    args = parser.parse_args(argv)
    # New files are private even when the local invoking shell uses umask 0002.
    # The remote fix independently creates 0600 without relying on this setting.
    os.umask(0o077)
    if args.supervise:
        value = supervise(args.journal_dir, dict(pid=args.main_pid, start_ticks=args.main_start), args.intent_sha256)
    elif args.apply:
        value = apply(args.journal_dir, retry=args.retry)
    else:
        require(not args.retry, 'retry requires the gated apply mode')
        for key in ('old_session', 'evidence_file', 'new_helper', 'new_helper_sha256', 'authorization_reference'):
            require(getattr(args, key), 'missing --' + key.replace('_', '-'))
        value = prepare(args.old_session, args.evidence_file, args.journal_dir,
            args.new_helper, args.new_helper_sha256, args.authorization_reference, args.budget_seconds,
            apply=args.prepare)
    print(json.dumps(value, sort_keys=True))


if __name__ == '__main__':
    main()
