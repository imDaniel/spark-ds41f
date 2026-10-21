"""Model behaviour: what a build computes, judged without timing.

- **Generation.** Every prompt once, greedily, at a fixed concurrency, recording each
  request's token ids, with the server's speculative-decoding counters per draft position
  read before and after. On a batch-invariant server greedy decoding makes the text, and
  with it the acceptance, a property of the build rather than of the run.
- **Teacher-forced scoring.** Token sequences (by default a base run's prompts and
  responses) are sent as prompts with ``prompt_logprobs``. For each response position it
  records the given token's log-probability and rank, plus the top K. Every build scores the
  same tokens, so two builds compare position by position: KL divergence, top-1 agreement,
  perplexity. A build scoring its own greedy text should rank every given token first,
  which checks that the prefill path agrees with the decode path that wrote it.

KL divergence is estimated from each position's top K, the rest of the vocabulary being
one bucket; a token outside the candidate's top K is given the candidate's K-th
log-probability, an upper bound, so the estimate errs low.
"""

import concurrent.futures
import hashlib
import json
import math
import time
import urllib.request
import uuid

from . import prom, stats

SCHEMA = "servebench-model/1"
ACCEPTED_PER_POSITION = "vllm:spec_decode_num_accepted_tokens_per_pos"
DRAFTED_PER_POSITION = "vllm:spec_decode_num_draft_tokens_per_pos"


