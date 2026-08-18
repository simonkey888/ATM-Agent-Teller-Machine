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


_ATM_FUNDING_PROOF_TOKEN = object()


class AuthoritativeFundingProof:
    """Non-serializable ATM-owned proof capability for funding admission.

    Raw source payloads can copy every visible field but cannot acquire the private
    in-process capability token. ORDER-005 R2 deliberately exposes only a fixed
    deterministic fixture producer; production rail verifiers may later return the
    same typed capability after performing their existing authoritative checks.
    """

    __slots__ = ("verifier_id", "rail", "evidence_ref", "evidence_hash", "verdict", "_authority_token")

    def __init__(
        self,
        *,
        verifier_id: str,
        rail: str,
        evidence_ref: str,
        evidence_hash: str,
        verdict: EvidenceState,
        _authority_token: object,
    ) -> None:
        if _authority_token is not _ATM_FUNDING_PROOF_TOKEN:
            raise ValueError("AuthoritativeFundingProof may only be minted by ATM verifier")
        if verifier_id != "ATM_AUTHORITATIVE_FUNDING_VERIFIER_V1":
            raise ValueError("unexpected funding verifier identity")
        if verdict != EvidenceState.PROVEN:
            raise ValueError("authoritative funding proof must prove funding")
        self.verifier_id = verifier_id
        self.rail = str(rail)
        self.evidence_ref = str(evidence_ref)
        self.evidence_hash = str(evidence_hash)
        self.verdict = verdict
        self._authority_token = _authority_token

    @property
    def is_atm_authoritative(self) -> bool:
        return self._authority_token is _ATM_FUNDING_PROOF_TOKEN and self.verdict == EvidenceState.PROVEN


def order005_authoritative_funding_fixture() -> AuthoritativeFundingProof:
    """Deterministic zero-action fixture proving the typed authority boundary only."""
    evidence = {
        "schema": "ATM_AUTHORITATIVE_FUNDING_FIXTURE_V1",
        "rail": "ORDER005_DETERMINISTIC_FIXTURE",
        "funding_observed": True,
        "economic_actions": 0,
        "outgoing_spend_usd": 0,
    }
    return AuthoritativeFundingProof(
        verifier_id="ATM_AUTHORITATIVE_FUNDING_VERIFIER_V1",
        rail="ORDER005_DETERMINISTIC_FIXTURE",
        evidence_ref="atm-proof://order005/deterministic-funding-fixture-v1",
        evidence_hash=canonical_hash(evidence),
        verdict=EvidenceState.PROVEN,
        _authority_token=_ATM_FUNDING_PROOF_TOKEN,
    )


class EconomicSemantics(BaseModel):
    """Structured economic identity/evidence. Free text and raw source claims have zero funding authority."""

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
    def from_structured(
        cls,
        payload: dict[str, Any],
        *,
        authoritative_funding_proof: AuthoritativeFundingProof | None = None,
    ) -> "EconomicSemantics":
        requester = str(payload.get("requester_id") or payload.get("requesterId") or "").strip()
        # Buyer identity must be supplied as an economic field. Requester/repo identity never aliases into it.
        buyer = str(payload.get("economic_buyer_id") or payload.get("buyer_id") or payload.get("buyerId") or "").strip()
        payer_verified = payload.get("payer_authority_verified") is True
        evidence_refs = [str(value) for value in payload.get("economic_evidence_refs", []) if str(value).strip()]
        funding_verified_claim = payload.get("funding_verified") is True
        external_funding_ref = str(payload.get("external_funding_ref") or payload.get("funding_tx_hash") or "").strip()
        budget_amount = payload.get("budget_amount")
        budget_currency = str(payload.get("budget_currency") or "").strip()

        # F009: source booleans/URLs/strings never mint FUNDING_VERIFIED. Only an ATM-owned typed proof can.
        if authoritative_funding_proof is not None:
            if not isinstance(authoritative_funding_proof, AuthoritativeFundingProof) or not authoritative_funding_proof.is_atm_authoritative:
                raise ValueError("invalid authoritative funding proof capability")
            budget = BudgetEvidence.FUNDING_VERIFIED
            if authoritative_funding_proof.evidence_ref not in evidence_refs:
                evidence_refs.append(authoritative_funding_proof.evidence_ref)
        elif external_funding_ref or funding_verified_claim:
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
            evidence_refs=evidence_refs,
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
    """Evidence-bounded supply ledger. None means UNKNOWN, never synthetic zero."""

    model_config = ConfigDict(extra="forbid")
    VISIBLE_USD: Decimal | None
    FRESH_USD: Decimal | None
    FUNDED_USD: Decimal | None
    ELIGIBLE_USD: Decimal | None
    CLAIMABLE_UNDER_CURRENT_AUTHORITY_USD: Decimal | None
    EXPECTED_WITHDRAWABLE_USD: Decimal | None
    OPEN_CANDIDATE_COUNT: int | None
    FRESH_CANDIDATE_COUNT: int | None
    FUNDED_CANDIDATE_COUNT: int | None
    ELIGIBLE_CANDIDATE_COUNT: int | None
    STALE_RATIO: Decimal | None
    MAX_TICKET_USD: Decimal | None
    MEDIAN_TICKET_USD: Decimal | None
    TOP_BLOCKER_REASONS: list[str]
    EVIDENCE_AS_OF: str
    INVENTORY_STATE: EvidenceState
    REALIZED_REVENUE_CREDITED: Decimal = Decimal("0")


