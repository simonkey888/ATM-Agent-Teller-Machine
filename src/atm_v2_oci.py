from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

import atm as v1
import atm_v2 as core
from atm_publish_client import publish_workspace
from atm_core.models import HumanGate, Phase
from atm_core.opportunities import HumanGateRequired, OpportunityValidationError
from atm_core.payments import PaymentLedger, PaymentNotFinal, PaymentValidationError
from atm_core.runtime import ProcessLock, SingletonLockError
from atm_core.security import redact_text
from atm_core.state import StateStore
from atm_core.taskmarket_cli import TaskmarketCliError, materialize_supervisor_keystore
from atm_core.work_conserving import (
    detach_external_wait,
    isolated_lane_environment,
    next_delay_seconds,
    watch_generic_in_flight,
)


SECRETLESS_PHASE_LANES = {
    Phase.DISCOVER: "SCOUTS",
    Phase.VERIFY: "FALSIFIERS",
    Phase.WORK: "MAKERS",
    Phase.CHECK: "CHECKERS",
}


def continuous_authority_fence() -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "atm-authority-fence.py"
    check = subprocess.run([sys.executable, str(script)], text=True, capture_output=True, timeout=30)
    if check.returncode != 0:
        raise RuntimeError("CONTINUOUS_AUTHORITY_FENCE_FAILED:" + (check.stderr or check.stdout)[-500:])


def publish_local_deliverable_if_needed(state, prior_phase: Phase) -> None:
    if prior_phase != Phase.WORK or state.phase != Phase.CHECK or not state.active_opportunity:
        return
    raw = str(state.active_opportunity.deliverable_url or "")
    if not raw.startswith("file://"):
        return
    workspace = Path(raw[7:])
    try:
        url = publish_workspace(state.active_opportunity.canonical_opportunity_id, workspace)
    except Exception as exc:
        raise HumanGateRequired(
            HumanGate(
                kind="DELIVERABLE_PUBLISHER_UNAVAILABLE",
                reason=redact_text(str(exc))[-1000:],
                exact_human_action="Repair/rotate the OCI GitHub token with public repository creation/content write permission; never provide the token in chat or to Hermes.",
                resume_phase=Phase.WORK,
                opportunity_id=state.active_opportunity.canonical_opportunity_id,
            )
        ) from exc
    state.active_opportunity.deliverable_url = url
    state.last_result = {
        "status": "WORK_PUBLISHED",
        "deliverable_url": url,
        "opportunity_id": state.active_opportunity.canonical_opportunity_id,
    }


def run_cycle_oci(config, state, adapters, ledger) -> None:
    """Run one economic phase while preserving secretless untrusted lanes and nonblocking waits."""
    paused = core.PAUSE_FILE.exists()
    if paused and state.phase not in {Phase.MONITOR, Phase.PAYMENT_VERIFY}:
        with isolated_lane_environment("SCOUTS"):
            core.refresh_discovery_cache(config, adapters)
        state.last_result = {
            "status": "PAUSED_SAFE_MONITOR_ONLY",
            "held_phase": state.phase.value,
            "active_opportunity_id": state.active_opportunity.canonical_opportunity_id if state.active_opportunity else None,
        }
        state.cycle += 1
        return
    prior_phase = state.phase
    lane = SECRETLESS_PHASE_LANES.get(prior_phase, "ECONOMIC_AUTHORITY")
    with isolated_lane_environment(lane):
        core.run_cycle(config, state, adapters, ledger)
    publish_local_deliverable_if_needed(state, prior_phase)
    detach_external_wait(state, prior_phase)


def run_watchers(config, state, adapters, ledger) -> dict[str, int]:
    """Monitor durable external waits without occupying the maker/checker lane."""
    before = len(state.in_flight)
    # TaskMarket has stronger source-specific award/payment semantics in core.
    v1.monitor_in_flight(config, state, adapters, ledger, limit=8)
    generic_checked = watch_generic_in_flight(
        config,
        state,
        adapters,
        ledger,
        v1.adapter_for,
        limit=8,
    )
    active = sum(1 for row in state.in_flight if not bool(getattr(row, "inflight_terminal", False)))
    return {"tracked": before, "active": active, "generic_checked": generic_checked}


def runtime_status(state, ledger, targets, watcher_stats: dict[str, int] | None = None) -> dict:
    payload = core.status_payload(state, ledger, targets)
    payload["WORK_CONSERVING_RUNTIME"] = True
    payload["IN_FLIGHT_COUNT"] = len(state.in_flight)
    payload["IN_FLIGHT_ACTIVE"] = sum(
        1 for row in state.in_flight if not bool(getattr(row, "inflight_terminal", False))
    )
    if watcher_stats is not None:
        payload["WATCHER_STATS"] = watcher_stats
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="ATM OCI always-on work-conserving supervisor")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    config = v1.load_config()
    try:
        signer_status = materialize_supervisor_keystore()
    except TaskmarketCliError as exc:
        signer_status = {"ready": False, "materialized": False, "reason": type(exc).__name__}
    v1.log_event("taskmarket-signer-bootstrap", signer_status)
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
        print(json.dumps(runtime_status(state, ledger, targets), indent=2, ensure_ascii=False, default=str))
        return 0

    lock = ProcessLock(core.LOCK_FILE)
    try:
        lock.acquire()
    except SingletonLockError as exc:
        print(f"ATM_SINGLETON_BLOCKED: {exc}", file=v1.sys.stderr)
        return 3

    try:
        while True:
            watcher_stats = {"tracked": len(state.in_flight), "active": 0, "generic_checked": 0}
            try:
                config = v1.load_config()
                adapters = core.build_adapters(config)

                # WATCHERS remain useful even while a different task is executing.
                continuous_authority_fence()
                watcher_stats = run_watchers(config, state, adapters, ledger)
                continuous_authority_fence()
                run_cycle_oci(config, state, adapters, ledger)

                if state.human_gate:
                    core.localize_gate(state.human_gate, state, config)
                store.save(state)
                payload = runtime_status(state, ledger, targets, watcher_stats)
                v1.log_event("cycle-oci-work-conserving", payload)
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
                v1.log_event(
                    "validation-oci",
                    {
                        "error": str(exc),
                        "failed_phase": failed_phase.value,
                        "phase": state.phase.value,
                        "opportunity_id": failed_id,
                    },
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
                        exact_human_action="No immediate human action required; persistent ATM continues independent discovery/watch work after provider recovery/config change.",
                        resume_phase=state.phase,
                        opportunity_id=state.active_opportunity.canonical_opportunity_id if state.active_opportunity else None,
                    )
                    core.localize_gate(gate, state, config)
                store.save(state)
                v1.log_event("error-oci", {"error": type(exc).__name__, "phase": state.phase.value})
                if args.once:
                    raise
                time.sleep(int(config.get("loop", {}).get("failure_backoff_seconds", 30)))

            if args.once:
                return 0
            delay = next_delay_seconds(config, state)
            time.sleep(delay)
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
