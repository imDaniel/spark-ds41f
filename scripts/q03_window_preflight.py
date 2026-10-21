#!/usr/bin/env python3
"""Read-only Q03 window guards. Site inventory stays outside this repository.

No import-time commands. All I/O is injected; check() is invoked by the local
operator, never by cloud validation. No fetch, checkout, tag, cache or service
mutation is performed here. A failed/unknown observation is a hard refusal.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import math
import re
import shlex
import zlib


class PreflightError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise PreflightError(message)


def parse_idle_metrics(text):
    """Both counters must be present, finite, nonnegative and exactly idle."""
    values = {"vllm:num_requests_running": [], "vllm:num_requests_waiting": []}
    for line in text.splitlines():
        for name in values:
            if line.startswith(name + "{") or line.startswith(name + " "):
                try:
                    match = re.fullmatch(
                        re.escape(name) + r"(?:\{[^}]*\})?\s+(\S+)(?:\s+\S+)?", line
                    )
                    require(match is not None, "Invalid idle metric syntax")
                    value = float(match.group(1))
                except ValueError as exc:
                    raise PreflightError("Invalid idle metric") from exc
                require(math.isfinite(value) and value >= 0, "Unknown idle state")
                values[name].append(value)
    require(all(values.values()), "Missing required idle counters")
    require(sum(map(sum, values.values())) == 0, "Requests are in flight")
    return 0


def _run(ops, argv, cwd=None, timeout=60):
    result = ops.run(argv, cwd=cwd, timeout=timeout)
    require(result.returncode == 0, "Read-only probe failed: " + argv[0])
    return result.stdout.strip()


def _json_run(ops, argv, **kwargs):
    try:
        return json.loads(_run(ops, argv, **kwargs))
    except (ValueError, TypeError) as exc:
        raise PreflightError("Malformed probe response") from exc


def _optional_json(ops, path):
    try:
        value = ops.read_json(path)
        require(isinstance(value, dict), "Unknown hold/request file state")
        return value
    except FileNotFoundError:
        return None


def _fresh_hold(hold, spec, now, recovery=False):
    require(isinstance(hold, dict), "Missing owned hold")
    require(hold.get("holder") == spec["holder"], "Foreign hold")
    require(hold.get("window") == spec["window_id"], "Wrong window identity")
    try:
        heartbeat = dt.datetime.fromisoformat(hold["heartbeat"].replace("Z", "+00:00"))
        age = (now - heartbeat).total_seconds()
    except (KeyError, ValueError, TypeError) as exc:
        raise PreflightError("Unknown hold freshness") from exc
    if recovery:
        return
    require(0 <= age <= 900, "Stale or future hold heartbeat")
    if hold.get("expected_end") is not None:
        try:
            end = dt.datetime.fromisoformat(hold["expected_end"].replace("Z", "+00:00"))
            require(now < end, "Window expired")
        except (ValueError, TypeError) as exc:
            raise PreflightError("Invalid hold expiration") from exc


def _validate_preflight(expected, observed):
    """Pure guard validation, also usable with CPU fault-injection fixtures."""
    phase = observed["phase"]
    require(phase in {"before_hold", "before_stop", "stopped", "before_restore", "restore_drained", "restored"},
            "Unknown preflight phase")
    require(observed.get("publication_ok") is True, "Publication not verified")
    require(observed.get("protected_ok") is True, "Protected inputs not verified")
    require(observed.get("node_count") == 4, "Require all four nodes")
    recovery = phase in {"before_restore", "restore_drained", "restored"}
    require(type(observed.get("requests_absent")) is bool, "Unknown request state")
    if not recovery:
        require(observed["requests_absent"], "Pending request")
    require(observed.get("legacy_hold_absent") is True, "Legacy hold exists or unknown")
    if phase == "before_hold":
        require(observed.get("hold") is None, "Existing hold")
    else:
        _fresh_hold(observed.get("hold"), expected, observed["now"], recovery)
    ds, glm = observed["deepseek"], observed["glm"]
    for service in (ds, glm):
        require(type(service.get("failed")) is bool, "Unknown service state")
        if service is glm or phase not in {"before_restore", "stopped", "restore_drained"}:
            require(service["failed"] is False, "Failed service state")
        require(len(service.get("nodes", {})) == 4, "Incomplete service observation")
    require(type(ds.get("running")) is bool and type(ds.get("ready")) is bool,
            "Unknown DeepSeek state")
    require(glm.get("running") is False, "GLM must remain stopped")
    require(all(row.get("running") is False for row in glm["nodes"]), "GLM rank running")
    if phase == "before_restore":
        require(ds.get("errors") == {}, "Unknown or foreign DeepSeek identity")
        require(all(row.get("valid") is True for row in ds["nodes"].values()),
                "DeepSeek identity must be known before cleanup")
    if phase in {"before_hold", "before_stop", "restored"}:
        require(ds.get("running") is True and ds.get("ready") is True,
                "DeepSeek is not fully ready")
        require(observed.get("idle") is True, "Unknown or busy serving state")
        require(ds.get("gpu_ownership_verified") is True, "Unexplained GPU ownership")
        if phase == "restored":
            require(ds.get("prompt_tokens_details") is True,
                    "Registered restored cache-details option is not active")
    elif phase in {"stopped", "restore_drained"}:
        require(ds.get("running") is False, "DeepSeek not stopped")
        require(observed.get("gpus_empty") is True, "GPU drain not verified")
        require(ds.get("errors") == {}, "DeepSeek cleanup errors")
        require(all(row.get("running") is False and row.get("valid") is True
                    and row.get("memguard") == "inactive"
                    for row in ds["nodes"].values()), "DeepSeek cleanup incomplete")
    return observed


def validate_preflight(expected, observed):
    try:
        return _validate_preflight(expected, observed)
    except PreflightError:
        raise
    except Exception as exc:
        raise PreflightError("Unknown preflight state: " + type(exc).__name__) from exc


# Payload only reads recorded paths and Git identity; no supplied probe is exec'd.
_NODE_PROBE = r'''
import base64,hashlib,json,os,pathlib,platform,subprocess,zlib
os.environ['GIT_OPTIONAL_LOCKS']='0'
for key in list(os.environ):
 if key.startswith(('DOCKER_','BUILDX_','BUILDKIT_')):os.environ.pop(key)
os.environ['DOCKER_HOST']='unix:///var/run/docker.sock'

e=json.loads(zlib.decompress(base64.b85decode(PAYLOAD)))
errors=[]
for want in e['files']:
 p=pathlib.Path(want['path']); got={'path':str(p),'exists':p.exists(),'symlink':p.is_symlink()}
 try:
  if p.is_symlink():got['target']=os.readlink(p)
  elif p.is_file():
   st=p.stat();got.update(size=st.st_size,mode=oct(st.st_mode&0o7777),uid=st.st_uid,gid=st.st_gid,mtime_ns=st.st_mtime_ns)
   h=hashlib.sha256()
   with p.open('rb') as f:
    for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
   got['sha256']=h.hexdigest()
  if got!=want:errors.append(str(p))
 except Exception:errors.append(str(p))
for item in e['extra']:
 p=pathlib.Path(item['path'])
 if (not p.is_absolute() or p.resolve()!=p or p.is_symlink()
     or not p.is_file() or p.stat().st_uid!=os.getuid()
     or p.stat().st_mode&0o022 or hashlib.sha256(p.read_bytes()).hexdigest()!=item['sha256']):
  errors.append(str(p))
if json.loads(pathlib.Path(e['extra'][1]['path']).read_text())!=e['authority']:
 errors.append('remote authority differs')
def git(*args):return subprocess.check_output(['git','-C',e['repository'],*args],text=True).strip()
print(json.dumps({'errors':errors,'hostname':platform.node(),'head':git('rev-parse','HEAD'),'status':git('status','--porcelain')}))
'''


# Fleet checks derived from the hash-pinned registered DS controller.
# Add read-only environment, fail-closed inspect errors, all-GPU ownership, and
# restored cache-details observation. Never import its original Executor.
_DS_NODE_PROBE = r'''import os,sys
os.environ['GIT_OPTIONAL_LOCKS']='0'
for key in list(os.environ):
 if key.startswith(('DOCKER_','BUILDX_','BUILDKIT_')):os.environ.pop(key)
os.environ['DOCKER_HOST']='unix:///var/run/docker.sock'

sys.dont_write_bytecode=True

import hashlib,json,subprocess,sys
from pathlib import Path
r=Path(P['repository']);q=subprocess.run(['/usr/bin/docker','--host','unix:///var/run/docker.sock','inspect',P['container']],capture_output=True,text=True)
row={'present':q.returncode==0,'running':False,'valid':False,'gpu_pids':[],'errors':[]}
if q.returncode and 'No such object:' not in q.stderr and 'No such container:' not in q.stderr:
 raise RuntimeError('Unknown DeepSeek container state')
profile=Path(P['profile_directory']);manifest=profile/P['profile_manifest'];directions=manifest.parent/'directions.f32'
checks=[hashlib.sha256((r/P['cluster_config']).read_bytes()).hexdigest()==P['config_sha256'],hashlib.sha256(manifest.read_bytes()).hexdigest()==P['manifest_sha256'],hashlib.sha256(directions.read_bytes()).hexdigest()==P['directions_sha256'],subprocess.check_output(['git','-C',str(r),'rev-parse','HEAD'],text=True).strip()==P['revision'],not subprocess.check_output(['git','-C',str(r),'status','--porcelain'])]
for name,digest in P.get('bundled_files',{}).items():checks.append(hashlib.sha256((Path(P['model_directory'])/name).read_bytes()).hexdigest()==digest)
row['valid']=all(checks)
if not all(checks):row['errors'].append('Candidate inputs changed')
row['memguard']=subprocess.run(['/usr/bin/systemctl','is-active',P['container']+'-memguard.service'],capture_output=True,text=True).stdout.strip()

if q.returncode==0:
 c=json.loads(q.stdout)[0];row['running']=c['State']['Running'];row['failed']=bool(c['State'].get('OOMKilled') or c['State'].get('ExitCode'))
 mounts={m['Destination']:m for m in c['Mounts']};cmd=c['Config']['Cmd'];row['prompt_tokens_details']='--enable-prompt-tokens-details' in cmd;env=dict(x.split('=',1) for x in c['Config'].get('Env',[]) if '=' in x)
 def arg(k,v):return k in cmd and cmd.index(k)+1<len(cmd) and cmd[cmd.index(k)+1]==str(v)
 checks += [c['Image']==P['image_id'],arg('--nnodes',4),arg('--tensor-parallel-size',4),arg('--node-rank',NODE-1),arg('--served-model-name',P['model_id']),env.get('VLLM_USE_V2_MODEL_RUNNER')=='1',env.get('VLLM_DS41_KERNEL_BACKEND')=='tilelang']
 for dst,src in [('/models',P['model_directory']),('/steering',P['profile_directory'])]:checks.append(dst in mounts and mounts[dst]['Source']==src and not mounts[dst]['RW'])
 model=Path(cmd[0]);model_mount=next((m for m in c['Mounts'] if model.is_relative_to(m['Destination'])),None)
 checks.append(bool(model_mount) and (Path(model_mount['Source'])/model.relative_to(model_mount['Destination'])).resolve()==Path(P['model_directory']).resolve())
 try:
  steering=json.loads(cmd[cmd.index('--additional-config')+1])['ds41_steering']
  checks += [steering['profile_path']==P.get('profile_startup_path','/steering/'+str(Path(P['profile_manifest']).parent)),steering['profile_sha256']==P.get('profile_startup_sha256',P['manifest_sha256']),steering['operator_version']==P['operator_version']]
 except (KeyError,ValueError,IndexError):checks.append(False)
 checks += [arg('--port',P['api_port']),arg('--max-model-len',1048576),arg('--max-num-seqs',16),arg('--kv-cache-memory-bytes',11274289152)]
 row['valid']=all(checks)
 if not row['valid']:row['errors'].append('Candidate identity mismatch')
 if row['running']:
  own={int(x.strip()) for x in subprocess.check_output(['/usr/bin/docker','--host','unix:///var/run/docker.sock','top',c['Id'],'-eo','pid'],text=True).splitlines()[1:] if x.strip().isdigit()};gpu_lines=subprocess.check_output(['/usr/bin/nvidia-smi','--query-compute-apps=pid','--format=csv,noheader,nounits'],text=True).splitlines();assert all(x.strip().isdigit() for x in gpu_lines if x.strip()),'Unknown GPU PID output';gpu={int(x.strip()) for x in gpu_lines if x.strip()};row['gpu_pids']=sorted(own&gpu);row['gpu_ownership_verified']=gpu<=own
print(json.dumps(row))
'''


def _observe_deepseek(spec, policy, ops):
    rows = {}
    for node in spec["nodes"]:
        rank = node["rank"]
        try:
            rows[str(rank)] = _json_run(ops, _node_argv(spec, node, [
                "sudo", "-n", "python3", "-I", "-B", "-c",
                "P=" + repr(policy) + "\nNODE=" + str(rank) + "\n" + _DS_NODE_PROBE,
            ]), timeout=30)
        except Exception as exc:
            rows[str(rank)] = dict(present=False, running=False, valid=False,
                                   gpu_pids=[], errors=[type(exc).__name__])
    running = any(row["running"] for row in rows.values())
    complete = all(row["running"] and row["valid"] and not row["errors"]
                   for row in rows.values())
    ready = False
    if complete:
        base = f"http://127.0.0.1:{policy['api_port']}"
        try:
            _run(ops, ["curl", "--fail", "--silent", "--show-error", "--max-time", "2",
                       base + "/health"])
            models = _json_run(ops, ["curl", "--fail", "--silent", "--show-error",
                                    "--max-time", "2", base + "/v1/models"])
            ready = [item["id"] for item in models["data"]] == [policy["model_id"]]
        except Exception:
            ready = False
    errors = {name: row["errors"] for name, row in rows.items() if row["errors"]}
    return {"running": running, "ready": complete and ready,
            "failed": bool(errors or any(row.get("failed") for row in rows.values())
                           or (running and not complete)),
            "gpu_pids": {name: row["gpu_pids"] for name, row in rows.items()},
            "gpu_ownership_verified": all(row.get("gpu_ownership_verified") is True
                                          for row in rows.values()),
            "prompt_tokens_details": all(row.get("prompt_tokens_details") is True
                                         for row in rows.values()),
            "nodes": rows, "errors": errors, "api_model": policy["model_id"]}


_GLM_PROBE = r'''
import json,subprocess,os
for key in list(os.environ):
 if key.startswith(('DOCKER_','BUILDX_','BUILDKIT_')):os.environ.pop(key)
os.environ['DOCKER_HOST']='unix:///var/run/docker.sock'
p=subprocess.run(['sudo','-n','docker','--host','unix:///var/run/docker.sock','inspect',NAME],capture_output=True,text=True)
if p.returncode:
 if 'No such object:' not in p.stderr and 'No such container:' not in p.stderr:
  raise RuntimeError('Unknown GLM container state')
 print(json.dumps({'exists':False}))
else:
 rows=json.loads(p.stdout)
 if len(rows)!=1:raise RuntimeError('Ambiguous GLM container state')
 c=rows[0]
 print(json.dumps({'exists':True,'id':c['Id'],'image':c['Image'],'state':c['State']}))
'''


def _node_argv(spec, node, argv):
    host = node["ssh_host"]
    require(re.fullmatch(r"[A-Za-z0-9_.@:-]+", host) is not None and not host.startswith("-"),
            "Invalid node host")
    if node.get("local") is True:
        return argv
    return ["ssh", "-i", spec["ssh_identity"], "-o", "BatchMode=yes", "-o",
            "ConnectTimeout=10", host, shlex.join(argv)]


def collect_preflight(manifest, ops, phase):
    spec = manifest["preflight"]
    for key in ("hold_path", "request_path", "legacy_hold_path", "legacy_request_path"):
        require(str(ops.resolve(spec[key])) == spec[key], "Coordination path must be canonical")
    require(len({spec[key] for key in ("hold_path", "request_path", "legacy_hold_path",
                                      "legacy_request_path")}) == 4, "Coordination paths overlap")
    pub = spec["publication"]
    checkout = str(ops.resolve(spec["independent_checkout"]))
    require(pub["url"] in {"https://github.com/imDaniel/spark-ds41f.git",
                           "git@github.com:imDaniel/spark-ds41f.git"}, "Not the self fork")
    require(re.fullmatch(r"[0-9a-f]{40}", pub["sha"]) is not None, "Unpinned publication")
    require(re.fullmatch(r"refs/heads/[A-Za-z0-9_./-]+", pub["ref"]) is not None,
            "Publication must name an exact branch ref")
    policy = ops.read_json(spec["policy_path"])
    require(all(policy.get(key) == value for key, value in spec["registered_policy"].items()),
            "Registered policy authority drift")
    require({"repository", "cluster_config", "config_sha256", "revision", "image_id", "container"}
            <= set(spec["registered_policy"]), "Incomplete registered policy authority")
    original = str(ops.resolve(policy["repository"]))
    require(checkout != original and not checkout.startswith(original + "/")
            and not original.startswith(checkout + "/"), "Independent checkout required")
    require(ops.sha256(spec["policy_path"]) == spec["policy_sha256"], "Policy drift")
    require(ops.sha256(original + "/" + policy["cluster_config"]) == policy["config_sha256"],
            "Registered restore profile drift")
    git = ["git", "--no-optional-locks", "-C", checkout]
    if phase not in {"before_restore", "restore_drained", "restored"}:
        require(_run(ops, git + ["remote", "get-url", "origin"]) == pub["url"], "Fork URL drift")
        require(_run(ops, git + ["rev-parse", "HEAD"]) == pub["sha"], "Candidate HEAD drift")
        require(not _run(ops, git + ["status", "--porcelain"]), "Dirty independent checkout")
        remote = _run(ops, git + ["ls-remote", "--exit-code", pub["url"], pub["ref"]])
        require(remote.split() == [pub["sha"], pub["ref"]], "Exact ref is not published")
    nodes = spec["nodes"]
    require(len(nodes) == 4 and len({n["name"] for n in nodes}) == 4
            and {n["rank"] for n in nodes} == {1, 2, 3, 4},
            "Require four distinct nodes")
    require([n["rank"] for n in nodes if n.get("local") is True] == [1],
            "Run only on the registered local head")
    for node in nodes:
        require(ops.sha256(node["inventory_path"]) == node["inventory_sha256"],
                "Protected inventory receipt drift")
        receipt = ops.read_json(node["inventory_path"])
        inventory = receipt["inventory"]
        require(receipt.get("exit_code") == 0 and not inventory.get("pin_errors"),
                "Invalid protected inventory")
        require(receipt["node"] == node["name"], "Wrong node inventory")
        require(inventory["source"]["head"]["stdout"] == policy["revision"],
                "Inventory source differs from registered policy")
        require(inventory["files"] and all("error" not in f for f in inventory["files"]),
                "Incomplete protected inventory")
        # Bound every -c argument below Linux's per-argument limit, including
        # SSH quoting. Inventory hashes are deliberately not copied to nodes.
        for offset in range(0, len(inventory["files"]), 400):
            payload = base64.b85encode(zlib.compress(json.dumps({
                "files": inventory["files"][offset:offset + 400],
                "repository": original,
                "extra": [{"path": spec[key + "_path"], "sha256": spec[key + "_sha256"]}
                          for key in ("remote_helper", "remote_authority")],
                "authority": {"independent_root": manifest["independent_root"],
                              "protected_roots": spec["protected_roots"]},
            }).encode())).decode()
            require(len(payload) < 90000, "Inventory probe payload too large")
            result = _json_run(ops, _node_argv(spec, node, ["python3", "-I", "-B", "-c",
                               "PAYLOAD=" + repr(payload) + "\n" + _NODE_PROBE]), timeout=180)
            require(result.get("hostname") == inventory["hostname"], "Wrong node identity")
            require(result.get("errors") == [] and result.get("head") == policy["revision"]
                    and result.get("status") == "", "Protected node inputs changed")
    glm_identity = {}
    for node in nodes:
        identity = _json_run(ops, _node_argv(spec, node, ["python3", "-I", "-B", "-c",
                                "NAME=" + repr(spec["glm_container"]) + "\n" + _GLM_PROBE]))
        if identity["exists"]:
            state = identity["state"]
            require(identity["image"] == spec["glm_image_id"] and state["Running"] is False
                    and state["OOMKilled"] is False and state["ExitCode"] == 0
                    and state["Paused"] is False and state["Restarting"] is False,
                    "GLM initial state is not safely stopped")
        glm_identity[node["name"]] = identity
    if phase != "before_hold":
        require(spec.get("glm_identity") == glm_identity, "Stopped GLM identity changed")
    services = {}
    for name in ("deepseek", "glm"):
        controller = spec["controllers"][name]
        require(re.fullmatch(r"[0-9a-f]{64}", controller["sha256"]), "Unpinned controller")
        require(ops.sha256(controller["path"]) == controller["sha256"], "Controller drift")
        if name == "deepseek":
            services[name] = _observe_deepseek(spec, policy, ops)
        else:
            services[name] = _json_run(ops, ["python3", "-I", "-B",
                                            controller["path"], "probe"], timeout=120)
            # Public environment values are unnecessary in the session receipt.
            for row in services[name]["nodes"]:
                row.pop("actual_env", None)
                row.pop("cmd", None)
    idle = False
    if phase in {"before_hold", "before_stop", "restored"}:
        require(type(spec["idle_samples"]) is int and spec["idle_samples"] >= 2
                and type(spec["idle_interval_seconds"]) in (int, float)
                and math.isfinite(spec["idle_interval_seconds"])
                and spec["idle_interval_seconds"] >= 1
                and (spec["idle_samples"] - 1) * spec["idle_interval_seconds"] >= 30,
                "Idle guard requires at least 30 seconds of complete observations")
        require(spec["metrics_url"] == f"http://127.0.0.1:{policy['api_port']}/metrics",
                "Idle metrics must identify the registered DeepSeek endpoint")
        for sample in range(spec["idle_samples"]):
            parse_idle_metrics(_run(ops, ["curl", "--fail", "--silent", "--show-error",
                                         "--max-time", "5", spec["metrics_url"]]))
            if sample + 1 < spec["idle_samples"]:
                ops.sleep(spec["idle_interval_seconds"])
        idle = True
    empty = False
    if phase in {"stopped", "restore_drained"}:
        empty = all(not _run(ops, _node_argv(spec, node, ["nvidia-smi",
                    "--query-compute-apps=pid", "--format=csv,noheader,nounits"])) for node in nodes)
    return {"phase": phase, "policy": policy, "publication_ok": True,
            "protected_ok": True, "node_count": len(nodes), **services,
            "idle": idle, "gpus_empty": empty,
            "glm_identity": glm_identity,
            "hold": _optional_json(ops, spec["hold_path"]),
            "legacy_hold_absent": _optional_json(ops, spec["legacy_hold_path"]) is None,
            "requests_absent": all(_optional_json(ops, spec[key]) is None
                                   for key in ("request_path", "legacy_request_path")),
            "now": dt.datetime.now(dt.timezone.utc)}


def check(phase, manifest, ops):
    """Fresh read-only guards immediately before each local transition."""
    try:
        require(phase in {"before_hold", "before_stop", "stopped", "before_restore",
                          "restore_drained", "restored"}, "Unknown preflight phase")
        return validate_preflight(manifest["preflight"], collect_preflight(manifest, ops, phase))
    except PreflightError:
        raise
    except Exception as exc:
        raise PreflightError("Unknown preflight state: " + type(exc).__name__) from exc
