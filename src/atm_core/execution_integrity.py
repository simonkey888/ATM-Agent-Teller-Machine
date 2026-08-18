from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .actuator import ActuatorProfile
from .execution_jobs import ExecutionJob, ExecutionJobStore, ExecutionStatus, TERMINAL_STATUSES, utcnow_iso
from .lease_bound_actuator import LeaseBoundCheckoutSubprocessActuator
from .worker_integration_checker import check_registered_worker_material
from .workers import WorkerJobSpec, WorkerManifest, WorkLease, canonical_hash, canonical_json


class RecoveryOutcome(StrEnum):
    RESUME = "RESUME"
    RECONCILE_RESULT = "RECONCILE_RESULT"
    FAIL_EXPIRE = "FAIL_EXPIRE"
    CANCEL = "CANCEL"


class IndependentCheckerReceipt(BaseModel):
    """ATM-owned material checker proof. Worker payloads cannot mint its verdict."""

    model_config = ConfigDict(extra="forbid")
    checker_id: str = "ATM_INDEPENDENT_CHECKER_V1"
    checker_boundary: str = "ATM_SUPERVISOR_INDEPENDENT_VERIFIER_V2"
    checker_workspace_id: str
    execution_job_id: str
    worker_id: str
    work_lease_id: str
    scope_hash: str
    job_spec_hash: str
    acceptance_contract_hash: str
    artifact_hash: str
    task_result_hash: str
    progress_receipt_hashes: list[str] = Field(default_factory=list)
    predicate_results: list[dict[str, Any]] = Field(default_factory=list)
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
    """Canonical supervisor integrity around a lease-bounded pinned worker actuator.

    Worker execution and ATM acceptance checking are deliberately distinct. The
    worker subprocess receives only a frozen job path/result path, exact WorkLease
    expiry, and zero-secret environment. The checker runs afterwards in a
    supervisor-owned workspace and cannot create a PASS after lease expiry.
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

    def checker_workspace(self, execution_job_id: str) -> Path:
        path = self.artifact_root / "checker-workspaces" / execution_job_id
        worker_path = (self.workspace_root / execution_job_id).resolve()
        checker_path = path.resolve()
        if checker_path == worker_path or checker_path in worker_path.parents or worker_path in checker_path.parents:
            raise ValueError("checker workspace overlaps worker execution boundary")
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def acceptance_contract_hash(spec: WorkerJobSpec) -> str:
        return canonical_hash(
            {
                "criteria": list(spec.frozen_acceptance_criteria),
                "deterministic_checks": list(spec.deterministic_checks),
            }
        )

    def freeze_acceptance(self, job: ExecutionJob, spec: WorkerJobSpec) -> str:
        payload = {
            "schema": "ATM_FROZEN_ACCEPTANCE_V2",
            "execution_job_id": job.execution_job_id,
            "job_spec_hash": job.job_spec_hash,
            "scope_hash": job.scope_hash,
            "criteria": list(spec.frozen_acceptance_criteria),
            "deterministic_checks": list(spec.deterministic_checks),
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

    @staticmethod
    def _evaluate_acceptance(
        spec: WorkerJobSpec,
        acceptance: dict[str, Any],
        task_result: dict[str, Any],
        lease: WorkLease,
        worker_id: str,
        source_sha: str,
    ) -> tuple[str, str, list[dict[str, Any]]]:
        results: list[dict[str, Any]] = []
        output = task_result.get("task_output")
        if not isinstance(output, dict):
            return "FAIL", "TASK_OUTPUT_MISSING", results

        if spec.task_type in {"zungun_reliability_audit_v1", "across_readonly_unsigned_tx_v1"}:
            try:
                specialist = check_registered_worker_material(worker_id, task_result, spec, lease, source_sha)
            except ValueError:
                return "FAIL", "UNSUPPORTED_MATERIAL_ACCEPTANCE_TASK", results
            results.extend(dict(row) for row in specialist.predicates)
            if specialist.verdict != "PASS":
                return "FAIL", "REGISTERED_WORKER_MATERIAL_CHECKER_FAILED", results
            frozen = acceptance.get("deterministic_checks")
            if not isinstance(frozen, list) or not frozen:
                return "FAIL", "NO_ATM_MATERIAL_ACCEPTANCE_PREDICATE", results
            if list(frozen) != list(spec.deterministic_checks):
                return "FAIL", "FROZEN_ACCEPTANCE_PREDICATE_FAILED", results
            results.append({
                "predicate": "ATM_TYPED_WORKER_CHECKER_REGISTRY",
                "expected": f"{worker_id}:{spec.task_type}",
                "actual": f"{worker_id}:{spec.task_type}",
                "passed": True,
            })
            return "PASS", "ATM_REGISTERED_WORKER_MATERIAL_CHECKER_PASS", results

        if spec.task_type != "deterministic_digest_v1":
            return "FAIL", "UNSUPPORTED_MATERIAL_ACCEPTANCE_TASK", results
        frozen_input = spec.repository_or_input.encode("utf-8")
        recomputed_sha = hashlib.sha256(frozen_input).hexdigest()
        actual_sha = str(output.get("sha256") or "")
        actual_length = output.get("byte_length")
        recomputed_ok = actual_sha == recomputed_sha and actual_length == len(frozen_input)
        results.append(
            {
                "predicate": "ATM_RECOMPUTE_SHA256_UTF8_INPUT_V1",
                "expected": recomputed_sha,
                "actual": actual_sha,
                "passed": recomputed_ok,
            }
        )
        if not recomputed_ok:
            return "FAIL", "TASK_RESULT_MATERIALLY_WRONG", results

        predicates = acceptance.get("deterministic_checks")
        if not isinstance(predicates, list) or not predicates:
            return "FAIL", "NO_ATM_MATERIAL_ACCEPTANCE_PREDICATE", results
        for raw in predicates:
            predicate = str(raw)
            sha_match = re.fullmatch(r"TASK_RESULT_SHA256_EQ:([0-9a-f]{64})", predicate)
            length_match = re.fullmatch(r"TASK_RESULT_BYTE_LENGTH_EQ:([0-9]+)", predicate)
            if sha_match:
                expected = sha_match.group(1)
                passed = actual_sha == expected
                results.append(
                    {
                        "predicate": predicate,
                        "expected": expected,
                        "actual": actual_sha,
                        "passed": passed,
                    }
                )
            elif length_match:
                expected_int = int(length_match.group(1))
                passed = actual_length == expected_int
                results.append(
                    {
                        "predicate": predicate,
                        "expected": expected_int,
                        "actual": actual_length,
                        "passed": passed,
                    }
                )
            else:
                results.append(
                    {
                        "predicate": predicate,
                        "expected": "SUPPORTED_ATM_PREDICATE",
                        "actual": "UNSUPPORTED",
                        "passed": False,
                    }
                )
                passed = False
            if not passed:
                return "FAIL", "FROZEN_ACCEPTANCE_PREDICATE_FAILED", results
        return "PASS", "ATM_MATERIAL_ACCEPTANCE_PREDICATES_SATISFIED", results

    def _rehydrate_task_result(
        self,
        artifact: dict[str, Any],
        job: ExecutionJob,
        spec: WorkerJobSpec,
        lease: WorkLease,
    ) -> tuple[dict[str, Any], str, str]:
        task_result = artifact.get("task_result")
        task_result_hash = str(artifact.get("task_result_hash") or "")
        if not isinstance(task_result, dict):
            raise ValueError("checker requires task-specific result")
        if canonical_hash(task_result) != task_result_hash:
            raise ValueError("checker task result hash mismatch")
        expected = {
            "schema": "ATM_TASK_RESULT_V1",
            "execution_job_id": job.execution_job_id,
            "work_lease_id": lease.lease_id,
            "scope_hash": spec.scope_hash,
            "job_spec_hash": job.job_spec_hash,
            "task_type": spec.task_type,
            "outgoing_spend_usd": 0,
        }
        for key, value in expected.items():
            if task_result.get(key) != value:
                raise ValueError(f"checker task result binding mismatch: {key}")

        checker_dir = self.checker_workspace(job.execution_job_id)
        checker_input = checker_dir / "task-result.json"
        raw = (canonical_json(task_result) + "\n").encode("utf-8")
        if checker_input.exists():
            if checker_input.read_bytes() != raw:
                raise ValueError("checker workspace task result conflict")
        else:
            checker_input.write_bytes(raw)
        rehydrated = json.loads(checker_input.read_text(encoding="utf-8"))
        if canonical_hash(rehydrated) != task_result_hash:
            raise ValueError("checker rehydrated result hash mismatch")
        return rehydrated, task_result_hash, f"checker:{job.execution_job_id}"

    def independently_check(
        self,
        job: ExecutionJob,
        spec: WorkerJobSpec,
        lease: WorkLease,
        manifest: WorkerManifest,
        profile: ActuatorProfile,
    ) -> IndependentCheckerReceipt:
        """Recompute material acceptance outside the worker execution boundary."""
        self._validate_bindings(job, spec, lease, manifest, profile)
        assert_lease_executable(lease)

        acceptance_path = self.acceptance_path(job.execution_job_id)
        if not acceptance_path.exists():
            raise ValueError("frozen acceptance contract missing")
        acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
        expected_acceptance_hash = self.acceptance_contract_hash(spec)
        if acceptance.get("schema") != "ATM_FROZEN_ACCEPTANCE_V2":
            raise ValueError("checker acceptance schema mismatch")
        if acceptance.get("execution_job_id") != job.execution_job_id:
            raise ValueError("checker acceptance execution identity mismatch")
        if acceptance.get("criteria") != list(spec.frozen_acceptance_criteria):
            raise ValueError("checker acceptance criteria mismatch")
        if acceptance.get("deterministic_checks") != list(spec.deterministic_checks):
            raise ValueError("checker deterministic acceptance mismatch")
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
            "work_lease_expires_at": lease.expires_at.isoformat(),
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

        task_result, task_result_hash, checker_workspace_id = self._rehydrate_task_result(
            artifact, job, spec, lease
        )
        verdict, reason, predicate_results = self._evaluate_acceptance(
            spec,
            acceptance,
            task_result,
            lease,
            manifest.worker_id,
            profile.source_sha,
        )

        existing = self.checker_receipt(job.execution_job_id)
        if existing is not None:
            if existing.artifact_hash != artifact_hash or existing.task_result_hash != task_result_hash:
                raise ValueError("existing checker receipt does not bind current result")
            return existing

        assert_lease_executable(lease)
        receipt = IndependentCheckerReceipt(
            checker_workspace_id=checker_workspace_id,
            execution_job_id=job.execution_job_id,
            worker_id=job.worker_id,
            work_lease_id=job.work_lease_id,
            scope_hash=job.scope_hash,
            job_spec_hash=job.job_spec_hash,
            acceptance_contract_hash=expected_acceptance_hash,
            artifact_hash=artifact_hash,
            task_result_hash=task_result_hash,
            progress_receipt_hashes=[row.receipt_hash for row in progress],
            predicate_results=predicate_results,
            verdict=verdict,
            reason=reason,
        )
        return self._persist_checker(receipt)

    def _checker_fail_closed(self, job: ExecutionJob, reason: str) -> tuple[ExecutionJob, RecoveryOutcome]:
        failed = self.store.fail(job.execution_job_id, ExecutionStatus.QUARANTINED, reason)
        return failed, RecoveryOutcome.CANCEL

    def _expire_fail_closed(self, job: ExecutionJob, reason: str) -> tuple[ExecutionJob, RecoveryOutcome]:
        failed = self.store.fail(job.execution_job_id, ExecutionStatus.EXPIRED, reason)
        return failed, RecoveryOutcome.FAIL_EXPIRE

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
                return self._expire_fail_closed(job, "LEASE_EXPIRED_BEFORE_DISPATCH")
            return job, RecoveryOutcome.RESUME

        if status in {ExecutionStatus.DISPATCHING, ExecutionStatus.ACKNOWLEDGED, ExecutionStatus.RUNNING}:
            if not lease_is_executable(lease, now):
                return self._expire_fail_closed(job, "LEASE_EXPIRED_DURING_RECOVERY")
            return self.store.fail(job.execution_job_id, ExecutionStatus.CANCELLED, "RESTART_NO_SAFE_PROCESS_REATTACH"), RecoveryOutcome.CANCEL

        if status in {ExecutionStatus.RESULT_READY, ExecutionStatus.CHECKING}:
            if not lease_is_executable(lease, now):
                return self._expire_fail_closed(job, "LEASE_EXPIRED_BEFORE_RECOVERY_CHECK")
            if not job.result_hash or not artifact_path.exists() or self._sha256_file(artifact_path) != job.result_hash:
                return self.store.fail(job.execution_job_id, ExecutionStatus.CANCELLED, "RECOVERY_RESULT_ARTIFACT_MISSING_OR_MISMATCH"), RecoveryOutcome.CANCEL
            try:
                receipt = self.independently_check(job, spec, lease, manifest, profile)
            except Exception:
                if not lease_is_executable(lease):
                    return self._expire_fail_closed(job, "LEASE_EXPIRED_DURING_RECOVERY_CHECK")
                return self._checker_fail_closed(job, "RECOVERY_INDEPENDENT_CHECK_FAILED")
            if receipt.verdict != "PASS":
                return self._checker_fail_closed(job, "RECOVERY_MATERIAL_ACCEPTANCE_FAILED")
            if not lease_is_executable(lease):
                return self._expire_fail_closed(job, "LEASE_EXPIRED_BEFORE_RECOVERY_TERMINAL")
            reconciled = self.store.terminal(
                job.execution_job_id,
                checker_hash=receipt.receipt_hash,
                reason="INDEPENDENT_MATERIAL_CHECKER_PASS_RECONCILED",
            )
            return reconciled, RecoveryOutcome.RECONCILE_RESULT

        if status == ExecutionStatus.TERMINAL:
            receipt = self.checker_receipt(job.execution_job_id)
            if receipt and receipt.verdict == "PASS" and receipt.receipt_hash == job.checker_hash:
                return job, RecoveryOutcome.RECONCILE_RESULT
            if receipt and receipt.verdict == "FAIL":
                return self._checker_fail_closed(job, "TERMINAL_MATERIAL_CHECKER_FAIL")
            if job.result_hash and artifact_path.exists() and self._sha256_file(artifact_path) == job.result_hash:
                if not lease_is_executable(lease):
                    return self._expire_fail_closed(job, "LEASE_EXPIRED_BEFORE_TERMINAL_RECONCILIATION")
                try:
                    receipt = self.independently_check(job, spec, lease, manifest, profile)
                except Exception:
                    if not lease_is_executable(lease):
                        return self._expire_fail_closed(job, "LEASE_EXPIRED_DURING_TERMINAL_CHECK")
                    return self._checker_fail_closed(job, "TERMINAL_INDEPENDENT_CHECK_FAILED")
                if receipt.verdict != "PASS":
                    return self._checker_fail_closed(job, "TERMINAL_MATERIAL_ACCEPTANCE_FAILED")
                if not lease_is_executable(lease):
                    return self._expire_fail_closed(job, "LEASE_EXPIRED_BEFORE_TERMINAL_COMMIT")
                reconciled = self.store.terminal(
                    job.execution_job_id,
                    checker_hash=receipt.receipt_hash,
                    reason="INDEPENDENT_MATERIAL_CHECKER_PASS_RECONCILED",
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
        """Create identity before dispatch, recover re-entry, then require material checker proof."""
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

        if not lease_is_executable(lease):
            self.store.fail(job.execution_job_id, ExecutionStatus.EXPIRED, "LEASE_EXPIRED_BEFORE_DISPATCH")
            raise ValueError("WorkLease expired or expires exactly now")
        if lease.terminal_state is not None:
            self.store.fail(job.execution_job_id, ExecutionStatus.CANCELLED, "TERMINAL_WORK_LEASE")
            raise ValueError("terminal WorkLease cannot execute")

        actuator = LeaseBoundCheckoutSubprocessActuator(self.store, self.workspace_root, self.artifact_root)
        job = actuator.dispatch(spec, lease, manifest, profile)
        if job.status in {ExecutionStatus.RESULT_READY, ExecutionStatus.CHECKING, ExecutionStatus.TERMINAL}:
            if not lease_is_executable(lease):
                return self._expire_fail_closed(job, "LEASE_EXPIRED_BEFORE_CHECK")
            try:
                receipt = self.independently_check(job, spec, lease, manifest, profile)
            except Exception:
                if not lease_is_executable(lease):
                    return self._expire_fail_closed(job, "LEASE_EXPIRED_DURING_CHECK")
                return self._checker_fail_closed(job, "INDEPENDENT_CHECK_FAILED_AFTER_DISPATCH")
            if receipt.verdict != "PASS":
                return self._checker_fail_closed(job, "MATERIAL_ACCEPTANCE_FAILED_AFTER_DISPATCH")
            if not lease_is_executable(lease):
                return self._expire_fail_closed(job, "LEASE_EXPIRED_BEFORE_TERMINAL_PASS")
            job = self.store.terminal(
                job.execution_job_id,
                checker_hash=receipt.receipt_hash,
                reason="INDEPENDENT_MATERIAL_CHECKER_PASS",
            )
            return job, RecoveryOutcome.RECONCILE_RESULT
        return job, RecoveryOutcome.CANCEL
