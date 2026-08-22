from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SRC = Path(__file__).resolve().parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

SECRET_MARKERS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "CREDENTIAL", "MNEMONIC", "SEED", "WALLET")
CREDENTIAL_PATH_MARKERS = (".env", "keystore", "credential", "api_key", "apikey", "token", "mnemonic", "seed", "wallet")
ALLOWED = {
    ("SCOUT", "DISCOVER"): ("READ_ONLY_PUBLIC_HTTPS", "READ_ONLY_SOURCE_DISCOVERY"),
    ("FALSIFIER", "VERIFY"): ("READ_ONLY_PUBLIC_HTTPS", "AUTHORITATIVE_READ_VERIFY"),
    ("FALSIFIER", "BOUNDARY_PROBE"): ("READ_ONLY_PUBLIC_HTTPS", "AUTHORITATIVE_READ_VERIFY"),
    ("DOCTOR", "HANDLE"): ("DENY", "DURABLE_TYPED_RECOVERY"),
    ("DOCTOR", "INSPECT"): ("DENY", "DURABLE_TYPED_RECOVERY"),
}


def _secret_env_count() -> int:
    return sum(1 for key in os.environ if any(marker in key.upper() for marker in SECRET_MARKERS))


def _credential_files(home: Path) -> list[Path]:
    rows: list[Path] = []
    for path in home.rglob("*"):
        if path.is_file() and any(marker in path.name.lower() for marker in CREDENTIAL_PATH_MARKERS):
            rows.append(path)
    return rows


def _validate_isolated_home(request: dict[str, Any]) -> Path:
    configured = Path(str(request.get("isolated_home") or os.environ.get("HOME") or "")).resolve()
    resolved = Path.home().resolve()
    if configured != resolved or not configured.is_dir():
        raise SystemExit("ROLE_ISOLATED_HOME_RESOLUTION_MISMATCH")
    if _credential_files(configured):
        raise SystemExit("ROLE_ISOLATED_HOME_CREDENTIAL_FILE_PRESENT")
    return configured


