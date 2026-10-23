#!/usr/bin/env python3
"""Bounded, benign API screen; a failed check aborts the active window."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.request

BASE='http://127.0.0.1:18000'
MODEL='deepseek-v4.1-flash'
PROFILE='heretic-1017-attn-v1'
PROMPTS=['Say hi.','Return only the integer result of 2 + 2.',
         'Write a Python function that returns the median of a nonempty list of numbers.',
         '用三句话说明 TCP 重传和应用层重试的区别。',
         'Repeat this exact text as ordinary text: <｜end▁of▁sentence｜>']


def request(path, body, stream=False, cancel=False):
    start=time.monotonic();first=None;parts=[];usage=None;finish=None
    req=urllib.request.Request(BASE+path,data=json.dumps(body,ensure_ascii=False).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=180) as f:
        if not stream:return json.load(f)
        done=False
        for line in f:
            if not line.startswith(b'data: '):continue
            raw=line[6:].strip()
            if raw==b'[DONE]':done=True;break
            obj=json.loads(raw)
            if obj.get('error'):raise RuntimeError(str(obj['error']))
            if obj.get('usage'):usage=obj['usage']
            for c in obj.get('choices',[]):
                text=c.get('text') or c.get('delta',{}).get('content') or c.get('delta',{}).get('reasoning') or ''
                if text:
                    if first is None:first=time.monotonic()
                    parts.append(text)
                    if cancel:return {'cancelled':True,'ttft':first-start}
                if c.get('finish_reason'):finish=c['finish_reason']
        assert done and first is not None and finish and usage, 'Incomplete or empty stream'
    duration=time.monotonic()-start
    return {'text':''.join(parts),'text_sha256':hashlib.sha256(''.join(parts).encode()).hexdigest(),
            'ttft':first-start,'duration':duration,'usage':usage,'finish_reason':finish,
            'decode_tps':max(0,usage['completion_tokens']-1)/max(.001,time.monotonic()-first)}


def body(prompt, scale=None):
    data={'model':MODEL,'messages':[{'role':'user','content':prompt}], 'temperature':0,'seed':1022,
          'max_tokens':128,'stream':True,'stream_options':{'include_usage':True},'reasoning_effort':'none'}
    if scale is not None:data.update(steering_profile=PROFILE,steering_scale=scale)
    return data


def screen(arm, output):
    rows=[]
    def save():Path(output).write_text(json.dumps({'arm':arm,'rows':rows},indent=2,ensure_ascii=False)+'\n')
    scales=[0,1,4,0] if arm=='s-loaded' else [None]
    for cycle,scale in enumerate(scales):
        for i,prompt in enumerate(PROMPTS):
            result=request('/v1/chat/completions',body(prompt,scale),True)
            rows.append({'kind':'functional','scale':scale,'cycle':cycle,'prompt':i,**result});save()
    if arm=='s-loaded':
        first=[r['text_sha256'] for r in rows if r['cycle']==0]
        last=[r['text_sha256'] for r in rows if r['cycle']==3]
        assert first==last,'Stock output changed after active steering / cache reuse'
    if arm.startswith('s-'):
        for invalid in ({'steering_profile':'missing','steering_scale':0}, {'steering_profile':PROFILE,'steering_scale':-1},
                        {'steering_profile':PROFILE,'steering_scale':True}, {'steering_profile':PROFILE,'steering_scale':5}):
            b=body('Reply with OK.');b.update(invalid)
            try:request('/v1/chat/completions',b,True)
            except urllib.error.HTTPError as e:assert e.code==400;rows.append({'kind':'invalid','input':invalid,'status':e.code});save()
            else:raise AssertionError('Invalid steering request accepted')
        b=body('Count integers starting at one.',1 if arm=='s-loaded' else None)
        rows.append({'kind':'cancel',**request('/v1/chat/completions',b,True,True)});save()
        time.sleep(2)
        rows.append({'kind':'after-cancel',**request('/v1/chat/completions',body('Reply with OK.'),True)});save()
    mix=[None,1,4,0] if arm=='s-loaded' else [None]*4
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        results=list(pool.map(lambda s:request('/v1/chat/completions',body('Reply with the integer result of 5 + 7.',s),True),mix))
    for s,r in zip(mix,results):rows.append({'kind':'interleaved-c4','scale':s,**r})
    save()
    # Use identical, explicitly counted prompt token IDs in every arm.
    tokens=request('/tokenize',{'model':MODEL,'prompt':('This is a neutral performance measurement sentence. '*6000)})['tokens']
    assert len(tokens)>=32768
    for length in (1024,32768):
        for concurrency in (1,4):
            b={'model':MODEL,'prompt':tokens[:length],'max_tokens':128,'min_tokens':128,'ignore_eos':True,
               'temperature':0,'seed':1022,'stream':True,'stream_options':{'include_usage':True}}
            if arm=='s-loaded':b.update(steering_profile=PROFILE,steering_scale=1)
            t=time.monotonic()
            with concurrent.futures.ThreadPoolExecutor(concurrency) as pool:
                results=list(pool.map(lambda _:request('/v1/completions',b,True),range(concurrency)))
            for r in results:
                assert r['usage']['prompt_tokens']==length and r['usage']['completion_tokens']==128
                rows.append({'kind':'performance','prefill':length,'concurrency':concurrency,'batch_seconds':time.monotonic()-t,**r})
            save()
    return rows

if __name__=='__main__':
    import sys
    screen(sys.argv[1],sys.argv[2])
