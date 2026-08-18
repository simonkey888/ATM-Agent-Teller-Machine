from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import atm as v1
import atm_v2 as core
from atm_core.actuator import load_actuator_profile
from atm_core.capabilities import CapabilityResolver
from atm_core.execution_integrity import ProductionExecutionIntegrity, RecoveryOutcome
from atm_core.execution_jobs import ExecutionJobStore, ExecutionStatus
from atm_core.models import Phase, RuntimeState
from atm_core.opportunities import OpportunityValidationError, external_state_hash
from atm_core.payments import PaymentLedger
from atm_core.workers import WorkLease, WorkerJobSpec, WorkerManifest, WorkerRegistry, WorkerResult, WorkLeaseStore, canonical_hash
from atm_core.zungun_worker import validate_worker_output_authority, validate_zungun_result_contract

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_DIR = ROOT / "workers" / "manifests"
ACTUATOR_DIR = ROOT / "workers" / "actuators"
FABRIC_DB = ROOT / ".atm" / "worker-fabric.sqlite3"
EXECUTION_DB = ROOT / ".atm" / "execution-jobs.sqlite3"
EXECUTION_WORKSPACE = ROOT / ".atm" / "execution-workspaces"
EXECUTION_ARTIFACTS = ROOT / ".atm" / "execution-artifacts"
BOQA_RAW_PREFIX = "https://raw.githubusercontent.com/simonkey888/boqa/"


def _registry() -> WorkerRegistry:
    return WorkerRegistry.from_directory(MANIFEST_DIR)


def _raw_job_id(canonical_opportunity_id: str) -> str:
    return canonical_opportunity_id.split(":", 1)[1] if ":" in canonical_opportunity_id else canonical_opportunity_id


def _safe_job_id(canonical_opportunity_id: str) -> str:
    job_id = _raw_job_id(canonical_opportunity_id)
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", job_id):
        raise OpportunityValidationError("unsafe worker job id")
    return job_id


def _worker_repo_identity(manifest: WorkerManifest) -> tuple[str, str]:
    parsed = urlparse(manifest.repo_url)
    pieces = [part for part in parsed.path.split("/") if part]
    if parsed.scheme != "https" or parsed.netloc != "github.com" or len(pieces) != 2:
        raise OpportunityValidationError("worker repository identity invalid")
    owner, repo = pieces
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", repo):
        raise OpportunityValidationError("worker repository identity unsafe")
    return owner, repo


def _worker_raw_prefix(manifest: WorkerManifest) -> str:
    owner, repo = _worker_repo_identity(manifest)
    return f"https://raw.githubusercontent.com/{owner}/{repo}/"


def worker_result_locations(manifest: WorkerManifest, canonical_opportunity_id: str, source: str) -> tuple[str, str, str]:
    """Compatibility-only public result locations; production WORK never trusts them as authority."""
    job_id = _safe_job_id(canonical_opportunity_id)
    source_slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(source).strip().lower()).strip("-")
    if not source_slug or len(source_slug) > 64:
        raise OpportunityValidationError("unsafe worker source id")
    branch = f"atm-{source_slug}-{job_id}"
    base = f"{_worker_raw_prefix(manifest)}{branch}/paid-work/{job_id}"
    return branch, f"{base}/atm-worker-result.json", f"{base}/atm-checker-result.json"


def boqa_result_locations(canonical_opportunity_id: str) -> tuple[str, str, str]:
    """Compatibility contract retained for historical BOQA tests; not production WORK authority."""
    return worker_result_locations(_registry().get("boqa"), canonical_opportunity_id, "workprotocol")


