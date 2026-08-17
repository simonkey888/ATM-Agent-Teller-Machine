from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import atm as v1
from atm_core.models import HumanGate, Phase, RuntimeState
from atm_core.money_velocity import MonthlyTargetStore, choose_money_velocity, money_velocity, monthly_metrics
from atm_core.opportunities import (
    HumanGateRequired,
    OpportunityValidationError,
    external_state_hash,
    local_atm_value,
)
from atm_core.payments import PaymentLedger, PaymentNotFinal, PaymentValidationError
from atm_core.runtime import ProcessLock, SingletonLockError
from atm_core.security import redact_text
from atm_core.state import StateStore
from atm_core.workprotocol_v2 import upgrade_workprotocol_adapter

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".atm"
STATE_FILE = STATE_DIR / "state.json"
PAYMENT_LEDGER_FILE = STATE_DIR / "validated-payment-proofs.jsonl"
TARGET_FILE = STATE_DIR / "monthly-targets.json"
GATES_FILE = STATE_DIR / "human-gates.jsonl"
DEGRADED_FILE = STATE_DIR / "degraded-opportunities.json"
DISCOVERY_CACHE = STATE_DIR / "discovery-cache.json"
MONEY_BOARD_FILE = STATE_DIR / "money-board.sqlite3"
PAUSE_FILE = STATE_DIR / "paused"
LOCK_FILE = STATE_DIR / "atm.lock"
MONTH1_FLOOR = Decimal("500")
HARD_DEFAULT_HOURS = Decimal("3")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def build_adapters(config: dict[str, Any]) -> dict[str, Any]:
    return upgrade_workprotocol_adapter(v1.build_adapters(config), config)


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{uuid.uuid4().hex}")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _route_config_hash(config: dict[str, Any]) -> str:
    safe = {
        "payment_recipient_public_identifier": str(config.get("payment_recipient_public_identifier") or ""),
        "provider": config.get("provider", {}),
        "opportunity_adapters": config.get("opportunity_adapters", {}),
        "discovery_order": config.get("discovery_order", []),
    }
    raw = json.dumps(safe, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _load_degraded(config: dict[str, Any]) -> dict[str, Any]:
    if not DEGRADED_FILE.exists():
        return {}
    try:
        raw = json.loads(DEGRADED_FILE.read_text(encoding="utf-8"))
        raw = raw if isinstance(raw, dict) else {}
    except Exception:
        raw = {}
    now = utcnow()
    current_hash = _route_config_hash(config)
    live: dict[str, Any] = {}
    for opportunity_id, record in raw.items():
        try:
            until = datetime.fromisoformat(str(record.get("retry_after")).replace("Z", "+00:00"))
        except Exception:
            continue
        if str(record.get("config_hash") or "") != current_hash:
            continue
        if until <= now:
            continue
        live[opportunity_id] = record
    if live != raw:
        _atomic_json(DEGRADED_FILE, live)
    return live


def _degrade_opportunity(opportunity_id: str | None, kind: str, config: dict[str, Any]) -> None:
    if not opportunity_id:
        return
    current = _load_degraded(config)
    seconds = 900 if kind == "PROVIDER_ROUTE_UNAVAILABLE" else 21600
    current[opportunity_id] = {
        "kind": kind,
        "retry_after": (utcnow() + timedelta(seconds=seconds)).isoformat(),
        "config_hash": _route_config_hash(config),
    }
    _atomic_json(DEGRADED_FILE, current)


def append_local_gate(gate: HumanGate, state: RuntimeState) -> None:
    GATES_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "at": utcnow().isoformat(),
        "gate": gate.model_dump(mode="json"),
        "opportunity": state.active_opportunity.model_dump(mode="json") if state.active_opportunity else None,
        "resume_phase": gate.resume_phase.value,
        "resolved": False,
    }
    with GATES_FILE.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def localize_gate(gate: HumanGate, state: RuntimeState, config: dict[str, Any]) -> None:
    opportunity_id = gate.opportunity_id or (
        state.active_opportunity.canonical_opportunity_id if state.active_opportunity else None
    )
    append_local_gate(gate, state)
    _degrade_opportunity(opportunity_id, gate.kind, config)
    state.last_result = {
        "status": "HUMAN_GATE_LOCALIZED",
        "kind": gate.kind,
        "opportunity_id": opportunity_id,
    }
    state.human_gate = None
    state.active_opportunity = None
    state.phase = Phase.DISCOVER


