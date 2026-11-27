"""Run a published qualification command with the deployment's memory/thermal watch."""
import argparse,concurrent.futures,json,os,shlex,signal,subprocess,sys,time
from pathlib import Path
ROOT=Path("/srv/ai-cluster/runtimes/1126-attention/source/spark-ds41f")
sys.path.insert(0,str(ROOT))
from spark_ds41f.config import configuration,ssh_target
from spark_ds41f.bench import MemoryWatch

def vm_counters(nodes):
 remote = """import json,os
from pathlib import Path
values={line.split()[0]:int(line.split()[1]) for line in Path('/proc/vmstat').read_text().splitlines()}
print(json.dumps({'pagesize':os.sysconf('SC_PAGE_SIZE'),'swappiness':int(Path('/proc/sys/vm/swappiness').read_text()),**{key:values[key] for key in ['pswpin','pswpout','pgmajfault']}}))
"""
 def one(node):
  result=subprocess.check_output(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=6',ssh_target(nodes,node),shlex.join(['python3','-c',remote])],text=True,timeout=15)
  return node['name'],json.loads(result)
 with concurrent.futures.ThreadPoolExecutor(4) as pool:
  return dict(pool.map(one,nodes['nodes']))

receipt=Path(sys.argv[1]); command=sys.argv[2:]
if receipt.exists() or not command: raise ValueError("New receipt and command required")
problems=[]; child=None; watch=None
def interrupted(*_): raise RuntimeError("Monitored command interrupted")
signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGINT,interrupted)
record={"command":command,"started_at":time.time(),"status":"running","monitor":"spark_ds41f.bench.MemoryWatch","sample_seconds":1,"thermal_sample_seconds":15}
try:
 _,nodes,_=configuration(argparse.Namespace(cluster_config="experiments/2026-11-28-1126-tp4-qualification/cluster.json",shape="tp4-ring"))
 os.environ["SPARK_DS41F_STRICT_TELEMETRY"]="1"
 record["vm_before"]=vm_counters(nodes)
 watch=MemoryWatch(nodes,3,problems.append)
 watch.wait_ready()
 child=subprocess.Popen(command,cwd=ROOT,start_new_session=True)
 while child.poll() is None:
  watch.check()
  if problems: raise RuntimeError(problems[0])
  time.sleep(1)
 watch.check()
 if problems: raise RuntimeError(problems[0])
 record["exit_code"]=child.returncode
 if child.returncode: raise RuntimeError("Qualification command failed")
 record["status"]="passed"
except BaseException as error:
 record.update(status="failed",error=str(error))
 raise
finally:
 if child is not None and child.poll() is None:
  os.killpg(child.pid,signal.SIGTERM)
  try: child.wait(timeout=20)
  except subprocess.TimeoutExpired: os.killpg(child.pid,signal.SIGKILL);child.wait()
 if watch is not None:
  record["thermal"]=watch.thermal_summary()
  record["memory"]=watch.close()
 try:
  if "vm_before" in record:
   record["vm_after"]=vm_counters(nodes)
   record["vm_delta"]={name:{key:after[key]-record["vm_before"][name][key] for key in ['pswpin','pswpout','pgmajfault']} for name,after in record["vm_after"].items()}
 except Exception as error:
  record.update(status="failed",telemetry_error=str(error))
 record["finished_at"]=time.time()
 receipt.write_text(json.dumps(record,indent=2)+"\n")

if "telemetry_error" in record: raise RuntimeError(record["telemetry_error"])
