#!/usr/bin/env python3
"""Site binding for the pinned window; no operations on import."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import urllib.request


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def run(args, timeout=30, check=True, input=None):
    p = subprocess.run(args, input=input, text=True, capture_output=True, timeout=timeout)
    if check and p.returncode:
        raise RuntimeError(f'{args[0]} failed: {p.returncode}: {p.stderr[:500]}')
    return p


def api(base, path, data=None, key=None, timeout=60):
    headers = {'Content-Type': 'application/json'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    req = urllib.request.Request(base + path, data=json.dumps(data).encode() if data else None, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as f:
        return f.read()


def queue(text):
    result = {}
    for field in ('running', 'waiting'):
        matches = re.findall(r'^vllm:num_requests_' + field + r'(?:\{[^\n]*\})? ([0-9.]+)$', text, re.M)
        if not matches or any(not float(x).is_integer() for x in matches):
            raise ValueError('Missing or invalid queue telemetry: ' + field)
        result[field] = sum(int(float(x)) for x in matches)
    return result


def node_observe(m, name):
    """Runs on each node. Identity fields are computed from actual inputs."""
    services = {}
    known_pids = set()
    errors = []
    for arm, spec in m['services'].items():
        repo = Path(spec['repository'])
        cfg = json.loads((repo / spec['config']).read_text())
        identity = dict(spec['identity'])
        identity['head'] = run(['git', '-C', str(repo), 'rev-parse', 'HEAD']).stdout.strip()
        identity['config_sha256'] = sha(repo / spec['config'])
        identity['nodes_sha256'] = sha(repo / cfg['nodes_config'])
        image = json.loads(run(['docker', 'image', 'inspect', cfg['container']['image']]).stdout)[0]
        identity['image_id'] = image['Id']
        identity['vllm_tree'] = image['Config']['Labels']['local.spark3.vllm.tree']
        expected_mounts = [{'Source': a, 'Destination': b, 'RW': c == 'rw'} for a,b,c in cfg['container']['mounts']]
        expected_mounts.append({'Source': '/dev/dri/by-path/pci-000f:01:00.0-card', 'Destination': '/dev/dri/card0', 'RW': True})
        mounts = sorted(expected_mounts, key=lambda x: x['Destination'])
        inspected = run(['docker', 'container', 'inspect', spec['container']], check=False)
        container = json.loads(inspected.stdout)[0] if inspected.returncode == 0 else None
        running = bool(container and container['State']['Running'])
        pids = []
        if running:
            if container['Image'] != identity['image_id']:
                raise ValueError(name + '/' + arm + ' running image drift')
            mounts = sorted([{k: v[k] for k in ('Source','Destination','RW')} for v in container['Mounts']], key=lambda x:x['Destination'])
            if mounts != sorted(expected_mounts, key=lambda x:x['Destination']):
                raise ValueError('Live mount drift')
            pids = [int(x.strip()) for x in run(['docker','top',spec['container'],'-eo','pid']).stdout.splitlines()[1:]]
            known_pids.update(pids)
        identity['mounts_sha256'] = digest(mounts)
        if spec.get('profile_path'):
            identity['profile_sha256'] = sha(spec['profile_path'])
        model = next(Path(a) for a,b,c in cfg['container']['mounts'] if b == '/models')
        verified = all(sha(model / k) == v for k,v in m['model_metadata'].items())
        verified = verified and len(list(model.glob('*.safetensors'))) == 48
        verified = verified and all(sha(k) == v for k,v in spec['files'].items())
        verified = verified and all(Path(v['Source']).exists() for v in expected_mounts)
        published = bool(run(['git','-C',str(repo),'branch','-r','--contains',identity['head']]).stdout.strip())
        guard = run(['systemctl','is-active',spec['container']+'-memguard.service'], check=False).stdout.strip() == 'active'
        clean = not run(['git','-C',str(repo),'status','--porcelain','--untracked-files=all']).stdout.strip()
        services[arm] = {'identity':identity,'clean_checkout':clean,'assets_verified':verified,
                         'published_revision':published,'startup_guards_configured':cfg['host'].get('startup_memguard_gib',0)>=5,
                         'running':running,'guard_active':guard,'owned_processes':len(pids)}
    gpu = run(['nvidia-smi','--query-compute-apps=pid','--format=csv,noheader,nounits'],check=False)
    foreign = [int(x) for x in gpu.stdout.splitlines() if x.strip().isdigit() and int(x) not in known_pids]
    # A window-specific component container is the only additional owned GPU job.
    owned = []
    jobs = run(['docker','ps','-q','--filter','label=spark.window='+m['window_id']]).stdout.split()
    for jid in jobs:
        obj = json.loads(run(['docker','inspect',jid]).stdout)[0]
        if obj['Config']['Labels'].get('spark.role') != 'component':
            raise ValueError('Unexpected owned job role')
        jpids = [int(x.strip()) for x in run(['docker','top',jid,'-eo','pid']).stdout.splitlines()[1:]]
        foreign = [p for p in foreign if p not in jpids]
        owned.append({'id':jid,'window_id':m['window_id']})
    # Kernel GPU faults since the preparation snapshot stop automatic experiments.
    journal = run(['sudo','-n','journalctl','-k','--since',m['faults_since'],'--no-pager','-o','cat'],check=False)
    fault = gpu.returncode != 0 or journal.returncode != 0 or bool(re.search(r'NVRM: Xid|GPU has fallen off',journal.stdout))
    return {'name':name,'reachable':True,'gpu_fault':fault,'foreign_jobs':foreign,'owned_jobs':owned,'services':services}


def remote(m, name, action='node'):
    node = m['site_nodes'][name]
    cmd = ['/usr/bin/python3',m['adapter_path'],action,'--manifest',m['manifest_path'],'--node',name]
    return json.loads(run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5','admin@'+node,shlex.join(cmd)],timeout=60).stdout)


def firewall(m, mode):
    # Dedicated UUID comment: removal cannot affect an existing rule.
    args = ['INPUT','!','-i','lo','-p','tcp','--dport','8000','-m','comment','--comment','spark-window-'+m['window_id'],'-j','REJECT','--reject-with','tcp-reset']
    return ['sudo','-n','iptables','-w','5',mode,*args]


def aliases(m):
    key = Path(m['gateway_key_file']).read_text().strip()
    ids = sorted(x['id'] for x in json.loads(api(m['gateway_base'],'/v1/models',key=key))['data'])
    if ids != m['gateway_model_ids']:
        raise ValueError('Gateway model aliases drift')
    return digest(ids)


def observe(m):
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        nodes = list(pool.map(lambda n:remote(m,n),m['nodes']))
    ready = False
    q = {'running':0,'waiting':0}
    if nodes[0]['services']['retained']['running']:
        ready = api('http://127.0.0.1:8000','/health',timeout=5) == b''
        q = queue(api('http://127.0.0.1:8000','/metrics',timeout=5).decode())
    return {'observed_at':time.time(),'nodes':nodes,'retained_ready':ready,
            'requests':q,'ingress':{'blocked':run(firewall(m,'-C'),check=False).returncode==0,
                                  'aliases_sha256':aliases(m)}}


def block(m):
    if run(firewall(m,'-C'),check=False).returncode==0:
        return
    # First refuse new TCP connections. Existing streams get up to five minutes
    # to finish/close before the final gate; no packet in an existing stream is cut.
    new = firewall(m,'-I')
    pos = new.index('-m')
    new[pos:pos] = ['-m','conntrack','--ctstate','NEW']
    check = new.copy();check[check.index('-I')] = '-C'
    delete = new.copy();delete[delete.index('-I')] = '-D'
    if run(check,check=False).returncode:
        run(new)
    deadline=time.monotonic()+300
    try:
        idle=None
        while time.monotonic()<deadline:
            q=queue(api('http://127.0.0.1:8000','/metrics',timeout=5).decode())
            connections=run(['ss','-Htn','state','established','sport','=',':8000']).stdout.strip()
            if q == {'running':0,'waiting':0} and not connections:
                idle=idle or time.monotonic()
                if time.monotonic()-idle>=30:
                    run(firewall(m,'-I'));return
            else:idle=None
            time.sleep(2)
        raise RuntimeError('Ingress did not drain; keeping service running')
    finally:
        if run(check,check=False).returncode==0:run(delete)


def verify(m, context):
    gateway=context.get('recovery_step')=='gateway_after_ingress'
    base=m['gateway_base'] if gateway else 'http://127.0.0.1:8000'
    key=Path(m['gateway_key_file']).read_text().strip() if gateway else None
    health=api('http://127.0.0.1:8000','/health',timeout=10)==b''
    ids=[v['id'] for v in json.loads(api(base,'/v1/models',key=key))['data']]
    models='DeepSeek-V4.1-Flash' in ids if gateway else 'deepseek-v4.1-flash' in ids
    checks=[]
    for active in (False,True):
        model=('DeepSeek-V4.1-Flash-heretic-1' if active else 'DeepSeek-V4.1-Flash') if gateway else 'deepseek-v4.1-flash'
        body={'model':model,'messages':[{'role':'user','content':'Reply with OK.'}], 'max_tokens':32,'temperature':0,'reasoning_effort':'none'}
        if active and not gateway:body.update(steering_profile='heretic-1017-attn-v1',steering_scale=1)
        obj=json.loads(api(base,'/v1/chat/completions',body,key,timeout=120))
        checks.append(bool(obj['choices'][0]['message'].get('content')))
    return dict(zip(('health','models','stock_request','steered_request','gateway_aliases'),[health,models,*checks,bool(aliases(m))]))


def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('action');p.add_argument('--manifest');p.add_argument('--node');a=p.parse_args()
    context=json.loads(os.environ.get('SPARK_WINDOW_CONTEXT','{}'))
    path=a.manifest or context['manifest'];m=json.loads(Path(path).read_text())
    if a.action=='node':result=node_observe(m,a.node)
    elif a.action=='observe':result=observe(m)
    elif a.action=='block_ingress':block(m);result={}
    elif a.action=='restore_ingress':
        if run(firewall(m,'-C'),check=False).returncode==0:run(firewall(m,'-D'))
        result={}
    elif a.action=='verify_retained':result=verify(m,context)
    elif a.action=='stop_owned_jobs':
        for name in m['nodes']:
            for job in remote(m,name)['owned_jobs']:
                run(['ssh','-o','BatchMode=yes','admin@'+m['site_nodes'][name],shlex.join(['docker','stop','--time','10',job['id']])])
        result={}
    else:raise ValueError('Unknown hook')
    print(json.dumps(result))

if __name__=='__main__':main()
