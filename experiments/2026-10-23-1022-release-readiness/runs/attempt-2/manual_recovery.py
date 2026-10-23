"""Operator-reviewed restoration after the two intentional Engram child failures.
No manifest, fault policy, driver or Docker configuration is changed.
"""
import hashlib,json,pathlib,subprocess,sys,time
REPO=pathlib.Path('/srv/ai-cluster/runtimes/1022-screen-20261023/source/spark-ds41f')
OPS=pathlib.Path('/srv/ai-cluster/operations/1022-release-20261023')
sys.path.insert(0,str(REPO/'experiments/2026-10-23-1022-recovery-executor'))
from window_executor import Executor,read,sha,boot_id
x=Executor(OPS/'window.json',OPS/'authorization.json');x.authorized()
review=read(OPS/'manual-review-2fb58ec6.json')
assert x.m['window_id']==review['window_id']=='2fb58ec6-68f8-457f-94f4-bce64c95c2f6'
def observed():
    journal=subprocess.check_output(['sudo','-n','journalctl','-k','--since',x.m['faults_since'],'--no-pager','-o','short-iso'])
    assert hashlib.sha256(journal).hexdigest()==review['kernel_sha256'],'New or changed kernel events'
    gpu=subprocess.check_output(['nvidia-smi','-q'],text=True)
    assert any('GPU Recovery Action' in l and l.split(':',1)[1].strip()=='None' for l in gpu.splitlines())
    assert boot_id()==x.state['boot_id']
    data=json.loads(x.hook('observe',recovering=True))
    assert 0<=time.time()-data['observed_at']<=x.m['observe_max_age_seconds']
    assert len(data['nodes'])==4 and {n['name'] for n in data['nodes']}==set(x.m['nodes'])
    for n in data['nodes']:
        assert n['reachable'] and not n['foreign_jobs'] and not n['owned_jobs']
        # Keep the historical fault visible; acknowledge only the reviewed node
        # and the exact immutable journal, never a blanket Xid exemption.
        assert n['gpu_fault']==(n['name']=='spark01')
        for name,spec in x.m['services'].items():
            actual=n['services'][name]
            assert actual['identity']==spec['identity'],(n['name'],name)
            assert all(actual[k] for k in ('clean_checkout','assets_verified','published_revision','startup_guards_configured'))
            if name!='retained':assert not actual['running'] and not actual['guard_active'] and actual['owned_processes']==0
    x.event('manual_review_observation',review_sha256=sha(OPS/'manual-review-2fb58ec6.json'),retained_ready=data['retained_ready'])
    return data
with x.lock():
    x.load_owned()
    assert x.state['phase']=='manual' and x.state['recovery_error']=='GPU/driver fault needs operator review'
    assert x.state['pending'] is None
    data=observed()
    assert not any(n['services']['retained']['running'] or n['services']['retained']['guard_active'] or n['services']['retained']['owned_processes'] for n in data['nodes'])
    x.event('manual_recovery_review',review=review,script_sha256=sha(pathlib.Path(__file__)))
    x.cluster('retained','start',recovering=True)
    data=observed();assert x.healthy_retained(data)
    s=x.m['services']['retained']
    x.run([s['repository']+'/bin/spark','--shape','tp4-ring','--cluster-config',s['config'],'doctor','--live'],s['repository'],s['files'],'manual-retained-doctor',recovering=True)
    x.verify('private_api_before_ingress')
    x.hook('restore_ingress',recovering=True)
    data=observed();assert data['ingress']==x.state['original_ingress']
    x.verify('gateway_after_ingress')
    data=observed();assert x.healthy_retained(data) and data['ingress']==x.state['original_ingress']
    x.state['phase']='closed';x.state['manual_review_sha256']=sha(OPS/'manual-review-2fb58ec6.json');x.save();x.owns_hold();x.hold.unlink()
    x.event('restored',retained=s['identity'],mode='operator-reviewed',review_sha256=x.state['manual_review_sha256'])
print('Original four-rank r6x restored and verified; ingress open; hold removed',flush=True)
