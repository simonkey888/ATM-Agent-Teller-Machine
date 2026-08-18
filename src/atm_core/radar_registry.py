from __future__ import annotations

from .p1_radar_sources import AlgoraSource, ClawEarnSource, ZeroXWorkSource
from .radar_sources import (
    AgentHansaSource,
    BountyBookSource,
    GitHubDirectBountySource,
    JsonHttpClient,
    MoltJobsSource,
    NearAgentMarketSource,
    OpireSource,
    SuperteamEarnSource,
    WorkProtocolRadarSource,
)
from .universal_radar import RadarRegistry


def build_canonical_registry(http: JsonHttpClient | None = None) -> RadarRegistry:
    """All R1 sources are concrete adapters; adding a source only touches this registry."""
    shared = http or JsonHttpClient()
    return RadarRegistry(
        [
            SuperteamEarnSource(shared),
            WorkProtocolRadarSource(shared),
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
    )
