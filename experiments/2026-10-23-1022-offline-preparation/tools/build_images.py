"""Build CPU-audited ARM64 comparison images on Mac Docker Desktop only.

Requires the source archives and native-input-proof.json prepared and checked
with bin/spark. Never contacts a Spark or runs an image with GPU devices.
No existing tag is overwritten. Image IDs are receipts, not GPU acceptance.
"""
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys

EXP = pathlib.Path(__file__).resolve().parents[1]
REPO = EXP.parents[1]
WORK = REPO / '.work/1022-offline-preparation'
DOCKER = ['docker', '--context', 'desktop-linux']
PARENT = 'sha256:8ad0da0366af0560678e09dbccefedc6688a58d79758b4f11fc63ef69ede4a0a'
PARENT_TAG = 'ds41f-1022-native-parent:8ad0da0366af'
COMMON_TAG = 'ds41f-1022-screen:native-ed6af327-offline1'


def run(*args, **kwargs):
    return subprocess.check_output([*DOCKER, *args], text=True, **kwargs).strip()


def absent(tag):
    if subprocess.run([*DOCKER, 'image', 'inspect', tag], stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL).returncode == 0:
        raise RuntimeError('Refuse to overwrite existing tag: ' + tag)


def context_digest(context):
    digest = hashlib.sha256()
    for path in sorted(context.rglob('*')):
        if path.is_file():
            digest.update(str(path.relative_to(context)).encode() + b'\0')
            with path.open('rb') as stream:
                digest.update(hashlib.file_digest(stream, 'sha256').digest())
    return digest.hexdigest()


def identify(image):
    image['portable_config_id'] = image['Descriptor']['annotations']['config.digest']
    if image['Architecture'] != 'arm64':
        raise RuntimeError('Image architecture mismatch')
    return image


def build(tag, context):
    log = WORK / 'logs' / (tag.replace(':', '-') + '.txt')
    metadata = WORK / 'images' / (tag.replace(':', '-') + '.metadata.json')
    inputs = metadata.with_suffix('.inputs.json')
    digest = context_digest(context)
    existing = subprocess.run([*DOCKER, 'image', 'inspect', tag], text=True,
                              capture_output=True)
    if existing.returncode == 0:
        image = json.loads(existing.stdout)[0]
        if (not inputs.exists() or json.loads(inputs.read_text())['context_sha256'] != digest
                or not metadata.exists()
                or json.loads(metadata.read_text())['containerimage.digest'] != image['Id']):
            raise RuntimeError('Existing tag lacks matching build receipts: ' + tag)
        return identify(image)
    inputs.write_text(json.dumps({'context_sha256': digest}))
    with log.open('w') as output:
        subprocess.run([*DOCKER, 'build', '--platform', 'linux/arm64', '--network=none',
                        '--provenance=false', '--metadata-file', str(metadata),
                        '--progress=plain', '-t', tag, str(context)], check=True,
                       stdout=output, stderr=subprocess.STDOUT)
    image = json.loads(run('image', 'inspect', tag))[0]
    return identify(image)


