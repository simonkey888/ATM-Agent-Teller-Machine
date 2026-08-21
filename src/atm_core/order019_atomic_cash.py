from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

_INSTALLED = False
ATOMICITY_ORDER = {"A0": 0, "A1": 1, "A2": 2, "A3": 3}
WORKPROTOCOL_ATOMICITY_PROOF = "WORKPROTOCOL_API_V2_AUTO_VERIFY_WINDOW"
WORKPROTOCOL_ATOMICITY_PROOF_URL = "https://workprotocol.ai/docs/reference"


def _decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _explicit_bool(opportunity: Any, name: str) -> bool | None:
    value = getattr(opportunity, name, None)
    return value if isinstance(value, bool) else None


def _atomicity(opportunity: Any) -> tuple[str, str, str]:
    explicit = str(getattr(opportunity, "atomicity_tier", "") or "").upper()
    if explicit in ATOMICITY_ORDER:
        return explicit, str(getattr(opportunity, "atomicity_proof", "EXPLICIT_SOURCE_EVIDENCE")), str(
            getattr(opportunity, "atomicity_proof_url", "") or ""
        )

    source = str(getattr(opportunity, "source", "") or "").lower()
    if source == "workprotocol" and _decimal(getattr(opportunity, "payout_latency_hours", 0)) > 0:
        # WorkProtocol API v2 documents an idempotent /api/jobs/auto-verify path
        # that approves delivered claims after the per-job verification window.
        # This proves A1 protocol atomicity only; it does NOT prove that a
        # particular job is claimable, executable, funded onchain, or zero-cost.
        return "A1", WORKPROTOCOL_ATOMICITY_PROOF, WORKPROTOCOL_ATOMICITY_PROOF_URL

    # TaskMarket ordinary bounty settlement remains requester-discretionary
    # unless a stronger source-specific terminal path is explicitly attached.
    return "A0", "NO_FORCED_SETTLEMENT_PROVEN", ""


def _capability_ready(opportunity: Any) -> bool:
    explicit = _explicit_bool(opportunity, "capability_ready")
    if explicit is not None:
        return explicit
    if str(getattr(opportunity, "source", "") or "").lower() == "taskmarket":
        return bool(getattr(opportunity, "maker_supported", False))
    return False


def _outgoing_cost(opportunity: Any) -> tuple[str, bool]:
    explicit = _optional_decimal(getattr(opportunity, "outgoing_cost_usd", None))
    if explicit is not None:
        return str(explicit), explicit == 0
    source = str(getattr(opportunity, "source", "") or "").lower()
    if source == "workprotocol":
        # WorkProtocol claim/deliver API operations are authenticated writes,
        # not X402-paid actions. Economic eligibility is still rechecked later.
        return "0", True
    return "UNKNOWN_PRECANON", False


def _workprotocol_identity_ready() -> bool:
    # Narrow lookup only: A1 specifically needs the existing WorkProtocol
    # identity. Values are never returned, logged, or copied into evidence.
    from .opportunities import local_atm_value

    return bool(local_atm_value("WORKPROTOCOL_AGENT_ID") and local_atm_value("WORKPROTOCOL_API_KEY"))


def _funding_signal(opportunity: Any) -> bool:
    from .money_velocity import has_funding_evidence

    try:
        return bool(has_funding_evidence(opportunity))
    except Exception:
        return False


