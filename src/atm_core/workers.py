from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .capabilities import CapabilityResolver


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def canonical_json(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class WorkerManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str
    repo_url: str
    enabled: bool
    capabilities: list[str]
    execution_backend: str
    entrypoint: str
    max_concurrency: int = 1
    timeout_seconds: int = 900
    network_policy: str
    expected_artifacts: list[str] = Field(default_factory=list)
    cost_ceiling_usd: Decimal = Decimal("0")
    financial_authority: bool = False

    @field_validator("repo_url")
    @classmethod
    def repo_must_be_https(cls, value: str) -> str:
        if not value.startswith("https://github.com/"):
            raise ValueError("worker repo_url must be an https GitHub URL")
        return value.rstrip("/")

    @field_validator("max_concurrency")
    @classmethod
    def bounded_concurrency(cls, value: int) -> int:
        if value < 1 or value > 2:
            raise ValueError("worker max_concurrency out of bounded range")
        return value

    @field_validator("timeout_seconds")
    @classmethod
    def bounded_timeout(cls, value: int) -> int:
        if value < 30 or value > 7200:
            raise ValueError("worker timeout_seconds out of bounded range")
        return value

    @model_validator(mode="after")
    def no_financial_authority_or_spend(self) -> "WorkerManifest":
        if self.financial_authority:
            raise ValueError("workers may not hold financial authority")
        if self.cost_ceiling_usd != Decimal("0"):
            raise ValueError("current OWNER order requires zero worker spend")
        CapabilityResolver.assert_manifest_safe(self.capabilities)
        return self


class WorkerJobSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    canonical_opportunity_id: str
    external_source: str
    external_url: str
    task_type: str
    frozen_acceptance_criteria: list[str]
    repository_or_input: str
    deadline: datetime | None = None
    scope_hash: str = ""
    max_spend_usd: Decimal = Decimal("0")
    required_capabilities: list[str] = Field(default_factory=list)

    @field_validator("external_url")
    @classmethod
    def external_url_https(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("external_url must use https")
        return value

    @model_validator(mode="after")
    def bind_scope(self) -> "WorkerJobSpec":
        if self.max_spend_usd != Decimal("0"):
            raise ValueError("job max_spend_usd must be zero")
        CapabilityResolver.assert_job_safe(self.required_capabilities)
        payload = self.model_dump(mode="json", exclude={"scope_hash"})
        expected = canonical_hash(payload)
        if self.scope_hash and self.scope_hash != expected:
            raise ValueError("scope_hash mismatch")
        object.__setattr__(self, "scope_hash", expected)
        return self


class WorkLease(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lease_id: str
    canonical_opportunity_id: str
    worker_id: str
    scope_hash: str
    acquired_at: datetime
    expires_at: datetime
    heartbeat_at: datetime
    terminal_state: str | None = None


class WorkerResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str
    job_id: str
    status: str
    artifact_urls: list[str] = Field(default_factory=list)
    commit_sha: str | None = None
    pr_url: str | None = None
    test_results: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    content_hashes: list[str] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime
    error_class: str | None = None

    @property
    def envelope_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))


class CanHandleDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    can_handle: bool
    score: int
    reason: str


class PrepareDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ready: bool
    reason: str


class WorkerAdapter(Protocol):
    def can_handle(self, job: WorkerJobSpec) -> CanHandleDecision: ...
    def prepare(self, job: WorkerJobSpec, lease: WorkLease) -> PrepareDecision: ...
    def execute(self, job: WorkerJobSpec, lease: WorkLease) -> str: ...
    def status(self, handle: str) -> str: ...
    def collect(self, handle: str) -> WorkerResult: ...
    def cancel(self, handle: str) -> bool: ...


class WorkerRegistry:
    def __init__(self, manifests: list[WorkerManifest]):
        ids = [manifest.worker_id for manifest in manifests]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate worker_id")
        self._manifests = {manifest.worker_id: manifest for manifest in manifests}

    @classmethod
    def from_directory(cls, path: Path) -> "WorkerRegistry":
        manifests = []
        for item in sorted(Path(path).glob("*.json")):
            manifests.append(WorkerManifest.model_validate_json(item.read_text(encoding="utf-8")))
        return cls(manifests)

    def get(self, worker_id: str) -> WorkerManifest:
        return self._manifests[worker_id]

    def all(self) -> list[WorkerManifest]:
        return [self._manifests[key] for key in sorted(self._manifests)]

    def choose(
        self,
        job: WorkerJobSpec,
        active_counts: dict[str, int] | None = None,
    ) -> tuple[WorkerManifest, CanHandleDecision] | None:
        active_counts = active_counts or {}
        candidates: list[tuple[int, str, WorkerManifest, CanHandleDecision]] = []
        for manifest in self.all():
            if not manifest.enabled:
                continue
            if active_counts.get(manifest.worker_id, 0) >= manifest.max_concurrency:
                continue
            decision = CapabilityResolver.can_handle(manifest.capabilities, job.required_capabilities)
            if decision["can_handle"]:
                typed = CanHandleDecision(**decision)
                candidates.append((-typed.score, manifest.worker_id, manifest, typed))
        if not candidates:
            return None
        candidates.sort(key=lambda row: (row[0], row[1]))
        _, _, manifest, decision = candidates[0]
        return manifest, decision


class WorkLeaseStore:
    """Supervisor-owned durable WORK leases. Workers receive leases but cannot mint them."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS work_leases(
              canonical_opportunity_id TEXT PRIMARY KEY,
              lease_id TEXT NOT NULL,
              worker_id TEXT NOT NULL,
              scope_hash TEXT NOT NULL,
              acquired_at TEXT NOT NULL,
              expires_at TEXT NOT NULL,
              heartbeat_at TEXT NOT NULL,
              released_at TEXT,
              terminal_state TEXT
            )
            """
        )

    def close(self) -> None:
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.conn.close()

    def active_counts(self, now: datetime | None = None) -> dict[str, int]:
        now = now or utcnow()
        rows = self.conn.execute(
            "SELECT worker_id, COUNT(*) n FROM work_leases "
            "WHERE released_at IS NULL AND expires_at>? GROUP BY worker_id",
            (now.isoformat(),),
        ).fetchall()
        return {str(row["worker_id"]): int(row["n"]) for row in rows}

    def acquire(
        self,
        job: WorkerJobSpec,
        manifest: WorkerManifest,
        ttl_seconds: int | None = None,
    ) -> WorkLease | None:
        ttl = max(30, min(manifest.timeout_seconds, int(ttl_seconds or manifest.timeout_seconds)))
        now = utcnow()
        expires = now + timedelta(seconds=ttl)
        lease_id = uuid.uuid4().hex
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT * FROM work_leases WHERE canonical_opportunity_id=?",
                (job.canonical_opportunity_id,),
            ).fetchone()
            if row and row["released_at"] is None:
                old_exp = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
                if old_exp > now:
                    self.conn.execute("ROLLBACK")
                    return None
            if self.active_counts(now).get(manifest.worker_id, 0) >= manifest.max_concurrency:
                self.conn.execute("ROLLBACK")
                return None
            self.conn.execute(
                """
                INSERT INTO work_leases(
                  canonical_opportunity_id,lease_id,worker_id,scope_hash,acquired_at,
                  expires_at,heartbeat_at,released_at,terminal_state
                ) VALUES(?,?,?,?,?,?,?,NULL,NULL)
                ON CONFLICT(canonical_opportunity_id) DO UPDATE SET
                  lease_id=excluded.lease_id,worker_id=excluded.worker_id,scope_hash=excluded.scope_hash,
                  acquired_at=excluded.acquired_at,expires_at=excluded.expires_at,
                  heartbeat_at=excluded.heartbeat_at,released_at=NULL,terminal_state=NULL
                """,
                (
                    job.canonical_opportunity_id,
                    lease_id,
                    manifest.worker_id,
                    job.scope_hash,
                    now.isoformat(),
                    expires.isoformat(),
                    now.isoformat(),
                ),
            )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return WorkLease(
            lease_id=lease_id,
            canonical_opportunity_id=job.canonical_opportunity_id,
            worker_id=manifest.worker_id,
            scope_hash=job.scope_hash,
            acquired_at=now,
            expires_at=expires,
            heartbeat_at=now,
        )

    def supervisor_heartbeat(self, lease: WorkLease, ttl_seconds: int) -> WorkLease:
        """Only ATM may call this method; it is deliberately absent from WorkerAdapter."""
        now = utcnow()
        expires = now + timedelta(seconds=max(30, ttl_seconds))
        cur = self.conn.execute(
            "UPDATE work_leases SET heartbeat_at=?,expires_at=? "
            "WHERE canonical_opportunity_id=? AND lease_id=? AND worker_id=? AND released_at IS NULL",
            (
                now.isoformat(),
                expires.isoformat(),
                lease.canonical_opportunity_id,
                lease.lease_id,
                lease.worker_id,
            ),
        )
        if cur.rowcount != 1:
            raise ValueError("lease no longer active")
        return lease.model_copy(update={"heartbeat_at": now, "expires_at": expires})

    def release(self, lease: WorkLease, terminal_state: str) -> bool:
        cur = self.conn.execute(
            "UPDATE work_leases SET released_at=?,terminal_state=? "
            "WHERE canonical_opportunity_id=? AND lease_id=? AND worker_id=? AND released_at IS NULL",
            (
                utcnow().isoformat(),
                terminal_state,
                lease.canonical_opportunity_id,
                lease.lease_id,
                lease.worker_id,
            ),
        )
        return cur.rowcount == 1
