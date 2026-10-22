#!/usr/bin/env python3
"""Closed identities for a new r6x188 versus native197-OFF baseline capsule.

Pure validation/planning by default. No model/native imports, Docker, SSH, or
service actions occur on import. Existing Q03 schemas and freezes are untouched.
A new static capture describes the actual immutable image, not an old build
reproduction and not a borrowed native197 receipt.
"""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import re

def read_owned_bytes(path):
    from q03_held_window import read_owned_bytes as read
    return read(path)

IDENTITIES = {
    'r6x188': {'image': 'sha256:8ad0da0366af0560678e09dbccefedc6688a58d79758b4f11fc63ef69ede4a0a',
               'source_head': 'ebb44766d6f04d3a9272b712778d7f2fa2cec1fd',
               'source_tree': 'ac79557915e3ee2778f8f6353a3d35f848f564e4'},
    'native197-off': {'image': 'sha256:90bd2bbc4709b14a2210a6752c707d9d16661180248949e183fa0c25b6691ff6',
                      'source_head': '154159dc3e38ca8b2321c01a153a1b2ccaf8663b',
                      'source_tree': 'ae15047b376c7792062a2607c4ef1dfbc78df18a'},
}
STEERING = ('unloaded', 'loaded0', 'loaded1', 'loaded2', 'loaded3', 'loaded4')
NATIVE_NAMES = ('vllm._C_stable_libtorch', 'vllm._moe_C_stable_libtorch')
COMPONENTS = ('vllm', 'b12x', 'nccl', 'tilelang', 'tile_kernels', 'sparknet')
PROFILE_NAME = 'heretic-1017-attn-v1'
PROFILE_SHA256 = 'd7f0b997ea2748b03a353188583d494b2bfe7c40838aff98945941032da6a093'
# Full bytes of each selected module, independently extracted from the exact
# Git revisions above. This is not the old selected-function G1 fixture.
SOURCE_SHA256 = {
  "native197-off": {
    "__init__.py": "fd3708e5a13abe98c566afd79a6b1987cef70c0548bb19c1218ff6a7f9d43346",
    "model_executor/layers/mhc.py": "923828e71cc695c9ce393c77bbc3bc3d69370e12ee37678a11078618e1cd36ee",
    "models/deepseek_v4_1/nvidia/dspark.py": "52bce488dcb7aa6ca66581f219364207cc5308da00c1e8548ac98f8380a94e1c",
    "models/deepseek_v4_1/nvidia/model.py": "a05a7d48cd2283478b751f473f848374fa8e2a688d9f4b11276454b7881e597b",
    "models/deepseek_v4_1/steering/__init__.py": "8e7e10a737b2033b7ac68a049320113a0d640b0898718a833fcbbd165dec88af",
    "models/deepseek_v4_1/steering/artifact.py": "3afe34c5ae1f2ecef8ea5a5b8de58e00bdec0964de2f337f10d7c15c36837389",
    "models/deepseek_v4_1/steering/cache.py": "a3b9a84349a13f7e174a0f8d4edd106ec9144a423bca7f7e6f2aa9d839b51040",
    "models/deepseek_v4_1/steering/config.py": "5305406bbef89a63700daf3e50942abd9b96481e5204847db0c248ccbb020f62",
    "models/deepseek_v4_1/steering/consume.py": "917dc0646dd5573c6d5d27314f7e08c58a2041f588bd0a8a1d9e2618096d17db",
    "models/deepseek_v4_1/steering/ops.py": "d539205a087213fea9faf1c4ceff7db520db458eea91b1075e66d80fbb72cb27",
    "models/deepseek_v4_1/steering/profile.py": "761b7be767a8cea168ace7d8a1d7e9f63991372751d561f2073e53f5d93912c7",
    "models/deepseek_v4_1/steering/reference.py": "0030a4e686752db4ad382e6a2ee39c93943827df6ee40eed7f8c39881267af65",
    "models/deepseek_v4_1/steering/request.py": "c57520b61716be75cb76bbf9c83ade3e6c355421e37cd792438de062bfcface3",
    "models/deepseek_v4_1/steering/row_kernel.py": "6849de8fa57d12be3a8c386922da58222c4340a825ffd4d5956f6f574c7bef13",
    "models/deepseek_v4_1/steering/rows.py": "3736525745fb324fe285f4eb091a6b72d407a73db64a46db6f9c72eaa7d288f3",
    "models/deepseek_v4_1/steering/runtime.py": "aa17173fdb25772273a2c5a2d0053e580b56c185b2091f19e41a56ee8c3ea346",
    "v1/worker/gpu/model_runner.py": "f8ea26dce6ac8ba54eabe31621055516c7764b721ef1849cb48df0ab01ed1d10",
    "v1/worker/gpu_model_runner.py": "74842cd4f20704768e2ac8b0ec3ccd3dcb80af6115675bf4e3fc703cec146340",
    "v1/worker/gpu_worker.py": "1dfdf4fd410947bfd32d69b53224a4aea26d2767693596481aea4a14339e4bb3",
    "v1/worker/worker_base.py": "3a0ca44daf0b00fe626ca21a5b71d1a0e0e506759449a7fb8967292edccbcf35"
  },
  "r6x188": {
    "__init__.py": "fd3708e5a13abe98c566afd79a6b1987cef70c0548bb19c1218ff6a7f9d43346",
    "model_executor/layers/mhc.py": "923828e71cc695c9ce393c77bbc3bc3d69370e12ee37678a11078618e1cd36ee",
    "models/deepseek_v4_1/nvidia/dspark.py": "52bce488dcb7aa6ca66581f219364207cc5308da00c1e8548ac98f8380a94e1c",
    "models/deepseek_v4_1/nvidia/model.py": "cc90dad8970cf7bc9cd46271ff9df95b38b0eaa6b5bb11e8134cbf253469955b",
    "models/deepseek_v4_1/steering/__init__.py": "8e7e10a737b2033b7ac68a049320113a0d640b0898718a833fcbbd165dec88af",
    "models/deepseek_v4_1/steering/artifact.py": "3afe34c5ae1f2ecef8ea5a5b8de58e00bdec0964de2f337f10d7c15c36837389",
    "models/deepseek_v4_1/steering/cache.py": "a3b9a84349a13f7e174a0f8d4edd106ec9144a423bca7f7e6f2aa9d839b51040",
    "models/deepseek_v4_1/steering/config.py": "5305406bbef89a63700daf3e50942abd9b96481e5204847db0c248ccbb020f62",
    "models/deepseek_v4_1/steering/consume.py": "917dc0646dd5573c6d5d27314f7e08c58a2041f588bd0a8a1d9e2618096d17db",
    "models/deepseek_v4_1/steering/ops.py": "d539205a087213fea9faf1c4ceff7db520db458eea91b1075e66d80fbb72cb27",
    "models/deepseek_v4_1/steering/profile.py": "761b7be767a8cea168ace7d8a1d7e9f63991372751d561f2073e53f5d93912c7",
    "models/deepseek_v4_1/steering/reference.py": "0030a4e686752db4ad382e6a2ee39c93943827df6ee40eed7f8c39881267af65",
    "models/deepseek_v4_1/steering/request.py": "c57520b61716be75cb76bbf9c83ade3e6c355421e37cd792438de062bfcface3",
    "models/deepseek_v4_1/steering/row_kernel.py": "6849de8fa57d12be3a8c386922da58222c4340a825ffd4d5956f6f574c7bef13",
    "models/deepseek_v4_1/steering/rows.py": "3736525745fb324fe285f4eb091a6b72d407a73db64a46db6f9c72eaa7d288f3",
    "models/deepseek_v4_1/steering/runtime.py": "aa17173fdb25772273a2c5a2d0053e580b56c185b2091f19e41a56ee8c3ea346",
    "v1/worker/gpu/model_runner.py": "f8ea26dce6ac8ba54eabe31621055516c7764b721ef1849cb48df0ab01ed1d10",
    "v1/worker/gpu_model_runner.py": "74842cd4f20704768e2ac8b0ec3ccd3dcb80af6115675bf4e3fc703cec146340",
    "v1/worker/gpu_worker.py": "1dfdf4fd410947bfd32d69b53224a4aea26d2767693596481aea4a14339e4bb3",
    "v1/worker/worker_base.py": "3a0ca44daf0b00fe626ca21a5b71d1a0e0e506759449a7fb8967292edccbcf35"
  }
}

