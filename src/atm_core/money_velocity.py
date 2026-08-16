from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable

from .models import Opportunity, ValidatedPaymentProof


D0 = Decimal("0")
D1 = Decimal("1")


def _d(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _clamp01(value: Decimal) -> Decimal:
    return max(D0, min(D1, value))


def _age_hours(when: datetime | None, now: datetime) -> Decimal | None:
    if when is None:
        return None
    delta = now - when.astimezone(timezone.utc)
    return max(D0, Decimal(str(delta.total_seconds())) / Decimal("3600"))


def task_alive_probability(opportunity: Opportunity, now: datetime | None = None) -> Decimal:
    now = now or datetime.now(timezone.utc)
    if str(opportunity.upstream_status).lower() not in {"open", "available", "active"}:
        return D0
    if opportunity.deadline and opportunity.deadline <= now:
        return D0
    activity_at = opportunity.updated_at or opportunity.created_at
    age = _age_hours(activity_at, now)
    if age is None:
        return Decimal("0.55")
    if age <= 48:
        return Decimal("0.98")
    if age <= 24 * 7:
        return Decimal("0.85")
    if age <= 24 * 30:
        return Decimal("0.45")
    return Decimal("0.05")


def payer_active_probability(opportunity: Opportunity, now: datetime | None = None) -> Decimal:
    now = now or datetime.now(timezone.utc)
    explicit = getattr(opportunity, "p_payer_active", None)
    if explicit is not None:
        return _clamp01(_d(explicit))
    payer_at = getattr(opportunity, "payer_last_activity_at", None)
    if isinstance(payer_at, str):
        try:
            payer_at = datetime.fromisoformat(payer_at.replace("Z", "+00:00"))
        except ValueError:
            payer_at = None
    activity_at = payer_at or opportunity.updated_at or opportunity.created_at
    age = _age_hours(activity_at, now)
    if age is None:
        return Decimal("0.50")
    if age <= 48:
        return Decimal("0.95")
    if age <= 24 * 7:
        return Decimal("0.80")
    if age <= 24 * 30:
        return Decimal("0.40")
    return Decimal("0.10")


def finish_fast_probability(opportunity: Opportunity) -> Decimal:
    hours = max(Decimal("0.01"), opportunity.expected_agent_hours)
    if hours <= 1:
        return Decimal("0.97")
    if hours <= 2:
        return Decimal("0.88")
    if hours <= 3:
        return Decimal("0.72")
    if hours <= 6:
        return Decimal("0.35")
    return Decimal("0.12")


def expected_minutes_to_withdrawable(opportunity: Opportunity) -> Decimal:
    review_hours = _d(getattr(opportunity, "review_latency_hours", 0))
    work_minutes = opportunity.expected_agent_hours * Decimal("60") + opportunity.expected_owner_minutes
    cash_minutes = (opportunity.payout_latency_hours + review_hours) * Decimal("60")
    return max(Decimal("1"), work_minutes + cash_minutes)


def has_funding_evidence(opportunity: Opportunity) -> bool:
    proof = opportunity.funding_proof or {}
    return any(
        bool(proof.get(key))
        for key in (
            "escrow_funded",
            "escrow_tx_hash",
            "escrow_status",
            "settlement_tx_hash",
            "funded",
        )
    )


def money_velocity(opportunity: Opportunity, now: datetime | None = None) -> Decimal:
    now = now or datetime.now(timezone.utc)
    p_alive = task_alive_probability(opportunity, now)
    p_payer = payer_active_probability(opportunity, now)
    p_claim_now = _clamp01(opportunity.p_claim)
    p_finish = finish_fast_probability(opportunity)
    p_accept = _clamp01(opportunity.p_accept)
    p_pay = _clamp01(opportunity.p_pay)
    competition_penalty = Decimal("1") / Decimal(max(1, 1 + opportunity.competition))
    numerator = (
        opportunity.reward_net
        * p_alive
        * p_payer
        * p_claim_now
        * p_finish
        * p_accept
        * p_pay
        * competition_penalty
    )
    return (numerator / expected_minutes_to_withdrawable(opportunity)).quantize(
        Decimal("0.000001"), rounding=ROUND_HALF_UP
    )


def reject_reason(opportunity: Opportunity, now: datetime | None = None) -> str | None:
    now = now or datetime.now(timezone.utc)
    if opportunity.reward_net <= 0:
        return "NO_POSITIVE_PAYOUT"
    if task_alive_probability(opportunity, now) == 0:
        return "STALE_OR_CLOSED"
    age = _age_hours(opportunity.updated_at or opportunity.created_at, now)
    if age is not None and age > 24 * 30:
        return "STALE_DEMAND"
    if not has_funding_evidence(opportunity):
        return "NO_FUNDING_PROOF"
    if opportunity.competition >= 8 or opportunity.open_prs >= 4:
        return "HIGH_COMPETITION"
    if str(getattr(opportunity, "payment_timing", "")).lower() in {"post_campaign", "subjective_campaign"}:
        return "POST_CAMPAIGN_PAY"
    if bool(getattr(opportunity, "requires_upfront_payment", False)):
        return "PAID_BID_STAKE_GAS"
    if str(opportunity.eligibility).upper() in {"HUMAN_ONLY", "HUMAN_WORK_REQUIRED"}:
        return "HUMAN_WORK_REQUIRED"
    if not opportunity.payment_method or opportunity.payment_method.upper() == "UNKNOWN":
        return "NO_WITHDRAWAL_PATH"
    return None


def choose_money_velocity(
    opportunities: Iterable[Opportunity],
    *,
    now: datetime | None = None,
    hard_default_hours: Decimal = Decimal("3"),
) -> Opportunity | None:
    now = now or datetime.now(timezone.utc)
    valid = [o for o in opportunities if reject_reason(o, now) is None]
    if not valid:
        return None
    short = [o for o in valid if o.expected_agent_hours <= hard_default_hours]
    pool = short or valid
    return max(
        pool,
        key=lambda o: (
            money_velocity(o, now),
            payer_active_probability(o, now),
            -expected_minutes_to_withdrawable(o),
            o.reward_net,
        ),
    )


def month_key(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m")


def current_month_bounds(value: datetime) -> tuple[datetime, datetime]:
    current = value.astimezone(timezone.utc)
    start = datetime(current.year, current.month, 1, tzinfo=timezone.utc)
    if current.month == 12:
        end = datetime(current.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(current.year, current.month + 1, 1, tzinfo=timezone.utc)
    return start, end


class MonthlyTargetStore:
    def __init__(self, path: Path, month1_target: Decimal = Decimal("500")):
        self.path = path
        self.month1_target = month1_target

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _save(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + f".tmp-{uuid.uuid4().hex}")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def target_for(self, now: datetime, proofs: Iterable[ValidatedPaymentProof]) -> Decimal:
        current_key = month_key(now)
        data = self._load()
        if current_key in data:
            return Decimal(data[current_key])
        if not data:
            data[current_key] = str(self.month1_target)
            self._save(data)
            return self.month1_target
        prior_key = max(k for k in data if k < current_key) if any(k < current_key for k in data) else None
        if prior_key is None:
            target = self.month1_target
        else:
            prior_target = Decimal(data[prior_key])
            prior_realized = sum(
                (p.normalized_usd for p in proofs if month_key(p.timestamp) == prior_key),
                Decimal("0"),
            )
            target = max(prior_target * Decimal("1.10"), prior_realized * Decimal("1.05"))
            target = target.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        data[current_key] = str(target)
        self._save(data)
        return target


@dataclass(frozen=True)
class MonthlyMetrics:
    monthly_target_usd: Decimal
    monthly_realized_withdrawable_usd: Decimal
    monthly_gap_usd: Decimal
    days_remaining_in_month: int
    required_daily_run_rate_usd: Decimal
    observed_7d_run_rate_usd: Decimal
    observed_30d_run_rate_usd: Decimal
    average_minutes_to_cash: Decimal
    payouts_count: int
    acceptance_rate: Decimal | None
    payment_rate_after_acceptance: Decimal | None
    realized_by_rail: dict[str, Decimal]
    realized_by_archetype: dict[str, Decimal]

    def as_dict(self) -> dict[str, Any]:
        return {
            "MONTHLY_TARGET_USD": str(self.monthly_target_usd),
            "MONTHLY_REALIZED_WITHDRAWABLE_USD": str(self.monthly_realized_withdrawable_usd),
            "MONTHLY_GAP_USD": str(self.monthly_gap_usd),
            "DAYS_REMAINING_IN_MONTH": self.days_remaining_in_month,
            "REQUIRED_DAILY_RUN_RATE_USD": str(self.required_daily_run_rate_usd),
            "OBSERVED_7D_RUN_RATE_USD": str(self.observed_7d_run_rate_usd),
            "OBSERVED_30D_RUN_RATE_USD": str(self.observed_30d_run_rate_usd),
            "AVERAGE_MINUTES_TO_CASH": str(self.average_minutes_to_cash),
            "PAYOUTS_COUNT": self.payouts_count,
            "ACCEPTANCE_RATE": None if self.acceptance_rate is None else str(self.acceptance_rate),
            "PAYMENT_RATE_AFTER_ACCEPTANCE": None if self.payment_rate_after_acceptance is None else str(self.payment_rate_after_acceptance),
            "REALIZED_USD_BY_RAIL": {k: str(v) for k, v in sorted(self.realized_by_rail.items())},
            "REALIZED_USD_BY_TASK_ARCHETYPE": {k: str(v) for k, v in sorted(self.realized_by_archetype.items())},
        }


def monthly_metrics(
    proofs: Iterable[ValidatedPaymentProof],
    target: Decimal,
    now: datetime | None = None,
) -> MonthlyMetrics:
    now = now or datetime.now(timezone.utc)
    proofs = list(proofs)
    start, end = current_month_bounds(now)
    current = [p for p in proofs if start <= p.timestamp.astimezone(timezone.utc) < end]
    realized = sum((p.normalized_usd for p in current), Decimal("0"))
    gap = max(Decimal("0"), target - realized)
    days_remaining = max(1, (end.date() - now.astimezone(timezone.utc).date()).days)
    required_daily = (gap / Decimal(days_remaining)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    last7 = now - timedelta(days=7)
    last30 = now - timedelta(days=30)
    realized7 = sum((p.normalized_usd for p in proofs if p.timestamp >= last7), Decimal("0"))
    realized30 = sum((p.normalized_usd for p in proofs if p.timestamp >= last30), Decimal("0"))
    by_rail: dict[str, Decimal] = {}
    for proof in current:
        by_rail[proof.platform] = by_rail.get(proof.platform, Decimal("0")) + proof.normalized_usd
    return MonthlyMetrics(
        monthly_target_usd=target,
        monthly_realized_withdrawable_usd=realized,
        monthly_gap_usd=gap,
        days_remaining_in_month=days_remaining,
        required_daily_run_rate_usd=required_daily,
        observed_7d_run_rate_usd=(realized7 / Decimal("7")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        observed_30d_run_rate_usd=(realized30 / Decimal("30")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        average_minutes_to_cash=Decimal("0"),
        payouts_count=len(current),
        acceptance_rate=None,
        payment_rate_after_acceptance=None,
        realized_by_rail=by_rail,
        realized_by_archetype={"paid_digital_work": realized} if realized else {},
    )
