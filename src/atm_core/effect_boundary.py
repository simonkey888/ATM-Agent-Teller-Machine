from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EFFECT_SCHEMA = "ATM_UNIVERSAL_EFFECT_BOUNDARY_V1"
EFFECT_STATES = {"PREPARED", "PRECONDITION_REFETCH", "COMMITTING", "AUTHORITATIVE_VERIFY", "COMMITTED"}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def effect_key(
    *,
    canonical_identity: str,
    canonical_opportunity_id: str,
    external_action: str,
    canonical_args: dict[str, Any],
    current_external_object_hash: str = "",
) -> str:
    payload = {
        "canonical_identity": canonical_identity.lower(),
        "canonical_opportunity_id": canonical_opportunity_id,
        "external_action": external_action,
        "canonical_args": canonical_args,
        "current_external_object_hash": current_external_object_hash,
    }
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


@dataclass(frozen=True)
class EffectRecord:
    effect_key: str
    state: str
    canonical_identity: str
    canonical_opportunity_id: str
    external_action: str
    canonical_args_hash: str
    external_receipt_id: str | None
    updated_at: str


class UniversalEffectBoundary:
    """Single durable at-most-once intent log; external APIs remain final truth."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS effects ("
            "effect_key TEXT PRIMARY KEY,state TEXT NOT NULL,canonical_identity TEXT NOT NULL,"
            "canonical_opportunity_id TEXT NOT NULL,external_action TEXT NOT NULL,canonical_args_hash TEXT NOT NULL,"
            "external_receipt_id TEXT,updated_at TEXT NOT NULL)"
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def get(self, key: str) -> EffectRecord | None:
        row = self.conn.execute(
            "SELECT effect_key,state,canonical_identity,canonical_opportunity_id,external_action,canonical_args_hash,external_receipt_id,updated_at "
            "FROM effects WHERE effect_key=?",
            (key,),
        ).fetchone()
        return EffectRecord(*row) if row else None

    def prepare(
        self,
        *,
        canonical_identity: str,
        canonical_opportunity_id: str,
        external_action: str,
        canonical_args: dict[str, Any],
        current_external_object_hash: str = "",
    ) -> EffectRecord:
        key = effect_key(
            canonical_identity=canonical_identity,
            canonical_opportunity_id=canonical_opportunity_id,
            external_action=external_action,
            canonical_args=canonical_args,
            current_external_object_hash=current_external_object_hash,
        )
        args_hash = hashlib.sha256(canonical_json(canonical_args).encode()).hexdigest()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute(
                "INSERT OR IGNORE INTO effects VALUES(?,?,?,?,?,?,?,?)",
                (key, "PREPARED", canonical_identity.lower(), canonical_opportunity_id, external_action, args_hash, None, self._now()),
            )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        record = self.get(key)
        if record is None or record.canonical_args_hash != args_hash:
            raise RuntimeError("effect boundary collision or corrupt record")
        return record

    def transition(self, key: str, expected: set[str], target: str, *, external_receipt_id: str | None = None) -> EffectRecord:
        if target not in EFFECT_STATES:
            raise ValueError("invalid effect state")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            current = self.get(key)
            if current is None or current.state not in expected:
                raise RuntimeError(f"effect transition rejected: {current.state if current else 'MISSING'}->{target}")
            receipt = external_receipt_id if external_receipt_id is not None else current.external_receipt_id
            self.conn.execute(
                "UPDATE effects SET state=?,external_receipt_id=?,updated_at=? WHERE effect_key=?",
                (target, receipt, self._now(), key),
            )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return self.get(key)  # type: ignore[return-value]

    def precondition_refetched(self, key: str) -> EffectRecord:
        return self.transition(key, {"PREPARED", "PRECONDITION_REFETCH"}, "PRECONDITION_REFETCH")

    def committing(self, key: str) -> EffectRecord:
        return self.transition(key, {"PRECONDITION_REFETCH"}, "COMMITTING")

    def authoritative_verify(self, key: str) -> EffectRecord:
        return self.transition(key, {"COMMITTING", "AUTHORITATIVE_VERIFY"}, "AUTHORITATIVE_VERIFY")

    def committed(self, key: str, receipt_id: str) -> EffectRecord:
        if not receipt_id:
            raise ValueError("authoritative receipt id required")
        return self.transition(key, {"AUTHORITATIVE_VERIFY", "COMMITTED"}, "COMMITTED", external_receipt_id=receipt_id)

    def recover_committed(self, key: str, receipt_id: str) -> EffectRecord:
        if not receipt_id:
            raise ValueError("authoritative receipt id required")
        return self.transition(
            key,
            {"PREPARED", "PRECONDITION_REFETCH", "COMMITTING", "AUTHORITATIVE_VERIFY", "COMMITTED"},
            "COMMITTED",
            external_receipt_id=receipt_id,
        )

    def redrive_proven_absent(self, key: str) -> EffectRecord:
        """Re-arm only after the provider's authoritative state proves no effect."""
        return self.transition(key, {"COMMITTING", "AUTHORITATIVE_VERIFY"}, "PREPARED")
