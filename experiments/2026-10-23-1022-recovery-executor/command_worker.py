#!/usr/bin/env python3
"""Gated command process: no command starts before its PID is journalled.

The controller writes a job, starts this worker in a new process group, saves
its birth identity, then sends GO. EOF before GO is a harmless abandoned spawn.
The worker stays alive until its child exits, including on controller loss.
"""
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    job = json.loads(Path(sys.argv[1]).read_text())
    if sys.stdin.readline() != "GO\n":
        return 125
    env = os.environ.copy()
    env["SPARK_WINDOW_CONTEXT"] = json.dumps(job["context"])
    return subprocess.call(job["argv"], cwd=job["cwd"], env=env)


if __name__ == "__main__":
    sys.exit(main())