@dataclass(frozen=True)
class AtomicCashMetrics:
    reward_net_usd: str
    atomicity_tier: str
    atomicity_proof: str
    atomicity_proof_url: str
    expected_work_minutes: str
    expected_verify_minutes: str
    expected_settlement_minutes: str
    p_accept: str
    p_pay: str
    p_withdrawable: str
    outgoing_cost_usd: str
    competition: int
    capability_ready: bool
    identity_ready: bool | None
    funding_signal: bool
    acceptance_machine_checkable: bool | None
    no_prior_atm_effect: bool | None
    p0_first_cash_ready: bool
    expected_withdrawable_usd: str
    cash_velocity_usd_per_minute: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def atomic_cash_metrics(opportunity: Any) -> AtomicCashMetrics:
    source = str(getattr(opportunity, "source", "") or "").lower()
    reward = _decimal(getattr(opportunity, "reward_net", 0))
    tier, proof, proof_url = _atomicity(opportunity)
    work_minutes = max(Decimal("1"), _decimal(getattr(opportunity, "expected_agent_hours", 0)) * Decimal("60"))

    explicit_verify = _optional_decimal(getattr(opportunity, "expected_verify_minutes", None))
    explicit_settlement = _optional_decimal(getattr(opportunity, "expected_settlement_minutes", None))
    payout_minutes = max(Decimal("0"), _decimal(getattr(opportunity, "payout_latency_hours", 0)) * Decimal("60"))
    if explicit_verify is not None:
        verify_minutes = max(Decimal("0"), explicit_verify)
    elif source == "workprotocol":
        verify_minutes = payout_minutes
    else:
        verify_minutes = Decimal("0")
    if explicit_settlement is not None:
        settlement_minutes = max(Decimal("0"), explicit_settlement)
    elif source == "workprotocol":
        settlement_minutes = Decimal("0")
    else:
        settlement_minutes = payout_minutes

    p_accept = max(Decimal("0"), min(Decimal("1"), _decimal(getattr(opportunity, "p_accept", 0))))
    p_pay = max(Decimal("0"), min(Decimal("1"), _decimal(getattr(opportunity, "p_pay", 0))))
    p_withdrawable = max(Decimal("0"), min(Decimal("1"), _decimal(getattr(opportunity, "p_withdrawable", 0))))
    p_eligible = max(Decimal("0"), min(Decimal("1"), _decimal(getattr(opportunity, "p_eligible", 0))))
    p_claim = max(Decimal("0"), min(Decimal("1"), _decimal(getattr(opportunity, "p_claim", 0))))
    p_complete = max(Decimal("0"), min(Decimal("1"), _decimal(getattr(opportunity, "p_complete", 0))))
    expected = reward * p_eligible * p_claim * p_complete * p_accept * p_pay * p_withdrawable
    total_minutes = max(Decimal("1"), work_minutes + verify_minutes + settlement_minutes)
    velocity = expected / total_minutes

    capability = _capability_ready(opportunity)
    outgoing_text, outgoing_zero = _outgoing_cost(opportunity)
    identity_ready: bool | None = _workprotocol_identity_ready() if source == "workprotocol" else _explicit_bool(
        opportunity, "identity_ready"
    )
    funding = _funding_signal(opportunity)
    acceptance = _explicit_bool(opportunity, "acceptance_machine_checkable")
    no_prior = _explicit_bool(opportunity, "no_prior_atm_effect")

    p0_ready = bool(
        source == "workprotocol"
        and tier in {"A1", "A2", "A3"}
        and capability
        and outgoing_zero
        and identity_ready is True
        and funding
        and acceptance is True
        and no_prior is True
    )

    return AtomicCashMetrics(
        reward_net_usd=str(reward),
        atomicity_tier=tier,
        atomicity_proof=proof,
        atomicity_proof_url=proof_url,
        expected_work_minutes=str(work_minutes),
        expected_verify_minutes=str(verify_minutes),
        expected_settlement_minutes=str(settlement_minutes),
        p_accept=str(p_accept),
        p_pay=str(p_pay),
        p_withdrawable=str(p_withdrawable),
        outgoing_cost_usd=outgoing_text,
        competition=max(0, int(getattr(opportunity, "competition", 0) or 0)),
        capability_ready=capability,
        identity_ready=identity_ready,
        funding_signal=funding,
        acceptance_machine_checkable=acceptance,
        no_prior_atm_effect=no_prior,
        p0_first_cash_ready=p0_ready,
        expected_withdrawable_usd=str(expected),
        cash_velocity_usd_per_minute=str(velocity),
    )


def _rank_key(opportunity: Any, metrics: AtomicCashMetrics) -> tuple[Any, ...]:
    from .money_velocity import money_velocity

    return (
        1 if metrics.p0_first_cash_ready else 0,
        1 if metrics.capability_ready else 0,
        ATOMICITY_ORDER.get(metrics.atomicity_tier, 0),
        _decimal(metrics.cash_velocity_usd_per_minute),
        money_velocity(opportunity),
        -metrics.competition,
        _decimal(metrics.reward_net_usd),
    )


def choose_atomic_swarm_candidate(
    found: list[Any],
    eligible_order: list[str],
    *,
    adapters: dict[str, Any],
    config: dict[str, Any],
    hard_cap: Decimal,
):
    """A1 selector: Money Velocity is diagnostic/ranking only, never admission.

    MoneyBoard CONFIRM remains a candidate hint. Missing durable IDs are
    authoritatively re-fetched. Every surviving candidate is ranked; legacy
    reject_reason is recorded but cannot silently veto a Canon candidate.
    Canon and the effect boundary retain all economic authority downstream.
    """
    del hard_cap
    from . import money_velocity as mv
    from .order019_recovery import refetch_board_candidate

    floor = Decimal(str(config.get("min_reward_usd", 1)))
    fresh = {row.canonical_opportunity_id: row for row in found}
    ledger: list[dict[str, Any]] = []
    candidates: list[tuple[Any, AtomicCashMetrics, dict[str, Any]]] = []

    for canonical_id in eligible_order:
        candidate = fresh.get(canonical_id)
        origin = "CURRENT_DISCOVER_PASS"
        reason = ""
        if candidate is None:
            candidate, reason = refetch_board_candidate(canonical_id, adapters=adapters, floor=floor)
            origin = "DIRECT_SOURCE_REFETCH"
        if candidate is None:
            ledger.append(
                {
                    "canonical_id": canonical_id,
                    "origin": origin,
                    "result": "REJECTED",
                    "reason": reason or "REDISCOVERY_MISS_UNEXPLAINED",
                }
            )
            continue

        metrics = atomic_cash_metrics(candidate)
        legacy_reason = mv.reject_reason(candidate)
        row = {
            "canonical_id": canonical_id,
            "origin": origin,
            "external_state_hash": candidate.external_state_hash,
            "result": "RANKED_FOR_CANON_VERIFY",
            "legacy_ranker_reason_diagnostic_only": legacy_reason,
            **metrics.as_dict(),
        }
        ledger.append(row)
        candidates.append((candidate, metrics, row))

    if not candidates:
        return None, ledger

    selected, selected_metrics, selected_row = max(candidates, key=lambda item: _rank_key(item[0], item[1]))
    selected_row["result"] = "SELECTED_FOR_CANON_VERIFY"
    selected_row["money_velocity"] = str(mv.money_velocity(selected))
    selected_row["a1_selection_authority"] = "RANK_ONLY_CANON_AND_EFFECT_BOUNDARY_REMAIN_AUTHORITATIVE"
    selected_row["atomicity_tier"] = selected_metrics.atomicity_tier
    return selected, ledger


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    from . import order019_recovery as recovery

    recovery.choose_swarm_candidate = choose_atomic_swarm_candidate
