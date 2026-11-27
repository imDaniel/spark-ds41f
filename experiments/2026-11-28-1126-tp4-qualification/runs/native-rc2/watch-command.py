"""Run a published qualification command with the deployment's memory/thermal watch."""
import argparse,json,os,signal,subprocess,sys,time
from pathlib import Path
ROOT=Path("/srv/ai-cluster/runtimes/1126-attention/source/spark-ds41f")
sys.path.insert(0,str(ROOT))
from spark_ds41f.config import configuration
from spark_ds41f.bench import MemoryWatch
receipt=Path(sys.argv[1]); command=sys.argv[2:]
if receipt.exists() or not command: raise ValueError("New receipt and command required")
problems=[]; child=None; watch=None
def interrupted(*_): raise RuntimeError("Monitored command interrupted")
signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGINT,interrupted)
record={"command":command,"started_at":time.time(),"status":"running","monitor":"spark_ds41f.bench.MemoryWatch","sample_seconds":1,"thermal_sample_seconds":15}
try:
 _,nodes,_=configuration(argparse.Namespace(cluster_config="experiments/2026-11-28-1126-tp4-qualification/cluster.json",shape="tp4-ring"))
 os.environ["SPARK_DS41F_STRICT_TELEMETRY"]="1"
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
 record["finished_at"]=time.time()
 receipt.write_text(json.dumps(record,indent=2)+"\n")
