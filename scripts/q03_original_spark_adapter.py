#!/usr/bin/env python3
"""Fixed-policy original service transition; only failure evidence is redirected.

Run on the approved head by the independent window owner, never from cloud.
No original source/config bytes are edited. No publication/network dependency is
introduced into recovery. This adapter does not acquire or release the hold.
"""
import argparse
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import stat
from pathlib import Path
import re
import shlex
import sys
import subprocess
import time
import uuid
import types



def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def verify_hash(path, expected):
    require(re.fullmatch(r'[0-9a-f]{64}', expected) is not None, 'invalid expected digest')
    data = path.read_bytes()
    require(hashlib.sha256(data).hexdigest() == expected, f'protected identity drift: {path}')
    return data


def evidence_path(path, manifest):
    path = Path(path)
    independent = Path(manifest['independent_root'])
    require(path.is_absolute() and independent.is_absolute(), 'paths must be absolute')
    require(independent.resolve() == independent, 'independent root has an alias')
    protected = manifest['preflight']['protected_roots']
    require(isinstance(protected, list) and bool(protected), 'protected roots must be explicit')
    pre = manifest['preflight']
    protected = protected + [pre[k] for k in ('hold_path', 'request_path',
                                'legacy_hold_path', 'legacy_request_path', 'policy_path')]
    protected.append(pre['registered_policy']['repository'])
    lexical, resolved = path.absolute(), path.resolve()
    for value in protected:
        root = Path(value)
        require(root.is_absolute(), 'protected root must be absolute')
        for candidate in (lexical, resolved, independent):
            require(not candidate.is_relative_to(root) and not root.is_relative_to(candidate),
                    'evidence path overlaps protected namespace')
    require(resolved.is_relative_to(independent) and resolved != independent,
            'evidence must be inside independent validation root')
    return resolved


def owned_hold(path, window_id, expected_path, holder):
    require(bool(window_id) and re.fullmatch(r'[A-Za-z0-9_-]+', window_id), 'invalid window identity')
    require(Path(path) == Path(expected_path), 'unexpected hold path')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode), 'hold is not regular')
        with os.fdopen(os.dup(fd), 'r') as handle:
            value = json.load(handle)
        after = Path(path).lstat()
        require((before.st_dev, before.st_ino) == (after.st_dev, after.st_ino), 'hold inode changed')
        require(value.get('window') == window_id and
                value.get('holder') == holder,
                'hold is absent or owned by another window')
        return value
    finally:
        os.close(fd)


def install_readonly_hooks(original, destination, dispatcher=None):
    """Suppress incidental original-tree Git index/bytecode writes, not guards."""
    ssh, shell = original.run_ssh, original.remote_shell
    def readonly_ssh(nodes, node, *command):
        if dispatcher is not None:
            return dispatcher(nodes, node, *command)
        return ssh(nodes, node, 'env', 'GIT_OPTIONAL_LOCKS=0',
                   'PYTHONDONTWRITEBYTECODE=1', *command)
    def readonly_shell(nodes, node, script):
        return shell(nodes, node,
                     'export GIT_OPTIONAL_LOCKS=0 PYTHONDONTWRITEBYTECODE=1; ' + script)
    original.run_ssh = readonly_ssh
    original.remote_shell = readonly_shell
    original.preserve_failed_start_logs = redirected_log_writer(original, destination)


def transition_args(policy, action, apply):
    require(action in ('stop', 'start'), 'only registered stop/start supported')
    args = ['--shape', 'tp4-ring', '--cluster-config', policy['cluster_config'], 'cluster', action]
    if action == 'start':
        # Exact existing registered controller flags, not a topology change.
        args += ['--replace', '--unqualified-shape']
    if apply:
        args.append('--apply')
    return args