def main():
    if sys.platform != 'darwin':
        raise RuntimeError('This build route is restricted to the Mac, away from the serving cluster')
    if run('info', '--format', '{{.Architecture}}') != 'aarch64':
        raise RuntimeError('ARM64 Docker Desktop is required')
    # Classic Docker names images by config hash; the containerd store names
    # this imported object by its OCI manifest. Verify both representations.
    imported = json.loads((WORK / 'parent-import.json').read_text())
    parent_ref = imported['local_store_id']
    parent_bytes = (WORK / 'parent-config.json').read_bytes()
    if 'sha256:' + hashlib.sha256(parent_bytes).hexdigest() != PARENT:
        raise RuntimeError('Parent image identity mismatch')
    parent_config = json.loads(parent_bytes)
    parent = json.loads(run('image', 'inspect', parent_ref))[0]
    if parent['RootFS']['Layers'] != parent_config['rootfs']['diff_ids'] or parent['Config'] != parent_config['config']:
        raise RuntimeError('Imported parent configuration or layer identity changed')
    old = json.loads((REPO / 'experiments/2026-10-19-r6x-steering/source.json').read_text())
    for component in ('vllm', 'b12x', 'nccl', 'tilelang', 'tile_kernels', 'sparknet'):
        if parent['Config']['Labels']['local.spark3.' + component + '.tree'] != old[component]['expected_tree']:
            raise RuntimeError('Parent component mismatch: ' + component)
    recorded_sources = json.loads(run('run', '--rm', '--network=none', '--read-only',
                                      '--entrypoint', 'cat', parent_ref,
                                      '/opt/spark3/r6x-qualification/source-hashes.json'))
    if recorded_sources != json.loads((WORK / 'parent-source-hashes.json').read_text()):
        raise RuntimeError('Parent source inventory differs from the native compatibility proof')
    tagged = subprocess.run([*DOCKER, 'image', 'inspect', PARENT_TAG], text=True, capture_output=True)
    if tagged.returncode:
        run('tag', parent_ref, PARENT_TAG)
    elif json.loads(tagged.stdout)[0]['Id'] != parent_ref:
        raise RuntimeError('Parent tag changed')
    proof = json.loads((EXP / 'runs/native-input-proof.json').read_text())
    for arm in proof.values():
        if arm['native_source_changes']:
            raise RuntimeError('Rebuild native libraries before proceeding')
    common = WORK / 'context-native'
    common.mkdir(exist_ok=True)
    source = REPO / proof['s']['build_directory'] / 'context/sparknet-source'
    shutil.copytree(source, common / 'sparknet', dirs_exist_ok=True)
    (common / 'Dockerfile').write_text(f'''FROM {PARENT_TAG}
USER root
LABEL local.spark3.sparknet.revision="ed6af327a8953eb71de2ed925a5e605cb5eb6e20" local.spark3.sparknet.patch-head="ed6af327a8953eb71de2ed925a5e605cb5eb6e20" local.spark3.sparknet.tree="f997a594f03a696897e6ad8e50bb027ed877ebe9" local.spark3.sparknet.ref="refs/tags/v0.5.0"
COPY sparknet /tmp/sparknet-source
ENV SPARKNET_ROCE_CACHE_DIR=/opt/sparknet/roce-1022
RUN python3 -m pip install --no-index --no-build-isolation --no-deps /tmp/sparknet-source && python3 -c 'import importlib.metadata as m; assert m.version("dgx-spark-networking")=="0.5.0"; from sparknet.oneshot._proxy import load; print("proxy ABI", load().roce_abi_version())'
''')
    common_image = build(COMMON_TAG, common)
    receipts = {'native_parent': PARENT, 'parent_local_store_id': parent_ref,
                'common_image': common_image['Id'], 'common_config_id': common_image['portable_config_id'], 'arms': {},
                'gpu_verified': False, 'node_distribution': False}
    (WORK / 'images/build-progress.json').write_text(json.dumps(receipts, indent=2))
    for key in ('u', 'e', 's'):
        arm = proof[key]
        context = WORK / ('context-' + key)
        with (context / 'source.tar').open('rb') as f:
            digest = hashlib.file_digest(f, 'sha256').hexdigest()
        if digest != arm['source_archive_sha256']:
            raise RuntimeError('Source archive changed: ' + key)
        shutil.copy(EXP / 'tools/verify_image.py', context / 'verify_image.py')
        cfg_name = 's-loaded' if key == 's' else key
        config = json.loads((EXP / 'arms' / (cfg_name + '.json')).read_text())
        tag = config['container']['image']
        vllm = arm['vllm']
        labels = {**config['container']['expected_labels'],
                  'local.spark3.vllm.patch-head': vllm['patch_head'],
                  'local.site.native-parent': PARENT,
                  'local.site.qualification': '1022-offline-preparation',
                  'org.opencontainers.image.source': 'https://github.com/imDaniel/spark-ds41f',
                  'org.opencontainers.image.revision': json.loads((EXP/'base.json').read_text())['candidate_repository_commit']}
        label = ' '.join(k + '=' + json.dumps(v) for k, v in labels.items())
        (context / 'Dockerfile').write_text(f'''FROM {COMMON_TAG}
USER root
LABEL {label}
COPY verify_image.py source-hashes.json deletions.json /opt/spark3/1022-offline/
RUN python3 /opt/spark3/1022-offline/verify_image.py before
ADD source.tar /opt/spark3/candidate/vllm/
RUN find /opt/spark3/candidate/vllm -type d -name __pycache__ -prune -exec rm -rf '{{}}' + && python3 /opt/spark3/1022-offline/verify_image.py after
WORKDIR /opt/spark3/candidate/vllm
''')
        image = build(tag, context)
        for label_key, expected in labels.items():
            if image['Config']['Labels'][label_key] != expected:
                raise RuntimeError('Image label mismatch: ' + label_key)
        verification = json.loads(run('run', '--rm', '--network=none', '--read-only',
                                     '--entrypoint', 'cat', image['Id'],
                                     '/opt/spark3/1022-offline/verification.json'))
        receipts['arms'][key] = dict(tag=tag, local_store_id=image['Id'],
                                     image_id=image['portable_config_id'], size_bytes=image['Size'],
                                     verification=verification, **arm)
        (WORK / 'images/build-progress.json').write_text(json.dumps(receipts, indent=2))
        print(key, image['Id'], flush=True)
    (EXP / 'runs/images.json').write_text(json.dumps(receipts, indent=2) + '\n')


if __name__ == '__main__':
    main()
