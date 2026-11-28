"""Monitor a qualification command on the TP4 head and preserve its exit status."""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from spark_ds41f.bench import MemoryWatch, THERMAL_FIELDS, parse_thermal
from spark_ds41f.config import configuration, ssh_target

CONFIG = str((HERE / "cluster.json").relative_to(ROOT))
VM_COUNTERS = ("pswpin", "pswpout", "pgmajfault")
MONITOR_FAILURE = 125

# Send the read-only collector over SSH so it also works before the new
# qualification checkout is synchronized to the workers.
COLLECT = """
import json, os, subprocess, sys
from pathlib import Path

def output(*args):
    return subprocess.check_output(args, text=True, timeout=15).strip()

vm = dict(line.split() for line in Path('/proc/vmstat').read_text().splitlines())
mem = {row[0].rstrip(':'): int(row[1]) for line in Path('/proc/meminfo').read_text().splitlines()
       if (row := line.split()) and row[0] in ('MemAvailable:', 'SwapTotal:', 'SwapFree:')}
thermal = output('nvidia-smi', '--query-gpu=uuid,' + sys.argv[1], '--format=csv,noheader,nounits')
if len(thermal.splitlines()) != 1:
    raise ValueError('expected one GPU per rank')
uuid, fields = thermal.split(',', 1)
board = max(int(path.read_text()) for path in Path('/sys/class/thermal').glob('thermal_zone*/temp'))
journal = subprocess.run(['sudo', '-n', 'journalctl', '-k', '-b', '--quiet', '--no-pager',
                          '-o', 'json', '--output-fields=MESSAGE', '--grep=NVRM: Xid|GPU has fallen off'],
                         text=True, capture_output=True, timeout=20)
# journalctl --grep exits 1 with no output when the boot has no matching entry.
if journal.returncode and (journal.returncode != 1 or journal.stderr.strip() or journal.stdout.strip()):
    raise RuntimeError('GPU kernel journal: ' + journal.stderr.strip())
events = [json.loads(line) for line in journal.stdout.splitlines() if line.strip()]
print(json.dumps({
    'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
    'gpu_uuid': uuid.strip(),
    'vm': {'pagesize': os.sysconf('SC_PAGE_SIZE'),
           'swappiness': int(Path('/proc/sys/vm/swappiness').read_text()),
           **{key: int(vm[key]) for key in ('pswpin', 'pswpout', 'pgmajfault')}},
    'memory_kib': mem,
    'thermal_csv': fields + ',' + str(board),
    'gpu_faults': {event['__CURSOR']: event['MESSAGE'] for event in events},
}))
"""


def collect(nodes):
    def read(node):
        target = ssh_target(nodes, node)
        command = shlex.join(["python3", "-c", COLLECT, ",".join(THERMAL_FIELDS)])
        raw = subprocess.check_output(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", target, command],
            text=True, timeout=45,
        )
        snapshot = json.loads(raw)
        snapshot["thermal"] = parse_thermal(snapshot.pop("thermal_csv"))
        if not snapshot["thermal"]["complete"]:
            raise ValueError(f"{node['name']}: incomplete thermal counters")
        return node["name"], snapshot

    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        return dict(pool.map(read, nodes["nodes"]))


def differences(before, after):
    if len(before) != 4 or before.keys() != after.keys():
        raise ValueError("Telemetry needs the same four ranks at both boundaries")
    vm, faults, thermal = {}, {}, {}
    for name, start in before.items():
        end = after[name]
        if (start["boot_id"], start["gpu_uuid"], start["vm"]["pagesize"]) != (
            end["boot_id"], end["gpu_uuid"], end["vm"]["pagesize"]
        ):
            raise ValueError(f"{name}: boot, GPU or page size changed during command")
        vm[name] = {key: end["vm"][key] - start["vm"][key] for key in VM_COUNTERS}
        thermal[name] = {
            key: end["thermal"][key] - start["thermal"][key]
            for key in ("thermal_slowdown_us", "power_cap_us")
        }
        if min(*vm[name].values(), *thermal[name].values()) < 0:
            raise ValueError(f"{name}: a telemetry counter reset")
        old_faults, new_faults = start["gpu_faults"], end["gpu_faults"]
        if not old_faults.keys() <= new_faults.keys():
            raise ValueError(f"{name}: kernel journal lost previously visible GPU events")
        faults[name] = [message for event, message in new_faults.items() if event not in old_faults]
    return {"vm_delta": vm, "thermal_counter_delta": thermal, "new_gpu_faults": faults}


def stop_child(child):
    if child is None or child.poll() is not None:
        return
    try:
        os.killpg(child.pid, signal.SIGTERM)
        child.wait(timeout=20)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait()
    except ProcessLookupError:
        child.wait()


def monitor(receipt, command):
    _, nodes, _ = configuration(argparse.Namespace(cluster_config=CONFIG, shape="tp4-ring"))
    if {node["rank"] for node in nodes["nodes"]} != {0, 1, 2, 3} or len(nodes["nodes"]) != 4:
        raise ValueError("A four-rank TP4 recipe is required")
    record = {"command": command, "started_at": time.time(), "status": "running",
              "monitor": "spark_ds41f.bench.MemoryWatch", "sample_seconds": 1,
              "thermal_sample_seconds": 15, "sample_floor_gib": 3}
    with receipt.open("x") as stream:
        json.dump(record, stream)
    child = watch = None
    result = MONITOR_FAILURE
    problems = []
    started = time.monotonic()
    os.environ["SPARK_DS41F_STRICT_TELEMETRY"] = "1"
    try:
        before = record["before"] = collect(nodes)
        watch = MemoryWatch(nodes, 3, problems.append)
        watch.wait_ready()
        if problems:
            raise RuntimeError(problems[0])
        child = subprocess.Popen(command, cwd=ROOT, start_new_session=True)
        while child.poll() is None:
            watch.check()
            if problems:
                raise RuntimeError(problems[0])
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                continue
        record["exit_code"] = child.returncode
        result = 128 - child.returncode if child.returncode < 0 else child.returncode
        watch.check()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, KeyError) as error:
        problems.append(str(error))
    except KeyboardInterrupt:
        problems.append("monitor interrupted")
        result = 130
    finally:
        stop_child(child)
        if child is not None:
            record["exit_code"] = child.returncode
        if watch is not None:
            record["memory"] = watch.close()
            record["thermal"] = watch.thermal_summary()
        if "before" in record:
            try:
                after = record["after"] = collect(nodes)
                record.update(differences(before, after))
                if any(record["new_gpu_faults"].values()):
                    problems.append("new GPU fault in the kernel journal")
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, KeyError) as error:
                problems.append(str(error))
        if problems and result == 0:
            result = MONITOR_FAILURE
        record.update(status="passed" if result == 0 else "failed",
                      monitor_exit_code=result, errors=list(dict.fromkeys(problems)),
                      finished_at=time.time(), elapsed_seconds=time.monotonic() - started)
        with tempfile.NamedTemporaryFile("w", dir=receipt.parent, delete=False) as stream:
            json.dump(record, stream, indent=2)
            stream.write("\n")
        os.replace(stream.name, receipt)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("supply the monitored command after --")

    def interrupted(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    sys.exit(monitor(args.receipt, command))
