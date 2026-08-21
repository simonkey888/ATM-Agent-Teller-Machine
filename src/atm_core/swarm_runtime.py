from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from atm_swarm import MoneyBoard, ResourceGovernor

from .action_history import (
    canonical_non_actionable_ids,
    install_money_board_history_guard,
    record_duplicate_kill_without_reopening,
)
from .autonomy_fabric import DeterministicSupervisor, EventKind
from .durable_doctor import DurableDoctor
from .models import Opportunity
from .role_runtime import IsolatedRoleRunner


ROOT = Path(__file__).resolve().parents[2]
STATE_ROOT = ROOT / ".atm"
ROLE_RECEIPTS = STATE_ROOT / "role-runtime.sqlite3"
DOCTOR_STATE = STATE_ROOT / "doctor-state.sqlite3"
EPISODES_STATE = STATE_ROOT / "economic-episodes.sqlite3"
RUNTIME_STATE = STATE_ROOT / "state.json"


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


def _doctor_available_sources(discovery_order: list[str]) -> list[str]:
    doctor = DurableDoctor(DOCTOR_STATE)
    try:
        return [source for source in discovery_order if doctor.available(f"source:{source}")]
    finally:
        doctor.close()


def _doctor_handle(runner: IsolatedRoleRunner, fault: str, key: str) -> dict[str, Any]:
    return runner.run(
        "DOCTOR", "HANDLE",
        {"state_path": str(DOCTOR_STATE), "fault": fault, "key": key, "threshold": 3, "cooldown_seconds": 300},
        network_policy="DENY", tool_policy="DURABLE_TYPED_RECOVERY",
    )