# New comparisons do not rename or reuse either historical steer-bearing image.
# These are source-frozen slots, never manifest/CLI-supplied image or build pins.
# A separately reviewed composition must fill every UNBOUND value before even
# static-image inspection or a service plan is admitted.
REVISED_ARMS = ('r6x-unpatched', 'r6x-steer')
REVISED_IDENTITIES = {
    'r6x-unpatched': {
        'image': 'sha256:fc6a9ef83dd1a741a32c2724263e9c05a2635dc8154784ffe0f9c36709018082',
        'source_head': '7316c7f9fae1bc4a0b7ab1848de0db4143eec5d2',
        'source_tree': '4d6851c8bb1397a55254ba88905a87d9785b98dc'},
    'r6x-steer': {
        'image': 'sha256:8ad0da0366af0560678e09dbccefedc6688a58d79758b4f11fc63ef69ede4a0a',
        'source_head': 'ebb44766d6f04d3a9272b712778d7f2fa2cec1fd',
        'source_tree': 'ac79557915e3ee2778f8f6353a3d35f848f564e4'},
}
REVISED_SOURCE_SHA256 = {   'r6x-steer': {   '__init__.py': 'fd3708e5a13abe98c566afd79a6b1987cef70c0548bb19c1218ff6a7f9d43346',
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
    'r6x-unpatched': {   '__init__.py': 'fd3708e5a13abe98c566afd79a6b1987cef70c0548bb19c1218ff6a7f9d43346',
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
REVISED_FREEZE = {
    'upstream_deployment_head': 'a125256eb47f2d76f077117cf6e437eb42bfaa1d',
    'upstream_deployment_tree': '9834bc0d20f29bce89e8dec8c01162923110411f',
    'upstream_patchset_sha256': '6519df98b2039f508a0b38677ecef889201c8117325cf944a8b5a787844a5f7c',
    'steering_patchset_sha256': 'e6bdcb3147290a6cb7df6471bcb2c3e658a8780b1e594a561e59875bcf0cc1e0',
    'common_recipe_sha256': 'cb76902efccbacd3b62fdedbcc4ebf7f49c6d273b25a406a2f8d1c5aff5600d7',
    'profile_sha256': {'r6x-unpatched': 'UNBOUND',
                       'r6x-steer': 'e935b492d1f76eeae09fe5a57b094c3bfbf8e63e83b37fe1be0e42fa46170a36'},
    'effective_sha256': 'a144ea7b1dbb12c50ca4cf0862d1ec9e83f34d87a2938ea441f8c57f181914c8', 'common_runtime_sha256': 'UNBOUND',
    'model_tokenizer_sha256': 'UNBOUND', 'component_sources': {'b12x': {'head': '69e5795e2141fbb5fa2b3af096d51e754939bb41',
          'tree': '1a647a311ca55d45070ab83d60668d9c8fb8886c'},
 'nccl': {'head': 'eeacf1c6343f0499e4e7ab20ec7eecfb6f62eda6',
          'tree': '6af10aa730a64fcee8781d62340a9c9184e79469'},
 'sparknet': {'head': '3bc8d10104a5012244a0424d7dfc1107a72eafab',
              'tree': 'f72599fa212e894d67ee114553e628ab05daf2da'},
 'tile_kernels': {'head': 'edfa64d018bc64006fcb07c95c32fa196363c0ae',
                  'tree': 'cb2fa41c8af290204c81c547ed678ebf957cca53'},
 'tilelang': {'head': 'a0e66d71fe7617216f2de5d86c3b088595367387',
              'tree': '222b895d04ee6cd75179155d06e0c0d450636f51'}},
    'build_inputs_sha256': {'r6x-unpatched': 'b0f7abaf3c42b4904fde3d440c9cae2e8dcbf05d7a46123c8eb0cd4b07bfc014', 'r6x-steer': '0002002f9d67d697c1b829d6a57d83a84d3a1371d9adce7d4ab6e3514aed227f'},
    'build_receipt_sha256': {arm: '80d2f820df658504b4a01959773fc94d5e9997a223278585966e8ececebed5e4' for arm in REVISED_ARMS},
    'runtime_manifest_sha256': {arm: 'UNBOUND' for arm in REVISED_ARMS},
    'image_rootfs_sha256': {'r6x-unpatched': '690a9ee62a4c3cea729c8e4bd4d0851da0b2b236bf38c0f96226711fe5e62de0', 'r6x-steer': '00cd11979aa31b51b1f66269156f059ca7f4c3460b1a5b7a3d8d1a61c872de7a'},
    'native_parent_image': 'sha256:a63259a196d82dae51016d487ed43093f2045261adcb41045cfe9c1be29f70d2',
}
STEERING_DIRECTORY = 'models/deepseek_v4_1/steering'
# Safe string RPC and CLI/config compatibility must be bound in both trees,
# rather than assumed to exist because a patched image had them.
REVISED_COMMON_SOURCES = frozenset(k for k in SOURCE_SHA256['r6x188']
    if not k.startswith(STEERING_DIRECTORY + '/')) | frozenset({
        'engine/arg_utils.py', 'entrypoints/cli/serve.py',
        'entrypoints/openai/api_server.py', 'config/parallel.py',
        'entrypoints/launchers/api_server/routers.py', 'entrypoints/serve/__init__.py', 'entrypoints/serve/dev/rpc/api_router.py', 'v1/serial_utils.py', 'envs.py',
        'tokenizers/deepseek_v41.py', 'tokenizers/deepseek_v41_encoding.py'})
REVISED_STEERING_SOURCES = frozenset(k for k in SOURCE_SHA256['r6x188']
    if k.startswith(STEERING_DIRECTORY + '/'))


def validate_revised_freeze():
    """No fallback to A188/B197 or trust in first-observed runtime values."""
    freeze = REVISED_FREEZE
    for key in ('upstream_deployment_head', 'upstream_deployment_tree'):
        require(type(freeze[key]) is str and re.fullmatch('[0-9a-f]{40}', freeze[key]),
                'revised baseline remains UNBOUND: ' + key)
    for key in ('upstream_patchset_sha256', 'steering_patchset_sha256', 'common_recipe_sha256', 'effective_sha256',
                'common_runtime_sha256', 'model_tokenizer_sha256'):
        require(_hash(freeze[key]), 'revised baseline remains UNBOUND: ' + key)
    for key in ('profile_sha256', 'build_inputs_sha256', 'build_receipt_sha256', 'runtime_manifest_sha256', 'image_rootfs_sha256'):
        require(isinstance(freeze[key], dict) and set(freeze[key]) == set(REVISED_ARMS)
                and all(_hash(v) for v in freeze[key].values()),
                'revised baseline remains UNBOUND: ' + key)
    require(type(freeze['native_parent_image']) is str
            and re.fullmatch('sha256:[0-9a-f]{64}', freeze['native_parent_image']),
            'revised baseline remains UNBOUND: native_parent_image')
    components = freeze['component_sources']
    require(isinstance(components, dict) and set(components) == set(COMPONENTS) - {'vllm'}
            and all(isinstance(v, dict) and set(v) == {'head', 'tree'}
                    and all(type(x) is str and re.fullmatch('[0-9a-f]{40}', x) for x in v.values())
                    for v in components.values()), 'revised baseline remains UNBOUND: component_sources')
    for arm in REVISED_ARMS:
        value = REVISED_IDENTITIES[arm]
        require(set(value) == {'image', 'source_head', 'source_tree'}
                and type(value['image']) is str and re.fullmatch('sha256:[0-9a-f]{64}', value['image'])
                and all(type(value[k]) is str and re.fullmatch('[0-9a-f]{40}', value[k])
                        for k in ('source_head', 'source_tree')),
                'revised baseline remains UNBOUND: ' + arm + ' identity')
        if arm == 'r6x-unpatched':
            require(value['image'] not in {v['image'] for v in IDENTITIES.values()},
                    'steering-bearing historical image cannot be relabelled as unpatched')
        sources = REVISED_SOURCE_SHA256[arm]
        expected = REVISED_COMMON_SOURCES | (REVISED_STEERING_SOURCES if arm == 'r6x-steer' else set())
        require(isinstance(sources, dict) and set(sources) == expected
                and all(_hash(v) for v in sources.values()),
                'revised baseline remains UNBOUND: ' + arm + ' source closure')
    for name in ('tokenizers/deepseek_v41.py', 'tokenizers/deepseek_v41_encoding.py'):
        require(REVISED_SOURCE_SHA256[REVISED_ARMS[0]][name] == REVISED_SOURCE_SHA256[REVISED_ARMS[1]][name],
                'revised pair native tokenizer source differs')
    for key in ('image', 'source_head', 'source_tree'):
        require(REVISED_IDENTITIES[REVISED_ARMS[0]][key] != REVISED_IDENTITIES[REVISED_ARMS[1]][key],
                'unpatched and steering identities must be distinct: ' + key)
    return copy.deepcopy(freeze)


def source_hashes(arm):
    checked_identity(arm)
    return copy.deepcopy(REVISED_SOURCE_SHA256[arm] if arm in REVISED_ARMS else SOURCE_SHA256[arm])


def absent_source_paths(arm):
    return [STEERING_DIRECTORY] if arm == 'r6x-unpatched' else []


def common_runtime_sha256(runtime):
    keys = ('installation', 'platform', 'metadata_versions', 'package_origins', 'package_sha256',
            'torch_version', 'torch_version_sha256', 'native_paths', 'native_sha256', 'pythonpath')
    return digest(canonical({k: runtime[k] for k in keys}))


def require(ok, message):
    if not ok:
        raise ValueError(message)


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


def checked_identity(arm):
    require(type(arm) is str and arm in (*IDENTITIES, *REVISED_ARMS), 'unknown baseline arm')
    if arm in REVISED_ARMS:
        validate_revised_freeze()
        return dict(arm=arm, **REVISED_IDENTITIES[arm])
    return dict(arm=arm, **IDENTITIES[arm])


def validate_selection(selection):
    require(isinstance(selection, dict) and set(selection) == {'arm', 'steering'},
            'baseline selection fields differ; no free image, mode, dose or argv')
    checked_identity(selection['arm'])
    states = ('absent',) if selection['arm'] == 'r6x-unpatched' else STEERING
    require(type(selection['steering']) is str and selection['steering'] in states,
            'baseline steering state differs')
    return copy.deepcopy(selection)


def steering_request(selection):
    validate_selection(selection)
    if selection['steering'] in ('absent', 'unloaded'):
        return {}
    return {'steering_profile': PROFILE_NAME, 'steering_scale': int(selection['steering'][-1])}


def _hash(value):
    return type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _path(value):
    return (type(value) is str and Path(value).is_absolute() and '..' not in Path(value).parts
            and str(Path(value)) == value)


def validate_runtime(runtime, arm):
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
        require(common_runtime_sha256(runtime) == REVISED_FREEZE['common_runtime_sha256'],
                'revised pair common runtime/native package identity differs')
    return copy.deepcopy(runtime)


def validate_image(arm, image, runtime):
    """Raw current image inspect, never the unavailable historical build receipt."""
    identity = checked_identity(arm)
    validate_runtime(runtime, arm)
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
        require({k: v for k, v in components.items() if k != 'vllm'} == REVISED_FREEZE['component_sources'],
                'revised pair non-vLLM component sources differ')
    require([v for v in config.get('Env', []) if v.startswith('PYTHONPATH=')]
            == ['PYTHONPATH=' + runtime['pythonpath']], 'baseline image baked Python path differs')
    rootfs = image.get('RootFS', {})
    layers = rootfs.get('Layers')
    require(rootfs.get('Type') == 'layers' and isinstance(layers, list) and layers
            and all(type(v) is str and re.fullmatch(r'sha256:[0-9a-f]{64}', v) for v in layers),
            'baseline image RootFS layer identity missing')
    if arm in REVISED_ARMS:
        require(digest(canonical(rootfs)) == REVISED_FREEZE['image_rootfs_sha256'][arm]
                and labels.get('local.site.native-parent') == REVISED_FREEZE['native_parent_image'],
                'revised baseline historical RootFS/native parent differs')
    return dict(identity=identity, components=components, image_labels=copy.deepcopy(labels),
                rootfs_sha256=digest(canonical(rootfs)), image_config_sha256=digest(canonical(config)),
                pythonpath=runtime['pythonpath'])


def validate_binding(binding, candidate=None):
    require(isinstance(binding, dict) and set(binding) == {'schema_version', 'arm', 'provenance'}
            and type(binding['schema_version']) is int and binding['schema_version'] == 1,
            'baseline identity binding fields differ')
    identity = checked_identity(binding['arm'])
    pin = binding['provenance']
    require(isinstance(pin, dict) and set(pin) == {'path', 'sha256'} and _hash(pin['sha256']),
            'baseline provenance needs exact immutable path/hash')
    if binding['arm'] in REVISED_ARMS:
        require(pin['sha256'] == REVISED_FREEZE['runtime_manifest_sha256'][binding['arm']],
                'revised baseline runtime provenance is not independently frozen')
    raw = read_owned_bytes(pin['path'])
    require(digest(raw) == pin['sha256'], 'baseline provenance bytes changed')
    runtime = validate_runtime(json_bytes(raw), binding['arm'])
    if candidate is not None:
        require(candidate.get('image') == identity['image']
                and candidate.get('component_head') == identity['source_head']
                and candidate.get('runtime_manifest_path') == pin['path']
                and candidate.get('runtime_manifest_sha256') == pin['sha256']
                and candidate.get('pytest_base_pythonpath') == runtime['pythonpath'],
                'baseline candidate and explicit provenance differ')
    return dict(identity=identity, runtime=runtime, provenance=copy.deepcopy(pin))


def validate_host(binding, candidate, ops):
    """Only fixed-image inspect, no imports, image creation or service actions."""
    checked = validate_binding(binding, candidate)
    argv = ['/usr/bin/env', '-u', 'DOCKER_CONTEXT', '-u', 'DOCKER_HOST', 'docker',
            '--host', 'unix:///var/run/docker.sock', 'image', 'inspect', checked['identity']['image']]
    result = ops.run(argv, timeout=90)
    require(result.returncode == 0, 'baseline image inspection failed')
    images = json_bytes(result.stdout)
    require(isinstance(images, list) and len(images) == 1, 'ambiguous baseline image inspect')
    checked['image_observation'] = validate_image(binding['arm'], images[0], checked['runtime'])
    return checked


def input_contract(binding):
    """The caller adds its profile, assets, normal renderer and source closure."""
    checked = validate_binding(binding)
    return dict(files=[checked['provenance']], identity=checked['identity'], runtime=checked['runtime'])


# This body is intentionally stdlib-only, even in the target image. Python -S
# skips site/.pth execution; PathFinder inspects a top-level package without
# importing it. No Torch, vLLM, CUDA or ELF import occurs during preparation.
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


def static_capture_plan(arm, capture_id):
    """Return one inert fixed CPU-only command; an authorized supervisor owns it."""
    identity = checked_identity(arm)
    require(type(capture_id) is str and re.fullmatch(r'[0-9a-f]{32}', capture_id), 'baseline capture UUID differs')
    params = dict(identity=identity, source_sha256=source_hashes(arm), native_names=NATIVE_NAMES)
    if arm in REVISED_ARMS:
        params['absent_source_paths'] = absent_source_paths(arm)
    program = 'P=' + repr(params) + '\n' + STATIC_CAPTURE
    name = 'q03-baseline-static-' + capture_id
    argv = ['/usr/bin/env', '-u', 'DOCKER_CONTEXT', '-u', 'DOCKER_HOST', 'docker',
            '--host', 'unix:///var/run/docker.sock', 'run', '--pull', 'never', '--rm', '--name', name,
            '--label', 'q03.role=baseline-static', '--label', 'q03.capture=' + capture_id,
            '--runtime=runc', '--network=none', '--read-only', '--cap-drop=ALL',
            '--env', 'NVIDIA_VISIBLE_DEVICES=void', '--env', 'NVIDIA_DRIVER_CAPABILITIES=',
            '--security-opt=no-new-privileges', '--memory=1g', '--memory-swap=1g', '--cpus=1',
            '--pids-limit=64', '--entrypoint', 'python3', identity['image'], '-I', '-S', '-B', '-c', program]
    return dict(schema_version=1, arm=arm, image=identity['image'], capture_id=capture_id,
                name=name, argv=argv, timeout_seconds=120, program_sha256=digest(program.encode()),
                no_model_or_native_imports=True, no_gpu_devices=True,
                status='planned-only; requires an owned authorized finite supervisor and absence check')


def validate_static_capture_output(raw, arm):
    require(type(raw) is str and len(raw.encode()) <= 1024 * 1024, 'baseline capture output exceeds bound')
    prefix = 'Q03_BASELINE_STATIC='
    lines = [line[len(prefix):] for line in raw.splitlines() if line.startswith(prefix)]
    require(len(lines) == 1, 'baseline capture requires exactly one marked provenance')
    return validate_runtime(json_bytes(lines[0]), arm)


def validate_ready(raw, plan):
    require(type(raw) is bytes and len(raw) <= 4 * 1024 * 1024, 'baseline ready receipt size/type differs')
    value = json_bytes(raw)
    require(isinstance(value, dict) and set(value) == {'schema_version', 'window_id', 'attempt_id',
        'spec_sha256', 'reports', 'reports_sha256'} and type(value['schema_version']) is int
        and value['schema_version'] == 1, 'baseline ready envelope differs')
    require(all(value[k] == plan[k] for k in ('window_id', 'attempt_id', 'spec_sha256')),
            'baseline readiness belongs to another attempt')
    selection = validate_selection(plan['selection'])
    checked = plan['spec']['baseline_identity']
    identity, runtime = checked['identity'], checked['runtime']
    require(identity == checked_identity(selection['arm']), 'baseline ready plan arm differs')
    validate_runtime(runtime, selection['arm'])
    reports = value['reports']
    require(isinstance(reports, list) and len(reports) == 4
            and digest(canonical(reports)) == value['reports_sha256'], 'baseline four-rank report hash differs')
    expected_config = plan['spec']['expected_effective_config']
    seen, versions, profile = set(), None, None
    for report in reports:
        rank = report.get('worker_rank')
        require(type(rank) is int and rank in range(4) and rank not in seen, 'baseline duplicate/foreign worker rank')
        seen.add(rank)
        for key, expected in (('tp_rank', rank), ('config_rank', rank), ('node_rank', rank),
                              ('node_rank_within_dp', rank), ('local_rank', 0)):
            require(type(report.get(key)) is int and report[key] == expected, 'baseline worker topology differs')
        require(report.get('identity') == identity and report.get('runtime_sha256') == checked['provenance']['sha256']
                and all(report.get(k) == plan[k] for k in ('window_id', 'attempt_id', 'spec_sha256')),
                'baseline worker identity differs')
        require(report.get('source_sha256') == source_hashes(selection['arm'])
                and report.get('native_sha256') == runtime['native_sha256']
                and report.get('native_paths') == runtime['native_paths']
                and report.get('package_root') == runtime['installation']['package_root'],
                'baseline observed source/native files differ')
        require(canonical(report.get('effective_config')) == canonical(expected_config)
                and canonical(report.get('runner_config')) == canonical(expected_config),
                'baseline actual worker/runner config differs')
        revised = selection['arm'] in REVISED_ARMS
        if revised:
            require(report.get('absent_source_paths') == absent_source_paths(selection['arm'])
                    and report.get('steering_capability') == (
                        'absent' if selection['arm'] == 'r6x-unpatched' else 'present'),
                    'revised baseline steering capability/absence proof differs')
            require(expected_config.get('model_config.max_model_len') == 1048576,
                    'revised baseline service context must remain 1048576')
        require(report.get('sm') == [12, 1] and report.get('q03_switch') == (None if revised else '0')
                and report.get('q03_owner_present') is False and report.get('sequence_parallel') is False,
                'baseline actual OFF/SM121/SP state differs')
        actual_versions = report.get('actual_versions')
        require(isinstance(actual_versions, dict) and set(actual_versions) == {'python', 'torch'}
                and all(type(v) is str and v for v in actual_versions.values())
                and actual_versions['python'] == runtime['platform']['python']
                and actual_versions['torch'] == runtime['torch_version'], 'baseline imported runtime versions missing or changed')
        require(versions is None or actual_versions == versions, 'baseline rank imported runtime versions differ')
        versions = actual_versions
        require(report.get('metadata_versions') == runtime['metadata_versions'],
                'baseline package metadata differs from static provenance')
        require(report.get('package_origins') == runtime['package_origins']
                and report.get('package_sha256') == runtime['package_sha256']
                and report.get('torch_version_sha256') == runtime['torch_version_sha256'],
                'baseline imported package/version sources differ')
        observed = report.get('steering_profile')
        if selection['steering'] in ('absent', 'unloaded'):
            require(observed is None, 'baseline unloaded steering unexpectedly loaded')
        else:
            require(isinstance(observed, dict) and observed.get('name') == PROFILE_NAME
                    and observed.get('artifact_sha256') == PROFILE_SHA256,
                    'baseline loaded steering identity differs')
            require(profile is None or observed == profile, 'baseline rank steering profiles differ')
            profile = observed
    return value


def validate_candidate(manifest, ops):
    return validate_host(manifest['http_service']['baseline_identity'], manifest['candidate'], ops)


INPUT_PROBE = r'''
import hashlib,json,pathlib,subprocess,sys,types
v=json.loads(sys.argv[1])
def need(ok,message):
 if not ok: raise RuntimeError(message)
for item in v['files']:
 p=pathlib.Path(item['path']); need(p.resolve()==p and p.is_file(),'baseline input alias/missing')
 need(hashlib.sha256(p.read_bytes()).hexdigest()==item['sha256'],'baseline input hash differs')
for p in v['directories']: need(pathlib.Path(p).is_dir(),'baseline input directory missing')
pin=v['identity_source']; raw=pathlib.Path(pin['path']).read_bytes()
need(hashlib.sha256(raw).hexdigest()==pin['sha256'],'baseline identity source differs')
m=types.ModuleType('_baseline_identity');m.__file__=pin['path'];exec(compile(raw,pin['path'],'exec'),m.__dict__)
image=json.loads(subprocess.check_output(['/usr/bin/env','-u','DOCKER_CONTEXT','-u','DOCKER_HOST','docker','--host','unix:///var/run/docker.sock','image','inspect',v['identity']['image']],text=True,timeout=30))
need(len(image)==1,'ambiguous baseline image')
observed=m.validate_image(v['identity']['arm'],image[0],v['runtime'])
need(observed==v['image_observation'],'baseline all-four image configuration/RootFS differs')
deployment=v['deployment']
g=['git','--no-replace-objects','-c','core.fsmonitor=false','-c','core.hooksPath=/dev/null','-C',deployment['path']]
def git(*a): return subprocess.check_output(g+list(a),text=True,timeout=30).strip()
need(pathlib.Path(deployment['path'],'.git').is_dir(),'baseline deployment is not a standalone checkout')
for relative in ('commondir','config.worktree','objects/info/alternates','objects/info/http-alternates'):
 p=pathlib.Path(deployment['path'],'.git',relative);need(not p.exists() and not p.is_symlink(),'baseline Git metadata aliases another checkout')
need(git('rev-parse','HEAD')==deployment['head'] and not git('status','--porcelain'),'baseline deployment changed')
need(git('config','--get','remote.origin.url').rstrip('/').removesuffix('.git')==deployment['repository'].rstrip('/').removesuffix('.git'),'baseline deployment origin differs')
print(json.dumps({'identity':v['identity'],'image_observation':observed,'deployment_head':deployment['head'],'verified_files':len(v['files'])}))
'''


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Inert finite baseline static-capture plan or receipt validation')
    sub = parser.add_subparsers(dest='operation', required=True)
    plan = sub.add_parser('plan-static')
    plan.add_argument('arm', choices=(*IDENTITIES, *REVISED_ARMS))
    plan.add_argument('--capture-id', required=True)
    validate = sub.add_parser('validate-static')
    validate.add_argument('arm', choices=(*IDENTITIES, *REVISED_ARMS))
    validate.add_argument('--log', required=True)
    args = parser.parse_args()
    if args.operation == 'plan-static':
        result = static_capture_plan(args.arm, args.capture_id)
    else:
        # No claim that a plan or JSON receipt alone proves image ownership,
        # all-four agreement, lifecycle safety or a completed benchmark.
        result = validate_static_capture_output(Path(args.log).read_text(), args.arm)
    print(canonical(result).decode())


if __name__ == '__main__':
    main()
