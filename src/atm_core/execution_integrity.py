from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .actuator import ActuatorProfile, CheckoutSubprocessActuator
from .execution_jobs import ExecutionJob, ExecutionJobStore, ExecutionStatus, TERMINAL_STATUSES, utcnow_iso
from .workers import WorkerJobSpec, WorkerManifest, WorkLease, canonical_hash, canonical_json


class RecoveryOutcome(StrEnum):
    RESUME = "RESUME"
    RECONCILE_RESULT = "RECONCILE_RESULT"
    FAIL_EXPIRE = "FAIL_EXPIRE"
    CANCEL = "CANCEL"


class IndependentCheckerReceipt(BaseModel):
    """ATM-owned checker proof. Worker payloads cannot mint or influence its verdict."""

    model_config = ConfigDict(extra="forbid")
    checker_id: str = "ATM_INDEPENDENT_CHECKER_V1"
    checker_boundary: str = "ATM_SUPERVISOR_INDEPENDENT_VERIFIER_V1"
    execution_job_id: str
    worker_id: str
    work_lease_id: str
    scope_hash: str
    job_spec_hash: str
    acceptance_contract_hash: str
    artifact_hash: str
    progress_receipt_hashes: list[str] = Field(default_factory=list)
    verdict: str
    reason: str
    checked_at: str = Field(default_factory=utcnow_iso)

    @field_validator("verdict")
    @classmethod
    def verdict_enum(cls, value: str) -> str:
        value = value.upper()
        if value not in {"PASS", "FAIL"}:
            raise ValueError("invalid independent checker verdict")
        return value

    @property
    def receipt_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))


