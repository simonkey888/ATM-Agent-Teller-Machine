from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .workers import canonical_hash, canonical_json


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExecutionStatus(StrEnum):
    CREATED = "CREATED"
    DISPATCHING = "DISPATCHING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RUNNING = "RUNNING"
    RESULT_READY = "RESULT_READY"
    CHECKING = "CHECKING"
    TERMINAL = "TERMINAL"
    DISPATCH_FAILED = "DISPATCH_FAILED"
    STALLED = "STALLED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    QUARANTINED = "QUARANTINED"


TERMINAL_STATUSES = {
    ExecutionStatus.TERMINAL,
    ExecutionStatus.DISPATCH_FAILED,
    ExecutionStatus.STALLED,
    ExecutionStatus.EXPIRED,
    ExecutionStatus.CANCELLED,
    ExecutionStatus.QUARANTINED,
}


class ExecutionJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_job_id: str
    opportunity_id: str
    worker_id: str
    worker_version_or_source_sha: str
    work_lease_id: str
    scope_hash: str
    job_spec_hash: str
    status: ExecutionStatus = ExecutionStatus.CREATED
    created_at: str
    started_at: str | None = None
    heartbeat_at: str | None = None
    checkpoint_seq: int = 0
    result_hash: str | None = None
    checker_hash: str | None = None
    terminal_reason: str | None = None
    launch_count: int = 0

    @field_validator("worker_version_or_source_sha")
    @classmethod
    def source_identity_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("worker source identity required")
        return value.strip()


class ExecutionAck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    execution_job_id: str
    work_lease_id: str
    scope_hash: str
    worker_id: str
    source_sha: str
    acknowledged_at: str

    @property
    def receipt_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))


class ProgressReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_job_id: str
    checkpoint_seq: int
    objective_hash: str
    artifact_before_hash: str
    artifact_after_hash: str
    tests_run: list[str] = Field(default_factory=list)
    new_evidence_refs: list[str] = Field(default_factory=list)
    blocker_class: str = "NONE"
    uncertainty_or_acceptance_delta: str = ""
    recommendation: str = "CONTINUE"
    error_signature: str | None = None
    created_at: str

    @field_validator("recommendation")
    @classmethod
    def recommendation_enum(cls, value: str) -> str:
        value = value.upper()
        if value not in {"CONTINUE", "REPLAN", "FORK", "ABANDON"}:
            raise ValueError("invalid progress recommendation")
        return value

    @property
    def receipt_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))

    @property
    def measurable_progress(self) -> bool:
        return bool(
            self.artifact_after_hash != self.artifact_before_hash
            or self.new_evidence_refs
            or self.uncertainty_or_acceptance_delta.strip()
        )