def discover_candidates(config: dict[str, Any], adapters: dict[str, Any]) -> list[Any]:
    found = []
    minimum = Decimal(str(config.get("min_reward_usd", 1)))
    degraded = set(_load_degraded(config))
    for name in config.get("discovery_order", list(adapters)):
        adapter = adapters.get(name)
        if not adapter:
            continue
        try:
            found.extend(o for o in adapter.discover(minimum) if o.canonical_opportunity_id not in degraded)
        except Exception as exc:
            v1.log_event("discovery-error", {"adapter": name, "error": str(exc)})
    return found


def refresh_discovery_cache(config: dict[str, Any], adapters: dict[str, Any]) -> None:
    found = discover_candidates(config, adapters)
    ranked = sorted(found, key=lambda o: money_velocity(o), reverse=True)
    _atomic_json(
        DISCOVERY_CACHE,
        {
            "updated_at": utcnow().isoformat(),
            "count": len(ranked),
            "top": [
                {
                    "id": o.canonical_opportunity_id,
                    "source": o.source,
                    "reward_net": str(o.reward_net),
                    "money_velocity": str(money_velocity(o)),
                    "updated_at": o.updated_at.isoformat() if o.updated_at else None,
                }
                for o in ranked[:20]
            ],
        },
    )


def _swarm_eligible_order(path: Path | None = None) -> list[str] | None:
    """Return falsifier-confirmed board order, or None when swarm state is absent.

    A present Money Board is authoritative for candidate admission. If it cannot be
    read, fail closed rather than silently bypassing swarm falsification.
    """
    board_path = path or MONEY_BOARD_FILE
    if not board_path.exists():
        return None
    connection = None
    try:
        connection = sqlite3.connect(f"file:{board_path}?mode=ro", uri=True, timeout=10)
        rows = connection.execute(
            "SELECT canonical_id FROM opportunities "
            "WHERE status='ELIGIBLE' AND falsifier_verdict='CONFIRM' AND verified_at IS NOT NULL "
            "ORDER BY signal DESC, touched_at DESC"
        ).fetchall()
    except sqlite3.Error as exc:
        raise RuntimeError("Money Board eligible-set read failed") from exc
    finally:
        if connection is not None:
            connection.close()
    return [str(row[0]) for row in rows]


def _choose_discovery_candidate(found: list[Any], hard_cap: Decimal) -> tuple[Any | None, str]:
    eligible_order = _swarm_eligible_order()
    if eligible_order is None:
        return choose_money_velocity(found, hard_default_hours=hard_cap), "SEQUENTIAL_FALLBACK_NO_BOARD"

    # Never execute an old board payload directly. Economic authority may only
    # admit a falsifier-confirmed candidate that was rediscovered in this exact
    # authoritative DISCOVER pass and still passes the canonical economic gates.
    fresh_by_id = {o.canonical_opportunity_id: o for o in found}
    for opportunity_id in eligible_order:
        candidate = fresh_by_id.get(opportunity_id)
        if candidate is None:
            continue
        selected = choose_money_velocity([candidate], hard_default_hours=hard_cap)
        if selected is not None:
            return selected, "SWARM_MONEY_BOARD"
    return None, "SWARM_MONEY_BOARD"


def phase_discover(config: dict[str, Any], state: RuntimeState, adapters: dict[str, Any]) -> None:
    found = discover_candidates(config, adapters)
    configured = Decimal(str(config.get("max_task_hours", 3)))
    hard_cap = min(HARD_DEFAULT_HOURS, max(Decimal("0.25"), configured))
    selected, selector = _choose_discovery_candidate(found, hard_cap)
    if selected:
        state.active_opportunity = selected
        state.phase = Phase.VERIFY
        state.last_result = {
            "status": "FOUND",
            "opportunity": selected.model_dump(mode="json"),
            "money_velocity": str(money_velocity(selected)),
            "selector": selector,
        }
    else:
        state.last_result = {"status": "NO_ELIGIBLE_OPPORTUNITY", "count": len(found), "selector": selector}


