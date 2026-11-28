"""Exercise profile publication with the locked source, checkpoint metadata and accepted sidecar."""

import errno
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

import rebind_profile
from rebind_profile import rebind


@pytest.fixture(scope="module")
def inputs():
    return tuple(Path(os.environ[key]).resolve() for key in (
        "STEERING_VLLM_SOURCE", "STEERING_MODEL_IDENTITY", "STEERING_ACCEPTED_PROFILE",
    ))


def release_bytes(release):
    return {path.relative_to(release): path.read_bytes()
            for path in release.rglob("*") if path.is_file()}


@pytest.fixture(scope="module")
def reference(tmp_path_factory, inputs):
    release = tmp_path_factory.mktemp("reference") / "release"
    return rebind(*inputs, release), release_bytes(release)


@pytest.mark.parametrize("parent", ["real", "alias", "alias/child/.."])
def test_publish_by_directory_alias(tmp_path, monkeypatch, inputs, reference, parent):
    real = tmp_path / "real"
    (real / "child").mkdir(parents=True)
    (tmp_path / "alias").symlink_to(real, target_is_directory=True)
    monkeypatch.chdir(tmp_path)
    output = Path(parent) / "release"
    receipt = rebind(*inputs, output)
    assert (receipt, release_bytes(real / "release")) == reference
    assert not list(real.glob(".profile-rebind-*"))
    with pytest.raises(FileExistsError):
        rebind(*inputs, output)
    assert release_bytes(real / "release") == reference[1]


def test_existing_output_symlink_is_preserved(tmp_path, inputs):
    existing = tmp_path / "release"
    target = tmp_path / "absent"
    existing.symlink_to(target)
    with pytest.raises(FileExistsError):
        rebind(*inputs, existing)
    assert existing.is_symlink() and existing.readlink() == target
    assert not target.exists()


def test_invalid_index_cleans_stage_and_allows_retry(tmp_path, monkeypatch, inputs, reference):
    output = tmp_path / "release"
    load = rebind_profile.candidate_codec

    @contextmanager
    def incomplete_index(source):
        with load(source) as (artifact, profile):
            resolve = artifact.resolve_bundled

            def unexpected_path(release, selector):
                name, _, digest = resolve(release, selector)
                return name, release / "different.safetensors", digest

            artifact.resolve_bundled = unexpected_path
            yield artifact, profile

    with monkeypatch.context() as patch:
        patch.setattr(rebind_profile, "candidate_codec", incomplete_index)
        with pytest.raises(ValueError, match="index does not resolve"):
            rebind(*inputs, output)
    assert not list(tmp_path.iterdir())
    assert (rebind(*inputs, output), release_bytes(output)) == reference


def test_concurrent_rebind_publishes_one_complete_release(tmp_path, monkeypatch, inputs, reference):
    output = tmp_path / "release"
    ready = Barrier(2, timeout=10)
    rename = Path.rename

    def publish(path, destination):
        if destination == output:
            ready.wait()
        return rename(path, destination)

    monkeypatch.setattr(Path, "rename", publish)
    with ThreadPoolExecutor(max_workers=2) as pool:
        runs = [pool.submit(rebind, *inputs, output) for _ in range(2)]
    failures = [run.exception() for run in runs if run.exception() is not None]
    assert len(failures) == 1 and isinstance(failures[0], OSError)
    assert failures[0].errno in (errno.EEXIST, errno.ENOTEMPTY)
    assert (next(run.result() for run in runs if run.exception() is None),
            release_bytes(output)) == reference
    assert not list(tmp_path.glob(".profile-rebind-*"))

def test_rebind_after_loading_another_candidate(tmp_path, inputs):
    source, metadata, accepted = inputs
    experiment = rebind_profile.EXPERIMENT
    previous = experiment.parent / "2026-11-28-1126-attention-fixes"
    revision = json.loads((previous / "source.json").read_text())["vllm"]["patch_head"]
    package = Path("vllm/models/deepseek_v4_1/steering")
    codec_files = [package / name for name in ("__init__.py", "artifact.py", "profile.py")]
    template_files = [
        Path("vllm/tokenizers/deepseek_v41.py"),
        Path("vllm/tokenizers/deepseek_v41_encoding.py"),
        Path("vllm/renderers/deepseek_v41.py"),
    ]
    for relative in codec_files + template_files:
        path = tmp_path / "previous" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(subprocess.check_output(
            ["git", "-C", str(source), "show", f"{revision}:{relative}"]
        ))

    fresh_dir = tmp_path / "fresh"
    tool = Path(rebind_profile.__file__).resolve()
    subprocess.run([sys.executable, str(tool), "--vllm-source", str(source),
                    "--model-identity", str(metadata), "--input", str(accepted),
                    "--output", str(fresh_dir)], check=True, stdout=subprocess.DEVNULL)
    expected = json.loads((fresh_dir / "receipt.json").read_text())
    previous_template = json.loads((previous / "profile-rebind.json").read_text())["new_template_identity"]
    assert previous_template != expected["new_template_identity"]

    reused_dir = tmp_path / "reused"
    # Model an interpreter in which a preceding experiment imported its codec.
    script = """
import sys
from pathlib import Path
from steering import profile
from rebind_profile import rebind
source, metadata, accepted, output = map(Path, sys.argv[1:5])
assert profile.template_identity(metadata) == sys.argv[5]
search = list(sys.path)
bytecode = sys.dont_write_bytecode
rebind(source, metadata, accepted, output)
assert list(sys.path) == search and sys.dont_write_bytecode == bytecode
assert sys.modules["steering.profile"] is profile
assert not any(key.startswith("_ds41_rebind_") for key in sys.modules)
"""
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(tool.parent),
               str(tmp_path / "previous" / package.parent))))
    subprocess.run([sys.executable, "-c", script, str(source), str(metadata), str(accepted),
                    str(reused_dir), previous_template], env=env, check=True)
    assert release_bytes(reused_dir) == release_bytes(fresh_dir)
