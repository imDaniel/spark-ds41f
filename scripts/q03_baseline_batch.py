"""Exactly C1 then C4 at one immutable baseline arm/steering state.

The existing client owns every original workload proof. This module only gives
each client a separate hash-bound scope and gates the next client on validated
results, all-four epoch sealing, unchanged workers and continuing authority.
It never launches/stops servers, changes steering, or resumes partial batches.
"""
from __future__ import annotations

import copy
from pathlib import Path

import q03_owner_model as owner
from q03_held_window import exclusive_json

require, canonical, digest = owner.require, owner.canonical, owner.digest
CASES = ('warm4k_c1', 'warm4k_c4')


def validate_sequence(http):
    require(type(http.get('schema_version')) is int and http['schema_version'] == 6
            and type(http.get('case_sequence')) is list
            and http['case_sequence'] == list(CASES),
            'only fixed same-state warm4k_c1 then warm4k_c4 is supported')
    require(http['legacy_case']['case_id'] == CASES[0],
            'batch must retain the first original C1 case binding')


def client_scope(parent, directory, index):
    """Pure, reproducible projection; recovery derives the same two commands."""
    validate_sequence(parent['binding']['http'])
    require(type(index) is int and 0 <= index < len(CASES), 'fixed batch case index required')
    require('batch_case' not in parent, 'a client projection is not a boot plan')
    unhashed = dict(parent)
    require(unhashed.pop('plan_sha256') == digest(canonical(unhashed)), 'batch parent plan hash differs')
    require(parent['selection'] == parent['binding']['selection']
            == parent['client']['legacy_case']['selection'], 'batch arm/steering state differs')
    require(parent['client']['legacy_case']['case_id'] == CASES[0], 'batch first case differs')
    plan = copy.deepcopy(parent)
    plan['batch_case'] = {'parent_plan_sha256': parent['plan_sha256'], 'index': index}
    plan['client']['legacy_case']['case_id'] = CASES[index]
    root = Path(parent['client']['output']).parent / CASES[index]
    plan['client']['output'] = str(root / 'legacy-case.json')
    plan['client']['runtime_context'] = str(root / 'runtime-context.json')
    del plan['plan_sha256']
    plan['plan_sha256'] = digest(canonical(plan))
    return plan, Path(directory) / ('case-' + CASES[index])


def run_cases(ops, check, running, ready, validate_ready):
    """No skip/resume: any request, failure, cancel or unknown state unwinds.

    Server cleanup remains the existing outer all-four stop/log/drain path.
    Both original client caps and the one existing outer deadline are retained.
    """
    results = []
    previous = None
    for index, case in enumerate(CASES):
        check()
        running('steady')
        plan, directory = client_scope(ops.plan, ops.journal.directory, index)
        ops.journal.event('baseline-case-intent', case_index=index, case_id=case,
            case_plan_sha256=plan['plan_sha256'], previous_result_sha256=previous)
        ops.run_client(index)
        ops.seal_all()  # Positive client-descendant proof before any reuse.
        check()
        running('steady')
        final_ready = ops.read_ready()
        validate_ready(final_ready, ops.plan)  # Always validate the one boot spec.
        require(owner.json_bytes(final_ready)['reports'] == ready['reports'],
                'HTTP workers changed during batch case')
        result = ops.collect_result(index)  # Includes original final idle scrape.
        require(result.get('result') == 'passed', 'baseline batch case did not pass')
        previous = exclusive_json(directory / 'result.json', result)
        ops.journal.event('baseline-case-complete', case_index=index, case_id=case,
            case_plan_sha256=plan['plan_sha256'], result_sha256=previous)
        results.append(dict(case_id=case, case_plan_sha256=plan['plan_sha256'],
            result_sha256=previous, result=result))
    check()
    return dict(result='passed', window_id=ops.plan['window_id'], attempt_id=ops.plan['attempt_id'],
        plan_sha256=ops.plan['plan_sha256'], selection=copy.deepcopy(ops.plan['selection']),
        cases=results, lifecycle='REQUIRES_OWNED_FOUR_RANK_DRAIN',
        performance='NOT_EVALUATED', quality='NOT_EVALUATED', capacity='NOT_EVALUATED')
