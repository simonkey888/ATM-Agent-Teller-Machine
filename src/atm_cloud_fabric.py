from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import atm as v1
import atm_cloud
import atm_fabric
import atm_v2 as core_runtime
from atm_core.action_history import install_money_board_history_guard
from atm_core.autonomy_fabric import CapabilityGap, CapabilityGapLedger, DeterministicSupervisor, EventKind, classify_gap
from atm_core.capability_flywheel import (
    public_flywheel_status,
    enqueue_gap,
    ensure_synthetic_plumbing_if_no_real_gap,
    run_flywheel_once,
)
from atm_core.capability_registry_v3 import public_registry as public_capability_registry
from atm_core.durable_doctor import DurableDoctor
from atm_core.execution_router import UniversalExecutionRouter, legacy_to_universal
from atm_core.funding_gate import install_workprotocol_chain_funding_gate
from atm_core.models import Phase
from atm_core.opportunities import external_state_hash
from atm_core.provider_fabric import public_provider_fabric
from atm_core.provider_registry import public_registry as public_provider_registry
from atm_core.radar_registry import build_canonical_registry
from atm_core.role_runtime import RoleReceiptStore
from atm_core.swarm_runtime import current_pass_swarm_shadow
from atm_core.universal_radar import OperationalState, RadarDisposition, UniversalRadar, load_snapshot, persist_snapshot


STATE_ROOT = Path(__file__).resolve().parents[1] / ".atm"
RADAR_SNAPSHOT = STATE_ROOT / "universal-radar.json"
CAPABILITY_GAP_LEDGER = STATE_ROOT / "capability-gaps.sqlite3"
EPISODES_STATE = STATE_ROOT / "economic-episodes.sqlite3"
FLYWHEEL_STATE = STATE_ROOT / "capability-flywheel.sqlite3"
REGISTRY_V3_STATE = STATE_ROOT / "capability-registry-v3.sqlite3"
PROVIDER_STATE = STATE_ROOT / "free-provider-fabric.sqlite3"
DOCTOR_STATE = STATE_ROOT / "doctor-state.sqlite3"
ROLE_RUNTIME_STATE = STATE_ROOT / "role-runtime.sqlite3"

# Extend the existing one canonical durable cloud state; no second supervisor/effect ledger.
for name in (
    "worker-fabric.sqlite3", "execution-jobs.sqlite3", "economic-episodes.sqlite3", "immune-memory.sqlite3",
    "vnext-learning.sqlite3", "universal-radar.json", "universal-effects.sqlite3", "capability-gaps.sqlite3",
    "capability-flywheel.sqlite3", "capability-registry-v3.sqlite3", "free-provider-fabric.sqlite3",
    "doctor-state.sqlite3", "role-runtime.sqlite3",
):
    atm_cloud.STATE_ALLOWLIST.add(name)
install_workprotocol_chain_funding_gate()


_original_phase_verify_v2 = core_runtime.phase_verify_v2


def _record_gap(opp, normalized, availability) -> tuple[bool, str | None, str | None]:
    """Persist authoritative missed demand, link an Episode, enqueue Forge; never money truth."""
    try:
        funding_verified = str(normalized.funding_status.value).upper() == "VERIFIED"
        current = bool(normalized.external_object_exists and normalized.submission_window_open)
        gap = CapabilityGap(
            canonical_opportunity_id=str(opp.canonical_opportunity_id),
            source=str(normalized.source), external_id=str(normalized.external_id),
            observed_at=normalized.source_checked_at.isoformat(), fresh_object_hash=str(normalized.source_state_hash),
            work_class_candidate=str(normalized.executor_class.value),
            requirements=tuple(str(value) for value in normalized.required_capabilities),
            net_reward_if_verified=str(normalized.payout_net),
            funding_verification_state="VERIFIED" if funding_verified else "UNVERIFIED",
            competition_if_known=int(normalized.competition),
            rejection_reason="WORK_CLASS_NOT_FIXTURE_QUALIFIED" if availability.reason in {"UNSUPPORTED_CAPABILITY", "NO_MATERIAL_EXECUTION_PATH"} else str(availability.reason),
            capability_gap_reason=str(availability.reason),
            evidence_refs=(str(normalized.canonical_url), f"source_state_hash:{normalized.source_state_hash}"),
            currentness_state="CURRENT_AUTHORITATIVE_PREFLIGHT" if current else "NOT_CURRENT",
            demand_class=classify_gap(synthetic=False, current=current, funding_verified=funding_verified),
            synthetic=False,
        )
        ledger = CapabilityGapLedger(CAPABILITY_GAP_LEDGER)
        try:
            inserted = ledger.record(gap)
        finally:
            ledger.close()
        _, episode_id = enqueue_gap(gap, flywheel_path=FLYWHEEL_STATE, episodes_path=EPISODES_STATE)
        return inserted, gap.fingerprint(), episode_id
    except Exception:
        return False, None, None


