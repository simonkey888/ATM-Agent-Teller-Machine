from __future__ import annotations

import re
from typing import Any, Iterable

ZUNGUN_CAPABILITIES = {
    "zungun.network_resilience",
    "zungun.offline_sync",
    "zungun.idempotency_retry",
    "zungun.ambiguous_timeout",
    "zungun.reconciliation",
    "zungun.android_background_work",
    "zungun.resumable_transfer",
    "zungun.blackout_testing",
    "zungun.reliability_audit",
    "zungun.evidence_generation",
}

CANONICAL_CAPABILITIES = {
    "model",
    "git",
    "github",
    "filesystem",
    "shell",
    "browser",
    "python",
    "node",
    "web3_readonly",
    "benchmark",
    "evidence",
} | ZUNGUN_CAPABILITIES

RESERVED_DISABLED = {"cuda", "web3_signer", "payment_writer"}
HARD_DENY = {"web3_signer", "payment_writer"}
FINANCIAL_REQUIREMENTS = {
    "financial_execution",
    "trading",
    "wallet_signing",
    "wallet",
    "blockchain_broadcast",
    "web3_signing",
}

# These are normalized requirement labels, not free-text keywords. Ingress adapters
# may populate them only from structured platform/features/constraints/failure-mode
# fields or from an explicit human/auditor normalization artifact.
ZUNGUN_REQUIREMENT_CAPABILITIES = {
    "network_resilience": "zungun.network_resilience",
    "intermittent_connectivity": "zungun.network_resilience",
    "network_handoff": "zungun.network_resilience",
    "offline_sync": "zungun.offline_sync",
    "offline_first": "zungun.offline_sync",
    "durable_outbox": "zungun.offline_sync",
    "idempotency": "zungun.idempotency_retry",
    "retry_safety": "zungun.idempotency_retry",
    "duplicate_operation": "zungun.idempotency_retry",
    "ambiguous_timeout": "zungun.ambiguous_timeout",
    "unknown_outcome": "zungun.ambiguous_timeout",
    "reconciliation": "zungun.reconciliation",
    "receiver_confirmation": "zungun.reconciliation",
    "process_death": "zungun.android_background_work",
    "reboot_recovery": "zungun.android_background_work",
    "android_background_work": "zungun.android_background_work",
    "workmanager": "zungun.android_background_work",
    "resumable_transfer": "zungun.resumable_transfer",
    "partial_transfer": "zungun.resumable_transfer",
    "blackout_testing": "zungun.blackout_testing",
    "reliability_audit": "zungun.reliability_audit",
    "evidence_continuity": "zungun.evidence_generation",
}

STRUCTURED_FIELD_ALIASES = {
    "offline-first": "offline_first",
    "offline first": "offline_first",
    "offline": "offline_sync",
    "sync queue": "durable_outbox",
    "durable queue": "durable_outbox",
    "durable outbox": "durable_outbox",
    "retry": "retry_safety",
    "backoff": "retry_safety",
    "idempotent": "idempotency",
    "idempotency": "idempotency",
    "duplicate operation": "duplicate_operation",
    "ambiguous timeout": "ambiguous_timeout",
    "unknown outcome": "unknown_outcome",
    "reconcile": "reconciliation",
    "reconciliation": "reconciliation",
    "receiver confirmation": "receiver_confirmation",
    "process death": "process_death",
    "reboot": "reboot_recovery",
    "reboot recovery": "reboot_recovery",
    "android": "android_background_work",
    "workmanager": "workmanager",
    "background sync": "android_background_work",
    "resumable upload": "resumable_transfer",
    "resumable download": "resumable_transfer",
    "partial transfer": "partial_transfer",
    "blackout": "blackout_testing",
    "intermittent connectivity": "intermittent_connectivity",
    "network failure": "network_resilience",
    "network handoff": "network_handoff",
    "evidence continuity": "evidence_continuity",
    "reliability audit": "reliability_audit",
    "financial execution": "financial_execution",
    "trading": "trading",
    "wallet signing": "wallet_signing",
}


def _norm(values: Iterable[str]) -> list[str]:
    return sorted({str(value).strip().lower() for value in values if str(value).strip()})


def _structured_token(value: Any) -> str | None:
    text = re.sub(r"[_-]+", " ", str(value).strip().lower())
    text = re.sub(r"\s+", " ", text)
    alias = STRUCTURED_FIELD_ALIASES.get(text)
    if alias:
        return alias
    underscored = text.replace(" ", "_")
    if underscored in ZUNGUN_REQUIREMENT_CAPABILITIES or underscored in FINANCIAL_REQUIREMENTS:
        return underscored
    return None


class CapabilityResolver:
    """Deterministic capability matching; never grants financial/signing authority."""

    @staticmethod
    def assert_manifest_safe(capabilities: Iterable[str]) -> None:
        caps = set(_norm(capabilities))
        forbidden = caps & HARD_DENY
        if forbidden:
            raise ValueError("financial/signing capabilities are disabled: " + ",".join(sorted(forbidden)))

    @staticmethod
    def assert_job_safe(capabilities: Iterable[str]) -> None:
        caps = set(_norm(capabilities))
        forbidden = caps & RESERVED_DISABLED
        if forbidden:
            raise ValueError("job requested reserved disabled capabilities: " + ",".join(sorted(forbidden)))

    @staticmethod
    def normalize_structured_requirements(payload: dict[str, Any] | None) -> list[str]:
        """Normalize only explicit structured requirement fields; never scan task prose."""
        payload = payload or {}
        values: list[Any] = []
        for key in ("platforms", "features", "constraints", "failure_modes", "reliability", "delivery_semantics"):
            raw = payload.get(key)
            if raw is None:
                continue
            values.extend(raw if isinstance(raw, list) else [raw])
        normalized = {_structured_token(value) for value in values}
        return sorted(value for value in normalized if value)

    @staticmethod
    def required_for_structured(requirements: Iterable[str]) -> list[str]:
        normalized = set(_norm(requirements))
        if normalized & FINANCIAL_REQUIREMENTS:
            return ["financial_execution"]
        specialized = sorted({ZUNGUN_REQUIREMENT_CAPABILITIES[item] for item in normalized if item in ZUNGUN_REQUIREMENT_CAPABILITIES})
        if not specialized:
            return []
        return ["git", "github", "filesystem", "shell", "node", "evidence", *specialized]

    @staticmethod
    def can_handle(manifest_capabilities: Iterable[str], required_capabilities: Iterable[str]) -> dict[str, object]:
        offered = set(_norm(manifest_capabilities))
        required = set(_norm(required_capabilities))
        CapabilityResolver.assert_manifest_safe(offered)
        CapabilityResolver.assert_job_safe(required)
        missing = sorted(required - offered)
        if missing:
            return {"can_handle": False, "score": 0, "reason": "missing:" + ",".join(missing)}
        # Stable score rewards tight useful coverage instead of indiscriminate tool bundles.
        # Specialist requirements necessarily exclude generic workers that do not declare them.
        extra = len(offered - required)
        return {"can_handle": True, "score": max(1, 100 - min(50, extra)), "reason": "capability_match"}
