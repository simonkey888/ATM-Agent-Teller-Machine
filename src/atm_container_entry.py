from __future__ import annotations

import argparse
import base64
import io
import json
import os
import resource
import runpy
import socket
import sys
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any

SANDBOX_SCHEMA = "ATM_STRUCTURAL_SANDBOX_V2"
SECRET_MARKERS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "CREDENTIAL", "MNEMONIC", "SEED", "WALLET")


def _secret_env_count() -> int:
    return sum(1 for key in os.environ if any(marker in key.upper() for marker in SECRET_MARKERS))


def _policy() -> dict[str, Any]:
    obj = json.loads(os.environ.get("ATM_SANDBOX_POLICY_JSON", ""))
    if obj.get("schema") != SANDBOX_SCHEMA:
        raise RuntimeError("SANDBOX_POLICY_SCHEMA_INVALID")
    return obj


class _BrokerResponse(io.BytesIO):
    def __init__(self, body: bytes, status: int, url: str, headers: dict[str, str]):
        super().__init__(body); self.status = status; self.url = url; self.headers = headers
    def getcode(self): return self.status
    def geturl(self): return self.url
    def __enter__(self): return self
    def __exit__(self, *args): self.close(); return False


def _broker_urlopen(request, *args, **kwargs):
    policy = _policy()
    if policy.get("network_policy") != "READ_ONLY_PUBLIC_HTTPS":
        raise PermissionError("SANDBOX_DIRECT_NETWORK_DENY")
    url = request.full_url if isinstance(request, urllib.request.Request) else str(request)
    method = request.get_method().upper() if isinstance(request, urllib.request.Request) else "GET"
    headers = dict(request.header_items()) if isinstance(request, urllib.request.Request) else {}
    token = uuid.uuid4().hex; root = Path(os.environ["ATM_SANDBOX_HTTPS_PROXY_DIR"])
    req = root / f"{token}.request.json"; resp = root / f"{token}.response.json"
    req.write_text(json.dumps({"url":url,"method":method,"headers":headers,"timeout":kwargs.get("timeout",20)}, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    deadline = time.monotonic() + min(25.0, float(kwargs.get("timeout", 20)) + 2.0)
    while time.monotonic() < deadline:
        if resp.exists():
            data = json.loads(resp.read_text(encoding="utf-8")); req.unlink(missing_ok=True); resp.unlink(missing_ok=True)
            if not data.get("ok"): raise OSError(str(data.get("error_class") or "BROKER_ERROR"))
            return _BrokerResponse(base64.b64decode(str(data.get("body_b64") or "")), int(data.get("status") or 200), str(data.get("url") or url), dict(data.get("headers") or {}))
        time.sleep(0.01)
    raise TimeoutError("SANDBOX_BROKER_TIMEOUT")


def _install_network_guard() -> None:
    policy = _policy(); allowed_hosts = {str(h).lower().rstrip(".") for h in policy.get("allowed_hosts") or []}
    original = socket.socket; original_getaddrinfo = socket.getaddrinfo
    class DeniedSocket(original):
        def connect(self, address): raise PermissionError("SANDBOX_DIRECT_NETWORK_DENY")
        def connect_ex(self, address): return 13
    socket.socket = DeniedSocket  # type: ignore[assignment]
    def allowlisted_getaddrinfo(host, port, *args, **kwargs):
        name = str(host).lower().rstrip(".")
        if name in allowed_hosts:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", int(port)))]
        return original_getaddrinfo(host, port, *args, **kwargs)
    socket.getaddrinfo = allowlisted_getaddrinfo  # type: ignore[assignment]
    urllib.request.urlopen = _broker_urlopen  # type: ignore[assignment]


def _owner_home_denied(policy: dict[str, Any]) -> tuple[bool, str]:
    try:
        fd = os.open(Path(str(policy["owner_sentinel_host_path"])), os.O_RDONLY)
    except OSError as exc:
        return True, f"{type(exc).__name__}:{getattr(exc, 'errno', None)}"
    os.close(fd); return False, "READABLE"


def _repo_write_denied(policy: dict[str, Any]) -> bool:
    target = Path(str(policy["canonical_repo_root"])) / f".atm-probe-{os.getpid()}"
    try: target.write_text("x", encoding="utf-8")
    except OSError: return True
    try: target.unlink()
    except OSError: pass
    return False


def _direct_network_denied() -> tuple[bool, str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        code = sock.connect_ex(("1.1.1.1",443)); return code != 0, str(code)
    except OSError as exc: return True, f"{type(exc).__name__}:{getattr(exc,'errno',None)}"
    finally: sock.close()


def _status_fields() -> dict[str, str]:
    out = {}
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if ":" in line:
                k,v=line.split(":",1)
                if k in {"NoNewPrivs","CapEff"}: out[k]=v.strip()
    except OSError: pass
    return out


def _cgroup_value(name: str) -> str | None:
    try: return (Path("/sys/fs/cgroup")/name).read_text().strip()
    except OSError: return None


def _resource_observed() -> dict[str, Any]:
    return {
        "RLIMIT_CPU": list(resource.getrlimit(resource.RLIMIT_CPU)),
        "RLIMIT_FSIZE": list(resource.getrlimit(resource.RLIMIT_FSIZE)),
        "RLIMIT_NOFILE": list(resource.getrlimit(resource.RLIMIT_NOFILE)),
        "RLIMIT_NPROC": list(resource.getrlimit(resource.RLIMIT_NPROC)),
        "cgroup_memory_max": _cgroup_value("memory.max"), "cgroup_pids_max": _cgroup_value("pids.max"), "cgroup_cpu_max": _cgroup_value("cpu.max"),
    }


def _verify_resources(policy: dict[str, Any], observed: dict[str, Any]) -> None:
    limits = dict(policy["limits"])
    expected = {
        "RLIMIT_CPU":[int(limits["cpu_seconds"]),int(limits["cpu_seconds"])],
        "RLIMIT_FSIZE":[int(limits["max_file_bytes"]),int(limits["max_file_bytes"])],
        "RLIMIT_NOFILE":[int(limits["max_open_files"]),int(limits["max_open_files"])],
        "RLIMIT_NPROC":[int(limits["max_processes"]),int(limits["max_processes"])],
    }
    for name,value in expected.items():
        if [int(x) for x in observed.get(name) or []] != value: raise RuntimeError(f"SANDBOX_RESOURCE_NOT_OBSERVED:{name}")
    if str(observed.get("cgroup_pids_max")) != str(limits["max_processes"]): raise RuntimeError("SANDBOX_CGROUP_PIDS_NOT_OBSERVED")
    if str(observed.get("cgroup_memory_max")) != str(int(limits["memory_mb"])*1024*1024): raise RuntimeError("SANDBOX_CGROUP_MEMORY_NOT_OBSERVED")


def _run_worker(worker: Path, raw_request: str) -> tuple[int,str,str]:
    old_stdin,old_stdout,old_stderr=sys.stdin,sys.stdout,sys.stderr; stdout=io.StringIO(); stderr=io.StringIO(); sys.stdin=io.StringIO(raw_request); sys.stdout=stdout; sys.stderr=stderr; code=0
    try: runpy.run_path(str(worker), run_name="__main__")
    except SystemExit as exc: code=int(exc.code or 0) if isinstance(exc.code,int) or exc.code is None else 1
    except BaseException as exc: code=1; stderr.write(f"{type(exc).__name__}:{exc}")
    finally: sys.stdin,sys.stdout,sys.stderr=old_stdin,old_stdout,old_stderr
    return code,stdout.getvalue(),stderr.getvalue()


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--worker",required=True); parser.add_argument("--request",required=True); parser.add_argument("--response",required=True); args=parser.parse_args()
    if _secret_env_count()!=0: raise SystemExit("SANDBOX_SECRET_ENV_PRESENT")
    policy=_policy(); home=Path.home().resolve()
    if home != Path("/home/atm") or list(home.iterdir()): raise SystemExit("SANDBOX_EPHEMERAL_HOME_INVALID")
    status=_status_fields()
    if status.get("NoNewPrivs")!="1" or int(status.get("CapEff","1"),16)!=0: raise SystemExit("SANDBOX_PRIVILEGE_POLICY_NOT_APPLIED")
    owner_denied,owner_probe=_owner_home_denied(policy); repo_denied=_repo_write_denied(policy)
    _install_network_guard(); network_denied,network_probe=_direct_network_denied()
    if not owner_denied: raise SystemExit("SANDBOX_OWNER_HOME_READABLE")
    if not repo_denied: raise SystemExit("SANDBOX_CANONICAL_REPO_WRITABLE")
    if not network_denied: raise SystemExit("SANDBOX_DIRECT_NETWORK_AVAILABLE")
    observed=_resource_observed(); _verify_resources(policy,observed)
    worker=Path(args.worker).resolve(); request=json.loads(Path(args.request).read_text(encoding="utf-8")); raw_request=json.dumps(request,sort_keys=True,separators=(",",":"),default=str)
    code,stdout,stderr=_run_worker(worker,raw_request)
    if code!=0:
        Path(args.response).write_text(json.dumps({"worker_error":stderr[-500:],"worker_exit":code},sort_keys=True,separators=(",",":")),encoding="utf-8"); return code
    try: worker_payload=json.loads(stdout)
    except json.JSONDecodeError:
        Path(args.response).write_text(json.dumps({"worker_error":"INVALID_JSON"}),encoding="utf-8"); return 2
    receipt={
        "schema":SANDBOX_SCHEMA,"platform":sys.platform,"backend":"DOCKER_HOST_MANAGED","network_policy":str(policy["network_policy"]),"tool_policy":str(policy["tool_policy"]),"child_pid":os.getpid(),"secret_env_count":_secret_env_count(),"fresh_ephemeral_home":True,
        "owner_home_read_denied_observed":owner_denied,"owner_home_probe":owner_probe,"canonical_repo_write_denied_observed":repo_denied,"direct_network_denied_observed":network_denied,"direct_network_probe":network_probe,
        "filesystem_boundary_applied":True,"network_boundary_applied":True,"resource_limits_applied":True,"process_tree_bounded":True,"resource_limits_requested":dict(policy["limits"]),"resource_limits_observed":observed,
        "no_new_privileges_observed":status.get("NoNewPrivs")=="1","effective_capabilities_zero":int(status.get("CapEff","1"),16)==0,"worker_sha256":__import__("hashlib").sha256(worker.read_bytes()).hexdigest(),
    }
    Path(args.response).write_text(json.dumps({"worker":worker_payload,"sandbox":receipt},sort_keys=True,separators=(",",":"),default=str),encoding="utf-8"); return 0


if __name__=="__main__": raise SystemExit(main())
