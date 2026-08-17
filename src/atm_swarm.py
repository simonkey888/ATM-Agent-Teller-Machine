from __future__ import annotations

import json
import math
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

LEASE_CLASSES = {"VERIFY", "CLAIM", "WORK", "SUBMIT"}
ACTIVE_STATES = {
    "DISCOVERED", "NORMALIZED", "VERIFYING", "VERIFIED", "FALSIFYING", "ELIGIBLE",
    "CLAIM_LEASED", "CLAIMED", "WORK_LEASED", "WORKING", "CHECKING",
    "SUBMIT_LEASED", "SUBMITTED", "MONITORING", "PAYMENT_VERIFY",
}
TERMINAL_STATES = {"PAID", "REJECTED", "STALE", "LOST"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or utcnow()).astimezone(timezone.utc).isoformat()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def freshness_decay(age_hours: float, half_life_hours: float = 48.0) -> float:
    if age_hours <= 0:
        return 1.0
    return max(0.01, math.pow(0.5, age_hours / max(1.0, half_life_hours)))


def evidence_factor(negative_evidence: Iterable[str]) -> float:
    factor = 1.0
    severe = {"CLOSED", "UNFUNDED", "PAYER_INACTIVE", "AI_FORBIDDEN", "PAYOUT_UNAVAILABLE", "CLAIM_LOST"}
    medium = {"SATURATED", "AMBIGUOUS_ACCEPTANCE", "SLOW_SETTLEMENT", "KYC_REQUIRED", "STALE"}
    for item in {str(x).upper() for x in negative_evidence}:
        if item in severe:
            factor *= 0.05
        elif item in medium:
            factor *= 0.35
        else:
            factor *= 0.75
    return max(0.001, factor)


def swarm_signal(base_score: float, updated_at: datetime | None, confidence: float, negative: Iterable[str]) -> float:
    now = utcnow()
    age = 720.0 if updated_at is None else max(0.0, (now - updated_at.astimezone(timezone.utc)).total_seconds() / 3600)
    return max(0.0, float(base_score)) * freshness_decay(age) * min(1.0, max(0.0, float(confidence))) * evidence_factor(negative)


@dataclass(frozen=True)
class Lease:
    opportunity_id: str
    lease_class: str
    owner: str
    lease_id: str
    acquired_at: str
    expires_at: str


