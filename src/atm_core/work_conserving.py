from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterator

from .models import Phase, RuntimeState
from .payments import PaymentNotFinal

MUTATION_SECRETS = (
    "TASKBOUNTY_API_KEY",
    "MOLTJOBS_API_KEY",
    "SUPERTEAM_AGENT_API_KEY",
    "AGENTHANSA_API_KEY",
    "TASKMARKET_KEYSTORE_B64",
    "WORKPROTOCOL_API_KEY",
    "WORKPROTOCOL_AGENT_ID",
)

UNTRUSTED_LANES = {"SCOUTS", "FALSIFIERS", "MAKERS", "CHECKERS"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@contextmanager
def isolated_lane_environment(lane: str) -> Iterator[None]:
    """Remove marketplace mutation credentials from untrusted functional lanes.

    The supervisor remains the sole economic authority. Discovery, falsification,
    making, and checking run without access to claim/submission credentials. The
    environment is restored before CLAIM/SUBMIT/WATCHER work resumes.
    """

    if lane not in UNTRUSTED_LANES:
        yield
        return
    saved = {name: os.environ[name] for name in MUTATION_SECRETS if name in os.environ}
    try:
        for name in MUTATION_SECRETS:
            os.environ.pop(name, None)
        yield
    finally:
        for name, value in saved.items():
            os.environ[name] = value


def detach_external_wait(state: RuntimeState, prior_phase: Phase) -> bool:
    """Convert residual SUBMIT->MONITOR waits into durable nonblocking in_flight work."""

    if prior_phase != Phase.SUBMIT or state.phase != Phase.MONITOR or state.active_opportunity is None:
        return False
    opp = state.active_opportunity
    monitored = opp.model_copy(
        update={
            "inflight_stage": "SUBMITTED",
            "submitted_at": utcnow(),
            "inflight_terminal": False,
            "last_monitor_error": None,
        }
    )
    key = monitored.submission_id or monitored.canonical_opportunity_id
    existing = {
        (row.submission_id or row.canonical_opportunity_id)
        for row in state.in_flight
    }
    if key not in existing:
        state.in_flight.append(monitored)
    state.active_opportunity = None
    state.phase = Phase.DISCOVER
    state.last_result = {
        "status": "SUBMITTED_NONBLOCKING",
        "submission_id": monitored.submission_id,
        "opportunity_id": monitored.canonical_opportunity_id,
    }
    return True


def watch_generic_in_flight(
    config: dict[str, Any],
    state: RuntimeState,
    adapters: dict[str, Any],
    ledger: Any,
    adapter_for: Any,
    *,
    limit: int = 8,
) -> int:
    """Watch non-TaskMarket external waits without consuming maker capacity.

    TaskMarket has a stronger source-specific watcher in atm_v2.monitor_in_flight;
    this watcher handles the remaining adapters conservatively. Only authoritative
    payment proofs can make an item terminal-withdrawable.
    """

    recipient = str(config.get("payment_recipient_public_identifier") or "").strip()
    if not recipient:
        return 0
    changed = 0
    updated = []
    for index, opp in enumerate(state.in_flight):
        if index >= limit or getattr(opp, "inflight_terminal", False) or opp.source == "taskmarket":
            updated.append(opp)
            continue
        stage = str(getattr(opp, "inflight_stage", "SUBMITTED") or "SUBMITTED").upper()
        changes: dict[str, Any] = {"last_checked_at": utcnow(), "last_monitor_error": None}
        try:
            adapter = adapter_for(adapters, opp)
            snapshot = adapter.monitor(opp)
            root = snapshot.get("job") or snapshot.get("task") or snapshot if isinstance(snapshot, dict) else {}
            status = str(root.get("status") or root.get("state") or "").strip().lower() if isinstance(root, dict) else ""
            if status in {"rejected", "expired", "cancelled", "canceled", "failed"}:
                changes.update({"inflight_stage": status.upper(), "inflight_terminal": True})
            else:
                if status in {"accepted", "completed", "verified", "approved", "released", "paid", "resolved"}:
                    stage = "ACCEPTED"
                try:
                    proofs = adapter.fetch_payment(opp, recipient)
                    appended = 0
                    total = Decimal("0")
                    txids: list[str] = []
                    for proof in proofs:
                        if ledger.append(proof):
                            appended += 1
                        total += proof.amount
                        txids.append(proof.payout_id_or_txid)
                    if proofs:
                        changes.update(
                            {
                                "inflight_stage": "WITHDRAWABLE",
                                "inflight_terminal": True,
                                "settled_usd": str(total),
                                "settlement_tx_hashes": txids,
                                "new_payment_proofs": appended,
                            }
                        )
                    else:
                        changes["inflight_stage"] = stage
                except PaymentNotFinal:
                    changes["inflight_stage"] = stage
            changed += 1
        except Exception as exc:  # source-scoped degradation; never global stop
            changes["last_monitor_error"] = type(exc).__name__
        updated.append(opp.model_copy(update=changes))
    state.in_flight = updated
    return changed


def next_delay_seconds(config: dict[str, Any], state: RuntimeState) -> int:
    """Work-conserving cadence with a hard anti-busy-loop floor."""

    loop = config.get("loop", {}) if isinstance(config.get("loop"), dict) else {}
    phase_delays = loop.get("phase_delay_seconds", {}) if isinstance(loop.get("phase_delay_seconds"), dict) else {}
    configured = int(phase_delays.get(state.phase.value, 10))
    floor = max(1, int(loop.get("work_conserving_min_sleep_seconds", 1)))
    active_cap = max(floor, int(loop.get("active_work_poll_seconds", 1)))
    watcher_cap = max(floor, int(loop.get("watcher_poll_seconds", 15)))
    idle_scan_cap = max(floor, int(loop.get("idle_discovery_poll_seconds", 30)))

    if state.active_opportunity is not None or state.phase in {Phase.VERIFY, Phase.CLAIM, Phase.WORK, Phase.CHECK, Phase.SUBMIT}:
        return min(max(floor, configured), active_cap)
    if any(not bool(getattr(row, "inflight_terminal", False)) for row in state.in_flight):
        return min(max(floor, configured), watcher_cap)
    if state.phase == Phase.DISCOVER:
        return min(max(floor, configured), idle_scan_cap)
    return min(max(floor, configured), watcher_cap)
