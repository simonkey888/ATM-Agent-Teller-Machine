from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .autonomy_fabric import PromotionDecision
from .capability_registry import CAPABILITY_REGISTRY, capability_enabled
from .skill_forge import ForgeDecision, public_skill_forge_contract


REGISTRY_SCHEMA_V3 = "ATM_CAPABILITY_REGISTRY_V3"
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STORE = ROOT / ".atm" / "capability-registry-v3.sqlite3"


def _v3_contract(row: Any) -> dict[str, Any]:
    base = asdict(row)
    base["contracts"] = {
        "executor": row.executor_id,
        "checker": row.checker_id,
        "tools": list(row.required_tools),
        "network": row.network_policy,
        "license_commercial": row.commercial_use_contract,
        "resources": {
            "owner_cost_ceiling_usd": row.cost_ceiling_usd,
            "max_runtime_seconds": row.max_runtime_seconds,
            "max_memory_mb": row.max_memory_mb,
            "sandbox_profile": row.sandbox_profile,
        },
        "artifact": row.artifact_contract,
        "failure": row.failure_policy,
    }
    base["promotion_authority"] = "DETERMINISTIC_PROMOTION_GATE_ONLY"
    return base


class CapabilityRegistryV3Store:
    """Durable supervisor-owned mutation log for promoted capabilities."""

    def __init__(self, path: Path = DEFAULT_STORE):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS promotions(
               capability_id TEXT PRIMARY KEY,
               activation_enabled INTEGER NOT NULL,
               synthetic INTEGER NOT NULL,
               evidence_digest TEXT NOT NULL,
               sandbox_receipt_digest TEXT NOT NULL,
               episode_id TEXT,
               gap_fingerprint TEXT,
               decision_json TEXT NOT NULL,
               promoted_at TEXT NOT NULL,
               promoted_by TEXT NOT NULL)"""
        )

    def close(self) -> None:
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.conn.close()

    def persist(
        self,
        decision: ForgeDecision,
        *,
        evidence_digest: str,
        sandbox_receipt_digest: str,
        synthetic: bool,
        episode_id: str | None,
        gap_fingerprint: str | None,
        supervisor_authority: str,
    ) -> dict[str, Any]:
        if not supervisor_authority.startswith("ATM_SUPERVISOR"):
            raise ValueError("registry mutation requires deterministic supervisor authority")
        if not decision.promoted:
            raise ValueError("failed forge decision cannot mutate Registry V3")
        enabled = not bool(synthetic)
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "schema": decision.schema,
            "capability_id": decision.capability_id,
            "promoted": decision.promoted,
            "failures": list(decision.failures),
            "registry_mutated": True,
            "activation_enabled": enabled,
            "synthetic_non_economic": bool(synthetic),
        }
        self.conn.execute(
            "INSERT INTO promotions(capability_id,activation_enabled,synthetic,evidence_digest,sandbox_receipt_digest,episode_id,gap_fingerprint,decision_json,promoted_at,promoted_by) VALUES(?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(capability_id) DO UPDATE SET activation_enabled=excluded.activation_enabled,synthetic=excluded.synthetic,evidence_digest=excluded.evidence_digest,sandbox_receipt_digest=excluded.sandbox_receipt_digest,episode_id=excluded.episode_id,gap_fingerprint=excluded.gap_fingerprint,decision_json=excluded.decision_json,promoted_at=excluded.promoted_at,promoted_by=excluded.promoted_by",
            (decision.capability_id, int(enabled), int(bool(synthetic)), str(evidence_digest), str(sandbox_receipt_digest), episode_id, gap_fingerprint, json.dumps(payload, sort_keys=True), now, supervisor_authority),
        )
        return {**payload, "promoted_at": now, "promoted_by": supervisor_authority}

    def rows(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.conn.execute("SELECT * FROM promotions ORDER BY promoted_at DESC").fetchall()]

    def enabled_ids(self) -> tuple[str, ...]:
        rows = self.conn.execute("SELECT capability_id FROM promotions WHERE activation_enabled=1 AND synthetic=0 ORDER BY capability_id").fetchall()
        return tuple(str(row[0]) for row in rows)


def _persisted_rows(path: Path = DEFAULT_STORE) -> list[dict[str, Any]]:
    if not Path(path).exists():
        return []
    store = CapabilityRegistryV3Store(path)
    try:
        rows = []
        for row in store.rows():
            rows.append({
                "capability_id": row["capability_id"],
                "activation_enabled": bool(row["activation_enabled"]),
                "synthetic_non_economic": bool(row["synthetic"]),
                "evidence_digest": row["evidence_digest"],
                "sandbox_receipt_digest": row["sandbox_receipt_digest"],
                "episode_id": row["episode_id"],
                "gap_fingerprint": row["gap_fingerprint"],
                "decision": json.loads(str(row["decision_json"])),
                "promoted_at": row["promoted_at"],
                "promoted_by": row["promoted_by"],
            })
        return rows
    finally:
        store.close()


def public_registry(promotions: Iterable[PromotionDecision] = (), *, store_path: Path = DEFAULT_STORE) -> dict[str, Any]:
    promotion_rows = []
    for decision in promotions:
        promotion_rows.append({
            "capability_id": decision.capability_id,
            "promoted": decision.promoted,
            "reasons": list(decision.reasons),
            "decided_at": decision.decided_at,
            "schema": decision.schema,
        })
    persisted = _persisted_rows(store_path)
    return {
        "schema": REGISTRY_SCHEMA_V3,
        "preserved_proven_capabilities": [row.capability_id for row in CAPABILITY_REGISTRY if capability_enabled(row.capability_id)],
        "capabilities": [_v3_contract(row) for row in CAPABILITY_REGISTRY],
        "persisted_promotions": persisted,
        "persisted_enabled_capabilities": [row["capability_id"] for row in persisted if row["activation_enabled"] and not row["synthetic_non_economic"]],
        "promotion_policy": {
            "authority": "DETERMINISTIC_PROMOTION_GATE_ONLY",
            "llm_self_enable": False,
            "forge_self_enable": False,
            "synthetic_can_enable_economic_execution": False,
            "required_fixture_classes": ["HAPPY", "EDGE", "ADVERSARIAL", "MARKET_SHAPED"],
            "owner_cost_ceiling_usd": "0",
            "fail_closed": True,
        },
        "skill_forge": public_skill_forge_contract(),
        "promotion_decisions": promotion_rows,
    }
