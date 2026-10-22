"""Synthetic wrapper composition only; no genuine tokenizer/image/model evidence."""
import unittest

import test_q03_quality_portable_corpus as F


SITE = F.BOOT + F.FIXTURE + r'''
from contextlib import contextmanager, ExitStack

@contextmanager
def site():
 with tempfile.TemporaryDirectory() as d, ExitStack() as stack:
  root=Path(d); preparation=root/'prep.json'; binding_path=root/'bindings.json'
  materials=root/'materials'; materials.mkdir(); (materials/'prompts.jsonl').write_bytes(prompt_raw)
  prep=dict(schema_version=1,kind=S.KIND,admissibility=P.ADMISSIBILITY,
            content=content,content_sha256=Q.digest(Q.canonical(content)))
  prep_sha=Q.exclusive_json(preparation,prep)
  sidecar_raw={arm:Q.canonical({'kind':'SYNTHETIC_NOT_RUNTIME_EVIDENCE','arm':arm})
               for arm in E.REVISED_NATIVE_HEADS}
  sidecars={}
  for arm,data in sidecar_raw.items():
   path=root/(arm+'-metadata.json'); path.write_bytes(data)
   sidecars[arm]={'path':str(path),'sha256':Q.digest(data)}
  bindings=dict(schema_version=2,native_cpu_bindings={'path':str(root/'native.json')},
    native_sources={arm:{'root':str(root/arm),'head':head} for arm,head in E.REVISED_NATIVE_HEADS.items()},
    toolbox_root=str(root/'toolbox'),tokenizer_metadata=sidecars)
  binding_path.write_bytes(Q.canonical(bindings))
  native=dict(model_root=str(root/'model'),weights_manifest={'path':str(root/'weights.json')},
              runtime_metadata=sidecars['r6x-unpatched'])
  export=dict(identity={**identity,'transformers_version':'FIXTURE','tokenizers_version':'FIXTURE'},
              artifacts={'fixture':'unreal'})
  sources={arm:{name:(b'# SYNTHETIC SOURCE ONLY '+arm.encode()+b' '+name.encode()+b'\n')
                for name in E.REVISED_SOURCE_FILES[arm]} for arm in E.REVISED_NATIVE_HEADS}
  inputs=(native,export,sources,{},{})
  packages={name:{'version':'FIXTURE'} for name in ('transformers','tokenizers')}
  modules={'transformers':SimpleNamespace(TokenizersBackend=object),'tokenizers':object()}
  for obj,name,value in [(S,'isolate',None),(P,'recheck_packages',None),(L,'artifact_files',[])]:
   stack.enter_context(patch.object(obj,name,return_value=value))
  # Actual source/asset/sidecar validators have their existing independent
  # tests. Here only their declared return is synthetic; no fake field pass.
  full=stack.enter_context(patch.object(E,'validate_bindings',return_value=inputs))
  stack.enter_context(patch.object(S,'read_materials',return_value=(materials,snapshot,prompts,metadata)))
  backend=stack.enter_context(patch.object(P,'packages',return_value=(modules,packages)))
  loader=stack.enter_context(patch.object(N,'native_tokenizer',return_value=Tokenizer()))
  stack.enter_context(patch.object(S,'construct_content',side_effect=AssertionError('regeneration attempted')))
  output=root/'out'
  def run(): return S.bind_site(preparation,prep_sha,binding_path,str(materials),str(output))
  yield SimpleNamespace(**locals())

def rejected(f, message):
 try: f.run()
 except RuntimeError as e: assert message in str(e), str(e)
 else: raise AssertionError('invalid binding accepted')
 assert not f.output.exists()
'''


