from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from .capabilities import CapabilityResolver
from .workers import CanHandleDecision, WorkerJobSpec, WorkerManifest, WorkerRegistry


DENIED_INTEGRATION_CAPABILITIES = {
    "financial_execution",
    "payment_writer",
    "wallet",
    "wallet_signing",
    "web3_signer",
    "web3_signing",
    "blockchain_broadcast",
    "transaction_broadcast",
    "write_rpc",
}


class WorkerIntegrationRecord(BaseModel):
    """Registration truth is separate from runtime activation truth."""

    model_config = ConfigDict(extra="forbid")

    worker_id: str
    registered: bool
    active: bool
    source_pin: str | None = None
    source_pin_ancestor: str | None = None
    integration_audit_ref: str | None = None
    claim_authority: bool = False
    submission_authority: bool = False
    financial_authority: bool = False
    max_spend_usd: Decimal = Decimal("0")

    @field_validator("source_pin", "source_pin_ancestor")
    @classmethod
    def exact_optional_sha(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[0-9a-f]{40}", value):
            raise ValueError("integration source identity must be exact lowercase git SHA")
        return value

    @model_validator(mode="after")
    def registration_activation_boundary(self) -> "WorkerIntegrationRecord":
        if self.claim_authority or self.submission_authority or self.financial_authority:
            raise ValueError("worker integration authority must remain zero")
        if self.max_spend_usd != Decimal("0"):
            raise ValueError("worker integration spend must remain zero")
        if self.registered:
            if not self.source_pin or not self.source_pin_ancestor or not self.integration_audit_ref:
                raise ValueError("registered worker requires source pin, readiness ancestor, and audit ref")
        elif self.source_pin is not None or self.source_pin_ancestor is not None:
            raise ValueError("placeholder worker cannot advertise a final source pin")
        if self.active and not self.registered:
            raise ValueError("inactive readiness gate cannot be bypassed")
        return self


class WorkerIntegrationRegistry:
    def __init__(self, records: list[WorkerIntegrationRecord]):
        ids = [record.worker_id for record in records]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate worker integration record")
        self._records = {record.worker_id: record for record in records}

    @classmethod
    def from_directory(cls, path: Path) -> "WorkerIntegrationRegistry":
        return cls([
            WorkerIntegrationRecord.model_validate_json(item.read_text(encoding="utf-8"))
            for item in sorted(Path(path).glob("*.json"))
        ])

    def get(self, worker_id: str) -> WorkerIntegrationRecord:
        return self._records[worker_id]

    def all(self) -> list[WorkerIntegrationRecord]:
        return [self._records[key] for key in sorted(self._records)]

    @staticmethod
    def _assert_job_boundary(job: WorkerJobSpec) -> None:
        if job.max_spend_usd != Decimal("0"):
            raise ValueError("nonzero spend task rejected")
        requested = {str(value).strip().lower() for value in job.required_capabilities}
        denied = sorted(requested & DENIED_INTEGRATION_CAPABILITIES)
        if denied:
            raise ValueError("signing/broadcast/financial task rejected: " + ",".join(denied))

    def route_registered(
        self,
        job: WorkerJobSpec,
        manifests: WorkerRegistry,
    ) -> tuple[WorkerManifest, CanHandleDecision] | None:
        """Dry-run deterministic router over readiness-registered workers only.

        This method does not mint a WorkLease and does not imply activation.
        """
        self._assert_job_boundary(job)
        candidates: list[tuple[int, str, WorkerManifest, CanHandleDecision]] = []
        for record in self.all():
            if not record.registered:
                continue
            manifest = manifests.get(record.worker_id)
            decision = CapabilityResolver.can_handle(manifest.capabilities, job.required_capabilities)
            if not decision["can_handle"]:
                continue
            typed = CanHandleDecision(**decision)
            candidates.append((-typed.score, manifest.worker_id, manifest, typed))
        if not candidates:
            return None
        candidates.sort(key=lambda row: (row[0], row[1]))
        _, _, manifest, decision = candidates[0]
        return manifest, decision

    def route_active(
        self,
        job: WorkerJobSpec,
        manifests: WorkerRegistry,
        active_counts: dict[str, int] | None = None,
    ) -> tuple[WorkerManifest, CanHandleDecision] | None:
        """Activation gate remains fail-closed until an explicit later order flips both truths."""
        routed = self.route_registered(job, manifests)
        if routed is None:
            return None
        manifest, decision = routed
        record = self.get(manifest.worker_id)
        if not record.active or not manifest.enabled:
            return None
        active_counts = active_counts or {}
        if active_counts.get(manifest.worker_id, 0) >= manifest.max_concurrency:
            return None
        return manifest, decision
