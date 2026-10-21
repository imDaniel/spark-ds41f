#!/usr/bin/env python3
"""Shared exact-ID evidence barrier for owner and HTTP candidate retirement.

Import is inert. No arbitrary command or new daemon action is exposed here.
Each invocation tries at most one bounded capture per uncaptured rank. Failed
reads retain the stopped containers and all partial evidence; a later recovery
may retry only after the existing remote epoch protocol proves quiescence.
"""
from __future__ import annotations
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid

from q03_held_window import exclusive_json
from q03_remote_operation import LOG_STREAM_LIMIT


def require(value, message):
    if not value:
        raise RuntimeError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_owned_bytes(path):
    """Bounded, nonblocking reread of our private evidence; never follow aliases."""
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path, 'log artifact path alias')
    for parent in (path.parent, *path.parent.parents):
        info = parent.stat()
        require(info.st_uid in (0, os.geteuid()) and not info.st_mode & 0o022,
                'log artifact ancestry is not owner-controlled')
    limit = LOG_STREAM_LIMIT if path.name in ('stdout.log', 'stderr.log') else 4 * 1024 * 1024
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
                and not before.st_mode & 0o022 and before.st_size <= limit,
                'log artifact type/owner/mode/size differs')
        raw = stream.read(limit + 1)
        after, current = os.fstat(stream.fileno()), path.lstat()
    wanted = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    require(len(raw) == before.st_size and wanted ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) ==
            (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns),
            'log artifact changed while reading')
    return raw


def load(path):
    return json.loads(read_owned_bytes(path))


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def mkdir(path):
    require(path.resolve() == path, 'log evidence path aliases another owner')
    path.mkdir(mode=0o700, exist_ok=True)
    require(path.is_dir() and path.stat().st_uid == os.geteuid()
            and not path.stat().st_mode & 0o077, 'log evidence directory must be private')
    sync_dir(path.parent)


