"""Idle-server diagnostics through vLLM's explicit worker extension interface.

This module is mounted only by qualification recipes. It does not wrap request
execution. The fixed-grid call temporarily substitutes dummy input preparation
to compare stock/active computation at identical shapes without text or draft
acceptance differences. Real-request traces measure admission/row preparation.
"""

import json
import math

import torch


def _is_dummy_batch(batch, req_states):
    marker = getattr(batch, "is_dummy", None)
    if marker is not None:
        return marker is True
    # The original 172-patch InputBatch has no marker. Its make_dummy creates
    # req_<index>_<uuid> IDs without registering them in RequestState. Check both
    # parts of that contract; an ordinary registered request must be rejected.
    return bool(batch.req_ids) and all(
        req.startswith(f"req_{index}_")
        and len(req) > len(f"req_{index}_")
        and req not in req_states.req_id_to_index
        for index, req in enumerate(batch.req_ids)
    )


class _DummyState:
    def __init__(self, state, variant):
        self._state = state
        self._variant = variant

    def __getattr__(self, name):
        return getattr(self._state, name)

    def get_model_input_variant(self, req_ids, *, dummy=False):
        if not dummy:
            raise RuntimeError("Qualification grid requires an idle dummy batch")
        return self._variant

    def prepare_inputs(self, batch, req_states):
        if not _is_dummy_batch(batch, req_states):
            raise RuntimeError("Qualification grid cannot process real requests")
        result = self._state.prepare_inputs(batch, req_states)
        runtime = getattr(self._state, "steering", None)
        if self._variant:
            result.update(
                steering_active=True,
                steering_scales=runtime.row_scales[: batch.num_tokens_after_padding],
                steering_dots=runtime.dots,
                steering_scratch=runtime.scratch,
            )
        return result


class QualificationWorker:
    def qualification_state(self):
        runner = self.model_runner
        manager = getattr(runner, "cudagraph_manager", None)
        runtime = getattr(runner.model_state, "steering", None)
        graph_maps = {}
        if manager is not None:
            for name, value in vars(manager).items():
                if isinstance(value, dict):
                    graph_maps[name] = [str(key) for key in value]
        stats = torch.cuda.memory_stats()
        return {
            "rank": self.rank,
            "allocated": torch.cuda.memory_allocated(),
            "reserved": torch.cuda.memory_reserved(),
            "peak_allocated": torch.cuda.max_memory_allocated(),
            "peak_reserved": torch.cuda.max_memory_reserved(),
            "allocation_retries": stats.get("num_alloc_retries"),
            "ooms": stats.get("num_ooms"),
            "graph_maps": graph_maps,
            "profile": None if runtime is None else runtime.profile.profile_id,
            "controls": None
            if runtime is None
            else {
                name: None
                if getattr(runtime, name, None) is None
                else {
                    "shape": list(getattr(runtime, name, None).shape),
                    "bytes": getattr(runtime, name, None).numel()
                    * getattr(runtime, name, None).element_size(),
                    "pointer": getattr(runtime, name, None).data_ptr(),
                }
                for name in ("request_scales", "row_scales", "dots", "scratch")
            },
        }

    def qualification_fixed_grid(self, text):
        spec = json.loads(text)
        scale = float(spec.pop("qualification_scale", 0))
        if not math.isfinite(scale) or not 0 <= scale <= 4:
            raise ValueError("Invalid qualification scale")
        runner = self.model_runner
        original = runner.model_state
        runtime = getattr(original, "steering", None)
        if scale and runtime is None:
            raise ValueError("Nonzero grid needs a loaded profile")
        before = self.qualification_state()
        if runtime is not None:
            runtime.row_scales.fill_(scale)
        runner.model_state = _DummyState(original, int(scale != 0))
        try:
            result = runner.engine_grid(spec)
            torch.cuda.synchronize()
        finally:
            runner.model_state = original
            if runtime is not None:
                runtime.row_scales.zero_()
        result.update(
            rank=self.rank,
            qualification_scale=scale,
            dummy_row_preparation_excluded=runtime is None
            or hasattr(runtime, "model_inputs"),
            before=before,
            after=self.qualification_state(),
        )
        return result
