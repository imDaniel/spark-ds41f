#!/usr/bin/env python3
"""Long-prefix cache separation: require real cache hits, not short-prompt inference."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import sys
import uuid
from request_screen import MODEL, PROFILE, request


def main(output):
    nonce=str(uuid.uuid4())
    text='Window cache isolation '+nonce+'\n'+('This is a neutral prefix cache isolation measurement sentence. '*6000)
    tokens=request('/tokenize',{'model':MODEL,'prompt':text})['tokens'][:32768]
    assert len(tokens)==32768
    report={'nonce':nonce,'prompt_tokens':len(tokens),'tokens_sha256':hashlib.sha256(json.dumps(tokens).encode()).hexdigest(),'rows':[]}
    def save():Path(output).write_text(json.dumps(report,indent=2)+'\n')
    def run(scale,cancel=False):
        b={'model':MODEL,'prompt':tokens,'max_tokens':32,'min_tokens':32,'ignore_eos':True,
           'temperature':0,'seed':1022,'stream':True,'stream_options':{'include_usage':True},
           'steering_profile':PROFILE,'steering_scale':scale}
        r=request('/v1/completions',b,True,cancel)
        if not cancel:
            assert r['usage']['prompt_tokens']==32768
            r['cached_tokens']=r['usage']['prompt_tokens_details']['cached_tokens']
        return {'scale':scale,**r}
    baselines={}
    for index,scale in enumerate((0,0,1,1,4,4,0)):
        r=run(scale);report['rows'].append({'kind':'sequential',**r});save()
        if index in (0,2,4):
            assert r['cached_tokens']==0, 'Another steering identity reused prefix KV'
            baselines[scale]=r['text_sha256']
        else:
            assert r['cached_tokens']>=8192, 'Warm request did not demonstrate actual prefix reuse'
            assert r['text_sha256']==baselines[scale], 'Warm/cold same-scale output mismatch'
    with concurrent.futures.ThreadPoolExecutor(4) as pool:rows=list(pool.map(run,(0,1,4,0)))
    for r in rows:
        report['rows'].append({'kind':'mixed-c4',**r});save()
        assert r['cached_tokens']>=8192 and r['text_sha256']==baselines[r['scale']], 'Mixed request state isolation failed'
    r=run(1,cancel=True);report['rows'].append({'kind':'cancel',**r});save()
    r=run(0);report['rows'].append({'kind':'stock-after-cancel',**r});save()
    assert r['cached_tokens']>=8192 and r['text_sha256']==baselines[0]
    report['passed']=True;save()

if __name__=='__main__':main(sys.argv[1])
