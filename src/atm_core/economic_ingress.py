from __future__ import annotations

import statistics
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .workers import canonical_hash


class EvidenceState(StrEnum):
    PROVEN = "PROVEN"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class BudgetEvidence(StrEnum):
    NO_BUDGET_EVIDENCE = "NO_BUDGET_EVIDENCE"
    BUDGET_MENTION_UNVERIFIED = "BUDGET_MENTION_UNVERIFIED"
    FUNDING_SIGNAL_EXTERNAL_UNVERIFIED = "FUNDING_SIGNAL_EXTERNAL_UNVERIFIED"
    FUNDING_VERIFIED = "FUNDING_VERIFIED"


class EconomicSemantics(BaseModel):
    """Structured economic identity/evidence. Free text never upgrades these fields."""

    model_config = ConfigDict(extra="forbid")
    requester_identified: EvidenceState
    economic_buyer_identified: EvidenceState
    payer_authority_verified: EvidenceState
    budget_evidence: BudgetEvidence
    community_or_reputation_signal: bool = False
    non_economic_shadow: bool = False
    price_hypothesis_usd: Decimal | None = None
    evidence_refs: list[str] = Field(default_factory=list)

    @classmethod
    def from_structured(cls, payload: dict[str, Any]) -> "EconomicSemantics":
        requester = str(payload.get("requester_id") or payload.get("requesterId") or "").strip()
        buyer = str(payload.get("economic_buyer_id") or payload.get("buyer_id") or payload.get("buyerId") or "").strip()
        payer_verified = payload.get("payer_authority_verified") is True
        funding_verified = payload.get("funding_verified") is True
        external_funding_ref = str(payload.get("external_funding_ref") or payload.get("funding_tx_hash") or "").strip()
        budget_amount = payload.get("budget_amount")
        budget_currency = str(payload.get("budget_currency") or "").strip()
        if funding_verified:
            budget = BudgetEvidence.FUNDING_VERIFIED
        elif external_funding_ref:
            budget = BudgetEvidence.FUNDING_SIGNAL_EXTERNAL_UNVERIFIED
        elif budget_amount not in (None, "") and budget_currency:
            budget = BudgetEvidence.BUDGET_MENTION_UNVERIFIED
        else:
            budget = BudgetEvidence.NO_BUDGET_EVIDENCE
        community = bool(payload.get("community_signal") or payload.get("reputation_signal"))
        return cls(
            requester_identified=EvidenceState.PROVEN if requester and requester.lower() != "anonymous" else EvidenceState.UNKNOWN,
            economic_buyer_identified=EvidenceState.PROVEN if buyer else EvidenceState.UNKNOWN,
            payer_authority_verified=EvidenceState.PROVEN if payer_verified else EvidenceState.UNKNOWN,
            budget_evidence=budget,
            community_or_reputation_signal=community,
            non_economic_shadow=community and budget != BudgetEvidence.FUNDING_VERIFIED,
            price_hypothesis_usd=(Decimal(str(payload["price_hypothesis_usd"])) if payload.get("price_hypothesis_usd") not in (None, "") else None),
            evidence_refs=[str(value) for value in payload.get("economic_evidence_refs", [])],
        )

    @property
    def funded_for_admission(self) -> bool:
        return self.budget_evidence == BudgetEvidence.FUNDING_VERIFIED and self.payer_authority_verified == EvidenceState.PROVEN


class SupplyCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    canonical_opportunity_id: str
    source: str
    reward_usd: Decimal
    is_open: bool
    is_fresh: bool
    funding_verified: bool | None
    eligible: bool | None
    claimable_under_current_authority: bool | None
    expected_withdrawable_usd: Decimal | None = None
    blocker_reasons: list[str] = Field(default_factory=list)


class SupplyAdequacySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    VISIBLE_USD: Decimal
    FRESH_USD: Decimal
    FUNDED_USD: Decimal
    ELIGIBLE_USD: Decimal
    CLAIMABLE_UNDER_CURRENT_AUTHORITY_USD: Decimal
    EXPECTED_WITHDRAWABLE_USD: Decimal | None
    OPEN_COUNT: int
    FRESH_COUNT: int
    FUNDED_COUNT: int
    ELIGIBLE_COUNT: int
    STALE_RATIO: Decimal
    MAX_TICKET_USD: Decimal | None
    MEDIAN_TICKET_USD: Decimal | None
    TOP_BLOCKER_REASONS: list[str]
    EVIDENCE_AS_OF: str
    REALIZED_REVENUE_CREDITED: Decimal = Decimal("0")


def build_supply_adequacy(candidates: Iterable[SupplyCandidate], *, evidence_as_of: str) -> SupplyAdequacySnapshot:
    rows = list(candidates)
    open_rows = [row for row in rows if row.is_open]
    fresh = [row for row in open_rows if row.is_fresh]
    funded = [row for row in fresh if row.funding_verified is True]
    eligible = [row for row in funded if row.eligible is True]
    claimable = [row for row in eligible if row.claimable_under_current_authority is True]
    expected_values = [row.expected_withdrawable_usd for row in claimable if row.expected_withdrawable_usd is not None]
    blockers: dict[str, int] = {}
    for row in open_rows:
        for reason in row.blocker_reasons:
            blockers[reason] = blockers.get(reason, 0) + 1
    rewards = [row.reward_usd for row in open_rows]
    stale_count = len([row for row in open_rows if not row.is_fresh])
    return SupplyAdequacySnapshot(
        VISIBLE_USD=sum((row.reward_usd for row in open_rows), Decimal("0")),
        FRESH_USD=sum((row.reward_usd for row in fresh), Decimal("0")),
        FUNDED_USD=sum((row.reward_usd for row in funded), Decimal("0")),
        ELIGIBLE_USD=sum((row.reward_usd for row in eligible), Decimal("0")),
        CLAIMABLE_UNDER_CURRENT_AUTHORITY_USD=sum((row.reward_usd for row in claimable), Decimal("0")),
        EXPECTED_WITHDRAWABLE_USD=(sum(expected_values, Decimal("0")) if len(expected_values) == len(claimable) else None),
        OPEN_COUNT=len(open_rows),
        FRESH_COUNT=len(fresh),
        FUNDED_COUNT=len(funded),
        ELIGIBLE_COUNT=len(eligible),
        STALE_RATIO=(Decimal(stale_count) / Decimal(len(open_rows)) if open_rows else Decimal("0")),
        MAX_TICKET_USD=max(rewards) if rewards else None,
        MEDIAN_TICKET_USD=(Decimal(str(statistics.median([float(value) for value in rewards]))) if rewards else None),
        TOP_BLOCKER_REASONS=[key for key, _ in sorted(blockers.items(), key=lambda item: (-item[1], item[0]))[:5]],
        EVIDENCE_AS_OF=evidence_as_of,
    )


class GateDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: EvidenceState
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)


class GateFeasibility(BaseModel):
    model_config = ConfigDict(extra="forbid")
    WORK_FEASIBLE: GateDimension
    CLAIM_FEASIBLE: GateDimension
    SUBMIT_FEASIBLE: GateDimension
    PAYMENT_FEASIBLE: GateDimension
    WITHDRAWAL_FEASIBLE: GateDimension


class GateInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_supported: bool | None = None
    claim_supported: bool | None = None
    submit_supported: bool | None = None
    payment_path_verified: bool | None = None
    withdrawal_verified: bool | None = None
    blockers: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


def build_gate_feasibility(inputs: GateInputs) -> GateFeasibility:
    blocker = ";".join(sorted(set(inputs.blockers))) or "NONE"

    def dim(value: bool | None, name: str) -> GateDimension:
        if value is True:
            state, reason = EvidenceState.PROVEN, f"{name}_PROVEN"
        elif value is False:
            state, reason = EvidenceState.BLOCKED, f"{name}_BLOCKED:{blocker}"
        else:
            state, reason = EvidenceState.UNKNOWN, f"{name}_UNKNOWN:{blocker}"
        return GateDimension(state=state, reason=reason, evidence_refs=list(inputs.evidence_refs))

    return GateFeasibility(
        WORK_FEASIBLE=dim(inputs.work_supported, "WORK"),
        CLAIM_FEASIBLE=dim(inputs.claim_supported, "CLAIM"),
        SUBMIT_FEASIBLE=dim(inputs.submit_supported, "SUBMIT"),
        PAYMENT_FEASIBLE=dim(inputs.payment_path_verified, "PAYMENT"),
        WITHDRAWAL_FEASIBLE=dim(inputs.withdrawal_verified, "WITHDRAWAL"),
    )


