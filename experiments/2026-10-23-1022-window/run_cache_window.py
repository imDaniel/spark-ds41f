#!/usr/bin/env python3
"""One additional loaded-arm boot, bounded by the original reservation end."""
import sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'2026-10-23-1022-recovery-executor'))
from window_executor import Executor, read, sha


def main():
    x=Executor(*sys.argv[1:]);x.open()
    try:
        x.switch('s-loaded')
        with x.lock():
            x.load_owned()
            paths=[HERE/'cache_screen.py',HERE/'request_screen.py',Path('/usr/bin/python3')]
            x.run(['/usr/bin/python3',str(paths[0]),str(x.directory/'cache-isolation.json')],str(HERE),
                  {str(p):sha(p) for p in paths},'long-prefix-cache-isolation',timeout=600)
            data=x.observe()
            assert all(n['services']['s-loaded']['running'] and n['services']['s-loaded']['guard_active'] for n in data['nodes'])
    finally:
        if x.hold.exists() and read(x.state_path)['phase']!='manual':x.recover()
        assert read(x.state_path)['phase']=='closed'

if __name__=='__main__':main()