def write_bytes(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sync_dir(path.parent)


def binding(plan, row):
    return dict(window_id=plan['window_id'], attempt_id=plan['attempt_id'],
                plan_sha256=plan['plan_sha256'], rank=row['rank'],
                container_id=row['container_id'], config_sha256=row['config_sha256'])


def stopped(item, plan, row, validate):
    validate(item, plan, row)
    state = item['State']
    require(state.get('Running') is False and type(state.get('ExitCode')) is int
            and not state.get('Restarting', False) and not state.get('Paused', False),
            'exact owned container not proven stopped with exit identity')


def validate_receipt(value, plan, row):
    capture = value.get('log_capture', {})
    require(value.get('status') == 'terminal' and value.get('window') == plan['window_id']
            and re.fullmatch(r'[0-9a-f]{32}', value.get('operation', ''))
            and re.fullmatch(r'[0-9a-f]{32}', value.get('epoch', ''))
            and re.fullmatch(r'[0-9a-f]{64}', value.get('manifest_sha256', ''))
            and value.get('group_quiescent') is True and value.get('epoch_sealed') is True,
            'log read lacks exact operation/quiescent sealed receipt')
    require(capture.get('container_id') == row['container_id']
            and capture.get('timestamps') is True
            and type(capture.get('stream_limit_bytes')) is int
            and capture['stream_limit_bytes'] == LOG_STREAM_LIMIT
            and set(capture.get('streams', {})) == {'stdout', 'stderr'},
            'log receipt identity/stream contract differs')
    for stream in capture['streams'].values():
        require(type(stream.get('bytes')) is int and 0 <= stream['bytes'] <= LOG_STREAM_LIMIT
                and re.fullmatch(r'[0-9a-f]{64}', stream.get('sha256', '')),
                'invalid log stream size/hash')
    return (value.get('action_succeeded') is True and type(value.get('exit_code')) is int
            and value['exit_code'] == 0 and value.get('cancelled') is False
            and value.get('timed_out') is False and capture.get('complete') is True
            and capture.get('truncated') is False and not capture.get('artifact_errors')
            and all(s['bytes'] < LOG_STREAM_LIMIT for s in capture['streams'].values()))


FILES = ('intent.json', 'pre-inspect.json', 'stdout.log', 'stderr.log',
         'receipt.json', 'post-inspect.json')


def capture_rank(journal, ops, row, validate):
    plan, rank = journal.plan, row['rank']
    base = journal.directory / 'rank-logs'
    mkdir(base)
    base /= str(rank)
    mkdir(base)
    generation = uuid.uuid4().hex
    directory = base / generation
    mkdir(directory)
    identity = binding(plan, row)
    exclusive_json(directory / 'intent.json', dict(identity, capture_id=generation,
                   started_epoch=time.time(), timestamped=True, stream_limit_bytes=LOG_STREAM_LIMIT))
    try:
        before = ops.inspect(rank, row)
        exclusive_json(directory / 'pre-inspect.json', before)
        stopped(before, plan, row, validate)
        receipt = ops.capture_logs(rank, row)
        metadata = copy.deepcopy(receipt)
        # Save the actual returned bytes, including failed/truncated reads. Never
        # synthesize an empty successful stream after an exception or process death.
        for stream in metadata.get('log_capture', {}).get('streams', {}).values():
            stream.pop('data_base64', None)
        exclusive_json(directory / 'receipt.json', metadata)
        for name, stream in receipt.get('log_capture', {}).get('streams', {}).items():
            require(name in ('stdout', 'stderr'), 'unknown log stream')
            encoded = stream.get('data_base64')
            require(isinstance(encoded, str) and len(encoded) <= ((LOG_STREAM_LIMIT + 2) // 3) * 4,
                    'oversized/nonbinary log response')
            raw = base64.b64decode(encoded, validate=True)
            require(len(raw) <= LOG_STREAM_LIMIT, 'oversized log response')
            write_bytes(directory / (name + '.log'), raw)
            require(len(raw) == stream['bytes'] and digest(raw) == stream['sha256'],
                    'log transport bytes/hash mismatch')
        after = ops.inspect(rank, row)
        exclusive_json(directory / 'post-inspect.json', after)
        stopped(after, plan, row, validate)
        require(before['State'] == after['State'], 'container exit identity drift during log read')
        complete = validate_receipt(metadata, plan, row)
        files = {name: dict(sha256=digest(read_owned_bytes(directory / name)),
                           bytes=(directory / name).stat().st_size) for name in FILES}
        index = dict(identity, schema_version=1, capture_id=generation, complete=complete,
                     files=files, finished_epoch=time.time())
        index_sha = exclusive_json(directory / 'index.json', index)
        require(complete, 'rank log read failed or truncated; stopped container retained')
        return dict(capture_id=generation, index_sha256=index_sha)
    except Exception as error:
        if not (directory / 'post-inspect.json').exists():
            try:
                exclusive_json(directory / 'post-inspect.json', ops.inspect(rank, row))
            except Exception as inspection_error:
                exclusive_json(directory / 'post-inspect-error.json',
                               dict(error=str(inspection_error), observed_epoch=time.time()))
        exclusive_json(directory / 'failure.json', dict(identity, capture_id=generation,
                       complete=False, error=type(error).__name__ + ': ' + str(error),
                       finished_epoch=time.time()))
        raise


def verified_capture(journal, row, validate):
    proof = row.get('log_capture')
    require(isinstance(proof, dict) and set(proof) == {'capture_id', 'index_sha256'}
            and re.fullmatch(r'[0-9a-f]{32}', proof.get('capture_id', '')),
            'missing complete rank log preservation proof')
    directory = journal.directory / 'rank-logs' / str(row['rank']) / proof['capture_id']
    raw = read_owned_bytes(directory / 'index.json')
    require(digest(raw) == proof['index_sha256'], 'retained log index digest differs')
    index = json.loads(raw)
    require(all(index.get(k) == v for k, v in binding(journal.plan, row).items())
            and index.get('complete') is True and index.get('capture_id') == proof['capture_id']
            and set(index.get('files', {})) == set(FILES), 'retained log index identity/inventory differs')
    for name, item in index['files'].items():
        data = read_owned_bytes(directory / name)
        require(len(data) == item['bytes'] and digest(data) == item['sha256'], 'retained log artifact differs')
    intent = load(directory / 'intent.json')
    require(all(intent.get(k) == v for k, v in binding(journal.plan, row).items())
            and intent.get('capture_id') == proof['capture_id'], 'retained log intent differs')
    before, after = (load(directory / name) for name in ('pre-inspect.json', 'post-inspect.json'))
    stopped(before, journal.plan, row, validate)
    stopped(after, journal.plan, row, validate)
    require(before['State'] == after['State'], 'retained log exit identity differs')
    receipt = load(directory / 'receipt.json')
    require(validate_receipt(receipt, journal.plan, row), 'retained log read was not complete')
    for name, stream in receipt['log_capture']['streams'].items():
        require(index['files'][name + '.log'] == dict(bytes=stream['bytes'], sha256=stream['sha256']),
                'retained stream not bound to read receipt')
    return after


def quiesce_with_logs(journal, ops, validate):
    """All-four STOP, all-four durable evidence, then exact-ID removal. No START."""
    plan, records = journal.plan, journal.state['ranks']
    require(len(records) == 4 and all(type(r.get('rank')) is int for r in records)
            and [r['rank'] for r in records] == list(range(4))
            and len({r['container_id'] for r in records if r['container_id']})
                == len([r for r in records if r['container_id']]), 'duplicate/foreign rank bindings')
    ops.check_authority()
    ops.seal_all()
    errors = []
    # Do not let one unavailable daemon/guard prevent STOP on another known rank.
    # Nothing reads logs or removes a container in this safety phase.
    for row in records:
        try:
            if row['container_id'] is None:
                events = [load(p) for p in journal.directory.glob('[0-9]*.json')]
                require(not any(e['kind'] == 'create-intent' and e['rank'] == row['rank'] for e in events),
                        'create intent lacks a durable ID: UNKNOWN')
                continue
            item = ops.inspect(row['rank'], row)
            if item is not None:
                validate(item, plan, row)
                if item['State'].get('Running') is True:
                    ops.action(row['rank'], 'stop', row)
                    item = ops.inspect(row['rank'], row)
                stopped(item, plan, row, validate)
            if row['guard_intent']:
                state = ops.guard_state(row['rank'], row)
                if state.get('absent') is not True:
                    require(state.get('owned') is True, 'foreign/unknown guard cannot be stopped')
                    if state.get('active') is True:
                        ops.action(row['rank'], 'stop_guard', row)
                state = ops.guard_state(row['rank'], row)
                require(state.get('absent') is True or
                        (state.get('owned') is True and state.get('active') is False),
                        'owned guard not proven inactive')
        except Exception as error:
            errors.append(dict(rank=row['rank'], error=str(error)))
    journal.event('daemon-stop-check', daemon_quiescent=not errors, errors=errors)
    require(not errors, 'all-four daemon/guard drain unproven: ' + json.dumps(errors))
    # A stopped daemon is not the same as retained diagnostics or GPU drain.
    errors = []
    for row in records:
        if row['container_id'] is None:
            continue
        try:
            if not row.get('log_capture'):
                row['log_capture'] = capture_rank(journal, ops, row, validate)
                journal.save()  # Durable proof BEFORE any possible removal.
            after = verified_capture(journal, row, validate)
            current = ops.inspect(row['rank'], row)
            if current is not None:
                stopped(current, plan, row, validate)
                require(current['State'] == after['State'], 'container restarted after log preservation')
        except Exception as error:
            errors.append(dict(rank=row['rank'], error=str(error)))
    journal.event('log-preservation-check', daemon_quiescent=True, logs_complete=not errors, errors=errors)
    require(not errors, 'rank logs incomplete; stopped containers retained: ' + json.dumps(errors))
    index = dict(schema_version=1, window_id=plan['window_id'], attempt_id=plan['attempt_id'],
                 plan_sha256=plan['plan_sha256'], node_count=4, logs_complete=True,
                 ranks=[dict(binding(plan, r), capture=r.get('log_capture'),
                             not_created=r['container_id'] is None) for r in records])
    path = journal.directory / 'rank-logs-index.json'
    if path.exists():
        require(load(path) == index, 'all-four log preservation index changed')
    else:
        exclusive_json(path, index)
    index_sha = digest(read_owned_bytes(path))
    for row in records:
        if row['container_id'] is None:
            continue
        # Verify bytes again at the destructive boundary, including retry after a
        # controller died between rm and recording its successful absence.
        after = verified_capture(journal, row, validate)
        current = ops.inspect(row['rank'], row)
        if current is not None:
            stopped(current, plan, row, validate)
            require(current['State'] == after['State'], 'container exit identity changed before remove')
            ops.action(row['rank'], 'remove', row)
        require(ops.inspect(row['rank'], row) is None, 'exact owned ID still present')
        row['removed'] = True
        journal.save()
    ops.seal_all()
    journal.phase('drained')
    proof = dict(window_id=plan['window_id'], attempt_id=plan['attempt_id'], node_count=4,
                 daemon_quiescent=True, gpu_drain_required_by_caller=True,
                 plan_sha256=plan['plan_sha256'], logs_complete=True, log_index_sha256=index_sha)
    path = journal.directory / 'drained.json'
    if path.exists():
        require(load(path) == proof, 'retained drained proof differs')
    else:
        exclusive_json(path, proof)
    return proof