class MoneyBoard:
    """Durable transactional swarm board. It never writes payment truth."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._schema()

    def _schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS opportunities (
                canonical_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                authoritative_url TEXT NOT NULL,
                status TEXT NOT NULL,
                base_score REAL NOT NULL DEFAULT 0,
                signal REAL NOT NULL DEFAULT 0,
                evidence_confidence REAL NOT NULL DEFAULT 0,
                updated_at TEXT,
                verified_at TEXT,
                external_state_hash TEXT,
                negative_evidence_json TEXT NOT NULL DEFAULT '[]',
                payload_json TEXT NOT NULL,
                scout_sources_json TEXT NOT NULL DEFAULT '[]',
                falsifier_verdict TEXT,
                rejection_reason TEXT,
                next_revalidation_at TEXT,
                created_board_at TEXT NOT NULL,
                touched_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS leases (
                opportunity_id TEXT NOT NULL,
                lease_class TEXT NOT NULL,
                owner TEXT NOT NULL,
                lease_id TEXT NOT NULL,
                acquired_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                released_at TEXT,
                terminal_state TEXT,
                PRIMARY KEY (opportunity_id, lease_class)
            );
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                at TEXT NOT NULL,
                kind TEXT NOT NULL,
                opportunity_id TEXT,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS provider_circuits (
                provider TEXT PRIMARY KEY,
                failures INTEGER NOT NULL DEFAULT 0,
                opened_until TEXT,
                last_error_class TEXT,
                touched_at TEXT NOT NULL
            );
            """
        )

    def close(self) -> None:
        self.checkpoint()
        self._conn.close()

    def checkpoint(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def _event(self, kind: str, opportunity_id: str | None, payload: dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT INTO events(event_id,at,kind,opportunity_id,payload_json) VALUES(?,?,?,?,?)",
            (uuid.uuid4().hex, _iso(), kind, opportunity_id, json.dumps(payload, sort_keys=True, default=str)),
        )

    def upsert_candidate(
        self,
        payload: dict[str, Any],
        *,
        scout: str,
        base_score: float,
        confidence: float = 0.8,
        negative_evidence: Iterable[str] = (),
        status: str = "NORMALIZED",
    ) -> dict[str, Any]:
        cid = str(payload.get("canonical_opportunity_id") or "").strip()
        source = str(payload.get("source") or scout).strip()
        url = str(payload.get("authoritative_url") or "").strip()
        if not cid or not source or not url.startswith("https://"):
            raise ValueError("candidate identity/source/https authoritative_url required")
        updated = payload.get("updated_at")
        if isinstance(updated, str):
            updated_dt = _parse(updated)
        elif isinstance(updated, datetime):
            updated_dt = updated.astimezone(timezone.utc)
        else:
            updated_dt = None
        negative = sorted({str(x).upper() for x in negative_evidence})
        signal = swarm_signal(base_score, updated_dt, confidence, negative)
        now = _iso()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute("SELECT scout_sources_json FROM opportunities WHERE canonical_id=?", (cid,)).fetchone()
                scouts = set(json.loads(row[0])) if row else set()
                scouts.add(scout)
                self._conn.execute(
                    """
                    INSERT INTO opportunities(
                      canonical_id,source,authoritative_url,status,base_score,signal,evidence_confidence,
                      updated_at,external_state_hash,negative_evidence_json,payload_json,scout_sources_json,
                      created_board_at,touched_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(canonical_id) DO UPDATE SET
                      source=excluded.source,
                      authoritative_url=excluded.authoritative_url,
                      status=CASE WHEN opportunities.status IN ('PAID','REJECTED','LOST') THEN opportunities.status ELSE excluded.status END,
                      base_score=excluded.base_score,
                      signal=excluded.signal,
                      evidence_confidence=excluded.evidence_confidence,
                      updated_at=excluded.updated_at,
                      external_state_hash=COALESCE(excluded.external_state_hash, opportunities.external_state_hash),
                      negative_evidence_json=excluded.negative_evidence_json,
                      payload_json=excluded.payload_json,
                      scout_sources_json=excluded.scout_sources_json,
                      touched_at=excluded.touched_at
                    """,
                    (
                        cid, source, url, status, float(base_score), float(signal), float(confidence),
                        updated_dt.isoformat() if updated_dt else None,
                        payload.get("external_state_hash"), json.dumps(negative),
                        json.dumps(payload, sort_keys=True, default=str), json.dumps(sorted(scouts)), now, now,
                    ),
                )
                self._event("CANDIDATE_UPSERT", cid, {"scout": scout, "signal": signal, "negative": negative})
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return self.get(cid) or {}

    def get(self, opportunity_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM opportunities WHERE canonical_id=?", (opportunity_id,)).fetchone()
        return dict(row) if row else None

    def mark_falsifier(self, opportunity_id: str, verdict: str, *, reason: str | None = None, confidence: float = 1.0) -> None:
        verdict = verdict.upper()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute("SELECT signal,status FROM opportunities WHERE canonical_id=?", (opportunity_id,)).fetchone()
                if not row:
                    raise KeyError(opportunity_id)
                signal = float(row[0])
                status = "ELIGIBLE" if verdict == "CONFIRM" else "REJECTED"
                if verdict != "CONFIRM":
                    signal *= 0.01
                self._conn.execute(
                    "UPDATE opportunities SET falsifier_verdict=?,rejection_reason=?,evidence_confidence=?,signal=?,status=?,verified_at=?,touched_at=? WHERE canonical_id=?",
                    (verdict, reason, confidence, signal, status, _iso(), _iso(), opportunity_id),
                )
                self._event("FALSIFIER", opportunity_id, {"verdict": verdict, "reason": reason})
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def top(self, limit: int = 20, eligible_only: bool = False) -> list[dict[str, Any]]:
        where = "WHERE status='ELIGIBLE'" if eligible_only else "WHERE status NOT IN ('PAID','REJECTED','STALE','LOST')"
        rows = self._conn.execute(
            f"SELECT * FROM opportunities {where} ORDER BY signal DESC, touched_at DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [dict(r) for r in rows]

    def expire_stale(self, max_age_hours: float = 720.0) -> int:
        cutoff = utcnow() - timedelta(hours=max_age_hours)
        with self._lock:
            rows = self._conn.execute(
                "SELECT canonical_id,updated_at,touched_at FROM opportunities WHERE status NOT IN ('PAID','REJECTED','STALE','LOST')"
            ).fetchall()
            stale = []
            for row in rows:
                ts = _parse(row[1]) or _parse(row[2])
                if ts and ts < cutoff:
                    stale.append(row[0])
            for cid in stale:
                self._conn.execute("UPDATE opportunities SET status='STALE',signal=0,touched_at=? WHERE canonical_id=?", (_iso(), cid))
                self._event("STALE", cid, {})
            return len(stale)

    def acquire_lease(self, opportunity_id: str, lease_class: str, owner: str, ttl_seconds: int = 300) -> Lease | None:
        lease_class = lease_class.upper()
        if lease_class not in LEASE_CLASSES:
            raise ValueError("unsupported lease class")
        now = utcnow()
        expires = now + timedelta(seconds=max(30, int(ttl_seconds)))
        lease_id = uuid.uuid4().hex
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT owner,lease_id,expires_at,released_at FROM leases WHERE opportunity_id=? AND lease_class=?",
                    (opportunity_id, lease_class),
                ).fetchone()
                if row and row[3] is None:
                    old_exp = _parse(row[2])
                    if old_exp and old_exp > now:
                        self._conn.execute("ROLLBACK")
                        return None
                self._conn.execute(
                    """
                    INSERT INTO leases(opportunity_id,lease_class,owner,lease_id,acquired_at,expires_at,released_at,terminal_state)
                    VALUES(?,?,?,?,?,?,NULL,NULL)
                    ON CONFLICT(opportunity_id,lease_class) DO UPDATE SET
                      owner=excluded.owner,lease_id=excluded.lease_id,acquired_at=excluded.acquired_at,
                      expires_at=excluded.expires_at,released_at=NULL,terminal_state=NULL
                    """,
                    (opportunity_id, lease_class, owner, lease_id, now.isoformat(), expires.isoformat()),
                )
                self._event("LEASE_ACQUIRE", opportunity_id, {"class": lease_class, "owner": owner, "lease_id": lease_id})
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return Lease(opportunity_id, lease_class, owner, lease_id, now.isoformat(), expires.isoformat())

    def renew_lease(self, lease: Lease, ttl_seconds: int = 300) -> bool:
        expires = utcnow() + timedelta(seconds=max(30, int(ttl_seconds)))
        with self._lock:
            cur = self._conn.execute(
                "UPDATE leases SET expires_at=? WHERE opportunity_id=? AND lease_class=? AND lease_id=? AND owner=? AND released_at IS NULL",
                (expires.isoformat(), lease.opportunity_id, lease.lease_class, lease.lease_id, lease.owner),
            )
            return cur.rowcount == 1

    def release_lease(self, lease: Lease, terminal_state: str = "RELEASED") -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE leases SET released_at=?,terminal_state=? WHERE opportunity_id=? AND lease_class=? AND lease_id=? AND owner=? AND released_at IS NULL",
                (_iso(), terminal_state, lease.opportunity_id, lease.lease_class, lease.lease_id, lease.owner),
            )
            if cur.rowcount:
                self._event("LEASE_RELEASE", lease.opportunity_id, {"class": lease.lease_class, "state": terminal_state})
            return cur.rowcount == 1

    def provider_failure(self, provider: str, error_class: str, *, threshold: int = 3, cooldown_seconds: int = 900) -> None:
        now = utcnow()
        with self._lock:
            row = self._conn.execute("SELECT failures FROM provider_circuits WHERE provider=?", (provider,)).fetchone()
            failures = (int(row[0]) if row else 0) + 1
            opened = (now + timedelta(seconds=cooldown_seconds)).isoformat() if failures >= threshold else None
            self._conn.execute(
                "INSERT INTO provider_circuits(provider,failures,opened_until,last_error_class,touched_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(provider) DO UPDATE SET failures=excluded.failures,opened_until=excluded.opened_until,last_error_class=excluded.last_error_class,touched_at=excluded.touched_at",
                (provider, failures, opened, error_class, now.isoformat()),
            )

    def provider_available(self, provider: str) -> bool:
        row = self._conn.execute("SELECT opened_until FROM provider_circuits WHERE provider=?", (provider,)).fetchone()
        if not row or not row[0]:
            return True
        return (_parse(row[0]) or utcnow()) <= utcnow()

    def stats(self) -> dict[str, Any]:
        counts = {r[0]: int(r[1]) for r in self._conn.execute("SELECT status,COUNT(*) FROM opportunities GROUP BY status")}
        active_leases = int(self._conn.execute("SELECT COUNT(*) FROM leases WHERE released_at IS NULL AND expires_at>?", (_iso(),)).fetchone()[0])
        scouts = set()
        for row in self._conn.execute("SELECT scout_sources_json FROM opportunities"):
            scouts.update(json.loads(row[0]))
        return {
            "candidate_counts": counts,
            "active_leases": active_leases,
            "scout_sources": sorted(scouts),
            "scout_count": len(scouts),
            "falsified_count": int(self._conn.execute("SELECT COUNT(*) FROM opportunities WHERE falsifier_verdict IS NOT NULL").fetchone()[0]),
        }


@dataclass
class ResourceGovernor:
    max_scouts: int = 4
    max_falsifiers: int = 2
    max_workers: int = 1
    max_model_subprocesses: int = 2
    max_candidates: int = 500

    def validate(self) -> None:
        if not (2 <= self.max_scouts <= 8):
            raise ValueError("max_scouts out of bounds")
        if not (1 <= self.max_falsifiers <= 4):
            raise ValueError("max_falsifiers out of bounds")
        if self.max_workers != 1:
            raise ValueError("master order requires exactly one WORK slot initially")
        if not (1 <= self.max_model_subprocesses <= 4):
            raise ValueError("max_model_subprocesses out of bounds")
        if not (50 <= self.max_candidates <= 2000):
            raise ValueError("max_candidates out of bounds")

    def shed_order(self) -> tuple[str, ...]:
        return ("LOW_SCORE_SCOUTS", "LOW_INFORMATION_FALSIFIERS", "LOW_PRIORITY_WATCHERS")
