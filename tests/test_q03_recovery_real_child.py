"""Sanitized real-subprocess regression for the PR37 adapter boundary.

Private historical admission is replaced ONLY in the copied test entry. The
production bootstrap, RuntimeOps supervisor/gate, adapter, registered loader,
original CLI/configuration/STOP and transport are real. A PATH-local ssh fake
never invokes a shell or network. This is not a live-drain or admission proof.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / 'scripts/q03_failed_window_recovery.py'
SPEC = importlib.util.spec_from_file_location('real_child_entry', ENTRY)
r = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(r)
HELPERS = ('topology', 'mesh_fabric', 'kernel_backend', 'kernel_readiness',
           'transport_profiles', 'model_layout', 'recipe')
CORE = ('q03_window_watchdog', 'q03_window_preflight', 'q03_window',
        'q03_original_spark_adapter', 'q03_remote_operation')


def source(relative):
    path = ROOT / relative
    if path.is_file():
        return path.read_bytes()
    # The cloud author's sparse checkout omits bin/spark. CI's full checkout
    # takes the direct branch above; no fetch or external Git operation occurs.
    return subprocess.check_output(['git', 'show', 'HEAD:' + relative], cwd=ROOT)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def record(path, role):
    s = path.lstat()
    value = {'role': role, 'path': str(path), 'resolved_path': str(path.resolve()),
             'exists': True, 'lstat': dict(type='directory' if path.is_dir() else 'regular',
             mode=format(stat.S_IMODE(s.st_mode), '04o'), uid=s.st_uid, gid=s.st_gid,
             dev=s.st_dev, ino=s.st_ino, nlink=s.st_nlink, size=s.st_size,
             mtime_ns=s.st_mtime_ns, ctime_ns=s.st_ctime_ns)}
    if path.is_file():
        value['sha256'] = sha(path.read_bytes())
    return value


# The only replaced functions concern private historical authority, which the
# separate admission tests cover. Every executed source/config/SSH boundary is
# the production implementation. The copied source remains hash-bound by the
# unchanged ENTRY_BOOTSTRAP.
FIXTURE_ADMISSION = r'''
_fixture = json.loads(Path(os.environ['Q03_CHILD_FIXTURE']).read_text())
CAPSULE_SHA256 = _fixture['capsule_sha256']
WITNESS_SHA256 = _fixture['witness_sha256']
CONTINUATION_WITNESS_SHA256 = _fixture['continuation_sha256']
def load_witness(path, directory):
    global _WITNESS
    _WITNESS = {'path': str(path), 'roles': {'restore_evidence': {'path': str(Path(directory) / 'restore-evidence')}}}
def load_continuation(path, directory):
    global _CONTINUATION
    _CONTINUATION = _fixture['continuation']
def verify_old(directory):
    values = {name: Path(path).read_bytes() for name,path in _fixture['sources'].items()}
    return _fixture['capsule'], {}, {}, values
def validate_destination(destination, capsule):
    return Path(destination)
def verify_hold(capsule, **kwargs):
    return tuple(_fixture['hold_identity'])
def verify_predecessor(capsule, journal, **kwargs):
    return {'deadline_epoch': _fixture['deadline_epoch']}
'''

FAKE_SSH = r'''
import json, os, pathlib, shlex, sys
wire = shlex.split(sys.argv[-1])
target = sys.argv[-2]
with open(os.environ['Q03_FAKE_CALLS'], 'a') as out:
    out.write(json.dumps({'target': target, 'wire': wire}) + '\n')
if '--authority-sha256' in wire:
    action = wire[7]
    def argument(key): return wire[wire.index(key) + 1]
    response = {'window': argument('--window'), 'epoch': argument('--epoch')}
    if action == 'verify':
        response.update(helper_sha256=wire[6], authority_sha256=argument('--authority-sha256'))
        mutate = os.environ.get('Q03_FAKE_MUTATE_NODES')
        if mutate and not pathlib.Path(mutate + '.done').exists():
            p = pathlib.Path(mutate); nodes = json.loads(p.read_text())
            for node in nodes['nodes']: node['ssh_host'] = 'forbidden-target.invalid'
            p.write_text(json.dumps(nodes)); pathlib.Path(mutate + '.done').touch()
    elif action == 'seal':
        response.update(sealed=True, safe_to_overlap=True, operations=[])
    elif action == 'launch':
        command = json.loads(argument('--argv-json'))
        assert command[-5:] == ['sudo', '-n', 'systemctl', 'stop', 'fixture-memguard.service'], command
        response.update(operation=argument('--operation'), status='terminal', exit_code=0,
                        stdout='', stderr='', action_succeeded=True)
    else:
        raise AssertionError('Unexpected fake helper action: ' + action)
    print(json.dumps(response)); sys.exit(0)
# Read-only original probes are wrapped by the real remote helper's bound argv.
assert wire[:4] == ['python3', '-I', '-B', '-c'], wire
command = wire[5:]
if command == ['docker', 'inspect', 'fixture']:
    sys.exit(1)  # Exact original service is absent.
if command == ['systemctl', 'is-active', '--quiet', 'fixture-memguard.service']:
    sys.exit(3)  # Exact original memguard is inactive.
raise AssertionError('Unrecognized transport operation: ' + repr(command))
'''


class RealChildBoundaryTests(unittest.TestCase):
    def setUp(self):
        if not sys.platform.startswith('linux'):
            self.skipTest('The actual supervisor contract requires Linux /proc')
        parent = Path(os.environ.get('Q03_TRUSTED_TEST_ROOT', str(ROOT))).resolve()
        # Never relax bootstrap ancestry, even for test execution.
        for p in (parent, *parent.parents):
            s = p.lstat()
            if s.st_mode & 0o022 or s.st_uid not in (0, os.getuid()):
                self.skipTest('Set Q03_TRUSTED_TEST_ROOT to an owned, non-writable-ancestor directory')
        self.temp = tempfile.TemporaryDirectory(prefix='q03-child-', dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repository = self.root / 'registered'
        for sub in ('config', 'scripts', 'bin'):
            (self.repository / sub).mkdir(parents=True, mode=0o775)
        self.repository.chmod(0o775)
        for sub in ('config', 'scripts', 'bin'):
            (self.repository / sub).chmod(0o775)
        self.checkout = self.root / 'independent'
        (self.checkout / 'scripts').mkdir(parents=True, mode=0o700)
        (self.checkout / 'bin').mkdir(mode=0o700)
        self.sources = {}
        for name in CORE + HELPERS:
            raw = source('scripts/' + name + '.py')
            path = self.checkout / 'scripts' / (name + '.py')
            path.write_bytes(raw); path.chmod(0o600)
            self.sources[name + '.py'] = str(path)
            if name in HELPERS:
                path = self.repository / 'scripts' / (name + '.py')
                path.write_bytes(raw); path.chmod(0o664)
        spark = source('bin/spark')
        (self.checkout / 'bin/spark').write_bytes(spark)
        (self.checkout / 'bin/spark').chmod(0o600)
        self.sources['../bin/spark'] = str(self.checkout / 'bin/spark')
        (self.repository / 'bin/spark').write_bytes(spark)
        (self.repository / 'bin/spark').chmod(0o775)
        self.profile = self.repository / 'config/registered.json'
        self.nodes_path = self.repository / 'config/nodes.json'
        self.upstreams = self.repository / 'upstreams.lock.json'
        self.profile.write_text(json.dumps({'container': {'name': 'fixture', 'image': 'fixture-image'},
            'deployment': {'ready_url': 'http://127.0.0.1:1/unused'},
            'serve_args': ['serve', '--nnodes', '4', '--tensor-parallel-size', '4'],
            'distributed': {'master_addr': 'fixture-head.invalid', 'master_port': '1'}}))
        nodes = {'ssh_user': 'fixture', 'nodes': [dict(name='node'+str(i), rank=i,
            head=i == 0, ssh_host='node'+str(i)+'.invalid') for i in range(4)]}
        self.nodes_path.write_text(json.dumps(nodes)); self.upstreams.write_text('{}')
        for path in (self.profile, self.nodes_path, self.upstreams): path.chmod(0o664)
        self.policy_path = self.root / 'policy.json'
        self.policy = {'repository': str(self.repository), 'cluster_config': 'config/registered.json',
                       'config_sha256': sha(self.profile.read_bytes())}
        self.policy_path.write_text(json.dumps(self.policy)); self.policy_path.chmod(0o600)
        self.destination = self.root / r.CONTINUATION_NAME
        self.destination.mkdir(mode=0o700)
        self.witness = self.root / 'witness.json'; self.witness.write_text('{}'); self.witness.chmod(0o600)
        self.continuation_path = self.root / 'continuation.json'
        self.continuation_path.write_text('{}'); self.continuation_path.chmod(0o600)
        self.calls = self.root / 'ssh-calls.jsonl'
        fakebin = self.root / 'fakebin'; fakebin.mkdir(mode=0o700)
        ssh = fakebin / 'ssh'; ssh.write_text('#!' + sys.executable + '\n' + FAKE_SSH); ssh.chmod(0o700)
        tmp = self.root / 'tmp'; tmp.mkdir(mode=0o700)
        self.env = {'PATH': str(fakebin) + os.pathsep + os.environ['PATH'],
                    'Q03_FAKE_CALLS': str(self.calls), 'TMPDIR': str(tmp)}
        patcher = patch.dict(os.environ, self.env); patcher.start(); self.addCleanup(patcher.stop)
        self.core = types.ModuleType('real_child_core')
        self.core.__file__ = self.sources['q03_window.py']
        exec(compile(Path(self.core.__file__).read_bytes(), self.core.__file__, 'exec'), self.core.__dict__)
        self.deadline = time.time() + 120
        self.capsule = {'window_id': r.WINDOW, 'recovery_timeout_s': 120, 'deadline_epoch': self.deadline,
            'recovery_source_sha256': {n: sha(Path(p).read_bytes()) for n,p in self.sources.items()},
            'manifest': {'independent_root': str(self.checkout), 'preflight': {
                'independent_checkout': str(self.checkout), 'policy_path': str(self.policy_path),
                'policy_sha256': sha(self.policy_path.read_bytes()), 'registered_policy': self.policy,
                'spark_sha256': sha(spark), 'nodes': [dict(name=n['name'], rank=n['rank']+1,
                    ssh_host='fixture@'+n['ssh_host']) for n in nodes['nodes']],
                'remote_helper_path': self.sources['q03_remote_operation.py'],
                'remote_helper_sha256': sha(Path(self.sources['q03_remote_operation.py']).read_bytes()),
                'remote_authority_path': str(self.checkout / 'authority.json'),
                'remote_authority_sha256': 'a'*64}}}

    def prepare(self):
        files = [self.policy_path, self.profile, self.nodes_path, self.upstreams,
                 self.repository / 'bin/spark'] + [self.repository/'scripts'/(n+'.py') for n in HELPERS]
        continuation = {'path': str(self.continuation_path),
            'files': {str(p): record(p, 'original_file') for p in files},
            'ancestors': {str(p): record(p, 'original_ancestor') for p in
                          [self.repository, *(self.repository / n for n in ('config','scripts','bin'))]},
            'config_files': [record(p, 'configuration') for p in (self.profile,self.nodes_path,self.upstreams)]}
        self.fixture = {'capsule': self.capsule, 'capsule_sha256': sha(json.dumps(self.capsule).encode()),
            'sources': self.sources, 'continuation': continuation, 'deadline_epoch': self.deadline,
            'witness_sha256': sha(self.witness.read_bytes()), 'hold_identity': [1, 2],
            'continuation_sha256': sha(self.continuation_path.read_bytes())}
        path = self.root / 'fixture.json'; path.write_text(json.dumps(self.fixture)); path.chmod(0o600)
        self.fixture_path = path
        self.entry = self.root / 'entry.py'
        raw = ENTRY.read_text(); marker = "if __name__ == '__main__':"
        self.assertEqual(raw.count(marker), 1)
        self.entry.write_text(raw.replace(marker, FIXTURE_ADMISSION + '\n' + marker))
        self.entry.chmod(0o600)
        self.entry_sha = sha(self.entry.read_bytes())
        self.intent = {'entry_sha256': self.entry_sha, 'window_id': r.WINDOW,
            'old_capsule_sha256': self.fixture['capsule_sha256'], 'journal_dir': str(self.destination),
            'private_witness_path': str(self.witness), 'private_witness_sha256': self.fixture['witness_sha256'],
            'recovery_process': self.core.process_identity(), 'hold_identity': [1,2],
            'deadline_epoch': self.deadline, 'continuation_witness_sha256': self.fixture['continuation_sha256']}
        r.private_json(self.destination / 'intent.json', self.intent)

    def run_child(self, action='quiesce', expect_failure=None, epoch=None):
        outer = self
        epoch = epoch or (r.OLD_EPOCH if action == 'quiesce' else 'e'*32)
        prior_logs = {p: p.read_bytes() for p in self.destination.glob('command-*.log')}
        class Session:
            directory = outer.destination
            capsule = outer.capsule
            state = {'phase': 'restoring', 'transition_epochs': [r.OLD_EPOCH],
                     'restoring_started_epoch': outer.deadline - 120}
            def beat(self):
                if time.time() >= outer.deadline: raise TimeoutError('fixture deadline')
            def update(self, **fields):
                self.state.update(fields); r.private_json(self.directory / 'state.json', self.state)
        if not hasattr(self, 'session'):
            self.session = Session()
        session = self.session
        if action == 'stop':
            session.update(transition_epochs=[r.OLD_EPOCH, epoch])
        argv = [sys.executable, '-I', '-B', '-c', r.ENTRY_BOOTSTRAP, str(self.entry), self.entry_sha,
                '--old-session', str(self.root/'unused-old'), '--journal-dir', str(self.destination),
                '--live-witness', str(self.witness), '--continuation-witness', str(self.continuation_path),
                '--adapter-step', action, '--epoch', epoch]
        error = None
        with patch.dict(os.environ, Q03_CHILD_FIXTURE=str(self.fixture_path)):
            try:
                result = self.core.RuntimeOps().execute(session, argv, cwd=str(self.destination), timeout=30)
            except RuntimeError as caught:
                error = caught
        logs = [p for p in self.destination.glob('command-*.log') if p not in prior_logs]
        self.assertEqual(len(logs), 1)
        self.assertEqual(prior_logs, {p: p.read_bytes() for p in prior_logs})
        text = logs[0].read_text()
        self.assertIsNone(session.state['active_process'])
        if expect_failure:
            self.assertIsNotNone(error, text)
            self.assertIn(expect_failure, text)
            self.assertFalse(self.calls.exists(), 'Refusal happened after SSH')
        else:
            self.assertIsNone(error, text)
            self.assertEqual(result['exit_code'], 0)
        return text

    def test_real_quiesce_reaches_all_four_after_0775_0664_configuration(self):
        self.prepare(); text = self.run_child()
        report = json.loads(text)
        self.assertEqual({row['node'] for row in report['sealed_epochs']}, {'node0','node1','node2','node3'})
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertEqual(len(calls), 8)
        self.assertEqual({c['target'] for c in calls}, {'fixture@node'+str(i)+'.invalid' for i in range(4)})

    def test_real_stop_uses_frozen_configuration_even_if_nodes_change_after_loading(self):
        self.prepare()
        with patch.dict(os.environ, Q03_FAKE_MUTATE_NODES=str(self.nodes_path)):
            text = self.run_child('stop')
        self.assertIn('already absent', text)
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertEqual(len(calls), 17)
        self.assertEqual({c['target'] for c in calls}, {'fixture@node'+str(i)+'.invalid' for i in range(4)})
        launches = [c for c in calls if '--argv-json' in c['wire']]
        self.assertEqual(len(launches), 4)
        self.assertIn('forbidden-target.invalid', self.nodes_path.read_text())

    def test_real_old_seal_stop_new_seal_preserves_all_three_command_logs(self):
        self.prepare()
        old = json.loads(self.run_child())
        self.assertEqual(old['epoch'], r.OLD_EPOCH)
        self.run_child('stop')
        new = json.loads(self.run_child(epoch='e'*32))
        self.assertEqual(new['epoch'], 'e'*32)
        self.assertEqual(len(list(self.destination.glob('command-*.log'))), 3)
        self.assertEqual(len(self.session.state['command_history']), 3)
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertEqual(len(calls), 33)
        targets = {'fixture@node'+str(i)+'.invalid' for i in range(4)}
        for section, epoch in ((calls[:8], r.OLD_EPOCH), (calls[-8:], 'e'*32)):
            self.assertEqual([call['wire'][7] for call in section], ['verify']*4 + ['seal']*4)
            for call in section:
                wire = call['wire']
                self.assertEqual(wire[wire.index('--epoch')+1], epoch)
            self.assertEqual({call['target'] for call in section}, targets)
        launches = [call for call in calls[8:-8] if '--argv-json' in call['wire']]
        self.assertEqual(len(launches), 4)
        self.assertEqual({call['target'] for call in launches}, targets)
        for call in launches:
            wire = call['wire']
            self.assertEqual(wire[wire.index('--epoch')+1], 'e'*32)
            command = json.loads(wire[wire.index('--argv-json')+1])
            self.assertEqual(command[-5:], ['sudo','-n','systemctl','stop','fixture-memguard.service'])

    def test_profile_byte_drift_is_rejected_before_ssh(self):
        self.prepare(); self.profile.write_bytes(self.profile.read_bytes() + b' ')
        self.run_child(expect_failure='Witnessed')

    def test_registered_helper_symlink_is_rejected_before_ssh(self):
        self.prepare()
        target = self.repository/'scripts/topology.py'
        retained = self.root/'retained-topology.py'
        target.rename(retained); target.symlink_to(retained)
        self.run_child(expect_failure='alias')

    def test_registered_helper_hardlink_is_rejected_before_ssh(self):
        self.prepare()
        os.link(self.repository/'scripts/topology.py', self.root/'linked-topology.py')
        self.run_child(expect_failure='Witnessed link count/size changed')

    def test_unlisted_group_writable_ancestor_is_rejected_before_ssh(self):
        self.prepare()
        del self.fixture['continuation']['ancestors'][str(self.repository/'config')]
        self.fixture_path.write_text(json.dumps(self.fixture))
        self.run_child(expect_failure='Untrusted directory ancestry')

    def test_wrong_physical_node_target_is_rejected_before_ssh(self):
        nodes = json.loads(self.nodes_path.read_text()); nodes['nodes'][0]['ssh_host'] = 'other.invalid'
        self.nodes_path.write_text(json.dumps(nodes)); self.prepare()
        self.run_child(expect_failure='Registered node target/rank binding differs')

    def test_unlisted_config_read_is_rejected_before_ssh(self):
        extra = self.repository/'config/extra.json'; extra.write_bytes(self.nodes_path.read_bytes()); extra.chmod(0o664)
        profile = json.loads(self.profile.read_text()); profile['nodes_config'] = 'config/extra.json'
        self.profile.write_text(json.dumps(profile)); self.policy['config_sha256'] = sha(self.profile.read_bytes())
        self.policy_path.write_text(json.dumps(self.policy))
        self.capsule['manifest']['preflight']['policy_sha256'] = sha(self.policy_path.read_bytes())
        self.prepare(); self.run_child(expect_failure='Unlisted original configuration read')


if __name__ == '__main__':
    unittest.main()