def _post(url: str, body: dict, timeout: float) -> dict:
    request = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _in_parallel(items: list, work, concurrency: int) -> list:
    """``work`` over ``items`` with ``concurrency`` in flight, each starting as one ends;
    results in item order."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        return list(pool.map(work, items))


def _positions(base_url: str) -> dict | None:
    """Per-position counters; Prometheus clients may expose them with a ``_total`` suffix."""
    text = prom.fetch(base_url)
    if text is None:
        return None
    out = {}
    for key, name in (("accepted", ACCEPTED_PER_POSITION), ("drafted", DRAFTED_PER_POSITION)):
        out[key] = prom.by_label(text, name + "_total", "position") or prom.by_label(text, name, "position")
    return out


def acceptance(start: dict | None, end: dict | None, positions_start: dict | None,
               positions_end: dict | None) -> dict:
    """Counter deltas as acceptance: mean acceptance length (tokens per verification
    step), draft tokens verified per step, and per draft position the acceptance rate and
    how often that position was verified at all."""
    if not start or not end:
        return {}
    delta = {key: end[key] - start[key] for key in end if key in start}
    drafts = delta.get("drafts", 0)
    out = {key: delta.get(key) for key in ("drafts", "draft_tokens", "accepted_tokens",
                                           "generation_tokens", "steps")}
    if drafts:
        out["acceptance_length"] = 1 + delta.get("accepted_tokens", 0) / drafts
        out["verified_per_draft"] = delta.get("draft_tokens", 0) / drafts
    if delta.get("draft_tokens"):
        out["accepted_per_verified"] = delta.get("accepted_tokens", 0) / delta["draft_tokens"]
    if positions_start and positions_end:
        keys = sorted(set(positions_end["accepted"]) | set(positions_end["drafted"]), key=int)
        rows = []
        for key in keys:
            drafted = positions_end["drafted"].get(key, 0) - positions_start["drafted"].get(key, 0)
            accepted = positions_end["accepted"].get(key, 0) - positions_start["accepted"].get(key, 0)
            rows.append({"position": int(key), "drafted": drafted, "accepted": accepted,
                         "rate": accepted / drafted if drafted else None,
                         "coverage": drafted / drafts if drafts else None})
        out["per_position"] = rows
    return out


def generate(base_url: str, model: str, prompt_set: list[dict], *, concurrency: int = 16,
             max_tokens: int = 256, fixed_length: bool = False, extra_body: dict | None = None,
             timeout: float = 1800.0, require_idle: bool = True) -> dict:
    """Every prompt once, greedily; token ids per request and acceptance over the pass."""
    base = base_url.rstrip("/")
    if require_idle:
        prom.require_idle(base, "a model-behaviour run")

    def one(item: tuple[int, dict]) -> dict:
        index, prompt = item
        body = {"model": model, "messages": [{"role": "user", "content": prompt["text"]}],
                "max_tokens": max_tokens, "temperature": 0, "return_token_ids": True,
                "cache_salt": uuid.uuid4().hex}
        if fixed_length:
            body["ignore_eos"] = True
            body["min_tokens"] = max_tokens
        body.update(extra_body or {})
        try:
            answer = _post(base + "/v1/chat/completions", body, timeout)
        except Exception as error:  # noqa: BLE001 - recorded per request
            return {"id": index, "error": f"{type(error).__name__}: {error}"[:300]}
        choice = answer["choices"][0]
        record = {"id": index, "prompt_ids": answer.get("prompt_token_ids"),
                  "output_ids": choice.get("token_ids"), "finish_reason": choice.get("finish_reason")}
        if record["prompt_ids"] is None or record["output_ids"] is None:
            record["error"] = "no token ids in the reply (the server needs return_token_ids)"
        return record

    counters, positions = prom.scrape(base, prom.VLLM), _positions(base)
    began = time.monotonic()
    sequences = _in_parallel(list(enumerate(prompt_set)), one, concurrency)
    seconds = time.monotonic() - began
    return {"seconds": round(seconds, 2),
            "acceptance": acceptance(counters, prom.scrape(base, prom.VLLM), positions, _positions(base)),
            "sequences": sequences}


def corpus_digest(sequences: list[dict]) -> str:
    usable = sorted((s for s in sequences if not s.get("error")), key=lambda s: s["id"])
    return hashlib.sha256(json.dumps([[s["prompt_ids"], s["output_ids"]] for s in usable]).encode()).hexdigest()


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


def score(base_url: str, model: str, sequences: list[dict], *, top: int = 20,
          concurrency: int = 8, timeout: float = 1800.0,
          extra_body: dict | None = None) -> list[dict]:
    """Teacher-forced log-probabilities with generation's supported steering controls."""
    base = base_url.rstrip("/")
    extra = score_extra_body(extra_body)

    def one(sequence: dict) -> dict:
        start = len(sequence["prompt_ids"])
        tokens = sequence["prompt_ids"] + sequence["output_ids"]
        body = {"model": model, "prompt": tokens, "max_tokens": 1, "temperature": 0,
                "prompt_logprobs": top, "cache_salt": uuid.uuid4().hex}
        body.update(extra)
        try:
            rows = _post(base + "/v1/completions", body, timeout)["choices"][0]["prompt_logprobs"]
            if not isinstance(rows, list) or len(rows) != len(tokens):
                raise ValueError("prompt_logprobs must contain one row per input token")
            # The prefix is request metadata, not a verified server token-ID echo.
            scored = {"id": sequence["id"], "prompt_ids": list(sequence["prompt_ids"]),
                      "tokens": sequence["output_ids"], "logprob": [], "rank": [],
                      "top_ids": [], "top_logprobs": []}
            for position in range(start, len(tokens)):
                entry = rows[position] or {}
                given = entry.get(str(tokens[position]))
                if given is None:
                    return {"id": sequence["id"], "error": f"position {position}: no log-probability for the given token"}
                ranked = sorted(((int(key), value) for key, value in entry.items()
                                 if value.get("rank") is not None and value["rank"] <= top),
                                key=lambda pair: pair[1]["rank"])
                scored["logprob"].append(given["logprob"])
                scored["rank"].append(given.get("rank"))
                scored["top_ids"].append([key for key, _ in ranked])
                scored["top_logprobs"].append([value["logprob"] for _, value in ranked])
        except Exception as error:  # noqa: BLE001 - recorded per sequence
            return {"id": sequence["id"], "error": f"{type(error).__name__}: {error}"[:300]}
        return scored

    usable = [s for s in sequences if not s.get("error") and s.get("output_ids")]
    return _in_parallel(usable, one, concurrency)


