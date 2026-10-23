#!/usr/bin/env python3
"""One bounded window, always restoring the retained service on ordinary failure."""
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'2026-10-23-1022-recovery-executor'))
from window_executor import Executor, ARMS, sha, read


def main():
    manifest,permit=sys.argv[1:]
    x=Executor(manifest,permit)
    x.open()
    stop_monitor=threading.Event()
    def monitor():
        while not stop_monitor.is_set():
            for name,host in x.m['site_nodes'].items():
                try:
                    p=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=3','admin@'+host,
                       "cat /proc/meminfo; nvidia-smi --query-gpu=temperature.gpu,utilization.gpu --format=csv,noheader"],capture_output=True,text=True,timeout=10)
                    with open(x.directory/'telemetry.jsonl','a') as f:f.write(json.dumps({'at':time.time(),'node':name,'rc':p.returncode,'sample':p.stdout})+'\n')
                except Exception as e:x.event('telemetry_error',node=name,error=str(e))
            stop_monitor.wait(10)
    thread=threading.Thread(target=monitor,daemon=True);thread.start()
    failure=None
    try:
        with x.lock():
            x.load_owned();x.cluster('retained','stop')
            data=x.observe()
            assert not any(x.active(data,n) for n in x.m['services'])
            image=x.m['services']['s-loaded']['identity']['image_id']
            # Synthetic components only; the four-node engine is stopped first.
            # Named/labelled container remains discoverable if this controller dies.
            args=['/usr/bin/docker','run','--rm','--name','ds41f-1022-components',
                  '--label','spark.window='+x.m['window_id'],'--label','spark.role=component',
                  '--gpus=all','--network=none','--memory=12g','--memory-swap=12g','--cpus=4',
                  '-v',str(x.directory)+':/out','-e','PYTEST_DISABLE_PLUGIN_AUTOLOAD=1',
                  '-e','PYTHONDONTWRITEBYTECODE=1','-e','CUDA_VISIBLE_DEVICES=0',
                  '--entrypoint','python3',image,'-m','pytest','--confcutdir=/opt/spark3/candidate/vllm/tests/models',
                  '-o','addopts=','-q','/opt/spark3/candidate/vllm/tests/models/test_deepseek_v4_1_steering_gpu.py',
                  '--junitxml=/out/components.xml']
            x.run(args,str(HERE),{'/usr/bin/docker':sha('/usr/bin/docker')},'GPU-components',timeout=600)
            suite=ET.parse(x.directory/'components.xml').getroot()
            cases=suite.findall('.//testcase')
            assert len(cases)>=29 and not suite.findall('.//skipped') and not suite.findall('.//failure') and not suite.findall('.//error')
            assert not any(n['owned_jobs'] for n in x.observe()['nodes'])
        for arm in ARMS:
            x.event('arm_begin',arm=arm);x.switch(arm)
            with x.lock():
                x.load_owned()
                script=HERE/'request_screen.py'
                x.run(['/usr/bin/python3',str(script),arm,str(x.directory/(arm+'-requests.json'))],str(HERE),
                      {'/usr/bin/python3':sha('/usr/bin/python3'),str(script):sha(script)},'requests:'+arm,timeout=900)
                data=x.observe()
                assert all(n['services'][arm]['running'] and n['services'][arm]['guard_active'] for n in data['nodes'])
            x.event('arm_complete',arm=arm)
        # Matched stock paths across E and both S modes are a release gate.
        def stock(arm):
            rows=read(x.directory/(arm+'-requests.json'))['rows']
            return [r['text_sha256'] for r in rows if r['kind']=='functional' and r['cycle']==0]
        assert stock('e')==stock('s-unloaded')==stock('s-loaded'),'Matched stock path outputs differ'
        x.event('screen_complete')
    except BaseException as e:
        failure=e;x.event('screen_failed',error=str(e))
    finally:
        stop_monitor.set();thread.join(timeout=45)
        # switch() may already have recovered after a failed launch.
        if x.hold.exists():
            state=read(x.state_path)
            if state['phase']!='manual':x.recover()
        state=read(x.state_path)
        if state['phase']!='closed':raise RuntimeError('Manual recovery required: '+json.dumps(state))
    if failure:raise failure

if __name__=='__main__':main()
