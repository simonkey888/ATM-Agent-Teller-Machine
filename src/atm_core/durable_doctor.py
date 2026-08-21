from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any


DOCTOR_SCHEMA = "ATM_DURABLE_DOCTOR_V1"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RecoveryAction(StrEnum):
    ROTATE_VERIFIED_FREE_PROVIDER = "ROTATE_TO_VERIFIED_FREE_PROVIDER"
    SOURCE_BACKOFF = "REOPEN_READ_ONLY_SOURCE_AFTER_BACKOFF"
    QUARANTINE_SOURCE = "QUARANTINE_BAD_SOURCE"
    REDISPATCH_SECRETLESS_WORKER = "REDISPATCH_CRASHED_SECRETLESS_WORKER"
    RESUME_NONCOMMITTED_JOB = "RESUME_NONCOMMITTED_JOB"
    RESTORE_CHECKPOINT = "RESTORE_CHECKPOINT"
    REVERIFY_EXACT_HEAD = "REVERIFY_EXACT_HEAD"
    REFRESH_STALE_STATE = "REFRESH_STALE_STATE"
    RECOVER_EFFECT_RECEIPT = "RECOVER_COMMITTED_EFFECT_FROM_EXTERNAL_RECEIPT"
    RETRY_EPHEMERAL_WORKER = "RETRY_EPHEMERAL_WORKER"


FAULT_ACTIONS: dict[str, RecoveryAction] = {
    "PROVIDER_429": RecoveryAction.ROTATE_VERIFIED_FREE_PROVIDER,
    "PROVIDER_EXHAUSTED": RecoveryAction.ROTATE_VERIFIED_FREE_PROVIDER,
    "PROVIDER_TIMEOUT": RecoveryAction.ROTATE_VERIFIED_FREE_PROVIDER,
    "SOURCE_TIMEOUT": RecoveryAction.SOURCE_BACKOFF,
    "SOURCE_LIED": RecoveryAction.QUARANTINE_SOURCE,
    "WORKER_CRASH": RecoveryAction.REDISPATCH_SECRETLESS_WORKER,
    "CHECK_FAILED": RecoveryAction.RESUME_NONCOMMITTED_JOB,
    "FORGE_CRASH": RecoveryAction.RESTORE_CHECKPOINT,
    "HEARTBEAT_FAULT": RecoveryAction.REVERIFY_EXACT_HEAD,
    "STALE_STATE": RecoveryAction.REFRESH_STALE_STATE,
    "EFFECT_RECEIPT_RECOVERY": RecoveryAction.RECOVER_EFFECT_RECEIPT,
}


@dataclass(frozen=True)
class DoctorDecision:
    schema: str
    fault: str
    key: str
    action: str
    state: str
    attempt: int
    decided_at: str


