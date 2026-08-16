from __future__ import annotations

import argparse
import json
import time
from decimal import Decimal

import atm as v1
import atm_v2 as core
from atm_core.models import HumanGate, Phase
from atm_core.opportunities import HumanGateRequired, OpportunityValidationError
from atm_core.payments import PaymentLedger, PaymentNotFinal, PaymentValidationError
from atm_core.runtime import ProcessLock, SingletonLockError
from atm_core.security import redact_text
from atm_core.state import StateStore


def run_cycle_oci(config, state, adapters, ledger) -> None:
    """PAUSE blocks new economic mutation but preserves safe monitor/payment lanes."""
    paused = core.PAUSE_FILE.exists()
    if paused and state.phase not in {Phase.MONITOR, Phase.PAYMENT_VERIFY}:
        core.refresh_discovery_cache(config, adapters)
        state.last_result = {
            "status": "PAUSED_SAFE_MONITOR_ONLY",
            "held_phase": state.phase.value,
            "active_opportunity_id": state.active_opportunity.canonical_opportunity_id if state.active_opportunity else None,
        }
        state.cycle += 1
        return
    core.run_cycle(config, state, adapters, ledger)


def main() -> int:
    parser = argparse.ArgumentParser(description="ATM OCI always-on supervisor")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    config = v1.load_config()
    store = StateStore(core.STATE_FILE)
    state = store.load(max(core.MONTH1_FLOOR, Decimal(str(config.get("target_paid_usd", 500)))))
    ledger = PaymentLedger(core.PAYMENT_LEDGER_FILE)
    month1 = max(core.MONTH1_FLOOR, Decimal(str(config.get("month1_target_realized_withdrawable_usd", 500))))
    targets = core.MonthlyTargetStore(core.TARGET_FILE, month1)
    adapters = core.build_adapters(config)
    state.target_paid_usd = targets.target_for(core.utcnow(), ledger.load())

    if state.human_gate:
        core.localize_gate(state.human_gate, state, config)
        store.save(state)

    if args.status:
        print(json.dumps(core.status_payload(state, ledger, targets), indent=2, ensure_ascii=False, default=str))
        return 0

    lock = ProcessLock(core.LOCK_FILE)
    try:
        lock.acquire()
    except SingletonLockError as exc:
        print(f"ATM_SINGLETON_BLOCKED: {exc}", file=v1.sys.stderr)
        return 3

    try:
        while True:
            try:
                config = v1.load_config()
                adapters = core.build_adapters(config)
                run_cycle_oci(config, state, adapters, ledger)
                if state.human_gate:
                    core.localize_gate(state.human_gate, state, config)
                store.save(state)
                payload = core.status_payload(state, ledger, targets)
                v1.log_event("cycle-oci", payload)
                if Decimal(payload["MONTHLY_REALIZED_WITHDRAWABLE_USD"]) >= Decimal(payload["MONTHLY_TARGET_USD"]):
                    v1.log_event("monthly-target-reached", payload)
            except HumanGateRequired as exc:
                core.localize_gate(exc.gate, state, config)
                store.save(state)
                v1.log_event("human-gate-localized", state.last_result)
            except (OpportunityValidationError, PaymentNotFinal, PaymentValidationError) as exc:
                failed_id = state.active_opportunity.canonical_opportunity_id if state.active_opportunity else None
                failed_phase = state.phase
                state.last_error = str(exc)
                if failed_phase == Phase.VERIFY:
                    core._degrade_opportunity(failed_id, "VERIFY_ROUTE_INVALID", config)
                    state.active_opportunity = None
                    state.phase = Phase.DISCOVER
                elif failed_phase == Phase.CLAIM:
                    core._degrade_opportunity(failed_id, "CLAIM_ROUTE_FAILED", config)
                    state.active_opportunity = None
                    state.phase = Phase.DISCOVER
                elif failed_phase == Phase.PAYMENT_VERIFY:
                    state.phase = Phase.MONITOR
                store.save(state)
                v1.log_event("validation-oci", {"error": str(exc), "failed_phase": failed_phase.value, "phase": state.phase.value, "opportunity_id": failed_id})
            except KeyboardInterrupt:
                store.save(state)
                return 130
            except Exception as exc:
                state.last_error = str(exc)
                if state.phase in {Phase.WORK, Phase.CHECK} and "all configured providers failed" in str(exc).lower():
                    gate = HumanGate(
                        kind="PROVIDER_ROUTE_UNAVAILABLE",
                        reason=redact_text(str(exc))[-1200:],
                        exact_human_action="No immediate human action required; OCI ATM will continue independent discovery after provider recovery/config change.",
                        resume_phase=state.phase,
                        opportunity_id=state.active_opportunity.canonical_opportunity_id if state.active_opportunity else None,
                    )
                    core.localize_gate(gate, state, config)
                store.save(state)
                v1.log_event("error-oci", {"error": str(exc), "phase": state.phase.value})
                if args.once:
                    raise
                time.sleep(int(config.get("loop", {}).get("failure_backoff_seconds", 30)))

            if args.once:
                return 0
            if core.PAUSE_FILE.exists():
                delay = int(config.get("loop", {}).get("pause_poll_seconds", 10))
            else:
                delay = int(config.get("loop", {}).get("phase_delay_seconds", {}).get(state.phase.value, 10))
            time.sleep(delay)
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
