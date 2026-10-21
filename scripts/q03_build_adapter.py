#!/usr/bin/env python3
"""Owned wrapper around the verified independent bin/spark build recipe.

No import-time operations. Never changes original service source or policies.
The core first drains its journalled build process group, then calls quiesce.
Automatic cleanup covers proven prelaunch aborts or durably completed normal
build+smoke only. Abnormal/unknown daemon outcomes retain the hold and require
manual reconciliation; this adapter never claims universal crash recovery.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

sys.dont_write_bytecode = True
REAL_POPEN = subprocess.Popen


def require(value, message):
    if not value:
        raise RuntimeError(message)


def atomic(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def identity(pid):
    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    return {'pid': pid, 'start_ticks': fields[19], 'pgid': int(fields[2])}


def group_members(pgid):
    found = []
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            fields = path.read_text().rsplit(')', 1)[1].split()
            if int(fields[2]) == pgid and fields[0] != 'Z':
                found.append(int(path.parent.name))
        except FileNotFoundError:
            pass
        # Permission/parse errors are unknown, not proof of quiescence.
    return found


def owned_smoke(container, name, window, known_id=None):
    labels = container.get('Config', {}).get('Labels') or {}
    require(container.get('Name') == '/' + name and labels.get('q03.window') == window
            and labels.get('q03.role') == 'build-smoke', 'Foreign smoke container')
    if known_id is not None:
        require(container.get('Id') == known_id, 'Smoke container identity changed')
    return container['Id']


def smoke_create_command(command, name, window):
    require(command[:2] == ['docker', 'run'], 'Unexpected smoke launcher')
    require(command[2:5] == ['--rm', '-i', '--gpus'] and command[5] == 'all',
            'Official smoke command changed')
    require('--entrypoint' in command and command[-1] == '-', 'Unexpected smoke payload')
    require(not any(x in command for x in ('--name', '--label', '--mount', '-v')),
            'Unexpected smoke mounts/ownership')
    # Keep official GPU, memory, ulimit, image, interpreter and stdin arguments.
    return ['docker', 'create', '--name', name, '--label', 'q03.window=' + window,
            '--label', 'q03.role=build-smoke', *command[3:]]


class Adapter:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        capsule_path = self.directory / 'capsule.json'
        data = capsule_path.read_bytes()
        require(hashlib.sha256(data).hexdigest() ==
                (self.directory / 'capsule.sha256').read_text().strip(), 'Capsule drift')
        self.capsule = json.loads(data)
        root = Path(self.capsule['manifest']['independent_root']).resolve()
        require(root.is_absolute() and self.directory != root
                and self.directory.is_relative_to(root), 'Session outside independent root')
        require(Path(self.capsule['session_dir']).resolve() == self.directory, 'Wrong session')
        self.window = self.capsule['window_id']
        require(len(self.window) == 32 and all(c in '0123456789abcdef' for c in self.window),
                'Invalid window identity')
        self.manifest = self.capsule['manifest']
        require([j['kind'] for j in self.manifest['jobs']] == ['build'], 'Not a build-only session')
        for name in ('q03_build_adapter.py', 'q03_window_build.py', 'q03_buildkit_plan.py'):
            source = Path(__file__).with_name(name)
            require(hashlib.sha256(source.read_bytes()).hexdigest() ==
                    self.capsule['recovery_source_sha256'][name], 'Build recovery code drift')
        self.plan = self.capsule['build_plan']['builder']
        self.receipt = json.loads(Path(self.manifest['build']['builder_receipt_path']).read_text())
        require(self.receipt['plan'] == self.plan, 'Builder plan drift')
        require(self.receipt.get('preabsence') ==
                {'container': True, 'volume': True, 'metadata': True},
                'Builder lacks pinned fresh-provision evidence')
        require(hashlib.sha256(Path(self.manifest['build']['builder_receipt_path']).read_bytes()).hexdigest()
                == self.manifest['build']['builder_receipt_sha256'], 'Builder receipt drift')
        self.builder_id = self.receipt['result']['container_id']
        self.name = 'q03-build-smoke-' + self.window
        self.journal_path = self.directory / 'build-adapter.json'
        self.fence = self.directory / 'build-adapter.sealed.json'
        self.lock_path = self.directory / 'build-adapter.lock'
        self.journal = (json.loads(self.journal_path.read_text()) if self.journal_path.exists()
                        else {'window_id': self.window, 'commands': [], 'smoke_name': self.name,
                              'smoke_id': None, 'builder_id': self.builder_id})
        require(self.journal['window_id'] == self.window
                and self.journal['builder_id'] == self.builder_id, 'Build journal drift')

    @contextlib.contextmanager
    def lock(self):
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def save(self):
        atomic(self.journal_path, self.journal)

    def authorize_launch(self):
        require(not self.fence.exists(), 'Build launch is fenced')
        hold = json.loads(Path(self.capsule['hold_path']).read_text())
        require((hold.get('window'), hold.get('holder')) ==
                (self.window, self.capsule['holder']), 'Build no longer owns hold')
        require(time.time() < self.capsule['deadline_epoch'], 'Build deadline expired')

    def spawn(self, command, kwargs):
        require(isinstance(command, (list, tuple)) and command, 'Shell commands are forbidden')
        require(not kwargs.get('shell') and not kwargs.get('preexec_fn')
                and kwargs.get('process_group') in (None, -1), 'Escaping process group forbidden')
        kwargs = dict(kwargs)
        kwargs['start_new_session'] = False
        with self.lock():
            self.authorize_launch()
            record = {'argv': list(command), 'state': 'intent', 'process': None}
            self.journal['commands'].append(record)
            self.save()  # Core supervisor already owns the whole inherited PGID.
            process = REAL_POPEN(command, **kwargs)
            record.update(state='launched', process=identity(process.pid))
            require(record['process']['pgid'] == self.journal['build_group']['pid'],
                    'Child escaped supervised process group')
            self.save()
            return process

    def raw(self, command):
        process = self.spawn(command, {'stdout': subprocess.PIPE, 'stderr': subprocess.PIPE,
                                       'text': True})
        stdout, stderr = process.communicate()
        require(process.returncode == 0, 'Owned subprocess failed: ' + command[0])
        return stdout.strip()

    def prepare_smoke(self, command):
        with self.lock():
            self.authorize_launch()
            require(self.journal.get('smoke_id') is None, 'Smoke already launched')
            self.journal['smoke_create_intent'] = True
            self.save()
        # Successful listing avoids interpreting a daemon error as absence.
        require(self.name not in self.raw(['docker', 'ps', '-a', '--format', '{{.Names}}']).splitlines(),
                'Smoke name already exists')
        image_tag = command[-2]
        images = json.loads(self.raw(['docker', 'image', 'inspect', image_tag]))
        require(len(images) == 1, 'Ambiguous smoke image')
        with self.lock():
            self.authorize_launch()
            self.journal['smoke_image_id'] = images[0]['Id']
            self.save()
        container_id = self.raw(smoke_create_command(command, self.name, self.window))
        require(len(container_id) == 64 and all(c in '0123456789abcdef' for c in container_id),
                'Unknown created smoke identity')
        rows = json.loads(self.raw(['docker', 'container', 'inspect', container_id]))
        require(len(rows) == 1, 'Ambiguous smoke identity')
        owned_smoke(rows[0], self.name, self.window, container_id)
        require(rows[0]['Image'] == self.journal['smoke_image_id'], 'Smoke image changed')
        with self.lock():
            self.authorize_launch()
            self.journal['smoke_id'] = container_id
            self.save()
        # Any later start addresses this immutable ID, never a reusable name.
        return ['docker', 'start', '--attach', '--interactive', container_id]

    def run(self):
        with self.lock():
            self.authorize_launch()
            require('build_group' not in self.journal, 'Build launch cannot be retried')
            leader = identity(os.getpgrp())
            require(leader['pid'] != os.getpid(), 'Core-owned group supervisor required')
            self.journal['build_group'] = leader
            self.save()
        sys.path.insert(0, str(Path(__file__).parent))
        from q03_window_build import _context, _builder
        from q03_buildkit_plan import clean_environment

        class Ops:
            resolve = staticmethod(lambda p: str(Path(p).resolve()))
            read_json = staticmethod(lambda p: json.loads(Path(p).read_text()))
            sha256 = staticmethod(lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest())
            def run(_, argv, cwd=None, timeout=120):
                return subprocess.run(argv, cwd=cwd, timeout=timeout, capture_output=True, text=True)

        _, checkout, _, _, _ = _context(self.manifest, Ops())
        plan, _ = _builder(self.manifest['build'], Ops(), self.manifest['independent_root'])
        require(plan == self.plan, 'Builder changed before launch')
        environment = clean_environment(plan)
        os.environ.clear()
        os.environ.update(environment)
        os.environ['GIT_OPTIONAL_LOCKS'] = '0'
        os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
        source = Path(checkout) / 'bin/spark'
        expected = subprocess.check_output(['git', '--no-optional-locks', '-C', checkout,
            'show', self.manifest['preflight']['publication']['sha'] + ':bin/spark'])
        require(source.read_bytes() == expected, 'Candidate build recipe changed')
        loader = importlib.machinery.SourceFileLoader('q03_verified_build_recipe', str(source))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        spark = importlib.util.module_from_spec(spec)
        sys.modules[loader.name] = spark
        loader.exec_module(spark)
        require(spark.BUILD_MIN_AVAILABLE_GIB == 64 and spark.BUILD_RESERVE_GIB == 24,
                'Official build guard contract changed')
        adapter = self

        def popen(command, *args, **kwargs):
            require(not args, 'Unexpected positional Popen arguments')
            if list(command[:2]) == ['docker', 'run']:
                command = adapter.prepare_smoke(list(command))
            return adapter.spawn(command, kwargs)

        def cancel(process):
            # No nested process group exists. Stop the client; the core and
            # daemon outcome is then UNKNOWN; retain hold for reconciliation.
            for sig, seconds in ((signal.SIGINT, 30), (signal.SIGTERM, 10), (signal.SIGKILL, 5)):
                if process.poll() is not None:
                    return
                process.send_signal(sig)
                try:
                    process.wait(timeout=seconds)
                    return
                except subprocess.TimeoutExpired:
                    pass
            raise RuntimeError('Owned build client did not terminate')

        spark.cancel_process_group = cancel
        subprocess.Popen = popen
        try:
            args = spark.parser().parse_args(['--shape', 'tp4-ring', '--cluster-config',
                'config/q03-window.local.json', 'build', 'image', '--tag', self.manifest['build']['tag'], '--apply'])
            with self.lock():
                self.authorize_launch()
                self.journal['recipe_launch_intent'] = True
                self.save()
            result = args.func(args)
            require(result == 0, 'Official build or smoke failed')
            self.journal['official_build_completed'] = True
            self.save()
        finally:
            subprocess.Popen = REAL_POPEN

    def quiesce(self, run):
        """run(argv)->stdout raises on failure. Core has drained local group first."""
        with self.lock():
            atomic(self.fence, {'window_id': self.window, 'sealed': True})
        if 'build_group' in self.journal:
            require(not group_members(self.journal['build_group']['pid']),
                    'Build process group has not drained')
        launched = self.journal.get('recipe_launch_intent') is True
        require(not launched or self.journal.get('official_build_completed') is True,
                'Build outcome uncertain; retain hold for manual daemon reconciliation')
        docker = ['docker', '--host', 'unix:///var/run/docker.sock']
        names = run(docker + ['ps', '-a', '--format', '{{.Names}}']).splitlines()
        ids = run(docker + ['ps', '-a', '--no-trunc', '--format', '{{.ID}}']).splitlines()
        smoke_id = self.journal.get('smoke_id')
        if self.journal.get('smoke_create_intent') and smoke_id is None and self.name not in names:
            raise RuntimeError('Unresolved smoke create intent; retain hold for reconciliation')
        if self.name in names or (smoke_id is not None and smoke_id in ids):
            rows = json.loads(run(docker + ['container', 'inspect', smoke_id or self.name]))
            require(len(rows) == 1, 'Ambiguous smoke container')
            container_id = owned_smoke(rows[0], self.name, self.window, self.journal.get('smoke_id'))
            require(rows[0]['Image'] == self.journal['smoke_image_id'], 'Smoke image changed')
            self.journal['smoke_id'] = container_id
            self.save()
            run(docker + ['container', 'rm', '--force', container_id])
        require(self.name not in run(docker + ['ps', '-a', '--format', '{{.Names}}']).splitlines(),
                'Owned smoke cleanup is incomplete')
        # Remove only the immutable provisioned container ID. Preserve the state
        # volume and receipts. A delayed start of this old ID can no longer run.
        names = run(docker + ['ps', '-a', '--format', '{{.Names}}']).splitlines()
        ids = run(docker + ['ps', '-a', '--no-trunc', '--format', '{{.ID}}']).splitlines()
        if launched and not self.journal.get('quiesced'):
            require(self.plan['container'] in names and self.builder_id in ids,
                    'Completed build daemon disappeared; retain hold for reconciliation')
        if self.plan['container'] in names or self.builder_id in ids:
            rows = json.loads(run(docker + ['container', 'inspect', self.builder_id]))
            require(len(rows) == 1 and rows[0]['Id'] == self.builder_id, 'Foreign builder container')
            from q03_buildkit_plan import verify_owned_state
            volumes = json.loads(run(docker + ['volume', 'inspect', self.plan['volume']]))
            require(len(volumes) == 1, 'Ambiguous builder state volume')
            observed = verify_owned_state(self.plan, rows[0], volumes[0], self.receipt['result'])
            require(observed['container_id'] == self.builder_id, 'Builder identity mismatch')
            run(docker + ['container', 'rm', '--force', self.builder_id])
        require(self.plan['container'] not in run(docker + ['ps', '-a', '--format', '{{.Names}}']).splitlines(),
                'Owned builder cleanup is incomplete')
        final_ids = run(docker + ['ps', '-a', '--no-trunc', '--format', '{{.ID}}']).splitlines()
        require(self.builder_id not in final_ids and
                (self.journal.get('smoke_id') is None or self.journal['smoke_id'] not in final_ids),
                'Owned container ID remains after cleanup')
        volumes = json.loads(run(docker + ['volume', 'inspect', self.plan['volume']]))
        require(len(volumes) == 1 and volumes[0].get('Name') == self.plan['volume']
                and volumes[0].get('Driver') == 'local'
                and volumes[0].get('Options') in (None, {}), 'Builder volume preservation unknown')
        self.journal['quiesced'] = True
        self.save()
        return {'window_id': self.window, 'safe_to_restore': True,
                'builder_volume_preserved': self.plan['volume']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('run', 'quiesce'))
    parser.add_argument('--session-dir', required=True)
    args = parser.parse_args()
    adapter = Adapter(args.session_dir)
    if args.action == 'run':
        adapter.run()
    else:
        sys.path.insert(0, str(Path(__file__).parent))
        from q03_buildkit_plan import clean_environment
        environment = clean_environment(adapter.plan)
        def run(argv):
            timeout = 25 if 'rm' in argv else 5
            return subprocess.run(argv, capture_output=True, text=True, check=True,
                                  timeout=timeout, env=environment).stdout
        print(json.dumps(adapter.quiesce(run), sort_keys=True))


if __name__ == '__main__':
    main()
