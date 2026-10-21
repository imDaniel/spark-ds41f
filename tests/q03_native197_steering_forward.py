# Exact native197 protocol method; source-only stdlib fixture, not a runtime implementation.
# Native protocol SHA256: ac2836423b9d50e7b19c20e90803cd9f27bf6941078c10b0894b4f1c54edd688
from typing import Any

def _forward_steering_fields(cls, data: Any):
    if not isinstance(data, dict):
        return data
    original = data.get("vllm_xargs")
    if original is not None and not isinstance(original, dict):
        return data
    keys = ("steering_profile", "steering_scale")
    if not any(key in data or key in (original or {}) for key in keys):
        return data
    extra = dict(original or {})
    for key in keys:
        value = data.get(key)
        if value is not None:
            if extra.get(key) is not None and extra[key] != value:
                raise ValueError(f"Conflicting {key} in request and vllm_xargs")
            extra[key] = value
        elif extra.get(key) is None:
            extra.pop(key, None)
    return {**data, "vllm_xargs": extra or None}
