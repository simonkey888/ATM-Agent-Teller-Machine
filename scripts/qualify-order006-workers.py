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


def command(argv: list[str], cwd: Path, timeout: int = 180) -> str:
    p = subprocess.run(argv, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, text=True, check=False, timeout=timeout)
    if p.returncode:
        raise RuntimeError(f"command_failed:{argv}:{p.stderr.strip()}")
    return p.stdout.strip()


def make_lease(spec: WorkerJobSpec, worker_id: str, suffix: str) -> WorkLease:
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


def zungun_spec(source: str, suffix: str = "positive") -> WorkerJobSpec:
    return WorkerJobSpec(
        job_id=f"order006-zungun-{suffix}",
        canonical_opportunity_id=f"fixture:order006:zungun:{suffix}",
        external_source="order006-fixture",
        external_url="https://github.com/simonkey888/ATM-Agent-Teller-Machine/issues/31",
        task_type="zungun_reliability_audit_v1",
        frozen_acceptance_criteria=["min_findings:0", "no_authority_expansion", "outgoing_spend_usd:0"],
        repository_or_input="https://github.com/simonkey888/Zungun",
        max_spend_usd=0,
        required_capabilities=[
            "git", "filesystem", "shell", "node", "evidence",
            "zungun.network_resilience", "zungun.reconciliation",
            "zungun.blackout_testing", "zungun.reliability_audit",
        ],
        structured_requirements=["network_resilience", "reconciliation", "blackout_testing", "reliability_audit"],
        target_base_sha=source,
        allowed_paths=["tools/link-doctor/src", "tools/worker-adapter/src"],
        expected_deliverable="native reliability analysis with blackout and reconciliation evidence",
        deterministic_checks=["link_doctor", "target_clean", "scope_unchanged", "hash_artifacts", "blackout_core", "no_authority_expansion"],
    )


def across_spec(source: str, suffix: str = "positive") -> WorkerJobSpec:
    return WorkerJobSpec(
        job_id=f"order006-across-{suffix}",
        canonical_opportunity_id=f"fixture:order006:across:{suffix}",
        external_source="order006-fixture",
        external_url="https://github.com/simonkey888/ATM-Agent-Teller-Machine/issues/31",
        task_type="across_readonly_unsigned_tx_v1",
        frozen_acceptance_criteria=["data-only unsigned validation", "no signing", "no broadcast", "independent checker"],
        repository_or_input="https://github.com/simonkey888/Across-Edge",
        max_spend_usd=0,
        required_capabilities=["web3_readonly", "unsigned_transaction_validation", "evidence"],
        structured_requirements=["web3_readonly", "unsigned_transaction_validation", "no_signing", "no_broadcast"],
        target_base_sha=source,
        allowed_paths=["src/across_edge_worker"],
        expected_deliverable="read-only unsigned transaction validation evidence",
        deterministic_checks=["unsigned_tx_data_only", "checkout_unchanged", "no_external_mutation"],
    )


def prove_lineage(checkout: Path, source: str, ancestor: str) -> dict[str, object]:
    commit = command(["git", "cat-file", "-p", source], checkout)
    parents = [line.split(" ", 1)[1] for line in commit.splitlines() if line.startswith("parent ")]
    if parents != [ancestor]:
        raise RuntimeError(f"non_minimal_readiness_descendant:{source}:{parents}")
    command(["git", "fetch", "--depth=1", "origin", ancestor], checkout)
    changed = [x for x in command(["git", "diff", "--name-only", ancestor, source], checkout).splitlines() if x]
    if changed != ["tools/atm-worker-entrypoint.mjs"]:
        raise RuntimeError(f"readiness_delta_widened:{changed}")
    return {"ancestor": ancestor, "source": source, "parent": parents[0], "changed_paths": changed, "minimal_descendant": True}


def checker_for(worker_id: str, task: dict, spec: WorkerJobSpec, lease: WorkLease, source: str):
    return check_zungun(task, spec, lease, source) if worker_id == "zungun" else check_across(task, spec, lease, source)


