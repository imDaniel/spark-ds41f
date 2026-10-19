"""Recheck retained API observations, independent mHC byte hashes and GPU JUnit."""
import argparse
import importlib.util
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('qualification_probe', ROOT / 'probe_model.py')
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)

# Source bytes from the independently replayed 180/184-patch component trees.
MHC_SOURCE_SHA256 = {
    'parent': '7849ab0e453f10e1044cb5e9bc1a5f40668ec63f49877eb4cf0ef76b1f6f7aa1',
    'candidate': '19e3f85d32ec917dc62839e564f494cfb44d1ebbb6d42008e2d213a1b789f23b',
}
GPU_CLASS = 'tests.models.test_deepseek_v4_1_steering_gpu'
GPU_CASES = {
    f'test_projection_fp64_oracle_and_stock_rows[{world}-{rows}]'
    for world in (1, 4) for rows in (1, 3, 16, 96, 127, 128, 129, 205, 8191, 8192)
} | {
    'test_rows_graph_replay_reuses_controls_and_output',
    'test_native_dummy_batch_does_not_own_real_slots',
    'test_openai_wire_boolean_is_not_coerced_into_a_dose',
    *(f'test_zero_layer_alpha_preserves_stock_writer_bits[{world}]' for world in (1, 4)),
    *(f'test_fused_consumer_graph_matches_independent_projection[{rows}]' for rows in (3, 16, 96, 129)),
}


def is_digest(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def check_api(data):
    arms = {'official', 'parent', 'candidate-unloaded', 'candidate-loaded'}
    expected = {(arm, i) for arm in arms for i in range(3)}
    rows = data['observations']
    keys = [(row['arm'], row['prompt_id']) for row in rows]
    if len(keys) != 12 or set(keys) != expected:
        raise ValueError('Expected twelve unique API observations')
    groups = {}
    for row in rows:
        value = probe.fingerprint(row, 64)
        if value != row['fingerprint']:
            raise ValueError('API observation fingerprint mismatch')
        groups.setdefault(row['prompt_id'], set()).add(value)
    if any(len(values) != 1 for values in groups.values()):
        raise ValueError('Cross-arm API observations differ')
    return {'observations': 12, 'prompts': 3, 'tokens_each': 64,
            'scope': 'canonical selected API JSON equality, not hidden-state tensor bit equality'}


def check_mhc(parent, candidate):
    expected = {f'{mode}-world{world}-rows{rows}' for rows in (1, 96, 8192)
                for mode, world in (('pre', 1), ('broadcast', 1), ('post_pre', 1), ('post_pre', 4))}
    indexed = []
    for arm, record in (('parent', parent), ('candidate', candidate)):
        if record.get('arm') != arm or record.get('mhc_source_sha256') != MHC_SOURCE_SHA256[arm]:
            raise ValueError('Independent mHC arm/source identity mismatch')
        cases = record['cases']
        if len(cases) != 12 or {row['case'] for row in cases} != expected:
            raise ValueError('Expected all twelve independent mHC cases')
        indexed.append({row['case']: row for row in cases})
    for name in expected:
        before, after = (record[name] for record in indexed)
        for row in (before, after):
            mode, world_part, rows_part = name.split('-')
            world, rows = int(world_part[5:]), int(rows_part[4:])
            seed = 1729 + rows + world * 1000000 + {'pre': 1, 'broadcast': 2, 'post_pre': 3}[mode] * 10000
            if (row.get('mode') != mode or type(row.get('world')) is not int
                    or row['world'] != world or type(row.get('rows')) is not int
                    or row['rows'] != rows or type(row.get('seed')) is not int or row['seed'] != seed):
                raise ValueError('Independent mHC case metadata mismatch')
            inputs, outputs = row.get('input_sha256'), row.get('output_sha256')
            if (not isinstance(inputs, dict) or set(inputs) != {'residual', 'writer', 'post', 'comb', 'pre', 'fn'}
                    or any(not is_digest(value) for value in inputs.values())
                    or not isinstance(outputs, list) or len(outputs) != 5
                    or any(not is_digest(value) for value in outputs)
                    or row['output_shapes'] != [[rows, 4, 5120], [rows, 4], [rows, 4, 4], [rows, 5120], [rows, 4]]
                    or row['graph_replays_bit_stable'] is not True):
                raise ValueError('Incomplete mHC byte/replay evidence')
        if any(before[key] != after[key] for key in ('seed', 'input_sha256', 'output_sha256', 'output_shapes')):
            raise ValueError('Independent mHC input/output bytes differ')
    return {'cases': 12, 'input_output_byte_sha256_equal': True, 'graph_replays_stable': True}


def check_junit(path):
    root = ET.parse(path).getroot()
    cases = list(root.iter('testcase'))
    identities = {(row.get('classname'), row.get('name')) for row in cases}
    if len(cases) != 29 or identities != {(GPU_CLASS, name) for name in GPU_CASES}:
        raise ValueError('Recorded GPU JUnit case identity mismatch')
    if (any(list(root.iter(tag)) for tag in ('failure', 'error', 'skipped'))
            or any(int(suite.get(key, 0)) for suite in root.iter('testsuite')
                   for key in ('failures', 'errors', 'skipped'))):
        raise ValueError('Recorded GPU JUnit must contain 29 unique passing cases')
    return {'recorded_tests': 29, 'failures': 0, 'errors': 0, 'skipped': 0,
            'scope': 'verify retained JUnit; no new GPU execution'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    read = lambda name: json.loads((args.evidence / name).read_text())
    result = {'api': check_api(read('api-stock-observations.json')),
              'mhc': check_mhc(read('mhc-parent.json'), read('mhc-candidate.json')),
              'gpu_junit': check_junit(args.evidence / 'gpu-kernels.xml')}
    with args.output.open('x') as out:
        json.dump(result, out, indent=2)
        out.write('\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