def redirected_log_writer(original, destination):
    """Never let log failure interrupt the original rollback; return None.

    The registered original source formats a non-None result with relative_to(original ROOT).
    Returning an external Path would raise before its rollback, so emit the
    external receipt ourselves and leave that formatting branch unused.
    """
    def preserve(cluster, nodes, started, cluster_config):
        if not started:
            return None
        try:
            target = destination / ('failed-start-' + uuid.uuid4().hex)
            target.mkdir(parents=True, exist_ok=False)
            name = cluster['container']['name']
            for index, node in enumerate(started):
                label = re.sub(r'[^A-Za-z0-9_.-]', '_', str(node['name']))
                try:
                    state = original.run_ssh(nodes, node, 'docker', 'inspect', name)
                    logs = original.remote_shell(nodes, node,
                                                 f'docker logs --timestamps {shlex.quote(name)} 2>&1')
                    text = (f'node: {node["name"]}\ncontainer: {name}\n'
                            f'inspect_returncode: {state.returncode}\n\n[docker inspect]\n'
                            f'{state.stdout}{state.stderr}\n[docker logs]\n{logs.stdout}{logs.stderr}')
                except Exception as error:
                    text = f'failure evidence capture error: {type(error).__name__}: {error}\n'
                (target / f'{index}-{label}.log').write_text(text, encoding='utf-8')
            print(json.dumps({'redirected_failure_evidence': str(target)}), flush=True)
        except Exception as error:
            print(f'failure evidence unavailable; original rollback continues: {error}', file=sys.stderr)
        return None
    return preserve


def readonly_probe(command, cluster):
    """Exact reviewed probe vectors only; arbitrary shells never get a bypass."""
    command = tuple(command)
    name = cluster['container']['name']
    image = cluster['container']['image']
    unit = name + '-memguard.service'
    exact = {
        ('docker', 'inspect', name),
        ('docker', 'image', 'inspect', image),
        ('docker', 'inspect', '--format',
         '{{.State.Running}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}}', name),
        ('docker', 'logs', '--tail', '20', name),
        ('systemctl', 'is-active', '--quiet', unit),
        ('sudo', '-n', 'true'),
        ('curl', '--fail', '--silent', '--show-error', '--max-time', '3',
         cluster['deployment']['ready_url']),
    }
    log_script = f'docker logs --timestamps {shlex.quote(name)} 2>&1'
    exact.add(('bash', '-lc', log_script))
    exact.add(('bash', '-lc',
               'export GIT_OPTIONAL_LOCKS=0 PYTHONDONTWRITEBYTECODE=1; ' + log_script))
    return command in exact


def load_remote_helper(expected_sha256):
    """Load only the hash-pinned sibling under isolated Python, without sys.path."""
    source = Path(__file__).resolve().with_name('q03_remote_operation.py')
    raw = verify_hash(source, expected_sha256)
    module = types.ModuleType('_q03_verified_remote_operation')
    module.__file__ = str(source)
    exec(compile(raw, str(source), 'exec'), module.__dict__)
    return module


