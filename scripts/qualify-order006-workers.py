from __future__ import annotations

import copy
import json
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from atm_core.actuator import CheckoutSubprocessActuator, load_actuator_profile
from atm_core.execution_jobs import ExecutionJobStore, ExecutionStatus
from atm_core.worker_integration import WorkerIntegrationRegistry
from atm_core.worker_integration_checker import check_across, check_zungun
from atm_core.workers import WorkerJobSpec, WorkerRegistry, WorkLease, canonical_hash


ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ROOT / "workers" / "manifests"
PROFILES = ROOT / "workers" / "actuators"
INTEGRATIONS = ROOT / "workers" / "integrations"
READINESS = {
    "zungun": "c95002f94d5c5316e83bfca7215cf1d591a62739",
    "across-edge": "f65e597a650bfaa5b09824b299811c8da713249e",
}


def run(argv: list[str], cwd: Path, timeout: int = 180) -> str:
    completed = subprocess.run(
        argv,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"command failed {argv!r}: {completed.stderr.strip()}")
    return completed.stdout.strip()


def lease_for(spec: WorkerJobSpec, worker_id: str, suffix: str) -> WorkLease:
    now = datetime.now(timezone.utc)
    return WorkLease(
        lease_id=f"order006-{worker_id}-{suffix}",
        canonical_opportunity_id=spec.canonical_opportunity_id,
        worker_id=worker_id,
        scope_hash=spec.scope_hash,
        acquired_at=now - timedelta(seconds=2),
        expires_at=now + timedelta(minutes=20),
        heartbeat_at=now,
        terminal_state=None,
    )


def zungun_spec(source_sha: str, suffix: str = "positive") -> WorkerJobSpec:
    return WorkerJobSpec(
        job_id=f"order006-zungun-{suffix}",
        canonical_opportunity_id=f"fixture:order006:zungun:{suffix}",
        external_source="order006-fixture",
        external_url="https://github.com/simonkey888/ATM-Agent-Teller-Machine/issues/31",
        task_type="zungun_reliability_audit_v1",
        frozen_acceptance_criteria=[
            "min_findings:0",
            "no_authority_expansion",
            "outgoing_spend_usd:0",
            "result_preserves_unknown",
        ],
        repository_or_input="https://github.com/simonkey888/Zungun",
        max_spend_usd=0,
        required_capabilities=[
            "git",
            "filesystem",
            "shell",
            "node",
            "evidence",
            "zungun.network_resilience",
            "zungun.reconciliation",
            "zungun.blackout_testing",
            "zungun.reliability_audit",
        ],
        structured_requirements=[
            "network_resilience",
            "reconciliation",
            "blackout_testing",
            "reliability_audit",
        ],
        target_base_sha=source_sha,
        allowed_paths=["tools/link-doctor/src", "tools/worker-adapter/src"],
        expected_deliverable="Zungun native reliability analysis with blackout and reconciliation evidence",
        deterministic_checks=[
            "link_doctor",
            "target_clean",
            "scope_unchanged",
            "hash_artifacts",
            "blackout_core",
            "no_authority_expansion",
        ],
    )


def across_spec(source_sha: str, suffix: str = "positive") -> WorkerJobSpec:
    return WorkerJobSpec(
        job_id=f"order006-across-{suffix}",
        canonical_opportunity_id=f"fixture:order006:across:{suffix}",
        external_source="order006-fixture",
        external_url="https://github.com/simonkey888/ATM-Agent-Teller-Machine/issues/31",
        task_type="across_readonly_unsigned_tx_v1",
        frozen_acceptance_criteria=[
            "unsigned transaction is validated as data only",
            "signing and broadcast remain zero",
            "worker checkout remains unchanged",
            "ATM independently checks material result",
        ],
        repository_or_input="https://github.com/simonkey888/Across-Edge",
        max_spend_usd=0,
        required_capabilities=["web3_readonly", "unsigned_transaction_validation", "evidence"],
        structured_requirements=["web3_readonly", "unsigned_transaction_validation", "no_signing", "no_broadcast"],
        target_base_sha=source_sha,
        allowed_paths=["src/across_edge_worker"],
        expected_deliverable="read-only unsigned transaction validation evidence",
        deterministic_checks=["unsigned_tx_data_only", "checkout_unchanged", "no_external_mutation"],
    )