def lease_is_executable(lease: WorkLease, now: datetime | None = None) -> bool:
    """Strict boundary: expires_at must be strictly greater than authoritative UTC now."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or lease.expires_at.tzinfo is None:
        raise ValueError("lease timestamps must be timezone-aware")
    return lease.expires_at.astimezone(timezone.utc) > now.astimezone(timezone.utc)


def assert_lease_executable(lease: WorkLease, now: datetime | None = None) -> None:
    if not lease_is_executable(lease, now):
        raise ValueError("WorkLease expired or expires exactly now")
    if lease.terminal_state is not None:
        raise ValueError("terminal WorkLease cannot execute")


class ProductionExecutionIntegrity:
    """Canonical production integrity layer around the landed CheckoutSubprocessActuator.

    Production WORK is not complete until this supervisor-owned layer re-reads the
    immutable acceptance contract and exact result artifact and persists a distinct
    checker receipt. The worker checkout never gets access to this receipt table.
    """

    FORBIDDEN_WORKER_SELF_CERT_FIELDS = {
        "checker_independent",
        "checker_verdict",
        "acceptance_passed",
        "externally_accepted",
    }

    def __init__(self, store: ExecutionJobStore, workspace_root: Path, artifact_root: Path):
        self.store = store
        self.workspace_root = Path(workspace_root)
        self.artifact_root = Path(artifact_root)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.store.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS independent_checker_receipts(
              execution_job_id TEXT PRIMARY KEY,
              checker_id TEXT NOT NULL,
              receipt_json TEXT NOT NULL,
              receipt_hash TEXT NOT NULL,
              checked_at TEXT NOT NULL
            );
            """
        )

    @staticmethod
    def _sha256_file(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def artifact_path(self, execution_job_id: str) -> Path:
        return self.artifact_root / f"{execution_job_id}.json"

    def acceptance_path(self, execution_job_id: str) -> Path:
        return self.artifact_root / f"{execution_job_id}.acceptance.json"

    @staticmethod
    def acceptance_contract_hash(spec: WorkerJobSpec) -> str:
        return canonical_hash({"criteria": list(spec.frozen_acceptance_criteria)})

    def freeze_acceptance(self, job: ExecutionJob, spec: WorkerJobSpec) -> str:
        payload = {
            "schema": "ATM_FROZEN_ACCEPTANCE_V1",
            "execution_job_id": job.execution_job_id,
            "job_spec_hash": job.job_spec_hash,
            "scope_hash": job.scope_hash,
            "criteria": list(spec.frozen_acceptance_criteria),
        }
        raw = (canonical_json(payload) + "\n").encode("utf-8")
        path = self.acceptance_path(job.execution_job_id)
        if path.exists():
            if path.read_bytes() != raw:
                raise ValueError("frozen acceptance contract mutation detected")
        else:
            path.write_bytes(raw)
        return self.acceptance_contract_hash(spec)

    def checker_receipt(self, execution_job_id: str) -> IndependentCheckerReceipt | None:
        row = self.store.conn.execute(
            "SELECT receipt_json FROM independent_checker_receipts WHERE execution_job_id=?",
            (execution_job_id,),
        ).fetchone()
        if row is None:
            return None
        return IndependentCheckerReceipt.model_validate_json(str(row["receipt_json"]))

    def _persist_checker(self, receipt: IndependentCheckerReceipt) -> IndependentCheckerReceipt:
        existing = self.store.conn.execute(
            "SELECT receipt_hash FROM independent_checker_receipts WHERE execution_job_id=?",
            (receipt.execution_job_id,),
        ).fetchone()
        if existing:
            if str(existing["receipt_hash"]) != receipt.receipt_hash:
                raise ValueError("conflicting independent checker receipt")
            return self.checker_receipt(receipt.execution_job_id) or receipt
        self.store.conn.execute(
            "INSERT INTO independent_checker_receipts VALUES(?,?,?,?,?)",
            (
                receipt.execution_job_id,
                receipt.checker_id,
                canonical_json(receipt.model_dump(mode="json")),
                receipt.receipt_hash,
                receipt.checked_at,
            ),
        )
        return receipt

    @staticmethod
    def _validate_bindings(
        job: ExecutionJob,
        spec: WorkerJobSpec,
        lease: WorkLease,
        manifest: WorkerManifest,
        profile: ActuatorProfile,
    ) -> None:
        if manifest.worker_id != job.worker_id or profile.worker_id != job.worker_id:
            raise ValueError("execution worker identity mismatch")
        if profile.source_sha != job.worker_version_or_source_sha:
            raise ValueError("execution worker source mismatch")
        if lease.lease_id != job.work_lease_id or lease.scope_hash != job.scope_hash:
            raise ValueError("execution lease/scope mismatch")
        if lease.canonical_opportunity_id != job.opportunity_id:
            raise ValueError("execution opportunity binding mismatch")
        if canonical_hash(spec.model_dump(mode="json")) != job.job_spec_hash:
            raise ValueError("execution job spec hash mismatch")

    def independently_check(
        self,
        job: ExecutionJob,
        spec: WorkerJobSpec,
        lease: WorkLease,
        manifest: WorkerManifest,
        profile: ActuatorProfile,
    ) -> IndependentCheckerReceipt:
        """Re-read ATM-owned criteria and exact artifact bytes outside worker execution context."""
        self._validate_bindings(job, spec, lease, manifest, profile)

        acceptance_path = self.acceptance_path(job.execution_job_id)
        if not acceptance_path.exists():
            raise ValueError("frozen acceptance contract missing")
        acceptance_raw = acceptance_path.read_bytes()
        acceptance = json.loads(acceptance_raw.decode("utf-8"))
        expected_acceptance_hash = self.acceptance_contract_hash(spec)
        if acceptance.get("schema") != "ATM_FROZEN_ACCEPTANCE_V1":
            raise ValueError("checker acceptance schema mismatch")
        if acceptance.get("execution_job_id") != job.execution_job_id:
            raise ValueError("checker acceptance execution identity mismatch")
        if acceptance.get("criteria") != list(spec.frozen_acceptance_criteria):
            raise ValueError("checker acceptance criteria mismatch")
        if acceptance.get("scope_hash") != job.scope_hash or acceptance.get("job_spec_hash") != job.job_spec_hash:
            raise ValueError("checker frozen acceptance binding mismatch")

        artifact_path = self.artifact_path(job.execution_job_id)
        if not artifact_path.exists() or not job.result_hash:
            raise ValueError("checker exact result artifact missing")
        artifact_hash = self._sha256_file(artifact_path)
        if artifact_hash != job.result_hash:
            raise ValueError("checker result artifact hash mismatch")
        artifact: dict[str, Any] = json.loads(artifact_path.read_text(encoding="utf-8"))
        if self.FORBIDDEN_WORKER_SELF_CERT_FIELDS.intersection({str(key).lower() for key in artifact}):
            raise ValueError("worker self-certification field forbidden")
        expected = {
            "schema": "ATM_EXECUTION_ARTIFACT_V1",
            "execution_job_id": job.execution_job_id,
            "worker_id": job.worker_id,
            "worker_source_sha": profile.source_sha,
            "work_lease_id": job.work_lease_id,
            "scope_hash": job.scope_hash,
            "job_spec_hash": job.job_spec_hash,
            "outgoing_spend_usd": 0,
        }
        for key, value in expected.items():
            if artifact.get(key) != value:
                raise ValueError(f"checker artifact binding mismatch: {key}")
        command_receipts = artifact.get("command_receipts")
        if not isinstance(command_receipts, list) or not command_receipts:
            raise ValueError("checker requires concrete worker command receipts")

        progress = self.store.progress(job.execution_job_id)
        if len(progress) < 2:
            raise ValueError("checker requires at least two durable progress receipts")
        if any(not receipt.measurable_progress for receipt in progress):
            raise ValueError("checker progress is not measurable")
        receipt = IndependentCheckerReceipt(
            execution_job_id=job.execution_job_id,
            worker_id=job.worker_id,
            work_lease_id=job.work_lease_id,
            scope_hash=job.scope_hash,
            job_spec_hash=job.job_spec_hash,
            acceptance_contract_hash=expected_acceptance_hash,
            artifact_hash=artifact_hash,
            progress_receipt_hashes=[row.receipt_hash for row in progress],
            verdict="PASS",
            reason="ATM_REVALIDATED_FROZEN_ACCEPTANCE_BINDINGS_EXACT_ARTIFACT_AND_PROGRESS",
        )
        return self._persist_checker(receipt)

    def _checker_fail_closed(self, job: ExecutionJob, reason: str) -> tuple[ExecutionJob, RecoveryOutcome]:
        failed = self.store.fail(job.execution_job_id, ExecutionStatus.QUARANTINED, reason)
        return failed, RecoveryOutcome.CANCEL

    def recover(
        self,
        job: ExecutionJob,
        spec: WorkerJobSpec,
        lease: WorkLease,
        manifest: WorkerManifest,
        profile: ActuatorProfile,
        *,
        now: datetime | None = None,
    ) -> tuple[ExecutionJob, RecoveryOutcome]:
        """Deterministic restart reconciliation. Never launches a second worker process."""
        now = now or datetime.now(timezone.utc)
        try:
            self._validate_bindings(job, spec, lease, manifest, profile)
        except Exception:
            return self._checker_fail_closed(job, "RECOVERY_BINDING_MISMATCH")

        status = job.status
        artifact_path = self.artifact_path(job.execution_job_id)

        if status == ExecutionStatus.CREATED:
            if not lease_is_executable(lease, now):
                return self.store.fail(job.execution_job_id, ExecutionStatus.EXPIRED, "LEASE_EXPIRED_BEFORE_DISPATCH"), RecoveryOutcome.FAIL_EXPIRE
            return job, RecoveryOutcome.RESUME

        if status in {ExecutionStatus.DISPATCHING, ExecutionStatus.ACKNOWLEDGED, ExecutionStatus.RUNNING}:
            if not lease_is_executable(lease, now):
                return self.store.fail(job.execution_job_id, ExecutionStatus.EXPIRED, "LEASE_EXPIRED_DURING_RECOVERY"), RecoveryOutcome.FAIL_EXPIRE
            return self.store.fail(job.execution_job_id, ExecutionStatus.CANCELLED, "RESTART_NO_SAFE_PROCESS_REATTACH"), RecoveryOutcome.CANCEL

        if status in {ExecutionStatus.RESULT_READY, ExecutionStatus.CHECKING}:
            if not job.result_hash or not artifact_path.exists() or self._sha256_file(artifact_path) != job.result_hash:
                return self.store.fail(job.execution_job_id, ExecutionStatus.CANCELLED, "RECOVERY_RESULT_ARTIFACT_MISSING_OR_MISMATCH"), RecoveryOutcome.CANCEL
            try:
                receipt = self.independently_check(job, spec, lease, manifest, profile)
            except Exception:
                return self._checker_fail_closed(job, "RECOVERY_INDEPENDENT_CHECK_FAILED")
            reconciled = self.store.terminal(
                job.execution_job_id,
                checker_hash=receipt.receipt_hash,
                reason="INDEPENDENT_CHECKER_PASS_RECONCILED",
            )
            return reconciled, RecoveryOutcome.RECONCILE_RESULT

        if status == ExecutionStatus.TERMINAL:
            receipt = self.checker_receipt(job.execution_job_id)
            if receipt and receipt.verdict == "PASS" and receipt.receipt_hash == job.checker_hash:
                return job, RecoveryOutcome.RECONCILE_RESULT
            if job.result_hash and artifact_path.exists() and self._sha256_file(artifact_path) == job.result_hash:
                try:
                    receipt = self.independently_check(job, spec, lease, manifest, profile)
                except Exception:
                    return self._checker_fail_closed(job, "TERMINAL_INDEPENDENT_CHECK_FAILED")
                reconciled = self.store.terminal(
                    job.execution_job_id,
                    checker_hash=receipt.receipt_hash,
                    reason="INDEPENDENT_CHECKER_PASS_RECONCILED",
                )
                return reconciled, RecoveryOutcome.RECONCILE_RESULT
            return self._checker_fail_closed(job, "TERMINAL_WITHOUT_INDEPENDENT_CHECKER_PROOF")

        if status in TERMINAL_STATUSES:
            return job, RecoveryOutcome.CANCEL
        return self.store.fail(job.execution_job_id, ExecutionStatus.CANCELLED, "UNSUPPORTED_RECOVERY_STATE"), RecoveryOutcome.CANCEL

    def execute(
        self,
        spec: WorkerJobSpec,
        lease: WorkLease,
        manifest: WorkerManifest,
        profile: ActuatorProfile,
    ) -> tuple[ExecutionJob, RecoveryOutcome]:
        """Create identity before dispatch, recover re-entry, then require independent checker proof."""
        job = self.store.create_or_get(
            opportunity_id=spec.canonical_opportunity_id,
            worker_id=manifest.worker_id,
            worker_version_or_source_sha=profile.source_sha,
            work_lease_id=lease.lease_id,
            scope_hash=spec.scope_hash,
            job_spec_hash=canonical_hash(spec.model_dump(mode="json")),
        )
        self.freeze_acceptance(job, spec)
        if job.status != ExecutionStatus.CREATED:
            return self.recover(job, spec, lease, manifest, profile)

        # P0-B boundary: reject expired/equal lease before CheckoutSubprocessActuator can spawn anything.
        if not lease_is_executable(lease):
            self.store.fail(job.execution_job_id, ExecutionStatus.EXPIRED, "LEASE_EXPIRED_BEFORE_DISPATCH")
            raise ValueError("WorkLease expired or expires exactly now")
        if lease.terminal_state is not None:
            self.store.fail(job.execution_job_id, ExecutionStatus.CANCELLED, "TERMINAL_WORK_LEASE")
            raise ValueError("terminal WorkLease cannot execute")

        actuator = CheckoutSubprocessActuator(self.store, self.workspace_root, self.artifact_root)
        job = actuator.dispatch(spec, lease, manifest, profile)
        if job.status in {ExecutionStatus.RESULT_READY, ExecutionStatus.CHECKING, ExecutionStatus.TERMINAL}:
            try:
                receipt = self.independently_check(job, spec, lease, manifest, profile)
            except Exception:
                return self._checker_fail_closed(job, "INDEPENDENT_CHECK_FAILED_AFTER_DISPATCH")
            job = self.store.terminal(
                job.execution_job_id,
                checker_hash=receipt.receipt_hash,
                reason="INDEPENDENT_CHECKER_PASS",
            )
            return job, RecoveryOutcome.RECONCILE_RESULT
        return job, RecoveryOutcome.CANCEL
