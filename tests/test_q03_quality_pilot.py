"""Bounded fake-transport/CPU integration only, never actual tokenizer or model evidence."""
import contextlib
import copy
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import q03_quality_capture as C
import q03_quality_pilot as P
import q03_baseline_service as B
import q03_http_service as H
import q03_http_client as HC
from test_q03_quality_capture import Response, answer

from quality_test_fixtures import MATERIAL, isolated_servebench_modules, toolbox
TOOLBOX = str(toolbox())


def corpus(prompt_path):
    prompts = [C.strict_json(line) for line in Path(prompt_path).read_bytes().splitlines()]
    metadata = {c['id']: c for c in json.loads(Path(MATERIAL,'cases-with-gold.json').read_bytes())}
    native = {'deepseek_v41.py': '8450587010152846a2e3f6269c661b464615b7e46c312f4e38a397c44218a6e5',
        'deepseek_v41_encoding.py': '8ee7a8010c3ed257a0f1caa4dfbeb3ec68df0b03e36fcd20dad044ec7cf114fe'}
    tokenizer = dict(model_revision='a'*40, model_config_sha256='b'*64, weights_manifest_sha256='c'*64,
        tokenizer_revision='a'*40, tokenizer_assets_sha256={'tokenizer.json':'d'*64},
        encoding_source_sha256=native['deepseek_v41_encoding.py'],
        chat_template_sha256=C.digest(C.canonical(native)), special_tokens_sha256='d'*64)
    artifacts = dict(model_root='/synthetic/models', weights_manifest={'path':'/synthetic/weights','sha256':'c'*64},
        runtime_metadata={'path':'/synthetic/runtime','sha256':'e'*64}, special_tokens_file='tokenizer.json',
        native_sources={name:{'path':'/synthetic/'+name,'sha256':sha} for name,sha in native.items()})
    return dict(schema_version=1, workload=P.WORKLOAD, model=C.MODEL, material_sha256=C.MATERIAL_SHA256,
        prompts={'path':str(prompt_path),'sha256':C.PROMPTS_SHA256}, tokenizer_identity=tokenizer,
        artifacts=artifacts, cases=[{'id':p['id'],'category':metadata[p['id']]['category'],'language':metadata[p['id']]['language'],
            'prompt_sha256':C.digest(p['text'].encode()),'input_ids':[1,i+2]} for i,p in enumerate(prompts)],
        default_rendering=copy.deepcopy(P.DEFAULT_RENDERING))


def plan(temp):
    inputs = corpus(Path(MATERIAL) / 'prompts.jsonl')
    output = Path(temp) / 'quality-pr8.json'
    result = dict(url='http://127.0.0.1:8000',window_id='a'*32,attempt_id='b'*32,lane_id='r6x188/unloaded',
        plan_sha256='c'*64,selection={'arm':'r6x188','steering':'unloaded'},client=dict(output=str(output),
        runtime_context=str(Path(temp)/'runtime-context.json'),timeout_seconds=600,quality_pilot=dict(
            workload=P.WORKLOAD,corpus=inputs,corpus_sha256=C.digest(C.canonical(inputs)),
            request_seconds=60,max_response_bytes=100000,max_header_bytes=8192)))
    context={k:result[k] for k in ('url','window_id','attempt_id','plan_sha256')}
    context.update(ready_sha256='d'*64,ranks=[{'rank':i,'container_id':str(i+1)*64,'config_sha256':'e'*64}
        for i in range(4)],observed_loaded_state={'status':'OBSERVED_FOUR_RANKS','profiles':[None]*4})
    Path(result['client']['runtime_context']).write_bytes(C.canonical(context))
    return result,context