def _capability_first_phase_verify(config, state, adapters):
    _original_phase_verify_v2(config, state, adapters)
    opp = state.active_opportunity
    if not opp or state.phase != Phase.CLAIM:
        return
    adapter = v1.adapter_for(adapters, opp)
    snapshot = adapter.fetch_authoritative(opp)
    if external_state_hash(snapshot) != str(opp.external_state_hash or ""):
        state.phase = Phase.VERIFY
        state.last_result = {"status": "EXECUTOR_PREFLIGHT_RETRY_EXTERNAL_CHANGED"}
        return
    normalized = legacy_to_universal(opp, snapshot, config)
    allowed, availability = UniversalExecutionRouter().claim_allowed(normalized)
    opp.executor_class = normalized.executor_class.value
    opp.executor_availability = availability.model_dump(mode="json")
    opp.universal_source_state_hash = normalized.source_state_hash
    if not allowed:
        inserted, fingerprint, episode_id = _record_gap(opp, normalized, availability)
        state.last_result = {
            "status": "DO_NOT_CLAIM_EXECUTOR_UNAVAILABLE",
            "opportunity_id": opp.canonical_opportunity_id,
            "executor_class": normalized.executor_class.value,
            "executor_reason": availability.reason,
            "capability_gap_recorded": bool(inserted),
            "capability_gap_fingerprint": fingerprint,
            "capability_gap_episode_id": episode_id,
            "outgoing_spend_usd": "0",
        }
        state.active_opportunity = None
        state.phase = Phase.DISCOVER
        return
    state.active_opportunity = opp
    state.last_result = {
        **(state.last_result or {}),
        "executor_class": normalized.executor_class.value,
        "executor_preclaim_proof": availability.model_dump(mode="json"),
        "outgoing_spend_usd": "0",
    }


core_runtime.phase_verify_v2 = _capability_first_phase_verify
atm_fabric.install()


def _run_flywheel_runtime() -> dict:
    try:
        ensure_synthetic_plumbing_if_no_real_gap(
            ledger_path=CAPABILITY_GAP_LEDGER, flywheel_path=FLYWHEEL_STATE, episodes_path=EPISODES_STATE,
        )
        return run_flywheel_once(flywheel_path=FLYWHEEL_STATE, registry_path=REGISTRY_V3_STATE)
    except Exception as exc:
        return {"schema": "ATM_CAPABILITY_FLYWHEEL_RUNTIME_V1", "state": "FAIL_CLOSED", "error_class": type(exc).__name__, "economic_authority": False}


