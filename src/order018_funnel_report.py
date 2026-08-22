from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from atm_core.payments import PaymentLedger
from atm_core.universal_radar import OperationalState, load_snapshot

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".atm"
RADAR = STATE / "universal-radar.json"
BOARD = STATE / "money-board.sqlite3"
RUNTIME_STATE = STATE / "state.json"
PAYMENTS = STATE / "validated-payment-proofs.jsonl"


def _runtime() -> dict[str, Any]:
    try:
        raw = json.loads(RUNTIME_STATE.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _board_eligible() -> int | None:
    if not BOARD.exists():
        return None
    try:
        db = sqlite3.connect(f"file:{BOARD}?mode=ro", uri=True, timeout=5)
        try:
            return int(db.execute("SELECT COUNT(*) FROM opportunities WHERE status='ELIGIBLE' AND falsifier_verdict='CONFIRM' AND verified_at IS NOT NULL").fetchone()[0])
        finally:
            db.close()
    except sqlite3.Error:
        return None


def _withdrawable() -> tuple[str, int]:
    try:
        proofs = PaymentLedger(PAYMENTS).load()
    except Exception:
        return "0", 0
    total = sum((Decimal(str(p.normalized_usd)) for p in proofs), Decimal("0"))
    return str(total), len(proofs)


def build() -> dict[str, Any]:
    runtime = _runtime()
    active = runtime.get("active_opportunity") if isinstance(runtime.get("active_opportunity"), dict) else None
    phase = str(runtime.get("phase") or "UNKNOWN")
    last = runtime.get("last_result") if isinstance(runtime.get("last_result"), dict) else {}
    radar = load_snapshot(RADAR)
    discovered = len(radar.opportunities) if radar else None
    capability_ready = sum(1 for row in radar.opportunities if row.operational_state == OperationalState.EXECUTABLE) if radar else None
    rejections = []
    if radar:
        for row in radar.opportunities[:80]:
            reasons = [str(x) for x in row.rejection_reasons]
            if reasons:
                rejections.append({"id": row.canonical_id, "source": row.source, "first_terminal_rejection_reason": reasons[0], "reasons": reasons[:8]})
    entry = active.get("entry_zero_cost_proven") if active else None
    settlement = active.get("settlement_capability") if active and isinstance(active.get("settlement_capability"), dict) else None
    tier = str((settlement or {}).get("tier") or active.get("settlement_tier") or "") if active else ""
    allocated = 1 if active and phase in {"CLAIM","WORK","CHECK","SUBMIT","MONITOR","PAYMENT_VERIFY"} else 0
    checker_pass = 1 if active and phase in {"SUBMIT","MONITOR","PAYMENT_VERIFY"} else (0 if active and phase == "CHECK" else None)
    submitted = 1 if active and (active.get("submission_id") or phase in {"MONITOR","PAYMENT_VERIFY"}) else (0 if active else None)
    accepted = None
    settled = None
    status = str(last.get("status") or "").upper()
    if status in {"ACCEPTED","SETTLED","PAID","PAYMENT_VERIFIED"}:
        accepted = 1
    if status in {"SETTLED","PAID","PAYMENT_VERIFIED"}:
        settled = 1
    withdrawable, payment_proofs = _withdrawable()
    return {
        "schema": "ATM_ORDER018_CANDIDATE_FUNNEL_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "truth_policy": "UNKNOWN_IS_NULL_NEVER_ZERO",
        "stages": {
            "DISCOVERED": discovered,
            "CANONICAL_ELIGIBLE": _board_eligible(),
            "ACTION_COST_READY": (1 if entry is True else 0 if entry is False else None),
            "CAPABILITY_READY": capability_ready,
            "SETTLEMENT_TIER_GTE_S1": (1 if tier in {"S1","S2","S3","S4"} else 0 if tier == "S0" else None),
            "ALLOCATED": allocated,
            "CHECKER_PASS": checker_pass,
            "SUBMITTED": submitted,
            "ACCEPTED": accepted,
            "SETTLED": settled,
            "WITHDRAWABLE_USDC": withdrawable,
        },
        "active": {
            "id": active.get("canonical_opportunity_id") if active else None,
            "source": active.get("source") if active else None,
            "phase": phase,
            "settlement_capability": settlement,
        },
        "validated_payment_proof_count": payment_proofs,
        "candidate_rejections": rejections[:32],
    }


def main() -> int:
    print("ATM_ORDER018_FUNNEL=" + json.dumps(build(), sort_keys=True, separators=(",", ":"), default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
