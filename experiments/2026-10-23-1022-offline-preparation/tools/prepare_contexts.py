"""Prepare/recheck the three pinned build trees and exact source archives.

The retained parent's source archive is an explicit input; build_images.py
also compares its inventory with the content-addressed retained image.
Run on the Mac. This tool performs no node operations or GPU execution.
"""
import argparse
import hashlib
import json
import pathlib
import runpy
import subprocess

EXP = pathlib.Path(__file__).resolve().parents[1]
REPO = EXP.parents[1]
WORK = REPO / '.work/1022-offline-preparation'


def inventory(archive):
    import tarfile
    with tarfile.open(archive) as tar:
        files = {m.name: hashlib.sha256(tar.extractfile(m).read()).hexdigest()
                 for m in tar if m.isfile()}
        links = {m.name: m.linkname for m in tar if m.issym() or m.islnk()}
    return files, links


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent-source-archive', required=True, type=pathlib.Path)
    parser.add_argument('--seed-build', type=pathlib.Path,
                        help='Optional verified native-input seed; every component is rechecked')
    args = parser.parse_args()
    spark = runpy.run_path(str(REPO / 'bin/spark'))
    meta = json.loads((EXP / 'base.json').read_text())
    old, old_links = inventory(args.parent_source_archive)
    WORK.mkdir(exist_ok=True, parents=True)
    (WORK / 'logs').mkdir(exist_ok=True)
    (WORK / 'parent-source-hashes.json').write_text(json.dumps(old, sort_keys=True))
    report = {}
    for arm in ('u', 'e', 's'):
        recipe = EXP / 'arms' / (('s-loaded' if arm == 's' else arm) + '.json')
        lock = REPO / json.loads(recipe.read_text())['upstreams_config']
        inputs = spark['build_inputs'](json.loads(lock.read_text()))
        directory = spark['build_directory'](inputs)
        if not directory.exists() and args.seed_build:
            subprocess.run(['cp', '-cR', str(args.seed_build.resolve()), str(directory)], check=True)
        for action in ('prepare', 'check'):
            with (WORK / 'logs' / f'{action}-{arm}.txt').open('w') as log:
                subprocess.run([str(REPO / 'bin/spark'), '--cluster-config',
                                str(recipe.relative_to(REPO)), 'build', action], cwd=REPO,
                               stdout=log, stderr=subprocess.STDOUT, check=True)
        source = directory / 'src/vllm'
        context = WORK / ('context-' + arm)
        context.mkdir(exist_ok=True)
        head = meta['arms'][arm]['patch_head']
        with (context / 'source.tar').open('wb') as stream:
            subprocess.run(['git', '-C', str(source), 'archive', head], stdout=stream, check=True)
        current, links = inventory(context / 'source.tar')
        if links != old_links:
            raise RuntimeError('Source link changes require a separate native compatibility review')
        changed = [name for name in sorted(current.keys() | old.keys()) if current.get(name) != old.get(name)]
        native = [name for name in changed if not name.endswith('.py')]
        if native:
            raise RuntimeError('Native build inputs changed: ' + repr(native))
        deleted = sorted(old.keys() - current.keys())
        (context / 'source-hashes.json').write_text(json.dumps(current, sort_keys=True))
        (context / 'deletions.json').write_text(json.dumps(deleted))
        report[arm] = dict(vllm_changed_files=changed, native_source_changes=native,
                           deleted_files=deleted,
                           source_archive_sha256=spark['sha256'](context / 'source.tar'),
                           source_files=len(current), vllm=meta['arms'][arm],
                           build_directory=str(directory.relative_to(REPO)))
    target = EXP / 'runs/native-input-proof.json'
    if target.exists() and json.loads(target.read_text()) != report:
        raise RuntimeError('Existing proof differs; preserve it and create a new experiment')
    if not target.exists():
        target.write_text(json.dumps(report, indent=2) + '\n')
    print('Prepared source archives match the pinned proof; native source and links are unchanged.')


if __name__ == '__main__':
    main()