def current_pass_swarm_shadow(config: dict[str, Any], adapters: dict[str, Any], board: MoneyBoard) -> dict[str, Any]:
    """Isolated read-only Scout/Falsifier/Doctor pass feeding the one canonical MoneyBoard."""
    import atm_v2 as core

    governor = ResourceGovernor()
    governor.validate()
    supervisor = DeterministicSupervisor()
    scout_jobs = supervisor.dispatch(EventKind.DISCOVERY_TICK, {"minimum": str(config.get("min_reward_usd", 1))})
    if len(scout_jobs) != 1:
        raise RuntimeError("DETERMINISTIC_SUPERVISOR_SCOUT_CARDINALITY_VIOLATION")

    install_money_board_history_guard(board)
    history = canonical_non_actionable_ids(board, episodes_path=EPISODES_STATE, state_path=RUNTIME_STATE)
    discovery_order = [name for name in config.get("discovery_order", []) if name in adapters]
    enabled_sources = _doctor_available_sources(discovery_order)
    blocked_sources = sorted(set(discovery_order) - set(enabled_sources))
    runner = IsolatedRoleRunner(ROLE_RECEIPTS, timeout_seconds=110)
    minimum = config.get("min_reward_usd", 1)
    candidates: dict[str, Opportunity] = {}
    errors: dict[str, str] = {source: "DOCTOR_CIRCUIT_OPEN" for source in blocked_sources}
    observations: list[dict[str, Any]] = []
    max_parallel = max(1, min(3, int(governor.max_scouts)))

    def scout_one(source: str) -> dict[str, Any]:
        result = runner.run(
            "SCOUT", "DISCOVER",
            {"config": config, "sources": [source], "minimum": minimum},
            network_policy="READ_ONLY_PUBLIC_HTTPS", tool_policy="READ_ONLY_SOURCE_DISCOVERY",
        )
        rows = result.get("observations") or []
        if len(rows) != 1:
            raise RuntimeError("ISOLATED_SCOUT_CARDINALITY_VIOLATION")
        return dict(rows[0])

    with ThreadPoolExecutor(max_workers=max_parallel) as pool:
        futures = {pool.submit(scout_one, source): source for source in enabled_sources}
        for future in as_completed(futures):
            source = futures[future]
            try:
                observations.append(future.result())
            except Exception as exc:
                observations.append({"source": source, "state": "DEGRADED", "error_class": type(exc).__name__, "candidates": []})
    observations.sort(key=lambda row: str(row.get("source") or ""))

    for observation in observations:
        source = str(observation.get("source") or "unknown")
        if observation.get("state") != "OK":
            error_class = str(observation.get("error_class") or "SOURCE_FAILURE")
            errors[source] = error_class
            _doctor_handle(runner, "SOURCE_TIMEOUT" if "TIMEOUT" in error_class.upper() else "WORKER_CRASH", f"source:{source}")
            continue
        for raw in observation.get("candidates") or []:
            opp = Opportunity.model_validate(raw)
            payload = opp.model_dump(mode="json")
            payload["scout_observed_at"] = datetime.now(timezone.utc).isoformat()
            candidates[opp.canonical_opportunity_id] = opp
            board.upsert_candidate(
                payload,
                scout=source,
                base_score=float(core.money_velocity(opp)),
                confidence=0.8,
                negative_evidence=_negative_evidence(opp),
                status="NORMALIZED",
            )

    board.expire_stale()
    history = canonical_non_actionable_ids(board, episodes_path=EPISODES_STATE, state_path=RUNTIME_STATE)
    duplicate_ids = [cid for cid in candidates if cid in history]
    for cid in duplicate_ids:
        record_duplicate_kill_without_reopening(board, cid)

    current_rows = []
    for canonical_id in candidates:
        if canonical_id in history:
            continue
        row = board.get(canonical_id)
        if row and str(row.get("status") or "") not in {"PAID", "REJECTED", "STALE", "LOST", "SUBMITTED", "MONITORING", "PAYMENT_VERIFY"}:
            current_rows.append(row)
    current_rows.sort(key=lambda row: (float(row.get("signal") or 0), str(row.get("touched_at") or "")), reverse=True)

    falsifier_summary: dict[str, Any]
    if duplicate_ids:
        duplicate_id = sorted(duplicate_ids)[0]
        duplicate_result = runner.run(
            "FALSIFIER", "VERIFY",
            {"config": config, "opportunity": candidates[duplicate_id].model_dump(mode="json"), "submitted_ids": sorted(history)},
            network_policy="READ_ONLY_PUBLIC_HTTPS", tool_policy="AUTHORITATIVE_READ_VERIFY",
        )
        falsifier_summary = {
            "state": duplicate_result.get("verdict"), "canonical_id": duplicate_id,
            "reason": duplicate_result.get("reason"), "fresh_object_hash": duplicate_result.get("fresh_object_hash"),
            "history_guard": True,
        }
    elif current_rows:
        canonical_id = str(current_rows[0]["canonical_id"])
        jobs = supervisor.dispatch(EventKind.NEW_CANDIDATE, {"canonical_id": canonical_id})
        if len(jobs) != 1:
            raise RuntimeError("DETERMINISTIC_SUPERVISOR_FALSIFIER_CARDINALITY_VIOLATION")
        decision = runner.run(
            "FALSIFIER", "VERIFY",
            {"config": config, "opportunity": candidates[canonical_id].model_dump(mode="json"), "submitted_ids": sorted(history)},
            network_policy="READ_ONLY_PUBLIC_HTTPS", tool_policy="AUTHORITATIVE_READ_VERIFY",
        )
        verdict = str(decision.get("verdict") or "KILL")
        reason = decision.get("reason")
        board.mark_falsifier(canonical_id, verdict, reason=reason, confidence=0.9)
        falsifier_summary = {
            "state": verdict, "canonical_id": canonical_id, "reason": reason,
            "verified_at": datetime.now(timezone.utc).isoformat(), "fresh_object_hash": decision.get("fresh_object_hash"),
            "history_guard": True,
        }
        if verdict != "CONFIRM":
            fault = "SOURCE_LIED" if reason not in {"STALE_OR_CLOSED", "UNFUNDED", "ALREADY_SUBMITTED_OR_IN_FLIGHT"} else "STALE_STATE"
            _doctor_handle(runner, fault, f"source:{candidates[canonical_id].source}")
    else:
        falsifier_summary = {"state": "NO_CURRENT_CANDIDATE", "history_guard": True}

    doctor = DurableDoctor(DOCTOR_STATE)
    try:
        doctor_summary = doctor.summary()
    finally:
        doctor.close()
    stats = board.stats()
    stats["errors"] = errors
    stats["scout_fabric"] = {
        "observations": len(observations),
        "successful_sources": sum(1 for row in observations if row.get("state") == "OK"),
        "degraded_sources": sum(1 for row in observations if row.get("state") != "OK") + len(blocked_sources),
        "configured_sources_scanned": len(discovery_order),
        "bounded_concurrency": max_parallel,
        "process_boundary": "RECEIPT_REQUIRED",
        "secret_env": "EMPTY_BY_CONSTRUCTION",
        "network_policy": "READ_ONLY_PUBLIC_HTTPS",
        "external_mutation_authority": False,
    }
    stats["falsifier"] = falsifier_summary
    stats["doctor"] = doctor_summary
    stats["history_guard"] = {"non_actionable_ids": len(history), "rediscovered_duplicates_killed": len(duplicate_ids)}
    return {"stats": stats, "live_candidates": candidates}
