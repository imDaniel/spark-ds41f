"""Run the candidate's CUDA Graph capture and failure paths with a CPU fake device."""

from collections import defaultdict
from dataclasses import replace
from unittest.mock import Mock

import pytest
import torch

from tests.v1.worker import test_gpu_model_runner_v2_cudagraph_profiling as profiling
from vllm.config.compilation import CUDAGraphMode
from vllm.v1.worker.gpu import cudagraph_utils
from vllm.v1.worker.gpu.model_input_variant import (
    expand_capture_variants,
    select_input_variant,
)


def install_fake_device(monkeypatch):
    device = profiling._FakeDevice()
    profiling._patch_capture(monkeypatch, device)
    monkeypatch.setattr(cudagraph_utils, "_new_cudagraph", object)
    counter = cudagraph_utils.compilation_counter
    # The real capture loop increments this process-wide metric.
    monkeypatch.setattr(counter, "num_cudagraph_captured", counter.num_cudagraph_captured)
    return device


@pytest.mark.parametrize("sample_full_graphs", [False, True], ids=["full", "sampled"])
@pytest.mark.parametrize("breakable", [False, True], ids=["compiled", "breakable"])
def test_full_and_piecewise_warmups(monkeypatch, sample_full_graphs, breakable):
    device = install_fake_device(monkeypatch)
    sizes = [128, 96, 64, 32, 1]
    manager = profiling._CapturingGraphManager(sizes)
    manager.use_breakable_cg = breakable
    manager._sample_full_descs = sample_full_graphs
    stock = manager._capture_descs[CUDAGraphMode.FULL]
    full = expand_capture_variants(stock, (0, 1))
    piecewise = [
        replace(desc, cg_mode=CUDAGraphMode.PIECEWISE, num_reqs=None, uniform_token_count=None)
        for desc in stock
    ]
    manager._capture_descs[CUDAGraphMode.FULL] = full
    manager._capture_descs[CUDAGraphMode.PIECEWISE] = piecewise

    full_warmups = defaultdict(list)
    full_captures = defaultdict(list)
    piecewise_calls = defaultdict(list)

    def create_forward_fn(desc, warmup):
        def forward(runtime_mode):
            if desc.cg_mode == CUDAGraphMode.FULL:
                assert device.capturing == (not warmup)
                calls = full_warmups if warmup else full_captures
                calls[desc.model_input_variant].append(desc.num_tokens)
            else:
                piecewise_calls[runtime_mode].append(desc.num_tokens)

        return forward

    manager.capture(create_forward_fn)

    expected_full = [128, 96, 1] if sample_full_graphs else sizes
    assert full_warmups == {0: [128], 1: [128]}
    assert full_captures == {0: expected_full, 1: expected_full}
    assert set(manager.graphs) == {desc for desc in full if desc.num_tokens in expected_full}
    assert all(select_input_variant(desc, 1).cg_mode == CUDAGraphMode.NONE for desc in piecewise)
    assert piecewise_calls[CUDAGraphMode.NONE] == ([128] if breakable else sizes)
    assert piecewise_calls[CUDAGraphMode.PIECEWISE] == sizes


def test_warmup_families_keep_existing_descriptor_dimensions(monkeypatch):
    device = install_fake_device(monkeypatch)
    manager = profiling._CapturingGraphManager([128, 16, 1])
    layouts = (
        {},
        {"num_active_loras": 1},
        {"uniform_token_count": 1},
        {"max_query_len": 16},
        {"num_ubatches": 2},
    )
    descriptors = [
        replace(desc, **layout)
        for desc in manager._capture_descs[CUDAGraphMode.FULL]
        for layout in layouts
    ]
    manager._capture_descs[CUDAGraphMode.FULL] = expand_capture_variants(descriptors, (0, 1))
    warmed = set()

    def create_forward_fn(desc, warmup):
        family = (
            desc.num_active_loras,
            desc.uniform_token_count,
            desc.max_query_len,
            desc.num_ubatches,
            desc.model_input_variant,
        )

        def forward(_runtime_mode):
            if warmup:
                assert not device.capturing
                assert desc.num_tokens == 128
                assert family not in warmed
                warmed.add(family)
            else:
                assert device.capturing
                assert family in warmed

        return forward

    manager.capture(create_forward_fn)
    assert len(warmed) == 10
    assert len(manager.graphs) == 30


def test_active_warmup_failure_aborts_before_capture(monkeypatch):
    device = install_fake_device(monkeypatch)
    manager = profiling._CapturingGraphManager([128, 32])
    manager.ubatch_runner = Mock()
    manager._capture_descs[CUDAGraphMode.FULL] = expand_capture_variants(
        manager._capture_descs[CUDAGraphMode.FULL], (0, 1)
    )

    def create_forward_fn(desc, warmup):
        def forward(_runtime_mode):
            if desc.model_input_variant == 1:
                assert warmup and not device.capturing
                raise torch.OutOfMemoryError("active warmup")

        return forward

    with pytest.raises(torch.OutOfMemoryError, match="active warmup"):
        manager.capture(create_forward_fn)

    manager.ubatch_runner.abort_pending_run.assert_called_once()
    assert not manager._graphs_captured
    assert [(desc.num_tokens, desc.model_input_variant) for desc in manager.graphs] == [(128, 0)]
    assert not device.capturing


def test_failed_active_sample_releases_stock_graphs_and_restores_pools(monkeypatch):
    profiling._patch_module(monkeypatch)
    device = install_fake_device(monkeypatch)
    runner = profiling._make_profiling_runner(CUDAGraphMode.FULL)
    manager = profiling._CapturingGraphManager([128, 96, 64, 32, 1])
    runner.cudagraph_manager = manager
    manager._capture_descs[CUDAGraphMode.FULL] = expand_capture_variants(
        manager._capture_descs[CUDAGraphMode.FULL], (0, 1)
    )
    lifecycle = []
    monkeypatch.setattr(
        cudagraph_utils, "_new_cudagraph", lambda: profiling._RecordingGraph(lifecycle, "stock")
    )
    initial_count = cudagraph_utils.compilation_counter.num_cudagraph_captured

    def create_forward_fn(desc, warmup):
        def forward(_runtime_mode):
            if desc.model_input_variant == 1:
                assert warmup and not device.capturing
                assert len(manager.graphs) == 3
                raise torch.OutOfMemoryError("active warmup")

        return forward

    def capture_model(*, profile_only):
        assert profile_only
        assert manager.pool == profiling.THROWAWAY_POOL
        manager.capture(create_forward_fn)

    runner.capture_model = capture_model
    with pytest.raises(torch.OutOfMemoryError, match="active warmup"):
        cudagraph_utils.profile_cudagraph_memory(runner)

    assert lifecycle == ["reset-stock"] * 3
    assert not manager.graphs
    assert not manager.graph_capture_resources
    assert not manager._graphs_captured
    assert cudagraph_utils.compilation_counter.num_cudagraph_captured == initial_count
    assert manager.pool == profiling.GLOBAL_POOL
    assert cudagraph_utils.current_platform.get_global_graph_pool() == profiling.GLOBAL_POOL
    assert runner.events == ["init", "teardown"]
