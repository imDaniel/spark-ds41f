#!/usr/bin/python3 -I
"""Fixed promoted four-rank backend. No command overrides or automatic recovery."""
import concurrent.futures,hashlib,importlib.util,json,os,signal,subprocess,sys,time,uuid,urllib.request
from pathlib import Path
POLICY=Path('/etc/ai-cluster/services/deepseek-1017-attention-1022.json')
HOLD=Path('/home/admin/spark-hold.json')

def trusted(path):
 path=Path(path)
 for p in (path,*path.parents):
  st=p.stat()
  if p.is_symlink() or st.st_uid!=0 or st.st_mode&0o022:raise RuntimeError('Untrusted backend input: '+str(p))

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def load():
 trusted(__file__);trusted(POLICY);p=json.loads(POLICY.read_text())
 if p['schema_version']!=1 or p['container']!='ds41f-1022-parser-rc3' or p['api_port']!=8000:raise RuntimeError('Invalid fixed production policy')
 spec=importlib.util.spec_from_file_location('deepseek_legacy','/usr/local/libexec/ai-cluster/model-control.py');ds=importlib.util.module_from_spec(spec);spec.loader.exec_module(ds);ds.validate()
 return p,ds,ds.Executor()

def fleet(policy,executor):
 code='P='+repr(policy)+r'''
import hashlib,json,subprocess,sys
from pathlib import Path
r=Path(P['repository']);q=subprocess.run(['/usr/bin/docker','inspect',P['container']],capture_output=True,text=True)
row={'present':q.returncode==0,'running':False,'valid':False,'gpu_pids':[],'errors':[]}
profile=Path(P['profile_path'])
checks=[hashlib.sha256((r/P['cluster_config']).read_bytes()).hexdigest()==P['config_sha256'],hashlib.sha256(profile.read_bytes()).hexdigest()==P['profile_sha256'],subprocess.check_output(['git','-C',str(r),'rev-parse','HEAD'],text=True).strip()==P['revision'],not subprocess.check_output(['git','-C',str(r),'status','--porcelain'])]
for name,digest in P['repository_files'].items():checks.append(hashlib.sha256((r/name).read_bytes()).hexdigest()==digest)
if not all(checks):row['errors'].append('Production inputs changed')
row['memguard']=subprocess.run(['/usr/bin/systemctl','is-active',P['container']+'-memguard.service'],capture_output=True,text=True).stdout.strip()

row['valid']=all(checks)
if q.returncode==0:
 c=json.loads(q.stdout)[0];row['running']=c['State']['Running'];row['failed']=bool(c['State'].get('OOMKilled') or c['State'].get('ExitCode'))
 if not row['running']:
  row['valid']=all(checks);print(json.dumps(row));sys.exit(0)
 mounts={m['Destination']:m for m in c['Mounts']};cmd=c['Config']['Cmd'];env=dict(x.split('=',1) for x in c['Config'].get('Env',[]) if '=' in x)
 def arg(k,v):return k in cmd and cmd.index(k)+1<len(cmd) and cmd[cmd.index(k)+1]==str(v)
 expected=P['ranks'][str(NODE)]
 checks += [c['Image']==P['image_id'],cmd==expected['command'],all(env.get(k)==v for k,v in expected['environment'].items())]
 checks += [set(mounts)==set(P['mounts'])]
 for dst,wanted in P['mounts'].items():checks.append(dst in mounts and mounts[dst]['Source']==wanted['source'] and mounts[dst]['RW']==wanted['rw'])
 try:
  steering=json.loads(cmd[cmd.index('--additional-config')+1])['ds41_steering']
  checks += [steering['profile_path']==P['container_profile_path'],steering['profile_sha256']==P['profile_sha256'],steering['operator_version']==P['operator_version']]
 except (KeyError,ValueError,IndexError):checks.append(False)
 checks.append(row['memguard']=='active')
 row['valid']=all(checks)
 if not row['valid']:row['errors'].append('Production identity mismatch')
 if row['running']:
  own={int(x.strip()) for x in subprocess.check_output(['/usr/bin/docker','top',c['Id'],'-eo','pid'],text=True).splitlines()[1:] if x.strip().isdigit()};gpu={int(x.strip()) for x in subprocess.check_output(['/usr/bin/nvidia-smi','--query-compute-apps=pid','--format=csv,noheader,nounits'],text=True).splitlines() if x.strip().isdigit()};row['gpu_pids']=sorted(own&gpu)
print(json.dumps(row))
'''
 def one(n):
  try:return n,json.loads(executor.run(n,['/usr/bin/python3','-I','-c','NODE='+str(n)+'\n'+code],timeout=20))
  except Exception as e:return n,dict(present=False,running=False,valid=False,gpu_pids=[],errors=[type(e).__name__])
 with concurrent.futures.ThreadPoolExecutor(4) as pool:return dict(pool.map(one,range(1,5)))