def _universal_swarm_shadow(config, adapters, board):
    install_money_board_history_guard(board)
    result = current_pass_swarm_shadow(config, adapters, board)
    result["capability_flywheel"] = _run_flywheel_runtime()
    try:
        floor = Decimal(str(config.get("min_reward_usd", 5)))
    except Exception:
        floor = Decimal("5")
    try:
        snapshot = UniversalRadar(build_canonical_registry(), floor_usd=max(Decimal("5"), floor), per_source_timeout=10).scan()
        persist_snapshot(snapshot, RADAR_SNAPSHOT)
        for item in snapshot.opportunities:
            if item.operational_state != OperationalState.EXECUTABLE:
                continue
            payload = {
                "canonical_opportunity_id": item.canonical_id, "source": item.source,
                "authoritative_url": item.canonical_url, "upstream_status": "OPEN",
                "created_at": item.created_at.isoformat() if item.created_at else None,
                "updated_at": item.updated_at.isoformat() if item.updated_at else None,
                "deadline": item.deadline.isoformat() if item.deadline else None,
                "reward_gross": str(item.payout_gross), "expected_fees": str(item.fees),
                "funding_proof": item.funding_proof, "competition": item.competition, "claims": item.claims,
                "open_prs": item.open_prs, "ai_policy": item.agent_policy.value, "eligibility": item.eligibility,
                "claim_method": item.claim_method, "submission_method": item.submission_method,
                "payment_method": item.payment_rail, "payout_latency": item.payout_latency,
                "payment_proof_method": "authoritative-source-specific", "external_state_hash": item.source_state_hash,
                "executor_class": item.executor_class.value, "radar_disposition": item.disposition.value,
                "operational_state": item.operational_state.value, "withdrawal_path": item.withdrawal_path,
                "scout_observed_at": snapshot.generated_at.isoformat(),
            }
            board.upsert_candidate(
                payload, scout=f"universal:{item.source}", base_score=float(item.money_velocity_score),
                confidence=0.9 if item.funding_status.value == "VERIFIED" else 0.55,
                negative_evidence=item.rejection_reasons, status="NORMALIZED",
            )
        result["universal_radar"] = {
            "state": "ACTIVE", "generated_at": snapshot.generated_at.isoformat(),
            "sources": len(snapshot.platform_health), "opportunities": len(snapshot.opportunities),
            "attack_now": sum(1 for item in snapshot.opportunities if item.disposition == RadarDisposition.ATTACK_NOW),
            "errors": len(snapshot.source_errors),
        }
    except Exception as exc:
        result["universal_radar"] = {"state": "DEGRADED", "error": type(exc).__name__}
    return result


atm_cloud.swarm_shadow = _universal_swarm_shadow
_original_build_status = atm_cloud.build_status
_original_sanitize_status = atm_cloud.sanitize_status


def _public_radar_item(item):
    return {
        "id": item.canonical_id, "source": item.source, "url": item.canonical_url, "title": item.title[:180],
        "category": item.category, "payout_usd": str(item.payout_usd) if item.payout_usd is not None else None,
        "payout_net": str(item.payout_net), "funding_status": item.funding_status.value,
        "competition": item.competition, "agent_policy": item.agent_policy.value, "executor": item.executor_class.value,
        "disposition": item.disposition.value, "operational_state": item.operational_state.value,
        "money_velocity": str(item.money_velocity_score), "human_gate": bool(item.human_gate),
        "rejection_reasons": item.rejection_reasons[:8],
    }


def _gap_status() -> dict:
    if not CAPABILITY_GAP_LEDGER.exists():
        return {"schema": "ATM_CAPABILITY_GAP_LEDGER_V1", "total": 0, "verified_missed_cash_signals": 0, "synthetic": 0, "earnings_authority": False, "priority": []}
    ledger = CapabilityGapLedger(CAPABILITY_GAP_LEDGER)
    try:
        status = ledger.stats()
        status["priority"] = [{
            "canonical_id": row.get("canonical_id"), "source": row.get("source"), "work_class": row.get("work_class"),
            "funding_state": row.get("funding_state"), "rejection_reason": row.get("rejection_reason"),
            "demand_class": row.get("demand_class"), "synthetic": bool(row.get("synthetic")), "priority": ledger.priority(row),
        } for row in ledger.prioritized(limit=8)]
        return status
    finally:
        ledger.close()


def _role_runtime_status() -> dict:
    if not ROLE_RUNTIME_STATE.exists():
        return {"schema": "ATM_ISOLATED_ROLE_RUNTIME_V1", "process_boundary_proven": False, "secretless_env_proven": False, "tool_network_policy_proven": False, "proven_roles": []}
    store = RoleReceiptStore(ROLE_RUNTIME_STATE)
    try:
        return store.summary()
    finally:
        store.close()


def _doctor_status() -> dict:
    if not DOCTOR_STATE.exists():
        return {"schema": "ATM_DURABLE_DOCTOR_V1", "durable": True, "typed_handlers_only": True, "state": "UNOBSERVED"}
    doctor = DurableDoctor(DOCTOR_STATE)
    try:
        return doctor.summary()
    finally:
        doctor.close()