def phase_verify_v2(config: dict[str, Any], state: RuntimeState, adapters: dict[str, Any]) -> None:
    del config
    opp = state.active_opportunity
    if not opp:
        state.phase = Phase.DISCOVER
        return
    adapter = v1.adapter_for(adapters, opp)
    snapshot = adapter.fetch_authoritative(opp)
    adapter.verify_freshness(opp, snapshot)
    adapter.verify_funding(opp, snapshot)
    adapter.verify_eligibility(opp, snapshot)
    competition = adapter.inspect_competition(opp, snapshot)
    opp.competition = max(competition.values() or [0])
    opp.claims = int(competition.get("claims", opp.claims))
    opp.open_prs = int(competition.get("open_prs", opp.open_prs))

    if opp.source == "workprotocol":
        job = snapshot.get("job") or snapshot
        claims = snapshot.get("claims") or []
        max_workers = int(job.get("maxWorkers") or 0)
        terminal_claim_statuses = {"rejected", "expired", "cancelled", "canceled", "withdrawn", "refunded"}
        active_claims = [
            claim
            for claim in claims
            if str(claim.get("status") or "claimed").lower() not in terminal_claim_statuses
        ]
        our_agent_id = local_atm_value("WORKPROTOCOL_AGENT_ID")
        own_active_claim = bool(
            our_agent_id
            and any(
                str(claim.get("agentId") or claim.get("agent_id") or "") == our_agent_id
                for claim in active_claims
            )
        )
        if max_workers > 0 and len(active_claims) >= max_workers and not own_active_claim:
            raise OpportunityValidationError(
                f"WorkProtocol claim capacity exhausted: active_claims={len(active_claims)} maxWorkers={max_workers}"
            )

    opp.external_state_hash = external_state_hash(snapshot)
    state.active_opportunity = opp
    state.phase = Phase.CLAIM
    state.last_result = {"status": "VERIFIED", "snapshot_hash": opp.external_state_hash}


def status_payload(state: RuntimeState, ledger: PaymentLedger, target_store: MonthlyTargetStore) -> dict[str, Any]:
    proofs = ledger.load()
    target = target_store.target_for(utcnow(), proofs)
    metrics = monthly_metrics(proofs, target, utcnow()).as_dict()
    return {
        "schema": "ATM_STATUS_V2",
        "phase": state.phase.value,
        "cycle": state.cycle,
        "paused": PAUSE_FILE.exists(),
        "active_opportunity": state.active_opportunity.model_dump(mode="json") if state.active_opportunity else None,
        "last_result": state.last_result,
        "last_error": state.last_error,
        "validated_payment_proof_count": len(proofs),
        **metrics,
    }


def run_cycle(config: dict[str, Any], state: RuntimeState, adapters: dict[str, Any], ledger: PaymentLedger) -> None:
    if state.phase == Phase.DISCOVER:
        phase_discover(config, state, adapters)
    elif state.phase == Phase.VERIFY:
        phase_verify_v2(config, state, adapters)
    elif state.phase == Phase.CLAIM:
        v1.phase_claim(config, state, adapters)
    elif state.phase == Phase.WORK:
        v1.phase_work(config, state)
    elif state.phase == Phase.CHECK:
        v1.phase_check(config, state)
    elif state.phase == Phase.SUBMIT:
        v1.phase_submit(config, state, adapters)
    elif state.phase == Phase.MONITOR:
        refresh_discovery_cache(config, adapters)
        v1.phase_monitor(config, state, adapters)
    elif state.phase == Phase.PAYMENT_VERIFY:
        v1.phase_payment_verify(config, state, adapters, ledger)
    else:
        raise RuntimeError(f"unsupported phase: {state.phase}")
    state.cycle += 1


