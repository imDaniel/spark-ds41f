"""Exercise profile publication with the locked source, checkpoint metadata and accepted sidecar."""

import errno
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

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
    from steering import artifact

    output = tmp_path / "release"
    resolve = artifact.resolve_bundled

    def unexpected_path(release, selector):
        name, _, digest = resolve(release, selector)
        return name, release / "different.safetensors", digest

    with monkeypatch.context() as patch:
        patch.setattr(artifact, "resolve_bundled", unexpected_path)
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