def self_agreement(scores: list[dict]) -> dict:
    """How often the scored token is the top-ranked one, and the corpus perplexity. On a
    build's own greedy text the first should be 1.0."""
    ranks = [r for s in scores if not s.get("error") for r in s["rank"]]
    logprobs = [lp for s in scores if not s.get("error") for lp in s["logprob"]]
    if not ranks:
        return {}
    return {"positions": len(ranks), "rank1_fraction": sum(r == 1 for r in ranks) / len(ranks),
            "perplexity": math.exp(-sum(logprobs) / len(logprobs))}


def kl_divergence(p_logprobs: dict[int, float], q_logprobs: dict[int, float], q_floor: float) -> float:
    """KL(P || Q) over P's listed tokens plus one bucket for the rest of the vocabulary;
    a token Q does not list gets ``q_floor`` (Q's lowest listed log-probability)."""
    total = p_mass = q_mass = 0.0
    for token, p_lp in p_logprobs.items():
        q_lp = q_logprobs.get(token, q_floor)
        p = math.exp(p_lp)
        total += p * (p_lp - q_lp)
        p_mass += p
        q_mass += math.exp(q_lp)
    p_rest = 1.0 - p_mass
    if p_rest > 1e-9:
        total += p_rest * math.log(p_rest / max(1.0 - q_mass, 1e-9))
    return max(total, 0.0)


def _alignment(incomplete: list[str], incomparable: list[str]) -> dict:
    """Record completeness is not a quality or noninferiority verdict."""
    return {"status": "incomplete" if incomplete else "incomparable" if incomparable else "complete",
            "reasons": incomplete + incomparable}


def _token_ids(value, *, allow_empty: bool = False) -> bool:
    return (isinstance(value, list) and (allow_empty or bool(value))
            and all(type(t) is int and t >= 0 for t in value))


def _record_index(records: list[dict], label: str, incomplete: list[str],
                  expected_count: int | None = None, expected_ids: set[int] | None = None) -> dict:
    """Never silently overwrite duplicates or count failed records as coverage."""
    by_id, seen, duplicates = {}, set(), set()
    for row in records:
        index = row.get("id") if isinstance(row, dict) else None
        if type(index) is not int or index < 0:
            incomplete.append(f"{label}: missing or invalid record id")
            continue
        if index in seen:
            duplicates.add(index)
        seen.add(index)
        if row.get("error"):
            incomplete.append(f"{label}: id {index} failed: {row['error']}")
        else:
            by_id[index] = row
    for index in sorted(duplicates):
        incomplete.append(f"{label}: duplicate id {index}")
        by_id.pop(index, None)
    if expected_count is not None:
        if type(expected_count) is not int or expected_count <= 0:
            incomplete.append(f"{label}: invalid expected record count")
        else:
            expected_ids = set(range(expected_count))
    if expected_ids is not None and seen != expected_ids:
        incomplete.append(f"{label}: missing ids {sorted(expected_ids - seen)}; "
                          f"unexpected ids {sorted(seen - expected_ids)}")
    if not records and expected_ids != set():
        incomplete.append(f"{label}: no records")
    return by_id


def _score_rows_valid(row: dict) -> bool:
    tokens = row.get("tokens")
    if not _token_ids(tokens, allow_empty=True):
        return False
    keys = ("logprob", "rank", "top_ids", "top_logprobs")
    if any(not isinstance(row.get(k), list) or len(row[k]) != len(tokens) for k in keys):
        return False
    finite = lambda x: type(x) in (int, float) and math.isfinite(x)
    if not all(finite(x) for x in row["logprob"]):
        return False
    if not all(type(x) is int and x > 0 for x in row["rank"]):
        return False
    for ids, lps in zip(row["top_ids"], row["top_logprobs"]):
        if (not isinstance(ids, list) or not isinstance(lps, list) or len(ids) != len(lps)
                or not all(type(x) is int and x >= 0 for x in ids)
                or len(set(ids)) != len(ids) or not all(finite(x) for x in lps)):
            return False
    return True