def positive(worker_id: str, spec: WorkerJobSpec, lease: WorkLease, manifest, profile, root: Path) -> dict[str, object]:
    store = ExecutionJobStore(root / f"{worker_id}.sqlite3")
    actuator = CheckoutSubprocessActuator(store, root / "work", root / "artifacts")
    job = actuator.dispatch(spec, lease, manifest, profile)
    if job.status != ExecutionStatus.CHECKING or job.launch_count != 1:
        raise RuntimeError(f"positive_dispatch_failed:{worker_id}:{job.status}:{job.terminal_reason}")
    artifact = json.loads((root / "artifacts" / f"{job.execution_job_id}.json").read_text())
    task = artifact.get("task_result")
    if not isinstance(task, dict) or canonical_hash(task) != artifact.get("task_result_hash"):
        raise RuntimeError(f"task_result_binding_failed:{worker_id}")
    provenance = artifact.get("task_entrypoint_provenance")
    if not isinstance(provenance, dict) or provenance.get("path") != profile.task_entrypoint or provenance.get("worker_source_sha") != profile.source_sha:
        raise RuntimeError(f"entrypoint_provenance_failed:{worker_id}")
    checker = checker_for(worker_id, task, spec, lease, profile.source_sha)
    if checker.verdict != "PASS":
        raise RuntimeError(f"independent_checker_failed:{worker_id}:{[p for p in checker.predicates if not p['passed']]}")

    repeat = actuator.dispatch(spec, lease, manifest, profile)
    if repeat.execution_job_id != job.execution_job_id or repeat.launch_count != 1 or repeat.status != ExecutionStatus.CHECKING:
        raise RuntimeError(f"duplicate_launch_on_recovery:{worker_id}")
    progress = store.progress(job.execution_job_id)
    if len(progress) < 2 or not all(p.measurable_progress for p in progress):
        raise RuntimeError(f"durable_progress_failed:{worker_id}")

    only_self_test = copy.deepcopy(task)
    only_self_test["worker_self_check_pass"] = True
    only_self_test.pop("native_worker_result", None)
    if checker_for(worker_id, only_self_test, spec, lease, profile.source_sha).verdict != "FAIL":
        raise RuntimeError(f"worker_self_test_bypass:{worker_id}")

    wrong = copy.deepcopy(task)
    if worker_id == "zungun":
        wrong["native_worker_result"]["worker_source_sha"] = "0" * 40
    else:
        unsigned = next(row for row in wrong["native_analysis"]["outputs"] if row.get("capability") == "unsigned_transaction_validation")
        unsigned["observed"]["signed"] = True
    if checker_for(worker_id, wrong, spec, lease, profile.source_sha).verdict != "FAIL":
        raise RuntimeError(f"wrong_material_result_bypass:{worker_id}")

    lineage = prove_lineage(root / "work" / job.execution_job_id, profile.source_sha, READINESS[worker_id])
    evidence = {
        "worker_id": worker_id,
        "source_sha": profile.source_sha,
        "readiness_lineage": lineage,
        "execution_job_id": job.execution_job_id,
        "work_lease_id": lease.lease_id,
        "scope_hash": spec.scope_hash,
        "job_spec_hash": job.job_spec_hash,
        "status": job.status.value,
        "launch_count": repeat.launch_count,
        "progress_receipts": len(progress),
        "result_hash": job.result_hash,
        "task_result_hash": artifact.get("task_result_hash"),
        "entrypoint_provenance": provenance,
        "independent_checker_id": checker.checker_id,
        "independent_checker_hash": checker.receipt_hash,
        "independent_checker_verdict": checker.verdict,
        "independent_checker_predicates": list(checker.predicates),
        "worker_self_test_only_cannot_pass": True,
        "materially_wrong_result_cannot_pass": True,
        "zero_duplicate_launch": True,
        "outgoing_spend_usd": artifact.get("outgoing_spend_usd"),
    }
    store.close()
    return evidence


def broken(worker_id: str, factory, manifest, profile, root: Path) -> dict[str, object]:
    p = profile.model_copy(update={"task_entrypoint": "tools/missing-order006-entrypoint.mjs"})
    spec = factory(profile.source_sha, "broken")
    lease = make_lease(spec, worker_id, "broken")
    store = ExecutionJobStore(root / f"{worker_id}-broken.sqlite3")
    actuator = CheckoutSubprocessActuator(store, root / "broken-work", root / "broken-artifacts")
    job = actuator.dispatch(spec, lease, manifest, p)
    result = root / "broken-artifacts" / "worker-io" / job.execution_job_id / "task-result.json"
    passed = job.status == ExecutionStatus.DISPATCH_FAILED and job.launch_count == 1 and not result.exists()
    out = {"worker_id": worker_id, "source_sha": profile.source_sha, "status": job.status.value,
           "terminal_reason": job.terminal_reason, "launch_count": job.launch_count,
           "task_result_created": result.exists(), "fails_closed": passed}
    store.close()
    if not passed:
        raise RuntimeError(f"broken_entrypoint_not_closed:{out}")
    return out


