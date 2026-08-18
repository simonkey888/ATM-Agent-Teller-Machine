from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from .gcp_zero_cost import GcpReadOnlyBackend
from .github_executor import BoundedGitHubExecutor
from .universal_radar import (
    AgentPolicy,
    ExecutorClass,
    FundingStatus,
    RadarDisposition,
    UniversalOpportunity,
    ZeroCostProviderGate,
    qualify,
    source_hash,
)


class ExecutorAvailability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executor_class: ExecutorClass
    available: bool
    zero_cost: bool
    material_execution_path: bool
    reason: str


def legacy_to_universal(opportunity: Any, snapshot: dict[str, Any], config: dict[str, Any]) -> UniversalOpportunity:
    """Translate already-verified canonical ATM input without granting new economic truth.

    Call only after the source adapter's authoritative freshness/funding/eligibility
    checks have passed. That prior deterministic gate is why funding_status may be
    represented as VERIFIED here; source text alone can never call this helper.
    """
    job = snapshot.get("job") if isinstance(snapshot.get("job"), dict) else snapshot
    if not isinstance(job, dict):
        job = {}
    requirements = job.get("requirements") if isinstance(job.get("requirements"), dict) else {}
    capabilities = requirements.get("capabilities") or requirements.get("skills") or []
    if isinstance(capabilities, str):
        capabilities = [capabilities]
    languages = requirements.get("languages") or []
    if isinstance(languages, str):
        languages = [languages]
    required = [str(value) for value in [*capabilities, *languages] if str(value).strip()]
    if not required:
        required = [str(job.get("category") or getattr(opportunity, "source", "unknown"))]

    policy = str(getattr(opportunity, "ai_policy", "UNKNOWN") or "UNKNOWN").upper()
    if policy in {"AGENT_NATIVE", "AGENT_ONLY"}:
        agent_policy = AgentPolicy.AGENT_ONLY
    elif policy in {"AGENT_ALLOWED", "AI_ALLOWED", "PUBLIC_AGENT"}:
        agent_policy = AgentPolicy.AGENT_ALLOWED
    elif policy in {"HUMAN_ONLY"}:
        agent_policy = AgentPolicy.HUMAN_ONLY
    elif policy in {"FORBIDDEN", "NO_AI", "PROHIBITED"}:
        agent_policy = AgentPolicy.PROHIBITED
    else:
        agent_policy = AgentPolicy.UNKNOWN

    reward = Decimal(str(getattr(opportunity, "reward_gross", 0) or 0))
    fees = Decimal(str(getattr(opportunity, "expected_fees", 0) or 0))
    net = max(Decimal("0"), reward - fees)
    payment = str(getattr(opportunity, "payment_method", "UNKNOWN") or "UNKNOWN")
    payout_recipient = str(config.get("payment_recipient_public_identifier") or os.getenv("ATM_BASE_WALLET_ADDRESS") or "").strip()
    withdrawal = "configured public payout destination" if payout_recipient and payment.upper() not in {"UNKNOWN", "NONE"} else "UNKNOWN"
    external_id = str(getattr(opportunity, "canonical_opportunity_id", "unknown")).split(":", 1)[-1]

    normalized = UniversalOpportunity(
        source=str(getattr(opportunity, "source", "unknown")),
        external_id=external_id,
        canonical_url=str(getattr(opportunity, "authoritative_url")),
        title=str(job.get("title") or getattr(opportunity, "canonical_opportunity_id", "Paid work")),
        description=str(job.get("description") or ""),
        category=str(job.get("category") or requirements.get("category") or "unknown"),
        created_at=getattr(opportunity, "created_at", None),
        updated_at=getattr(opportunity, "updated_at", None),
        deadline=getattr(opportunity, "deadline", None),
        payout_gross=reward,
        payout_currency=str(job.get("paymentCurrency") or "USD").upper(),
        payout_usd=net,
        fees=fees,
        payout_net=net,
        funding_status=FundingStatus.VERIFIED,
        funding_proof=dict(getattr(opportunity, "funding_proof", {}) or {}),
        escrow={
            "status": job.get("escrowStatus"),
            "tx": job.get("escrowTxHash"),
        },
        competition=int(getattr(opportunity, "competition", 0) or 0),
        claims=int(getattr(opportunity, "claims", 0) or 0),
        open_prs=int(getattr(opportunity, "open_prs", 0) or 0),
        eligibility=str(getattr(opportunity, "eligibility", "UNKNOWN")),
        agent_policy=agent_policy,
        claim_method=str(getattr(opportunity, "claim_method", "UNKNOWN")),
        submission_method=str(getattr(opportunity, "submission_method", "UNKNOWN")),
        payment_rail=payment,
        payout_latency=str(getattr(opportunity, "payout_latency", "UNKNOWN")),
        withdrawal_path=withdrawal,
        human_gate=None,
        estimated_execution_minutes=max(1, int(Decimal(str(getattr(opportunity, "expected_agent_hours", 1))) * 60)),
        required_capabilities=required,
        execution_cost_usd=Decimal("0"),
        source_state_hash=source_hash(snapshot),
    )
    floor = max(Decimal("5"), Decimal(str(config.get("min_reward_usd", 5))))
    return qualify(normalized, floor_usd=floor)