def observe(policy,executor):
 rows=fleet(policy,executor);running=any(r['running'] for r in rows.values());complete=all(r['running'] and r['valid'] and not r['errors'] for r in rows.values());ready=False
 if complete:
  try:
   with urllib.request.urlopen(f"http://127.0.0.1:{policy['api_port']}/health",timeout=2) as f:assert f.status==200
   with urllib.request.urlopen(f"http://127.0.0.1:{policy['api_port']}/v1/models",timeout=2) as f:ready=[x['id'] for x in json.load(f)['data']]==[policy['model_id']]
  except Exception:ready=False
 errors={str(n):r['errors'] for n,r in rows.items() if r['errors']}
 return {'running':running,'ready':complete and ready,'failed':bool(errors or any(r.get('failed') for r in rows.values()) or (running and not complete)),'gpu_pids':{str(n):r['gpu_pids'] for n,r in rows.items()},'nodes':rows,'errors':errors,'api_model':policy['model_id']}

def atomic(path,data):
 temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp');temp.write_text(json.dumps(data));temp.chmod(0o600);temp.replace(path)

def transition(policy,ds,executor,action):
 owner='sparkdash-deepseek-steering';identity=uuid.uuid4().hex
 record=dict(holder=owner,window=identity,since=ds.power.utc(),expected_end=None,heartbeat=ds.power.utc(),rule='Exclusive coordinated DeepSeek steering transition')
 fd=os.open(HOLD,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as f:json.dump(record,f)
 def beat():
  now=json.loads(HOLD.read_text());assert now.get('window')==identity;now['heartbeat']=ds.power.utc();atomic(HOLD,now)
 args=[str(Path(policy['repository'])/'bin/spark'),'--shape','tp4-ring','--cluster-config',policy['cluster_config'],'cluster',action]
 if action=='start':args+=['--replace']
 def run(command,timeout):
  process=subprocess.Popen(command,cwd=policy['repository'],env=ds.power.ENV,start_new_session=True);begin=time.monotonic()
  try:
   while process.poll() is None:
    if time.monotonic()-begin>timeout:raise TimeoutError('Fixed production transition timed out')
    beat();time.sleep(2)
   if process.returncode:raise RuntimeError('Coordinated production command failed')
  except BaseException:
   if process.poll() is None:
    os.killpg(process.pid,signal.SIGTERM)
    try:process.wait(timeout=15)
    except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
   raise
 def interrupted(*_):raise RuntimeError('Production transition interrupted')
 signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGINT,interrupted)
 released=False;mutating=False
 try:
  initial=observe(policy,executor)
  if initial['errors']:raise RuntimeError('Production inputs differ from the fixed policy')
  if action=='start' and initial['ready']:
   released=True;print(json.dumps(initial),flush=True);return
  if action=='start' and initial['running']:raise RuntimeError('Production is partially running; use its coordinated stop first')
  plan=subprocess.run(args,cwd=policy['repository'],env=ds.power.ENV,capture_output=True,text=True,check=True,timeout=60)
  print(plan.stdout,flush=True)
  if 'live:' not in plan.stdout or (action=='start' and not any(x in plan.stdout for x in ('shape:   unchanged','shape:   new engine'))):raise RuntimeError('Production plan needs a different runtime shape')
  if action=='start':
   for n in range(1,5):
    if executor.run(n,['/usr/bin/nvidia-smi','--query-compute-apps=pid','--format=csv,noheader,nounits'],timeout=10).strip():raise RuntimeError('GPU is not drained')
  mutating=True;run(args+['--apply'],3650 if action=='start' else 210);status=observe(policy,executor)
  if action=='start' and not status['ready']:raise RuntimeError('Production identity/readiness verification failed')
  if action=='stop' and (status['running'] or status['errors'] or any(r.get('memguard')=='active' for r in status['nodes'].values())):raise RuntimeError('Production did not stop on all ranks')
  released=True;print(json.dumps(status),flush=True)
 except BaseException:
  if not mutating:released=True
  elif action=='start':
   try:
    run([str(Path(policy['repository'])/'bin/spark'),'--shape','tp4-ring','--cluster-config',policy['cluster_config'],'cluster','stop','--apply'],210);final=observe(policy,executor);released=not final['running'] and not final['errors'] and not any(r.get('memguard')=='active' for r in final['nodes'].values())
   except Exception:released=False
  raise
 finally:
  if released and HOLD.exists() and json.loads(HOLD.read_text()).get('window')==identity:HOLD.unlink()

def main():
 if len(sys.argv)!=2 or sys.argv[1] not in ('probe','start','stop'):raise ValueError('Only fixed probe/start/stop actions allowed')
 policy,ds,executor=load();action=sys.argv[1]
 if action=='probe':print(json.dumps(observe(policy,executor)));return
 transition(policy,ds,executor,action)
if __name__=='__main__':
 try:main()
 except Exception as e:print(json.dumps({'passed':False,'error_type':type(e).__name__,'error':str(e),'automatic_retry':False}),file=sys.stderr);sys.exit(1)
