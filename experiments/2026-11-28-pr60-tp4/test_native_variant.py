"""Exercise variant warmup, capture and replay with actual CUDA streams and graphs."""

import pytest


@pytest.mark.parametrize("sampled", [False, True], ids=["full", "profiling"])
def test_native_variant_capture_and_replay(sampled, tmp_path, monkeypatch):
    import torch

    from vllm.config import VllmConfig, set_current_vllm_config
    from vllm.config.compilation import CUDAGraphMode
    from vllm.distributed.parallel_state import (
        cleanup_dist_env_and_memory,
        init_distributed_environment,
        initialize_model_parallel,
    )
    from vllm.v1.worker.gpu.cudagraph_utils import CudaGraphManager
    from vllm.v1.worker.gpu.model_input_variant import (
        expand_capture_variants,
        select_input_variant,
    )

    assert torch.cuda.is_available(), "This exact-image check requires a real GPU"
    # The component container can be network-isolated; this group has just one rank.
    monkeypatch.setenv("GLOO_SOCKET_IFNAME", "lo")
    monkeypatch.setenv("NCCL_SOCKET_IFNAME", "lo")
    monkeypatch.setenv("VLLM_HOST_IP", "127.0.0.1")
    device = torch.device("cuda", 0)
    torch.cuda.set_device(device)
    config = VllmConfig()
    sizes = [1, 2, 4, 8]
    config.compilation_config.cudagraph_capture_sizes = sizes
    config.compilation_config.max_cudagraph_capture_size = max(sizes)
    config.scheduler_config.max_num_seqs = max(sizes)
    with set_current_vllm_config(config):
        try:
            init_distributed_environment(
                world_size=1, rank=0, local_rank=0, backend="nccl",
                distributed_init_method=(tmp_path / "rendezvous").as_uri(),
            )
            initialize_model_parallel(1, 1)
            manager = CudaGraphManager(config, device, CUDAGraphMode.FULL, decode_query_len=1)
            stock = manager._capture_descs[CUDAGraphMode.FULL]
            descriptors = expand_capture_variants(stock, (0, 1))
            manager._capture_descs[CUDAGraphMode.FULL] = descriptors
            manager._sample_full_descs = sampled
            inputs = {d: torch.zeros(d.num_tokens, device=device) for d in descriptors}
            outputs = {d: torch.empty_like(x) for d, x in inputs.items()}
            calls = {0: [], 1: []}

            def make_forward(desc, warmup):
                variant = desc.model_input_variant

                def forward(_mode):
                    capturing = torch.cuda.is_current_stream_capturing()
                    assert capturing != warmup
                    calls[variant].append(("capture" if capturing else "warmup", desc.num_tokens))
                    torch.add(inputs[desc], variant + 1, out=outputs[desc])

                return forward

            try:
                manager.capture(make_forward)
                captured_sizes = [8, 4, 1] if sampled else [8, 4, 2, 1]
                expected_calls = [("warmup", 8)] + [("capture", n) for n in captured_sizes]
                assert calls == {0: expected_calls, 1: expected_calls}
                assert len(manager.graphs) == 2 * len(captured_sizes)

                for generation in (11, 29):
                    for size in captured_sizes:
                        stock_desc = manager.dispatch(size, size, 1, 0)
                        assert stock_desc.cg_mode == CUDAGraphMode.FULL
                        for variant in (1, 0):
                            desc = select_input_variant(stock_desc, variant)
                            inputs[desc].fill_(generation)
                            outputs[desc].fill_(-1)
                            manager.run_fullgraph(desc)
                            assert outputs[desc].cpu().tolist() == [generation + variant + 1] * size
            finally:
                torch.cuda.synchronize()
                manager.reset_graphs()
        finally:
            cleanup_dist_env_and_memory()
