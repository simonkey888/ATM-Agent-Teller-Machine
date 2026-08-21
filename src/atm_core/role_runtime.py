from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


ROLE_RUNTIME_SCHEMA = "ATM_ISOLATED_ROLE_RUNTIME_V1"
ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "atm_role_worker.py"
SAFE_ENV_KEYS = ("SYSTEMROOT", "WINDIR", "SSL_CERT_FILE", "SSL_CERT_DIR")
SECRET_MARKERS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "CREDENTIAL", "MNEMONIC", "SEED", "WALLET")


def _clean_env() -> dict[str, str]:
    env = {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    for key in SAFE_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            env[key] = value
    if any(any(marker in key.upper() for marker in SECRET_MARKERS) for key in env):
        raise RuntimeError("ROLE_ENV_SECRET_FILTER_FAILED")
    return env


def worker_code_sha256() -> str:
    return hashlib.sha256(WORKER.read_bytes()).hexdigest()


@dataclass(frozen=True)
class RoleReceipt:
    role: str
    operation: str
    child_pid: int
    parent_pid: int
    process_isolated: bool
    secret_env_count: int
    network_policy: str
    tool_policy: str
    policy_enforced: bool
    worker_code_sha256: str
    request_sha256: str
    result_sha256: str

    @property
    def proven(self) -> bool:
        return all((
            self.process_isolated,
            self.secret_env_count == 0,
            self.policy_enforced,
            self.child_pid > 0,
            self.child_pid != self.parent_pid,
            self.worker_code_sha256 == worker_code_sha256(),
        ))


class RoleReceiptStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS role_receipts(
               receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
               role TEXT NOT NULL, operation TEXT NOT NULL, child_pid INTEGER NOT NULL,
               parent_pid INTEGER NOT NULL, process_isolated INTEGER NOT NULL,
               secret_env_count INTEGER NOT NULL, network_policy TEXT NOT NULL,
               tool_policy TEXT NOT NULL, policy_enforced INTEGER NOT NULL,
               worker_code_sha256 TEXT NOT NULL, request_sha256 TEXT NOT NULL,
               result_sha256 TEXT NOT NULL, receipt_json TEXT NOT NULL,
               created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"""
        )

    def close(self) -> None:
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.conn.close()

    def put(self, receipt: RoleReceipt) -> None:
        raw = json.dumps(receipt.__dict__, sort_keys=True)
        self.conn.execute(
            "INSERT INTO role_receipts(role,operation,child_pid,parent_pid,process_isolated,secret_env_count,network_policy,tool_policy,policy_enforced,worker_code_sha256,request_sha256,result_sha256,receipt_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (receipt.role, receipt.operation, receipt.child_pid, receipt.parent_pid, int(receipt.process_isolated), receipt.secret_env_count, receipt.network_policy, receipt.tool_policy, int(receipt.policy_enforced), receipt.worker_code_sha256, receipt.request_sha256, receipt.result_sha256, raw),
        )

    def latest_by_role(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        rows = self.conn.execute("SELECT role,receipt_json FROM role_receipts ORDER BY receipt_id DESC").fetchall()
        for row in rows:
            role = str(row["role"])
            if role not in out:
                out[role] = json.loads(str(row["receipt_json"]))
        return out

    def summary(self) -> dict[str, Any]:
        rows = self.latest_by_role()
        required = ("SCOUT", "FALSIFIER", "DOCTOR")
        proven = []
        for role in required:
            raw = rows.get(role)
            if not raw:
                continue
            try:
                if RoleReceipt(**raw).proven:
                    proven.append(role)
            except Exception:
                continue
        return {
            "schema": ROLE_RUNTIME_SCHEMA,
            "required_roles": list(required),
            "proven_roles": proven,
            "process_boundary_proven": set(proven) == set(required),
            "secretless_env_proven": set(proven) == set(required),
            "tool_network_policy_proven": set(proven) == set(required),
            "latest_receipts": rows,
        }


class IsolatedRoleRunner:
    """Spawn fixed typed role operations with an empty-secret environment.

    The child is trusted repository code, not arbitrary candidate code. It receives no
    marketplace/model/wallet credentials and cannot request arbitrary Python/shell work.
    """

    def __init__(self, receipt_db: Path, *, timeout_seconds: int = 120):
        self.receipt_db = Path(receipt_db)
        self.timeout_seconds = max(5, min(300, int(timeout_seconds)))

    def run(self, role: str, operation: str, payload: Mapping[str, Any], *, network_policy: str, tool_policy: str) -> dict[str, Any]:
        request = {
            "schema": ROLE_RUNTIME_SCHEMA,
            "role": str(role).upper(),
            "operation": str(operation).upper(),
            "payload": dict(payload),
            "network_policy": str(network_policy),
            "tool_policy": str(tool_policy),
            "parent_pid": os.getpid(),
            "worker_code_sha256": worker_code_sha256(),
        }
        raw_request = json.dumps(request, sort_keys=True, separators=(",", ":"), default=str)
        completed = subprocess.run(
            [sys.executable, "-I", str(WORKER)],
            input=raw_request,
            text=True,
            capture_output=True,
            env=_clean_env(),
            cwd=str(ROOT),
            timeout=self.timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            tail = (completed.stderr or completed.stdout or "")[-800:]
            raise RuntimeError(f"ISOLATED_ROLE_FAILED:{role}:{operation}:{completed.returncode}:{tail}")
        try:
            response = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("ISOLATED_ROLE_INVALID_RECEIPT") from exc
        receipt_raw = response.get("receipt") if isinstance(response, dict) else None
        result = response.get("result") if isinstance(response, dict) else None
        if not isinstance(receipt_raw, dict) or not isinstance(result, dict):
            raise RuntimeError("ISOLATED_ROLE_MISSING_RECEIPT")
        receipt = RoleReceipt(**receipt_raw)
        expected_request = hashlib.sha256(raw_request.encode()).hexdigest()
        expected_result = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
        if receipt.request_sha256 != expected_request or receipt.result_sha256 != expected_result or not receipt.proven:
            raise RuntimeError("ISOLATED_ROLE_RECEIPT_VERIFICATION_FAILED")
        store = RoleReceiptStore(self.receipt_db)
        try:
            store.put(receipt)
        finally:
            store.close()
        return result
