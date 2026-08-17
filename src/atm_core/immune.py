from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .workers import canonical_hash, canonical_json


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


ECONOMIC_FIELDS = {"paid", "withdrawable", "withdrawable_usdc", "realized_withdrawable_usd", "external_accepted"}


class CriticExecution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    execution_job_id: str
    critic_worker_id: str
    implementer_worker_id: str
    acceptance_contract_hash: str
    artifact_hash: str
    evidence_hash: str
    limitations_hash: str
    verdict: str
    findings: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utcnow_iso)

    @field_validator("verdict")
    @classmethod
    def verdict_enum(cls, value: str) -> str:
        value = value.upper()
        if value not in {"POSITIVE", "NEGATIVE", "UNKNOWN"}:
            raise ValueError("invalid critic verdict")
        return value

    @model_validator(mode="after")
    def distinct_sibling(self) -> "CriticExecution":
        if self.critic_worker_id == self.implementer_worker_id:
            raise ValueError("critic must be a distinct sibling execution")
        return self

    @property
    def receipt_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))


class CriticPolicy:
    @staticmethod
    def required(reward_usd: float, risk_tier: str, reward_threshold_usd: float = 100.0) -> bool:
        return float(reward_usd) >= float(reward_threshold_usd) or str(risk_tier).upper() in {"HIGH", "CRITICAL"}

    @staticmethod
    def submit_allowed(required: bool, critic: CriticExecution | None) -> bool:
        if not required:
            return True
        return critic is not None and critic.verdict == "POSITIVE"


class FailureSignature(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signature_id: str
    class_name: str
    source: str
    evidence_refs: list[str]
    fingerprint: str
    observed_at: str
    detail: str


class BehaviorFingerprint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    execution_job_id: str
    worker_id: str
    worker_source_sha: str
    command_hashes: list[str]
    test_hashes: list[str]
    evidence_hashes: list[str]
    duration_bucket: str
    terminal_status: str
    error_signature: str | None = None
    created_at: str = Field(default_factory=utcnow_iso)


class ImmuneStore:
    """Compact append-only failure memory and supervisor-controlled capability quarantine."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS failure_signatures(
              signature_id TEXT PRIMARY KEY,
              signature_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS capability_quarantine(
              worker_id TEXT NOT NULL,
              capability TEXT NOT NULL,
              active INTEGER NOT NULL,
              reason TEXT NOT NULL,
              doctor_hash TEXT,
              canary_hash TEXT,
              updated_at TEXT NOT NULL,
              PRIMARY KEY(worker_id, capability)
            );
            CREATE TABLE IF NOT EXISTS behavior_fingerprints(
              execution_job_id TEXT PRIMARY KEY,
              fingerprint_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            """
        )

    def close(self) -> None:
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.conn.close()

    def append_failure(self, signature: FailureSignature) -> bool:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO failure_signatures VALUES(?,?,?)",
            (signature.signature_id, canonical_json(signature.model_dump(mode="json")), utcnow_iso()),
        )
        return cur.rowcount == 1

    def seed_from_file(self, path: Path) -> int:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        count = 0
        for raw in data.get("signatures", []):
            count += int(self.append_failure(FailureSignature.model_validate(raw)))
        return count

    def quarantine(self, worker_id: str, capability: str, reason: str) -> None:
        if not worker_id or not capability or not reason:
            raise ValueError("quarantine identity/reason required")
        self.conn.execute(
            "INSERT INTO capability_quarantine VALUES(?,?,1,?,NULL,NULL,?) "
            "ON CONFLICT(worker_id,capability) DO UPDATE SET active=1,reason=excluded.reason,doctor_hash=NULL,canary_hash=NULL,updated_at=excluded.updated_at",
            (worker_id, capability, reason, utcnow_iso()),
        )

    def is_quarantined(self, worker_id: str, capability: str) -> bool:
        row = self.conn.execute(
            "SELECT active FROM capability_quarantine WHERE worker_id=? AND capability=?", (worker_id, capability)
        ).fetchone()
        return bool(row and int(row["active"]) == 1)

    def reenable(self, worker_id: str, capability: str, *, doctor_hash: str, canary_hash: str) -> None:
        if not doctor_hash or not canary_hash:
            raise ValueError("doctor and shadow/canary evidence required")
        row = self.conn.execute(
            "SELECT active FROM capability_quarantine WHERE worker_id=? AND capability=?", (worker_id, capability)
        ).fetchone()
        if row is None or int(row["active"]) != 1:
            raise ValueError("capability is not quarantined")
        self.conn.execute(
            "UPDATE capability_quarantine SET active=0,doctor_hash=?,canary_hash=?,updated_at=? WHERE worker_id=? AND capability=?",
            (doctor_hash, canary_hash, utcnow_iso(), worker_id, capability),
        )

    def record_fingerprint(self, fingerprint: BehaviorFingerprint) -> bool:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO behavior_fingerprints VALUES(?,?,?)",
            (fingerprint.execution_job_id, canonical_json(fingerprint.model_dump(mode="json")), fingerprint.created_at),
        )
        return cur.rowcount == 1


def validate_critic_payload(value: Any) -> None:
    """Critic is attack/evidence only; it cannot write economic authority fields."""
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).strip().lower() in ECONOMIC_FIELDS:
                raise ValueError("critic economic authority field forbidden")
            validate_critic_payload(child)
    elif isinstance(value, list):
        for child in value:
            validate_critic_payload(child)
