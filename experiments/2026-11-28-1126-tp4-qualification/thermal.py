"""The historical thermal gate, with freshness measured on the controller clock."""
import concurrent.futures
import json
import shlex
import subprocess
import time


def sample(pair):
    name, address = pair
    code = ("import pathlib,json,time;print(json.dumps({'at':time.time(),"
            "'hottest_c':max(int(p.read_text()) for p in "
            "pathlib.Path('/sys/class/thermal').glob('thermal_zone*/temp'))/1000}))")
    started = time.monotonic()
    result = subprocess.run(
        ["/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
         "admin@" + address, shlex.join(["/usr/bin/python3", "-c", code])],
        capture_output=True, text=True, check=True, timeout=15,
    )
    received = time.monotonic()
    return {"node": name, **json.loads(result.stdout),
            "query_started_monotonic": started,
            "received_monotonic": received, "received_at": time.time()}


def samples_ready(rows, names, observed):
    # Remote wall clocks may lead the controller by milliseconds. Each SSH
    # command reads the sensor afresh; bound its complete query on one clock.
    return (len(rows) == 4 and {row["node"] for row in rows} == set(names)
            and all(0 <= row["hottest_c"] < 55
                    and 0 <= observed - row["query_started_monotonic"] < 20
                    and row["query_started_monotonic"] <= row["received_monotonic"] <= observed
                    for row in rows))


def read_samples(nodes):
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        return list(pool.map(sample, nodes.items()))


def gate(manifest, output):
    deadline = time.monotonic() + 600
    while True:
        rows = read_samples(manifest["site_nodes"])
        observed = time.monotonic()
        with (output / "thermal.jsonl").open("a") as stream:
            stream.write(json.dumps(rows) + "\n")
        if samples_ready(rows, manifest["site_nodes"], observed):
            return rows
        if observed >= deadline:
            raise ValueError("Four-node thermal baseline unavailable")
        time.sleep(5)