def routing(manifests: WorkerRegistry, integrations: WorkerIntegrationRegistry) -> dict[str, object]:
    def spec(name: str, caps: list[str]) -> WorkerJobSpec:
        return WorkerJobSpec(job_id=f"router-{name}", canonical_opportunity_id=f"fixture:router:{name}", external_source="fixture",
            external_url="https://github.com/simonkey888/ATM-Agent-Teller-Machine/issues/31", task_type=f"router_{name}",
            frozen_acceptance_criteria=["routing-only"], repository_or_input="structured-fixture", max_spend_usd=0,
            required_capabilities=caps, structured_requirements=[name])
    cases = {
        "NETWORK_RELIABILITY_TASK": integrations.route_registered(spec("network", ["zungun.network_resilience", "zungun.reconciliation", "evidence"]), manifests),
        "WEB3_READONLY_TASK": integrations.route_registered(spec("web3", ["web3_readonly", "unsigned_transaction_validation", "evidence"]), manifests),
        "BROWSER_QA_TASK": integrations.route_registered(spec("browser", ["browser", "qa_repro"]), manifests),
        "STATISTICAL_RESEARCH_TASK": integrations.route_registered(spec("research", ["research", "data_analysis"]), manifests),
        "UNKNOWN_CAPABILITY": integrations.route_registered(spec("unknown", ["unknown.capability"]), manifests),
    }
    out = {key: (value[0].worker_id if value else None) for key, value in cases.items()}
    out["ACTIVE_ZUNGUN_ROUTE"] = integrations.route_active(spec("active-z", ["zungun.network_resilience", "evidence"]), manifests)
    out["ACTIVE_ACROSS_ROUTE"] = integrations.route_active(spec("active-a", ["web3_readonly", "unsigned_transaction_validation", "evidence"]), manifests)
    for name, cap in (("SIGNING_REJECTED", "web3_signing"), ("BROADCAST_REJECTED", "blockchain_broadcast")):
        try:
            integrations.route_registered(spec(name.lower(), [cap]), manifests)
            out[name] = False
        except ValueError:
            out[name] = True
    expected = {"NETWORK_RELIABILITY_TASK": "zungun", "WEB3_READONLY_TASK": "across-edge", "BROWSER_QA_TASK": None,
                "STATISTICAL_RESEARCH_TASK": None, "UNKNOWN_CAPABILITY": None, "ACTIVE_ZUNGUN_ROUTE": None,
                "ACTIVE_ACROSS_ROUTE": None, "SIGNING_REJECTED": True, "BROADCAST_REJECTED": True}
    if out != expected:
        raise RuntimeError(f"router_matrix_failed:{out}")
    return out


def main() -> int:
    manifests = WorkerRegistry.from_directory(MANIFESTS)
    integrations = WorkerIntegrationRegistry.from_directory(INTEGRATIONS)
    for worker_id in ("zungun", "across-edge"):
        record, manifest, profile = integrations.get(worker_id), manifests.get(worker_id), load_actuator_profile(PROFILES, worker_id)
        if not record.registered or record.active or manifest.enabled or record.source_pin != profile.source_sha or record.source_pin_ancestor != READINESS[worker_id]:
            raise RuntimeError(f"registration_activation_or_pin_boundary:{worker_id}")
        if manifest.max_concurrency != 1 or record.claim_authority or record.submission_authority or record.financial_authority or record.max_spend_usd != 0:
            raise RuntimeError(f"authority_concurrency_spend_boundary:{worker_id}")
    for worker_id in ("boqa", "senex-prophet"):
        record = integrations.get(worker_id)
        if record.registered or record.active or record.source_pin is not None or manifests.get(worker_id).enabled:
            raise RuntimeError(f"placeholder_boundary:{worker_id}")

    matrix = routing(manifests, integrations)
    with tempfile.TemporaryDirectory(prefix="atm-order006-") as td:
        root = Path(td)
        zp = load_actuator_profile(PROFILES, "zungun")
        zs = zungun_spec(zp.source_sha)
        ze = positive("zungun", zs, make_lease(zs, "zungun", "positive"), manifests.get("zungun"), zp, root)
        zb = broken("zungun", zungun_spec, manifests.get("zungun"), zp, root)
        ap = load_actuator_profile(PROFILES, "across-edge")
        a_s = across_spec(ap.source_sha)
        ae = positive("across-edge", a_s, make_lease(a_s, "across-edge", "positive"), manifests.get("across-edge"), ap, root)
        ab = broken("across-edge", across_spec, manifests.get("across-edge"), ap, root)
        payload = {
            "schema": "ATM_ORDER006_WORKER_INTEGRATION_QUALIFICATION_V1",
            "ZUNGUN_SOURCE_PIN_PROVEN": "PASS", "ACROSS_SOURCE_PIN_PROVEN": "PASS",
            "ROUTER_POSITIVE_NEGATIVE_MATRIX": "PASS", "REGISTERED_NOT_ACTIVE": "PASS",
            "FROZEN_JOB_CONSUMED_BY_PINNED_WORKER_SOURCE": "PASS",
            "TASK_RESULT_BOUND_TO_JOB_LEASE_SCOPE_SPEC": "PASS",
            "WORKER_SELF_TEST_ONLY_CANNOT_PASS": "PASS",
            "BROKEN_OR_MISSING_WORKER_ENTRYPOINT_FAILS_CLOSED": "PASS",
            "MIDFLIGHT_RECOVERY_ZERO_DUPLICATE_LAUNCH": "PASS",
            "INDEPENDENT_CHECKER_MATERIAL_RESULT": "PASS",
            "BOQA_PLACEHOLDER_INACTIVE_UNPINNED": "PASS", "SENEX_PLACEHOLDER_INACTIVE_UNPINNED": "PASS",
            "EXTERNAL_CLAIMS": 0, "EXTERNAL_SUBMISSIONS": 0, "OUTGOING_SPEND_USD": 0,
            "router_matrix": matrix, "zungun": ze, "zungun_broken": zb, "across_edge": ae, "across_broken": ab,
        }
        print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