def prove_minimal_readiness_descendant(checkout: Path, source_sha: str, ancestor_sha: str) -> dict[str, object]:
    commit_text = run(["git", "cat-file", "-p", source_sha], checkout)
    parents = [line.split(" ", 1)[1] for line in commit_text.splitlines() if line.startswith("parent ")]
    if parents != [ancestor_sha]:
        raise RuntimeError(f"source pin is not a one-commit readiness descendant: {source_sha}:{parents}")
    run(["git", "fetch", "--depth=1", "origin", ancestor_sha], checkout)
    changed = [row for row in run(["git", "diff", "--name-only", ancestor_sha, source_sha], checkout).splitlines() if row]
    if changed != ["tools/atm-worker-entrypoint.mjs"]:
        raise RuntimeError(f"readiness descendant widened beyond integration adapter: {changed}")
    return {
        "readiness_ancestor": ancestor_sha,
        "source_sha": source_sha,
        "source_parent": parents[0],
        "changed_paths_from_readiness": changed,
        "minimal_descendant": True,
    }


def dispatch_positive(
    worker_id: str,
    spec: WorkerJobSpec,
    lease: WorkLease,
    manifest,
    profile,
    root: Path,
) -> tuple[dict[str, object], object, dict[str, object]]:
    store = ExecutionJobStore(root / f"execution-{worker_id}.sqlite3")
    actuator = CheckoutSubprocessActuator(store, root / "workspaces", root / "artifacts")
    job = actuator.dispatch(spec, lease, manifest, profile)
    if job.status != ExecutionStatus.CHECKING or job.launch_count != 1:
        raise RuntimeError(f"{worker_id} positive dispatch failed: {job.status}:{job.terminal_reason}:{job.launch_count}")
    artifact_path = root / "artifacts" / f"{job.execution_job_id}.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    task_result = artifact.get("task_result")
    if not isinstance(task_result, dict):
        raise RuntimeError(f"{worker_id} task result missing")
    if canonical_hash(task_result) != artifact.get("task_result_hash"):
        raise RuntimeError(f"{worker_id} task result hash mismatch")
    if artifact.get("outgoing_spend_usd") != 0:
        raise RuntimeError(f"{worker_id} artifact spend nonzero")

    checker = check_zungun(task_result, spec, lease, profile.source_sha) if worker_id == "zungun" else check_across(task_result, spec, lease, profile.source_sha)
    if checker.verdict != "PASS":
        failed = [row for row in checker.predicates if not row["passed"]]
        raise RuntimeError(f"{worker_id} independent checker failed: {failed}")

    recovered = actuator.dispatch(spec, lease, manifest, profile)
    if recovered.execution_job_id != job.execution_job_id or recovered.launch_count != 1 or recovered.status != ExecutionStatus.CHECKING:
        raise RuntimeError(f"{worker_id} immutable recovery duplicated launch")

    progress = store.progress(job.execution_job_id)
    if len(progress) < 2 or not all(receipt.measurable_progress for receipt in progress):
        raise RuntimeError(f"{worker_id} durable measurable progress missing")
    checkout = root / "workspaces" / job.execution_job_id
    lineage = prove_minimal_readiness_descendant(checkout, profile.source_sha, READINESS[worker_id])

    self_test_only = copy.deepcopy(task_result)
    self_test_only["worker_self_check_pass"] = True
    self_test_only.pop("native_worker_result", None)
    self_test_checker = check_zungun(self_test_only, spec, lease, profile.source_sha) if worker_id == "zungun" else check_across(self_test_only, spec, lease, profile.source_sha)
    if self_test_checker.verdict != "FAIL":
        raise RuntimeError(f"{worker_id} worker self-test-only payload bypassed independent checker")

    wrong = copy.deepcopy(task_result)
    if worker_id == "zungun":
        wrong["native_worker_result"]["worker_source_sha"] = "0" * 40
        wrong_checker = check_zungun(wrong, spec, lease, profile.source_sha)
    else:
        observations = wrong["native_analysis"]["outputs"]
        unsigned = next(row for row in observations if row.get("capability") == "unsigned_transaction_validation")
        unsigned["observed"]["signed"] = True
        wrong_checker = check_across(wrong, spec, lease, profile.source_sha)
    if wrong_checker.verdict != "FAIL":
        raise RuntimeError(f"{worker_id} materially wrong result bypassed independent checker")

    entrypoint = artifact.get("worker_entrypoint")
    if not isinstance(entrypoint, dict) or entrypoint.get("path") != profile.task_entrypoint:
        raise RuntimeError(f"{worker_id} artifact missing worker-owned entrypoint provenance")

    evidence = {
        "worker_id": worker_id,
        "source_sha": profile.source_sha,
        "readiness": lineage,
        "execution_job_id": job.execution_job_id,
        "work_lease_id": lease.lease_id,
        "scope_hash": spec.scope_hash,
        "job_spec_hash": job.job_spec_hash,
        "status": job.status.value,
        "launch_count": recovered.launch_count,
        "progress_receipts": len(progress),
        "result_hash": job.result_hash,
        "task_result_hash": artifact.get("task_result_hash"),
        "worker_entrypoint": entrypoint,
        "independent_checker_id": checker.checker_id,
        "independent_checker_hash": checker.receipt_hash,
        "independent_checker_verdict": checker.verdict,
        "independent_checker_predicates": list(checker.predicates),
        "self_test_only_cannot_pass": True,
        "materially_wrong_result_cannot_pass": True,
        "midflight_recovery_zero_duplicate_launch": True,
        "outgoing_spend_usd": 0,
    }
    store.close()
    return task_result, checker, evidence