class UniversalExecutionRouter:
    """Capability-first gate. It proves a bounded zero-cost execution path before CLAIM.

    This router does not claim, submit, sign, spend, or mutate payment truth. A source
    adapter may reach its deterministic claim path only after this gate returns a
    material execution path. Specialist worker identity is intentionally not an input.
    """

    def __init__(
        self,
        *,
        provider_gate: ZeroCostProviderGate | None = None,
        github_executor: BoundedGitHubExecutor | None = None,
        gcp_backend: GcpReadOnlyBackend | None = None,
    ):
        self.provider_gate = provider_gate or ZeroCostProviderGate()
        self.github_executor = github_executor or BoundedGitHubExecutor()
        self.gcp_backend = gcp_backend or GcpReadOnlyBackend()

    def _model_ready(self) -> bool:
        try:
            return any(row.usable for row in self.provider_gate.inspect())
        except Exception:
            return False

    def _github_ready(self) -> bool:
        try:
            result = self.github_executor.capability_preflight()
            return bool(result.get("fork_branch_pr_possible"))
        except Exception:
            return False

    def _heavy_backend_ready(self) -> bool:
        # Activation is explicitly outside R1. Only an externally proven zero-cost
        # backend may turn this true; environment flags are evidence inputs, not spend authority.
        if os.getenv("ATM_OCI_EXECUTOR_AVAILABLE", "").strip().upper() == "YES":
            return True
        project = os.getenv("ATM_GCP_PROJECT", "").strip()
        region = os.getenv("ATM_GCP_REGION", "").strip()
        machine = os.getenv("ATM_GCP_MACHINE_TYPE", "").strip()
        if not (project and region and machine):
            return False
        decision = self.gcp_backend.validate_e2_micro(
            project=project,
            region=region,
            machine_type=machine,
            estimated_billable_usd=os.getenv("ATM_GCP_ESTIMATED_BILLABLE_USD", "0"),
        )
        return bool(decision.eligible)

    def probe(self, opportunity: UniversalOpportunity) -> ExecutorAvailability:
        klass = opportunity.executor_class
        if opportunity.execution_cost_usd != Decimal("0"):
            return ExecutorAvailability(
                executor_class=klass,
                available=False,
                zero_cost=False,
                material_execution_path=False,
                reason="EXECUTION_COST_NONZERO",
            )
        if klass == ExecutorClass.UNSUPPORTED:
            return ExecutorAvailability(
                executor_class=klass,
                available=False,
                zero_cost=True,
                material_execution_path=False,
                reason="UNSUPPORTED_CAPABILITY",
            )

        model_ready = self._model_ready()
        github_ready = self._github_ready()
        if klass in {ExecutorClass.HTTP_RESEARCH, ExecutorClass.CONTENT_GENERATION, ExecutorClass.DATA_EXTRACTION, ExecutorClass.LIGHT_QA}:
            return ExecutorAvailability(
                executor_class=klass,
                available=model_ready,
                zero_cost=True,
                material_execution_path=model_ready,
                reason="ZERO_COST_MODEL_READY" if model_ready else "ZERO_COST_MODEL_UNAVAILABLE",
            )
        if klass in {ExecutorClass.GITHUB_BOUNDED_PATCH, ExecutorClass.API_INTEGRATION, ExecutorClass.TEST_FIX, ExecutorClass.DOCS}:
            ready = model_ready and github_ready
            return ExecutorAvailability(
                executor_class=klass,
                available=ready,
                zero_cost=True,
                material_execution_path=ready,
                reason="MODEL_AND_GITHUB_READY" if ready else "MODEL_OR_GITHUB_WRITE_CAPABILITY_UNAVAILABLE",
            )
        if klass == ExecutorClass.HEAVY_BUILD:
            backend_ready = self._heavy_backend_ready()
            ready = model_ready and backend_ready
            return ExecutorAvailability(
                executor_class=klass,
                available=ready,
                zero_cost=True,
                material_execution_path=ready,
                reason="MODEL_AND_ZERO_COST_BACKEND_READY" if ready else "ZERO_COST_HEAVY_EXECUTOR_UNAVAILABLE",
            )
        return ExecutorAvailability(
            executor_class=klass,
            available=False,
            zero_cost=True,
            material_execution_path=False,
            reason="NO_MATERIAL_EXECUTION_PATH",
        )

    def claim_allowed(self, opportunity: UniversalOpportunity) -> tuple[bool, ExecutorAvailability]:
        availability = self.probe(opportunity)
        if opportunity.disposition == RadarDisposition.REJECT:
            return False, availability
        if opportunity.human_gate:
            return False, availability
        if opportunity.funding_status != FundingStatus.VERIFIED:
            return False, availability
        if opportunity.execution_cost_usd != Decimal("0"):
            return False, availability
        return bool(availability.available and availability.material_execution_path and availability.zero_cost), availability