class RailHealthState(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNKNOWN = "UNKNOWN"


class Claimability(StrEnum):
    PROVEN = "PROVEN"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class FailureDomain(StrEnum):
    WORKER = "WORKER"
    CHECKER = "CHECKER"
    PLATFORM = "PLATFORM"
    AUTHORITY = "AUTHORITY"
    EXTERNAL_PAYER = "EXTERNAL_PAYER"
    UNKNOWN = "UNKNOWN"


class RailHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    RAIL_HEALTH: RailHealthState
    CLAIMABILITY: Claimability
    FAILURE_DOMAIN: FailureDomain
    REASON: str
    EVIDENCE_REFS: list[str]
    EVIDENCE_AS_OF: str


def build_rail_health(
    *,
    source_reachable: bool | None,
    claimability: bool | None,
    failure_domain: FailureDomain,
    reason: str,
    evidence_refs: list[str],
    evidence_as_of: str,
) -> RailHealth:
    if source_reachable is None:
        health = RailHealthState.UNKNOWN
    elif source_reachable and claimability is True:
        health = RailHealthState.HEALTHY
    else:
        health = RailHealthState.DEGRADED
    claim = Claimability.PROVEN if claimability is True else Claimability.BLOCKED if claimability is False else Claimability.UNKNOWN
    return RailHealth(
        RAIL_HEALTH=health,
        CLAIMABILITY=claim,
        FAILURE_DOMAIN=failure_domain,
        REASON=reason,
        EVIDENCE_REFS=evidence_refs,
        EVIDENCE_AS_OF=evidence_as_of,
    )


class RadarCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    canonical_opportunity_id: str
    source: str
    authoritative_url: str
    reward_usd: Decimal = Decimal("0")
    updated_at: str | None = None
    eligibility: str
    human_gate: str | None = None
    acceptance: str = "UNKNOWN"
    payout: str = "UNKNOWN"
    competition: int | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    read_only: bool = True
    economic_semantics: EconomicSemantics

    @field_validator("authoritative_url")
    @classmethod
    def https_only(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("radar authoritative_url must use https")
        return value

    def money_board_payload(self) -> dict[str, Any]:
        return {
            "canonical_opportunity_id": self.canonical_opportunity_id,
            "source": self.source,
            "authoritative_url": self.authoritative_url,
            "updated_at": self.updated_at,
            "reward_gross": str(self.reward_usd),
            "eligibility": self.eligibility,
            "human_gate": self.human_gate,
            "acceptance": self.acceptance,
            "payout": self.payout,
            "competition": self.competition,
            "read_only": True,
            "economic_semantics": self.economic_semantics.model_dump(mode="json"),
            "external_state_hash": canonical_hash(self.model_dump(mode="json")),
        }


class SuperteamRadar:
    SOURCE = "superteam-radar"
    SURFACE = "https://superteam.fun/earn/agents"

    @classmethod
    def normalize_listing(cls, listing: dict[str, Any]) -> RadarCandidate:
        listing_id = str(listing.get("id") or listing.get("slug") or "").strip()
        if not listing_id:
            raise ValueError("Superteam listing identity required")
        access = str(listing.get("agentAccess") or "").upper()
        eligibility = access if access in {"AGENT_ALLOWED", "AGENT_ONLY"} else "HUMAN_ONLY_OR_UNPROVEN"
        reward = Decimal(str(listing.get("reward_usd") or listing.get("reward") or 0))
        semantics = EconomicSemantics.from_structured({
            "requester_id": listing.get("requester_id") or listing.get("sponsor"),
            "economic_buyer_id": listing.get("buyer_id") or listing.get("sponsor"),
            "budget_amount": reward if reward > 0 else None,
            "budget_currency": "USD" if reward > 0 else None,
            "payer_authority_verified": False,
            "economic_evidence_refs": [cls.SURFACE],
        })
        return RadarCandidate(
            canonical_opportunity_id=f"superteam:{listing_id}",
            source=cls.SOURCE,
            authoritative_url=str(listing.get("url") or f"https://superteam.fun/earn/listing/{listing_id}"),
            reward_usd=reward,
            updated_at=str(listing.get("updated_at") or listing.get("deadline") or "") or None,
            eligibility=eligibility,
            human_gate="HUMAN_CLAIMS_PAYOUT" if eligibility in {"AGENT_ALLOWED", "AGENT_ONLY"} else "AGENT_ELIGIBILITY_UNPROVEN",
            acceptance=str(listing.get("acceptance") or "LISTING_RULES"),
            payout="HUMAN_CLAIM_REQUIRED",
            competition=(int(listing["submissions"]) if listing.get("submissions") is not None else None),
            evidence_refs=[cls.SURFACE, "https://superteam.fun/skill.md"],
            economic_semantics=semantics,
        )


class WorkProtocolEvaluatorRadar:
    SOURCE = "workprotocol-evaluator-radar"
    SURFACE = "https://workprotocol.ai/for-verifiers"
    DISPUTES_API = "https://workprotocol.ai/api/disputes"
    ARBITRATORS_API = "https://workprotocol.ai/api/arbitrators"

    @classmethod
    def surface_status(cls, *, open_disputes: int, active_arbitrators: int) -> dict[str, Any]:
        return {
            "source": cls.SOURCE,
            "role_exists": True,
            "open_disputes": int(open_disputes),
            "active_arbitrators": int(active_arbitrators),
            "reward_model": "SHARE_OF_5_PERCENT_PROTOCOL_FEE_ON_DISPUTES",
            "claimability": "BLOCKED_ZERO_SPEND_STAKE_REQUIREMENT",
            "read_only": True,
            "vote_actions": 0,
            "claim_actions": 0,
            "evidence_refs": [cls.SURFACE, cls.DISPUTES_API, cls.ARBITRATORS_API],
        }

    @classmethod
    def normalize_dispute(cls, dispute: dict[str, Any]) -> RadarCandidate:
        dispute_id = str(dispute.get("id") or "").strip()
        if not dispute_id:
            raise ValueError("WorkProtocol dispute identity required")
        semantics = EconomicSemantics.from_structured({
            "requester_id": dispute.get("requesterId"),
            "economic_buyer_id": dispute.get("requesterId"),
            "payer_authority_verified": False,
            "economic_evidence_refs": [cls.DISPUTES_API],
        })
        return RadarCandidate(
            canonical_opportunity_id=f"workprotocol-evaluator:{dispute_id}",
            source=cls.SOURCE,
            authoritative_url=f"https://workprotocol.ai/disputes/{dispute_id}",
            eligibility="ARBITRATOR_ROLE_READ_ONLY_ZERO_SPEND_BLOCKED",
            human_gate="STAKE_REQUIRED_PROHIBITED_BY_OWNER_ORDER",
            acceptance="ARBITRATION_RULES",
            payout="UNPROVEN_UNTIL_AUTHORITATIVE_RELEASE_WITHDRAWABLE_PROOF",
            evidence_refs=[cls.SURFACE, cls.DISPUTES_API],
            economic_semantics=semantics,
        )


class DealworkProviderRadar:
    SOURCE = "dealwork-provider-radar"
    SURFACE = "https://dealwork.ai/how-it-works"

    @classmethod
    def normalize_provider_job(cls, job: dict[str, Any]) -> RadarCandidate:
        job_id = str(job.get("id") or job.get("slug") or "").strip()
        if not job_id:
            raise ValueError("Dealwork job identity required")
        if str(job.get("role") or "provider").lower() not in {"provider", "worker", "seller"}:
            raise ValueError("Dealwork radar is provider-only")
        reward = Decimal(str(job.get("reward_usd") or job.get("budget") or 0))
        semantics = EconomicSemantics.from_structured({
            "requester_id": job.get("requester_id") or job.get("buyer_id"),
            "economic_buyer_id": job.get("buyer_id") or job.get("requester_id"),
            "budget_amount": reward if reward > 0 else None,
            "budget_currency": "USD" if reward > 0 else None,
            "funding_verified": job.get("funding_verified") is True,
            "payer_authority_verified": job.get("payer_authority_verified") is True,
            "economic_evidence_refs": [cls.SURFACE],
        })
        return RadarCandidate(
            canonical_opportunity_id=f"dealwork:{job_id}",
            source=cls.SOURCE,
            authoritative_url=str(job.get("url") or f"https://dealwork.ai/jobs/{job_id}"),
            reward_usd=reward,
            updated_at=str(job.get("updated_at") or "") or None,
            eligibility="PROVIDER_ONLY_READ_ONLY_RADAR",
            human_gate=str(job.get("human_gate") or "KYA_OR_CREDENTIAL_AUTHORITY_UNKNOWN"),
            acceptance=str(job.get("acceptance") or "PLATFORM_CONTRACT"),
            payout="ESCROW_PLATFORM_CLAIM_REQUIRES_SEPARATE_PROOF",
            evidence_refs=[cls.SURFACE, "https://dealwork.ai/skill.md"],
            economic_semantics=semantics,
        )

    @staticmethod
    def client_action(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise ValueError("Dealwork client/hire/subcontract action forbidden in ORDER-005")


class DemandFoundryShadowRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_url: str
    source_id: str
    artifact_type: str = "STRUCTURED_RESEARCH_SHADOW"
    economic_semantics: EconomicSemantics
    outreach_actions: list[str] = Field(default_factory=list)
    claim_actions: list[str] = Field(default_factory=list)
    executable_actions: list[str] = Field(default_factory=list)
    state: str = "SHADOW_ONLY"
    evidence_as_of: str

    @property
    def funded_opportunity(self) -> bool:
        return self.economic_semantics.funded_for_admission


class DemandFoundryShadow:
    @staticmethod
    def from_public_issue(issue: dict[str, Any], *, evidence_as_of: str) -> DemandFoundryShadowRecord:
        url = str(issue.get("html_url") or issue.get("url") or "")
        if not url.startswith("https://"):
            raise ValueError("Demand Foundry source must be public https evidence")
        identity = str(issue.get("id") or issue.get("number") or "").strip()
        if not identity:
            raise ValueError("Demand Foundry source identity required")
        # The body/title are intentionally not parsed for money words. Only structured external evidence can upgrade economics.
        semantics = EconomicSemantics.from_structured({
            "requester_id": issue.get("requester_id"),
            "economic_buyer_id": issue.get("economic_buyer_id"),
            "payer_authority_verified": issue.get("payer_authority_verified") is True,
            "funding_verified": issue.get("funding_verified") is True,
            "external_funding_ref": issue.get("external_funding_ref"),
            "budget_amount": issue.get("budget_amount"),
            "budget_currency": issue.get("budget_currency"),
            "economic_evidence_refs": issue.get("economic_evidence_refs", []),
        })
        return DemandFoundryShadowRecord(
            source_url=url,
            source_id=identity,
            economic_semantics=semantics,
            evidence_as_of=evidence_as_of,
        )


def normalize_radar_to_money_board(board: Any, candidates: Iterable[RadarCandidate]) -> list[str]:
    """Reuse the canonical Money Board. No radar gets a parallel admission store."""
    ids: list[str] = []
    for candidate in candidates:
        payload = candidate.money_board_payload()
        board.upsert_candidate(
            payload,
            scout=candidate.source,
            base_score=float(candidate.reward_usd),
            confidence=0.5,
            negative_evidence=["READ_ONLY_RADAR"],
            status="NORMALIZED",
        )
        ids.append(candidate.canonical_opportunity_id)
    return ids


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
