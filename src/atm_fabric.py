from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from decimal import Decimal
from pathlib import Path
from typing import Any

import atm as v1
import atm_v2 as core
from atm_core.models import Phase, RuntimeState
from atm_core.opportunities import OpportunityValidationError, external_state_hash
from atm_core.payments import PaymentLedger
from atm_core.workers import WorkerJobSpec, WorkerRegistry, WorkerResult, WorkLeaseStore, canonical_hash

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_DIR = ROOT / "workers" / "manifests"
FABRIC_DB = ROOT / ".atm" / "worker-fabric.sqlite3"
BOQA_RAW_PREFIX = "https://raw.githubusercontent.com/simonkey888/boqa/"


def _registry() -> WorkerRegistry:
    return WorkerRegistry.from_directory(MANIFEST_DIR)


def _raw_job_id(canonical_opportunity_id: str) -> str:
    return canonical_opportunity_id.split(":", 1)[1] if ":" in canonical_opportunity_id else canonical_opportunity_id


def boqa_result_locations(canonical_opportunity_id: str) -> tuple[str, str, str]:
    job_id = _raw_job_id(canonical_opportunity_id)
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", job_id):
        raise OpportunityValidationError("unsafe worker job id")
    branch = f"atm-workprotocol-{job_id}"
    base = f"{BOQA_RAW_PREFIX}{branch}/paid-work/{job_id}"
    return branch, f"{base}/atm-worker-result.json", f"{base}/atm-checker-result.json"


def _fetch_public_worker_json(url: str) -> dict[str, Any] | None:
    if not url.startswith(BOQA_RAW_PREFIX):
        raise OpportunityValidationError("worker result URL outside BOQA allowlist")
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


def _build_worker_job(opp: Any, snapshot: dict[str, Any]) -> WorkerJobSpec:
    job = snapshot.get("job") or snapshot
    criteria = [str(value) for value in (job.get("acceptanceCriteria") or [])]
    if not criteria:
        raise OpportunityValidationError("worker route requires frozen acceptance criteria")
    requirements = job.get("requirements") or {}
    languages = {str(value).lower() for value in (requirements.get("languages") or [])}
    required = ["git", "github", "evidence", "ci_triage", "small_code_fix"]
    if any("python" in language for language in languages):
        required.append("python")
    if any(token in language for language in languages for token in ("typescript", "javascript", "node")):
        required.append("node")
    return WorkerJobSpec(
        job_id=_raw_job_id(opp.canonical_opportunity_id),
        canonical_opportunity_id=opp.canonical_opportunity_id,
        external_source=opp.source,
        external_url=opp.authoritative_url,
        task_type="small_code_fix",
        frozen_acceptance_criteria=criteria,
        repository_or_input="https://github.com/simonkey888/boqa",
        deadline=opp.deadline,
        max_spend_usd=Decimal("0"),
        required_capabilities=required,
    )


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
    branch, result_url, checker_url = boqa_result_locations(opp.canonical_opportunity_id)
    opp.worker_job_spec = job_spec.model_dump(mode="json")
    opp.worker_id = manifest.worker_id
    opp.worker_manifest_hash = canonical_hash(manifest.model_dump(mode="json"))
    opp.worker_branch = branch
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
    del config
    opp = state.active_opportunity
    if not opp or not getattr(opp, "worker_job_spec", None):
        state.last_result = {"status": "MODEL_WORK_PAUSED_NO_VERIFIED_ZERO_COST_ROUTE"}
        return
    job_spec = WorkerJobSpec.model_validate(opp.worker_job_spec)
    raw = _fetch_public_worker_json(str(opp.worker_result_url))
    if raw is None:
        state.last_result = {
            "status": "WORKER_PENDING",
            "worker_id": opp.worker_id,
            "work_lease_id": opp.worker_lease_id,
            "scope_hash": job_spec.scope_hash,
        }
        return
    raw_scope = str(raw.pop("scope_hash", ""))
    raw_lease = str(raw.pop("lease_id", ""))
    if raw_scope != job_spec.scope_hash or raw_lease != str(opp.worker_lease_id):
        raise OpportunityValidationError("worker result scope/lease binding mismatch")
    result = WorkerResult.model_validate(raw)
    if result.worker_id != str(opp.worker_id) or result.job_id != job_spec.job_id:
        raise OpportunityValidationError("worker result identity mismatch")
    if result.status.upper() not in {"PASS", "READY_FOR_CHECK"}:
        raise OpportunityValidationError("worker result not ready for checker")
    if not result.commit_sha or not re.fullmatch(r"[0-9a-f]{40}", result.commit_sha):
        raise OpportunityValidationError("worker result lacks exact commit SHA")
    deliverable = result.pr_url or (result.artifact_urls[0] if result.artifact_urls else None)
    if not deliverable or not deliverable.startswith("https://github.com/simonkey888/boqa"):
        raise OpportunityValidationError("worker deliverable outside BOQA allowlist")
    opp.deliverable_url = deliverable
    opp.worker_commit_sha = result.commit_sha
    opp.worker_result_hash = result.envelope_hash
    state.active_opportunity = opp
    state.last_result = {
        "status": "WORKER_ARTIFACT_READY",
        "worker_id": opp.worker_id,
        "work_lease_id": opp.worker_lease_id,
        "commit_sha": result.commit_sha,
        "result_hash": result.envelope_hash,
    }
    state.phase = Phase.CHECK


def phase_check(config: dict[str, Any], state: RuntimeState) -> None:
    del config
    opp = state.active_opportunity
    if not opp or not getattr(opp, "worker_checker_url", None):
        state.last_result = {"status": "CHECKER_PENDING_NO_FABRIC_RECEIPT"}
        return
    job_spec = WorkerJobSpec.model_validate(opp.worker_job_spec)
    receipt = _fetch_public_worker_json(str(opp.worker_checker_url))
    if receipt is None:
        state.last_result = {
            "status": "CHECKER_PENDING",
            "worker_id": opp.worker_id,
            "work_lease_id": opp.worker_lease_id,
        }
        return
    required = {"checker_id", "job_id", "scope_hash", "lease_id", "commit_sha", "status", "evidence_refs", "checked_at"}
    if not required.issubset(receipt):
        raise OpportunityValidationError("checker receipt missing required fields")
    if str(receipt["checker_id"]) == str(opp.worker_id):
        raise OpportunityValidationError("checker must be independent from worker identity")
    if str(receipt["job_id"]) != job_spec.job_id:
        raise OpportunityValidationError("checker job identity mismatch")
    if str(receipt["scope_hash"]) != job_spec.scope_hash or str(receipt["lease_id"]) != str(opp.worker_lease_id):
        raise OpportunityValidationError("checker scope/lease mismatch")
    if str(receipt["commit_sha"]) != str(opp.worker_commit_sha):
        raise OpportunityValidationError("checker commit mismatch")
    if str(receipt["status"]).upper() not in {"PASS", "READY_TO_SUBMIT"}:
        state.last_result = {"status": "CHECKER_FAIL", "evidence_refs": receipt.get("evidence_refs", [])}
        return
    state.last_result = {
        "status": "CHECKER_PASS",
        "checker_id": receipt["checker_id"],
        "work_lease_id": opp.worker_lease_id,
        "commit_sha": opp.worker_commit_sha,
        "receipt_hash": canonical_hash(receipt),
        "evidence_refs": receipt.get("evidence_refs", []),
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
