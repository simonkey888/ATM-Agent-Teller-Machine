from __future__ import annotations

import hashlib
import inspect
import json
import subprocess
import tempfile
from pathlib import Path

import atm_core.actuator as actuator_module
from atm_core.actuator import load_actuator_profile
from atm_core.execution_integrity import ProductionExecutionIntegrity, RecoveryOutcome
from atm_core.execution_jobs import ExecutionJobStore, ExecutionStatus
from atm_core.workers import WorkerJobSpec, WorkerRegistry, WorkLeaseStore

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ROOT / "workers" / "manifests"
PROFILES = ROOT / "workers" / "actuators"


def build_spec(worker_id: str, profile, frozen_input: str, case: str) -> WorkerJobSpec:
    expected_sha = hashlib.sha256(frozen_input.encode("utf-8")).hexdigest()
    return WorkerJobSpec(
        job_id=f"fixture-worker-actuator-f007-{worker_id}-{case}",
        canonical_opportunity_id=f"fixture:worker-actuator-f007:{worker_id}:{case}",
        external_source="fixture",
        external_url=f"https://example.invalid/fixture-worker-actuator-f007/{worker_id}/{case}",
        task_type="deterministic_digest_v1",
        frozen_acceptance_criteria=[
            "selected pinned worker-owned entrypoint consumes the exact supervisor-materialized frozen job",
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


def run_worker_fixture(worker_id: str, root: Path, case: str) -> dict[str, object]:
    registry = WorkerRegistry.from_directory(MANIFESTS)
    manifest = registry.get(worker_id)
    profile = load_actuator_profile(PROFILES, worker_id)
    frozen_input = f"ORDER-005-F007::{worker_id}::{case}::frozen-job-contract"
    expected_sha = hashlib.sha256(frozen_input.encode("utf-8")).hexdigest()
    spec = build_spec(worker_id, profile, frozen_input, case)
    lease_store = WorkLeaseStore(root / f"leases-{worker_id}-{case}.sqlite3")
    lease = lease_store.acquire(spec, manifest, ttl_seconds=min(1800, manifest.timeout_seconds))
    if lease is None:
        raise RuntimeError(f"fixture lease unavailable: {worker_id}:{case}")

    db = root / f"execution-{worker_id}-{case}.sqlite3"
    store = ExecutionJobStore(db)
    integrity = ProductionExecutionIntegrity(store, root / "workspaces", root / "artifacts")
    first, outcome = integrity.execute(spec, lease, manifest, profile)
    checker = integrity.checker_receipt(first.execution_job_id)
    if first.status != ExecutionStatus.TERMINAL or outcome != RecoveryOutcome.RECONCILE_RESULT:
        raise RuntimeError(f"{worker_id}:{case} material actuator failed: {first.status}:{first.terminal_reason}")
    if checker is None or checker.verdict != "PASS":
        raise RuntimeError(f"{worker_id}:{case} material independent checker missing/pass=false")
    receipts = store.progress(first.execution_job_id)
    if len(receipts) < 2 or not all(receipt.measurable_progress for receipt in receipts):
        raise RuntimeError(f"{worker_id}:{case} durable measurable progress contract failed")
    if not first.result_hash or not first.checker_hash or first.checker_hash != checker.receipt_hash:
        raise RuntimeError(f"{worker_id}:{case} result/checker binding missing")
    artifact_path = root / "artifacts" / f"{first.execution_job_id}.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    task_result = artifact.get("task_result") or {}
    provenance = artifact.get("task_entrypoint_provenance") or {}
    producer = task_result.get("producer") or {}
    if task_result.get("task_output", {}).get("sha256") != expected_sha:
        raise RuntimeError(f"{worker_id}:{case} frozen task output mismatch")
    if artifact.get("task_result_hash") != checker.task_result_hash:
        raise RuntimeError(f"{worker_id}:{case} checker did not bind exact task result")
    if provenance.get("path") != profile.task_entrypoint:
        raise RuntimeError(f"{worker_id}:{case} task entrypoint path not bound")
    if provenance.get("worker_source_sha") != profile.source_sha:
        raise RuntimeError(f"{worker_id}:{case} task entrypoint source pin not bound")
    if not str(provenance.get("git_blob_oid") or "") or not str(provenance.get("sha256") or ""):
        raise RuntimeError(f"{worker_id}:{case} task entrypoint byte provenance missing")
    if producer.get("worker_source_sha") != profile.source_sha or producer.get("worker_entrypoint") != profile.task_entrypoint:
        raise RuntimeError(f"{worker_id}:{case} worker result producer binding mismatch")

    # Simulated supervisor restart: same immutable identity, same checker, zero relaunch.
    store.close()
    store = ExecutionJobStore(db)
    integrity = ProductionExecutionIntegrity(store, root / "workspaces", root / "artifacts")
    recovered, recovery = integrity.execute(spec, lease, manifest, profile)
    recovered_checker = integrity.checker_receipt(recovered.execution_job_id)
    if recovered.execution_job_id != first.execution_job_id or recovered.launch_count != 1:
        raise RuntimeError(f"{worker_id}:{case} restart duplicated execution")
    if recovered.status != ExecutionStatus.TERMINAL or recovery != RecoveryOutcome.RECONCILE_RESULT:
        raise RuntimeError(f"{worker_id}:{case} terminal material result not recovered")
    if recovered_checker is None or recovered_checker.receipt_hash != checker.receipt_hash:
        raise RuntimeError(f"{worker_id}:{case} checker receipt not durable")

    evidence = {
        "worker_id": worker_id,
        "case": case,
        "source_sha": profile.source_sha,
        "task_entrypoint": profile.task_entrypoint,
        "task_entrypoint_git_blob_oid": provenance.get("git_blob_oid"),
        "task_entrypoint_sha256": provenance.get("sha256"),
        "execution_job_id": recovered.execution_job_id,
        "work_lease_id": lease.lease_id,
        "scope_hash": spec.scope_hash,
        "job_spec_hash": recovered.job_spec_hash,
        "status": recovered.status.value,
        "launch_count": recovered.launch_count,
        "progress_receipts": len(store.progress(recovered.execution_job_id)),
        "result_hash": recovered.result_hash,
        "task_result_hash": recovered_checker.task_result_hash,
        "task_output_sha256": task_result.get("task_output", {}).get("sha256"),
        "checker_hash": recovered.checker_hash,
        "checker_boundary": recovered_checker.checker_boundary,
        "material_predicates": recovered_checker.predicate_results,
        "manual_result_creation_required": False,
        "worker_owned_entrypoint_consumed_frozen_job": True,
        "outgoing_spend_usd": 0,
    }
    store.close()
    lease_store.release(lease, "FIXTURE_TERMINAL")
    lease_store.close()
    return evidence


def prove_broken_entrypoint_fails_closed(root: Path, successful_boqa: dict[str, object]) -> dict[str, object]:
    registry = WorkerRegistry.from_directory(MANIFESTS)
    manifest = registry.get("boqa")
    profile = load_actuator_profile(PROFILES, "boqa")
    checkout = root / "workspaces" / str(successful_boqa["execution_job_id"])
    if not checkout.exists():
        raise RuntimeError("successful BOQA checkout missing for negative proof")
    supporting = []
    for command in (*profile.execute_commands, *profile.checker_commands):
        completed = subprocess.run(command, cwd=checkout, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        supporting.append({"argv": command, "returncode": completed.returncode})
        if completed.returncode != 0:
            raise RuntimeError("supporting/self-test unexpectedly red before broken-entrypoint negative")

    broken_profile = profile.model_copy(update={"task_entrypoint": "tools/missing-atm-worker-entrypoint.mjs"})
    spec = build_spec("boqa", broken_profile, "ORDER-005-F007::boqa::broken-entrypoint", "broken")
    lease_store = WorkLeaseStore(root / "leases-boqa-broken.sqlite3")
    lease = lease_store.acquire(spec, manifest, ttl_seconds=min(1800, manifest.timeout_seconds))
    if lease is None:
        raise RuntimeError("broken-entrypoint fixture lease unavailable")
    store = ExecutionJobStore(root / "execution-boqa-broken.sqlite3")
    integrity = ProductionExecutionIntegrity(store, root / "workspaces", root / "artifacts")
    job, _ = integrity.execute(spec, lease, manifest, broken_profile)
    task_result_path = root / "artifacts" / "worker-io" / job.execution_job_id / "task-result.json"
    if job.status != ExecutionStatus.DISPATCH_FAILED or task_result_path.exists():
        raise RuntimeError("broken/missing pinned worker entrypoint did not fail closed")
    if integrity.checker_receipt(job.execution_job_id) is not None:
        raise RuntimeError("broken entrypoint unexpectedly produced checker PASS")
    evidence = {
        "source_sha": profile.source_sha,
        "broken_entrypoint": broken_profile.task_entrypoint,
        "supporting_and_self_tests": supporting,
        "job_status": job.status.value,
        "terminal_reason": job.terminal_reason,
        "task_result_created": task_result_path.exists(),
        "fails_closed": True,
    }
    store.close()
    lease_store.release(lease, "FIXTURE_NEGATIVE_TERMINAL")
    lease_store.close()
    return evidence


def main() -> int:
    if (ROOT / "src" / "atm_core" / "task_worker.py").exists():
        raise RuntimeError("ATM-owned task result helper still exists")
    dispatch_source = inspect.getsource(actuator_module.CheckoutSubprocessActuator.dispatch)
    if "task_worker.py" in dispatch_source or "sys.executable" in dispatch_source:
        raise RuntimeError("ATM-owned task helper remains on actuator task-producing path")

    with tempfile.TemporaryDirectory(prefix="atm-vnext-actuator-f007-") as directory:
        root = Path(directory)
        boqa_a = run_worker_fixture("boqa", root, "a")
        boqa_b = run_worker_fixture("boqa", root, "b")
        zungun = run_worker_fixture("zungun", root, "a")
        if boqa_a["source_sha"] != boqa_b["source_sha"]:
            raise RuntimeError("same pinned BOQA worker source changed across frozen specs")
        if boqa_a["job_spec_hash"] == boqa_b["job_spec_hash"]:
            raise RuntimeError("different frozen specs collapsed to same job-spec hash")
        if boqa_a["task_result_hash"] == boqa_b["task_result_hash"]:
            raise RuntimeError("different frozen specs collapsed to same bound task result")
        if boqa_a["task_output_sha256"] == boqa_b["task_output_sha256"]:
            raise RuntimeError("different frozen inputs collapsed to same task output")
        broken = prove_broken_entrypoint_fails_closed(root, boqa_a)
        payload = {
            "schema": "ATM_VNEXT_ACTUATOR_FIXTURE_F007_FINAL",
            "F007_REAL_FROZEN_TASK_ACTUATOR": "PASS",
            "EXACT_WORKER_SOURCE_PIN": "PROVEN",
            "TASK_ENTRYPOINT_PATH_OR_MODULE_IS_FROM_PINNED_WORKER_SOURCE": "PROVEN",
            "WORKER_ENTRYPOINT_READS_EXACT_MATERIALIZED_WORKER_JOB_SPEC": "PROVEN",
            "SAME_PINNED_WORKER_TWO_DIFFERENT_FROZEN_SPECS": "PASS",
            "BROKEN_OR_REMOVED_WORKER_TASK_ENTRYPOINT_FAILS_CLOSED": "PASS",
            "ATM_OWNED_HELPER_CANNOT_CREATE_PASSING_WORKER_TASK_RESULT": "PASS",
            "ZERO_RELAUNCH_ON_RECOVERY": "PASS",
            "OUTGOING_SPEND_USD": 0,
            "results": [boqa_a, boqa_b, zungun],
            "broken_entrypoint_negative": broken,
        }
        print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
