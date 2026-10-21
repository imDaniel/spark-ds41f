"""Derive an ignored local candidate profile from a private independent baseline."""
import argparse
import copy
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def derive(baseline, source_tree):
    profile = copy.deepcopy(baseline)
    profile['deployment']['launch_enabled'] = False
    profile.setdefault('environment', {})['VLLM_DS41_Q03_MHC_MXFP8'] = '0'
    profile['container'].setdefault('expected_labels', {})['local.spark3.vllm.tree'] = source_tree
    profile['upstreams_config'] = 'experiments/2026-10-21-mhc-input-validation/upstreams.lock.json'
    return profile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text())
    tree = json.loads((HERE / 'source.json').read_text())['vllm']['expected_tree']
    output = HERE / 'cluster.local.json'
    with output.open('x') as f:
        json.dump(derive(baseline, tree), f, indent=2)
        f.write('\n')
    print('Local profile created; independent isolation and runtime checks remain required.')


if __name__ == '__main__':
    main()