class DurableDoctor:
    """Persistent closed recovery controller. It never executes caller-supplied code."""

    FORBIDDEN = {
        "CHANGE_HEALTH_SLO_TO_HIDE_FAILURE", "DISABLE_CANON", "DISABLE_EFFECT_BOUNDARY",
        "DECLARE_GREEN_BY_LLM", "CREATE_WALLET", "CREATE_MARKETPLACE_IDENTITY", "SPEND_MONEY",
        "USE_PAID_PROVIDER", "EXPOSE_SECRETS", "BYPASS_PLATFORM_SECURITY", "ARBITRARY_SELF_PATCH_AND_DEPLOY",
    }

    def __init__(self, path: Path, *, threshold: int = 3, cooldown_seconds: int = 300):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.threshold = max(1, int(threshold))
        self.cooldown_seconds = max(1, int(cooldown_seconds))
        self.conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS circuits(
              key TEXT PRIMARY KEY,
              failures INTEGER NOT NULL DEFAULT 0,
              opened_until TEXT,
              quarantined INTEGER NOT NULL DEFAULT 0,
              last_error TEXT,
              touched_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS decisions(
              decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
              fault TEXT NOT NULL,
              key TEXT NOT NULL,
              action TEXT NOT NULL,
              state TEXT NOT NULL,
              attempt INTEGER NOT NULL,
              decided_at TEXT NOT NULL
            );
            """
        )

    def close(self) -> None:
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.conn.close()

    @staticmethod
    def choose(fault: str) -> str:
        return FAULT_ACTIONS.get(str(fault).upper(), RecoveryAction.RETRY_EPHEMERAL_WORKER).value

    def _row(self, key: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM circuits WHERE key=?", (str(key),)).fetchone()

    def record_failure(self, key: str, error_class: str) -> dict[str, Any]:
        now = utcnow()
        row = self._row(key)
        failures = (int(row["failures"]) if row else 0) + 1
        opened = (now + timedelta(seconds=self.cooldown_seconds)).isoformat() if failures >= self.threshold else (row["opened_until"] if row else None)
        quarantined = int(row["quarantined"]) if row else 0
        self.conn.execute(
            "INSERT INTO circuits(key,failures,opened_until,quarantined,last_error,touched_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET failures=excluded.failures,opened_until=excluded.opened_until,quarantined=excluded.quarantined,last_error=excluded.last_error,touched_at=excluded.touched_at",
            (str(key), failures, opened, quarantined, str(error_class), now.isoformat()),
        )
        return self.status(key)

    def record_success(self, key: str) -> dict[str, Any]:
        now = utcnow().isoformat()
        row = self._row(key)
        quarantined = int(row["quarantined"]) if row else 0
        self.conn.execute(
            "INSERT INTO circuits(key,failures,opened_until,quarantined,last_error,touched_at) VALUES(?,0,NULL,?,NULL,?) "
            "ON CONFLICT(key) DO UPDATE SET failures=0,opened_until=NULL,last_error=NULL,touched_at=excluded.touched_at",
            (str(key), quarantined, now),
        )
        return self.status(key)

    def quarantine(self, key: str, error_class: str = "SOURCE_LIED") -> dict[str, Any]:
        now = utcnow().isoformat()
        row = self._row(key)
        failures = int(row["failures"]) if row else 0
        opened = row["opened_until"] if row else None
        self.conn.execute(
            "INSERT INTO circuits(key,failures,opened_until,quarantined,last_error,touched_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET quarantined=1,last_error=excluded.last_error,touched_at=excluded.touched_at",
            (str(key), failures, opened, 1, str(error_class), now),
        )
        return self.status(key)

    def available(self, key: str) -> bool:
        row = self._row(key)
        if not row:
            return True
        if bool(row["quarantined"]):
            return False
        raw = row["opened_until"]
        if raw:
            opened = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if opened > utcnow():
                return False
        return True

    def status(self, key: str) -> dict[str, Any]:
        row = self._row(key)
        if not row:
            return {"failures": 0, "opened_until": None, "quarantined": False, "last_error": None, "available": True}
        return {
            "failures": int(row["failures"]), "opened_until": row["opened_until"],
            "quarantined": bool(row["quarantined"]), "last_error": row["last_error"], "available": self.available(key),
        }

    def handle(self, fault: str, key: str, *, attempt: int = 1) -> DoctorDecision:
        fault = str(fault).upper()
        action = self.choose(fault)
        if action in self.FORBIDDEN:
            raise RuntimeError("DOCTOR_RECOVERY_NOT_ALLOWED")
        if fault == "SOURCE_LIED":
            self.quarantine(key, fault)
            state = "QUARANTINED"
        else:
            self.record_failure(key, fault)
            state = "CIRCUIT_OPEN" if not self.available(key) else "RECOVERY_SCHEDULED"
        decided = utcnow().isoformat()
        self.conn.execute(
            "INSERT INTO decisions(fault,key,action,state,attempt,decided_at) VALUES(?,?,?,?,?,?)",
            (fault, str(key), action, state, max(1, int(attempt)), decided),
        )
        return DoctorDecision(DOCTOR_SCHEMA, fault, str(key), action, state, max(1, int(attempt)), decided)

    def last_decision(self) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM decisions ORDER BY decision_id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def summary(self) -> dict[str, Any]:
        circuits = int(self.conn.execute("SELECT COUNT(*) FROM circuits").fetchone()[0])
        quarantined = int(self.conn.execute("SELECT COUNT(*) FROM circuits WHERE quarantined=1").fetchone()[0])
        open_now = 0
        for row in self.conn.execute("SELECT key FROM circuits"):
            if not self.available(str(row["key"])):
                open_now += 1
        return {"schema": DOCTOR_SCHEMA, "durable": True, "typed_handlers_only": True, "circuits": circuits, "open_or_quarantined": open_now, "quarantined": quarantined, "last_decision": self.last_decision()}