def compare_scores(base: list[dict], candidate: list[dict], *,
                   base_count: int | None = None, candidate_count: int | None = None,
                   base_ids: set[int] | None = None, candidate_ids: set[int] | None = None) -> dict:
    """Descriptive metrics over aligned rows, with complete-corpus validity separate."""
    incomplete, incomparable = [], []
    by_id = _record_index(base, "base scores", incomplete, base_count, base_ids)
    candidates = _record_index(candidate, "candidate scores", incomplete, candidate_count, candidate_ids)
    if by_id.keys() != candidates.keys():
        incomplete.append("score id sets differ")
    for label, rows in (("base", by_id), ("candidate", candidates)):
        for index, row in list(rows.items()):
            if not _score_rows_valid(row):
                incomplete.append(f"{label} scores: id {index} has malformed or incomplete positions")
                del rows[index]
            elif not row["tokens"]:
                incomparable.append(f"{label} scores: id {index} has no scorable output positions")
                del rows[index]
            elif not _token_ids(row.get("prompt_ids")):
                incomparable.append(f"{label} scores: id {index} input prefix is unavailable")
    kls, per_sequence, deltas = [], [], []
    identical = top1 = 0
    base_sum = candidate_sum = 0.0
    skipped = len(candidate) - len(candidates)
    for index, cand in candidates.items():
        other = by_id.get(index)
        if other is None:
            skipped += 1
            continue
        if other["tokens"] != cand["tokens"]:
            incomparable.append(f"scores: id {index} output token corpus differs")
            skipped += 1
            continue
        if other.get("prompt_ids") != cand.get("prompt_ids"):
            incomparable.append(f"scores: id {index} input prefix differs")
            skipped += 1
            continue
        sequence_kls = []
        for i, token in enumerate(cand["tokens"]):
            b_ids, b_lps, c_ids, c_lps = (other["top_ids"][i], other["top_logprobs"][i],
                                          cand["top_ids"][i], cand["top_logprobs"][i])
            same = b_ids == c_ids and b_lps == c_lps and other["logprob"][i] == cand["logprob"][i]
            identical += same
            top1 += bool(b_ids and c_ids and b_ids[0] == c_ids[0])
            if same:
                kl = 0.0
            else:
                p = dict(zip(b_ids, b_lps))
                p.setdefault(token, other["logprob"][i])
                q = dict(zip(c_ids, c_lps))
                q.setdefault(token, cand["logprob"][i])
                kl = kl_divergence(p, q, min(c_lps) if c_lps else cand["logprob"][i])
            sequence_kls.append(kl)
            deltas.append(cand["logprob"][i] - other["logprob"][i])
            base_sum += other["logprob"][i]
            candidate_sum += cand["logprob"][i]
        if sequence_kls:
            kls.extend(sequence_kls)
            per_sequence.append(sum(sequence_kls) / len(sequence_kls))
    positions = len(kls)
    if not positions:
        incomparable.append("no scorable output positions")
        return {"sequences": 0, "skipped": skipped, "positions": 0,
                **_alignment(incomplete, incomparable)}
    base_ppl, cand_ppl = math.exp(-base_sum / positions), math.exp(-candidate_sum / positions)
    return {**_alignment(incomplete, incomparable),
            "sequences": len(per_sequence), "skipped": skipped, "positions": positions,
            "identical_fraction": identical / positions, "top1_agreement": top1 / positions,
            "kl": {"mean": sum(kls) / positions, **stats.percentiles(kls, (99,)), "max": max(kls),
                   "per_sequence": stats.describe(per_sequence)},
            "logprob_delta": {"mean": sum(deltas) / positions,
                              "mean_abs": sum(abs(d) for d in deltas) / positions,
                              "max_abs": max(abs(d) for d in deltas)},
            "perplexity": {"base": base_ppl, "candidate": cand_ppl,
                           "change_pct": 100.0 * (cand_ppl / base_ppl - 1.0)}}


