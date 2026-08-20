from __future__ import annotations

from dataclasses import asdict
from typing import Any, Iterable

from .autonomy_fabric import PromotionDecision
from .capability_registry import CAPABILITY_REGISTRY, capability_enabled
from .skill_forge import public_skill_forge_contract


REGISTRY_SCHEMA_V3 = "ATM_CAPABILITY_REGISTRY_V3"


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


def public_registry(promotions: Iterable[PromotionDecision] = ()) -> dict[str, Any]:
    promotion_rows = []
    for decision in promotions:
        promotion_rows.append(
            {
                "capability_id": decision.capability_id,
                "promoted": decision.promoted,
                "reasons": list(decision.reasons),
                "decided_at": decision.decided_at,
                "schema": decision.schema,
            }
        )
    return {
        "schema": REGISTRY_SCHEMA_V3,
        "preserved_proven_capabilities": [row.capability_id for row in CAPABILITY_REGISTRY if capability_enabled(row.capability_id)],
        "capabilities": [_v3_contract(row) for row in CAPABILITY_REGISTRY],
        "promotion_policy": {
            "authority": "DETERMINISTIC_PROMOTION_GATE_ONLY",
            "llm_self_enable": False,
            "forge_self_enable": False,
            "required_fixture_classes": ["HAPPY", "EDGE", "ADVERSARIAL", "MARKET_SHAPED"],
            "owner_cost_ceiling_usd": "0",
            "fail_closed": True,
        },
        "skill_forge": public_skill_forge_contract(),
        "promotion_decisions": promotion_rows,
    }