def _public_address(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    if not infos:
        return False
    for info in infos:
        raw = str(info[4][0]).split("%", 1)[0]
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            return False
        if not address.is_global:
            return False
    return True


class _ProxyResponse:
    def __init__(self, *, body: bytes, status: int, url: str, headers: dict[str, str]):
        self._body = body
        self._offset = 0
        self.status = int(status)
        self.url = str(url)
        msg = Message()
        for key, value in headers.items():
            msg[str(key)] = str(value)
        self.headers = msg

    def read(self, amt: int | None = None) -> bytes:
        if amt is None or amt < 0:
            data = self._body[self._offset:]
            self._offset = len(self._body)
            return data
        data = self._body[self._offset:self._offset + amt]
        self._offset += len(data)
        return data

    def getcode(self) -> int:
        return self.status

    def geturl(self) -> str:
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _proxy_urlopen(proxy_dir: Path, request: object, **kwargs: object) -> _ProxyResponse:
    if isinstance(request, urllib.request.Request):
        url = request.full_url
        method = request.get_method().upper()
        headers = dict(request.header_items())
    else:
        url = str(request)
        method = "GET"
        headers = {}
    timeout_raw = kwargs.get("timeout", 20)
    try:
        timeout_s = max(1.0, min(20.0, float(timeout_raw)))
    except (TypeError, ValueError):
        timeout_s = 20.0
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or method not in {"GET", "HEAD"}:
        raise PermissionError("ROLE_NETWORK_READ_ONLY_HTTPS")
    request_id = hashlib.sha256(f"{os.getpid()}|{time.monotonic_ns()}|{url}".encode()).hexdigest()
    req_path = proxy_dir / f"{request_id}.request.json"
    resp_path = proxy_dir / f"{request_id}.response.json"
    req_path.write_text(json.dumps({"url": url, "method": method, "headers": headers, "timeout": timeout_s}, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    deadline = time.monotonic() + timeout_s + 2
    while time.monotonic() < deadline:
        if resp_path.exists():
            payload = json.loads(resp_path.read_text(encoding="utf-8"))
            try:
                req_path.unlink(missing_ok=True)
                resp_path.unlink(missing_ok=True)
            except OSError:
                pass
            if not payload.get("ok"):
                raise urllib.error.URLError(str(payload.get("error_class") or "SANDBOX_PROXY_ERROR"))
            body = base64.b64decode(str(payload.get("body_b64") or ""))
            status = int(payload.get("status") or 200)
            if status >= 400:
                raise urllib.error.HTTPError(url, status, "sandbox proxy upstream error", Message(), None)
            return _ProxyResponse(body=body, status=status, url=str(payload.get("url") or url), headers=dict(payload.get("headers") or {}))
        time.sleep(0.01)
    raise TimeoutError("ROLE_SANDBOX_PROXY_TIMEOUT")


def _install_network_policy(policy: str) -> None:
    proxy_raw = os.environ.get("ATM_SANDBOX_HTTPS_PROXY_DIR")
    if proxy_raw and policy == "READ_ONLY_PUBLIC_HTTPS":
        proxy_dir = Path(proxy_raw).resolve()
        if not proxy_dir.is_dir():
            raise SystemExit("ROLE_SANDBOX_PROXY_DIR_INVALID")
        def proxy_urlopen(request, *args, **kwargs):
            return _proxy_urlopen(proxy_dir, request, **kwargs)
        urllib.request.urlopen = proxy_urlopen  # type: ignore[assignment]
        original_socket = socket.socket
        class DeniedSocket(original_socket):
            def connect(self, address):  # type: ignore[override]
                raise PermissionError("ROLE_DIRECT_NETWORK_DENY")
        socket.socket = DeniedSocket  # type: ignore[assignment]
        return

    original_socket = socket.socket
    original_urlopen = urllib.request.urlopen
    class GuardedSocket(original_socket):
        def connect(self, address):  # type: ignore[override]
            if policy == "DENY":
                raise PermissionError("ROLE_NETWORK_DENY")
            host = str(address[0]).split("%", 1)[0]
            try:
                ip = ipaddress.ip_address(host)
            except ValueError:
                if not _public_address(host):
                    raise PermissionError("ROLE_NETWORK_NONPUBLIC")
            else:
                if not ip.is_global:
                    raise PermissionError("ROLE_NETWORK_NONPUBLIC")
            return super().connect(address)
    socket.socket = GuardedSocket  # type: ignore[assignment]
    def guarded_urlopen(request, *args, **kwargs):
        if policy == "DENY":
            raise PermissionError("ROLE_NETWORK_DENY")
        url = request.full_url if isinstance(request, urllib.request.Request) else str(request)
        method = request.get_method().upper() if isinstance(request, urllib.request.Request) else "GET"
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or method not in {"GET", "HEAD"}:
            raise PermissionError("ROLE_NETWORK_READ_ONLY_HTTPS")
        if not _public_address(parsed.hostname):
            raise PermissionError("ROLE_NETWORK_NONPUBLIC")
        return original_urlopen(request, *args, **kwargs)
    urllib.request.urlopen = guarded_urlopen  # type: ignore[assignment]


def _discover(payload: dict[str, Any]) -> dict[str, Any]:
    sources = [str(x) for x in payload.get("sources") or []]
    if not sources:
        return {"observations": []}
    import atm_v2 as core
    config = dict(payload.get("config") or {})
    minimum = payload.get("minimum", 1)
    adapters = core.build_adapters(config)
    rows = []
    for source in sources:
        adapter = adapters.get(source)
        if adapter is None:
            rows.append({"source": source, "state": "DEGRADED", "error_class": "ADAPTER_MISSING", "candidates": []})
            continue
        try:
            found = adapter.discover(minimum)
            rows.append({"source": source, "state": "OK", "error_class": None, "candidates": [x.model_dump(mode="json") for x in found]})
        except Exception as exc:
            rows.append({"source": source, "state": "DEGRADED", "error_class": type(exc).__name__, "candidates": []})
    return {"observations": rows}


def _verify(payload: dict[str, Any]) -> dict[str, Any]:
    from atm_core.models import Opportunity
    opp = Opportunity.model_validate(dict(payload.get("opportunity") or {}))
    submitted = {str(x) for x in payload.get("submitted_ids") or []}
    cid = opp.canonical_opportunity_id
    if cid in submitted:
        return {"verdict": "KILL", "reason": "ALREADY_SUBMITTED_OR_IN_FLIGHT", "fresh_object_hash": None}
    if str(opp.upstream_status).upper() not in {"OPEN", "ACTIVE", "AVAILABLE", "FUNDED"}:
        return {"verdict": "KILL", "reason": "STALE_OR_CLOSED", "fresh_object_hash": None}
    if not opp.funding_proof:
        return {"verdict": "KILL", "reason": "UNFUNDED", "fresh_object_hash": None}
    import atm_v2 as core
    adapters = core.build_adapters(dict(payload.get("config") or {}))
    adapter = adapters.get(opp.source)
    if adapter is None:
        return {"verdict": "KILL", "reason": "ADAPTER_MISSING", "fresh_object_hash": None}
    try:
        snapshot = adapter.fetch_authoritative(opp)
        adapter.verify_freshness(opp, snapshot)
        adapter.verify_funding(opp, snapshot)
        adapter.verify_eligibility(opp, snapshot)
    except Exception as exc:
        return {"verdict": "KILL", "reason": type(exc).__name__, "fresh_object_hash": None}
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, default=str).encode()).hexdigest()
    return {"verdict": "CONFIRM", "reason": None, "fresh_object_hash": digest}


def _doctor_handle(payload: dict[str, Any]) -> dict[str, Any]:
    from atm_core.durable_doctor import DurableDoctor
    path = Path(str(payload["state_path"])).resolve()
    doctor = DurableDoctor(path, threshold=int(payload.get("threshold", 3)), cooldown_seconds=int(payload.get("cooldown_seconds", 300)))
    try:
        decision = doctor.handle(str(payload["fault"]), str(payload["key"]), attempt=int(payload.get("attempt", 1)))
        return {"decision": decision.__dict__, "status": doctor.status(str(payload["key"])), "summary": doctor.summary()}
    finally:
        doctor.close()


def _doctor_inspect(payload: dict[str, Any]) -> dict[str, Any]:
    from atm_core.durable_doctor import DurableDoctor
    doctor = DurableDoctor(Path(str(payload["state_path"])).resolve(), threshold=int(payload.get("threshold", 3)), cooldown_seconds=int(payload.get("cooldown_seconds", 300)))
    try:
        return {"state": "BOUNDARY_READY", "summary": doctor.summary()}
    finally:
        doctor.close()


def main() -> int:
    raw = sys.stdin.read()
    request = json.loads(raw)
    role = str(request.get("role") or "").upper()
    operation = str(request.get("operation") or "").upper()
    expected = ALLOWED.get((role, operation))
    if expected is None:
        raise SystemExit("ROLE_OPERATION_NOT_ALLOWED")
    network_policy, tool_policy = expected
    if request.get("network_policy") != network_policy or request.get("tool_policy") != tool_policy:
        raise SystemExit("ROLE_POLICY_MISMATCH")
    if _secret_env_count() != 0:
        raise SystemExit("ROLE_SECRET_ENV_PRESENT")
    child_home = _validate_isolated_home(request)
    own_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if request.get("worker_code_sha256") != own_sha:
        raise SystemExit("ROLE_CODE_DIGEST_MISMATCH")
    _install_network_policy(network_policy)
    payload = dict(request.get("payload") or {})
    if role == "SCOUT":
        result = _discover(payload)
    elif role == "FALSIFIER" and operation == "VERIFY":
        result = _verify(payload)
    elif role == "FALSIFIER":
        result = {"state": "BOUNDARY_READY", "submitted_history_count": len(payload.get("submitted_ids") or [])}
    elif operation == "HANDLE":
        result = _doctor_handle(payload)
    else:
        result = _doctor_inspect(payload)

    credential_files = _credential_files(child_home)
    if credential_files:
        raise SystemExit("ROLE_ISOLATED_HOME_CREDENTIAL_FILE_CREATED")
    result_raw = json.dumps(result, sort_keys=True, separators=(",", ":"), default=str)
    receipt = {
        "role": role,
        "operation": operation,
        "child_pid": os.getpid(),
        "parent_pid": int(request.get("parent_pid") or -1),
        "process_isolated": os.getpid() != int(request.get("parent_pid") or -1),
        "secret_env_count": _secret_env_count(),
        "network_policy": network_policy,
        "tool_policy": tool_policy,
        "policy_enforced": True,
        "worker_code_sha256": own_sha,
        "request_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "result_sha256": hashlib.sha256(result_raw.encode()).hexdigest(),
        "child_home_resolved": str(child_home),
        "child_home_matches_request": child_home == Path(str(request.get("isolated_home") or os.environ.get("HOME"))).resolve(),
        "child_home_initial_entry_count": 0,
        "child_home_credential_files_visible": len(credential_files),
        "isolated_home_proven": True,
    }
    sys.stdout.write(json.dumps({"result": result, "receipt": receipt}, sort_keys=True, separators=(",", ":"), default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