class SourceTests(unittest.TestCase):
    def test_source_only_quality_closure_and_pins(self):
        module=B.quality_pilot_module()
        self.assertEqual(module.WORKLOAD,P.WORKLOAD)
        with patch.object(B,'QUALITY_SOURCE_SHA256','0'*64):
            with self.assertRaisesRegex(RuntimeError,'source pin'):B.quality_pilot_module()
        with patch.object(B,'CAPTURE_SOURCE_SHA256','0'*64):
            with self.assertRaisesRegex(RuntimeError,'source pin'):B.quality_pilot_module()
        self.assertTrue(H.is_baseline({'http':{'schema_version':7}}))
        self.assertFalse(H.is_baseline({'http':{'schema_version':7.0}}))

    def test_no_retry_or_hidden_sequence_in_quality_capsule(self):
        session=SimpleNamespace(manifest={'jobs':[{'kind':'http_service','timeout_seconds':7200,
            'selection':{'arm':'r6x188','steering':'unloaded'}}],'window_seconds':10800,
            'preflight':{'original_service_disposition':'keep-stopped'},'http_service':{'schema_version':7}},
            state={'attempt_results':[]})
        self.assertEqual(B.next_selection(session),session.manifest['jobs'][0]['selection'])
        session.state['attempt_results']=[{'outcome':'failed','passed_gates':[]}]
        with self.assertRaisesRegex(RuntimeError,'no retry'): B.next_selection(session)

    def test_fixed_quality_loader_rejects_preloaded_or_changed_sources(self):
        pins={name:C.digest((ROOT/'scripts'/f'{name}.py').read_bytes()) for name in HC.QUALITY_NAMES}
        with self.assertRaisesRegex(RuntimeError,'preloaded'):HC.load_quality({'quality_sources':pins})
        saved={name:sys.modules.pop(name,None) for name in HC.QUALITY_NAMES}
        try:
            pins['q03_quality_pilot']='0'*64
            with self.assertRaisesRegex(RuntimeError,'source changed'):HC.load_quality({'quality_sources':pins})
        finally:
            sys.modules.update({k:v for k,v in saved.items() if v is not None})


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(isolated_servebench_modules())
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.plan,self.context=plan(self.temp.name)
        self.calls=[];self.fail=None;self.now=10
        sources={name:(Path(TOOLBOX)/name).read_bytes()
            for name in C.strict_json((ROOT/'scripts/q03_http_toolbox.json').read_bytes())['files']}
        self.loader=HC.FrozenToolbox(Path(TOOLBOX),sources);sys.meta_path.insert(0,self.loader)
        self.addCleanup(self.unload)
        self.cli=importlib.import_module('servebench.__main__')

    def unload(self):
        sys.meta_path.remove(self.loader)
        for name in list(sys.modules):
            if name=='servebench' or name.startswith('servebench.'):sys.modules.pop(name)

    def fake(self,request,timeout):
        self.calls.append(request.full_url)
        if request.full_url.endswith('/metrics'):
            if self.fail=='metrics':raise OSError('synthetic metrics failure')
            if self.fail=='metrics-overflow':return Response(b'x'*100001)
            if self.fail=='missing-gauge':return Response(b'vllm:num_requests_running 0\n')
            return Response(b'vllm:num_requests_running 0\nvllm:num_requests_waiting 0\n')
        payload=json.loads(request.data)
        prompt=payload['messages'][0]['content']
        prompts=[json.loads(line) for line in Path(MATERIAL,'prompts.jsonl').read_bytes().splitlines()]
        i=next(i for i,p in enumerate(prompts) if p['text']==prompt)
        if request.full_url.endswith('/tokenize'):
            return Response(C.canonical({'tokens':[1,999 if self.fail=='tokens' and i==1 else i+2],'count':2}))
        return Response(b'{bad' if self.fail=='generation' and i==1 else C.canonical(answer(i)))

    def run_lane(self):
        with contextlib.redirect_stdout(io.StringIO()), patch('urllib.request.urlopen',side_effect=AssertionError('global transport forbidden')):
            code=P.run_client(self.plan,self.cli.main,open_response=self.fake,clock=lambda:self.now)
        root=Path(self.temp.name,'quality-capture')
        return code,C.strict_json((root/'quality-capture.json').read_bytes())

    def test_all_sixty_live_tokens_then_natural_generation_and_full_rehash(self):
        code,summary=self.run_lane()
        self.assertEqual(code,0);self.assertEqual(len(self.calls),125)
        self.assertTrue(all(url.endswith('/tokenize') for url in self.calls[:60]))
        self.assertEqual(summary['counts'],{'COMPLETED':60,'TRUNCATED':0,'ERROR':0,'NOT_RUN':0})
        result=P.validate_output(C.strict_json(Path(self.plan['client']['output']).read_bytes()),self.plan,context=self.context)
        self.assertFalse(result['comparison_eligible']);self.assertEqual(result['semantic_scoring'],'NOT_EVALUATED')
        self.assertEqual(result['final_answer_extraction']['status'],'UNBOUND')

    @unittest.skipUnless(os.getenv('Q03_TEST_QUALITY_PREPARATION'), 'optional genuine portable preparation not configured')
    def test_genuine_preparation_vectors_retain_identity_through_fake_lane_and_scorer(self):
        # Genuine full vectors are data inputs only. Native site artifacts, live
        # replies and answers stay explicitly synthetic; this is not site proof.
        import q03_quality_data_score as S
        raw=Path(os.environ['Q03_TEST_QUALITY_PREPARATION']).read_bytes()
        self.assertEqual(C.digest(raw),'30e0d4ff22ccd71f6f0e64d47c6fd7a63077a2e48cae1d408dffbc069457472a')
        preparation=C.strict_json(raw);content=preparation['content']
        self.assertEqual(preparation['admissibility'],'NOT_REPLAY_ADMISSIBLE')
        self.assertEqual(C.digest(C.canonical(content)),preparation['content_sha256'])
        corpus=self.plan['client']['quality_pilot']['corpus']
        for case,prepared in zip(corpus['cases'],content['cases']):
            self.assertEqual({k:case[k] for k in ('id','category','language','prompt_sha256')},
                             {k:prepared[k] for k in ('id','category','language','prompt_sha256')})
            case['input_ids']=prepared['input_ids']
        self.assertEqual(len(content['cases']),60)
        self.plan['client']['quality_pilot']['corpus_sha256']=C.digest(C.canonical(corpus))
        prompts=[C.strict_json(line) for line in Path(MATERIAL,'prompts.jsonl').read_bytes().splitlines()]
        old_fake=self.fake
        def fake(request,timeout):
            if request.full_url.endswith('/metrics'):return old_fake(request,timeout)
            self.calls.append(request.full_url)
            prompt=C.strict_json(request.data)['messages'][0]['content']
            i=next(i for i,p in enumerate(prompts) if p['text']==prompt)
            ids=corpus['cases'][i]['input_ids']
            if request.full_url.endswith('/tokenize'):
                return Response(C.canonical({'tokens':ids,'count':len(ids)}))
            value=answer(i);value['prompt_token_ids']=ids
            value['usage']['prompt_tokens']=len(ids)
            value['usage']['total_tokens']=len(ids)+value['usage']['completion_tokens']
            return Response(C.canonical(value))
        self.fake=fake
        code,summary=self.run_lane()
        self.assertEqual(code,0);self.assertEqual(len(self.calls),125)
        P.validate_output(C.strict_json(Path(self.plan['client']['output']).read_bytes()),self.plan,context=self.context)
        result=S.evaluate(Path(MATERIAL),Path(self.temp.name,'quality-capture'),max_response_bytes=100000)
        self.assertTrue(result['inventory_valid'])
        self.assertEqual(result['counts']['completed'],60)
        self.assertEqual(result['counts']['evidence_error'],0)
        self.assertEqual(result['counts']['evaluated'],44)
        self.assertEqual(result['counts']['pending_isolated_code'],8)
        self.assertEqual(result['counts']['pending_blind'],8)
        self.assertEqual(result['counts']['comparison_eligible'],0)
        self.assertEqual(result['native_internal_final_extraction']['status'],'UNBOUND')
        self.assertEqual(result['applied_scale_status'],'UNBOUND')
        self.assertEqual(result['overall_quality'],'NOT_EVALUATED')
        self.assertEqual(result['score_target'],{'kind':'literal_api_field','json_pointer':'/choices/0/message/content'})
        for case,record in zip(content['cases'],summary['records']):
            self.assertEqual(case['input_ids'],record['expected_input_ids'])
            self.assertEqual(case['input_ids_sha256'],record['expected_input_ids_sha256'])

    def test_live_token_mismatch_stops_before_any_generation(self):
        self.fail='tokens';code,summary=self.run_lane()
        self.assertEqual(code,1);self.assertEqual(len(self.calls),2)
        self.assertEqual(summary['counts']['NOT_RUN'],60)
        self.assertEqual(len(summary['live_tokenization']),2)

    def test_first_generation_error_stops_all_later_network(self):
        self.fail='generation';code,summary=self.run_lane()
        self.assertEqual(code,1);self.assertEqual(len(self.calls),65)
        self.assertEqual(summary['counts'],{'COMPLETED':1,'TRUNCATED':0,'ERROR':1,'NOT_RUN':58})

    def test_metrics_failure_is_closed_before_model_requests(self):
        self.fail='metrics';code,summary=self.run_lane()
        self.assertEqual(code,1);self.assertEqual(len(self.calls),61)
        self.assertEqual(summary['counts']['NOT_RUN'],60)

    def test_metrics_overflow_is_not_silent(self):
        self.fail='metrics-overflow';code,summary=self.run_lane()
        self.assertEqual(code,1);self.assertEqual(len(self.calls),61)
        self.assertTrue(summary['metrics'][0]['response']['overflow'])

    def test_missing_idle_gauge_is_not_idle(self):
        self.fail='missing-gauge';code,summary=self.run_lane()
        self.assertEqual(code,1);self.assertEqual(len(self.calls),61)

    def test_receipt_corruption_rejected(self):
        self.run_lane();root=Path(self.temp.name,'quality-capture')
        value=C.strict_json(Path(self.plan['client']['output']).read_bytes())
        for filename in ('response-000.bin','tokenize-000.bin','metrics-000.bin','record-000.json','request-000.json','case-index.json'):
            path=root/filename;old=path.read_bytes();path.write_bytes(old+b'corrupt')
            with self.subTest(filename=filename),self.assertRaises(Exception):P.validate_output(value,self.plan,context=self.context)
            path.write_bytes(old)
        changed=copy.deepcopy(value);changed['generation']['sequences'][0]['output_ids']=[99]
        with self.assertRaisesRegex(C.CaptureError,'ordinary PR8 row'):P.validate_output(changed,self.plan,context=self.context)

    def test_content_length_and_idle_raw_evidence_rechecked_not_trusted(self):
        self.run_lane();root=Path(self.temp.name,'quality-capture')
        value=C.strict_json(Path(self.plan['client']['output']).read_bytes())
        path=root/'quality-capture.json';summary=C.strict_json(path.read_bytes())
        summary['records'][0]['response']['headers']=[['Content-Length','1']]
        (root/'record-000.json').write_bytes(C.canonical(summary['records'][0]))
        path.write_bytes(C.canonical(summary))
        with self.assertRaisesRegex(C.CaptureError,'Content-Length'):P.validate_output(value,self.plan,context=self.context)

    def test_quality_plan_and_input_closure_use_existing_normal_renderer(self):
        from test_q03_baseline_service import Tests, Identity
        holder=Tests();holder.identity=Identity();binding=holder.binding()
        binding['http']['schema_version']=7;binding['http'].pop('legacy_case')
        q=self.plan['client']['quality_pilot'];corpus_path=Path(self.temp.name,'corpus.json')
        corpus_path.write_bytes(C.canonical(q['corpus']))
        binding['http']['quality_pilot']={k:q[k] for k in ('workload','request_seconds','max_response_bytes','max_header_bytes')}
        binding['http']['quality_pilot']['corpus']={'path':str(corpus_path),'sha256':C.digest(corpus_path.read_bytes())}
        with patch.object(B,'identity_module',return_value=holder.identity),patch.object(B,'quality_pilot_module',return_value=P):
            planned=B.http_plan(binding,'a'*32,'b'*32,binding['selection'])
            self.assertEqual(planned['schema_version'],7);self.assertEqual(planned['lane_id'],'r6x188/unloaded')
            self.assertEqual(planned['client']['quality_pilot'],q)
            self.assertNotIn('legacy_case',planned['client'])
            self.assertEqual(planned['client']['timeout_seconds'],600)
            self.assertEqual(set(planned['client']['quality_sources']),set(P.SOURCES))
            self.assertEqual(len(planned['ranks']),4)
            for rank in planned['ranks']:
                self.assertEqual(rank['environment']['VLLM_DS41_Q03_MHC_MXFP8'],'0')
                self.assertEqual(rank['python_argv'][:4],['-B','/q03-http/q03_baseline_worker.py','serve','/models'])
            inputs=B.input_contract(binding)
            self.assertIn(q['corpus']['prompts'],inputs['files'])
            self.assertIn(binding['http']['quality_pilot']['corpus'],inputs['files'])
            self.assertFalse(any('gold' in p['path'] or 'reference_code' in p['path'] for p in inputs['files']))
            self.assertTrue(any(p['path'].endswith('/q03_quality_capture.py') for p in inputs['files']))
            self.assertTrue(any(p['path'].endswith('/q03_quality_pilot.py') for p in inputs['files']))
        altered=copy.deepcopy(q['corpus']);altered['cases'][0]['language']='en'
        with self.assertRaisesRegex(C.CaptureError,'case index'):P.validate_corpus(altered)

    def test_owned_controller_context_dispatch_and_receipt_collection(self):
        from test_q03_baseline_http_hooks import Journal
        from unittest.mock import Mock
        directory=Path(self.temp.name,'http');directory.mkdir()
        ready={'reports':[{'steering_profile':None} for i in range(4)]}
        raw=C.canonical(ready);(directory/'ready-observed.json').write_bytes(raw)
        ops=H.HttpOps.__new__(H.HttpOps);ops.plan=self.plan
        ops.binding={'checkout':str(ROOT),'http':{'schema_version':7}}
        self.plan.update(source_head='a'*40,binding=ops.binding,image='sha256:'+'f'*64)
        self.plan['client']['toolbox_head']='b'*40
        ops.journal=Journal(directory,copy.deepcopy(self.context['ranks']))
        ops.check_authority=Mock();ops.mutate=Mock()
        Path(self.plan['client']['runtime_context']).unlink()
        with patch.object(H,'validate_ready',return_value=ready), patch.object(H,'baseline_module',
                return_value=SimpleNamespace(quality_pilot_module=lambda:P)), patch.object(H,'plan_image',return_value=self.plan['image']):
            ops.run_client()
            self.assertEqual(ops.mutate.call_args.args[:2],(0,'http_client'))
            self.assertEqual(ops.mutate.call_args.args[3],600)
            self.assertEqual(ops.mutate.call_args.args[2][:3],['python3','-I','-B'])
            self.context=C.strict_json(Path(self.plan['client']['runtime_context']).read_bytes())
            self.assertEqual(self.context['ready_sha256'],C.digest(raw))
            self.run_lane()
            receipt={k:self.plan[k] for k in ('window_id','attempt_id','plan_sha256')}
            receipt.update(exit_code=0,error=None,output_sha256=C.digest(Path(self.plan['client']['output']).read_bytes()))
            Path(self.plan['client']['output']).with_name('client-receipt.json').write_bytes(C.canonical(receipt))
            result=ops.collect_result()
            self.assertEqual(result['execution_evidence'],'CAPTURE_COMPLETE_AWAITING_TERMINAL_DRAIN')
            self.assertFalse(result['comparison_eligible'])
            events=list(directory.glob('[0-9][0-9][0-9][0-9][0-9][0-9].json'))
            self.assertEqual(len(events),1)
            event=events[0];original=event.read_bytes();event.write_bytes(C.canonical({'kind':'unrelated'}))
            with self.assertRaisesRegex(RuntimeError,'durable controller event'):ops.collect_result()
            event.write_bytes(original)
            ops.journal.state['ranks'][0]['container_id']='f'*64
            with self.assertRaisesRegex(RuntimeError,'actual ranks/readiness'):ops.collect_result()

    def test_exact_source_loader_client_under_isolated_python(self):
        self.plan['client']['toolbox_path']=TOOLBOX
        self.plan['client']['toolbox_files']=C.strict_json((ROOT/'scripts/q03_http_toolbox.json').read_bytes())['files']
        self.plan['client']['quality_sources']={name:C.digest((ROOT/'scripts'/f'{name}.py').read_bytes()) for name in HC.QUALITY_NAMES}
        file=Path(self.temp.name,'client-plan.json');file.write_bytes(C.canonical(self.plan))
        program = r"""
import importlib.util,sys,json,io,urllib.request
from pathlib import Path
spec=importlib.util.spec_from_file_location('q03_http_client',sys.argv[1]);h=importlib.util.module_from_spec(spec)
sys.modules['q03_http_client']=h;spec.loader.exec_module(h)
plan=json.loads(Path(sys.argv[2]).read_bytes())
prompts=[json.loads(line) for line in Path(plan['client']['quality_pilot']['corpus']['prompts']['path']).read_bytes().splitlines()]
lookup={p['text']:i for i,p in enumerate(prompts)}
class Response:
 status=200;headers={}
 def __init__(self,raw):self.raw=io.BytesIO(raw)
 def read(self,n):return self.raw.read(n)
 def __enter__(self):return self
 def __exit__(self,*args):pass
calls=[]
def fake(req,timeout):
 calls.append(req.full_url)
 if req.full_url.endswith('/metrics'):return Response(b'vllm:num_requests_running 0\nvllm:num_requests_waiting 0\n')
 body=json.loads(req.data);i=lookup[body['messages'][0]['content']]
 if req.full_url.endswith('/tokenize'):return Response(json.dumps({'tokens':[1,i+2],'count':2}).encode())
 return Response(json.dumps({'model':'deepseek-v4.1-flash','prompt_token_ids':[1,i+2],
  'choices':[{'index':0,'message':{'role':'assistant','content':'synthetic'},'token_ids':[5],'finish_reason':'stop'}],
  'usage':{'prompt_tokens':2,'completion_tokens':1,'total_tokens':3}}).encode())
class Opener:
 open=staticmethod(fake)
urllib.request.build_opener=lambda *a:Opener()
def forbidden(*a,**k):raise AssertionError('global network forbidden')
urllib.request.urlopen=forbidden
receipt=h.run(plan)
assert receipt['exit_code']==0,receipt
assert len(calls)==125,len(calls)
assert all(name not in sys.modules for name in ('torch','vllm')), 'model modules imported'
for name in h.QUALITY_NAMES:assert isinstance(sys.modules[name].__loader__,h.FrozenQuality),name
print('ISOLATED_SOURCE_ONLY_OK')
"""
        result=subprocess.run([sys.executable,'-I','-B','-c',program,str(ROOT/'scripts/q03_http_client.py'),str(file)],
            capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('ISOLATED_SOURCE_ONLY_OK',result.stdout)

    def test_no_thinking_override_or_larger_client_budget(self):
        for field,value in [('timeout_seconds',601),('timeout_seconds',600.0)]:
            self.plan['client'][field]=value
            with self.assertRaisesRegex(C.CaptureError,'bound'):P.run_client(self.plan,self.cli.main,open_response=self.fake)
        self.assertFalse(self.calls)
        changed=copy.deepcopy(self.plan['client']['quality_pilot']['corpus']);changed['default_rendering']['thinking']=False
        with self.assertRaisesRegex(C.CaptureError,'thinking'):P.validate_corpus(changed)


if __name__=='__main__':unittest.main()
