from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .provider_registry import PROVIDERS, admitted_provider_ids
from .zero_cost_model import ProviderHealth, ZeroCostModelGate


PROVIDER_FABRIC_SCHEMA = "ATM_FREE_PROVIDER_FABRIC_V2"
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROVIDER_STATE = ROOT / ".atm" / "free-provider-fabric.sqlite3"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DurableFreeProviderFabric:
    """Execution provider state. HEALTHY requires a live zero-cost probe; config never suffices."""

    def __init__(self, path: Path = DEFAULT_PROVIDER_STATE):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS provider_routes(
               provider_id TEXT NOT NULL,
               model_id TEXT NOT NULL,
               state TEXT NOT NULL,
               failures INTEGER NOT NULL DEFAULT 0,
               opened_until TEXT,
               last_error TEXT,
               last_probe_at TEXT,
               probe_evidence_json TEXT,
               PRIMARY KEY(provider_id,model_id))"""
        )

    def close(self) -> None:
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.conn.close()

    def _row(self, provider_id: str, model_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM provider_routes WHERE provider_id=? AND model_id=?", (provider_id, model_id)).fetchone()

    def ensure_route(self, provider_id: str, model_id: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO provider_routes(provider_id,model_id,state,failures,last_error) VALUES(?,?,'UNVERIFIED',0,'LIVE_ZERO_COST_PROBE_REQUIRED')",
            (provider_id, model_id),
        )

    def circuit_open(self, provider_id: str, model_id: str) -> bool:
        row = self._row(provider_id, model_id)
        if not row or not row["opened_until"]:
            return False
        reopen = datetime.fromisoformat(str(row["opened_until"]).replace("Z", "+00:00"))
        return reopen > utcnow()

    def record_probe(self, provider_id: str, model_id: str, health: ProviderHealth) -> dict[str, Any]:
        self.ensure_route(provider_id, model_id)
        now = utcnow().isoformat()
        proven = bool(health.usable and health.zero_cost_tier_proven and health.rate_limit_usable and health.model_available and health.credential_present)
        state = "HEALTHY" if proven else "UNVERIFIED"
        evidence = {
            "provider": health.provider, "model": health.model,
            "credential_present": bool(health.credential_present), "model_available": bool(health.model_available),
            "zero_cost_tier_proven": bool(health.zero_cost_tier_proven), "rate_limit_usable": bool(health.rate_limit_usable),
            "usable": bool(health.usable), "reason": str(health.reason),
        }
        self.conn.execute(
            "UPDATE provider_routes SET state=?,failures=CASE WHEN ?='HEALTHY' THEN 0 ELSE failures END,opened_until=CASE WHEN ?='HEALTHY' THEN NULL ELSE opened_until END,last_error=?,last_probe_at=?,probe_evidence_json=? WHERE provider_id=? AND model_id=?",
            (state, state, state, None if proven else health.reason, now, json.dumps(evidence, sort_keys=True), provider_id, model_id),
        )
        return self.status(provider_id, model_id)

    def record_failure(self, provider_id: str, model_id: str, error_class: str) -> dict[str, Any]:
        self.ensure_route(provider_id, model_id)
        row = self._row(provider_id, model_id)
        failures = (int(row["failures"]) if row else 0) + 1
        error = str(error_class).upper()
        if "QUOTA" in error or "EXHAUST" in error:
            state, seconds = "QUOTA_EXHAUSTED", 3600
        elif "429" in error or "RATE" in error:
            state, seconds = "COOLDOWN", 900
        elif "TIMEOUT" in error or "NETWORK" in error or "URLERROR" in error:
            state, seconds = "COOLDOWN", 300
        else:
            state, seconds = "COOLDOWN", 180
        opened = (utcnow() + timedelta(seconds=seconds)).isoformat()
        self.conn.execute(
            "UPDATE provider_routes SET state=?,failures=?,opened_until=?,last_error=? WHERE provider_id=? AND model_id=?",
            (state, failures, opened, error, provider_id, model_id),
        )
        return self.status(provider_id, model_id)

    def status(self, provider_id: str, model_id: str) -> dict[str, Any]:
        self.ensure_route(provider_id, model_id)
        row = self._row(provider_id, model_id)
        assert row is not None
        available = str(row["state"]) == "HEALTHY" and not self.circuit_open(provider_id, model_id)
        return {
            "provider_id": provider_id, "model_id": model_id, "state": row["state"],
            "failures": int(row["failures"]), "opened_until": row["opened_until"],
            "last_error": row["last_error"], "last_probe_at": row["last_probe_at"],
            "probe_evidence": json.loads(str(row["probe_evidence_json"])) if row["probe_evidence_json"] else None,
            "available": available,
        }

    def route(self, route_candidates: list[tuple[str, str]]) -> dict[str, Any]:
        for provider_id, model_id in route_candidates:
            if provider_id not in set(admitted_provider_ids()):
                continue
            status = self.status(provider_id, model_id)
            if status["available"]:
                return {"state": "READY", "provider_id": provider_id, "model_id": model_id, "paid_fallback": False}
        return {"state": "DEGRADED_DETERMINISTIC_SEARCH", "provider_id": None, "model_id": None, "paid_fallback": False}

    def rows(self) -> list[dict[str, Any]]:
        result = []
        for row in self.conn.execute("SELECT provider_id,model_id FROM provider_routes ORDER BY provider_id,model_id"):
            result.append(self.status(str(row["provider_id"]), str(row["model_id"])))
        return result


def probe_gemini_route(fabric: DurableFreeProviderFabric, gate: ZeroCostModelGate, model: str) -> dict[str, Any]:
    provider_id = "gemini-api-free"
    fabric.ensure_route(provider_id, model)
    if fabric.circuit_open(provider_id, model):
        return fabric.status(provider_id, model)
    try:
        health = gate.google_health(model)
    except Exception as exc:
        fabric.record_failure(provider_id, model, type(exc).__name__)
        return fabric.status(provider_id, model)
    return fabric.record_probe(provider_id, model, health)


def public_provider_fabric(path: Path = DEFAULT_PROVIDER_STATE) -> dict[str, Any]:
    fabric = DurableFreeProviderFabric(path)
    try:
        rows = fabric.rows()
        healthy = [row for row in rows if row["available"]]
        return {
            "schema": PROVIDER_FABRIC_SCHEMA,
            "durable": True,
            "admitted_by_contract": list(admitted_provider_ids()),
            "contract_admission_is_not_live_health": True,
            "healthy_requires_live_probe": True,
            "routes": rows,
            "healthy_route_count": len(healthy),
            "route": {"state": "READY", "provider_id": healthy[0]["provider_id"], "model_id": healthy[0]["model_id"], "paid_fallback": False} if healthy else {"state": "DEGRADED_DETERMINISTIC_SEARCH", "provider_id": None, "model_id": None, "paid_fallback": False},
            "all_free_down_behavior": "DEGRADED_DETERMINISTIC_SEARCH",
            "auto_overage": False,
            "paid_fallback": False,
        }
    finally:
        fabric.close()
