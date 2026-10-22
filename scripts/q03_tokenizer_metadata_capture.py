#!/usr/bin/env python3
"""Separate fixed U176/S188 CPU-only tokenizer distribution evidence.

No package, model, GPU, tokenizer or native-extension import is performed. This
sidecar is producer provenance, never runtime admission. The unchanged reviewed
runtime helper is loaded from exact source bytes, not an ambient module or pyc.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import resource
import subprocess
import types

RUNTIME_HELPER_SHA256 = '36878c7dfe703eacc4c389f02a21ecc7e779a078e1e11d570225f43d22b848c9'


def load_runtime_helper():
    path = Path(__file__).resolve().with_name('q03_runtime_capture.py')
    if path.is_symlink() or path.resolve() != path or not path.is_file():
        raise ValueError('exact reviewed runtime capture helper is missing or aliased')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != RUNTIME_HELPER_SHA256:
        raise ValueError('reviewed runtime capture helper bytes differ')
    module = types.ModuleType('_q03_tokenizer_pinned_runtime_capture')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


R = load_runtime_helper()
require, canonical, digest, json_bytes = R.require, R.canonical, R.digest, R.json_bytes
KIND = 'q03_fixed_image_tokenizer_metadata'
PACKAGE_NAMES = ('transformers', 'tokenizers')
MAX_BYTES = 16 * 1024 * 1024
MAX_PACKAGE_FILES = 20000
_HELPER_SOURCE_SHA256 = digest(Path(__file__).read_bytes())
TOKENIZER_SOURCE_SHA256 = {
    'tokenizers/deepseek_v41.py': '8450587010152846a2e3f6269c661b464615b7e46c312f4e38a397c44218a6e5',
    'tokenizers/deepseek_v41_encoding.py': '8ee7a8010c3ed257a0f1caa4dfbeb3ec68df0b03e36fcd20dad044ec7cf114fe',
    'tokenizers/hf.py': 'fa96702c3b78dea6c70e90d3eaa6054315b953aec647a7ad1ac389931fdba85d',
    'tokenizers/protocol.py': '6eb7b47b5e0c21b0a51a8307d888cc075742dcee440eac2f6b8bb756a30876dd',
    'utils/tool_names.py': 'd2cc3e771d2e2072f02d43879b4012a0eb97a66b1cd1ff206e851abf3a989e67',
    'entrypoints/serve/tokenize/protocol.py': '0bb4ddf2c0b1412e88c9d853e2ef7163f62665df0b8bbef86df5bdbdd9a76e44',
    'entrypoints/serve/tokenize/serving.py': 'fb9fd00cc71df5c052f420a959e39434ce5fd3c84556275eb9dc25d43ff59750',
    'entrypoints/openai/chat_completion/protocol.py': 'f6a21a3b6ae07967b6e4994e14bcf9b39117d8f7dfc57bbc8c2979687784f95f',
}


def tokenizer_source_hashes(arm):
    R.checked_identity(arm)
    value = copy.deepcopy(TOKENIZER_SOURCE_SHA256)
    if arm == 'r6x-unpatched':
        value['entrypoints/openai/chat_completion/protocol.py'] = '0d85d431a4ccb56cbe45665aaa040578d707cc8c728408a72a68666ec085cab2'
    return value
BASELINE_PREFIX = 'Q03_BASELINE_STATIC='
TOKENIZER_PREFIX = 'Q03_TOKENIZER_STATIC='

# Runs immediately after the unmodified reviewed STATIC_CAPTURE in the same
# fresh -I -S -B interpreter. `result` is that collector's unchanged manifest.
# RECORD hashes are never trusted: every included file is read and rehashed.
# Bytecode is excluded explicitly, as it is neither imported nor executed.
TOKENIZER_CAPTURE = r'''
import csv, email.parser, io, stat
need(sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode,
     'fresh isolated no-site no-bytecode interpreter required')
need(not any(k.split('.')[0] in ('transformers','tokenizers','torch','vllm','tilelang','tile_kernels')
             for k in sys.modules),'unexpected package import')
def canon(value):
 return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def nonbytecode(path):
 return not (str(path).endswith('.pyc') or '__pycache__' in path.parts)
def plain_relative(value):
 path=pathlib.PurePosixPath(value)
 return bool(value) and not path.is_absolute() and '..' not in path.parts and str(path)==value and '\\' not in value
def walk_error(error): raise error
tokenizer_sources={}
vllm_root=pathlib.Path(result['installation']['package_root'])
for relative, expected in TOKENIZER_SOURCES.items():
 path=vllm_root/relative
 need(path.resolve()==path and path.is_file() and stat.S_ISREG(path.stat().st_mode),
      'tokenizer/protocol source missing or aliased: '+relative)
 tokenizer_sources[relative]=sha(path)
 need(tokenizer_sources[relative]==expected,'actual tokenizer/protocol source bytes differ: '+relative)
packages={}
for name in ('transformers','tokenizers'):
 distributions=[d for d in importlib.metadata.distributions(path=search)
                if normalize(d.metadata.get('Name',''))==name]
 need(len(distributions)==1,'missing or duplicate tokenizer metadata: '+name)
 dist=distributions[0]
 # Only ordinary filesystem-installed distributions are supported. Bind the
 # selected distribution's actual metadata directory, never a RECORD claim.
 need(isinstance(dist,importlib.metadata.PathDistribution) and
      isinstance(getattr(dist,'_path',None),pathlib.Path),
      'filesystem tokenizer distribution metadata origin required: '+name)
 metadata_path=dist._path
 need(metadata_path.is_absolute() and metadata_path.resolve()==metadata_path and
      metadata_path.is_dir() and metadata_path.name.startswith(name+'-') and
      metadata_path.name.endswith('.dist-info'),
      'selected tokenizer distribution metadata origin differs: '+name)
 metadata_snapshots={}
 for filename in ('METADATA','RECORD'):
  path=metadata_path/filename
  need(path.resolve()==path and path.is_file() and stat.S_ISREG(path.stat().st_mode),
       'selected tokenizer distribution metadata file missing or aliased: '+filename)
  metadata_snapshots[filename]=path.read_bytes()
 fields=email.parser.BytesParser().parsebytes(metadata_snapshots['METADATA'])
 need(len(fields.get_all('Name',[]))==1 and len(fields.get_all('Version',[]))==1,
      'ambiguous tokenizer distribution Name/Version: '+name)
 need(normalize(fields['Name'])==name,'selected tokenizer METADATA Name differs: '+name)
 version=fields['Version']
 need(isinstance(version,str) and version and len(version)<=128 and
      all(c.isalnum() or c in '.!+_-' for c in version),'invalid distribution version: '+name)
 record_text=metadata_snapshots['RECORD'].decode('utf-8')
 need(isinstance(record_text,str) and record_text,'distribution RECORD file inventory missing: '+name)
 record_rows=list(csv.reader(io.StringIO(record_text),strict=True))
 need(record_rows and all(len(row)==3 and row[0] for row in record_rows),'invalid RECORD rows: '+name)
 record_paths=[row[0] for row in record_rows]
 origins=[]
 for base in search:
  found=importlib.machinery.PathFinder.find_spec(name,[base])
  if found is not None:
   need(found.origin is not None,'namespace tokenizer package unsupported: '+name)
   origins.append(pathlib.Path(found.origin))
 need(len(origins)==1,'missing or ambiguous tokenizer package origin: '+name)
 origin=origins[0]
 need(origin.resolve()==origin and origin.name=='__init__.py' and origin.parent.name==name,
      'tokenizer package origin is aliased or unsupported: '+name)
 base=origin.parent.parent
 metadata_roots=set()
 for item in record_paths:
  rel=pathlib.PurePosixPath(str(item))
  if rel.parts and rel.parts[0].startswith(name+'-') and rel.parts[0].endswith('.dist-info'):
   metadata_roots.add(rel.parts[0])
 need(metadata_path.parent==base and metadata_roots=={metadata_path.name},
      'RECORD does not inventory selected distribution metadata root: '+name)
 metadata_root=metadata_path.name
 allowed_roots={name,metadata_root}
 if (base/(name+'.libs')).exists() or (base/(name+'.libs')).is_symlink():
  allowed_roots.add(name+'.libs')
 # Auxiliary wheel libraries are part of the package, not unrelated CLI scripts.
 for item in record_paths:
  rel=pathlib.PurePosixPath(str(item))
  if rel.parts and rel.parts[0]==name+'.libs': allowed_roots.add(name+'.libs')
 recorded={}
 for item in record_paths:
  relative=str(item); rel=pathlib.PurePosixPath(relative)
  if not rel.parts or rel.parts[0] not in allowed_roots or not nonbytecode(rel): continue
  need(plain_relative(relative) and relative not in recorded,'unsafe or duplicate RECORD path: '+relative)
  path=pathlib.Path(dist.locate_file(item))
  need(path==base/relative and path.resolve()==path and path.is_file() and
       stat.S_ISREG(path.stat().st_mode),'missing or aliased tokenizer file: '+relative)
  recorded[relative]={'path':str(path),'sha256':sha(path)}
 observed=set()
 for top in sorted(allowed_roots):
  root=base/top
  need(root.resolve()==root and root.is_dir(),'missing or aliased tokenizer tree: '+top)
  for directory, dirs, files in os.walk(root,followlinks=False,onerror=walk_error):
   for child in dirs+files:
    path=pathlib.Path(directory)/child
    need(not path.is_symlink() and path.resolve()==path,'aliased tokenizer tree entry: '+str(path))
   dirs[:]=[d for d in dirs if d!='__pycache__']
   for child in files:
    path=pathlib.Path(directory)/child; rel=path.relative_to(base)
    if not nonbytecode(rel): continue
    need(stat.S_ISREG(path.stat().st_mode),'nonregular tokenizer file: '+str(path))
    observed.add(str(rel))
 need(observed==set(recorded),'incomplete distribution RECORD/file closure: '+name)
 need(0<len(recorded)<=20000 and name+'/__init__.py' in recorded and
      metadata_root+'/METADATA' in recorded and metadata_root+'/RECORD' in recorded,
      'complete package, METADATA and RECORD required: '+name)
 need(all(recorded[metadata_root+'/'+filename]['sha256']==hashlib.sha256(raw).hexdigest()
          for filename,raw in metadata_snapshots.items()),
      'inventoried METADATA/RECORD differ from selected distribution snapshots: '+name)
 packages[name]={'version':version,'module_path':str(origin),'files':recorded,
                 'inventory_sha256':hashlib.sha256(canon(recorded)).hexdigest()}
need(not any(k.split('.')[0] in ('transformers','tokenizers','torch','vllm','tilelang','tile_kernels')
             for k in sys.modules),'unexpected package import')
metadata={'runtime_manifest_sha256':hashlib.sha256(canon(result)).hexdigest(),
          'capture_helper_sha256':TOKENIZER_HELPER_SHA256,'packages':packages,
          'tokenizer_source_sha256':tokenizer_sources}
print('Q03_TOKENIZER_STATIC='+canon(metadata).decode())
'''


def helper_sha256():
    path = Path(__file__)
    require(path.is_absolute() and path.resolve() == path and path.is_file(),
            'tokenizer capture helper must be an unaliased source file')
    require(digest(path.read_bytes()) == _HELPER_SOURCE_SHA256,
            'tokenizer capture helper bytes changed after load')
    runtime_path = path.with_name('q03_runtime_capture.py')
    require(runtime_path.resolve() == runtime_path and runtime_path.is_file()
            and digest(runtime_path.read_bytes()) == RUNTIME_HELPER_SHA256,
            'reviewed runtime capture helper bytes changed after load')
    return _HELPER_SOURCE_SHA256


def _relative(value):
    if type(value) is not str or not value or '\\' in value or '\x00' in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and '..' not in path.parts and str(path) == value


def validate_packages(packages):
    require(isinstance(packages, dict) and set(packages) == set(PACKAGE_NAMES),
            'both tokenizer distribution inventories required')
    for name in PACKAGE_NAMES:
        package = packages[name]
        require(isinstance(package, dict) and set(package) == {
                'version', 'module_path', 'files', 'inventory_sha256'},
                'tokenizer package provenance fields differ: ' + name)
        version = package['version']
        require(type(version) is str and 0 < len(version) <= 128
                and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.!+_-]*', version) is not None,
                'tokenizer distribution version differs: ' + name)
        module_path = package['module_path']
        require(R._path(module_path) and '\x00' not in module_path
                and Path(module_path).name == '__init__.py'
                and Path(module_path).parent.name == name,
                'tokenizer package module origin differs: ' + name)
        base = Path(module_path).parent.parent
        files = package['files']
        require(isinstance(files, dict) and 0 < len(files) <= MAX_PACKAGE_FILES
                and all(_relative(relative) for relative in files),
                'tokenizer package inventory incomplete or unsafe: ' + name)
        roots = {PurePosixPath(relative).parts[0] for relative in files}
        metadata_roots = {root for root in roots if root.startswith(name + '-') and root.endswith('.dist-info')}
        require(len(metadata_roots) == 1
                and roots <= {name, name + '.libs'} | metadata_roots,
                'tokenizer package inventory root differs: ' + name)
        metadata_root = next(iter(metadata_roots))
        require({name + '/__init__.py', metadata_root + '/METADATA', metadata_root + '/RECORD'} <= set(files),
                'tokenizer package origin, METADATA or RECORD missing: ' + name)
        for relative, pin in files.items():
            require(not relative.endswith('.pyc') and '__pycache__' not in PurePosixPath(relative).parts,
                    'bytecode is outside static tokenizer inventory')
            require(isinstance(pin, dict) and set(pin) == {'path', 'sha256'}
                    and R._path(pin['path']) and pin['path'] == str(base / relative)
                    and R._hash(pin['sha256']), 'tokenizer file path/hash differs: ' + relative)
        require(R._hash(package['inventory_sha256'])
                and digest(canonical(files)) == package['inventory_sha256'],
                'tokenizer package inventory hash differs: ' + name)
    return copy.deepcopy(packages)


def static_capture_plan(arm, capture_id):
    """Inert fixed-image plan. No Docker access is made by this function."""
    plan = R.static_capture_plan(arm, capture_id)
    name = 'q03-tokenizer-evidence-' + capture_id
    argv = plan['argv'][:]
    argv[argv.index('--name') + 1] = name
    argv[argv.index('q03.role=baseline-static')] = 'q03.role=tokenizer-static'
    program = (argv[-1] + '\nTOKENIZER_HELPER_SHA256=' + repr(helper_sha256()) +
               '\nTOKENIZER_SOURCES=' + repr(tokenizer_source_hashes(arm)) + '\n' + TOKENIZER_CAPTURE)
    argv[-1] = program
    plan.update(name=name, argv=argv, program_sha256=digest(program.encode()),
                runtime_capture_script_sha256=RUNTIME_HELPER_SHA256,
                capture_helper_sha256=helper_sha256(), collector_sha256=digest(TOKENIZER_CAPTURE.encode()))
    return plan


def _collector_value(runtime, packages):
    return {'runtime_manifest_sha256': digest(canonical(runtime)),
            'capture_helper_sha256': helper_sha256(), 'packages': packages,
            'tokenizer_source_sha256': tokenizer_source_hashes(runtime['arm'])}


def canonical_stdout(runtime, packages):
    return (BASELINE_PREFIX.encode() + canonical(runtime) + b'\n' +
            TOKENIZER_PREFIX.encode() + canonical(_collector_value(runtime, packages)) + b'\n')


def validate_static_capture_output(raw, arm):
    require(type(raw) is bytes and len(raw) <= MAX_BYTES, 'tokenizer capture output exceeds bound')
    lines = raw.decode('utf-8').splitlines()
    require(len(lines) == 2 and lines[0].startswith(BASELINE_PREFIX)
            and lines[1].startswith(TOKENIZER_PREFIX), 'exact runtime/tokenizer capture pair required')
    runtime = R.validate_capture(json_bytes(lines[0][len(BASELINE_PREFIX):]), arm)
    metadata = json_bytes(lines[1][len(TOKENIZER_PREFIX):])
    require(isinstance(metadata, dict) and set(metadata) == {
            'runtime_manifest_sha256', 'capture_helper_sha256', 'packages', 'tokenizer_source_sha256'},
            'static tokenizer collector fields differ')
    packages = validate_packages(metadata['packages'])
    require(metadata == _collector_value(runtime, packages), 'static tokenizer runtime/helper binding differs')
    require(raw == canonical_stdout(runtime, packages), 'noncanonical or extra tokenizer capture output')
    return runtime, packages


def validate_metadata(sidecar, arm):
    """Offline strict validation for exporters; never imports tokenizer packages.

    The sidecar's hashes establish consistency, not an authenticated execution or
    runtime qualification. Raw capture evidence still needs independent review.
    """
    identity = R.checked_identity(arm)
    require(isinstance(sidecar, dict) and set(sidecar) == {
            'schema_version', 'kind', 'arm', 'image', 'source_head', 'source_tree',
            'runtime_manifest', 'runtime_manifest_sha256', 'image_inspect', 'capture', 'tokenizer_source_sha256',
            'packages', 'status', 'runtime_qualification', 'requires_independent_review'},
            'tokenizer metadata sidecar fields differ')
    require(type(sidecar['schema_version']) is int and sidecar['schema_version'] == 1
            and sidecar['kind'] == KIND and all(sidecar[k] == v for k, v in identity.items()),
            'tokenizer metadata fixed arm/image/source differs')
    require(sidecar['status'] == 'captured-unreviewed' and sidecar['runtime_qualification'] is False
            and sidecar['requires_independent_review'] is True,
            'tokenizer metadata is unreviewed evidence, not runtime admission')
    runtime = R.validate_capture(sidecar['runtime_manifest'], arm)
    require(R._hash(sidecar['runtime_manifest_sha256'])
            and sidecar['runtime_manifest_sha256'] == digest(canonical(runtime)),
            'tokenizer metadata runtime manifest hash differs')
    R.validate_image_identity(sidecar['image_inspect'], arm)
    R.validate_image(arm, sidecar['image_inspect'], runtime)
    require(sidecar['tokenizer_source_sha256'] == tokenizer_source_hashes(arm),
            'tokenizer metadata complete tokenizer/protocol source bytes differ')
    packages = validate_packages(sidecar['packages'])
    capture = sidecar['capture']
    require(isinstance(capture, dict) and set(capture) == {
            'capture_id', 'runtime_capture_script_sha256', 'capture_helper_sha256',
            'collector_sha256', 'program_sha256', 'raw_stdout_sha256'},
            'tokenizer metadata capture provenance fields differ')
    plan = static_capture_plan(arm, capture['capture_id'])
    require(all(capture[key] == plan[key] for key in (
            'runtime_capture_script_sha256', 'capture_helper_sha256', 'collector_sha256', 'program_sha256')),
            'tokenizer metadata exact capture helper/program bytes differ')
    require(capture['raw_stdout_sha256'] == digest(canonical_stdout(runtime, packages)),
            'tokenizer metadata raw capture hash differs')
    return copy.deepcopy(sidecar)


def child_limits():
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_BYTES, MAX_BYTES))


def execute(directory_fd, label, argv, timeout, runner):
    """Retain bounded raw results; no shell and no network or cleanup action."""
    execution = dict(argv_sha256=digest(canonical(argv)), timeout_seconds=timeout, returncode=None)
    with R.private_file(directory_fd, label + '.stdout') as stdout:
        with R.private_file(directory_fd, label + '.stderr') as stderr:
            try:
                result = runner(argv, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                timeout=timeout, check=False, preexec_fn=child_limits)
                execution['returncode'] = result.returncode
            except BaseException as error:
                execution.update(error_type=type(error).__name__, error=str(error))
                raise
            finally:
                for stream in (stdout, stderr):
                    stream.flush()
                    os.fsync(stream.fileno())
                R.save(directory_fd, label + '.execution.json', canonical(execution))
    require(result.returncode == 0, label + ' failed; raw evidence retained')
    fd = os.open(label + '.stdout', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    with os.fdopen(fd, 'rb') as stream:
        require(os.fstat(stream.fileno()).st_size <= MAX_BYTES, 'raw tokenizer result exceeds size bound')
        return stream.read(MAX_BYTES + 1)


def capture(arm, capture_id, output, runner=None):
    """Finite retained-image metadata capture, not service or runtime authority."""
    plan = static_capture_plan(arm, capture_id)
    directory_fd = R.open_output(output)
    runner = subprocess.run if runner is None else runner
    receipt = dict(schema_version=1, arm=arm, image=plan['image'], capture_id=capture_id,
                   status='failed-unreviewed', requires_independent_review=True,
                   runtime_qualification=False, container_name=plan['name'])
    try:
        R.save(directory_fd, 'plan.json', canonical(plan))
        R.save(directory_fd, 'source.json', canonical({key: plan[key] for key in (
            'runtime_capture_script_sha256', 'capture_helper_sha256', 'collector_sha256', 'program_sha256')}))
        images = json_bytes(execute(directory_fd, 'image-inspect',
                                   R.DOCKER + ['image', 'inspect', plan['image']], 30, runner))
        require(isinstance(images, list) and len(images) == 1, 'ambiguous retained image inspect')
        R.validate_image_identity(images[0], arm)
        absence = R.DOCKER + ['container', 'ls', '--all', '--filter',
                             'name=^/' + plan['name'] + '$', '--format', '{{.Names}}']
        require(not execute(directory_fd, 'preabsence', absence, 30, runner).strip(),
                'tokenizer capture container already exists; nothing started')
        raw = execute(directory_fd, 'capture', plan['argv'], plan['timeout_seconds'], runner)
        require(not execute(directory_fd, 'postabsence', absence, 30, runner).strip(),
                'tokenizer capture container remains; manual reconciliation required')
        runtime, packages = validate_static_capture_output(raw, arm)
        observation = R.validate_image(arm, images[0], runtime)
        manifest = canonical(runtime)
        provenance = {key: plan[key] for key in (
            'capture_id', 'runtime_capture_script_sha256', 'capture_helper_sha256',
            'collector_sha256', 'program_sha256')}
        provenance['raw_stdout_sha256'] = digest(raw)
        sidecar = dict(schema_version=1, kind=KIND, **R.checked_identity(arm),
                       runtime_manifest=runtime, runtime_manifest_sha256=digest(manifest),
                       image_inspect=images[0], capture=provenance, packages=packages,
                       tokenizer_source_sha256=tokenizer_source_hashes(arm),
                       status='captured-unreviewed', runtime_qualification=False,
                       requires_independent_review=True)
        validate_metadata(sidecar, arm)
        R.save(directory_fd, 'runtime-manifest.json', manifest)
        R.save(directory_fd, 'image-observation.json', canonical(observation))
        R.save(directory_fd, 'tokenizer-metadata.json', canonical(sidecar))
        receipt.update(status='captured-unreviewed', runtime_manifest_sha256=digest(manifest),
                       runtime_manifest_path=str(Path(output) / 'runtime-manifest.json'),
                       tokenizer_metadata_sha256=digest(canonical(sidecar)),
                       tokenizer_metadata_path=str(Path(output) / 'tokenizer-metadata.json'),
                       raw_stdout_sha256=digest(raw))
    except BaseException as error:
        receipt.update(error_type=type(error).__name__, error=str(error),
                       container_state='not proven absent; inspect this exact capture name before retry')
        R.save(directory_fd, 'receipt.json', canonical(receipt))
        raise
    else:
        R.save(directory_fd, 'receipt.json', canonical(receipt))
        return receipt
    finally:
        os.close(directory_fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    for operation in ('plan', 'capture'):
        command = sub.add_parser(operation)
        command.add_argument('arm', choices=R.REVISED_ARMS)
        command.add_argument('--capture-id', required=True)
        if operation == 'capture':
            command.add_argument('--output', required=True)
    args = parser.parse_args()
    result = (static_capture_plan(args.arm, args.capture_id) if args.operation == 'plan'
              else capture(args.arm, args.capture_id, args.output))
    print(canonical(result).decode())


if __name__ == '__main__':
    main()
