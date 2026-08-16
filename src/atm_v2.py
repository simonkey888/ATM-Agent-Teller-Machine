from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import atm as v1
from atm_core.models import HumanGate, Phase, RuntimeState
from atm_core.money_velocity import MonthlyTargetStore, choose_money_velocity, money_velocity, monthly_metrics
from atm_core.opportunities import HumanGateRequired, OpportunityValidationError
from atm_core.payments import PaymentLedger, PaymentNotFinal, PaymentValidationError
from atm_core.runtime import ProcessLock, SingletonLockError
from atm_core.security import redact_text
from atm_core.state import StateStore

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".atm"
STATE_FILE = STATE_DIR / "state.json"
PAYMENT_LEDGER_FILE = STATE_DIR / "validated-payment-proofs.jsonl"
TARGET_FILE = STATE_DIR / "monthly-targets.json"
GATES_FILE = STATE_DIR / "human-gates.jsonl"
DISCOVERY_CACHE = STATE_DIR / "discovery-cache.json"
PAUSE_FILE = STATE_DIR / "paused"
LOCK_FILE = STATE_DIR / "atm.lock"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{uuid.uuid4().hex}")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, path)


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


def localize_gate(gate: HumanGate, state: RuntimeState) -> None:
    append_local_gate(gate, state)
    state.last_result = {
        "status": "HUMAN_GATE_LOCALIZED",
        "kind": gate.kind,
        "opportunity_id": gate.opportunity_id or (state.active_opportunity.canonical_opportunity_id if state.active_opportunity else None),
    }
    state.human_gate = None
    state.active_opportunity = None
    state.phase = Phase.DISCOVER


def discover_candidates(config: dict[str, Any], adapters: dict[str, Any]) -> list[Any]:
    found = []
    minimum = Decimal(str(config.get("min_reward_usd", 1)))
    for name in config.get("discovery_order", list(adapters)):
        adapter = adapters.get(name)
        if not adapter:
            continue
        try:
            found.extend(adapter.discover(minimum))
        except Exception as exc:
            v1.log_event("discovery-error", {"adapter": name, "error": str(exc)})
    return found


def refresh_discovery_cache(config: dict[str, Any], adapters: dict[str, Any]) -> None:
    found = discover_candidates(config, adapters)
    ranked = sorted(found, key=lambda o: money_velocity(o), reverse=True)
    _atomic_json(DISCOVERY_CACHE, {"updated_at": utcnow().isoformat(), "count": len(ranked), "top": [{"id": o.canonical_opportunity_id, "source": o.source, "reward_net": str(o.reward_net), "money_velocity": str(money_velocity(o)), "updated_at": o.updated_at.isoformat() if o.updated_at else None} for o in ranked[:20]]})


def phase_discover(config: dict[str, Any], state: RuntimeState, adapters: dict[str, Any]) -> None:
    found = discover_candidates(config, adapters)
    selected = choose_money_velocity(found, hard_default_hours=Decimal(str(config.get("max_task_hours", 3))))
    if selected:
        state.active_opportunity = selected
        state.phase = Phase.VERIFY
        state.last_result = {"status": "FOUND", "opportunity": selected.model_dump(mode="json"), "money_velocity": str(money_velocity(selected))}
    else:
        state.last_result = {"status": "NO_ELIGIBLE_OPPORTUNITY", "count": len(found)}


def status_payload(state: RuntimeState, ledger: PaymentLedger, target_store: MonthlyTargetStore) -> dict[str, Any]:
    proofs = ledger.load()
    target = target_store.target_for(utcnow(), proofs)
    metrics = monthly_metrics(proofs, target, utcnow()).as_dict()
    return {"schema": "ATM_STATUS_V2", "phase": state.phase.value, "cycle": state.cycle, "paused": PAUSE_FILE.exists(), "active_opportunity": state.active_opportunity.model_dump(mode="json") if state.active_opportunity else None, "last_result": state.last_result, "last_error": state.last_error, "validated_payment_proof_count": len(proofs), **metrics}


def run_cycle(config: dict[str, Any], state: RuntimeState, adapters: dict[str, Any], ledger: PaymentLedger) -> None:
    if state.phase == Phase.DISCOVER:
        phase_discover(config, state, adapters)
    elif state.phase == Phase.VERIFY:
        v1.phase_verify(config, state, adapters)
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
    state = store.load(config.get("target_paid_usd", 500))
    ledger = PaymentLedger(PAYMENT_LEDGER_FILE)
    targets = MonthlyTargetStore(TARGET_FILE, Decimal(str(config.get("month1_target_realized_withdrawable_usd", 500))))
    adapters = v1.build_adapters(config)
    current_target = targets.target_for(utcnow(), ledger.load())
    state.target_paid_usd = current_target
    if state.human_gate:
        localize_gate(state.human_gate, state)
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
                adapters = v1.build_adapters(config)
                run_cycle(config, state, adapters, ledger)
                if state.human_gate:
                    localize_gate(state.human_gate, state)
                store.save(state)
                payload = status_payload(state, ledger, targets)
                v1.log_event("cycle-v2", payload)
                if Decimal(payload["MONTHLY_REALIZED_WITHDRAWABLE_USD"]) >= Decimal(payload["MONTHLY_TARGET_USD"]):
                    v1.log_event("monthly-target-reached", payload)
            except HumanGateRequired as exc:
                localize_gate(exc.gate, state)
                store.save(state)
                v1.log_event("human-gate-localized", state.last_result)
            except (OpportunityValidationError, PaymentNotFinal, PaymentValidationError) as exc:
                state.last_error = str(exc)
                if state.phase == Phase.VERIFY:
                    state.active_opportunity = None; state.phase = Phase.DISCOVER
                elif state.phase == Phase.PAYMENT_VERIFY:
                    state.phase = Phase.MONITOR
                store.save(state)
                v1.log_event("validation-v2", {"error": str(exc), "phase": state.phase.value})
            except KeyboardInterrupt:
                store.save(state); return 130
            except Exception as exc:
                state.last_error = str(exc)
                if state.phase in {Phase.WORK, Phase.CHECK} and "all configured providers failed" in str(exc).lower():
                    gate = HumanGate(kind="PROVIDER_ROUTE_UNAVAILABLE", reason=redact_text(str(exc))[-1200:], exact_human_action="No immediate human action required; ATM will continue independent discovery and retry this route only after provider recovery/config change.", resume_phase=state.phase, opportunity_id=state.active_opportunity.canonical_opportunity_id if state.active_opportunity else None)
                    localize_gate(gate, state)
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
