from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from atm_core.actuator import load_actuator_profile
from atm_core.execution_integrity import ProductionExecutionIntegrity, RecoveryOutcome
from atm_core.execution_jobs import ExecutionJobStore, ExecutionStatus
from atm_core.workers import WorkerJobSpec, WorkerRegistry, WorkLeaseStore

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ROOT / "workers" / "manifests"
PROFILES = ROOT / "workers" / "actuators"


def run_worker_fixture(worker_id: str, root: Path) -> dict[str, object]:
    registry = WorkerRegistry.from_directory(MANIFESTS)
    manifest = registry.get(worker_id)
    profile = load_actuator_profile(PROFILES, worker_id)
    frozen_input = f"ORDER-005-R2::{worker_id}::frozen-job-contract"
    expected_sha = hashlib.sha256(frozen_input.encode("utf-8")).hexdigest()
    spec = WorkerJobSpec(
        job_id=f"fixture-worker-actuator-r2-{worker_id}",
        canonical_opportunity_id=f"fixture:worker-actuator-r2:{worker_id}",
        external_source="fixture",
        external_url=f"https://example.invalid/fixture-worker-actuator-r2/{worker_id}",
        task_type="deterministic_digest_v1",
        frozen_acceptance_criteria=[
            "selected worker process consumes the exact supervisor-materialized frozen job",
            "task-specific result is bound to execution/lease/scope/job-spec hash",
            "ATM checker recomputes result outside worker checkout",
            "restart recovery does not duplicate launch",
        ],
        repository_or_input=frozen_input,
        max_spend_usd=0,
        required_capabilities=["git", "filesystem", "shell", "node", "evidence"],
        target_base_sha=profile.source_sha,
        allowed_paths=["test", "tests", "scripts", "tools"],
        expected_deliverable="content-addressed deterministic digest result",
        deterministic_checks=[
            f"TASK_RESULT_SHA256_EQ:{expected_sha}",
            f"TASK_RESULT_BYTE_LENGTH_EQ:{len(frozen_input.encode('utf-8'))}",
        ],
    )
    lease_store = WorkLeaseStore(root / f"leases-{worker_id}.sqlite3")
    lease = lease_store.acquire(spec, manifest, ttl_seconds=min(1800, manifest.timeout_seconds))
    if lease is None:
        raise RuntimeError(f"fixture lease unavailable: {worker_id}")

    db = root / f"execution-{worker_id}.sqlite3"
    store = ExecutionJobStore(db)
    integrity = ProductionExecutionIntegrity(store, root / "workspaces", root / "artifacts")
    first, outcome = integrity.execute(spec, lease, manifest, profile)
    checker = integrity.checker_receipt(first.execution_job_id)
    if first.status != ExecutionStatus.TERMINAL or outcome != RecoveryOutcome.RECONCILE_RESULT:
        raise RuntimeError(f"{worker_id} material actuator failed: {first.status}:{first.terminal_reason}")
    if checker is None or checker.verdict != "PASS":
        raise RuntimeError(f"{worker_id} material independent checker missing/pass=false")
    receipts = store.progress(first.execution_job_id)
    if len(receipts) < 2 or not all(receipt.measurable_progress for receipt in receipts):
        raise RuntimeError(f"{worker_id} durable measurable progress contract failed")
    if not first.result_hash or not first.checker_hash or first.checker_hash != checker.receipt_hash:
        raise RuntimeError(f"{worker_id} result/checker binding missing")
    artifact_path = root / "artifacts" / f"{first.execution_job_id}.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    task_result = artifact.get("task_result") or {}
    if task_result.get("task_output", {}).get("sha256") != expected_sha:
        raise RuntimeError(f"{worker_id} frozen task output mismatch")
    if artifact.get("task_result_hash") != checker.task_result_hash:
        raise RuntimeError(f"{worker_id} checker did not bind exact task result")

    # Simulated supervisor restart: same immutable identity, same checker, zero relaunch.
    store.close()
    store = ExecutionJobStore(db)
    integrity = ProductionExecutionIntegrity(store, root / "workspaces", root / "artifacts")
    recovered, recovery = integrity.execute(spec, lease, manifest, profile)
    recovered_checker = integrity.checker_receipt(recovered.execution_job_id)
    if recovered.execution_job_id != first.execution_job_id or recovered.launch_count != 1:
        raise RuntimeError(f"{worker_id} restart duplicated execution")
    if recovered.status != ExecutionStatus.TERMINAL or recovery != RecoveryOutcome.RECONCILE_RESULT:
        raise RuntimeError(f"{worker_id} terminal material result not recovered")
    if recovered_checker is None or recovered_checker.receipt_hash != checker.receipt_hash:
        raise RuntimeError(f"{worker_id} checker receipt not durable")

    evidence = {
        "worker_id": worker_id,
        "source_sha": profile.source_sha,
        "execution_job_id": recovered.execution_job_id,
        "work_lease_id": lease.lease_id,
        "scope_hash": spec.scope_hash,
        "job_spec_hash": recovered.job_spec_hash,
        "status": recovered.status.value,
        "launch_count": recovered.launch_count,
        "progress_receipts": len(store.progress(recovered.execution_job_id)),
        "result_hash": recovered.result_hash,
        "task_result_hash": recovered_checker.task_result_hash,
        "checker_hash": recovered.checker_hash,
        "checker_boundary": recovered_checker.checker_boundary,
        "material_predicates": recovered_checker.predicate_results,
        "manual_result_creation_required": False,
        "frozen_job_consumed": True,
        "outgoing_spend_usd": 0,
    }
    store.close()
    lease_store.release(lease, "FIXTURE_TERMINAL")
    lease_store.close()
    return evidence


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="atm-vnext-actuator-r2-") as directory:
        root = Path(directory)
        results = [run_worker_fixture(worker_id, root) for worker_id in ("boqa", "zungun")]
        print(json.dumps({"schema": "ATM_VNEXT_ACTUATOR_FIXTURE_R2", "results": results}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
