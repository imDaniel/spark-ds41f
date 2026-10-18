#!/usr/bin/env python3
"""Run CPU contracts against the exact prepared vLLM tree, without serving."""

import argparse
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CPU_TESTS = tuple(
    f"tests/models/test_deepseek_v4_1_steering_{name}.py"
    for name in ("identity", "profile", "request", "rows", "state", "consume")
)
EXPECTED_CASES = ROOT / "experiments/2026-10-18-steering-consume/cpu-cases.json"


def load_expected_cases(path=None):
    manifest = json.loads((EXPECTED_CASES if path is None else path).read_text())
    cases = manifest["test_cases"]
    modules = {path[:-3].replace("/", ".") for path in CPU_TESTS}
    if (
        manifest["schema_version"] != 1
        or not cases
        or len(set(cases)) != len(cases)
        or {case.split("::", 1)[0] for case in cases} != modules
    ):
        raise ValueError("Invalid CPU collection manifest")
    return cases


def pytest_environment():
    env = dict(os.environ)
    # A reusable gate must not inherit selection flags or external plugins.
    for key in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTEST_CURRENT_TEST"):
        env.pop(key, None)
    env.update(
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        CUDA_VISIBLE_DEVICES="",
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        VLLM_NO_USAGE_STATS="1",
        PYTHONDONTWRITEBYTECODE="1",
    )
    return env


def check_report(path, expected_cases=None):
    root = ET.parse(path).getroot()
    suites = list(root.iter("testsuite"))
    counts = {
        key: sum(int(s.get(key, 0)) for s in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }
    if not counts["tests"] or any(counts[k] for k in ("failures", "errors", "skipped")):
        raise ValueError(
            f"CPU gate requires executed, passing, unskipped tests: {counts}"
        )
    cases = [
        f"{case.get('classname')}::{case.get('name')}" for case in root.iter("testcase")
    ]
    expected = load_expected_cases() if expected_cases is None else expected_cases
    missing = Counter(expected) - Counter(cases)
    unexpected = Counter(cases) - Counter(expected)
    if not expected or counts["tests"] != len(cases) or missing or unexpected:
        raise ValueError(
            "CPU gate requires the complete pinned test collection: "
            f"expected={len(expected)}, executed={len(cases)}, "
            f"missing={list(missing)}, unexpected={list(unexpected)}"
        )
    return counts


def load_spark():
    loader = importlib.machinery.SourceFileLoader(
        "steering_cpu_spark", str(ROOT / "bin/spark")
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstreams", type=Path, required=True)
    parser.add_argument("--cases", type=Path, help="Committed CPU collection for this experiment")
    parser.add_argument("--report", type=Path, default=ROOT / ".work/steering-cpu.xml")
    args = parser.parse_args()
    spark = load_spark()
    upstreams = spark.read_json(str(args.upstreams))
    inputs = spark.build_inputs(upstreams)
    source = spark.build_directory(inputs) / "src/vllm"
    problems = spark.project_problems(source, inputs["vllm"])
    if problems:
        parser.error("; ".join(problems))
    args.report = args.report.resolve()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.unlink(missing_ok=True)
    print(f"CPU gate: {source}; tree={inputs['vllm']['expected_tree']}", flush=True)
    # Validate the independent, committed collection before executing pytest.
    expected_cases = load_expected_cases(args.cases)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "--confcutdir=tests/models",
            "--strict-markers",
            "-o",
            "addopts=",
            "-q",
            *CPU_TESTS,
            f"--junitxml={args.report}",
        ],
        cwd=source,
        env=pytest_environment(),
        check=True,
    )
    print(f"CPU gate passed: {check_report(args.report, expected_cases)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
