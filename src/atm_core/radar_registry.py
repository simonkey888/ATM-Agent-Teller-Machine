from __future__ import annotations

from decimal import Decimal

from .order009_r1_sources import SuperteamR1Source, WorkProtocolR1Source
from .order009_sources import _contract, _trash, order009_sources
from .p1_radar_sources import AlgoraSource, ClawEarnSource, ZeroXWorkSource
from .radar_sources import (
    AgentHansaSource,
    BountyBookSource,
    GitHubDirectBountySource,
    JsonHttpClient,
    MoltJobsSource,
    NearAgentMarketSource,
    OpireSource,
)
from .universal_radar import AgentPolicy, RadarRegistry, UniversalOpportunity


class CanonicalPolicySource:
    """One policy/freshness envelope around every source in the single registry."""

    def __init__(self, inner):
        self.inner = inner
        self.name = inner.name
        self.priority = inner.priority

    def discover(self, floor_usd: Decimal) -> list[UniversalOpportunity]:
        out: list[UniversalOpportunity] = []
        for raw in self.inner.discover(floor_usd):
            reason = _trash(f"{raw.title} {raw.description}")
            policy_text = f"{raw.eligibility} {raw.claim_method} {raw.human_gate or ''}".upper()
            if any(term in policy_text for term in ("STAKE_REQUIRED", "STAKE REQUIRED", "COLLATERAL_REQUIRED", "COLLATERAL REQUIRED")):
                reason = reason or "ZERO_SPEND_STAKE_OR_COLLATERAL_REQUIRED"
            if reason:
                raw = raw.model_copy(update={"agent_policy": AgentPolicy.PROHIBITED, "policy_rejection": reason})
            out.append(_contract(raw, object_type=getattr(raw, "source_object_type", "job")))
        return out


def build_canonical_registry(http: JsonHttpClient | None = None) -> RadarRegistry:
    """Single ORDER-009 registry; adding a source never forks the ATM state machine."""
    shared = http or JsonHttpClient()
    concrete = [
        SuperteamR1Source(shared),
        WorkProtocolR1Source(shared),
        *order009_sources(shared),
        OpireSource(),
        GitHubDirectBountySource(shared),
        MoltJobsSource(shared),
        NearAgentMarketSource(shared),
        AlgoraSource(),
        AgentHansaSource(shared),
        BountyBookSource(shared),
        ZeroXWorkSource(),
        ClawEarnSource(),
    ]
    return RadarRegistry([CanonicalPolicySource(source) for source in concrete])
