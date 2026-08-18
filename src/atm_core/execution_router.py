from __future__ import annotations

import os
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from .gcp_zero_cost import GcpReadOnlyBackend
from .github_executor import BoundedGitHubExecutor
from .universal_radar import ExecutorClass, FundingStatus, RadarDisposition, UniversalOpportunity, ZeroCostProviderGate


class ExecutorAvailability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executor_class: ExecutorClass
    available: bool
    zero_cost: bool
    material_execution_path: bool
    reason: str


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