def compare_generation(base: dict, candidate: dict, *, base_workload: dict | None = None,
                       candidate_workload: dict | None = None) -> dict:
    """Token equality on matching inputs, retaining incomplete/incomparable evidence."""
    incomplete, incomparable = [], []
    indexes = []
    for label, run, workload in (("base", base, base_workload),
                                  ("candidate", candidate, candidate_workload)):
        workload = workload or {}
        rows = _record_index(run.get("sequences") or [], label, incomplete, workload.get("prompt_count"))
        for index, row in list(rows.items()):
            if not _token_ids(row.get("prompt_ids")) or not _token_ids(row.get("output_ids"), allow_empty=True):
                incomplete.append(f"{label}: id {index} has missing or malformed token ids")
                del rows[index]
            elif workload.get("fixed_length") and (len(row["output_ids"]) != workload.get("max_tokens")
                                                   or row.get("finish_reason") != "length"):
                incomplete.append(f"{label}: id {index} did not complete the requested fixed length")
            elif not row["output_ids"]:
                incomparable.append(f"{label}: id {index} has no output tokens to compare")
        indexes.append(rows)
    by_id, candidates = indexes
    if by_id.keys() != candidates.keys():
        incomplete.append("generation id sets differ")
    identical, divergence = 0, []
    compared = 0
    for index, cand in candidates.items():
        other = by_id.get(index)
        if other is None:
            continue
        if other["prompt_ids"] != cand["prompt_ids"]:
            incomparable.append(f"generation: id {index} input prefix differs")
            continue
        compared += 1
        a, b = other["output_ids"], cand["output_ids"]
        if a == b:
            identical += 1
            continue
        divergence.append(next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b))))
    acceptance_rows = []
    for key in ("acceptance_length", "verified_per_draft", "accepted_per_verified"):
        x, y = base["acceptance"].get(key), candidate["acceptance"].get(key)
        if x and y:
            acceptance_rows.append({"metric": key, "base": x, "candidate": y,
                                    "change_pct": 100.0 * (y / x - 1.0)})
    rates = []
    base_positions = {row["position"]: row for row in base["acceptance"].get("per_position", [])}
    for row in candidate["acceptance"].get("per_position", []):
        other = base_positions.get(row["position"])
        if other and other.get("rate") is not None and row.get("rate") is not None:
            rates.append({"position": row["position"], "base": other["rate"], "candidate": row["rate"]})
    return {**_alignment(incomplete, incomparable), "compared": compared, "identical": identical,
            "divergence": stats.percentiles(divergence, (10, 50, 90)) if divergence else {},
            "acceptance": acceptance_rows, "per_position": rates}


WORKLOAD_KEYS = ("prompts_sha256", "prompt_count", "concurrency", "max_tokens", "fixed_length", "top",
                 "extra_body", "score_extra_body")


def _score_inputs(run: dict, sources: tuple[dict, dict]) -> tuple[list[dict], list[dict] | None]:
    """Recover old score prefixes only from a complete, matching frozen corpus.

    Work on copies: historical/raw records and metric values remain untouched.
    Return the whole corpus so empty outputs are not mistaken for missing scores.
    """
    scores = [dict(row) if isinstance(row, dict) else row for row in (run.get("scores") or [])]
    digest = run.get("corpus", {}).get("sha256")
    for source in sources:
        sequences = source["generation"].get("sequences") or []
        count = source["workload"].get("prompt_count")
        incomplete = []
        by_id = _record_index(sequences, "corpus", incomplete, count)
        if (count is None or incomplete
                or any(not _token_ids(row.get("prompt_ids"))
                       or not _token_ids(row.get("output_ids"), allow_empty=True) for row in by_id.values())
                or corpus_digest(sequences) != digest):
            continue
        for row in scores:
            if not isinstance(row, dict) or "prompt_ids" in row or type(row.get("id")) is not int:
                continue
            original = by_id.get(row["id"])
            if original and original["output_ids"] == row.get("tokens"):
                row["prompt_ids"] = original["prompt_ids"]
        return scores, sequences
    return scores, None