def _fabric_build_status(core, state, ledger, targets, board, lease, control):
    status = _original_build_status(core, state, ledger, targets, board, lease, control)
    role_runtime = _role_runtime_status()
    status["capability_registry"] = public_capability_registry(store_path=REGISTRY_V3_STATE)
    status["provider_registry"] = public_provider_registry()
    status["free_provider_fabric"] = public_provider_fabric(PROVIDER_STATE)
    status["capability_gap_ledger"] = _gap_status()
    status["capability_flywheel"] = public_flywheel_status(FLYWHEEL_STATE)
    status["doctor_runtime"] = _doctor_status()
    supervisor = DeterministicSupervisor()
    status["autonomy_fabric"] = {
        "schema": supervisor.schema,
        "single_controller": supervisor.single_controller,
        "cash_canon_authority": supervisor.cash_canon_authority,
        "effect_authority": supervisor.effect_authority,
        "event_router": "DETERMINISTIC",
        "process_boundary_proven": bool(role_runtime.get("process_boundary_proven")),
        "secretless_env_proven": bool(role_runtime.get("secretless_env_proven")),
        "tool_network_policy_proven": bool(role_runtime.get("tool_network_policy_proven")),
        "proven_roles": role_runtime.get("proven_roles", []),
        "role_receipts": role_runtime.get("latest_receipts", {}),
        "cash_mode": "ACTIVE" if control.get("running") and not control.get("paused") else "INACTIVE",
        "outgoing_spend_usd": "0",
    }
    active = status.get("active_task")
    opp = getattr(state, "active_opportunity", None)
    if isinstance(active, dict) and opp is not None:
        active.update({
            "selector": getattr(opp, "selection_provenance", None),
            "same_pass_rediscovered": bool(getattr(opp, "same_pass_rediscovered", False)),
            "executor_class": getattr(opp, "executor_class", None),
            "executor_availability": getattr(opp, "executor_availability", None),
            "worker_id": getattr(opp, "worker_id", None), "work_lease_id": getattr(opp, "worker_lease_id", None),
            "worker_commit_sha": getattr(opp, "worker_commit_sha", None),
        })
    eligible = []
    for row in board.top(limit=5, eligible_only=True):
        eligible.append({
            "canonical_id": row.get("canonical_id"), "source": row.get("source"), "status": row.get("status"),
            "falsifier_verdict": row.get("falsifier_verdict"), "verified_at": row.get("verified_at"),
            "scout_sources_json": row.get("scout_sources_json"),
        })
    status.setdefault("money_board", {})["eligible_provenance"] = eligible

    radar = load_snapshot(RADAR_SNAPSHOT)
    if radar:
        executable = [item for item in radar.opportunities if item.operational_state == OperationalState.EXECUTABLE]
        status["radar"] = {
            "schema": radar.schema, "generated_at": radar.generated_at.isoformat(),
            "opportunity_count": len(executable), "attack_now_count": len(executable),
            "opportunities": [_public_radar_item(item) for item in executable[:80]],
            "rejection_counts": radar.rejection_counts, "canonical_duplicate_count": radar.canonical_duplicate_count,
        }
        status["platform_health"] = [row.model_dump(mode="json") for row in radar.platform_health]
    return status


def _fabric_sanitize_status(status):
    sanitized = _original_sanitize_status(status)
    if isinstance(status.get("radar"), dict):
        sanitized["radar"] = status["radar"]
    if isinstance(status.get("platform_health"), list):
        sanitized["platform_health"] = status["platform_health"][:32]
    for key in (
        "capability_registry", "provider_registry", "free_provider_fabric", "capability_gap_ledger",
        "capability_flywheel", "doctor_runtime", "autonomy_fabric",
    ):
        value = status.get(key)
        if isinstance(value, dict):
            sanitized[key] = value
    return sanitized


atm_cloud.build_status = _fabric_build_status
atm_cloud.sanitize_status = _fabric_sanitize_status


def main() -> int:
    status = atm_cloud.run_cloud_cycle()
    print(json.dumps(atm_cloud.sanitize_status(status), sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
