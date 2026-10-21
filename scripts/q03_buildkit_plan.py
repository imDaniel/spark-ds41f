"""Plan/provision one isolated local BuildKit builder; no build or service actions.

Operator: plan first, then --apply on the authorized head before service stop.
Supply a reviewed official moby/buildkit@sha256 digest. Retain all resources and
receipts on failure; this tool never removes or overwrites them. Export all four
returned build_environment keys for the later guarded bin/spark build. Dedicated
cache state is not an unprivileged sandbox: docker-container BuildKit may run
privileged under the local Docker daemon. The daemon permits network.host because
the unchanged project build explicitly requests --network host. The BuildKit
container itself remains on bridge networking.

Docker references:
https://docs.docker.com/reference/cli/docker/buildx/create/
https://docs.docker.com/build/builders/drivers/docker-container/
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ENDPOINT = 'unix:///var/run/docker.sock'


def make_plan(identity, root, buildkit_image, *, independent_root):
    if not re.fullmatch(r'q03-[a-z0-9][a-z0-9-]{2,47}', identity):
        raise ValueError('invalid independent experiment identity')
    root = Path(root).resolve()
    approved_root = Path(independent_root).resolve()
    if not Path(independent_root).is_absolute() or approved_root == Path('/'):
        raise ValueError('reviewed absolute independent root required')
    if not root.is_relative_to(approved_root) or root == approved_root:
        raise ValueError('builder metadata must be inside approved independent root')
    if not re.fullmatch(r'moby/buildkit@sha256:[0-9a-f]{64}', buildkit_image):
        raise ValueError('reviewed official BuildKit image digest required')
    node = identity + '0'
    config = root / 'buildkitd.toml'
    env = {'BUILDX_CONFIG': str(root / 'buildx'), 'BUILDX_BUILDER': identity,
           'DOCKER_CONFIG': str(root / 'docker'), 'DOCKER_HOST': ENDPOINT}
    docker = ['docker', '--host', ENDPOINT]
    create = docker + ['buildx', 'create', '--name', identity, '--node', node,
                       '--driver', 'docker-container', '--driver-opt', 'image=' + buildkit_image,
                       '--driver-opt', 'network=bridge', '--driver-opt', 'restart-policy=no',
                       '--driver-opt', 'env.NVIDIA_VISIBLE_DEVICES=void',
                       '--buildkitd-config', str(config), '--buildkitd-flags',
                       '--oci-worker-net bridge --allow-insecure-entitlement network.host',
                       ENDPOINT]
    return {'schema_version': 1, 'identity': identity, 'root': str(root), 'independent_root': str(approved_root),
            'node': node, 'container': 'buildx_buildkit_' + node,
            'volume': 'buildx_buildkit_' + node + '_state', 'image': buildkit_image,
            'build_environment': env, 'create': create,
            'bootstrap': docker + ['buildx', 'inspect', identity, '--bootstrap'],
            'docker': docker,
            'scope': 'builder provisioning only; no GPU build, service stop or restoration'}


def verify_state(plan, builder, container, volume, *, require_running=True):
    if builder.get('Name') != plan['identity'] or builder.get('Driver') != 'docker-container':
        raise ValueError('builder identity/driver mismatch')
    nodes = builder.get('Nodes', [])
    if len(nodes) != 1 or nodes[0].get('Name') != plan['node'] or nodes[0].get('Endpoint') != ENDPOINT:
        raise ValueError('builder node/endpoint mismatch')
    if require_running and nodes[0].get('Status') != 'running':
        raise ValueError('builder not running')
    if container.get('Name') != '/' + plan['container'] or container.get('Config', {}).get('Image') != plan['image']:
        raise ValueError('BuildKit container identity mismatch')
    if require_running and not container.get('State', {}).get('Running'):
        raise ValueError('BuildKit container not running')
    if 'NVIDIA_VISIBLE_DEVICES=void' not in container.get('Config', {}).get('Env', []):
        raise ValueError('BuildKit GPU visibility not disabled')
    host = container.get('HostConfig', {})
    if host.get('NetworkMode') != 'bridge' or host.get('RestartPolicy', {}).get('Name') != 'no':
        raise ValueError('unexpected builder network/restart policy')
    if host.get('Binds') or host.get('Devices') or host.get('DeviceRequests') or host.get('VolumesFrom') or host.get('PortBindings'):
        raise ValueError('unexpected host/GPU resources')
    mounts = container.get('Mounts', [])
    if len(mounts) != 1 or mounts[0].get('Type') != 'volume' or mounts[0].get('Name') != plan['volume'] or mounts[0].get('Destination') != '/var/lib/buildkit' or mounts[0].get('RW') is not True:
        raise ValueError('builder must use exactly its new state volume')
    if volume.get('Name') != plan['volume'] or volume.get('Driver') != 'local' or volume.get('Options') not in (None, {}):
        raise ValueError('unexpected volume driver/options')
    created = container.get('Created')
    if not isinstance(created, str) or not created:
        raise ValueError('missing observed container creation timestamp')
    projection = {k: container[k] for k in ('Config', 'HostConfig', 'Mounts')}
    config_sha256 = hashlib.sha256(json.dumps(projection, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    labels = container.get('Config', {}).get('Labels') or {}
    if not isinstance(labels, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in labels.items()):
        raise ValueError('invalid observed container labels')
    return {'verified': True, 'container_id': container['Id'], 'volume': plan['volume'],
            'ownership_labels': dict(labels), 'created': created,
            'config_sha256': config_sha256,
            'build_environment': plan['build_environment']}


def verify_owned_state(plan, container, volume, expected_result):
    """Immutable identity check for cleanup, including a stopped/crashed builder.

    Caller must separately bind this plan/result to the pinned fresh-provision
    receipt and cleanup journal. This function performs no removal or Docker call.
    """
    if plan != make_plan(plan['identity'], plan['root'], plan['image'], independent_root=plan['independent_root']):
        raise ValueError('noncanonical builder plan')
    builder = {'Name': plan['identity'], 'Driver': 'docker-container',
               'Nodes': [{'Name': plan['node'], 'Endpoint': ENDPOINT}]}
    observed = verify_state(plan, builder, container, volume, require_running=False)
    for key in ('container_id', 'volume', 'created', 'config_sha256', 'ownership_labels'):
        if key not in expected_result or observed[key] != expected_result[key]:
            raise ValueError('builder cleanup identity mismatch: ' + key)
    return observed


def clean_environment(plan):
    # Do not inherit remote Docker/context selectors or a different Buildx driver.
    env = {k: v for k, v in os.environ.items() if not k.startswith(('DOCKER_', 'BUILDX_', 'BUILDKIT_'))}
    env.update(plan['build_environment'])
    return env


def provision(plan, run):
    """run(argv) returns stdout, raising on nonzero; injectable for CPU fakes."""
    if plan != make_plan(plan['identity'], plan['root'], plan['image'], independent_root=plan['independent_root']):
        raise ValueError('noncanonical builder plan')
    root = Path(plan['root'])
    if root.exists() or root.is_symlink():
        raise ValueError('builder metadata path already exists')
    docker = plan['docker']
    # List commands must succeed. A failed inspect is not proof of absence.
    for argv, name in ((docker + ['ps', '-a', '--format', '{{.Names}}'], plan['container']),
                       (docker + ['volume', 'ls', '--format', '{{.Name}}'], plan['volume'])):
        if name in run(argv).splitlines():
            raise ValueError('independent resource already exists: ' + name)
    root = Path(plan['root'])
    root.mkdir(parents=False, exist_ok=False)
    (root / 'buildx').mkdir()
    (root / 'docker').mkdir()
    (root / 'buildkitd.toml').write_text('# Independent builder; no registry mirrors or external cache.\n')
    # Fresh metadata cannot contain an existing builder; still check successful ls.
    rows = run(docker + ['buildx', 'ls', '--format', '{{.Name}}']).splitlines()
    if plan['identity'] in [r.rstrip('*') for r in rows]:
        raise ValueError('builder already exists')
    run(plan['create'])
    run(plan['bootstrap'])
    receipt = verify_live(plan, run)
    with (root / 'provision-receipt.json').open('x') as f:
        json.dump({'plan': plan, 'result': receipt,
                   'preabsence': {'container': True, 'volume': True, 'metadata': True}}, f, indent=2)
        f.write('\n')
    return receipt


def parse_builder_inspect(text):
    # inspect has no documented --format. Fail closed on missing/duplicate fields.
    before, marker, after = text.partition('Nodes:')
    if not marker:
        raise ValueError('missing builder nodes')
    def one(part, key):
        values = re.findall(r'^' + key + r':\s*(.+)$', part, re.MULTILINE)
        if len(values) != 1:
            raise ValueError('missing/duplicate builder field: ' + key)
        return values[0].strip()
    return {'Name': one(before, 'Name'), 'Driver': one(before, 'Driver'),
            'Nodes': [{'Name': one(after, 'Name'), 'Endpoint': one(after, 'Endpoint'),
                       'Status': one(after, 'Status')}]}


def verify_live(plan, run):
    """Read-only reinspection before each build; run(argv)->stdout must fail closed."""
    if plan != make_plan(plan['identity'], plan['root'], plan['image'], independent_root=plan['independent_root']):
        raise ValueError('noncanonical builder plan')
    if (Path(plan['root']) / 'buildkitd.toml').read_text() != '# Independent builder; no registry mirrors or external cache.\n':
        raise ValueError('BuildKit daemon configuration changed')
    docker = plan['docker']
    builder = parse_builder_inspect(run(docker + ['buildx', 'inspect', plan['identity']]))
    container = json.loads(run(docker + ['container', 'inspect', plan['container']]))[0]
    volume = json.loads(run(docker + ['volume', 'inspect', plan['volume']]))[0]
    return verify_state(plan, builder, container, volume)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--identity', required=True)
    ap.add_argument('--root', required=True)
    ap.add_argument('--independent-root', required=True)
    ap.add_argument('--buildkit-image', required=True)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    plan = make_plan(args.identity, args.root, args.buildkit_image, independent_root=args.independent_root)
    if not args.apply:
        print(json.dumps(plan, indent=2))
        return
    # Path owner/disk authorization is an operator preflight prerequisite.
    if Path(plan['root']).exists() or Path(plan['root']).is_symlink():
        raise ValueError('builder metadata path already exists')
    env = clean_environment(plan)
    log = []
    def run(argv):
        result = subprocess.run(argv, env=env, text=True, capture_output=True, timeout=300)
        log.append({'argv': argv, 'returncode': result.returncode,
                    'stdout': result.stdout, 'stderr': result.stderr})
        if result.returncode:
            raise RuntimeError('Docker command failed: ' + ' '.join(argv))
        return result.stdout
    try:
        print(json.dumps(provision(plan, run), indent=2))
    finally:
        root = Path(plan['root'])
        if root.is_dir():
            # Exclusive creation: never replace evidence from another attempt.
            with (root / 'provision-log.json').open('x') as f:
                json.dump(log, f, indent=2)
                f.write('\n')


if __name__ == '__main__':
    main()
