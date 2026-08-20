from __future__ import annotations

from typing import Any

from atm_swarm import MoneyBoard, ResourceGovernor

from .autonomy_fabric import DeterministicSupervisor, Doctor, EventKind, Falsifier, ScoutFabric


def _negative_evidence(opp: Any) -> list[str]:
    negative: list[str] = []
    status = str(getattr(opp, "upstream_status", "") or "").upper()
    if status not in {"OPEN", "ACTIVE", "AVAILABLE", "FUNDED"}:
        negative.append("CLOSED")
    if not getattr(opp, "funding_proof", None):
        negative.append("UNFUNDED")
    if int(getattr(opp, "competition", 0) or 0) >= 8:
        negative.append("SATURATED")
    if str(getattr(opp, "ai_policy", "UNKNOWN")).upper() in {"FORBIDDEN", "NO_AI"}:
        negative.append("AI_FORBIDDEN")
    if str(getattr(opp, "payment_method", "UNKNOWN")).upper() in {"UNKNOWN", "NONE"}:
        negative.append("PAYOUT_UNAVAILABLE")
    return negative


def current_pass_swarm_shadow(config: dict[str, Any], adapters: dict[str, Any], board: MoneyBoard) -> dict[str, Any]:
    """Event-driven read-only scout/falsifier layer feeding the one canonical MoneyBoard.

    This function has no claim/submission authority. Durable rows from earlier passes cannot
    consume the current pass's falsifier slot unless the authoritative source rediscovered
    that canonical id. Source failures are isolated and exposed; Doctor recommends bounded
    recovery but cannot turn a failed source green or mutate economic truth.
    """
    import atm_v2 as core

    governor = ResourceGovernor()
    governor.validate()
    supervisor = DeterministicSupervisor()
    doctor = Doctor()
    scout_jobs = supervisor.dispatch(EventKind.DISCOVERY_TICK, {"minimum": str(config.get("min_reward_usd", 1))})
    if len(scout_jobs) != 1:
        raise RuntimeError("DETERMINISTIC_SUPERVISOR_SCOUT_CARDINALITY_VIOLATION")

    discovery_order = [name for name in config.get("discovery_order", []) if name in adapters]
    fabric = ScoutFabric(max_concurrent=governor.max_scouts)
    minimum = config.get("min_reward_usd", 1)
    candidates: dict[str, Any] = {}
    errors: dict[str, str] = {}
    observations_list = []
    # Concurrency bounds simultaneous source reads; it must never silently truncate
    # discovery coverage. Scan every configured source in deterministic bounded batches.
    for offset in range(0, len(discovery_order), fabric.max_concurrent):
        batch = discovery_order[offset : offset + fabric.max_concurrent]
        observations_list.extend(fabric.scan(adapters, batch, minimum))
    observations = tuple(observations_list)

    for observation in observations:
        if observation.state != "OK":
            error_class = observation.error_class or "SOURCE_FAILURE"
            errors[observation.source] = error_class
            doctor.record_failure(observation.source, error_class)
            continue
        for opp in observation.candidates:
            payload = opp.model_dump(mode="json")
            payload["scout_observed_at"] = observation.observed_at
            candidates[opp.canonical_opportunity_id] = opp
            board.upsert_candidate(
                payload,
                scout=observation.source,
                base_score=float(core.money_velocity(opp)),
                confidence=0.8,
                negative_evidence=_negative_evidence(opp),
                status="NORMALIZED",
            )

    board.expire_stale()

    current_rows = []
    for canonical_id in candidates:
        row = board.get(canonical_id)
        if row and str(row.get("status") or "") not in {"PAID", "REJECTED", "STALE", "LOST"}:
            current_rows.append(row)
    current_rows.sort(
        key=lambda row: (float(row.get("signal") or 0), str(row.get("touched_at") or "")),
        reverse=True,
    )

    falsifier_summary: dict[str, Any] = {"state": "NO_CURRENT_CANDIDATE"}
    if current_rows:
        canonical_id = str(current_rows[0]["canonical_id"])
        opp = candidates[canonical_id]
        adapter = adapters.get(str(getattr(opp, "source", "")))
        if adapter is not None:
            jobs = supervisor.dispatch(EventKind.NEW_CANDIDATE, {"canonical_id": canonical_id})
            if len(jobs) != 1:
                raise RuntimeError("DETERMINISTIC_SUPERVISOR_FALSIFIER_CARDINALITY_VIOLATION")
            decision = Falsifier().verify(opp, adapter)
            board.mark_falsifier(
                canonical_id,
                decision.verdict,
                reason=decision.reason,
                confidence=0.9,
            )
            falsifier_summary = {
                "state": decision.verdict,
                "canonical_id": canonical_id,
                "reason": decision.reason,
                "verified_at": decision.verified_at,
                "fresh_object_hash": decision.fresh_object_hash,
            }
            if decision.verdict != "CONFIRM":
                doctor.record_failure(str(getattr(opp, "source", "unknown")), decision.reason or "FALSIFIER_KILL")

    stats = board.stats()
    stats["errors"] = errors
    stats["scout_fabric"] = {
        "state": "ACTIVE",
        "observations": len(observations),
        "successful_sources": sum(1 for row in observations if row.state == "OK"),
        "degraded_sources": sum(1 for row in observations if row.state != "OK"),
        "configured_sources_scanned": len(discovery_order),
        "bounded_concurrency": fabric.max_concurrent,
        "external_mutation_authority": False,
    }
    stats["falsifier"] = falsifier_summary
    stats["doctor"] = {
        "state": "ACTIVE",
        "recovery_catalogue": sorted(Doctor.ALLOWED),
        "forbidden_recoveries": sorted(Doctor.FORBIDDEN),
        "can_change_health_truth": False,
        "external_mutation_authority": False,
    }
    return {"stats": stats, "live_candidates": candidates}
