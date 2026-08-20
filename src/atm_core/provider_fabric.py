from __future__ import annotations

from typing import Any

from .autonomy_fabric import FreeProviderFabric
from .provider_registry import PROVIDERS, admitted_provider_ids


PROVIDER_FABRIC_SCHEMA = "ATM_FREE_PROVIDER_FABRIC_V1"


def build_runtime_provider_fabric() -> FreeProviderFabric:
    admitted = set(admitted_provider_ids())
    # Admission remains distinct from live usability: credentials are an input to the
    # existing live model probe. Providers not admitted for paid-task use never route.
    fabric = FreeProviderFabric(admitted)
    for row in PROVIDERS:
        if row.provider_id not in admitted:
            fabric.register_unverified(row.provider_id)
    return fabric


def public_provider_fabric() -> dict[str, Any]:
    fabric = build_runtime_provider_fabric()
    status = fabric.public_status()
    status["schema"] = PROVIDER_FABRIC_SCHEMA
    status["admitted_by_contract"] = list(admitted_provider_ids())
    status["live_probe_authority"] = "ZERO_COST_MODEL_GATE_AT_EXECUTION_TIME"
    status["all_free_down_behavior"] = "DEGRADED_DETERMINISTIC_SEARCH"
    status["auto_overage"] = False
    return status