def broken_entrypoint_negative(worker_id: str, spec_factory, manifest, profile, root: Path) -> dict[str, object]:
    broken_profile = profile.model_copy(update={"task_entrypoint": "tools/missing-order006-entrypoint.mjs"})
    spec = spec_factory(profile.source_sha, "broken-entrypoint")
    lease = lease_for(spec, worker_id, "broken-entrypoint")
    store = ExecutionJobStore(root / f"execution-{worker_id}-broken.sqlite3")
    actuator = CheckoutSubprocessActuator(store, root / "broken-workspaces", root / "broken-artifacts")
    job = actuator.dispatch(spec, lease, manifest, broken_profile)
    task_result_path = root / "broken-artifacts" / "worker-io" / job.execution_job_id / "task-result.json"
    passed = job.status == ExecutionStatus.DISPATCH_FAILED and job.launch_count == 1 and not task_result_path.exists()
    evidence = {
        "worker_id": worker_id,
        "source_sha": profile.source_sha,
        "broken_entrypoint": broken_profile.task_entrypoint,
        "status": job.status.value,
        "terminal_reason": job.terminal_reason,
        "launch_count": job.launch_count,
        "task_result_created": task_result_path.exists(),
        "fails_closed": passed,
    }
    store.close()
    if not passed:
        raise RuntimeError(f"{worker_id} broken entrypoint did not fail closed: {evidence}")
    return evidence


def router_matrix(manifests: WorkerRegistry, integrations: WorkerIntegrationRegistry) -> dict[str, object]:
    def spec(name: str, capabilities: list[str]) -> WorkerJobSpec:
        return WorkerJobSpec(
            job_id=f"order006-router-{name}",
            canonical_opportunity_id=f"fixture:order006:router:{name}",
            external_source="fixture",
            external_url="https://github.com/simonkey888/ATM-Agent-Teller-Machine/issues/31",
            task_type=f"order006_router_{name}",
            frozen_acceptance_criteria=["routing-only"],
            repository_or_input="order006-router-fixture",
            max_spend_usd=0,
            required_capabilities=capabilities,
            structured_requirements=[name],
        )

    z = integrations.route_registered(spec("network", ["zungun.network_resilience", "zungun.reconciliation", "evidence"]), manifests)
    a = integrations.route_registered(spec("web3", ["web3_readonly", "unsigned_transaction_validation", "evidence"]), manifests)
    browser = integrations.route_registered(spec("browser", ["browser", "qa_repro"]), manifests)
    research = integrations.route_registered(spec("research", ["research", "data_analysis"]), manifests)
    unknown = integrations.route_registered(spec("unknown", ["unknown.capability"]), manifests)
    active_z = integrations.route_active(spec("network-active", ["zungun.network_resilience", "zungun.reconciliation", "evidence"]), manifests)
    active_a = integrations.route_active(spec("web3-active", ["web3_readonly", "unsigned_transaction_validation", "evidence"]), manifests)
    signing_rejected = False
    broadcast_rejected = False
    try:
        integrations.route_registered(spec("signing", ["web3_signing"]), manifests)
    except ValueError:
        signing_rejected = True
    try:
        integrations.route_registered(spec("broadcast", ["blockchain_broadcast"]), manifests)
    except ValueError:
        broadcast_rejected = True

    result = {
        "NETWORK_RELIABILITY_TASK": z[0].worker_id if z else None,
        "WEB3_READONLY_TASK": a[0].worker_id if a else None,
        "BROWSER_QA_TASK": browser[0].worker_id if browser else None,
        "STATISTICAL_RESEARCH_TASK": research[0].worker_id if research else None,
        "UNKNOWN_CAPABILITY": unknown[0].worker_id if unknown else None,
        "ACTIVE_ZUNGUN_ROUTE": active_z[0].worker_id if active_z else None,
        "ACTIVE_ACROSS_ROUTE": active_a[0].worker_id if active_a else None,
        "SIGNING_REJECTED": signing_rejected,
        "BROADCAST_REJECTED": broadcast_rejected,
    }
    expected = {
        "NETWORK_RELIABILITY_TASK": "zungun",
        "WEB3_READONLY_TASK": "across-edge",
        "BROWSER_QA_TASK": None,
        "STATISTICAL_RESEARCH_TASK": None,
        "UNKNOWN_CAPABILITY": None,
        "ACTIVE_ZUNGUN_ROUTE": None,
        "ACTIVE_ACROSS_ROUTE": None,
        "SIGNING_REJECTED": True,
        "BROADCAST_REJECTED": True,
    }
    if result != expected:
        raise RuntimeError(f"router positive/negative matrix failed: {result}")
    return result


