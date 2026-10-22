"""Focused synthetic corpus/loader tests, never real token/model/runtime evidence."""
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BOOT = '''import sys
sys.path.insert(0, sys.argv[1])
import q03_quality_portable_corpus as S
import copy, json, tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
Q, P, E, N, L = S.Q, S.P, S.E, S.N, S.L
'''
FIXTURE = '''
class Tokenizer:
    def apply_chat_template(self, messages, tokenize=False, **kw):
        assert kw == dict(add_generation_prompt=True, continue_final_message=False, **({'truncation': False} if tokenize else {}))
        text = '<thinking-high>' + messages[0]['content'] + '<assistant>'
        return self.encode(text, add_special_tokens=False, truncation=False) if tokenize else text
    def encode(self, text, **kw):
        assert kw == dict(add_special_tokens=False, truncation=False)
        return [ord(c) for c in text]
prompts = [{'id': f'fixture-{i:02d}', 'text': f' exact prompt {i}\\r\\n中文 '} for i in range(60)]
metadata = [{'id': p['id'], 'category': 'fixture', 'language': 'zh' if i % 2 else 'en'} for i, p in enumerate(prompts)]
prompt_raw = b''.join(Q.canonical(p) + b'\\n' for p in prompts)
S.PROMPTS_SHA256 = Q.digest(prompt_raw)
S.CASE_INDEX_SHA256 = Q.digest(Q.canonical(metadata))
identity = dict(model_revision=N.MODEL_REVISION, tokenizer_revision=N.MODEL_REVISION,
    model_config_sha256=P.REGISTERED_ASSETS['config.json'],
    tokenizer_assets_sha256={k:v for k,v in P.REGISTERED_ASSETS.items() if k != 'config.json'},
    weights_manifest_sha256='a'*64, encoding_source_sha256=Q.NATIVE_SOURCES['deepseek_v41_encoding.py'],
    chat_template_sha256=Q.TEMPLATE_SHA, special_tokens_sha256=P.REGISTERED_ASSETS['tokenizer_config.json'])
snapshot = {'prompts.jsonl': prompt_raw}
content = S.construct_content(Tokenizer(), identity, snapshot, prompts, metadata)
'''


