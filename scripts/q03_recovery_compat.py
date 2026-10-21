#!/usr/bin/env python3
"""Explicit old64 adapter overlay; no old source or terminal receipt is rewritten.

The caller owns reconciliation locks, heartbeat, durable epoch registration and
all restoration admission. This entry only preserves old registered operations
while acknowledging one proven, already-unloaded memguard lifecycle outcome.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import types
import uuid


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def load_source(path, name, expected=None):
    raw = Path(path).read_bytes()
    if expected is not None:
        require(hashlib.sha256(raw).hexdigest() == expected, 'source hash mismatch')
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


# Read-only bootstrap. Load the exact old helper bytes, never its replacement.
REMOTE_READ = r'''
import hashlib,json,os,pathlib,stat,subprocess,sys
p,h,authority,ah,root,window,operation,unit=sys.argv[1:]
p=pathlib.Path(p)
assert p.is_absolute() and p.resolve()==p
fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
st=os.fstat(fd)
assert stat.S_ISREG(st.st_mode) and st.st_uid==os.geteuid() and not st.st_mode&0o022
with os.fdopen(fd,'rb') as f: source=f.read()
assert hashlib.sha256(source).hexdigest()==h
m={'__name__':'_q03_old_readonly_helper','__file__':str(p),'_Q03_VERIFIED_SOURCE_SHA256':h}
exec(compile(source,str(p),'exec'),m)
a=m['read_authority'](authority,ah)
assert pathlib.Path(root)==pathlib.Path(a['independent_root'])/'remote-operations'
d=m['operation_dir'](root,window,operation)
manifest,digest=m['read_manifest'](d)
status=m['status'](root,window,operation)
raw={}
for name in ('manifest.json','receipt.json','output.log','error.log'):
    q=d/name
    assert q.resolve()==q and q.is_file() and q.stat().st_size<=1048576
    data=q.read_bytes()
    raw[name]={'text':data.decode('utf-8'),'sha256':hashlib.sha256(data).hexdigest()}
r=subprocess.run(['systemctl','show',unit,'--property=LoadState','--property=ActiveState','--property=SubState'],text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10,check=False)
print(json.dumps({'manifest_sha256':digest,'status':status,'raw':raw,'unit':unit,'unit_show':{'returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr}}))
'''


def derive_acknowledgment(bundle, expected_unit, window, epoch, operation, classify):
    """Validate fresh bound evidence; return separate proof, never mutate input."""
    raw = bundle['raw']
    for key in ('manifest.json', 'receipt.json', 'output.log', 'error.log'):
        require(hashlib.sha256(raw[key]['text'].encode()).hexdigest() == raw[key]['sha256'],
                'raw operation evidence hash mismatch')
    manifest = json.loads(raw['manifest.json']['text'])
    receipt = json.loads(raw['receipt.json']['text'])
    digest = raw['manifest.json']['sha256']
    require(bundle['manifest_sha256'] == digest and receipt['manifest_sha256'] == digest,
            'operation manifest binding mismatch')
    require(manifest['window'] == receipt['window'] == window and
            manifest['operation'] == receipt['operation'] == operation and
            manifest['epoch'] == epoch, 'operation/epoch identity mismatch')
    status = bundle['status']
    require(status['status'] == 'terminal' and status['action_succeeded'] is False and
            status['manifest_sha256'] == digest and
            all(status.get(k) == v for k, v in receipt.items()), 'terminal evidence differs')
    require(status['stdout'] == raw['output.log']['text'] and
            status['stderr'] == raw['error.log']['text'], 'terminal logs changed during observation')
    require(classify(manifest['argv'], receipt, raw['output.log']['text'],
                     raw['error.log']['text'], expected_unit=expected_unit),
            'not the exact unloaded registered memguard outcome')
    show = bundle['unit_show']
    require(bundle['unit'] == expected_unit and show['returncode'] == 0 and show['stderr'] == '',
            'live registered unit observation unavailable')
    require(sorted(show['stdout'].splitlines()) ==
            sorted(['LoadState=not-found', 'ActiveState=inactive', 'SubState=dead']),
            'registered unit is not unloaded and inactive')
    return {'window_id': window, 'epoch': epoch, 'operation': operation,
            'manifest_sha256': digest, 'raw_sha256': {k: v['sha256'] for k, v in raw.items()},
            'unit': expected_unit, 'unit_show': show,
            'classification': 'acknowledged-unloaded-memguard',
            'original_exit_code': 5, 'action_succeeded': False,
            'epoch_sealed': status['epoch_sealed']}


def install_overlay(old, classify, recovery_dir):
    base = old.RemoteOperations

    class CompatibleRemote(base):
        def helper_call(self, node, action, operation=None, extra=()):
            value = super().helper_call(node, action, operation, extra)
            def transform(item):
                if item.get('status') != 'terminal' or item.get('action_succeeded') is not False:
                    return item
                # All other failures retain the old unsafe semantics.
                if item.get('exit_code') != 5:
                    return item
                op = item['operation']
                unit = self.cluster['container']['name'] + '-memguard.service'
                result = self.transport(self.nodes, node, 'python3', '-I', '-B', '-c', REMOTE_READ,
                    str(self.helper), self.helper_sha, str(self.authority), self.authority_sha,
                    str(self.root), self.window, op, unit)
                require(result.returncode == 0, 'live operation evidence unavailable: ' + result.stderr)
                bundle = json.loads(result.stdout)
                require(bundle['manifest_sha256'] == item['manifest_sha256'] and
                        json.loads(bundle['raw']['manifest.json']['text'])['helper_sha256'] ==
                        self.helper_sha, 'fresh observation differs from pinned operation')
                proof = derive_acknowledgment(bundle, unit, self.window, self.epoch, op, classify)
                proof['node'] = node['name']
                path = recovery_dir / ('memguard-ack-' + uuid.uuid4().hex + '.json')
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'w') as out:
                    json.dump(proof, out, sort_keys=True)
                    out.flush()
                    os.fsync(out.fileno())
                fd = os.open(recovery_dir, os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
                derived = dict(bundle['status'])
                derived.update(lifecycle_acknowledged=True, lifecycle_unit=unit,
                               safe_to_overlap=proof['epoch_sealed'])
                self._ack_argv = json.loads(bundle['raw']['manifest.json']['text'])['argv']
                return derived
            if action == 'seal':
                value = dict(value)
                value['node'] = node['name']
                value['operations'] = [transform(item) for item in value['operations']]
                value['safe_to_overlap'] = value['sealed'] is True and all(
                    x.get('safe_to_overlap') is True for x in value['operations'])
            elif value.get('status') == 'terminal':
                value = transform(value)
            return value

        def run_ssh(self, nodes, node, *command):
            self._ack_argv = None
            result = super().run_ssh(nodes, node, *command)
            if result.returncode == 5 and self._ack_argv == self.helper_module.bound_command(command):
                self.blocked = False
            return result

    old.RemoteOperations = CompatibleRemote
    return CompatibleRemote


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('old-session', 'capsule-sha256', 'recovery-dir', 'policy', 'policy-sha256',
                 'spark-sha256', 'evidence-dir', 'hold', 'window-id', 'epoch'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--action', choices=('stop', 'start', 'quiesce'), required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    require(args.apply, 'explicit apply required; entry caller owns admission')
    here = Path(__file__).resolve().parent
    recovery_dir = Path(args.recovery_dir)
    require(recovery_dir.is_absolute() and recovery_dir.resolve() == recovery_dir,
            'new journal path alias')
    intent = json.loads((recovery_dir / 'intent.json').read_bytes())
    require(intent['old_session'] == args.old_session and
            intent['capsule_sha256'] == args.capsule_sha256, 'new intent/session mismatch')
    pins = intent['new_source_sha256']
    require(set(pins) == {'q03_reconcile_restore.py', 'q03_recovery_compat.py',
                          'q03_remote_operation.py', 'q03_recovery_supervisor.py'}, 'new source inventory mismatch')
    for name, digest in pins.items():
        require(hashlib.sha256((here / name).read_bytes()).hexdigest() == digest,
                'new released source changed: ' + name)
    support = load_source(here / 'q03_reconcile_restore.py', '_q03_compat_support',
                          pins['q03_reconcile_restore.py'])
    plan = support.verify_restore_plan(args.old_session, args.capsule_sha256)
    expected = support.adapter_command_plan(plan, args.action, args.epoch)[4:]
    actual = ['--policy', args.policy, '--policy-sha256', args.policy_sha256,
              '--spark-sha256', args.spark_sha256, '--evidence-dir', args.evidence_dir,
              '--hold', args.hold, '--window-id', args.window_id,
              '--epoch', args.epoch, '--action', args.action, '--apply']
    require(actual == expected, 'caller arguments differ from old registered transition')
    capsule = json.loads(Path(args.old_session, 'capsule.json').read_bytes())
    recovery_dir = Path(args.recovery_dir)
    root = Path(capsule['manifest']['independent_root'])
    require(recovery_dir.is_absolute() and recovery_dir.resolve() == recovery_dir and
            recovery_dir.is_dir() and recovery_dir != root and recovery_dir.is_relative_to(root) and
            not recovery_dir.is_relative_to(Path(args.old_session)), 'new independent journal required')
    for protected in capsule['manifest']['preflight']['protected_roots']:
        target = Path(protected)
        require(not recovery_dir.is_relative_to(target) and not target.is_relative_to(recovery_dir),
                'new journal overlaps protected namespace')
    old_path = Path(plan.source_directory) / 'q03_original_spark_adapter.py'
    old = load_source(old_path, '_q03_compat_verified_old_adapter',
                      dict(plan.source_hashes)['q03_original_spark_adapter.py'])
    future = load_source(here / 'q03_remote_operation.py', '_q03_compat_new_classifier',
                         pins['q03_remote_operation.py'])
    install_overlay(old, future.acknowledged_memguard_absence, recovery_dir)
    return old.run(args)


if __name__ == '__main__':
    raise SystemExit(main())