def main() -> int:
    parser = argparse.ArgumentParser(description="ATM V2 always-on money-velocity supervisor")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    config = v1.load_config()
    store = StateStore(STATE_FILE)
    state = store.load(max(MONTH1_FLOOR, Decimal(str(config.get("target_paid_usd", 500)))))
    ledger = PaymentLedger(PAYMENT_LEDGER_FILE)
    month1 = max(MONTH1_FLOOR, Decimal(str(config.get("month1_target_realized_withdrawable_usd", 500))))
    targets = MonthlyTargetStore(TARGET_FILE, month1)
    adapters = build_adapters(config)
    current_target = targets.target_for(utcnow(), ledger.load())
    state.target_paid_usd = current_target
    if state.human_gate:
        localize_gate(state.human_gate, state, config)
        store.save(state)
    if args.status:
        print(json.dumps(status_payload(state, ledger, targets), indent=2, ensure_ascii=False, default=str))
        return 0
    lock = ProcessLock(LOCK_FILE)
    try:
        lock.acquire()
    except SingletonLockError as exc:
        print(f"ATM_SINGLETON_BLOCKED: {exc}", file=v1.sys.stderr)
        return 3
    try:
        while True:
            if PAUSE_FILE.exists():
                if args.once:
                    return 0
                time.sleep(int(config.get("loop", {}).get("pause_poll_seconds", 10)))
                config = v1.load_config()
                continue
            try:
                config = v1.load_config()
                adapters = build_adapters(config)
                run_cycle(config, state, adapters, ledger)
                if state.human_gate:
                    localize_gate(state.human_gate, state, config)
                store.save(state)
                payload = status_payload(state, ledger, targets)
                v1.log_event("cycle-v2", payload)
                if Decimal(payload["MONTHLY_REALIZED_WITHDRAWABLE_USD"]) >= Decimal(payload["MONTHLY_TARGET_USD"]):
                    v1.log_event("monthly-target-reached", payload)
            except HumanGateRequired as exc:
                localize_gate(exc.gate, state, config)
                store.save(state)
                v1.log_event("human-gate-localized", state.last_result)
            except (OpportunityValidationError, PaymentNotFinal, PaymentValidationError) as exc:
                failed_id = state.active_opportunity.canonical_opportunity_id if state.active_opportunity else None
                failed_phase = state.phase
                state.last_error = str(exc)
                if failed_phase == Phase.VERIFY:
                    _degrade_opportunity(failed_id, "VERIFY_ROUTE_INVALID", config)
                    state.active_opportunity = None
                    state.phase = Phase.DISCOVER
                elif failed_phase == Phase.CLAIM:
                    _degrade_opportunity(failed_id, "CLAIM_ROUTE_FAILED", config)
                    state.active_opportunity = None
                    state.phase = Phase.DISCOVER
                elif failed_phase == Phase.PAYMENT_VERIFY:
                    state.phase = Phase.MONITOR
                store.save(state)
                v1.log_event(
                    "validation-v2",
                    {"error": str(exc), "failed_phase": failed_phase.value, "phase": state.phase.value, "opportunity_id": failed_id},
                )
            except KeyboardInterrupt:
                store.save(state)
                return 130
            except Exception as exc:
                state.last_error = str(exc)
                if state.phase in {Phase.WORK, Phase.CHECK} and "all configured providers failed" in str(exc).lower():
                    gate = HumanGate(
                        kind="PROVIDER_ROUTE_UNAVAILABLE",
                        reason=redact_text(str(exc))[-1200:],
                        exact_human_action="No immediate human action required; ATM will continue independent discovery and retry this route after provider recovery/config change.",
                        resume_phase=state.phase,
                        opportunity_id=state.active_opportunity.canonical_opportunity_id if state.active_opportunity else None,
                    )
                    localize_gate(gate, state, config)
                store.save(state)
                v1.log_event("error-v2", {"error": str(exc), "phase": state.phase.value})
                if args.once:
                    raise
                time.sleep(int(config.get("loop", {}).get("failure_backoff_seconds", 30)))
            if args.once:
                return 0
            delay = int(config.get("loop", {}).get("phase_delay_seconds", {}).get(state.phase.value, 10))
            time.sleep(delay)
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