class ExecutionJobStore:
    """Supervisor-owned durable process truth. It does not contain economic truth."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS execution_jobs(
              execution_job_id TEXT PRIMARY KEY,
              opportunity_id TEXT NOT NULL,
              worker_id TEXT NOT NULL,
              worker_version_or_source_sha TEXT NOT NULL,
              work_lease_id TEXT NOT NULL,
              scope_hash TEXT NOT NULL,
              job_spec_hash TEXT NOT NULL,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              started_at TEXT,
              heartbeat_at TEXT,
              checkpoint_seq INTEGER NOT NULL DEFAULT 0,
              result_hash TEXT,
              checker_hash TEXT,
              terminal_reason TEXT,
              launch_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_execution_identity
              ON execution_jobs(opportunity_id, worker_id, work_lease_id, scope_hash, job_spec_hash);
            CREATE TABLE IF NOT EXISTS execution_acks(
              execution_job_id TEXT PRIMARY KEY,
              worker_id TEXT NOT NULL,
              work_lease_id TEXT NOT NULL,
              scope_hash TEXT NOT NULL,
              source_sha TEXT NOT NULL,
              acknowledged_at TEXT NOT NULL,
              receipt_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS progress_receipts(
              execution_job_id TEXT NOT NULL,
              checkpoint_seq INTEGER NOT NULL,
              receipt_json TEXT NOT NULL,
              receipt_hash TEXT NOT NULL,
              PRIMARY KEY(execution_job_id, checkpoint_seq)
            );
            """
        )

    def close(self) -> None:
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.conn.close()

    @staticmethod
    def identity(
        opportunity_id: str,
        worker_id: str,
        work_lease_id: str,
        scope_hash: str,
        job_spec_hash: str,
    ) -> str:
        return "exec_" + canonical_hash(
            {
                "opportunity_id": opportunity_id,
                "worker_id": worker_id,
                "work_lease_id": work_lease_id,
                "scope_hash": scope_hash,
                "job_spec_hash": job_spec_hash,
            }
        )[:40]

    def create_or_get(
        self,
        *,
        opportunity_id: str,
        worker_id: str,
        worker_version_or_source_sha: str,
        work_lease_id: str,
        scope_hash: str,
        job_spec_hash: str,
    ) -> ExecutionJob:
        execution_job_id = self.identity(opportunity_id, worker_id, work_lease_id, scope_hash, job_spec_hash)
        now = utcnow_iso()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO execution_jobs(
                  execution_job_id,opportunity_id,worker_id,worker_version_or_source_sha,
                  work_lease_id,scope_hash,job_spec_hash,status,created_at
                ) VALUES(?,?,?,?,?,?,?,'CREATED',?)
                """,
                (
                    execution_job_id,
                    opportunity_id,
                    worker_id,
                    worker_version_or_source_sha,
                    work_lease_id,
                    scope_hash,
                    job_spec_hash,
                    now,
                ),
            )
            row = self.conn.execute(
                "SELECT * FROM execution_jobs WHERE execution_job_id=?", (execution_job_id,)
            ).fetchone()
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        job = self._row_to_job(row)
        expected = (worker_version_or_source_sha, work_lease_id, scope_hash, job_spec_hash)
        observed = (job.worker_version_or_source_sha, job.work_lease_id, job.scope_hash, job.job_spec_hash)
        if observed != expected:
            raise ValueError("execution identity collision/mismatch")
        return job

    def get(self, execution_job_id: str) -> ExecutionJob:
        row = self.conn.execute(
            "SELECT * FROM execution_jobs WHERE execution_job_id=?", (execution_job_id,)
        ).fetchone()
        if row is None:
            raise KeyError(execution_job_id)
        return self._row_to_job(row)

    def begin_dispatch(self, execution_job_id: str) -> tuple[ExecutionJob, bool]:
        """Atomically acquire the one allowed launch. Returns (job, should_launch)."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT * FROM execution_jobs WHERE execution_job_id=?", (execution_job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(execution_job_id)
            current = self._row_to_job(row)
            if current.status != ExecutionStatus.CREATED or current.launch_count != 0:
                self.conn.execute("COMMIT")
                return current, False
            now = utcnow_iso()
            self.conn.execute(
                "UPDATE execution_jobs SET status='DISPATCHING',started_at=?,heartbeat_at=?,launch_count=1 "
                "WHERE execution_job_id=? AND status='CREATED' AND launch_count=0",
                (now, now, execution_job_id),
            )
            row = self.conn.execute(
                "SELECT * FROM execution_jobs WHERE execution_job_id=?", (execution_job_id,)
            ).fetchone()
            self.conn.execute("COMMIT")
            return self._row_to_job(row), True
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def acknowledge(self, ack: ExecutionAck) -> ExecutionJob:
        job = self.get(ack.execution_job_id)
        if job.worker_id != ack.worker_id or job.work_lease_id != ack.work_lease_id or job.scope_hash != ack.scope_hash:
            raise ValueError("ACK binding mismatch")
        if job.worker_version_or_source_sha != ack.source_sha:
            raise ValueError("ACK stale worker source")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            existing = self.conn.execute(
                "SELECT * FROM execution_acks WHERE execution_job_id=?", (ack.execution_job_id,)
            ).fetchone()
            if existing:
                if str(existing["receipt_hash"]) != ack.receipt_hash:
                    raise ValueError("conflicting duplicate ACK")
            else:
                self.conn.execute(
                    "INSERT INTO execution_acks VALUES(?,?,?,?,?,?,?)",
                    (
                        ack.execution_job_id,
                        ack.worker_id,
                        ack.work_lease_id,
                        ack.scope_hash,
                        ack.source_sha,
                        ack.acknowledged_at,
                        ack.receipt_hash,
                    ),
                )
            now = utcnow_iso()
            self.conn.execute(
                "UPDATE execution_jobs SET status='ACKNOWLEDGED',heartbeat_at=? WHERE execution_job_id=?",
                (now, ack.execution_job_id),
            )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return self.get(ack.execution_job_id)

    def running(self, execution_job_id: str) -> ExecutionJob:
        now = utcnow_iso()
        self.conn.execute(
            "UPDATE execution_jobs SET status='RUNNING',heartbeat_at=? WHERE execution_job_id=? "
            "AND status IN ('DISPATCHING','ACKNOWLEDGED','RUNNING')",
            (now, execution_job_id),
        )
        return self.get(execution_job_id)

    def add_progress(self, receipt: ProgressReceipt) -> ExecutionJob:
        job = self.get(receipt.execution_job_id)
        if receipt.checkpoint_seq != job.checkpoint_seq + 1:
            raise ValueError("progress checkpoint sequence mismatch")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute(
                "INSERT INTO progress_receipts VALUES(?,?,?,?)",
                (
                    receipt.execution_job_id,
                    receipt.checkpoint_seq,
                    canonical_json(receipt.model_dump(mode="json")),
                    receipt.receipt_hash,
                ),
            )
            self.conn.execute(
                "UPDATE execution_jobs SET checkpoint_seq=?,heartbeat_at=? WHERE execution_job_id=?",
                (receipt.checkpoint_seq, receipt.created_at, receipt.execution_job_id),
            )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return self.get(receipt.execution_job_id)

    def progress(self, execution_job_id: str) -> list[ProgressReceipt]:
        rows = self.conn.execute(
            "SELECT receipt_json FROM progress_receipts WHERE execution_job_id=? ORDER BY checkpoint_seq",
            (execution_job_id,),
        ).fetchall()
        return [ProgressReceipt.model_validate_json(str(row["receipt_json"])) for row in rows]

    def result_ready(self, execution_job_id: str, result_hash: str) -> ExecutionJob:
        self.conn.execute(
            "UPDATE execution_jobs SET status='RESULT_READY',result_hash=?,heartbeat_at=? WHERE execution_job_id=?",
            (result_hash, utcnow_iso(), execution_job_id),
        )
        return self.get(execution_job_id)

    def checking(self, execution_job_id: str) -> ExecutionJob:
        self.conn.execute(
            "UPDATE execution_jobs SET status='CHECKING',heartbeat_at=? WHERE execution_job_id=?",
            (utcnow_iso(), execution_job_id),
        )
        return self.get(execution_job_id)

    def terminal(self, execution_job_id: str, *, checker_hash: str, reason: str = "PASS") -> ExecutionJob:
        self.conn.execute(
            "UPDATE execution_jobs SET status='TERMINAL',checker_hash=?,terminal_reason=?,heartbeat_at=? "
            "WHERE execution_job_id=?",
            (checker_hash, reason, utcnow_iso(), execution_job_id),
        )
        return self.get(execution_job_id)

    def fail(self, execution_job_id: str, status: ExecutionStatus, reason: str) -> ExecutionJob:
        if status not in TERMINAL_STATUSES or status == ExecutionStatus.TERMINAL:
            raise ValueError("failure status must be a failure terminal")
        self.conn.execute(
            "UPDATE execution_jobs SET status=?,terminal_reason=?,heartbeat_at=? WHERE execution_job_id=?",
            (status.value, reason, utcnow_iso(), execution_job_id),
        )
        return self.get(execution_job_id)

    def recoverable(self) -> list[ExecutionJob]:
        rows = self.conn.execute(
            "SELECT * FROM execution_jobs WHERE status NOT IN ('TERMINAL','DISPATCH_FAILED','STALLED','EXPIRED','CANCELLED','QUARANTINED')"
        ).fetchall()
        return [self._row_to_job(row) for row in rows]

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> ExecutionJob:
        return ExecutionJob(**dict(row))