class RemoteOperations:
    """Transport is injectable; constructing this object performs no SSH."""
    def __init__(self, original, cluster, nodes, capsule, window, epoch, destination,
                 transport=None, now=None, sleep=None):
        self.original, self.cluster, self.nodes = original, cluster, nodes
        self.window, self.epoch = uuid.UUID(window).hex, uuid.UUID(epoch).hex
        self.destination = destination
        manifest = capsule['manifest']
        self.independent = Path(manifest['independent_root'])
        pre = manifest['preflight']
        self.helper = Path(pre['remote_helper_path'])
        self.helper_sha = pre['remote_helper_sha256']
        self.authority = Path(pre['remote_authority_path'])
        self.authority_sha = pre['remote_authority_sha256']
        require(self.authority.is_absolute() and self.authority.is_relative_to(self.independent),
                'remote authority must be in independent namespace')
        require(self.helper.is_absolute() and self.helper.is_relative_to(self.independent),
                'remote helper must be in independent namespace')
        require(self.helper_sha == capsule['recovery_source_sha256']['q03_remote_operation.py'],
                'remote helper differs from immutable recovery source')
        self.helper_module = load_remote_helper(self.helper_sha)
        self.root = self.independent / 'remote-operations'
        self.timeout = min(3650, capsule['recovery_timeout_s'])
        self.transport = transport or self._transport
        self.now, self.sleep = now or time.monotonic, sleep or time.sleep
        self.blocked = False
        self.rpc_timeout = 30
        require(len(nodes['nodes']) == 4, 'registered transition requires all four configured nodes')

    def _transport(self, nodes, node, *command):
        return subprocess.run(['ssh', *self.original.ssh_options(),
                               self.original.ssh_target(nodes, node), shlex.join(command)],
                              text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=self.rpc_timeout, check=False)

    def verify_helpers(self):
        for node in self.nodes['nodes']:
            value = self.helper_call(node, 'verify')
            require(value.get('helper_sha256') == self.helper_sha
                    and value.get('authority_sha256') == self.authority_sha,
                    'remote helper or private authority identity drift')

    def helper_call(self, node, action, operation=None, extra=()):
        argv = [action, '--root', str(self.root), '--window', self.window, '--epoch', self.epoch,
                '--authority', str(self.authority), '--authority-sha256', self.authority_sha]
        if operation:
            argv += ['--operation', operation]
        wire = self.helper_module.verified_helper_command(
            self.helper, self.helper_sha, [*argv, *extra])
        wire[0] = 'python3'  # remote interpreter, not a local virtualenv path
        result = self.transport(self.nodes, node, *wire)
        require(result.returncode == 0, 'remote operation status unavailable: ' + result.stderr)
        value = json.loads(result.stdout)
        require(value.get('window') == self.window, 'remote receipt window mismatch')
        if operation:
            require(value.get('operation') == operation, 'remote receipt operation mismatch')
        else:
            require(value.get('epoch') == self.epoch, 'remote receipt epoch mismatch')
        return value

    def quiesce(self):
        # No later launches through this object once its epoch is fenced.
        self.blocked = True
        results, errors = {}, []
        for node in self.nodes['nodes']:
            try:
                results[node['name']] = self.helper_call(node, 'seal')
            except Exception as error:
                errors.append(str(node['name']) + ': ' + str(error))
        require(not errors, '; '.join(errors))
        deadline = self.now() + 60
        try:
            while True:
                pending = []
                for node in self.nodes['nodes']:
                    value = results[node['name']]
                    require(value.get('sealed') is True, 'remote epoch was not sealed')
                    if value.get('safe_to_overlap') is True:
                        continue
                    operations = value.get('operations', [])
                    require(bool(operations) and all(
                        item.get('safe_to_overlap') is True or item.get('status') == 'running'
                        for item in operations), 'epoch has unfinished or failed/missing/uncertain receipts')
                    pending.append(node)
                if not pending:
                    return list(results.values())
                require(self.now() < deadline, 'sealed epoch still running after bounded wait')
                self.sleep(min(.2, max(0, deadline - self.now())))
                for node in pending:
                    remaining = deadline - self.now()
                    require(remaining > 0, 'sealed epoch still running after bounded wait')
                    self.rpc_timeout = min(30, remaining)
                    results[node['name']] = self.helper_call(node, 'seal')
        finally:
            self.rpc_timeout = 30

    def run_ssh(self, nodes, node, *command):
        require(nodes == self.nodes and node in self.nodes['nodes'], 'unexpected node map')
        argv = self.helper_module.bound_command(command)
        if readonly_probe(command, self.cluster):
            return self.transport(nodes, node, *argv)
        require(not self.blocked, 'prior remote operation uncertain; refusing further mutations')
        operation = uuid.uuid4().hex
        self.destination.mkdir(parents=True, exist_ok=True)
        self.helper_module.atomic(self.destination / ('remote-operation-' + operation + '.json'),
               dict(window=self.window, epoch=self.epoch, operation=operation,
                    node=node['name'], argv_sha256=hashlib.sha256(json.dumps(argv).encode()).hexdigest()))
        try:
            value = self.helper_call(node, 'launch', operation, (
                '--argv-json', json.dumps(argv), '--cwd', str(self.independent),
                '--timeout-seconds', str(self.timeout)))
            deadline = self.now() + self.timeout + 15
            while value.get('status') != 'terminal':
                if value.get('status') == 'uncertain' and value.get('state', {}).get('supervisor'):
                    raise RuntimeError('remote supervisor lost without terminal receipt')
                require(self.now() < deadline, 'remote operation polling timed out; state uncertain')
                self.sleep(.2)
                value = self.helper_call(node, 'status', operation)
            require(type(value.get('exit_code')) is int and
                    isinstance(value.get('stdout'), str) and isinstance(value.get('stderr'), str),
                    'terminal receipt missing original command result')
            if value.get('action_succeeded') is not True:
                self.blocked = True
            return subprocess.CompletedProcess(command, value['exit_code'],
                                               value['stdout'], value['stderr'])
        except BaseException:
            self.blocked = True
            raise


