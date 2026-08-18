from __future__ import annotations

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
    spec = WorkerJobSpec(
        job_id="order005-production-fixture",
        canonical_opportunity_id="fixture:order005-production-fixture",
        external_source="fixture",
        external_url="https://example.invalid/order005-production-fixture",
        task_type="small_code_fix",
        frozen_acceptance_criteria=[
            "real production phase creates durable ExecutionJob before dispatch",
            "real BOQA pinned worker process ACKs and records measurable progress",
            "ATM independent checker re-resolves exact artifact and frozen criteria",
        ],
        repository_or_input=manifest.repo_url,
        max_spend_usd=Decimal("0"),
        required_capabilities=["git", "github", "filesystem", "shell", "node", "evidence", "small_code_fix"],
        expected_deliverable="immutable pinned BOQA qualification commit",
    )

    with tempfile.TemporaryDirectory(prefix="atm-order005-production-") as directory:
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
        execution_id = str(state.active_opportunity.execution_job_id)

        store = ExecutionJobStore(atm_fabric.EXECUTION_DB)
        integrity = ProductionExecutionIntegrity(store, atm_fabric.EXECUTION_WORKSPACE, atm_fabric.EXECUTION_ARTIFACTS)
        job = store.get(execution_id)
        progress = store.progress(execution_id)
        checker = integrity.checker_receipt(execution_id)
        assert job.status == ExecutionStatus.TERMINAL
        assert job.launch_count == 1
        assert len(progress) >= 2
        assert checker is not None and checker.verdict == "PASS"
        assert checker.receipt_hash == job.checker_hash
        assert checker.checker_id == "ATM_INDEPENDENT_CHECKER_V1"
        store.close()

        atm_fabric.phase_check({}, state)
        assert state.phase == Phase.SUBMIT, state.last_result

        print(json.dumps({
            "ORDER_005_PRODUCTION_PATH": "PASS",
            "claim_id": "order005-fixture-claim",
            "work_lease_id": lease_id,
            "execution_job_id": execution_id,
            "launch_count": job.launch_count,
            "progress_receipts": len(progress),
            "result_hash": job.result_hash,
            "checker_id": checker.checker_id,
            "checker_hash": checker.receipt_hash,
            "next_phase": state.phase.value,
            "OUTGOING_SPEND_USD": 0,
            "EXTERNAL_CLAIMS": 0,
            "EXTERNAL_SUBMISSIONS": 0,
        }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