def _same_request_value(base, candidate) -> bool:
    """JSON request identity: booleans are not numbers; 0 and 0.0 remain equal."""
    if isinstance(base, bool) or isinstance(candidate, bool):
        return type(base) is type(candidate) and base == candidate
    if isinstance(base, dict) and isinstance(candidate, dict):
        return base.keys() == candidate.keys() and all(
            _same_request_value(value, candidate[key]) for key, value in base.items())
    if isinstance(base, list) and isinstance(candidate, list):
        return len(base) == len(candidate) and all(
            _same_request_value(a, b) for a, b in zip(base, candidate))
    return base == candidate


def compare(base: dict, candidate: dict) -> dict:
    warnings = [f"{key}: {base['workload'].get(key)} vs {candidate['workload'].get(key)}"
                for key in WORKLOAD_KEYS
                if not _same_request_value(base["workload"].get(key), candidate["workload"].get(key))]
    incomplete, incomparable = [], list(warnings)
    for label, run in (("base", base), ("candidate", candidate)):
        if run["workload"].get("prompt_count") is None:
            incomplete.append(f"{label}: requested prompt count is unavailable")
        if not run["workload"].get("prompts_sha256"):
            incomparable.append(f"{label}: prompt-set identity is unavailable")
    generation = compare_generation(base["generation"], candidate["generation"],
                                    base_workload=base["workload"], candidate_workload=candidate["workload"])
    result = {"warnings": warnings, "generation": generation}
    sections = [generation]
    if any(run.get("scores") is not None or "score_extra_body" in run["workload"]
           for run in (base, candidate)):
        recovered = [_score_inputs(run, (base, candidate)) for run in (base, candidate)]
        score_incomplete, score_incomparable, expected_ids = [], [], []
        for label, run, (rows, corpus) in zip(("base", "candidate"), (base, candidate), recovered):
            digest = run.get("corpus", {}).get("sha256")
            count = run.get("corpus", {}).get("sequences")
            if type(count) is not int or count <= 0:
                score_incomplete.append(f"{label}: valid scoring corpus count is unavailable")
            if "score_extra_body" in run["workload"] and run.get("scores") is None:
                score_incomplete.append(f"{label}: scoring was requested but score records are unavailable")
            if "score_extra_body" not in run["workload"]:
                score_incomparable.append(f"{label}: scoring request state is unverified (no score_extra_body)")
            else:
                try:
                    expected_state = score_extra_body(run["workload"].get("extra_body"))
                except ValueError as error:
                    score_incomparable.append(f"{label}: invalid generation request state: {error}")
                else:
                    if not _same_request_value(run["workload"]["score_extra_body"], expected_state):
                        score_incomparable.append(f"{label}: scoring request state differs from generation")
            if not digest:
                score_incomparable.append(f"{label}: scoring corpus identity is unavailable")
            if corpus is None and rows and all(isinstance(row, dict) and not row.get("error")
                    and _token_ids(row.get("prompt_ids")) and _token_ids(row.get("tokens"), allow_empty=True)
                    and type(row.get("id")) is int for row in rows):
                corpus = [{"id": row["id"], "prompt_ids": row["prompt_ids"], "output_ids": row["tokens"]}
                          for row in rows]
                if len(corpus) != count or corpus_digest(corpus) != digest:
                    corpus = None
            if corpus is None:
                score_incomparable.append(f"{label}: complete frozen scoring corpus is unverified")
                expected_ids.append(None)
                continue
            if len(corpus) != count:
                score_incomplete.append(f"{label}: scoring corpus count does not match its records")
            by_id = _record_index(corpus, f"{label} scoring corpus", score_incomplete, count)
            expected_ids.append({row["id"] for row in corpus if row["output_ids"]})
            for row in rows:
                if not isinstance(row, dict) or row.get("error") or type(row.get("id")) is not int:
                    continue
                original = by_id.get(row["id"])
                if original and (row.get("tokens") != original["output_ids"]
                                 or row.get("prompt_ids") != original["prompt_ids"]):
                    score_incomparable.append(f"{label}: score id {row['id']} does not match the frozen corpus")
        scores = compare_scores(recovered[0][0], recovered[1][0],
                                base_ids=expected_ids[0], candidate_ids=expected_ids[1])
        if base.get("corpus", {}).get("sha256") != candidate.get("corpus", {}).get("sha256"):
            message = "the runs scored different corpora (score the candidate with --corpus <base run>)"
            warnings.append(message)
            score_incomparable.append(message)
        if scores["status"] == "incomplete":
            score_incomplete += scores["reasons"]
        else:
            score_incomparable += scores["reasons"]
        scores.update(_alignment(score_incomplete, score_incomparable))
        result["scores"] = scores
        sections.append(scores)
    for section in sections:
        if section["status"] == "incomplete":
            incomplete += section["reasons"]
        else:
            incomparable += section["reasons"]
    # This is record/request identity only, not proof of the server-applied
    # prefix, model/profile state, or a preregistered noninferiority margin.
    return result | _alignment(incomplete, incomparable)


