from __future__ import annotations

from typing import Any

from .autonomy_fabric import FreeProviderFabric, ProviderState
from .provider_registry import PROVIDERS, admitted_provider_ids


PROVIDER_FABRIC_SCHEMA = "ATM_FREE_PROVIDER_FABRIC_V1"


def build_runtime_provider_fabric() -> FreeProviderFabric:
    admitted = set(admitted_provider_ids())
    # Contract admission is necessary but never sufficient for runtime health. The existing
    # ZeroCostModelGate owns the live model/rate/billing probe immediately before use.
    # This public/static fabric therefore stays UNVERIFIED until execution-time evidence
    # exists instead of turning credential presence into a false health claim.
    fabric = FreeProviderFabric(admitted)
    for provider_id in admitted:
        fabric.providers[provider_id].state = ProviderState.UNVERIFIED
        fabric.providers[provider_id].last_error = "LIVE_ZERO_COST_PROBE_REQUIRED_AT_EXECUTION"
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
    status["contract_admission_is_not_live_health"] = True
    status["all_free_down_behavior"] = "DEGRADED_DETERMINISTIC_SEARCH"
    status["auto_overage"] = False
    return status
