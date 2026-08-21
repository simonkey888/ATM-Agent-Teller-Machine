from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .sandbox_boundary import SANDBOX_SCHEMA, SandboxLimits, SandboxPolicy, StructuralSandbox

ROLE_RUNTIME_SCHEMA = "ATM_ISOLATED_ROLE_RUNTIME_V2"
ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "atm_role_worker.py"
SECRET_MARKERS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "CREDENTIAL", "MNEMONIC", "SEED", "WALLET")


def _scrub_secret_fields(value: Any) -> Any:
    """Remove credential-shaped configuration before a request crosses into a role child."""
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, raw in value.items():
            name = str(key)
            if any(marker in name.upper() for marker in SECRET_MARKERS):
                continue
            out[name] = _scrub_secret_fields(raw)
        return out
    if isinstance(value, list):
        return [_scrub_secret_fields(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_scrub_secret_fields(item) for item in value)
    return value


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
    child_home_resolved: str = ""
    child_home_matches_request: bool = False
    child_home_initial_entry_count: int = -1
    child_home_credential_files_visible: int = -1
    isolated_home_proven: bool = False
    structural_sandbox_schema: str = ""
    structural_sandbox_proven: bool = False
    owner_home_read_denied_observed: bool = False
    canonical_repo_write_denied_observed: bool = False
    direct_network_denied_observed: bool = False
    filesystem_boundary_applied: bool = False
    network_boundary_applied: bool = False
    resource_limits_applied: bool = False
    process_tree_bounded: bool = False
    resource_limits_observed: dict[str, Any] | None = None
    sandbox_receipt_digest: str = ""

    @property
    def proven(self) -> bool:
        return all((
            self.process_isolated,
            self.secret_env_count == 0,
            self.policy_enforced,
            self.child_pid > 0,
            self.child_pid != self.parent_pid,
            self.worker_code_sha256 == worker_code_sha256(),
            bool(self.child_home_resolved),
            self.child_home_matches_request,
            self.child_home_initial_entry_count == 0,
            self.child_home_credential_files_visible == 0,
            self.isolated_home_proven,
            self.structural_sandbox_schema == SANDBOX_SCHEMA,
            self.structural_sandbox_proven,
            self.owner_home_read_denied_observed,
            self.canonical_repo_write_denied_observed,
            self.direct_network_denied_observed,
            self.filesystem_boundary_applied,
            self.network_boundary_applied,
            self.resource_limits_applied,
            self.process_tree_bounded,
            bool(self.resource_limits_observed),
            bool(self.sandbox_receipt_digest),
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
        all_proven = set(proven) == set(required)
        return {
            "schema": ROLE_RUNTIME_SCHEMA,
            "required_roles": list(required),
            "proven_roles": proven,
            "process_boundary_proven": all_proven,
            "secretless_env_proven": all_proven,
            "tool_network_policy_proven": all_proven,
            "sterile_home_proven": all_proven,
            "structural_sandbox_proven": all_proven,
            "owner_home_read_denied_proven": all_proven,
            "resource_limits_proven": all_proven,
            "latest_receipts": rows,
        }


class IsolatedRoleRunner:
    """Run fixed typed roles in the one shared structural sandbox boundary."""

    def __init__(self, receipt_db: Path, *, timeout_seconds: int = 120):
        self.receipt_db = Path(receipt_db)
        self.timeout_seconds = max(5, min(300, int(timeout_seconds)))
        self.sandbox = StructuralSandbox(source_root=ROOT)

    def run(self, role: str, operation: str, payload: Mapping[str, Any], *, network_policy: str, tool_policy: str) -> dict[str, Any]:
        request = {
            "schema": ROLE_RUNTIME_SCHEMA,
            "role": str(role).upper(),
            "operation": str(operation).upper(),
            "payload": _scrub_secret_fields(dict(payload)),
            "network_policy": str(network_policy),
            "tool_policy": str(tool_policy),
            "parent_pid": os.getpid(),
            "worker_code_sha256": worker_code_sha256(),
        }
        writable: tuple[Path, ...] = ()
        if request["role"] == "DOCTOR":
            state_raw = str(dict(request["payload"]).get("state_path") or "").strip()
            if state_raw:
                writable = (Path(state_raw).resolve().parent,)
        limits = SandboxLimits(
            cpu_seconds=min(30, self.timeout_seconds),
            memory_mb=512,
            max_file_bytes=8 * 1024 * 1024,
            max_open_files=64,
            max_processes=16,
            wall_seconds=self.timeout_seconds,
        )
        envelope = self.sandbox.run(
            worker_name="atm_role_worker.py",
            request=request,
            policy=SandboxPolicy(network_policy=str(network_policy), tool_policy=str(tool_policy), limits=limits),
            writable_paths=writable,
        )
        worker_envelope = envelope.get("worker")
        sandbox = envelope.get("sandbox")
        if not isinstance(worker_envelope, dict) or not isinstance(sandbox, dict):
            raise RuntimeError("ISOLATED_ROLE_MISSING_STRUCTURAL_RECEIPT")
        receipt_raw = worker_envelope.get("receipt")
        result = worker_envelope.get("result")
        if not isinstance(receipt_raw, dict) or not isinstance(result, dict):
            raise RuntimeError("ISOLATED_ROLE_MISSING_RECEIPT")
        receipt_raw = dict(receipt_raw)
        receipt_raw.update({
            "structural_sandbox_schema": sandbox.get("schema"),
            "structural_sandbox_proven": True,
            "owner_home_read_denied_observed": bool(sandbox.get("owner_home_read_denied_observed")),
            "canonical_repo_write_denied_observed": bool(sandbox.get("canonical_repo_write_denied_observed")),
            "direct_network_denied_observed": bool(sandbox.get("direct_network_denied_observed")),
            "filesystem_boundary_applied": bool(sandbox.get("filesystem_boundary_applied")),
            "network_boundary_applied": bool(sandbox.get("network_boundary_applied")),
            "resource_limits_applied": bool(sandbox.get("resource_limits_applied")),
            "process_tree_bounded": bool(sandbox.get("process_tree_bounded")),
            "resource_limits_observed": sandbox.get("resource_limits_observed"),
            "sandbox_receipt_digest": str(envelope.get("sandbox_receipt_digest") or ""),
            "child_home_initial_entry_count": int(sandbox.get("child_home_initial_entry_count", -1)),
            "child_home_resolved": str(sandbox.get("child_home_resolved") or receipt_raw.get("child_home_resolved") or ""),
        })
        receipt = RoleReceipt(**receipt_raw)
        expected_result = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
        if receipt.result_sha256 != expected_result or not receipt.proven:
            raise RuntimeError("ISOLATED_ROLE_RECEIPT_VERIFICATION_FAILED")
        store = RoleReceiptStore(self.receipt_db)
        try:
            store.put(receipt)
        finally:
            store.close()
        return result