class QualityPortableTest(unittest.TestCase):
    def run_python(self, code, scripts=None):
        result = subprocess.run([sys.executable, '-B', '-c', code, str(scripts or ROOT / 'scripts')],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_default_rendered_and_direct_complete_vectors(self):
        self.run_python(BOOT + FIXTURE + '''
assert len(content['cases']) == len(S.frozen_vectors(Tokenizer(), content)) == 60
assert content['prompts_jsonl'].encode() == prompt_raw
assert content['cases'][0]['input_ids'] == [ord(c) for c in content['cases'][0]['rendered_text']]
assert content['default_rendering'] == S.DEFAULT_RENDERING
assert 'gold' not in content and 'artifacts' not in content
''')

    def test_exact_prompts_metadata_no_truncation_override_or_missing_ids(self):
        self.run_python(BOOT + FIXTURE + '''
for mutate in (
 lambda v: v['cases'].pop(),
 lambda v: v['cases'][0].update(language='en' if v['cases'][0]['language']=='zh' else 'zh'),
 lambda v: v['cases'][0]['input_ids'].pop(),
 lambda v: v['cases'][0].update(input_ids=[True]),
 lambda v: v['cases'][0].update(rendered_text='changed'),
 lambda v: v.update(prompts_jsonl=v['prompts_jsonl']+' '),
 lambda v: v['default_rendering'].update(thinking=False),
 lambda v: v['chat_tokenization'].update(truncation=True),
 lambda v: v.update(artifacts={}),
):
 bad=copy.deepcopy(content); mutate(bad)
 try: S.validate_content(bad)
 except RuntimeError: pass
 else: raise AssertionError('invalid portable content accepted')
''')

    def test_direct_rendered_disagreement_is_not_accepted(self):
        self.run_python(BOOT + FIXTURE + '''
class Drift(Tokenizer):
 def apply_chat_template(self, *args, **kwargs):
  result=super().apply_chat_template(*args, **kwargs)
  return result+[999] if kwargs.get('tokenize') else result
try: S.construct_content(Drift(), identity, snapshot, prompts, metadata)
except RuntimeError as error: assert 'rendered/direct' in str(error)
else: raise AssertionError('direct encoding drift accepted')
''')

    def test_preparation_is_hash_bound_non_admissible_and_site_id_drift_fails(self):
        self.run_python(BOOT + FIXTURE + '''
with tempfile.TemporaryDirectory() as d:
 path=Path(d)/'prep.json'
 prep=dict(schema_version=1,kind=S.KIND,admissibility=P.ADMISSIBILITY,content=content,content_sha256=Q.digest(Q.canonical(content)))
 pin=Q.exclusive_json(path,prep)
 assert S.read_preparation(path,pin)[1]==prep
 try: L.validate_corpus(prep)
 except RuntimeError: pass
 else: raise AssertionError('portable artifact admitted')
 try: S.read_preparation(path,'0'*64)
 except RuntimeError: pass
 else: raise AssertionError('wrong pin accepted')
class Drift(Tokenizer):
 def encode(self,*args,**kwargs): return super().encode(*args,**kwargs)+[999]
try: S.frozen_vectors(Drift(),content)
except RuntimeError as error: assert 'complete IDs differ' in str(error)
else: raise AssertionError('site vector drift accepted')
''')

    def test_all_material_bytes_verified_as_data_only(self):
        self.run_python(BOOT + FIXTURE + '''
with tempfile.TemporaryDirectory() as d:
 root=Path(d)
 cases=[dict(**m,prompt_text=p['text'],prompt_utf8_sha256=Q.digest(p['text'].encode()),prompt_utf8_bytes=len(p['text'].encode()),gold={'unexecuted':'data'}) for p,m in zip(prompts,metadata)]
 files={'prompts.jsonl':prompt_raw,'cases-with-gold.json':Q.canonical(cases),'reference_code.py':b"raise AssertionError('MUST NEVER EXECUTE')"}
 files.update({f'original-{i}.txt':b'original' for i in range(31)})
 sums=b''.join((Q.digest(raw)+'  ./'+name+'\\n').encode() for name,raw in sorted(files.items()))
 S.MATERIAL_SHA256=Q.digest(sums); S.CASES_SHA256=Q.digest(files['cases-with-gold.json'])
 for name,raw in {**files,'SHA256SUMS':sums}.items(): (root/name).write_bytes(raw)
 got=S.read_materials(str(root)); assert len(got[1])==35 and got[2]==prompts and got[3]==metadata
 (root/'reference_code.py').write_bytes(b'changed')
 try: S.read_materials(str(root))
 except RuntimeError as error: assert 'material file drift' in str(error)
 else: raise AssertionError('original file drift accepted')
''')

    def test_full_site_validator_failure_cannot_fall_back_to_cloud(self):
        self.run_python(BOOT + FIXTURE + '''
with tempfile.TemporaryDirectory() as d:
 root=Path(d); path=root/'prep.json'; bindings=root/'bindings.json'; bindings.write_bytes(b'{}')
 prep=dict(schema_version=1,kind=S.KIND,admissibility=P.ADMISSIBILITY,content=content,content_sha256=Q.digest(Q.canonical(content)))
 pin=Q.exclusive_json(path,prep)
 with patch.object(S,'isolate'), patch.object(E,'validate_bindings',side_effect=RuntimeError('retained manifest file absent in snapshot')) as full, patch.object(P,'packages',side_effect=AssertionError('premature package import')):
  try: S.bind_site(path,pin,bindings,str(root),str(root/'out'))
  except RuntimeError as error: assert 'manifest file absent' in str(error)
  else: raise AssertionError('missing full site proof accepted')
  full.assert_called_once_with({})
 assert not (root/'out').exists()
''')

    def test_bind_compares_all_rows_keeps_original_and_uses_exact_final_schema(self):
        self.run_python(BOOT + FIXTURE + '''
from contextlib import ExitStack
with tempfile.TemporaryDirectory() as d:
 root=Path(d); path=root/'prep.json'; binding_path=root/'bindings.json'; materials=root/'materials'; materials.mkdir()
 (materials/'prompts.jsonl').write_bytes(prompt_raw)
 prep=dict(schema_version=1,kind=S.KIND,admissibility=P.ADMISSIBILITY,content=content,content_sha256=Q.digest(Q.canonical(content)))
 pin=Q.exclusive_json(path,prep); original=path.read_bytes()
 bindings=dict(native_cpu_bindings={'path':str(root/'native.json')},native_sources={'r6x188':{'root':str(root/'source-a')},'native197-off':{'root':str(root/'source-b')}},toolbox_root=str(root/'toolbox'))
 binding_path.write_bytes(Q.canonical(bindings))
 native=dict(model_root=str(root/'model'),weights_manifest={'path':str(root/'weights.json')},runtime_metadata={'path':str(root/'runtime.json')})
 export=dict(identity={**identity,'transformers_version':'FIXTURE','tokenizers_version':'FIXTURE'},artifacts={'fixture':'unreal'})
 inputs=(native,export,{'native197-off':{}},{},{})
 evidence={n:{'version':'FIXTURE'} for n in ('transformers','tokenizers')}
 with ExitStack() as stack:
  for obj,name,value in [(S,'isolate',None),(P,'recheck_packages',None),(L,'artifact_files',[])]: stack.enter_context(patch.object(obj,name,return_value=value))
  full=stack.enter_context(patch.object(E,'validate_bindings',return_value=inputs))
  stack.enter_context(patch.object(S,'read_materials',return_value=(materials,snapshot,prompts,metadata)))
  stack.enter_context(patch.object(P,'packages',return_value=({'transformers':SimpleNamespace(TokenizersBackend=object),'tokenizers':object()},evidence)))
  stack.enter_context(patch.object(N,'native_tokenizer',return_value=Tokenizer()))
  stack.enter_context(patch.object(S,'construct_content',side_effect=AssertionError('regeneration attempted')))
  result=S.bind_site(path,pin,binding_path,str(materials),str(root/'out'))
  assert full.call_count==2
 assert path.read_bytes()==original
 corpus=Q.json_bytes(Path(result['corpus']['path']).read_bytes())
 assert set(corpus)=={'schema_version','workload','model','material_sha256','prompts','tokenizer_identity','artifacts','cases','default_rendering'}
 assert len(corpus['cases'])==60 and set(corpus['cases'][0])=={'id','category','language','prompt_sha256','input_ids'}
 receipt=Q.json_bytes(Path(result['site_link_receipt']['path']).read_bytes())
 assert receipt['schema_version']==1 and 'producer_provenance' not in receipt
 assert receipt['comparison']==dict(chat_vectors=60,complete_ids_equal=True,complete_rendered_text_equal=True,regenerated_prompts=False)
''')

    def isolated_scripts(self, root):
        scripts = root / 'scripts'; scripts.mkdir()
        for name in ('q03_quality_portable_corpus.py','q03_legacy_portable_corpus.py','q03_legacy_corpus_export.py',
            'q03_native_corpus_export.py','q03_legacy_case_replay.py','q03_qualification.py','q03_owner_model.py',
            'q03_held_window.py','q03_http_toolbox.json'):
            shutil.copy2(ROOT / 'scripts' / name, scripts / name)
        return scripts

    def cli_missing(self, scripts, root):
        result = subprocess.run([sys.executable, '-B', str(scripts / 'q03_quality_portable_corpus.py'),
            'prepare-tokenizer-only','--bindings',str(root/'absent-bindings.json'),
            '--materials',str(root/'absent-materials'),'--output-directory',str(root/'never-created')],
            capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,1,result.stdout+result.stderr)
        self.assertIn('absent-bindings.json',result.stderr)
        self.assertFalse((root/'never-created').exists())

    def test_initial_helper_timestamp_pyc_and_sourceless_cache_cannot_execute(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); scripts=self.isolated_scripts(root); marker=root/'POISON_EXECUTED'
            for source in scripts.glob('*.py'):
                if source.name=='q03_quality_portable_corpus.py': continue
                payload=(f'from pathlib import Path\nPath({str(marker)!r}).write_text("executed")\n').encode()
                poison=root/'poison.py'; poison.write_bytes(payload+b'#'+b' '*(source.stat().st_size-len(payload)-1))
                os.utime(poison,ns=(source.stat().st_atime_ns,source.stat().st_mtime_ns))
                cache=Path(importlib.util.cache_from_source(str(source))); cache.parent.mkdir(exist_ok=True)
                py_compile.compile(str(poison),cfile=str(cache),doraise=True,invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
                shutil.copy2(cache,source.with_suffix('.pyc'))
            self.cli_missing(scripts,root); self.assertFalse(marker.exists())

    def test_package_and_extension_shadows_cannot_execute(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); scripts=self.isolated_scripts(root); marker=root/'SHADOW_EXECUTED'
            for source in scripts.glob('*.py'):
                if source.name=='q03_quality_portable_corpus.py': continue
                package=scripts/source.stem; package.mkdir()
                (package/'__init__.py').write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("executed")\n')
                (scripts/(source.stem+importlib.machinery.EXTENSION_SUFFIXES[0])).write_bytes(b'INVALID_SYNTHETIC_EXTENSION')
            self.cli_missing(scripts,root); self.assertFalse(marker.exists())

    def test_prefix_and_preimport_guards_before_initial_helper_import(self):
        self.run_python('''import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
original=Path.exists
with patch.object(Path,'exists',lambda p: True if str(p)=='/__q03_cpu_export_unused_bytecode__' else original(p)):
 try: import q03_quality_portable_corpus
 except RuntimeError as e: assert 'prefix already exists' in str(e)
 else: raise AssertionError('existing prefix accepted')
assert 'q03_legacy_portable_corpus' not in sys.modules
''')
        self.run_python('''import sys
sys.path.insert(0,sys.argv[1])
import q03_owner_model
try: import q03_quality_portable_corpus
except RuntimeError as e: assert 'fresh interpreter' in str(e)
else: raise AssertionError('preimported helper accepted')
assert 'q03_legacy_portable_corpus' not in sys.modules
''')

    def test_import_restores_caller_state_and_isolation_blocks_runtime(self):
        self.run_python('''import sys
sys.path.insert(0,sys.argv[1]); before=sys.dont_write_bytecode,sys.pycache_prefix; finders=list(sys.meta_path)
import q03_quality_portable_corpus as S
assert (sys.dont_write_bytecode,sys.pycache_prefix)==before and sys.meta_path==finders
S.isolate()
assert len(S.support_sources())==9
for name in S.N.FORBIDDEN_IMPORTS:
 assert name in sys.modules and sys.modules[name] is None
 try: __import__(name)
 except ImportError: pass
 else: raise AssertionError('runtime import accepted')
''')


if __name__ == '__main__':
    unittest.main()
