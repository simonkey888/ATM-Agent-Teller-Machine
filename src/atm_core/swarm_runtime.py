from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from atm_swarm import MoneyBoard, ResourceGovernor


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
    """Scout/falsify current discoveries without letting durable orphan rows starve verification.

    The Money Board remains durable, but a row from an earlier pass is never chosen for
    this pass's falsifier unless the authoritative source rediscovered that canonical id.
    This mirrors the supervisor's same-pass admission invariant.
    """
    import atm_v2 as core

    governor = ResourceGovernor()
    governor.validate()
    enabled = [(name, adapters[name]) for name in config.get("discovery_order", []) if name in adapters][
        : governor.max_scouts
    ]
    minimum = config.get("min_reward_usd", 1)
    candidates: dict[str, Any] = {}
    errors: dict[str, str] = {}

    def scout(name: str, adapter: Any) -> tuple[str, list[Any]]:
        return name, list(adapter.discover(minimum))

    with ThreadPoolExecutor(max_workers=max(2, min(governor.max_scouts, len(enabled) or 2))) as pool:
        futures = [pool.submit(scout, name, adapter) for name, adapter in enabled]
        for future in as_completed(futures):
            try:
                name, found = future.result()
                for opp in found:
                    payload = opp.model_dump(mode="json")
                    candidates[opp.canonical_opportunity_id] = opp
                    board.upsert_candidate(
                        payload,
                        scout=name,
                        base_score=float(core.money_velocity(opp)),
                        confidence=0.8,
                        negative_evidence=_negative_evidence(opp),
                        status="NORMALIZED",
                    )
            except Exception as exc:
                errors[str(len(errors))] = str(exc)[-500:]

    board.expire_stale()

    # Critical invariant: durable rows not rediscovered in this authoritative pass
    # cannot consume the falsifier slot. Rank only board rows corresponding to the
    # current pass's live candidate set.
    current_rows = []
    for canonical_id in candidates:
        row = board.get(canonical_id)
        if row and str(row.get("status") or "") not in {"PAID", "REJECTED", "STALE", "LOST"}:
            current_rows.append(row)
    current_rows.sort(key=lambda row: (float(row.get("signal") or 0), str(row.get("touched_at") or "")), reverse=True)

    if current_rows:
        canonical_id = str(current_rows[0]["canonical_id"])
        opp = candidates[canonical_id]
        adapter = adapters.get(str(getattr(opp, "source", "")))
        if adapter is not None:
            try:
                snapshot = adapter.fetch_authoritative(opp)
                adapter.verify_freshness(opp, snapshot)
                adapter.verify_funding(opp, snapshot)
                adapter.verify_eligibility(opp, snapshot)
                board.mark_falsifier(canonical_id, "CONFIRM", confidence=0.9)
            except Exception as exc:
                board.mark_falsifier(canonical_id, "KILL", reason=type(exc).__name__, confidence=0.9)

    stats = board.stats()
    stats["errors"] = errors
    return {"stats": stats, "live_candidates": candidates}
