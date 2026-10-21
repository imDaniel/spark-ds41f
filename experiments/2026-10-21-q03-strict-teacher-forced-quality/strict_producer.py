"""Opt-in source candidate for Q03 only. No network implementation or automatic use.

The caller supplies an already-authorized transport for existing /v1/completions.
This does not change PR8 score(), CLI defaults, model, native code or runtime state.
Returns raw per-record evidence, not forged identity/admission receipts or a PASS.
"""
import copy
import uuid

from scoring_contract import check_frozen, require, same, strict_request


def collect_records(frozen, post, *, timeout=1800.0, salt_factory=None):
    """Retain one result/error per frozen ID; never skip error/output intersections."""
    expected = check_frozen(frozen)  # entire corpus before any transport call
    require(callable(post), 'an explicit transport is required; no default endpoint')
    require(type(timeout) in (int, float) and 0 < timeout < float('inf'), 'invalid transport timeout')
    salt_factory = salt_factory or (lambda: uuid.uuid4().hex)
    requests = [(sid, strict_request(frozen, sid, salt_factory())) for sid in expected]
    require(len({body['cache_salt'] for _, body in requests}) == len(requests), 'reused caller salt')
    records = []
    for sid, body in requests:
        record = {'id': sid, 'request': copy.deepcopy(body)}
        try:
            # A recorder supplied by CPU fixtures is the only transport used here.
            record['response'] = post('/v1/completions', body, timeout)
            require(same(body, record['request']), 'transport mutated request after capture')
        except Exception as error:
            record['error'] = f'{type(error).__name__}: {error}'[:300]
        records.append(record)
    return records