def _fetch_public_worker_json(url: str, manifest: WorkerManifest) -> dict[str, Any] | None:
    """Compatibility reader. Production phase_work/phase_check do not call this function."""
    if not url.startswith(_worker_raw_prefix(manifest)):
        raise OpportunityValidationError("worker result URL outside selected worker allowlist")
    request = urllib.request.Request(url, headers={"User-Agent": "ATM-Worker-Fabric/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise OpportunityValidationError(f"worker result fetch HTTP {exc.code}") from exc
    if not isinstance(data, dict):
        raise OpportunityValidationError("worker result must be a JSON object")
    return data


def _job_target(requirements: dict[str, Any], job: dict[str, Any]) -> tuple[str | None, str | None, list[str]]:
    repo = (
        requirements.get("targetRepository")
        or requirements.get("target_repository")
        or requirements.get("repository")
        or job.get("targetRepository")
        or job.get("target_repository")
        or job.get("repository")
    )
    base_sha = (
        requirements.get("targetBaseSha")
        or requirements.get("target_base_sha")
        or job.get("targetBaseSha")
        or job.get("target_base_sha")
    )
    paths = (
        requirements.get("allowedPaths")
        or requirements.get("allowed_paths")
        or job.get("allowedPaths")
        or job.get("allowed_paths")
        or []
    )
    if isinstance(paths, str):
        paths = [paths]
    return (str(repo).rstrip("/") if repo else None, str(base_sha) if base_sha else None, [str(value) for value in paths])


def _build_worker_job(opp: Any, snapshot: dict[str, Any]) -> WorkerJobSpec:
    job = snapshot.get("job") or snapshot
    if not isinstance(job, dict):
        raise OpportunityValidationError("worker route requires object job snapshot")
    criteria = [str(value) for value in (job.get("acceptanceCriteria") or job.get("acceptance_criteria") or [])]
    if not criteria:
        raise OpportunityValidationError("worker route requires frozen acceptance criteria")
    requirements = job.get("requirements") or {}
    if not isinstance(requirements, dict):
        raise OpportunityValidationError("worker route requirements must be structured object")

    structured = CapabilityResolver.normalize_structured_requirements(requirements)
    specialized_required = CapabilityResolver.required_for_structured(structured)
    if specialized_required == ["financial_execution"]:
        required = specialized_required
        task_type = "financial_execution_forbidden"
        repository_or_input = "https://github.com/simonkey888/boqa"
        target_base_sha = None
        allowed_paths: list[str] = []
    elif any(capability.startswith("zungun.") for capability in specialized_required):
        repo, target_base_sha, allowed_paths = _job_target(requirements, job)
        if not repo or not repo.startswith("https://github.com/"):
            raise OpportunityValidationError("Zungun route requires explicit GitHub target repository")
        if not target_base_sha or not re.fullmatch(r"[0-9a-f]{40}", target_base_sha):
            raise OpportunityValidationError("Zungun route requires exact target base SHA")
        if not allowed_paths:
            raise OpportunityValidationError("Zungun route requires bounded allowed paths")
        required = specialized_required
        task_type = "network_reliability_offline_delivery"
        repository_or_input = repo
    else:
        languages = {str(value).lower() for value in (requirements.get("languages") or [])}
        required = ["git", "github", "evidence", "ci_triage", "small_code_fix"]
        if any("python" in language for language in languages):
            required.append("python")
        if any(token in language for language in languages for token in ("typescript", "javascript", "node")):
            required.append("node")
        task_type = "small_code_fix"
        repository_or_input = "https://github.com/simonkey888/boqa"
        target_base_sha = None
        allowed_paths = []

    return WorkerJobSpec(
        job_id=_raw_job_id(opp.canonical_opportunity_id),
        canonical_opportunity_id=opp.canonical_opportunity_id,
        external_source=opp.source,
        external_url=opp.authoritative_url,
        task_type=task_type,
        frozen_acceptance_criteria=criteria,
        repository_or_input=repository_or_input,
        deadline=opp.deadline,
        max_spend_usd=Decimal("0"),
        required_capabilities=required,
        structured_requirements=structured,
        target_base_sha=target_base_sha,
        allowed_paths=allowed_paths,
        expected_deliverable=str(requirements.get("deliverable") or job.get("deliverable") or "") or None,
        deterministic_checks=[str(value) for value in (requirements.get("deterministicChecks") or requirements.get("deterministic_checks") or [])],
    )


def _load_active_work_lease(opp: Any, manifest: WorkerManifest) -> WorkLease:
    lease_id = str(getattr(opp, "worker_lease_id", "") or "")
    if not lease_id:
        raise OpportunityValidationError("production WORK requires durable WorkLease identity")
    store = WorkLeaseStore(FABRIC_DB)
    try:
        row = store.conn.execute(
            "SELECT * FROM work_leases WHERE canonical_opportunity_id=? AND lease_id=? AND worker_id=?",
            (opp.canonical_opportunity_id, lease_id, manifest.worker_id),
        ).fetchone()
        if row is None or row["released_at"] is not None or row["terminal_state"] is not None:
            raise OpportunityValidationError("production WORK lease missing/released/terminal")
        return WorkLease(
            lease_id=str(row["lease_id"]),
            canonical_opportunity_id=str(row["canonical_opportunity_id"]),
            worker_id=str(row["worker_id"]),
            scope_hash=str(row["scope_hash"]),
            acquired_at=datetime.fromisoformat(str(row["acquired_at"]).replace("Z", "+00:00")),
            expires_at=datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00")),
            heartbeat_at=datetime.fromisoformat(str(row["heartbeat_at"]).replace("Z", "+00:00")),
            terminal_state=None,
        )
    finally:
        store.close()


def phase_discover(config: dict[str, Any], state: RuntimeState, adapters: dict[str, Any]) -> None:
    core.phase_discover(config, state, adapters)
    if state.active_opportunity and state.phase == Phase.VERIFY:
        selector = str((state.last_result or {}).get("selector") or "")
        state.active_opportunity.selection_provenance = selector
        state.active_opportunity.same_pass_rediscovered = selector == "SWARM_MONEY_BOARD"


def phase_verify(config: dict[str, Any], state: RuntimeState, adapters: dict[str, Any]) -> None:
    core.phase_verify_v2(config, state, adapters)
    opp = state.active_opportunity
    if not opp or state.phase != Phase.CLAIM:
        return
    adapter = v1.adapter_for(adapters, opp)
    snapshot = adapter.fetch_authoritative(opp)
    if external_state_hash(snapshot) != opp.external_state_hash:
        state.phase = Phase.VERIFY
        state.last_result = {"status": "VERIFY_RETRY_EXTERNAL_CHANGED"}
        return
    job_spec = _build_worker_job(opp, snapshot)
    selected = _registry().choose(job_spec)
    if selected is None:
        state.last_result = {"status": "NO_WORKER_CAPABILITY", "scope_hash": job_spec.scope_hash}
        state.active_opportunity = None
        state.phase = Phase.DISCOVER
        return
    manifest, decision = selected
    branch, result_url, checker_url = worker_result_locations(manifest, opp.canonical_opportunity_id, opp.source)
    opp.worker_job_spec = job_spec.model_dump(mode="json")
    opp.worker_id = manifest.worker_id
    opp.worker_manifest_hash = canonical_hash(manifest.model_dump(mode="json"))
    opp.worker_branch = branch
    # Kept for compatibility/observability only. Production WORK never trusts these URLs as execution authority.
    opp.worker_result_url = result_url
    opp.worker_checker_url = checker_url
    state.active_opportunity = opp
    state.last_result = {
        "status": "VERIFIED_WORKER_ROUTED",
        "snapshot_hash": opp.external_state_hash,
        "worker_id": manifest.worker_id,
        "capability_score": decision.score,
        "scope_hash": job_spec.scope_hash,
    }


def phase_claim(config: dict[str, Any], state: RuntimeState, adapters: dict[str, Any]) -> None:
    del config
    opp = state.active_opportunity
    if not opp or not getattr(opp, "worker_job_spec", None) or not getattr(opp, "worker_id", None):
        raise OpportunityValidationError("CLAIM requires a pre-routed zero-authority worker")
    v1.phase_claim({}, state, adapters)
    opp = state.active_opportunity
    if not opp or state.phase != Phase.WORK:
        return
    job_spec = WorkerJobSpec.model_validate(opp.worker_job_spec)
    manifest = _registry().get(str(opp.worker_id))
    store = WorkLeaseStore(FABRIC_DB)
    try:
        lease = store.acquire(job_spec, manifest, ttl_seconds=manifest.timeout_seconds)
    finally:
        store.close()
    if lease is None:
        raise OpportunityValidationError("exclusive worker WORK lease unavailable")
    opp.worker_lease_id = lease.lease_id
    opp.worker_scope_hash = lease.scope_hash
    state.active_opportunity = opp
    state.last_result = {
        "status": "CLAIMED_WORKER_LEASED",
        "claim_id": opp.claim_id,
        "worker_id": opp.worker_id,
        "work_lease_id": lease.lease_id,
        "scope_hash": lease.scope_hash,
    }


def phase_work(config: dict[str, Any], state: RuntimeState) -> None:
    """Canonical production WORK: durable lease -> ExecutionJob -> actuator -> independent checker."""
    del config
    opp = state.active_opportunity
    if not opp or not getattr(opp, "worker_job_spec", None):
        state.last_result = {"status": "MODEL_WORK_PAUSED_NO_VERIFIED_ZERO_COST_ROUTE"}
        return
    job_spec = WorkerJobSpec.model_validate(opp.worker_job_spec)
    manifest = _registry().get(str(opp.worker_id))
    profile = load_actuator_profile(ACTUATOR_DIR, manifest.worker_id)
    lease = _load_active_work_lease(opp, manifest)

    store = ExecutionJobStore(EXECUTION_DB)
    try:
        integrity = ProductionExecutionIntegrity(store, EXECUTION_WORKSPACE, EXECUTION_ARTIFACTS)
        job, recovery = integrity.execute(job_spec, lease, manifest, profile)
        checker = integrity.checker_receipt(job.execution_job_id)
    finally:
        store.close()

    opp.execution_job_id = job.execution_job_id
    opp.execution_status = job.status.value
    opp.execution_recovery = recovery.value
    opp.worker_commit_sha = profile.source_sha
    opp.worker_result_hash = job.result_hash
    opp.independent_checker_hash = checker.receipt_hash if checker else None
    state.active_opportunity = opp

    if job.status != ExecutionStatus.TERMINAL or checker is None or checker.verdict != "PASS":
        state.last_result = {
            "status": "WORKER_EXECUTION_NOT_READY",
            "execution_job_id": job.execution_job_id,
            "execution_status": job.status.value,
            "recovery": recovery.value,
            "launch_count": job.launch_count,
        }
        return

    # Non-economic qualification fixtures expose the exact checked task artifact as an internal
    # content-addressed deliverable. It is intentionally not HTTPS, so CHECK cannot submit it.
    if job_spec.external_source == "fixture":
        opp.deliverable_url = f"atm-artifact://sha256/{job.result_hash}"
        state.active_opportunity = opp
        state.last_result = {
            "status": "WORKER_TASK_ARTIFACT_READY_INTERNAL",
            "execution_job_id": job.execution_job_id,
            "worker_id": manifest.worker_id,
            "work_lease_id": lease.lease_id,
            "result_hash": job.result_hash,
            "task_result_hash": checker.task_result_hash,
            "checker_hash": checker.receipt_hash,
            "launch_count": job.launch_count,
        }
        state.phase = Phase.CHECK
        return

    # Real economic jobs still require an externally deliverable HTTPS artifact before SUBMIT.
    state.last_result = {
        "status": "EXECUTION_CHECKED_TASK_DELIVERABLE_NOT_MATERIALIZED",
        "execution_job_id": job.execution_job_id,
        "result_hash": job.result_hash,
        "task_result_hash": checker.task_result_hash,
        "checker_hash": checker.receipt_hash,
        "launch_count": job.launch_count,
    }


def phase_check(config: dict[str, Any], state: RuntimeState) -> None:
    """Only ATM-owned durable independent checker proof can advance production CHECK."""
    del config
    opp = state.active_opportunity
    execution_job_id = str(getattr(opp, "execution_job_id", "") or "") if opp else ""
    if not opp or not execution_job_id:
        state.last_result = {"status": "CHECKER_PENDING_NO_EXECUTION_IDENTITY"}
        return
    store = ExecutionJobStore(EXECUTION_DB)
    try:
        integrity = ProductionExecutionIntegrity(store, EXECUTION_WORKSPACE, EXECUTION_ARTIFACTS)
        job = store.get(execution_job_id)
        receipt = integrity.checker_receipt(execution_job_id)
    finally:
        store.close()
    if job.status != ExecutionStatus.TERMINAL or receipt is None or receipt.verdict != "PASS" or receipt.receipt_hash != job.checker_hash:
        state.last_result = {"status": "CHECKER_PENDING_INDEPENDENT_PROOF", "execution_job_id": execution_job_id}
        return
    if not opp.deliverable_url or not str(opp.deliverable_url).startswith("https://"):
        state.last_result = {"status": "CHECKER_PASS_DELIVERABLE_UNMATERIALIZED", "execution_job_id": execution_job_id}
        return
    state.last_result = {
        "status": "CHECKER_PASS",
        "checker_id": receipt.checker_id,
        "execution_job_id": execution_job_id,
        "receipt_hash": receipt.receipt_hash,
        "evidence_refs": [f"artifact:{receipt.artifact_hash}", f"task-result:{receipt.task_result_hash}", *[f"progress:{value}" for value in receipt.progress_receipt_hashes]],
    }
    state.phase = Phase.SUBMIT


def phase_submit(config: dict[str, Any], state: RuntimeState, adapters: dict[str, Any]) -> None:
    del config
    opp = state.active_opportunity
    if not opp:
        raise OpportunityValidationError("SUBMIT requires active opportunity")
    lease_id = str(getattr(opp, "worker_lease_id", ""))
    worker_id = str(getattr(opp, "worker_id", ""))
    v1.phase_submit({}, state, adapters)
    if state.phase == Phase.MONITOR and lease_id and worker_id:
        store = WorkLeaseStore(FABRIC_DB)
        try:
            store.conn.execute(
                "UPDATE work_leases SET released_at=?,terminal_state=? "
                "WHERE canonical_opportunity_id=? AND lease_id=? AND worker_id=? AND released_at IS NULL",
                (core.utcnow().isoformat(), "SUBMITTED", opp.canonical_opportunity_id, lease_id, worker_id),
            )
        finally:
            store.close()
        state.last_result = {
            **(state.last_result or {}),
            "worker_id": worker_id,
            "work_lease_id": lease_id,
            "worker_commit_sha": getattr(opp, "worker_commit_sha", None),
        }


def run_cycle(config: dict[str, Any], state: RuntimeState, adapters: dict[str, Any], ledger: PaymentLedger) -> None:
    if state.phase == Phase.DISCOVER:
        phase_discover(config, state, adapters)
    elif state.phase == Phase.VERIFY:
        phase_verify(config, state, adapters)
    elif state.phase == Phase.CLAIM:
        phase_claim(config, state, adapters)
    elif state.phase == Phase.WORK:
        phase_work(config, state)
    elif state.phase == Phase.CHECK:
        phase_check(config, state)
    elif state.phase == Phase.SUBMIT:
        phase_submit(config, state, adapters)
    elif state.phase == Phase.MONITOR:
        core.refresh_discovery_cache(config, adapters)
        v1.phase_monitor(config, state, adapters)
    elif state.phase == Phase.PAYMENT_VERIFY:
        v1.phase_payment_verify(config, state, adapters, ledger)
    else:
        raise RuntimeError(f"unsupported phase: {state.phase}")
    state.cycle += 1


def install() -> None:
    """Install the R1 worker router into the existing single ATM supervisor."""
    core.run_cycle = run_cycle
