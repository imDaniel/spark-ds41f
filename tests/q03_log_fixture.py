"""Synthetic log receipt boundary for real owner/HTTP lifecycle CPU tests."""
import base64
import hashlib
from q03_remote_operation import LOG_STREAM_LIMIT


def capture_logs(ops, rank, row):
    ops.events.append(('logs', rank))
    assert all(not item['State']['Running'] for item in ops.items.values())
    assert all(not guard['active'] for guard in ops.guards.values())
    streams = {}
    for name in ('stdout', 'stderr'):
        raw = f'2026-10-21T12:00:00.000000000Z rank={rank} {name}\n'.encode()
        streams[name] = dict(data_base64=base64.b64encode(raw).decode(), bytes=len(raw),
                             sha256=hashlib.sha256(raw).hexdigest())
    return dict(status='terminal', window=ops.plan['window_id'], operation='e'*32, epoch='f'*32,
                manifest_sha256='a'*64, group_quiescent=True, epoch_sealed=True,
                action_succeeded=True, exit_code=0, cancelled=False, timed_out=False,
                log_capture=dict(container_id=row['container_id'], timestamps=True,
                    stream_limit_bytes=LOG_STREAM_LIMIT, complete=True, truncated=False,
                    artifact_errors={}, streams=streams))
