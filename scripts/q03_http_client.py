#!/usr/bin/env python3
"""One fixed, bounded servebench burst. The remote supervisor owns descendants."""
import argparse
import hashlib
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import sys


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


class FrozenToolbox(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Source-only loader for the exact declared servebench closure, not sys.path."""
    def __init__(self, root, sources):
        self.root, self.sources = root, sources

    @staticmethod
    def relative(name):
        return 'servebench/__init__.py' if name == 'servebench' else name.replace('.', '/') + '.py'

    def find_spec(self, fullname, path=None, target=None):
        if fullname != 'servebench' and not fullname.startswith('servebench.'):
            return None
        relative = self.relative(fullname)
        if relative not in self.sources:
            raise ImportError('undeclared toolbox import: ' + fullname)
        package = [str(self.root / 'servebench')] if fullname == 'servebench' else None
        return importlib.util.spec_from_file_location(fullname, self.root / relative,
                                                      loader=self, submodule_search_locations=package)

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        relative = self.relative(module.__name__)
        exec(compile(self.sources[relative], str(self.root / relative), 'exec'), module.__dict__)


class FrozenQualification(FrozenToolbox):
    """The three fixed controller imports, source-only even with stale .pyc."""
    @staticmethod
    def relative(name):
        return name + '.py'

    def find_spec(self, fullname, path=None, target=None):
        if fullname not in ('q03_qualification', 'q03_owner_model', 'q03_held_window', 'q03_quality_matrix'):
            return None
        return importlib.util.spec_from_file_location(fullname, self.root / self.relative(fullname), loader=self)


def load_qualification(client):
    names = ('q03_qualification', 'q03_owner_model', 'q03_held_window')
    workload = client['qualification']['workload']
    if workload == 'lean_tokens_stage1b_v1':
        names += ('q03_quality_matrix',)
    elif workload != 'lean_tokens_c1':
        raise RuntimeError('unknown finite qualification workload')
    pins = client.get('qualification_sources')
    if not isinstance(pins, dict) or set(pins) != set(names) or any(name in sys.modules for name in names):
        raise RuntimeError('qualification source closure differs or was preloaded')
    root, sources = Path(__file__).resolve().parent, {}
    for name in names:
        raw = (root / (name + '.py')).read_bytes()
        if hashlib.sha256(raw).hexdigest() != pins[name]:
            raise RuntimeError('frozen qualification source changed: ' + name)
        sources[name + '.py'] = raw
    sys.meta_path.insert(0, FrozenQualification(root, sources))
    if workload == 'lean_tokens_stage1b_v1':
        from q03_quality_matrix import run_client
    else:
        from q03_qualification import run_client
    return run_client


class FrozenPerformance(FrozenQualification):
    def find_spec(self, fullname, path=None, target=None):
        if fullname not in ('q03_performance', 'q03_owner_model', 'q03_held_window'):
            return None
        return importlib.util.spec_from_file_location(fullname, self.root / self.relative(fullname), loader=self)


def load_performance(client):
    names = ('q03_performance', 'q03_owner_model', 'q03_held_window')
    pins = client.get('performance_sources')
    if not isinstance(pins, dict) or set(pins) != set(names) or any(name in sys.modules for name in names):
        raise RuntimeError('performance source closure differs or was preloaded')
    root, sources = Path(__file__).resolve().parent, {}
    for name in names:
        raw = (root / (name + '.py')).read_bytes()
        if hashlib.sha256(raw).hexdigest() != pins[name]:
            raise RuntimeError('frozen performance source changed: ' + name)
        sources[name + '.py'] = raw
    sys.meta_path.insert(0, FrozenPerformance(root, sources))
    from q03_performance import run_client
    return run_client


class FrozenLegacy(FrozenQualification):
    def find_spec(self, fullname, path=None, target=None):
        if fullname not in ('q03_legacy_case_replay', 'q03_baseline_identity', 'q03_owner_model', 'q03_held_window'):
            return None
        return importlib.util.spec_from_file_location(fullname, self.root / self.relative(fullname), loader=self)


def load_legacy(client):
    names = ('q03_legacy_case_replay', 'q03_baseline_identity', 'q03_owner_model', 'q03_held_window')
    pins = client.get('legacy_sources')
    if not isinstance(pins, dict) or set(pins) != set(names) or any(name in sys.modules for name in names):
        raise RuntimeError('legacy source closure differs or was preloaded')
    root, sources = Path(__file__).resolve().parent, {}
    for name in names:
        raw = (root / (name + '.py')).read_bytes()
        if hashlib.sha256(raw).hexdigest() != pins[name]:
            raise RuntimeError('frozen legacy source changed: ' + name)
        sources[name + '.py'] = raw
    sys.meta_path.insert(0, FrozenLegacy(root, sources))
    from q03_legacy_case_replay import run_client
    return run_client


QUALITY_NAMES = ('q03_quality_pilot', 'q03_quality_capture', 'q03_legacy_case_replay',
                 'q03_owner_model', 'q03_held_window')


class FrozenQuality(FrozenQualification):
    def find_spec(self, fullname, path=None, target=None):
        if fullname not in QUALITY_NAMES:
            return None
        return importlib.util.spec_from_file_location(fullname, self.root / self.relative(fullname), loader=self)


def load_quality(client):
    pins = client.get('quality_sources')
    if not isinstance(pins, dict) or set(pins) != set(QUALITY_NAMES) or any(name in sys.modules for name in QUALITY_NAMES):
        raise RuntimeError('quality source closure differs or was preloaded')
    root, sources = Path(__file__).resolve().parent, {}
    for name in QUALITY_NAMES:
        raw = (root / (name + '.py')).read_bytes()
        if hashlib.sha256(raw).hexdigest() != pins[name]:
            raise RuntimeError('frozen quality source changed: ' + name)
        sources[name + '.py'] = raw
    sys.meta_path.insert(0, FrozenQuality(root, sources))
    from q03_quality_pilot import run_client
    return run_client


def run(plan):
    # No supplied argv/import path. The frozen plan has been admitted and is
    # rehashed by the controller and remote boundary before this fixed program.
    client = plan['client']
    root = Path(client['toolbox_path'])
    sources = {}
    for relative, expected in client['toolbox_files'].items():
        raw = (root / relative).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise RuntimeError('frozen toolbox file changed: ' + relative)
        sources[relative] = raw
    output = Path(client['output'])
    if output.exists() or output.with_name('client-receipt.json').exists():
        raise RuntimeError('client output already exists')
    if any(name == 'servebench' or name.startswith('servebench.') for name in sys.modules):
        raise RuntimeError('toolbox must not be preloaded')
    loader = FrozenToolbox(root, sources)
    sys.meta_path.insert(0, loader)
    from servebench.__main__ import main
    argv = ['run', plan['url'], '--mode', 'burst', '--concurrency', '1',
            '--batches', '1', '--warmup', '0', '--duration', '1', '--max-tokens', '16',
            '--seed', '0', '--model', 'deepseek-v4.1-flash', '--prompts', 'default',
            '--save-requests', '--label', plan['attempt_id'], '--output', str(output)]
    if plan['selection']['steering'] != 'off':
        argv += ['--extra-body', canonical({'vllm_xargs': {
            'steering_profile': 'heretic-1017-attn-v1',
            'steering_scale': 0 if plan['selection']['steering'] == 'loaded_zero' else 1}}).decode()]
    error = None
    try:
        if 'quality_pilot' in client:
            code = load_quality(client)(plan, main)
        elif 'legacy_case' in client:
            code = load_legacy(client)(plan, main)
        elif 'performance_screen' in client:
            code = load_performance(client)(plan, main)
        elif 'qualification' in client:
            code = load_qualification(client)(plan, main)
        else:
            code = main(argv)
    except Exception as exc:
        code, error = 1, f'{type(exc).__name__}: {exc}'
    receipt = {'window_id': plan['window_id'], 'attempt_id': plan['attempt_id'],
               'plan_sha256': plan['plan_sha256'], 'exit_code': code,
               'error': error, 'output_sha256': hashlib.sha256(output.read_bytes()).hexdigest()
               if output.is_file() else None}
    path = output.with_name('client-receipt.json')
    if output.is_file():
        with output.open('rb') as stream:
            os.fsync(stream.fileno())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(canonical(receipt))
        stream.flush()
        os.fsync(stream.fileno())
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    # Successful process exit is only descendant-lifecycle completion; the
    # controller separately validates exit_code/output before accepting results.
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--sha256', required=True)
    args = parser.parse_args()
    raw = Path(args.plan).read_bytes()
    if hashlib.sha256(raw).hexdigest() != args.sha256:
        raise RuntimeError('immutable client plan changed')
    print(json.dumps(run(json.loads(raw)), sort_keys=True))


if __name__ == '__main__':
    # This owned, isolated child invokes the unchanged toolbox's file writers.
    # Never change the coordinator's process-global umask from run() or main().
    os.umask(0o077)
    # The capture verifies this very loader, not a second ambient import.
    sys.modules['q03_http_client'] = sys.modules[__name__]
    main()
