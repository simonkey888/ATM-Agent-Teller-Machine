from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .capabilities import CapabilityResolver, ZUNGUN_CAPABILITIES
from .workers import (
    CanHandleDecision,
    PrepareDecision,
    WorkerJobSpec,
    WorkerManifest,
    WorkerResult,
    WorkLease,
    canonical_hash,
)

LINK_DOCTOR_RULES = (
    "ZL001_UNSAFE_SIDE_EFFECT_RETRY",
    "ZL002_MISSING_IDEMPOTENCY_CONTRACT",
    "ZL003_STALE_CONNECTIVITY_BOOLEAN",
    "ZL004_NO_DURABLE_MUTATION_OUTBOX",
    "ZL005_AMBIGUOUS_TIMEOUT_COLLAPSED_TO_FAILURE",
    "ZL006_AMBIGUOUS_TIMEOUT_COLLAPSED_TO_SUCCESS",
    "ZL007_NO_SEMANTIC_REVALIDATION_FOR_DELAYED_ACTION",
    "ZL008_MONOLITHIC_LARGE_MUTATION",
    "ZL009_NON_RESUMABLE_LARGE_UPLOAD",
    "ZL010_BULK_CAN_STARVE_CRITICAL",
    "ZL011_NO_PROCESS_DEATH_RECOVERY",
    "ZL012_NO_REBOOT_RECOVERY",
    "ZL013_SECRET_OR_PAYLOAD_DIAGNOSTIC_RISK",
    "ZL014_NO_OPERATION_EXPIRY",
    "ZL015_NO_RECEIVER_RECONCILIATION_PATH",
    "ZL016_CONNECTED_WITHOUT_VALIDATION_CHECK",
)

FORBIDDEN_RESULT_KEYS = {
    "paid",
    "payment_status",
    "withdrawable",
    "withdrawable_usdc",
    "realized_withdrawable_usd",
    "external_accepted",
    "accepted",
    "money_ledger",
    "fsm_state",
}
SECRET_KEY_FRAGMENTS = ("private_key", "seed", "mnemonic", "authorization", "bearer", "github_token")
UNKNOWN_RULES = {
    "ZL005_AMBIGUOUS_TIMEOUT_COLLAPSED_TO_FAILURE",
    "ZL006_AMBIGUOUS_TIMEOUT_COLLAPSED_TO_SUCCESS",
    "ZL015_NO_RECEIVER_RECONCILIATION_PATH",
}


class LinkDoctorFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    severity: str
    path: str
    evidence: str
    explanation: str
    limitation: str
    assurance_tier: str
    status: str

    @field_validator("rule_id")
    @classmethod
    def known_rule(cls, value: str) -> str:
        if value not in LINK_DOCTOR_RULES:
            raise ValueError("unknown Link Doctor rule")
        return value

    @field_validator("severity")
    @classmethod
    def severity_enum(cls, value: str) -> str:
        value = value.upper()
        if value not in {"ERROR", "WARNING", "INFO"}:
            raise ValueError("unsupported finding severity")
        return value

    @field_validator("status")
    @classmethod
    def status_enum(cls, value: str) -> str:
        value = value.upper()
        if value not in {"WARN", "UNKNOWN"}:
            raise ValueError("a finding cannot be PASS")
        return value


class LinkDoctorReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = "zungun-link-doctor"
    source_head: str
    findings: list[LinkDoctorFinding] = Field(default_factory=list)
    deterministic: bool = True
    overall_status: str

    @field_validator("source_head")
    @classmethod
    def source_head_exact(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{40}", value):
            raise ValueError("Link Doctor source_head must be an exact git SHA")
        return value

    @field_validator("overall_status")
    @classmethod
    def preserve_unknown(cls, value: str) -> str:
        value = value.upper()
        if value not in {"PASS", "WARN", "UNKNOWN"}:
            raise ValueError("unsupported Link Doctor overall status")
        return value


class ZungunWorkerAdapter:
    """Worker-Fabric adapter for the zero-authority Zungun specialist.

    The adapter validates immutable ATM inputs and public worker evidence. It never
    mints/renews WorkLeases, executes financial actions, submits upstream work, or
    writes ATM lifecycle/payment truth.
    """

    def __init__(self, manifest: WorkerManifest):
        if manifest.worker_id != "zungun":
            raise ValueError("ZungunWorkerAdapter requires zungun manifest")
        if manifest.financial_authority or manifest.claim_authority or manifest.submission_authority or manifest.model_authority:
            raise ValueError("Zungun worker must be zero-authority")
        self.manifest = manifest
        self._handles: dict[str, str] = {}
        self._results: dict[str, WorkerResult] = {}

    def can_handle(self, job: WorkerJobSpec) -> CanHandleDecision:
        decision = CapabilityResolver.can_handle(self.manifest.capabilities, job.required_capabilities)
        return CanHandleDecision(**decision)

    def prepare(self, job: WorkerJobSpec, lease: WorkLease) -> PrepareDecision:
        self._assert_bound(job, lease)
        if not set(job.required_capabilities).intersection(ZUNGUN_CAPABILITIES):
            return PrepareDecision(ready=False, reason="not_zungun_specialized")
        if job.max_spend_usd != 0:
            return PrepareDecision(ready=False, reason="nonzero_cost_forbidden")
        if job.target_base_sha is None:
            return PrepareDecision(ready=False, reason="target_base_sha_required")
        if not job.allowed_paths:
            return PrepareDecision(ready=False, reason="allowed_paths_required")
        return PrepareDecision(ready=True, reason="immutable_scope_and_link_doctor_preflight_ready")

    def execute(self, job: WorkerJobSpec, lease: WorkLease) -> str:
        decision = self.prepare(job, lease)
        if not decision.ready:
            raise ValueError(decision.reason)
        handle = "zungun:" + canonical_hash(
            {"job_id": job.job_id, "scope_hash": job.scope_hash, "lease_id": lease.lease_id, "worker_id": lease.worker_id}
        )[:32]
        self._handles[handle] = "WORKING"
        return handle

    def status(self, handle: str) -> str:
        if handle not in self._handles:
            raise KeyError(handle)
        return self._handles[handle]

    def record_external_result(self, handle: str, result: WorkerResult, raw_envelope: dict[str, Any]) -> None:
        if handle not in self._handles:
            raise KeyError(handle)
        validate_worker_output_authority(raw_envelope)
        if result.worker_id != "zungun":
            raise ValueError("worker identity mismatch")
        self._results[handle] = result
        self._handles[handle] = "READY_FOR_CHECK"

    def collect(self, handle: str) -> WorkerResult:
        if handle not in self._results:
            raise RuntimeError("external result not yet bound by ATM delivery authority")
        return self._results[handle]

    def cancel(self, handle: str) -> bool:
        if handle not in self._handles:
            return False
        self._handles[handle] = "CANCELLED"
        return True

    @staticmethod
    def preflight_contract(job: WorkerJobSpec) -> dict[str, Any]:
        return {
            "tool": "zungun-link-doctor",
            "rules": list(LINK_DOCTOR_RULES),
            "target_repository": job.repository_or_input,
            "target_base_sha": job.target_base_sha,
            "allowed_paths": list(job.allowed_paths),
            "expected_deliverable": job.expected_deliverable,
            "semantics": {
                "UNKNOWN_is_PASS": False,
                "AMBIGUOUS_is_SUCCESS": False,
                "transport_accepted_is_durable_receiver_effect": False,
                "http_2xx_is_business_effect": False,
                "retryable_is_idempotent": False,
                "network_available_is_endpoint_reachable": False,
                "emulator_is_OEM_proof": False,
            },
        }

    @staticmethod
    def validate_link_doctor(receipt: LinkDoctorReceipt) -> None:
        if not receipt.deterministic:
            raise ValueError("Link Doctor receipt must be deterministic")
        has_unknown = False
        for item in receipt.findings:
            if item.rule_id in UNKNOWN_RULES and item.status != "UNKNOWN":
                raise ValueError("ambiguous outcome finding must remain UNKNOWN")
            has_unknown = has_unknown or item.status == "UNKNOWN"
        if has_unknown and receipt.overall_status == "PASS":
            raise ValueError("UNKNOWN cannot collapse to PASS")
        if receipt.findings and receipt.overall_status == "PASS":
            raise ValueError("findings cannot collapse to PASS")

    @staticmethod
    def secure_target_path(worktree: Path, relative_path: str) -> Path:
        root = worktree.resolve()
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts or ".git" in relative.parts:
            raise ValueError("path escapes immutable worktree scope")
        cursor = root
        for part in relative.parts[:-1]:
            cursor = cursor / part
            if cursor.exists() and cursor.is_symlink():
                raise ValueError("symlinked writable path forbidden")
        target = (root / relative).resolve(strict=False)
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError("resolved path escapes worktree") from exc
        return target

    @staticmethod
    def _assert_bound(job: WorkerJobSpec, lease: WorkLease) -> None:
        if lease.worker_id != "zungun":
            raise ValueError("lease not issued to Zungun")
        if lease.canonical_opportunity_id != job.canonical_opportunity_id:
            raise ValueError("lease opportunity mismatch")
        if lease.scope_hash != job.scope_hash:
            raise ValueError("lease scope mismatch")


def validate_worker_output_authority(value: Any, path: str = "result") -> None:
    """Reject attempts by an untrusted worker to mutate ATM/external authority truth."""
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_RESULT_KEYS:
                raise ValueError(f"worker authority field forbidden at {path}.{key}")
            if any(fragment in normalized for fragment in SECRET_KEY_FRAGMENTS):
                raise ValueError(f"secret-like worker output forbidden at {path}.{key}")
            validate_worker_output_authority(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            validate_worker_output_authority(child, f"{path}[{index}]")
