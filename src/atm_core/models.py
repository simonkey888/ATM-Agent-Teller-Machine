from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Phase(str, Enum):
    DISCOVER = "DISCOVER"
    VERIFY = "VERIFY"
    CLAIM = "CLAIM"
    WORK = "WORK"
    CHECK = "CHECK"
    SUBMIT = "SUBMIT"
    MONITOR = "MONITOR"
    PAYMENT_VERIFY = "PAYMENT_VERIFY"


class ProviderFailureClass(str, Enum):
    RATE_LIMIT = "RATE_LIMIT"
    QUOTA = "QUOTA"
    AUTH = "AUTH"
    BAD_REQUEST = "BAD_REQUEST"
    TIMEOUT = "TIMEOUT"
    NETWORK = "NETWORK"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    UNKNOWN = "UNKNOWN"


class ProviderFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str | None = None
    session_id: str | None = None
    error_class: ProviderFailureClass
    http_status: int | None = None
    retry_after_seconds: int | None = None
    message: str
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProviderCircuit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    opened_at: datetime
    reopen_at: datetime
    reason: str


class Opportunity(BaseModel):
    model_config = ConfigDict(extra="allow")

    canonical_opportunity_id: str
    source: str
    authoritative_url: str
    upstream_status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deadline: datetime | None = None
    reward_gross: Decimal
    expected_fees: Decimal = Decimal("0")
    funding_proof: dict[str, Any] = Field(default_factory=dict)
    competition: int = 0
    claims: int = 0
    open_prs: int = 0
    ai_policy: str = "UNKNOWN"
    eligibility: str = "UNKNOWN"
    claim_method: str = "UNKNOWN"
    submission_method: str = "UNKNOWN"
    payment_method: str = "UNKNOWN"
    payout_latency: str = "UNKNOWN"
    payment_proof_method: str = "UNKNOWN"
    expected_agent_hours: Decimal = Decimal("4")
    expected_owner_minutes: Decimal = Decimal("0")
    payout_latency_hours: Decimal = Decimal("24")
    p_eligible: Decimal = Decimal("0.80")
    p_claim: Decimal = Decimal("0.70")
    p_complete: Decimal = Decimal("0.75")
    p_accept: Decimal = Decimal("0.50")
    p_pay: Decimal = Decimal("0.95")
    p_withdrawable: Decimal = Decimal("0.98")
    external_state_hash: str | None = None
    claim_id: str | None = None
    submission_id: str | None = None
    deliverable_url: str | None = None
    claim_lease_until: datetime | None = None
    idempotency_key: str | None = None

    @field_validator("authoritative_url")
    @classmethod
    def https_authoritative_url(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("authoritative_url must use https")
        return value

    @field_validator("p_eligible", "p_claim", "p_complete", "p_accept", "p_pay", "p_withdrawable")
    @classmethod
    def probability_in_unit_interval(cls, value: Decimal) -> Decimal:
        if value < 0 or value > 1:
            raise ValueError("probability must be between 0 and 1")
        return value

    @field_validator("expected_agent_hours")
    @classmethod
    def positive_agent_hours(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("expected_agent_hours must be positive")
        return value

    @field_validator("expected_owner_minutes", "payout_latency_hours")
    @classmethod
    def nonnegative_time(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("time estimate cannot be negative")
        return value

    @property
    def reward_net(self) -> Decimal:
        return max(Decimal("0"), self.reward_gross - self.expected_fees)

    @property
    def ev_realized(self) -> Decimal:
        return (
            self.reward_net
            * self.p_eligible
            * self.p_claim
            * self.p_complete
            * self.p_accept
            * self.p_pay
            * self.p_withdrawable
        )

    @property
    def total_effort_hours(self) -> Decimal:
        return self.expected_agent_hours + (self.expected_owner_minutes / Decimal("60"))

    @property
    def ev_per_effort_hour(self) -> Decimal:
        return self.ev_realized / self.total_effort_hours


class HumanGate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    reason: str
    exact_human_action: str
    resume_phase: Phase
    opportunity_id: str | None = None


class PaymentReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: str
    external_id: str
    authoritative_url: str | None = None


FORBIDDEN_MODEL_MONEY_FIELDS = {
    "paid_usd",
    "accepted_usd",
    "claimed_usd",
    "realized_usd",
    "realized_withdrawable_usd",
    "withdrawable_usd",
    "validated_payment_proofs",
}


class AgentResult(BaseModel):
    """Strict model output. Economic truth is deliberately absent."""

    model_config = ConfigDict(extra="forbid")

    status: str
    next_phase: Phase | None = None
    active_opportunity: Opportunity | None = None
    human_gate: HumanGate | None = None
    payment_reference: PaymentReference | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ModelAuthorityViolation(ValueError):
    pass


def reject_forbidden_money_fields(raw: dict[str, Any]) -> None:
    present: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).lower() in FORBIDDEN_MODEL_MONEY_FIELDS:
                    present.add(str(key))
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(raw)
    if present:
        raise ModelAuthorityViolation(
            "model attempted to write economic truth fields: " + ", ".join(sorted(present))
        )


def parse_agent_result(raw: dict[str, Any]) -> AgentResult:
    reject_forbidden_money_fields(raw)
    return AgentResult.model_validate(raw)


class PaymentStatus(str, Enum):
    FUNDED_ESCROW = "FUNDED_ESCROW"
    ACCEPTED = "ACCEPTED"
    RELEASED = "RELEASED"
    WITHDRAWABLE = "WITHDRAWABLE"
    PAID = "PAID"


COUNTABLE_PAYMENT_STATUSES = {
    PaymentStatus.RELEASED,
    PaymentStatus.WITHDRAWABLE,
    PaymentStatus.PAID,
}


class ValidatedPaymentProof(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    platform: str
    payout_id_or_txid: str
    event_index_or_unique_id: str
    amount: Decimal
    currency: str
    recipient_public_identifier: str
    status: PaymentStatus
    timestamp: datetime
    authoritative_url_or_api: str
    evidence_hash: str
    normalized_usd: Decimal
    fx_source: str | None = None
    fx_timestamp: datetime | None = None
    fx_rate: Decimal | None = None
    chain_id: int | None = None
    token_address: str | None = None

    @field_validator("amount", "normalized_usd")
    @classmethod
    def positive_amount(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("amount must be positive")
        return value

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()

    @field_validator("authoritative_url_or_api")
    @classmethod
    def authoritative_https(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("payment evidence must be from an https authoritative source")
        return value

    @model_validator(mode="after")
    def fx_contract(self) -> "ValidatedPaymentProof":
        if self.currency in {"USD", "USDC"}:
            if self.normalized_usd != self.amount:
                raise ValueError("USD/USDC must normalize 1:1")
        else:
            if not (self.fx_source and self.fx_timestamp and self.fx_rate):
                raise ValueError("non-USD currency requires fx source, timestamp and rate")
            expected = self.amount * self.fx_rate
            if abs(expected - self.normalized_usd) > Decimal("0.000001"):
                raise ValueError("normalized_usd does not match amount * fx_rate")
        return self

    @property
    def dedupe_key(self) -> str:
        if self.chain_id is not None and self.event_index_or_unique_id.lower().startswith("log:"):
            return "|".join(
                [str(self.chain_id), self.payout_id_or_txid.lower(), self.event_index_or_unique_id.lower()]
            )
        return "|".join(
            [
                self.platform.lower(),
                self.payout_id_or_txid.lower(),
                self.event_index_or_unique_id.lower(),
            ]
        )


class RuntimeState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 2
    phase: Phase = Phase.DISCOVER
    cycle: int = 0
    target_paid_usd: Decimal = Decimal("200")
    active_opportunity: Opportunity | None = None
    in_flight: list[Opportunity] = Field(default_factory=list)
    last_result: dict[str, Any] | None = None
    last_error: str | None = None
    human_gate: HumanGate | None = None
    provider_failures: list[ProviderFailure] = Field(default_factory=list)
    provider_circuits: dict[str, ProviderCircuit] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("schema_version")
    @classmethod
    def schema_v2(cls, value: int) -> int:
        if value != 2:
            raise ValueError("unsupported state schema_version")
        return value
