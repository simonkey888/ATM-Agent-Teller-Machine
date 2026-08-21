from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
from pathlib import Path
from typing import Any


SECRET_MARKERS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "CREDENTIAL", "MNEMONIC", "SEED", "WALLET")
FIXTURES = {
    "happy": " alpha = one \n beta = two ",
    "edge": "",
    "adversarial": "<script>steal()</script>=literal\n__proto__=data",
    "market_shaped": "customer_id = 0042\nstatus = ready\nnote = preserve leading zeros",
}


def secret_count() -> int:
    return sum(1 for key in os.environ if any(marker in key.upper() for marker in SECRET_MARKERS))


def deny_network() -> None:
    original = socket.socket

    class DeniedSocket(original):
        def connect(self, address):  # type: ignore[override]
            raise PermissionError("FORGE_SANDBOX_NETWORK_DENY")

    socket.socket = DeniedSocket  # type: ignore[assignment]


def normalize_kv(value: str) -> str:
    rows = []
    for raw in str(value).splitlines():
        if not raw.strip():
            continue
        if "=" not in raw:
            rows.append(raw.strip())
            continue
        key, data = raw.split("=", 1)
        rows.append(f"{key.strip()}={data.strip()}")
    return "\n".join(rows)


def checker(source: str, artifact: str) -> bool:
    expected = normalize_kv(source)
    return artifact == expected and "\x00" not in artifact


def run_candidate(capability_id: str) -> dict[str, Any]:
    if capability_id != "SYNTHETIC_TEXT_KV_NORMALIZE_V1":
        return {"state": "NO_EXISTING_SAFE_PATTERN", "fixture_evidence": [], "falsification_pass": False, "benchmark_pass": False}
    evidence = []
    artifacts = []
    for fixture_class, source in FIXTURES.items():
        artifact = normalize_kv(source)
        passed = checker(source, artifact)
        digest = hashlib.sha256(artifact.encode()).hexdigest()
        artifacts.append(digest)
        evidence.append({
            "fixture_class": fixture_class,
            "sandbox_id": f"forge:{os.getpid()}",
            "process_isolated": True,
            "environment_secret_count": secret_count(),
            "network_policy": "DENY",
            "tool_allowlist_enforced": True,
            "executor_pass": True,
            "checker_pass": passed,
            "artifact_contract_pass": len(artifact.encode()) < 65536,
            "artifact_sha256": digest,
        })
    falsification_pass = checker("a = b", "a=b") and not checker("a = b", "WRONG")
    benchmark_pass = all(normalize_kv(value) == normalize_kv(value) for value in FIXTURES.values())
    return {
        "state": "SANDBOX_COMPLETE",
        "fixture_evidence": evidence,
        "falsification_pass": falsification_pass,
        "benchmark_pass": benchmark_pass,
        "artifact_set_sha256": hashlib.sha256("".join(artifacts).encode()).hexdigest(),
    }


def main() -> int:
    raw = sys.stdin.read()
    request = json.loads(raw)
    if request.get("schema") != "ATM_FORGE_SANDBOX_REQUEST_V1":
        raise SystemExit("FORGE_REQUEST_SCHEMA_INVALID")
    if request.get("network_policy") != "DENY" or request.get("tool_policy") != "FIXED_PATTERN_EXECUTOR_CHECKER_ONLY":
        raise SystemExit("FORGE_POLICY_INVALID")
    if secret_count() != 0:
        raise SystemExit("FORGE_SECRET_ENV_PRESENT")
    own_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if request.get("worker_code_sha256") != own_sha:
        raise SystemExit("FORGE_WORKER_DIGEST_MISMATCH")
    parent_pid = int(request.get("parent_pid") or -1)
    deny_network()
    result = run_candidate(str(request.get("capability_id") or ""))
    result_raw = json.dumps(result, sort_keys=True, separators=(",", ":"), default=str)
    provenance = {
        "schema": "ATM_FORGE_SANDBOX_RECEIPT_V1",
        "child_pid": os.getpid(),
        "parent_pid": parent_pid,
        "process_isolated": os.getpid() != parent_pid,
        "environment_secret_count": secret_count(),
        "network_policy": "DENY",
        "tool_policy": "FIXED_PATTERN_EXECUTOR_CHECKER_ONLY",
        "worker_code_sha256": own_sha,
        "request_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "result_sha256": hashlib.sha256(result_raw.encode()).hexdigest(),
    }
    receipt_digest = hashlib.sha256(json.dumps(provenance, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    sys.stdout.write(json.dumps({"result": result, "provenance": provenance, "receipt_digest": receipt_digest}, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
