# Unmodified function extracted from PR8 c6342a1b servebench/behaviour.py.
def score_extra_body(extra_body: dict | None) -> dict:
    """Carry supported steering controls from generation to token-ID scoring.

    Chat-template and sampling options do not belong on the teacher-forcing request;
    parity for arbitrary extra-body fields is outside this steering-only projection.
    Keep both supported locations without resolving conflicts, inserting defaults, or
    coercing values: the server's existing steering validation remains authoritative.
    This request identity does not prove engine admission or profile/vector identity.
    """
    if extra_body is None:
        return {}
    if not isinstance(extra_body, dict):
        raise ValueError("extra_body must be a JSON object")
    keys = ("steering_profile", "steering_scale")
    out = {key: extra_body[key] for key in keys if key in extra_body}
    original = extra_body.get("vllm_xargs")
    if original is not None:
        if not isinstance(original, dict):
            raise ValueError("vllm_xargs must be a JSON object or null")
        steering = {key: original[key] for key in keys if key in original}
        if steering:
            out["vllm_xargs"] = steering
    return out