class QualityUSBindingTest(unittest.TestCase):
    run_python = F.QualityPortableTest.run_python

    def test_revised_helper_closure_is_exact_and_historical_closure_unchanged(self):
        self.run_python(F.BOOT + r'''
old=S.support_sources(); before=(sys.dont_write_bytecode,sys.pycache_prefix)
new=S.support_sources(revised=True)
assert set(new)-set(old)=={'q03_tokenizer_metadata_capture.py','q03_runtime_capture.py'}
assert len(old)==9 and len(new)==11
assert {k:new[k] for k in old}==old
assert (sys.dont_write_bytecode,sys.pycache_prefix)==before
assert new['q03_runtime_capture.py']['sha256']==E.metadata_validator().RUNTIME_HELPER_SHA256
''')

    def test_schema2_preserves_original_vectors_and_both_raw_provenances(self):
        self.run_python(SITE + r'''
with site() as f:
 original=f.preparation.read_bytes(); result=f.run()
 assert f.full.call_count==2
 assert all(c.kwargs==dict(allow_revised=True) for c in f.full.call_args_list)
 f.loader.assert_called_once_with(f.sources['r6x-unpatched'],f.native['model_root'],object)
 assert f.preparation.read_bytes()==original
 corpus=Q.json_bytes(Path(result['corpus']['path']).read_bytes())
 assert corpus['schema_version']==1 and len(corpus['cases'])==60
 assert corpus['default_rendering']==S.DEFAULT_RENDERING
 assert [c['input_ids'] for c in corpus['cases']]==[c['input_ids'] for c in content['cases']]
 receipt=Q.json_bytes(Path(result['site_link_receipt']['path']).read_bytes())
 assert receipt['schema_version']==2 and receipt['scope']=='SITE_CPU_INPUT_BINDING_NOT_FIELD_QUALIFICATION'
 assert receipt['comparison']==dict(chat_vectors=60,complete_ids_equal=True,
                                  complete_rendered_text_equal=True,regenerated_prompts=False)
 provenance=receipt['producer_provenance']
 assert provenance['loader_arm']=='r6x-unpatched' and provenance['binding_schema_version']==2
 assert provenance['runtime_qualification']=='NOT_EVALUATED'
 assert provenance['package_byte_equivalence']=='NOT_ASSERTED_ACROSS_CPU_AND_NATIVE_IMAGES'
 for arm,raw in f.sidecar_raw.items():
  pin=provenance['tokenizer_metadata'][arm]
  assert Path(pin['path']).read_bytes()==raw and pin['sha256']==Q.digest(raw)
  for name,data in f.sources[arm].items():
   assert (f.output/'native-sources'/arm/name).read_bytes()==data
   assert provenance['native_source_files_sha256'][arm][name]==Q.digest(data)
 for name,pin in receipt['preparation_sources'].items():
  raw=(f.output/'preparation-sources'/name).read_bytes()
  assert Q.digest(raw)==pin['sha256']==provenance['retained_helper_sources_sha256'][name]
 # No relabeling the two different whole protocol files into claimed equality.
 assert f.sources['r6x-unpatched'][E.CHAT_PROTOCOL]!=f.sources['r6x-steer'][E.CHAT_PROTOCOL]
 try: f.run()
 except RuntimeError as e: assert 'new canonical' in str(e)
 else: raise AssertionError('existing output overwritten')
 assert f.preparation.read_bytes()==original
''')

    def test_underlying_schema2_admission_failure_has_no_fallback(self):
        self.run_python(SITE + r'''
with site() as f:
 f.full.side_effect=RuntimeError('actual U/S sidecar missing or mixed')
 rejected(f,'actual U/S sidecar missing or mixed')
 f.backend.assert_not_called(); f.loader.assert_not_called()
''')

    def test_sidecar_changed_after_validation_fails_before_backend(self):
        self.run_python(SITE + r'''
with site() as f:
 def changed(*args, allow_revised=False):
  assert allow_revised is True
  Path(f.sidecars['r6x-steer']['path']).write_bytes(b'changed')
  return f.inputs
 f.full.side_effect=changed
 rejected(f,'metadata changed after validation')
 f.backend.assert_not_called(); f.loader.assert_not_called()
''')

    def test_sidecar_changed_during_tokenization_is_retained_as_failure(self):
        self.run_python(SITE + r'''
with site() as f:
 def changed(*args):
  Path(f.sidecars['r6x-steer']['path']).write_bytes(b'changed')
  return Tokenizer()
 f.loader.side_effect=changed
 rejected(f,'metadata changed during quality binding')
''')

    def test_s_sidecar_is_an_immutable_output_overlap_input(self):
        self.run_python(SITE + r'''
with site() as f:
 f.output=Path(f.sidecars['r6x-steer']['path'])/'nested-output'
 f.run=lambda: S.bind_site(f.preparation,f.prep_sha,f.binding_path,str(f.materials),str(f.output))
 rejected(f,'output directory overlaps immutable inputs')
 f.backend.assert_not_called()
''')

    def test_package_version_mismatch_is_not_silently_substituted(self):
        self.run_python(SITE + r'''
with site() as f:
 f.packages['tokenizers']['version']='DIFFERENT'
 rejected(f,'tokenizers CPU/native runtime version drift')
 f.loader.assert_not_called()
''')

    def test_changed_frozen_vector_is_not_repaired_or_regenerated(self):
        self.run_python(SITE + r'''
class Drift(Tokenizer):
 def encode(self,*args,**kw): return super().encode(*args,**kw)+[999]
with site() as f:
 f.loader.return_value=Drift()
 rejected(f,'frozen full rendered text or complete IDs differ')
''')

    def test_helper_drift_before_output_is_rejected(self):
        self.run_python(SITE + r'''
with site() as f:
 helper=f.root/'fixture-helper.py'; helper.write_bytes(b'# SYNTHETIC helper\n')
 def helpers(**kw): return {helper.name:{'path':str(helper),'sha256':Q.digest(helper.read_bytes())}}
 def changed(*args):
  helper.write_bytes(b'# changed helper\n')
  return Tokenizer()
 f.loader.side_effect=changed
 with patch.object(S,'support_sources',side_effect=helpers):
  rejected(f,'immutable preparation/site bindings/source changed')
''')


if __name__ == '__main__':
    unittest.main()
