"""Manage this qualification's two ingress rules and distinguish missing rules from failed queries."""
import os
import subprocess
import time

from site_adapter import api, external_connections, firewall, new_connections_rule, queue, run


def rule_exists(rule, hold):
    result = subprocess.run(rule(hold, "-C"), text=True, capture_output=True, timeout=30,
                            env={**os.environ, "LC_ALL": "C"})
    if result.returncode == 0:
        return True
    if (result.returncode == 1 and
            result.stderr.strip() == "iptables: Bad rule (does a matching rule exist in that chain?)."):
        return False
    raise RuntimeError(f"Cannot inspect qualification ingress: {result.returncode}: {result.stderr.strip()}")


def ingress_stage(hold):
    full = rule_exists(firewall, hold)
    draining = rule_exists(new_connections_rule, hold)
    return "blocked" if full else "draining" if draining else "open"


def restore_ingress(hold):
    for rule in (firewall, new_connections_rule):
        if rule_exists(rule, hold):
            run(rule(hold, "-D"))


def block(hold):
    phase = ingress_stage(hold)
    if phase == "blocked":
        return
    if phase == "open":
        run(new_connections_rule(hold, "-I"))
    deadline = time.monotonic() + 300
    idle_since = None
    try:
        while time.monotonic() < deadline:
            load = queue(api("http://127.0.0.1:8000", "/metrics", timeout=5).decode())
            peers = external_connections(run(["ss", "-Htn", "state", "established", "sport", "=", ":8000"]).stdout)
            if load == {"running": 0, "waiting": 0} and not peers:
                if idle_since is None:
                    idle_since = time.monotonic()
                if time.monotonic() - idle_since >= 30:
                    run(firewall(hold, "-I"))
                    return
            else:
                idle_since = None
            time.sleep(2)
        raise RuntimeError("Ingress did not drain in five minutes")
    finally:
        if rule_exists(new_connections_rule, hold):
            run(new_connections_rule(hold, "-D"))