def build_supply_adequacy(
    candidates: Iterable[SupplyCandidate],
    *,
    evidence_as_of: str,
    inventory_state: EvidenceState = EvidenceState.UNKNOWN,
) -> SupplyAdequacySnapshot:
    rows = list(candidates)
    if not rows and inventory_state != EvidenceState.PROVEN:
        return SupplyAdequacySnapshot(
            VISIBLE_USD=None,
            FRESH_USD=None,
            FUNDED_USD=None,
            ELIGIBLE_USD=None,
            CLAIMABLE_UNDER_CURRENT_AUTHORITY_USD=None,
            EXPECTED_WITHDRAWABLE_USD=None,
            OPEN_CANDIDATE_COUNT=None,
            FRESH_CANDIDATE_COUNT=None,
            FUNDED_CANDIDATE_COUNT=None,
            ELIGIBLE_CANDIDATE_COUNT=None,
            STALE_RATIO=None,
            MAX_TICKET_USD=None,
            MEDIAN_TICKET_USD=None,
            TOP_BLOCKER_REASONS=[],
            EVIDENCE_AS_OF=evidence_as_of,
            INVENTORY_STATE=inventory_state,
        )

    open_rows = [row for row in rows if row.is_open]
    fresh = [row for row in open_rows if row.is_fresh]
    funding_unknown = any(row.funding_verified is None for row in fresh)
    funded = [row for row in fresh if row.funding_verified is True]
    eligibility_unknown = funding_unknown or any(row.eligible is None for row in funded)
    eligible = [row for row in funded if row.eligible is True]
    claimability_unknown = eligibility_unknown or any(row.claimable_under_current_authority is None for row in eligible)
    claimable = [row for row in eligible if row.claimable_under_current_authority is True]
    expected_unknown = claimability_unknown or any(row.expected_withdrawable_usd is None for row in claimable)

    blockers: dict[str, int] = {}
    for row in open_rows:
        for reason in row.blocker_reasons:
            blockers[reason] = blockers.get(reason, 0) + 1
    rewards = [row.reward_usd for row in open_rows]
    stale_count = len([row for row in open_rows if not row.is_fresh])

    return SupplyAdequacySnapshot(
        VISIBLE_USD=sum((row.reward_usd for row in open_rows), Decimal("0")),
        FRESH_USD=sum((row.reward_usd for row in fresh), Decimal("0")),
        FUNDED_USD=None if funding_unknown else sum((row.reward_usd for row in funded), Decimal("0")),
        ELIGIBLE_USD=None if eligibility_unknown else sum((row.reward_usd for row in eligible), Decimal("0")),
        CLAIMABLE_UNDER_CURRENT_AUTHORITY_USD=None if claimability_unknown else sum((row.reward_usd for row in claimable), Decimal("0")),
        EXPECTED_WITHDRAWABLE_USD=(None if expected_unknown else sum((row.expected_withdrawable_usd or Decimal("0") for row in claimable), Decimal("0"))),
        OPEN_CANDIDATE_COUNT=len(open_rows),
        FRESH_CANDIDATE_COUNT=len(fresh),
        FUNDED_CANDIDATE_COUNT=None if funding_unknown else len(funded),
        ELIGIBLE_CANDIDATE_COUNT=None if eligibility_unknown else len(eligible),
        STALE_RATIO=(Decimal(stale_count) / Decimal(len(open_rows)) if open_rows else Decimal("0")),
        MAX_TICKET_USD=max(rewards) if rewards else None,
        MEDIAN_TICKET_USD=(Decimal(str(statistics.median([float(value) for value in rewards]))) if rewards else None,
        TOP_BLOCKER_REASONS=[key for key, _ in sorted(blockers.items(), key=lambda item: (-item[1], item[0]))[:5]],
        EVIDENCE_AS_OF=evidence_as_of,
        INVENTORY_STATE=EvidenceState.PROVEN if rows else inventory_state,
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

    @field_validator("read_only")
    @classmethod
    def must_be_read_only(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("ORDER-005 radar candidates are read-only")
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


class _ReadOnlyMutationLock:
    @staticmethod
    def _forbid(action: str) -> None:
        raise ValueError(f"ORDER-005 read-only radar forbids external action: {action}")

    @classmethod
    def claim(cls, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        cls._forbid("CLAIM")

    @classmethod
    def submit(cls, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        cls._forbid("SUBMIT")

    @classmethod
    def vote(cls, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        cls._forbid("VOTE")

    @classmethod
    def bid(cls, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        cls._forbid("BID")


class SuperteamRadar(_ReadOnlyMutationLock):
    SOURCE = "superteam-radar"
    SURFACE = "https://superteam.fun/skill.md"

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
            "economic_buyer_id": listing.get("economic_buyer_id") or listing.get("buyer_id"),
            "budget_amount": reward if reward > 0 else None,
            "budget_currency": str(listing.get("currency") or "USD") if reward > 0 else None,
            "payer_authority_verified": listing.get("payer_authority_verified") is True,
            "funding_verified": listing.get("funding_verified") is True,
            "economic_evidence_refs": listing.get("economic_evidence_refs", []),
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
            evidence_refs=[cls.SURFACE],
            economic_semantics=semantics,
        )


class WorkProtocolEvaluatorRadar(_ReadOnlyMutationLock):
    SOURCE = "workprotocol-evaluator-radar"
    SURFACE = "https://workprotocol.ai/for-verifiers"
    DOCS = "https://workprotocol.ai/docs/reference"
    DISPUTES_API = "https://workprotocol.ai/api/disputes"
    ARBITRATORS_API = "https://workprotocol.ai/api/arbitrators"

    @classmethod
    def surface_status(cls, *, open_disputes: int, active_arbitrators: int) -> dict[str, Any]:
        return {
            "source": cls.SOURCE,
            "ROLE_EXISTS": True,
            "CURRENT_ELIGIBILITY_REQUIREMENTS": "verifier expertise; agent arbitrators require reputation >= 10",
            "REPUTATION_REQUIREMENT": "AGENT_ARBITRATOR_REPUTATION_GTE_10",
            "ACTIVE_CASE_SUPPLY": int(open_disputes),
            "ACTIVE_ARBITRATORS": int(active_arbitrators),
            "REWARD_FEE_MECHANISM": "portion of 5% protocol fee on disputed jobs",
            "PAYOUT_PATH": "USDC_PER_VERDICT_ADVERTISED",
            "WITHDRAWABILITY_EVIDENCE": "UNPROVEN",
            "HUMAN_GATE": "STAKE_REQUIRED_PROHIBITED_BY_ZERO_SPEND",
            "EVALUATOR_REVENUE": "UNPROVEN",
            "read_only": True,
            "vote_actions": 0,
            "claim_actions": 0,
            "evidence_refs": [cls.SURFACE, cls.DOCS, cls.DISPUTES_API, cls.ARBITRATORS_API],
        }

    @classmethod
    def normalize_dispute(cls, dispute: dict[str, Any]) -> RadarCandidate:
        dispute_id = str(dispute.get("id") or "").strip()
        if not dispute_id:
            raise ValueError("WorkProtocol dispute identity required")
        semantics = EconomicSemantics.from_structured({
            "requester_id": dispute.get("requesterId"),
            # A dispute requester is not automatically the evaluator's economic buyer or payer.
            "economic_buyer_id": dispute.get("economic_buyer_id"),
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
            evidence_refs=[cls.SURFACE, cls.DOCS, cls.DISPUTES_API],
            economic_semantics=semantics,
        )


class DealworkProviderRadar(_ReadOnlyMutationLock):
    SOURCE = "dealwork-provider-radar"
    SURFACE = "https://dealwork.ai/skill.md"

    @classmethod
    def normalize_provider_job(cls, job: dict[str, Any]) -> RadarCandidate:
        job_id = str(job.get("id") or job.get("slug") or "").strip()
        if not job_id:
            raise ValueError("Dealwork job identity required")
        if str(job.get("role") or "provider").lower() not in {"provider", "worker", "seller"}:
            raise ValueError("Dealwork radar is provider-only")
        if bool(job.get("requires_downstream_payment") or job.get("hire_required") or job.get("subcontract_required")):
            raise ValueError("Dealwork downstream payment/hire/subcontract is constitutionally ineligible")
        reward = Decimal(str(job.get("reward_usd") or job.get("budget") or 0))
        semantics = EconomicSemantics.from_structured({
            "requester_id": job.get("requester_id") or job.get("buyer_id"),
            "economic_buyer_id": job.get("buyer_id"),
            "budget_amount": reward if reward > 0 else None,
            "budget_currency": str(job.get("currency") or "USD") if reward > 0 else None,
            "funding_verified": job.get("funding_verified") is True,
            "payer_authority_verified": job.get("payer_authority_verified") is True,
            "external_funding_ref": job.get("external_funding_ref"),
            "economic_evidence_refs": job.get("economic_evidence_refs", []),
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
            evidence_refs=[cls.SURFACE],
            economic_semantics=semantics,
        )

    @staticmethod
    def client_action(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise ValueError("Dealwork client/hire/subcontract action forbidden in ORDER-005")


class PainEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_url: str
    source_id: str
    reproducibility_evidence: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class BuyerContractSpecShadow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_identity: str
    source_url: str
    proposed_deliverable: str
    objective_acceptance_predicate: str
    requester_identity_status: EvidenceState
    buyer_identity_status: EvidenceState
    payer_authority_status: EvidenceState
    budget_funding_status: BudgetEvidence
    price_hypothesis_usd: Decimal | None = None
    external_action_requirement: str
    human_gate: str
    legal_policy_uncertainty: str
    confidence: str
    evidence_refs: list[str] = Field(default_factory=list)
    disposition: str

    @field_validator("disposition")
    @classmethod
    def disposition_enum(cls, value: str) -> str:
        value = value.upper()
        if value not in {"KILL", "SHADOW_WATCH", "RESEARCH_CANDIDATE"}:
            raise ValueError("invalid Demand Foundry disposition")
        return value

    @field_validator("external_action_requirement")
    @classmethod
    def no_executable_transition(cls, value: str) -> str:
        forbidden = {"CONTACT", "OUTREACH", "PROPOSE", "CLAIM", "SIGN", "INVOICE", "ESCROW_CREATE"}
        if value.upper() in forbidden:
            raise ValueError("Demand Foundry executable external action forbidden")
        return value


class DemandFoundryShadowRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pain: PainEvidence
    contract: BuyerContractSpecShadow
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

        # Title/body are deliberately excluded from economic inference. Structured evidence only.
        semantics = EconomicSemantics.from_structured({
            "requester_id": issue.get("requester_id"),
            "economic_buyer_id": issue.get("economic_buyer_id"),
            "payer_authority_verified": issue.get("payer_authority_verified") is True,
            "funding_verified": issue.get("funding_verified") is True,
            "external_funding_ref": issue.get("external_funding_ref"),
            "budget_amount": issue.get("budget_amount"),
            "budget_currency": issue.get("budget_currency"),
            "price_hypothesis_usd": issue.get("price_hypothesis_usd"),
            "economic_evidence_refs": issue.get("economic_evidence_refs", []),
        })
        reproduction = [str(value) for value in issue.get("reproducibility_evidence", []) if str(value).strip()]
        evidence_refs = [url, *[str(value) for value in issue.get("evidence_refs", []) if str(value).strip()]]
        if semantics.funded_for_admission and semantics.economic_buyer_identified == EvidenceState.PROVEN:
            disposition = "RESEARCH_CANDIDATE"
        elif reproduction:
            disposition = "SHADOW_WATCH"
        else:
            disposition = "KILL"

        pain = PainEvidence(
            source_url=url,
            source_id=identity,
            reproducibility_evidence=reproduction,
            evidence_refs=evidence_refs,
        )
        contract = BuyerContractSpecShadow(
            source_identity=identity,
            source_url=url,
            proposed_deliverable=str(issue.get("proposed_deliverable") or "UNKNOWN"),
            objective_acceptance_predicate=str(issue.get("objective_acceptance_predicate") or "UNKNOWN"),
            requester_identity_status=semantics.requester_identified,
            buyer_identity_status=semantics.economic_buyer_identified,
            payer_authority_status=semantics.payer_authority_verified,
            budget_funding_status=semantics.budget_evidence,
            price_hypothesis_usd=semantics.price_hypothesis_usd,
            external_action_requirement="NONE_AUTHORIZED_ORDER_005",
            human_gate=str(issue.get("human_gate") or "UNKNOWN"),
            legal_policy_uncertainty=str(issue.get("legal_policy_uncertainty") or "UNKNOWN"),
            confidence="EVIDENCE_BOUNDED",
            evidence_refs=evidence_refs,
            disposition=disposition,
        )
        return DemandFoundryShadowRecord(
            pain=pain,
            contract=contract,
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