def read_capsule(destination):
    root = destination.parent
    raw = (root / 'capsule.json').read_bytes()
    require(hashlib.sha256(raw).hexdigest() == (root / 'capsule.sha256').read_text().strip(),
            'immutable session capsule changed')
    capsule = json.loads(raw)
    require(Path(capsule['session_dir']).resolve() == root, 'session directory mismatch')
    return capsule


def run(args):
    # Avoid import-created __pycache__ files in the immutable original tree.
    sys.dont_write_bytecode = True
    os.environ['GIT_OPTIONAL_LOCKS'] = '0'
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    capsule = read_capsule(Path(args.evidence_dir).resolve())
    manifest = capsule['manifest']
    pre = manifest['preflight']
    destination = evidence_path(args.evidence_dir, manifest)
    require(capsule['window_id'] == args.window_id, 'capsule window mismatch')
    owned_hold(args.hold, args.window_id, pre['hold_path'], capsule['holder'])
    policy_path = Path(args.policy)
    require(policy_path == Path(pre['policy_path']), 'unexpected registered policy path')
    require(args.policy_sha256 == pre['policy_sha256']
            and args.spark_sha256 == pre['spark_sha256'], 'private expectations differ')
    policy = json.loads(verify_hash(policy_path, args.policy_sha256))
    expected = pre['registered_policy']
    required = {'repository', 'cluster_config', 'config_sha256', 'revision', 'image_id', 'container'}
    require(required <= set(expected) and all(policy.get(k) == v for k, v in expected.items()),
            'registered service identity drift')
    repository = Path(policy['repository'])
    require(repository.is_absolute(), 'original repository must be absolute')
    config = repository / policy['cluster_config']
    require(config.resolve().is_relative_to(repository.resolve()), 'config escapes original repository')
    verify_hash(config, policy['config_sha256'])
    source = repository / 'bin/spark'
    verify_hash(source, args.spark_sha256)
    loader = importlib.machinery.SourceFileLoader('_q03_verified_original_spark', str(source))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    original = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = original
    loader.exec_module(original)
    # Recheck mutable coordination and protected policy after module imports.
    owned_hold(args.hold, args.window_id, pre['hold_path'], capsule['holder'])
    verify_hash(policy_path, args.policy_sha256)
    verify_hash(config, policy['config_sha256'])
    parsed = original.parser().parse_args(transition_args(
        policy, 'stop' if args.action == 'quiesce' else args.action, args.apply))
    cluster, nodes, _ = original.configuration(parsed)
    require(capsule['window_id'] == args.window_id, 'capsule window mismatch')
    remote = RemoteOperations(original, cluster, nodes, capsule, args.window_id,
                              args.epoch, destination)
    remote.verify_helpers()
    if args.action == 'quiesce':
        print(json.dumps({'window_id': args.window_id, 'epoch': uuid.UUID(args.epoch).hex,
                          'sealed_epochs': remote.quiesce(), 'safe_to_overlap': True}))
        return 0
    install_readonly_hooks(original, destination, remote.run_ssh)
    result = parsed.func(parsed)
    return 0 if result is None else result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy', required=True)
    parser.add_argument('--policy-sha256', required=True)
    parser.add_argument('--spark-sha256', required=True)
    parser.add_argument('--evidence-dir', required=True)
    parser.add_argument('--hold', required=True)
    parser.add_argument('--window-id', required=True)
    parser.add_argument('--action', choices=('stop', 'start', 'quiesce'), required=True)
    parser.add_argument('--epoch', required=True)
    parser.add_argument('--apply', action='store_true')
    try:
        return run(parser.parse_args())
    except Exception as error:
        print(json.dumps({'result': 'failed', 'error': f'{type(error).__name__}: {error}'}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
