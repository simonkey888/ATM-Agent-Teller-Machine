from __future__ import annotations

import hashlib
import json
import tempfile
from decimal import Decimal
from pathlib import Path

import atm_fabric
from atm_core.execution_integrity import ProductionExecutionIntegrity
from atm_core.execution_jobs import ExecutionJobStore, ExecutionStatus
from atm_core.models import Opportunity, Phase, RuntimeState
from atm_core.workers import WorkerJobSpec, WorkLeaseStore


class FixtureClaimAdapter:
    """Synthetic claim boundary only. It performs zero network/economic side effects."""

    def claim(self, opportunity):
        return {"claim": {"id": "order005-fixture-claim"}}


def main() -> int:
    registry = atm_fabric._registry()
    manifest = registry.get("boqa")
    frozen_input = "ORDER-005-R2 production-path frozen task input"
    expected_sha = hashlib.sha256(frozen_input.encode("utf-8")).hexdigest()
    spec = WorkerJobSpec(
        job_id="order005-production-fixture-r2",
        canonical_opportunity_id="fixture:order005-production-fixture-r2",
        external_source="fixture",
        external_url="https://example.invalid/order005-production-fixture-r2",
        task_type="deterministic_digest_v1",
        frozen_acceptance_criteria=[
            "production phase creates durable ExecutionJob before dispatch",
            "selected BOQA process consumes exact materialized WorkerJobSpec",
            "task-specific result binds exact lease/scope/job-spec",
            "ATM checker materially recomputes frozen acceptance outside worker checkout",
        ],
        repository_or_input=frozen_input,
        max_spend_usd=Decimal("0"),
        required_capabilities=["git", "github", "filesystem", "shell", "node", "evidence", "small_code_fix"],
        expected_deliverable="content-addressed internal deterministic result fixture",
        deterministic_checks=[
            f"TASK_RESULT_SHA256_EQ:{expected_sha}",
            f"TASK_RESULT_BYTE_LENGTH_EQ:{len(frozen_input.encode('utf-8'))}",
        ],
    )

    with tempfile.TemporaryDirectory(prefix="atm-order005-production-r2-") as directory:
        root = Path(directory)
        atm_fabric.FABRIC_DB = root / "worker-fabric.sqlite3"
        atm_fabric.EXECUTION_DB = root / "execution-jobs.sqlite3"
        atm_fabric.EXECUTION_WORKSPACE = root / "execution-workspaces"
        atm_fabric.EXECUTION_ARTIFACTS = root / "execution-artifacts"

        opp = Opportunity(
            canonical_opportunity_id=spec.canonical_opportunity_id,
            source="fixture",
            authoritative_url=spec.external_url,
            upstream_status="claim-confirmed-fixture",
            reward_gross=Decimal("1"),
            worker_job_spec=spec.model_dump(mode="json"),
            worker_id=manifest.worker_id,
        )
        state = RuntimeState(phase=Phase.CLAIM, active_opportunity=opp)

        atm_fabric.phase_claim({}, state, {"fixture": FixtureClaimAdapter()})
        assert state.phase == Phase.WORK
        assert state.active_opportunity is not None
        lease_id = str(state.active_opportunity.worker_lease_id)
        assert lease_id

        lease_store = WorkLeaseStore(atm_fabric.FABRIC_DB)
        lease_rows = lease_store.conn.execute("SELECT * FROM work_leases").fetchall()
        assert len(lease_rows) == 1
        assert str(lease_rows[0]["lease_id"]) == lease_id
        lease_store.close()

        atm_fabric.phase_work({}, state)
        assert state.phase == Phase.CHECK, state.last_result
        assert state.last_result["status"] == "WORKER_TASK_ARTIFACT_READY_INTERNAL"
        execution_id = str(state.active_opportunity.execution_job_id)
        deliverable_ref = str(state.active_opportunity.deliverable_url)
        assert deliverable_ref.startswith("atm-artifact://sha256/")

        store = ExecutionJobStore(atm_fabric.EXECUTION_DB)
        integrity = ProductionExecutionIntegrity(store, atm_fabric.EXECUTION_WORKSPACE, atm_fabric.EXECUTION_ARTIFACTS)
        job = store.get(execution_id)
        progress = store.progress(execution_id)
        checker = integrity.checker_receipt(execution_id)
        artifact = json.loads(integrity.artifact_path(execution_id).read_text(encoding="utf-8"))
        task_result = artifact["task_result"]
        assert job.status == ExecutionStatus.TERMINAL
        assert job.launch_count == 1
        assert len(progress) >= 2
        assert checker is not None and checker.verdict == "PASS"
        assert checker.receipt_hash == job.checker_hash
        assert checker.checker_id == "ATM_INDEPENDENT_CHECKER_V1"
        assert checker.checker_boundary == "ATM_SUPERVISOR_INDEPENDENT_VERIFIER_V2"
        assert task_result["task_output"]["sha256"] == expected_sha
        assert artifact["task_result_hash"] == checker.task_result_hash
        assert all(item["passed"] for item in checker.predicate_results)
        store.close()

        # The internal fixture artifact is not a real external submission deliverable.
        # CHECK therefore remains closed and proves zero external submissions.
        atm_fabric.phase_check({}, state)
        assert state.phase == Phase.CHECK, state.last_result
        assert state.last_result["status"] == "CHECKER_PASS_DELIVERABLE_UNMATERIALIZED"

        print(json.dumps({
            "ORDER_005_PRODUCTION_PATH": "PASS",
            "F007_REAL_FROZEN_TASK_ACTUATOR": "PASS",
            "F008_MATERIAL_INDEPENDENT_CHECKER": "PASS",
            "claim_id": "order005-fixture-claim",
            "work_lease_id": lease_id,
            "execution_job_id": execution_id,
            "job_spec_hash": job.job_spec_hash,
            "launch_count": job.launch_count,
            "progress_receipts": len(progress),
            "result_hash": job.result_hash,
            "task_result_hash": checker.task_result_hash,
            "checker_id": checker.checker_id,
            "checker_boundary": checker.checker_boundary,
            "checker_hash": checker.receipt_hash,
            "material_predicates": checker.predicate_results,
            "deliverable_ref": deliverable_ref,
            "next_phase": state.phase.value,
            "OUTGOING_SPEND_USD": 0,
            "EXTERNAL_CLAIMS": 0,
            "EXTERNAL_SUBMISSIONS": 0,
        }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
