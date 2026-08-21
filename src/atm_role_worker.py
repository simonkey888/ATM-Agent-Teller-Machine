from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import socket
import sys
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SRC = Path(__file__).resolve().parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

SECRET_MARKERS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "CREDENTIAL", "MNEMONIC", "SEED", "WALLET")
ALLOWED = {
    ("SCOUT", "DISCOVER"): ("READ_ONLY_PUBLIC_HTTPS", "READ_ONLY_SOURCE_DISCOVERY"),
    ("FALSIFIER", "VERIFY"): ("READ_ONLY_PUBLIC_HTTPS", "AUTHORITATIVE_READ_VERIFY"),
    ("DOCTOR", "HANDLE"): ("DENY", "DURABLE_TYPED_RECOVERY"),
}


def _secret_env_count() -> int:
    return sum(1 for key in os.environ if any(marker in key.upper() for marker in SECRET_MARKERS))


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


def _install_network_policy(policy: str) -> None:
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
    import atm_v2 as core

    config = dict(payload.get("config") or {})
    sources = [str(x) for x in payload.get("sources") or []]
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
    import atm_v2 as core
    from atm_core.models import Opportunity

    raw = dict(payload.get("opportunity") or {})
    opp = Opportunity.model_validate(raw)
    submitted = {str(x) for x in payload.get("submitted_ids") or []}
    cid = opp.canonical_opportunity_id
    if cid in submitted:
        return {"verdict": "KILL", "reason": "ALREADY_SUBMITTED_OR_IN_FLIGHT", "fresh_object_hash": None}
    if str(opp.upstream_status).upper() not in {"OPEN", "ACTIVE", "AVAILABLE", "FUNDED"}:
        return {"verdict": "KILL", "reason": "STALE_OR_CLOSED", "fresh_object_hash": None}
    if not opp.funding_proof:
        return {"verdict": "KILL", "reason": "UNFUNDED", "fresh_object_hash": None}
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


def _doctor(payload: dict[str, Any]) -> dict[str, Any]:
    from atm_core.durable_doctor import DurableDoctor

    path = Path(str(payload["state_path"])).resolve()
    doctor = DurableDoctor(path, threshold=int(payload.get("threshold", 3)), cooldown_seconds=int(payload.get("cooldown_seconds", 300)))
    try:
        decision = doctor.handle(str(payload["fault"]), str(payload["key"]), attempt=int(payload.get("attempt", 1)))
        return {"decision": decision.__dict__, "status": doctor.status(str(payload["key"])), "summary": doctor.summary()}
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
    own_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if request.get("worker_code_sha256") != own_sha:
        raise SystemExit("ROLE_CODE_DIGEST_MISMATCH")
    _install_network_policy(network_policy)
    if role == "SCOUT":
        result = _discover(dict(request.get("payload") or {}))
    elif role == "FALSIFIER":
        result = _verify(dict(request.get("payload") or {}))
    else:
        result = _doctor(dict(request.get("payload") or {}))
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
    }
    sys.stdout.write(json.dumps({"result": result, "receipt": receipt}, sort_keys=True, separators=(",", ":"), default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
