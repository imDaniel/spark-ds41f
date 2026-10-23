"""Offline recovery review. This program cannot execute cluster commands.

The inventory is evidence, not a deployment authority. The frozen policy names
the accepted service; all gates must be refreshed before a later window.
"""
import argparse
import json
import pathlib
import shlex

EXP = pathlib.Path(__file__).resolve().parents[1]


def require(value, message):
    if not value:
        raise ValueError(message)


def validate_inventory(policy, inventory):
    nodes = inventory['nodes']
    require(len(nodes) == 4 and {n.get('node') for n in nodes} == {1, 2, 3, 4},
            'Exactly four distinct reachable ranks are required')
    for node in nodes:
        require(not node.get('error'), 'Unreachable rank')
        for key in ('repository', 'head', 'config', 'profile_path', 'profile_sha256'):
            require(node.get(key) == policy[key], f"Rank {node['node']}: {key} mismatch")
        require(not node.get('tracked_changes'), 'Dirty retained source checkout')
        require(node.get('file_hashes') == policy['file_hashes'], 'Retained configuration/source changed')
        require(node['container']['image'] == policy['image_id'], 'Retained image mismatch')
        require(node['container']['source_tree'] == policy['vllm_tree'], 'Retained source tree mismatch')
        require(node['weights_present'] == 48, 'Checkpoint shard inventory incomplete')
        mounts = {m['Destination']: (m['Source'], m['RW']) for m in node['container']['mounts']}
        require(mounts == {m['Destination']: (m['Source'], m['RW']) for m in policy['mounts']},
                'Retained mount identity changed')


def command(policy, action):
    args = [policy['repository'] + '/bin/spark', '--shape', 'tp4-ring',
            '--cluster-config', policy['config']]
    if action == 'doctor':
        return args + ['doctor', '--live']
    args += ['cluster', action]
    if action == 'start':
        args += ['--replace', '--unqualified-shape']
    return args


def restore_decision(*, owned_hold, all_ranks_reachable, foreign_gpu_jobs,
                     candidate_running, retained_running_ranks, retained_ready,
                     candidate_stop_verified=False):
    """Fail closed on ambiguity; stop every rank before restoring a partial set."""
    require(owned_hold, 'The window does not own the hold')
    require(all_ranks_reachable, 'Restore requires all ranks; escalate management loss')
    require(not foreign_gpu_jobs, 'Unowned GPU jobs require operator review')
    require(0 <= retained_running_ranks <= 4, 'Invalid retained rank count')
    if candidate_running:
        return ['stop_candidate_all_ranks', 'verify_candidate_stopped', 'reobserve']
    if retained_running_ranks == 4 and retained_ready:
        return ['verify_retained_identity_and_api', 'release_own_hold']
    if retained_running_ranks:
        return ['stop_retained_all_ranks', 'verify_retained_stopped', 'reobserve']
    require(candidate_stop_verified, 'Do not start while candidate stop is unverified')
    return ['start_retained_all_ranks', 'doctor_live', 'verify_identity_and_api',
            'verify_gateway_aliases', 'release_own_hold']


def may_open_window(*, owner_authorized, assets_on_all_ranks, published_revision,
                    watchdog_selftest, hold_absent, requests_running, requests_waiting,
                    idle_seconds):
    require(owner_authorized, 'A later explicit runtime authorization is required')
    require(assets_on_all_ranks and published_revision, 'Stage and verify a published frozen release first')
    require(watchdog_selftest, 'Independent recovery watchdog must be installed and dry-tested')
    require(hold_absent, 'Another holder owns the cluster')
    require(requests_running is not None and requests_waiting is not None, 'Missing queue telemetry is not idle')
    require(requests_running == requests_waiting == 0 and idle_seconds >= 30, 'Requests are not drained')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=pathlib.Path, required=True)
    args = parser.parse_args()
    policy = json.loads((EXP / 'recovery-policy.json').read_text())
    validate_inventory(policy, json.loads(args.inventory.read_text()))
    print('Retained source, image, configuration, mounts and profile match on all four ranks.')
    print('Plans only; no subprocess or remote execution exists in this tool.')
    for action in ('stop', 'start', 'doctor'):
        print(action + ': ' + shlex.join(command(policy, action)))


if __name__ == '__main__':
    main()
