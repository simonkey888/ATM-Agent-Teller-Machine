from __future__ import annotations

import json
import tempfile
from pathlib import Path

from atm_core.actuator import CheckoutSubprocessActuator, load_actuator_profile
from atm_core.execution_jobs import ExecutionJobStore, ExecutionStatus
from atm_core.workers import WorkerJobSpec, WorkerRegistry, WorkLeaseStore

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ROOT / "workers" / "manifests"
PROFILES = ROOT / "workers" / "actuators"


def run_worker_fixture(worker_id: str, root: Path) -> dict[str, object]:
    registry = WorkerRegistry.from_directory(MANIFESTS)
    manifest = registry.get(worker_id)
    profile = load_actuator_profile(PROFILES, worker_id)
    spec = WorkerJobSpec(
        job_id=f"fixture-worker-actuator-001-{worker_id}",
        canonical_opportunity_id=f"fixture:worker-actuator-001:{worker_id}",
        external_source="fixture",
        external_url=f"https://example.invalid/fixture-worker-actuator-001/{worker_id}",
        task_type="non_economic_actuator_qualification",
        frozen_acceptance_criteria=[
            "exactly one worker launch",
            "ACK bound to execution job/lease/scope",
            "at least two durable progress receipts",
            "automated exact artifact",
            "independent checker",
            "restart recovery does not duplicate launch",
        ],
        repository_or_input=manifest.repo_url,
        max_spend_usd=0,
        required_capabilities=["git", "filesystem", "shell", "node", "evidence"],
        target_base_sha=profile.source_sha,
        allowed_paths=["test", "tests", "scripts", "tools"],
        expected_deliverable="automated actuator qualification evidence",
        deterministic_checks=["launch_count=1", "checkpoint_seq>=2", "terminal checker hash present"],
    )
    lease_store = WorkLeaseStore(root / f"leases-{worker_id}.sqlite3")
    lease = lease_store.acquire(spec, manifest, ttl_seconds=min(1800, manifest.timeout_seconds))
    if lease is None:
        raise RuntimeError(f"fixture lease unavailable: {worker_id}")
    store = ExecutionJobStore(root / f"execution-{worker_id}.sqlite3")
    actuator = CheckoutSubprocessActuator(store, root / "workspaces", root / "artifacts")
    first = actuator.dispatch(spec, lease, manifest, profile)
    if first.status != ExecutionStatus.TERMINAL:
        raise RuntimeError(f"{worker_id} actuator failed: {first.status}:{first.terminal_reason}")
    receipts = store.progress(first.execution_job_id)
    if len(receipts) < 2:
        raise RuntimeError(f"{worker_id} emitted fewer than two progress receipts")
    if not all(receipt.measurable_progress for receipt in receipts):
        raise RuntimeError(f"{worker_id} emitted non-progress receipt in fixture")
    if not first.result_hash or not first.checker_hash:
        raise RuntimeError(f"{worker_id} missing result/checker hash")
    artifact = root / "artifacts" / f"{first.execution_job_id}.json"
    if not artifact.exists():
        raise RuntimeError(f"{worker_id} automated artifact missing")
    # Simulated supervisor restart: close/reopen durable store then invoke same identity.
    store.close()
    store = ExecutionJobStore(root / f"execution-{worker_id}.sqlite3")
    recovered = CheckoutSubprocessActuator(store, root / "workspaces", root / "artifacts").dispatch(spec, lease, manifest, profile)
    if recovered.execution_job_id != first.execution_job_id or recovered.launch_count != 1:
        raise RuntimeError(f"{worker_id} restart duplicated execution")
    if recovered.status != ExecutionStatus.TERMINAL:
        raise RuntimeError(f"{worker_id} terminal state not recovered")
    evidence = {
        "worker_id": worker_id,
        "source_sha": profile.source_sha,
        "execution_job_id": recovered.execution_job_id,
        "work_lease_id": lease.lease_id,
        "scope_hash": spec.scope_hash,
        "status": recovered.status.value,
        "launch_count": recovered.launch_count,
        "progress_receipts": len(store.progress(recovered.execution_job_id)),
        "result_hash": recovered.result_hash,
        "checker_hash": recovered.checker_hash,
        "manual_result_creation_required": False,
        "outgoing_spend_usd": 0,
    }
    store.close()
    lease_store.release(lease, "FIXTURE_TERMINAL")
    lease_store.close()
    return evidence


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="atm-vnext-actuator-") as directory:
        root = Path(directory)
        results = [run_worker_fixture(worker_id, root) for worker_id in ("boqa", "zungun")]
        print(json.dumps({"schema": "ATM_VNEXT_ACTUATOR_FIXTURE_V1", "results": results}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
