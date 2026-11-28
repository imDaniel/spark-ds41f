"""Run one qualification window on Spark01 and restore the retained release."""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

import window


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CFG = str((HERE / "cluster.json").relative_to(ROOT))
SPARK = [str(ROOT / "bin/spark"), "--shape", "tp4-ring", "--cluster-config", CFG]
TAG = "ds41f-1126-attention:vllm-50402286dbe6"
TREE = "50402286dbe6d2f5975211809be8ecada25c8f69"


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def output(command):
    return subprocess.check_output(command, text=True, timeout=60).strip()


def unit_state(unit):
    data = output(["systemctl", "show", unit, "--property=LoadState,ActiveState,SubState,Result,ExecMainStatus"])
    return dict(row.split("=", 1) for row in data.splitlines())


class Run:
    def __init__(self, ident):
        self.ident = ident
        self.dir = window.OP / ("run-" + ident)
        self.dir.mkdir(exist_ok=False)
        self.deadline = time.monotonic() + 180 * 60

    def record(self, phase, **details):
        event = {"at": utc(), "phase": phase, **details}
        with (self.dir / "events.jsonl").open("a") as stream:
            stream.write(json.dumps(event) + "\n")
        print(event, flush=True)

    def result_dir(self, name):
        return window.OP / f"{self.ident}-{name}"

    def stage(self, kind, name, command, *, minutes, observe=False):
        if self.deadline - time.monotonic() < minutes * 60:
            raise RuntimeError(f"Insufficient time for {name} before the recovery reserve")
        unit = f"{window.PREFIX}-{self.ident}-{kind}.service"
        log = self.dir / f"{name}.log"
        if observe:
            command = [sys.executable, str(HERE / "observe.py"), str(self.dir / f"{name}-telemetry.json"),
                       "--", *command]
        self.record(name, command=shlex.join(map(str, command)))
        window.heartbeat(self.ident, "testing" if kind == "test" else name)
        with window.lock():
            hold = window.active(self.ident)
            window.require_watchdog(self.ident)
            if window.site.ingress_stage(hold) != "blocked":
                raise RuntimeError("External ingress must be blocked for qualification jobs")
            subprocess.run(["sudo", "-n", "systemd-run", "--quiet", "--unit=" + unit, "--uid=admin",
                            "--service-type=exec", "--property=RemainAfterExit=yes",
                            "--working-directory=" + str(ROOT), "--setenv=HOME=" + str(Path.home()),
                            "--setenv=PYTHONUNBUFFERED=1", "--setenv=PYTHONDONTWRITEBYTECODE=1",
                            "--property=StandardOutput=append:" + str(log),
                            "--property=StandardError=inherit", *command], check=True, timeout=40)
        while True:
            st = unit_state(unit)
            if st["SubState"] == "exited" or st["ActiveState"] not in ("active", "activating"):
                break
            window.heartbeat(self.ident)
            window.require_watchdog(self.ident)
            if time.monotonic() >= self.deadline:
                raise TimeoutError("Reached the qualification cutoff")
            time.sleep(8)
        self.record(name, unit=st)
        if st["SubState"] != "exited" or st["Result"] != "success" or st["ExecMainStatus"] != "0":
            raise RuntimeError(f"{name} failed; see {log}")
        subprocess.run(["sudo", "-n", "systemctl", "stop", unit], check=True, timeout=45)

    def exercise(self):
        self.record("draining")
        window.block_ingress(self.ident)
        window.stop_production(self.ident)
        # Reuse only an image with a matching earlier native build receipt.
        from spark_ds41f.build import image_labels
        existing = image_labels(TAG)
        if existing:
            receipts = (ROOT / ".work/build").glob("*/images/" + existing["id"][7:23] + ".json")
            if not any((data := json.loads(p.read_text())).get("image_id") == existing["id"]
                       and data.get("tag") == TAG for p in receipts):
                raise RuntimeError("Existing candidate tag has no local build receipt")
            self.stage("build", "build-smoke", [*SPARK, "build", "smoke"], minutes=2)
        else:
            self.stage("build", "build", [*SPARK, "build", "image", "--apply"], minutes=10)
        built = image_labels(TAG)
        if not built or built["labels"].get("local.spark-ds41f.vllm.tree") != TREE:
            raise RuntimeError("Built image does not identify the accepted RC3 source tree")
        ident = built["id"]
        if not re.fullmatch("sha256:[0-9a-f]{64}", ident):
            raise RuntimeError("Invalid Docker image identity")
        (self.dir / "image.json").write_text(json.dumps(built, indent=2) + "\n")
        image_file = window.OP / "candidate-image.txt"
        tmp = self.dir / "candidate-image.tmp"
        tmp.write_text(ident + " " + TAG + "\n")
        os.replace(tmp, image_file)
        self.stage("sync", "sync-plan", [*SPARK, "cluster", "sync"], minutes=1)
        plan = (self.dir / "sync-plan.log").read_text()
        if "plan only" not in plan or any(f"spark0{node}: fetch origin and detach" not in plan
                                          for node in range(1, 5)):
            raise RuntimeError("Incomplete four-rank sync plan")
        self.stage("sync", "sync", [*SPARK, "cluster", "sync", "--apply"], minutes=7)
        self.stage("kernel", "native", [sys.executable, str(HERE / "native.py"), self.ident, ident,
                                        str(self.result_dir("native"))], minutes=50)
        for boot in (1, 2):
            self.stage("start", f"start-{boot}", [sys.executable, str(HERE / "run.py"), "transition", "start",
                                                  "--window", self.ident],
                       minutes=17 if boot == 1 else 2, observe=True)
            self.stage("test", f"doctor-{boot}", [*SPARK, "doctor", "--live"], minutes=1)
            self.save_boot(boot)
            stages = ("targeted", "functional", "capacity", "performance") if boot == 1 else ("performance",)
            for stage in stages:
                name = f"{stage}-{boot}"
                self.stage("test", name, [sys.executable, str(HERE / "qualify.py"), stage, self.ident,
                                          ident, str(self.result_dir(name))],
                           minutes={"targeted": 2, "functional": 1, "capacity": 20, "performance": 14}[stage],
                           observe=True)
            if boot == 1:
                self.stage("start", "stop-1", [sys.executable, str(HERE / "run.py"), "transition", "stop",
                                               "--window", self.ident],
                           minutes=1)
        self.record("hardware-stages-passed")

    def save_boot(self, boot):
        for rank in range(1, 5):
            command = ["docker", "logs", "--timestamps", "ds41f-1126-attention-fixes"]
            with (self.dir / f"boot-{boot}-rank-{rank}.log").open("x") as log:
                subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                                f"admin@10.42.0.{rank}", shlex.join(command)], stdout=log,
                               stderr=subprocess.STDOUT, check=True, timeout=60)
            window.heartbeat(self.ident)


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("execute", "transition"))
    parser.add_argument("value")
    parser.add_argument("--window")
    args = parser.parse_args()
    if args.action == "transition":
        if not args.window:
            parser.error("supply the owning window UUID for a candidate transition")
        def check_window():
            h = window.active(args.window)
            window.require_watchdog(args.window)
            if window.site.ingress_stage(h) != "blocked":
                raise RuntimeError("Candidate transition requires drained ingress")
        check_window()
        if args.value == "start":
            window.transition(ROOT, CFG, "start", "--replace", pre_apply=check_window)
        elif args.value == "stop":
            window.transition(ROOT, CFG, "stop", "--parallel", pre_apply=check_window)
        else:
            parser.error("expected start or stop")
    else:
        window.active(args.value)
        window.require_watchdog(args.value)
        job = Run(args.value)
        try:
            job.exercise()
        except BaseException as error:
            job.record("failed", error=str(error))
            raise
        finally:
            job.record("restoring")
            window.restore(args.value)
            job.record("awaiting-gateway")
