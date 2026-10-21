"""Offline Q03 scoring evidence check; not a server, quality gate, or live scorer.

Reuses PR8's unchanged steering projection. All provenance is declared evidence,
not proof that the engine applied it. No imports of native/Torch, IO or networking.
"""
import hashlib
import json
import math
import re

from reference.pr8_projection import score_extra_body

SCOPE = ('Recorded complete same-input teacher-forced evidence only; runtime '
         'state/application, numerical equivalence and quality remain unproven. '
         'Top-K/given-token equality or truncated KL is not full-vocabulary equality.')
IDENTITY_KEYS = {'checkpoint_sha256', 'tokenizer_assets_sha256', 'tokenizer_implementation',
                 'chat_template_sha256', 'encoding_options_sha256', 'native_commit',
                 'image_digest', 'state'}


class InvalidEvidence(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise InvalidEvidence(reason)


def finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def same(a, b):
    """JSON equality without bool==1 or absent==null; retain numeric 0==0.0."""
    if type(a) is bool or type(b) is bool:
        return type(a) is type(b) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if type(a) in (int, float) and type(b) in (int, float):
        return finite(a) and finite(b) and a == b
    return type(a) is type(b) and a == b


def token_ids(value):
    return isinstance(value, list) and bool(value) and all(type(t) is int and t >= 0 for t in value)


def corpus_digest(sequences):
    """Hash every expected ID and full token sequence, never a successful subset."""
    return hashlib.sha256(json.dumps(sequences, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def load_json(text):
    """Reject duplicate members and non-JSON NaN/Infinity before information is lost."""
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate JSON member: ' + key)
            result[key] = value
        return result
    def bad(value):
        raise InvalidEvidence('nonfinite JSON constant: ' + value)
    return json.loads(text, object_pairs_hook=pairs, parse_constant=bad)


def check_frozen(frozen):
    require(isinstance(frozen, dict), 'frozen corpus must be an object')
    require(type(frozen.get('context_limit')) is int and frozen['context_limit'] > 0,
            'context_limit must be a positive integer')
    require(type(frozen.get('top_k')) is int and frozen['top_k'] > 0,
            'this bounded top-K contract requires positive top_k; not full vocabulary')
    require(isinstance(frozen.get('model'), str) and bool(frozen['model']), 'missing fixed model')
    identity = frozen.get('identity')
    require(isinstance(identity, dict) and IDENTITY_KEYS <= identity.keys(), 'missing frozen identity')
    for key in IDENTITY_KEYS - {'state'}:
        require(isinstance(identity[key], str) and bool(identity[key]), 'empty identity: ' + key)
    for key in ('checkpoint_sha256', 'tokenizer_assets_sha256', 'chat_template_sha256',
                'encoding_options_sha256'):
        require(re.fullmatch('[0-9a-f]{64}', identity[key]), 'invalid identity digest: ' + key)
    require(re.fullmatch('[0-9a-f]{40}', identity['native_commit']), 'invalid native commit')
    require(re.fullmatch('sha256:[0-9a-f]{64}', identity['image_digest']), 'invalid image digest')
    state = identity['state']
    require(isinstance(state, dict) and {'startup_profile', 'profile_artifact_sha256',
                                       'profile_identity', 'effective_scale_fp32_bits'} <= state.keys(),
            'missing declared state/profile/FP32 identity')
    require(isinstance(state['effective_scale_fp32_bits'], str) and
            re.fullmatch('[0-9a-f]{8}', state['effective_scale_fp32_bits']), 'invalid declared FP32 bits')
    for key in ('profile_artifact_sha256', 'profile_identity'):
        require(state[key] is None or (isinstance(state[key], str) and
                re.fullmatch('[0-9a-f]{64}', state[key])), 'invalid profile identity: ' + key)
    require(state['startup_profile'] is None or
            (isinstance(state['startup_profile'], str) and bool(state['startup_profile'])),
            'invalid declared startup profile')
    require(all(state[key] is None for key in ('profile_artifact_sha256', 'profile_identity'))
            if state['startup_profile'] is None else
            all(state[key] is not None for key in ('profile_artifact_sha256', 'profile_identity')),
            'incomplete declared loaded/unloaded profile identity')
    require(isinstance(frozen.get('raw_corpus_sha256'), str)
            and re.fullmatch('[0-9a-f]{64}', frozen['raw_corpus_sha256']), 'missing raw corpus hash')
    require('generation_extra_body' in frozen, 'missing generation request metadata')
    score_extra_body(frozen['generation_extra_body'])  # exact PR8 envelope validation
    sequences = frozen.get('sequences')
    require(isinstance(sequences, list) and bool(sequences), 'empty frozen corpus')
    index = {}
    for seq in sequences:
        require(isinstance(seq, dict) and 'error' not in seq, 'frozen corpus contains an error/malformed row')
        sid = seq.get('id')
        require(type(sid) is int and sid >= 0 and sid not in index, 'duplicate/malformed frozen ID')
        require(token_ids(seq.get('prompt_ids')) and token_ids(seq.get('output_ids')),
                'frozen prefix/output must be nonempty integer IDs')
        require(seq.get('finish_reason') == 'length', 'frozen fixed-length generation did not finish length')
        require(len(seq['prompt_ids']) + len(seq['output_ids']) + 1 <= frozen['context_limit'],
                'frozen sequence plus one scoring token exceeds context; never truncate')
        index[sid] = seq
    require(frozen.get('corpus_sha256') == corpus_digest(sequences), 'frozen parsed corpus hash differs')
    return index


def strict_request(frozen, sequence_id, cache_salt):
    """Opt-in request candidate: original PR8 semantics plus explicit ID echo.

    Returns data only. It does not send anything or change existing score() defaults.
    Caller must provide a fresh salt per request and independently observed identity.
    """
    index = check_frozen(frozen)
    require(type(sequence_id) is int and sequence_id in index, 'unknown frozen sequence ID')
    require(isinstance(cache_salt, str) and bool(cache_salt), 'missing fresh caller salt')
    seq = index[sequence_id]
    return {'model': frozen['model'], 'prompt': seq['prompt_ids'] + seq['output_ids'],
            'max_tokens': 1, 'temperature': 0, 'prompt_logprobs': frozen['top_k'],
            'cache_salt': cache_salt, 'return_token_ids': True,
            **score_extra_body(frozen['generation_extra_body'])}


def _one(frozen, seq, record):
    require('error' not in record, 'record contains an error, even if null/empty')
    request = record.get('request')
    require(isinstance(request, dict), 'missing captured scoring request')
    require(same(request, strict_request(frozen, seq['id'], request.get('cache_salt'))),
            'captured scoring request differs: IDs/model/steering/echo/one-token/top-K semantics')
    response = record.get('response')
    require(isinstance(response, dict) and 'error' not in response, 'missing/error response')
    require(response.get('model') == frozen['model'], 'response model differs')
    choices = response.get('choices')
    require(isinstance(choices, list) and len(choices) == 1, 'need exactly one choice')
    choice = choices[0]
    require(isinstance(choice, dict) and 'error' not in choice, 'malformed/error choice')
    require(type(choice.get('index')) is int and choice['index'] == 0, 'wrong choice index')
    require(choice.get('finish_reason') in ('length', 'stop'), 'scoring choice did not finish successfully')
    tokens = request['prompt']
    require(token_ids(choice.get('prompt_token_ids')) and choice['prompt_token_ids'] == tokens,
            'choice-level actual prompt_token_ids must equal full frozen prefix+output')
    rows = choice.get('prompt_logprobs')
    require(isinstance(rows, list) and len(rows) == len(tokens), 'prompt_logprobs coverage mismatch')
    require(rows[0] is None, 'native197 first prompt token has no preceding-token logprob')
    normalized = []
    censored_positions = []
    for position, row in enumerate(rows[1:], 1):
        require(isinstance(row, dict) and bool(row), f'position {position}: missing row')
        values = []
        for key, entry in row.items():
            require(isinstance(key, str) and re.fullmatch('0|[1-9][0-9]*', key),
                    f'position {position}: noncanonical token-ID key')
            require(isinstance(entry, dict), f'position {position}: malformed logprob')
            lp, rank = entry.get('logprob'), entry.get('rank')
            require(finite(lp), f'position {position}: nonfinite logprob')
            require(lp <= 0, f'position {position}: positive logprob is not a log-probability')
            if lp == -9999:
                censored_positions.append({'position': position, 'token_id': int(key)})
            require(type(rank) is int and rank >= 1, f'position {position}: invalid rank')
            values.append((int(key), lp, rank))
        given = row.get(str(tokens[position]))
        require(given is not None, f'position {position}: missing given-token logprob')
        top = sorted((value for value in values if value[2] <= frozen['top_k']), key=lambda x: x[2])
        require(len(top) == frozen['top_k'] and
                all(value[2] == rank for rank, value in enumerate(top, 1)),
                f'position {position}: incomplete/duplicate top-K ranks')
        require(all(token == tokens[position] or rank <= frozen['top_k'] for token, _, rank in values),
                f'position {position}: unexpected non-top-K token')
        normalized.append((given, top))
    start = len(seq['prompt_ids'])
    output_rows = normalized[start - 1:]
    require(len(output_rows) == len(seq['output_ids']), 'output score alignment mismatch')
    # Do not relabel requested prefix metadata as server echo. Preserve both.
    return {'id': seq['id'], 'prompt_ids': list(seq['prompt_ids']),
            'censored_positions': censored_positions,
            'returned_prompt_token_ids': list(choice['prompt_token_ids']),
            'tokens': list(seq['output_ids']),
            'logprob': [given['logprob'] for given, _ in output_rows],
            'rank': [given['rank'] for given, _ in output_rows],
            'top_ids': [[token for token, _, _ in top] for _, top in output_rows],
            'top_logprobs': [[lp for _, lp, _ in top] for _, top in output_rows]}


def validate(frozen, evidence):
    """No intersection fallback. On any defect no usable score collection is returned."""
    result = {'status': 'INVALID_RECORDED_SCORING', 'scope': SCOPE, 'quality_pass': False,
              'reasons': [], 'scores': []}
    try:
        expected = check_frozen(frozen)
        require(isinstance(evidence, dict), 'missing evidence object')
        for key in ('generation_identity', 'scoring_identity'):
            require(same(evidence.get(key), frozen['identity']), key + ' differs/missing')
        for key in ('generation_extra_body', 'raw_corpus_sha256', 'corpus_sha256'):
            require(key in evidence and same(evidence[key], frozen[key]), key + ' differs/missing')
        records = evidence.get('records')
        require(isinstance(records, list), 'missing records')
        actual = {}
        salts = set()
        for record in records:
            require(isinstance(record, dict), 'malformed record')
            sid = record.get('id')
            require(type(sid) is int and sid in expected and sid not in actual, 'duplicate/unexpected/malformed record ID')
            actual[sid] = record
        require(actual.keys() == expected.keys(), 'record coverage differs from independent frozen corpus')
        scores = []
        for sid, seq in expected.items():
            scores.append(_one(frozen, seq, actual[sid]))
            salt = actual[sid]['request']['cache_salt']
            require(salt not in salts, 'reused caller cache_salt')
            salts.add(salt)
        if any(score['censored_positions'] for score in scores):
            result.update(status='UNRESOLVED_LOGPROB_CENSORING',
                          reasons=['native197 may replace -inf with -9999 in place; '
                                   'finite transport values do not resolve raw-score identity'],
                          censored_positions=[{'id': score['id'], **position}
                              for score in scores for position in score['censored_positions']])
            return result
        result.update(status='COMPLETE_RECORDED_SCORING_ONLY', scores=scores,
                      sequences=len(scores), output_positions=sum(len(s['tokens']) for s in scores))
    except (InvalidEvidence, ValueError, TypeError, KeyError) as error:
        result['reasons'].append(str(error))
    return result
