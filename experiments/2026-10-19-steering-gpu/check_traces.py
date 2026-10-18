"""Check trace completeness and named steering events; never infer all-copy absence."""
import argparse
import collections
import gzip
import hashlib
import json
import tarfile
from pathlib import Path

NODES = {1, 2, 3, 4}
COHORTS = {('prefill', 0), ('prefill', 1), ('decode', 0), ('decode', 1)}
STEERING_KERNELS = ('_row_scales_kernel', '_dot_kernel', '_project_kernel')


def validate_trace(entry, raw):
    if hashlib.sha256(raw).hexdigest() != entry['sha256']:
        raise ValueError('Trace file SHA mismatch')
    trace = json.loads(gzip.decompress(raw))
    distributed = trace.get('distributedInfo', {})
    if (type(distributed.get('rank')) is not int or type(distributed.get('world_size')) is not int
            or distributed['rank'] != entry['node'] - 1 or distributed['world_size'] != 4):
        raise ValueError('Trace rank/world does not match the manifest')
    events = trace.get('traceEvents')
    if not isinstance(events, list):
        raise TypeError('Missing trace events')
    event_digest = hashlib.sha256(json.dumps(events, separators=(',', ':')).encode()).hexdigest()
    if event_digest != entry['events_sha256']:
        raise ValueError('Trace event SHA mismatch')
    kernels = collections.Counter(e.get('name') for e in events if e.get('cat') == 'kernel')
    cpu = collections.Counter(e.get('name') for e in events if e.get('cat') == 'cpu_op')
    if not kernels or not cpu:
        raise ValueError('Trace needs nonempty GPU kernel and CPU-op events')
    graph_launches = sum(e.get('name') == 'cudaGraphLaunch' and e.get('cat') == 'cuda_runtime'
                         and e.get('ph') == 'X' and isinstance(e.get('args'), dict)
                         and type(e.get('dur')) in (int, float) and e['dur'] > 0
                         for e in events)
    if entry['stage'] == 'decode' and not graph_launches:
        raise ValueError('Decode trace has no graph replay evidence')
    steering = {k: v for k, v in kernels.items() if any(p in k for p in STEERING_KERNELS)}
    ops = {k: v for k, v in cpu.items() if k.startswith('ds41_steering::')}
    if entry['scale'] == 0 and (steering or ops):
        raise ValueError('Stock trace contains forbidden named steering events')
    if entry['scale'] == 1 and not steering:
        raise ValueError('Active trace lacks a steering positive control')
    categories = collections.Counter(e.get('cat') for e in events if e.get('cat'))
    return {'node': entry['node'], 'stage': entry['stage'], 'scale': entry['scale'],
            'trace': entry['path'], 'sha256': entry['sha256'], 'events': len(events),
            'kernels': sum(kernels.values()), 'cpu_ops': sum(cpu.values()),
            'cuda_graph_launches': graph_launches, 'steering_kernels': steering,
            'steering_ops': ops, 'event_categories': dict(categories)}


def check_manifest(manifest, read_bytes):
    entries = manifest['traces']
    expected = {(n, stage, scale) for n in NODES for stage, scale in COHORTS}
    keys = [(e['node'], e['stage'], e['scale']) for e in entries]
    if (len(keys) != len(expected) or set(keys) != expected
            or any(type(e['node']) is not int or type(e['scale']) is not int for e in entries)
            or len({e['path'] for e in entries}) != len(entries)):
        raise ValueError('Expected all sixteen unique node/stage/scale traces')
    for identity in ('source_trace_sha256', 'source_trace_name', 'sha256', 'events_sha256'):
        if len({e[identity] for e in entries}) != 16:
            raise ValueError('Repeated trace capture identity: ' + identity)
    results = []
    for entry in entries:
        if Path(entry['path']).name != entry['path']:
            raise ValueError('Trace paths must be plain archive member names')
        results.append(validate_trace(entry, read_bytes(entry['path'])))
    return {'capture_completeness': 'passed', 'known_steering_event_gate': 'passed',
            'generic_copy_launch_sp_materialization': 'not_evaluated',
            'matched_unloaded_or_official_trace_control': 'not_available',
            'scope': 'named row/dot/project kernels and steering custom ops only',
            'traces': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    if hashlib.sha256(args.archive.read_bytes()).hexdigest() != manifest['archive_sha256']:
        raise ValueError('Trace archive SHA mismatch')
    with tarfile.open(args.archive) as archive:
        def read(name):
            member = archive.getmember(name)
            if not member.isfile():
                raise ValueError('Trace member is not a regular file')
            return archive.extractfile(member).read()
        result = check_manifest(manifest, read)
    # Verification never overwrites a previous acceptance receipt.
    with args.output.open('x') as out:
        json.dump(result, out, indent=2)
        out.write('\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'traces'}))


if __name__ == '__main__':
    main()
