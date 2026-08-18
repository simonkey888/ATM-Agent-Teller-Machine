from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import atm_cloud
import atm_fabric
from atm_core.funding_gate import install_workprotocol_chain_funding_gate
from atm_core.radar_sources import build_default_registry
from atm_core.swarm_runtime import current_pass_swarm_shadow
from atm_core.universal_radar import RadarDisposition, UniversalRadar, load_snapshot, persist_snapshot


RADAR_SNAPSHOT = Path(__file__).resolve().parents[1] / ".atm" / "universal-radar.json"

# Extend the existing canonical durable cloud state; do not introduce a second supervisor.
atm_cloud.STATE_ALLOWLIST.add("worker-fabric.sqlite3")
atm_cloud.STATE_ALLOWLIST.add("execution-jobs.sqlite3")
atm_cloud.STATE_ALLOWLIST.add("economic-episodes.sqlite3")
atm_cloud.STATE_ALLOWLIST.add("immune-memory.sqlite3")
atm_cloud.STATE_ALLOWLIST.add("vnext-learning.sqlite3")
atm_cloud.STATE_ALLOWLIST.add("universal-radar.json")
install_workprotocol_chain_funding_gate()
atm_fabric.install()


def _universal_swarm_shadow(config, adapters, board):
    """Preserve the canonical swarm path and add a read-mostly universal radar pass.

    Universal candidates enter the existing MoneyBoard as NORMALIZED evidence. They do
    not gain claim/submission authority merely by being observed. Existing canonical
    adapters remain the only economic mutators until a source-specific gate promotes a
    candidate through the supervisor.
    """
    result = current_pass_swarm_shadow(config, adapters, board)
    try:
        floor = Decimal(str(config.get("min_reward_usd", 5)))
    except Exception:
        floor = Decimal("5")
    try:
        snapshot = UniversalRadar(build_default_registry(), floor_usd=max(Decimal("5"), floor), per_source_timeout=10).scan()
        persist_snapshot(snapshot, RADAR_SNAPSHOT)
        for item in snapshot.opportunities:
            payload = {
                "canonical_opportunity_id": item.canonical_id,
                "source": item.source,
                "authoritative_url": item.canonical_url,
                "upstream_status": "OPEN",
                "created_at": item.created_at.isoformat() if item.created_at else None,
                "updated_at": item.updated_at.isoformat() if item.updated_at else None,
                "deadline": item.deadline.isoformat() if item.deadline else None,
                "reward_gross": str(item.payout_gross),
                "expected_fees": str(item.fees),
                "funding_proof": item.funding_proof,
                "competition": item.competition,
                "claims": item.claims,
                "open_prs": item.open_prs,
                "ai_policy": item.agent_policy.value,
                "eligibility": item.eligibility,
                "claim_method": item.claim_method,
                "submission_method": item.submission_method,
                "payment_method": item.payment_rail,
                "payout_latency": item.payout_latency,
                "payment_proof_method": "authoritative-source-specific",
                "external_state_hash": item.source_state_hash,
                "executor_class": item.executor_class.value,
                "radar_disposition": item.disposition.value,
                "withdrawal_path": item.withdrawal_path,
            }
            board.upsert_candidate(
                payload,
                scout=f"universal:{item.source}",
                base_score=float(item.money_velocity_score),
                confidence=0.9 if item.funding_status.value == "VERIFIED" else 0.55,
                negative_evidence=item.rejection_reasons,
                status="NORMALIZED",
            )
        result["universal_radar"] = {
            "sources": len(snapshot.platform_health),
            "opportunities": len(snapshot.opportunities),
            "attack_now": sum(1 for item in snapshot.opportunities if item.disposition == RadarDisposition.ATTACK_NOW),
            "errors": len(snapshot.source_errors),
        }
    except Exception as exc:
        # Radar failure is a routing/degradation event, never a global ATM stop.
        result["universal_radar"] = {"state": "DEGRADED", "error": type(exc).__name__}
    return result


atm_cloud.swarm_shadow = _universal_swarm_shadow
_original_build_status = atm_cloud.build_status
_original_sanitize_status = atm_cloud.sanitize_status


def _public_radar_item(item):
    return {
        "id": item.canonical_id,
        "source": item.source,
        "url": item.canonical_url,
        "title": item.title[:180],
        "category": item.category,
        "payout_usd": str(item.payout_usd) if item.payout_usd is not None else None,
        "payout_net": str(item.payout_net),
        "funding_status": item.funding_status.value,
        "competition": item.competition,
        "agent_policy": item.agent_policy.value,
        "executor": item.executor_class.value,
        "disposition": item.disposition.value,
        "money_velocity": str(item.money_velocity_score),
        "human_gate": bool(item.human_gate),
        "rejection_reasons": item.rejection_reasons[:8],
    }


def _fabric_build_status(core, state, ledger, targets, board, lease, control):
    status = _original_build_status(core, state, ledger, targets, board, lease, control)
    active = status.get("active_task")
    opp = getattr(state, "active_opportunity", None)
    if isinstance(active, dict) and opp is not None:
        active.update(
            {
                "selector": getattr(opp, "selection_provenance", None),
                "same_pass_rediscovered": bool(getattr(opp, "same_pass_rediscovered", False)),
                "worker_id": getattr(opp, "worker_id", None),
                "work_lease_id": getattr(opp, "worker_lease_id", None),
                "worker_commit_sha": getattr(opp, "worker_commit_sha", None),
            }
        )
    eligible = []
    for row in board.top(limit=5, eligible_only=True):
        eligible.append(
            {
                "canonical_id": row.get("canonical_id"),
                "source": row.get("source"),
                "status": row.get("status"),
                "falsifier_verdict": row.get("falsifier_verdict"),
                "verified_at": row.get("verified_at"),
                "scout_sources_json": row.get("scout_sources_json"),
            }
        )
    status.setdefault("money_board", {})["eligible_provenance"] = eligible

    radar = load_snapshot(RADAR_SNAPSHOT)
    if radar:
        status["radar"] = {
            "schema": radar.schema,
            "generated_at": radar.generated_at.isoformat(),
            "opportunity_count": len(radar.opportunities),
            "attack_now_count": sum(1 for item in radar.opportunities if item.disposition == RadarDisposition.ATTACK_NOW),
            "opportunities": [_public_radar_item(item) for item in radar.opportunities[:80]],
        }
        status["platform_health"] = [row.model_dump(mode="json") for row in radar.platform_health]
    return status


def _fabric_sanitize_status(status):
    sanitized = _original_sanitize_status(status)
    radar = status.get("radar")
    if isinstance(radar, dict):
        sanitized["radar"] = radar
    platform_health = status.get("platform_health")
    if isinstance(platform_health, list):
        sanitized["platform_health"] = platform_health[:32]
    return sanitized


atm_cloud.build_status = _fabric_build_status
atm_cloud.sanitize_status = _fabric_sanitize_status


def main() -> int:
    status = atm_cloud.run_cloud_cycle()
    print(json.dumps(atm_cloud.sanitize_status(status), sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
