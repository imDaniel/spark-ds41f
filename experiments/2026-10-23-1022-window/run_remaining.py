#!/usr/bin/env python3
"""Resume only the two S arms after reviewing the request-harness rejection bug."""
import sys
import time
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'2026-10-23-1022-recovery-executor'))
from window_executor import Executor, read, sha


def main():
    x=Executor(*sys.argv[1:])
    assert time.time()+x.m['window_seconds'] <= x.m['original_reservation_end'], 'Cannot extend original maintenance reservation'
    x.check_files(x.m['reference_files'])
    x.open()
    try:
        for arm in ('s-unloaded','s-loaded'):
            x.switch(arm)
            with x.lock():
                x.load_owned()
                script=HERE/'request_screen.py'
                x.run(['/usr/bin/python3',str(script),arm,str(x.directory/(arm+'-requests.json'))],str(HERE),
                      {'/usr/bin/python3':sha('/usr/bin/python3'),str(script):sha(script)},'requests:'+arm,timeout=900)
                d=x.observe()
                assert all(n['services'][arm]['running'] and n['services'][arm]['guard_active'] for n in d['nodes'])
        def stock(path):return [r['text_sha256'] for r in read(path)['rows'] if r['kind']=='functional' and r['cycle']==0]
        assert stock(x.m['reference_e'])==stock(x.directory/'s-unloaded-requests.json')==stock(x.directory/'s-loaded-requests.json'), 'Matched stock outputs differ'
        with x.lock():
            x.load_owned()
            script=HERE/'cache_screen.py';reader=HERE/'request_screen.py'
            x.run(['/usr/bin/python3',str(script),str(x.directory/'cache-isolation.json')],str(HERE),
                  {'/usr/bin/python3':sha('/usr/bin/python3'),str(script):sha(script),str(reader):sha(reader)},'long-prefix-cache-isolation',timeout=600)
            d=x.observe()
            assert all(n['services']['s-loaded']['running'] and n['services']['s-loaded']['guard_active'] for n in d['nodes'])
        x.event('remaining_screen_complete')
    finally:
        if x.hold.exists() and read(x.state_path)['phase']!='manual':x.recover()
        assert read(x.state_path)['phase']=='closed'

if __name__=='__main__':main()
