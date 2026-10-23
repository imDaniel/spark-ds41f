"""Fault checks of the offline recovery decisions, without remote execution."""
import copy
import json
import pathlib

import pytest
from recovery_contract import command, may_open_window, restore_decision, validate_inventory

EXP = pathlib.Path(__file__).resolve().parents[1]
POLICY = json.loads((EXP / 'recovery-policy.json').read_text())
INVENTORY = json.loads((EXP / 'runs/recovery-inventory.json').read_text())


def test_retained_fleet_identity():
    validate_inventory(POLICY, INVENTORY)


@pytest.mark.parametrize('field,value', [
    ('head', 'wrong'), ('tracked_changes', ' M bin/spark'),
    ('profile_sha256', 'wrong'), ('profile_path', '/other/profile'),
    ('repository', '/other/checkout'), ('config', 'config/cluster.json'),
    ('file_hashes', {}), ('weights_present', 47),
])
def test_identity_drift_is_rejected(field, value):
    data = copy.deepcopy(INVENTORY)
    data['nodes'][2][field] = value
    with pytest.raises(ValueError):
        validate_inventory(POLICY, data)


def test_missing_and_duplicate_ranks_rejected():
    for nodes in (INVENTORY['nodes'][:3], [INVENTORY['nodes'][0]] * 4):
        with pytest.raises(ValueError):
            validate_inventory(POLICY, {'nodes': nodes})


@pytest.mark.parametrize('field,value', [('image', 'wrong'), ('source_tree', 'wrong'), ('mounts', [])])
def test_image_and_mount_drift_is_rejected(field, value):
    data = copy.deepcopy(INVENTORY)
    data['nodes'][3]['container'][field] = value
    with pytest.raises(ValueError):
        validate_inventory(POLICY, data)


def decision(**changes):
    state = dict(owned_hold=True, all_ranks_reachable=True, foreign_gpu_jobs=False,
                 candidate_running=False, retained_running_ranks=0,
                 retained_ready=False, candidate_stop_verified=True)
    return restore_decision(**(state | changes))


@pytest.mark.parametrize('field,value', [('owned_hold', False), ('all_ranks_reachable', False),
                                       ('foreign_gpu_jobs', True), ('candidate_stop_verified', False)])
def test_ambiguous_restore_does_not_start(field, value):
    with pytest.raises(ValueError):
        decision(**{field: value})


def test_partial_candidate_stops_before_restore():
    assert decision(candidate_running=True) == ['stop_candidate_all_ranks', 'verify_candidate_stopped', 'reobserve']


@pytest.mark.parametrize('ranks', [1, 2, 3, 4])
def test_partial_or_unhealthy_retained_service_is_coordinated(ranks):
    assert decision(retained_running_ranks=ranks)[0] == 'stop_retained_all_ranks'


def test_healthy_original_is_not_restarted():
    assert decision(retained_running_ranks=4, retained_ready=True) == [
        'verify_retained_identity_and_api', 'release_own_hold']


def test_full_restore_keeps_hold_until_all_checks():
    result = decision()
    assert result[0] == 'start_retained_all_ranks'
    assert result[-1] == 'release_own_hold'
    assert 'doctor_live' in result and 'verify_gateway_aliases' in result


@pytest.mark.parametrize('field,value', [
    ('owner_authorized', False), ('assets_on_all_ranks', False), ('published_revision', False),
    ('watchdog_selftest', False), ('hold_absent', False), ('requests_running', None),
    ('requests_waiting', None), ('requests_running', 1), ('requests_waiting', 1), ('idle_seconds', 29),
])
def test_window_gates_fail_closed(field, value):
    state = dict(owner_authorized=True, assets_on_all_ranks=True, published_revision=True,
                 watchdog_selftest=True, hold_absent=True, requests_running=0,
                 requests_waiting=0, idle_seconds=30)
    with pytest.raises(ValueError):
        may_open_window(**(state | {field: value}))


def test_command_is_pinned_and_plan_only():
    for action in ('start', 'stop', 'doctor'):
        args = command(POLICY, action)
        assert args[0].startswith(POLICY['repository'] + '/')
        assert args[args.index('--cluster-config') + 1] == POLICY['config']
        assert '--apply' not in args and '--change-shape' not in args
