#!/usr/bin/env python3
"""Finite U176/S188 CPU image evidence, independent of runtime admission.

The frozen inventories and stdlib collector are copied verbatim from admission
commit eb775f13d4a61ece739003c5fca860864d91ea63, q03_baseline_identity.py.
This standalone slice deliberately does not import that admission/controller.
Successful capture is unreviewed evidence to bind later, NEVER qualification.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import stat
import subprocess

NATIVE_NAMES = ('vllm._C_stable_libtorch', 'vllm._moe_C_stable_libtorch')

REVISED_IDENTITIES = {'r6x-steer': {'image': 'sha256:8ad0da0366af0560678e09dbccefedc6688a58d79758b4f11fc63ef69ede4a0a',
               'source_head': 'ebb44766d6f04d3a9272b712778d7f2fa2cec1fd',
               'source_tree': 'ac79557915e3ee2778f8f6353a3d35f848f564e4'},
 'r6x-unpatched': {'image': 'sha256:fc6a9ef83dd1a741a32c2724263e9c05a2635dc8154784ffe0f9c36709018082',
                   'source_head': '7316c7f9fae1bc4a0b7ab1848de0db4143eec5d2',
                   'source_tree': '4d6851c8bb1397a55254ba88905a87d9785b98dc'}}

REVISED_SOURCE_SHA256 = {'r6x-steer': {'__init__.py': 'fd3708e5a13abe98c566afd79a6b1987cef70c0548bb19c1218ff6a7f9d43346',
               'config/parallel.py': 'e3802272e792ad710b8a55233bdc7ba7cd08bcee79c5259098b34bcab2b431a7',
               'engine/arg_utils.py': '454d9cc4d2f6575d19bddc19fe7845878eb355020a8a9557905c4bd7e70730a3',
               'entrypoints/cli/serve.py': '3eb557c63f1f7bdda22287fe9fcae184b40363ae211672c2b17742e33d255847',
               'entrypoints/launchers/api_server/routers.py': 'c2f1bb2708f44f0fe24f3d8ab8f884c7262e551c6557ebc606263abc4d7b1db1',
               'entrypoints/openai/api_server.py': '470ff555bdc19f8e17edf4ee7b6ec3f49ff5af0f773e2a7f91bab536e2239109',
               'entrypoints/serve/__init__.py': '36278041128fab7bebb55fab25db39e9b229a660239d13ac910202b27d7d0a51',
               'entrypoints/serve/dev/rpc/api_router.py': '02b3ac71867b333548b15cb5d8382877d587b211c4ef80fdd881712ab523d107',
               'envs.py': '90760d268818ad20fd471ebc40c26ed65ca44bfd39e88b75e8e88d16b6146f66',
               'model_executor/layers/mhc.py': '923828e71cc695c9ce393c77bbc3bc3d69370e12ee37678a11078618e1cd36ee',
               'models/deepseek_v4_1/nvidia/dspark.py': '52bce488dcb7aa6ca66581f219364207cc5308da00c1e8548ac98f8380a94e1c',
               'models/deepseek_v4_1/nvidia/model.py': 'cc90dad8970cf7bc9cd46271ff9df95b38b0eaa6b5bb11e8134cbf253469955b',
               'models/deepseek_v4_1/steering/__init__.py': '8e7e10a737b2033b7ac68a049320113a0d640b0898718a833fcbbd165dec88af',
               'models/deepseek_v4_1/steering/artifact.py': '3afe34c5ae1f2ecef8ea5a5b8de58e00bdec0964de2f337f10d7c15c36837389',
               'models/deepseek_v4_1/steering/cache.py': 'a3b9a84349a13f7e174a0f8d4edd106ec9144a423bca7f7e6f2aa9d839b51040',
               'models/deepseek_v4_1/steering/config.py': '5305406bbef89a63700daf3e50942abd9b96481e5204847db0c248ccbb020f62',
               'models/deepseek_v4_1/steering/consume.py': '917dc0646dd5573c6d5d27314f7e08c58a2041f588bd0a8a1d9e2618096d17db',
               'models/deepseek_v4_1/steering/ops.py': 'd539205a087213fea9faf1c4ceff7db520db458eea91b1075e66d80fbb72cb27',
               'models/deepseek_v4_1/steering/profile.py': '761b7be767a8cea168ace7d8a1d7e9f63991372751d561f2073e53f5d93912c7',
               'models/deepseek_v4_1/steering/reference.py': '0030a4e686752db4ad382e6a2ee39c93943827df6ee40eed7f8c39881267af65',
               'models/deepseek_v4_1/steering/request.py': 'c57520b61716be75cb76bbf9c83ade3e6c355421e37cd792438de062bfcface3',
               'models/deepseek_v4_1/steering/row_kernel.py': '6849de8fa57d12be3a8c386922da58222c4340a825ffd4d5956f6f574c7bef13',
               'models/deepseek_v4_1/steering/rows.py': '3736525745fb324fe285f4eb091a6b72d407a73db64a46db6f9c72eaa7d288f3',
               'models/deepseek_v4_1/steering/runtime.py': 'aa17173fdb25772273a2c5a2d0053e580b56c185b2091f19e41a56ee8c3ea346',
               'tokenizers/deepseek_v41.py': '8450587010152846a2e3f6269c661b464615b7e46c312f4e38a397c44218a6e5',
               'tokenizers/deepseek_v41_encoding.py': '8ee7a8010c3ed257a0f1caa4dfbeb3ec68df0b03e36fcd20dad044ec7cf114fe',
               'v1/serial_utils.py': 'd75c17a098231b2398cbdb7da6f9983e5b9229c0ba138b2874934d45fafd1f28',
               'v1/worker/gpu/model_runner.py': 'f8ea26dce6ac8ba54eabe31621055516c7764b721ef1849cb48df0ab01ed1d10',
               'v1/worker/gpu_model_runner.py': '74842cd4f20704768e2ac8b0ec3ccd3dcb80af6115675bf4e3fc703cec146340',
               'v1/worker/gpu_worker.py': '1dfdf4fd410947bfd32d69b53224a4aea26d2767693596481aea4a14339e4bb3',
               'v1/worker/worker_base.py': '3a0ca44daf0b00fe626ca21a5b71d1a0e0e506759449a7fb8967292edccbcf35'},
 'r6x-unpatched': {'__init__.py': 'fd3708e5a13abe98c566afd79a6b1987cef70c0548bb19c1218ff6a7f9d43346',
                   'config/parallel.py': 'e3802272e792ad710b8a55233bdc7ba7cd08bcee79c5259098b34bcab2b431a7',
                   'engine/arg_utils.py': '454d9cc4d2f6575d19bddc19fe7845878eb355020a8a9557905c4bd7e70730a3',
                   'entrypoints/cli/serve.py': '3eb557c63f1f7bdda22287fe9fcae184b40363ae211672c2b17742e33d255847',
                   'entrypoints/launchers/api_server/routers.py': 'c2f1bb2708f44f0fe24f3d8ab8f884c7262e551c6557ebc606263abc4d7b1db1',
                   'entrypoints/openai/api_server.py': '470ff555bdc19f8e17edf4ee7b6ec3f49ff5af0f773e2a7f91bab536e2239109',
                   'entrypoints/serve/__init__.py': '36278041128fab7bebb55fab25db39e9b229a660239d13ac910202b27d7d0a51',
                   'entrypoints/serve/dev/rpc/api_router.py': '02b3ac71867b333548b15cb5d8382877d587b211c4ef80fdd881712ab523d107',
                   'envs.py': '90760d268818ad20fd471ebc40c26ed65ca44bfd39e88b75e8e88d16b6146f66',
                   'model_executor/layers/mhc.py': '923828e71cc695c9ce393c77bbc3bc3d69370e12ee37678a11078618e1cd36ee',
                   'models/deepseek_v4_1/nvidia/dspark.py': '52bce488dcb7aa6ca66581f219364207cc5308da00c1e8548ac98f8380a94e1c',
                   'models/deepseek_v4_1/nvidia/model.py': '9443fedb35140e8e146f71ecd2603f40c0ebdc90de0488c47d711fcd284833fa',
                   'tokenizers/deepseek_v41.py': '8450587010152846a2e3f6269c661b464615b7e46c312f4e38a397c44218a6e5',
                   'tokenizers/deepseek_v41_encoding.py': '8ee7a8010c3ed257a0f1caa4dfbeb3ec68df0b03e36fcd20dad044ec7cf114fe',
                   'v1/serial_utils.py': 'd75c17a098231b2398cbdb7da6f9983e5b9229c0ba138b2874934d45fafd1f28',
                   'v1/worker/gpu/model_runner.py': '4dad9aa8e7964f33a11ce695fadfe010882f112d82e31a892a855527e8bc4d36',
                   'v1/worker/gpu_model_runner.py': '74842cd4f20704768e2ac8b0ec3ccd3dcb80af6115675bf4e3fc703cec146340',
                   'v1/worker/gpu_worker.py': '1dfdf4fd410947bfd32d69b53224a4aea26d2767693596481aea4a14339e4bb3',
                   'v1/worker/worker_base.py': '3a0ca44daf0b00fe626ca21a5b71d1a0e0e506759449a7fb8967292edccbcf35'}}

STATIC_CAPTURE = r'''
import ast, hashlib, importlib.machinery, importlib.metadata, json, os, pathlib, platform, sys, sysconfig
p = P
def need(ok, message):
 if not ok: raise RuntimeError(message)
def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
 return h.hexdigest()
pythonpath=os.environ.get('PYTHONPATH','')
search=list(dict.fromkeys([v for v in pythonpath.split(':') if v]+[sysconfig.get_path('purelib'),sysconfig.get_path('platlib')]))
need(all(pathlib.Path(v).is_absolute() for v in search),'relative package search path')
spec=importlib.machinery.PathFinder.find_spec('vllm',search)
need(spec is not None and spec.origin is not None,'image vllm package missing')
root=pathlib.Path(spec.origin).resolve().parent
sources={}
for rel in p.get('absent_source_paths', []):
 path=root/rel
 need(not path.exists() and not path.is_symlink(),'unpatched steering source must be absent')
for rel, expected in p['source_sha256'].items():
 path=root/rel
 need(path.resolve()==path and path.is_file(),'source path alias or missing')
 sources[rel]=sha(path)
 need(sources[rel]==expected,'actual source bytes differ: '+rel)
native_paths={};native_sha={}
for name in p['native_names']:
 matches=list(root.glob(name.split('.')[-1]+'.*.so'))
 need(len(matches)==1 and matches[0].resolve()==matches[0],'ambiguous native ELF')
 path=matches[0]
 with path.open('rb') as f: need(f.read(4)==b'\x7fELF','native file is not ELF')
 native_paths[name]=str(path);native_sha[name]=sha(path)
versions={}
def normalize(name): return name.lower().replace('-','_').replace('.','_')
for distribution in importlib.metadata.distributions(path=search):
 name=normalize(distribution.metadata.get('Name',''))
 if name in ('torch','tilelang','tile_kernels'):
  need(name not in versions,'duplicate package metadata: '+name)
  versions[name]=distribution.version
need(set(versions)=={'torch','tilelang','tile_kernels'},'package metadata missing')
package_origins={};package_sha={}
for name in ('torch','tilelang','tile_kernels'):
 package=importlib.machinery.PathFinder.find_spec(name,search)
 need(package is not None and package.origin is not None,'package origin missing: '+name)
 path=pathlib.Path(package.origin)
 need(path.resolve()==path and path.name=='__init__.py','package origin alias: '+name)
 package_origins[name]=str(path);package_sha[name]=sha(path)
version_path=pathlib.Path(package_origins['torch']).with_name('version.py')
need(version_path.resolve()==version_path and version_path.is_file(),'Torch version source missing or aliased')
assignments=[]
for statement in ast.parse(version_path.read_bytes()).body:
 if isinstance(statement,(ast.Assign,ast.AnnAssign)):
  targets=statement.targets if isinstance(statement,ast.Assign) else [statement.target]
  if any(isinstance(t,ast.Name) and t.id=='__version__' for t in targets):
   need(isinstance(statement.value,ast.Constant) and isinstance(statement.value.value,str),'Torch version is not a literal')
   assignments.append(statement.value.value)
need(len(assignments)==1 and assignments[0],'Torch version literal missing or ambiguous')
need(not any(k=='torch' or k=='vllm' or k.startswith(('torch.','vllm.')) for k in sys.modules),'unexpected model import')
result=dict(schema_version=1,**p['identity'],installation={'mode':'image-source','package_root':str(root)},
 platform={'python':platform.python_version(),'machine':platform.machine(),'system':platform.system()},
 metadata_versions=versions,package_origins=package_origins,package_sha256=package_sha,
 torch_version=assignments[0],torch_version_sha256=sha(version_path),
 native_paths=native_paths,native_sha256=native_sha,source_sha256=sources,pythonpath=pythonpath)
if 'absent_source_paths' in p: result['absent_source_paths']=p['absent_source_paths']
print('Q03_BASELINE_STATIC='+json.dumps(result,sort_keys=True,separators=(',',':'),allow_nan=False))
'''

IMAGE_FREEZE = {'component_sources': {'b12x': {'head': '69e5795e2141fbb5fa2b3af096d51e754939bb41',
                                'tree': '1a647a311ca55d45070ab83d60668d9c8fb8886c'},
                       'nccl': {'head': 'eeacf1c6343f0499e4e7ab20ec7eecfb6f62eda6',
                                'tree': '6af10aa730a64fcee8781d62340a9c9184e79469'},
                       'sparknet': {'head': '3bc8d10104a5012244a0424d7dfc1107a72eafab',
                                    'tree': 'f72599fa212e894d67ee114553e628ab05daf2da'},
                       'tile_kernels': {'head': 'edfa64d018bc64006fcb07c95c32fa196363c0ae',
                                        'tree': 'cb2fa41c8af290204c81c547ed678ebf957cca53'},
                       'tilelang': {'head': 'a0e66d71fe7617216f2de5d86c3b088595367387',
                                    'tree': '222b895d04ee6cd75179155d06e0c0d450636f51'}},
 'image_rootfs_sha256': {'r6x-steer': '00cd11979aa31b51b1f66269156f059ca7f4c3460b1a5b7a3d8d1a61c872de7a',
                         'r6x-unpatched': '690a9ee62a4c3cea729c8e4bd4d0851da0b2b236bf38c0f96226711fe5e62de0'},
 'native_parent_image': 'sha256:a63259a196d82dae51016d487ed43093f2045261adcb41045cfe9c1be29f70d2'}

REVISED_ARMS = ('r6x-unpatched', 'r6x-steer')
COMPONENTS = ('vllm', 'b12x', 'nccl', 'tilelang', 'tile_kernels', 'sparknet')
MAX_BYTES = 1024 * 1024
DOCKER = ['/usr/bin/env', '-i', 'PATH=/usr/bin:/bin', 'HOME=/nonexistent',
          'docker', '--host', 'unix:///var/run/docker.sock']


def require(condition, message):
    if not condition:
        raise ValueError(message)


def checked_identity(arm):
    require(type(arm) is str and arm in REVISED_ARMS, 'unknown evidence arm')
    return dict(arm=arm, **REVISED_IDENTITIES[arm])


def source_hashes(arm):
    checked_identity(arm)
    return copy.deepcopy(REVISED_SOURCE_SHA256[arm])


def absent_source_paths(arm):
    checked_identity(arm)
    return ['models/deepseek_v4_1/steering'] if arm == 'r6x-unpatched' else []


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def json_bytes(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'duplicate baseline JSON field')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite baseline JSON')))


def _hash(value):
    return type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _path(value):
    return (type(value) is str and Path(value).is_absolute() and '..' not in Path(value).parts
            and str(Path(value)) == value)


def validate_capture(runtime, arm):
    identity = checked_identity(arm)
    extra = {'absent_source_paths'} if arm in REVISED_ARMS else set()
    require(isinstance(runtime, dict) and set(runtime) == extra | {
        'schema_version', 'arm', 'image', 'source_head', 'source_tree', 'installation',
        'platform', 'metadata_versions', 'package_origins', 'package_sha256', 'torch_version',
        'torch_version_sha256', 'native_paths', 'native_sha256', 'source_sha256', 'pythonpath'},
        'baseline static provenance fields differ')
    require(type(runtime['schema_version']) is int and runtime['schema_version'] == 1
            and all(runtime[k] == v for k, v in identity.items()), 'baseline provenance arm/image/source differs')
    install = runtime['installation']
    require(isinstance(install, dict) and set(install) == {'mode', 'package_root'}
            and install['mode'] == 'image-source' and _path(install['package_root']),
            'baseline installed package root differs')
    platform = runtime['platform']
    require(isinstance(platform, dict) and set(platform) == {'python', 'machine', 'system'}
            and all(type(v) is str and v for v in platform.values())
            and platform['machine'] == 'aarch64' and platform['system'] == 'Linux',
            'baseline image platform differs')
    versions = runtime['metadata_versions']
    require(isinstance(versions, dict) and set(versions) == {'torch', 'tilelang', 'tile_kernels'}
            and all(type(v) is str and v for v in versions.values()), 'baseline package metadata incomplete')
    origins, package_hashes = runtime['package_origins'], runtime['package_sha256']
    require(isinstance(origins, dict) and isinstance(package_hashes, dict)
            and set(origins) == set(package_hashes) == set(versions)
            and all(_path(v) and v.endswith('/__init__.py') for v in origins.values())
            and all(_hash(v) for v in package_hashes.values()), 'baseline package origin/hash missing')
    require(type(runtime['torch_version']) is str and runtime['torch_version']
            and _hash(runtime['torch_version_sha256']), 'baseline static Torch version source missing')
    paths, hashes = runtime['native_paths'], runtime['native_sha256']
    require(isinstance(paths, dict) and isinstance(hashes, dict)
            and set(paths) == set(hashes) == set(NATIVE_NAMES), 'baseline native inventory differs')
    root = Path(install['package_root'])
    for name in NATIVE_NAMES:
        require(_path(paths[name]) and Path(paths[name]).parent == root
                and Path(paths[name]).name.startswith(name.split('.')[-1] + '.')
                and Path(paths[name]).name.endswith('.so') and _hash(hashes[name]),
                'baseline native path/hash differs')
    require(runtime['source_sha256'] == source_hashes(arm),
            'baseline actual full-module source bytes differ from exact source revision')
    require(type(runtime['pythonpath']) is str and runtime['pythonpath']
            and all(_path(p) for p in runtime['pythonpath'].split(':')),
            'baseline baked Python path is missing or relative')
    if arm in REVISED_ARMS:
        require(runtime['absent_source_paths'] == absent_source_paths(arm),
                'revised baseline absent-source proof differs')
    return copy.deepcopy(runtime)

def validate_image(arm, image, runtime):
    """Raw current image inspect, never the unavailable historical build receipt."""
    identity = checked_identity(arm)
    validate_capture(runtime, arm)
    require(isinstance(image, dict) and image.get('Id') == identity['image'], 'baseline inspected image differs')
    require(image.get('Architecture') == 'arm64' and image.get('Os') == 'linux', 'baseline image architecture differs')
    config = image.get('Config', {})
    labels = config.get('Labels', {})
    require(isinstance(labels, dict)
            and labels.get('local.spark3.vllm.patch-head') == identity['source_head']
            and labels.get('local.spark3.vllm.tree') == identity['source_tree'],
            'baseline inspected vLLM source labels differ')
    components = {}
    for name in COMPONENTS:
        prefix = 'local.spark3.' + name
        tree, head = labels.get(prefix + '.tree'), labels.get(prefix + '.patch-head')
        require(type(tree) is str and re.fullmatch(r'[0-9a-f]{40}', tree)
                and type(head) is str and re.fullmatch(r'[0-9a-f]{40}', head),
                'baseline component label missing: ' + name)
        components[name] = {'head': head, 'tree': tree}
    if arm in REVISED_ARMS:
        require({k: v for k, v in components.items() if k != 'vllm'} == IMAGE_FREEZE['component_sources'],
                'revised pair non-vLLM component sources differ')
    require([v for v in config.get('Env', []) if v.startswith('PYTHONPATH=')]
            == ['PYTHONPATH=' + runtime['pythonpath']], 'baseline image baked Python path differs')
    rootfs = image.get('RootFS', {})
    layers = rootfs.get('Layers')
    require(rootfs.get('Type') == 'layers' and isinstance(layers, list) and layers
            and all(type(v) is str and re.fullmatch(r'sha256:[0-9a-f]{64}', v) for v in layers),
            'baseline image RootFS layer identity missing')
    if arm in REVISED_ARMS:
        require(digest(canonical(rootfs)) == IMAGE_FREEZE['image_rootfs_sha256'][arm]
                and labels.get('local.site.native-parent') == IMAGE_FREEZE['native_parent_image'],
                'revised baseline historical RootFS/native parent differs')
    return dict(identity=identity, components=components, image_labels=copy.deepcopy(labels),
                rootfs_sha256=digest(canonical(rootfs)), image_config_sha256=digest(canonical(config)),
                pythonpath=runtime['pythonpath'])

def static_capture_plan(arm, capture_id):
    """Return one inert fixed CPU-only command; an authorized supervisor owns it."""
    identity = checked_identity(arm)
    require(type(capture_id) is str and re.fullmatch(r'[0-9a-f]{32}', capture_id), 'baseline capture UUID differs')
    params = dict(identity=identity, source_sha256=source_hashes(arm), native_names=NATIVE_NAMES)
    if arm in REVISED_ARMS:
        params['absent_source_paths'] = absent_source_paths(arm)
    program = 'P=' + repr(params) + '\n' + STATIC_CAPTURE
    name = 'q03-runtime-evidence-' + capture_id
    argv = DOCKER + ['run', '--pull', 'never', '--rm', '--name', name,
            '--label', 'q03.role=baseline-static', '--label', 'q03.capture=' + capture_id,
            '--runtime=runc', '--network=none', '--read-only', '--cap-drop=ALL',
            '--env', 'NVIDIA_VISIBLE_DEVICES=void', '--env', 'NVIDIA_DRIVER_CAPABILITIES=',
            '--security-opt=no-new-privileges', '--memory=1g', '--memory-swap=1g', '--cpus=1',
            '--pids-limit=64', '--entrypoint', 'python3', identity['image'], '-I', '-S', '-B', '-c', program]
    return dict(schema_version=1, arm=arm, image=identity['image'], capture_id=capture_id,
                name=name, argv=argv, timeout_seconds=120, program_sha256=digest(program.encode()),
                no_model_or_native_imports=True, no_gpu_devices=True,
                status='planned-only; evidence, not runtime qualification')

def validate_static_capture_output(raw, arm):
    require(type(raw) is str and len(raw.encode()) <= 1024 * 1024, 'baseline capture output exceeds bound')
    prefix = 'Q03_BASELINE_STATIC='
    lines = [line[len(prefix):] for line in raw.splitlines() if line.startswith(prefix)]
    require(len(lines) == 1, 'baseline capture requires exactly one marked provenance')
    return validate_capture(json_bytes(lines[0]), arm)


def validate_image_identity(image, arm):
    """Reject the wrong retained image before starting its CPU-only collector."""
    identity = checked_identity(arm)
    require(isinstance(image, dict) and image.get('Id') == identity['image']
            and image.get('Architecture') == 'arm64' and image.get('Os') == 'linux',
            'retained image identity/platform differs')
    labels = image.get('Config', {}).get('Labels', {})
    expected = dict(IMAGE_FREEZE['component_sources'],
                    vllm={'head': identity['source_head'], 'tree': identity['source_tree']})
    require(isinstance(labels, dict) and all(
        labels.get('local.spark3.' + name + '.' + suffix) == value[key]
        for name, value in expected.items() for suffix, key in (('patch-head', 'head'), ('tree', 'tree'))),
        'retained image component labels differ')
    require(digest(canonical(image.get('RootFS'))) == IMAGE_FREEZE['image_rootfs_sha256'][arm]
            and labels.get('local.site.native-parent') == IMAGE_FREEZE['native_parent_image'],
            'retained image RootFS/native parent differs')


def open_output(path):
    """Create a fresh private evidence directory in a private owned parent."""
    value = str(path)
    require(_path(value), 'output must be an absolute normalized path')
    path = Path(value)
    require(path.resolve() == path, 'output aliases are forbidden')
    parent = path.parent
    info = parent.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o700, 'output parent must be owned mode 0700')
    source = Path(__file__).resolve().parent
    require(not path.is_relative_to(source.parent), 'output must be separate from the source slice')
    parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(parent_fd)
        require(opened.st_ino == info.st_ino and opened.st_dev == info.st_dev
                and opened.st_uid == os.getuid() and stat.S_IMODE(opened.st_mode) == 0o700,
                'output parent changed')
        os.mkdir(path.name, mode=0o700, dir_fd=parent_fd)
        fd = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        current = os.fstat(fd)
        if current.st_uid != os.getuid() or stat.S_IMODE(current.st_mode) != 0o700:
            os.close(fd)
            raise ValueError('output directory is not private and owned')
        return fd
    finally:
        os.close(parent_fd)


def private_file(directory_fd, name):
    require(re.fullmatch(r'[a-z][a-z0-9.-]*', name), 'invalid evidence filename')
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o600, dir_fd=directory_fd)
    return os.fdopen(fd, 'wb')


def save(directory_fd, name, value):
    with private_file(directory_fd, name) as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    os.fsync(directory_fd)


def read_result(directory_fd, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    with os.fdopen(fd, 'rb') as stream:
        require(os.fstat(stream.fileno()).st_size <= MAX_BYTES, 'raw result exceeds size bound')
        return stream.read(MAX_BYTES + 1)


def child_limits():
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_BYTES, MAX_BYTES))


def execute(directory_fd, label, argv, timeout, runner):
    """Retain exact stdout/stderr, including failures; never use a shell."""
    execution = dict(argv_sha256=digest(canonical(argv)), timeout_seconds=timeout, returncode=None)
    with private_file(directory_fd, label + '.stdout') as stdout:
        with private_file(directory_fd, label + '.stderr') as stderr:
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
                save(directory_fd, label + '.execution.json', canonical(execution))
    require(result.returncode == 0, label + ' failed; raw evidence retained')
    return read_result(directory_fd, label + '.stdout')


def capture(arm, capture_id, output, runner=None):
    """Only this finite evidence operation executes Docker; no service authority."""
    plan = static_capture_plan(arm, capture_id)
    directory_fd = open_output(output)
    runner = subprocess.run if runner is None else runner
    receipt = dict(schema_version=1, arm=arm, image=plan['image'], capture_id=capture_id,
                   status='failed-unreviewed', requires_independent_review=True,
                   runtime_qualification=False, container_name=plan['name'])
    try:
        save(directory_fd, 'plan.json', canonical(plan))
        save(directory_fd, 'source.json', canonical({
            'script_sha256': digest(Path(__file__).read_bytes()),
            'collector_sha256': digest(STATIC_CAPTURE.encode()),
            'admission_base': 'eb775f13d4a61ece739003c5fca860864d91ea63'}))
        images = json_bytes(execute(directory_fd, 'image-inspect',
                            DOCKER + ['image', 'inspect', plan['image']], 30, runner))
        require(isinstance(images, list) and len(images) == 1, 'ambiguous retained image inspect')
        validate_image_identity(images[0], arm)
        absence = DOCKER + ['container', 'ls', '--all', '--filter',
                           'name=^/' + plan['name'] + '$', '--format', '{{.Names}}']
        require(not execute(directory_fd, 'preabsence', absence, 30, runner).strip(),
                'capture container already exists; nothing started')
        raw = execute(directory_fd, 'capture', plan['argv'], plan['timeout_seconds'], runner)
        require(not execute(directory_fd, 'postabsence', absence, 30, runner).strip(),
                'capture container remains; manual reconciliation required')
        runtime = validate_static_capture_output(raw.decode('utf-8'), arm)
        observation = validate_image(arm, images[0], runtime)
        manifest = canonical(runtime)
        save(directory_fd, 'runtime-manifest.json', manifest)
        save(directory_fd, 'image-observation.json', canonical(observation))
        receipt.update(status='captured-unreviewed', runtime_manifest_sha256=digest(manifest),
                       runtime_manifest_path=str(Path(output) / 'runtime-manifest.json'),
                       raw_stdout_sha256=digest(raw))
    except BaseException as error:
        receipt.update(error_type=type(error).__name__, error=str(error),
                       container_state='not proven absent; inspect this exact capture name before retry')
        save(directory_fd, 'receipt.json', canonical(receipt))
        raise
    else:
        save(directory_fd, 'receipt.json', canonical(receipt))
        return receipt
    finally:
        os.close(directory_fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    for operation in ('plan', 'capture'):
        command = sub.add_parser(operation)
        command.add_argument('arm', choices=REVISED_ARMS)
        command.add_argument('--capture-id', required=True)
        if operation == 'capture':
            command.add_argument('--output', required=True)
    args = parser.parse_args()
    result = (static_capture_plan(args.arm, args.capture_id) if args.operation == 'plan'
              else capture(args.arm, args.capture_id, args.output))
    print(canonical(result).decode())


if __name__ == '__main__':
    main()