def main() -> int:
    manifests = WorkerRegistry.from_directory(MANIFESTS)
    integrations = WorkerIntegrationRegistry.from_directory(INTEGRATIONS)
    for worker_id in ("zungun", "across-edge"):
        record = integrations.get(worker_id)
        if not record.registered or record.active or manifests.get(worker_id).enabled:
            raise RuntimeError(f"{worker_id} must be registered but inactive")
        if record.source_pin != load_actuator_profile(PROFILES, worker_id).source_sha:
            raise RuntimeError(f"{worker_id} integration source pin/profile mismatch")
        if record.source_pin_ancestor != READINESS[worker_id]:
            raise RuntimeError(f"{worker_id} readiness ancestor mismatch")
        if any((record.claim_authority, record.submission_authority, record.financial_authority)) or record.max_spend_usd != 0:
            raise RuntimeError(f"{worker_id} authority/spend boundary widened")
    for worker_id in ("boqa", "senex-prophet"):
        record = integrations.get(worker_id)
        if record.registered or record.active or record.source_pin is not None or manifests.get(worker_id).enabled:
            raise RuntimeError(f"{worker_id} must remain inactive source-unpinned placeholder")

    matrix = router_matrix(manifests, integrations)
    with tempfile.TemporaryDirectory(prefix="atm-order006-") as directory:
        root = Path(directory)
        z_manifest = manifests.get("zungun")
        z_profile = load_actuator_profile(PROFILES, "zungun")
        z_spec = zungun_spec(z_profile.source_sha)
        z_lease = lease_for(z_spec, "zungun", "positive")
        _, _, z_evidence = dispatch_positive("zungun", z_spec, z_lease, z_manifest, z_profile, root)
        z_broken = broken_entrypoint_negative("zungun", zungun_spec, z_manifest, z_profile, root)

        a_manifest = manifests.get("across-edge")
        a_profile = load_actuator_profile(PROFILES, "across-edge")
        a_spec = across_spec(a_profile.source_sha)
        a_lease = lease_for(a_spec, "across-edge", "positive")
        _, _, a_evidence = dispatch_positive("across-edge", a_spec, a_lease, a_manifest, a_profile, root)
        a_broken = broken_entrypoint_negative("across-edge", across_spec, a_manifest, a_profile, root)

        payload = {
            "schema": "ATM_ORDER006_WORKER_INTEGRATION_QUALIFICATION_V1",
            "ZUNGUN_SOURCE_PIN_PROVEN": "PASS",
            "ACROSS_SOURCE_PIN_PROVEN": "PASS",
            "ROUTER_POSITIVE_NEGATIVE_MATRIX": "PASS",
            "REGISTERED_NOT_ACTIVE": "PASS",
            "FROZEN_JOB_CONSUMED_BY_PINNED_WORKER_SOURCE": "PASS",
            "TASK_RESULT_BOUND_TO_JOB_LEASE_SCOPE_SPEC": "PASS",
            "WORKER_SELF_TEST_ONLY_CANNOT_PASS": "PASS",
            "BROKEN_OR_MISSING_WORKER_ENTRYPOINT_FAILS_CLOSED": "PASS",
            "MIDFLIGHT_RECOVERY_ZERO_DUPLICATE_LAUNCH": "PASS",
            "INDEPENDENT_CHECKER_MATERIAL_RESULT": "PASS",
            "BOQA_PLACEHOLDER_INACTIVE_UNPINNED": "PASS",
            "SENEX_PLACEHOLDER_INACTIVE_UNPINNED": "PASS",
            "EXTERNAL_CLAIMS": 0,
            "EXTERNAL_SUBMISSIONS": 0,
            "OUTGOING_SPEND_USD": 0,
            "router_matrix": matrix,
            "zungun": z_evidence,
            "zungun_broken_entrypoint_negative": z_broken,
            "across_edge": a_evidence,
            "across_broken_entrypoint_negative": a_broken,
        }
        print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
