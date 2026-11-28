"""Install or restore the site's controls in one RC3 promotion window."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import uuid


ROOT = Path(__file__).resolve().parents[2]
OPERATIONS = Path("/srv/ai-cluster/operations/pr60-tp4-promotion")
OWNER = "pr60-tp4-promotion"
TRANSACTION = ROOT / "experiments/2026-11-28-1126-tp4-qualification/install_controls.py"


def transact(action, window):
    if str(uuid.UUID(window)) != window:
        raise ValueError("Use the canonical promotion window UUID")
    if os.geteuid() != 0:
        raise RuntimeError("The fixed control installation requires root")
    spec = importlib.util.spec_from_file_location("control_transaction", TRANSACTION)
    controls = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(controls)
    controls.ROOT = ROOT
    controls.OWNER = OWNER
    controls.OP = OPERATIONS / window
    with controls.locks():
        if action == "prepare":
            return controls.prepare(window)
        if action == "install":
            return controls.install(window)
        if action == "rollback":
            return controls.restore(controls.record_for(window))
        raise ValueError("Unknown control installation action")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "install", "rollback"])
    parser.add_argument("window")
    args = parser.parse_args()
    result = transact(args.action, args.window)
    print(json.dumps({"window_id": result["window_id"], "status": result["status"]}))
