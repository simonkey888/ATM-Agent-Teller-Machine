from __future__ import annotations

import os
from dataclasses import asdict, dataclass


PROVIDER_REGISTRY_SCHEMA = "ATM_FREE_MODEL_PROVIDER_REGISTRY_V2"


@dataclass(frozen=True)
class ProviderContract:
    provider_id: str
    base_url: str
    model_id: str
    modalities: tuple[str, ...]
    context: str
    rate_limits: str
    free_tier_type: str
    commercial_use_allowed: bool
    paid_task_use_allowed: bool
    credit_card_required: bool
    auto_overage_possible: bool
    data_retention_training_disclosure: str
    region_restrictions: str
    credential_env: str
    last_official_verification: str
    official_terms_url: str
    official_pricing_url: str
    admission: str

    def public_status(self) -> dict[str, object]:
        data = asdict(self)
        data["credential_present"] = bool(os.getenv(self.credential_env, "").strip())
        data["health"] = "REQUIRES_LIVE_PROBE" if data["credential_present"] else "NO_CREDENTIAL"
        return data


PROVIDERS: tuple[ProviderContract, ...] = (
    ProviderContract(
        provider_id="gemini-api-free",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model_id="gemma-4-31b-it",
        modalities=("text",),
        context="OFFICIAL_MODEL_METADATA_AT_RUNTIME",
        rate_limits="OFFICIAL_FREE_TIER_DYNAMIC; FAIL_CLOSED_ON_429",
        free_tier_type="UNPAID_QUOTA_NO_BILLING_ACCOUNT",
        commercial_use_allowed=True,
        paid_task_use_allowed=True,
        credit_card_required=False,
        auto_overage_possible=False,
        data_retention_training_disclosure="UNPAID_CONTENT_MAY_BE_USED_TO_IMPROVE_GOOGLE_PRODUCTS; PUBLIC_NONCONFIDENTIAL_TASK_INPUT_ONLY",
        region_restrictions="AVAILABLE_REGIONS_ONLY; ARGENTINA_CHECK_AT_RUNTIME",
        credential_env="GEMINI_API_KEY",
        last_official_verification="2026-08-20",
        official_terms_url="https://ai.google.dev/gemini-api/terms",
        official_pricing_url="https://ai.google.dev/gemini-api/docs/pricing",
        admission="READY_IF_LIVE_MODEL_AND_RATE_PROBE_PASS_AND_PROJECT_UNBILLED",
    ),
    ProviderContract(
        provider_id="opencode-zen-free-catalog",
        base_url="https://opencode.ai/zen/v1",
        model_id="DISCOVERY_ONLY",
        modalities=("text",),
        context="MODEL_SPECIFIC",
        rate_limits="DYNAMIC",
        free_tier_type="LIMITED_TIME_MODELS",
        commercial_use_allowed=False,
        paid_task_use_allowed=False,
        credit_card_required=False,
        auto_overage_possible=True,
        data_retention_training_disclosure="MODEL_SPECIFIC; MULTIPLE FREE MODELS MAY USE DATA_FOR_IMPROVEMENT",
        region_restrictions="MODEL_SPECIFIC",
        credential_env="OPENCODE_API_KEY",
        last_official_verification="2026-08-20",
        official_terms_url="https://opencode.ai/legal/terms-of-service",
        official_pricing_url="https://opencode.ai/docs/zen/",
        admission="DISCOVERY_ONLY_UNTIL_MODEL_COMMERCIAL_TERMS_AND_NO_OVERAGE_PROVEN",
    ),
)


def admitted_provider_ids() -> tuple[str, ...]:
    return tuple(
        row.provider_id
        for row in PROVIDERS
        if row.commercial_use_allowed
        and row.paid_task_use_allowed
        and not row.credit_card_required
        and not row.auto_overage_possible
        and row.admission.startswith("READY_IF_")
    )


def public_registry() -> dict[str, object]:
    return {"schema": PROVIDER_REGISTRY_SCHEMA, "providers": [row.public_status() for row in PROVIDERS]}