def format_comparison(result: dict, base_label: str, candidate_label: str) -> str:
    lines = [f"model behaviour: {base_label} -> {candidate_label}",
             f"  recorded-input alignment: {result['status']}; not a noninferiority verdict"]
    lines += [f"  {reason}" for reason in result["reasons"]]
    if result["status"] != "complete":
        lines.append("  metrics below are diagnostic only; incomplete/incomparable inputs")
    g = result["generation"]
    lines.append(f"  greedy responses identical: {g['identical']}/{g['compared']}"
                 + (f"; the others diverge at token p10/p50/p90 {g['divergence'].get('p10')}/"
                    f"{g['divergence'].get('p50')}/{g['divergence'].get('p90')}" if g["divergence"] else ""))
    for row in g["acceptance"]:
        lines.append(f"  {row['metric']:24s} {row['base']:8.3f} -> {row['candidate']:8.3f}  {row['change_pct']:+.2f}%")
    if g["per_position"]:
        lines.append("  acceptance by draft position: " + ", ".join(
            f"{r['position']}: {r['base']:.3f}->{r['candidate']:.3f}" for r in g["per_position"]))
    s = result.get("scores")
    if s and s.get("positions"):
        kl = s["kl"]
        lines.append(f"  teacher-forced over {s['positions']} positions in {s['sequences']} sequences"
                     + (f" ({s['skipped']} skipped)" if s["skipped"] else ""))
        lines.append(f"    identical recorded top-K/given-token logprobs {100 * s['identical_fraction']:.2f}%, "
                     f"top-1 agreement {100 * s['top1_agreement']:.3f}%")
        lines.append(f"    estimated top-K KL mean {kl['mean']:.3e} (±{kl['per_sequence'].get('ci95') or 0:.1e} over sequences), "
                     f"p99 {kl.get('p99', 0):.3e}, max {kl['max']:.3e} nats")
        lines.append(f"    given-token log-probability change mean {s['logprob_delta']['mean']:+.2e}, "
                     f"mean |change| {s['logprob_delta']['mean_abs']:.2e}, max {s['logprob_delta']['max_abs']:.2e}")
        p = s["perplexity"]
        lines.append(f"    perplexity {p['base']:.5f} -> {p['candidate']:.5f} ({p['change_pct']:+.3f}%)")
    elif s is not None:
        lines.append(f"  teacher-forced: no comparable sequences ({s.get('skipped', 0)} skipped)")
    return "\n".join(lines)
