#!/usr/bin/env python3
from __future__ import annotations

import json
from decimal import Decimal

from atm_core.order009_sources import RAIL_CAPABILITIES
from atm_core.radar_registry import build_canonical_registry
from atm_core.universal_radar import UniversalRadar

MANDATORY = {
    "superteam", "workprotocol", "agentpact", "taskmarket-daydreams", "ugig", "opire", "github-direct",
    "moltjobs", "near-agent-market", "algora", "agenthire-verify", "agenthansa", "bountybook", "0xwork",
    "claw-earn", "runtime-open-federation-verify", "the402", "clustly", "toku",
}


def main() -> int:
    registry = build_canonical_registry()
    snapshot = UniversalRadar(registry, floor_usd=Decimal("5"), per_source_timeout=4).scan()
    health = {row.source: {"state": row.state.value, "open_count": row.open_count} for row in snapshot.platform_health}
    names = set(registry.names)
    missing = sorted(MANDATORY - names)
    polluted = [row["source"] for row in RAIL_CAPABILITIES if row.get("job_counted") is not False]
    print(json.dumps({
        "schema": snapshot.schema,
        "radar_sources": registry.names,
        "source_count": len(registry.names),
        "normalized_count": len(snapshot.opportunities),
        "platform_health": health,
        "rail_capabilities": [row["source"] for row in RAIL_CAPABILITIES],
        "mandatory_missing": missing,
        "sell_service_job_count_pollution": polluted,
        "outgoing_spend_usd": str(snapshot.outgoing_spend_usd),
        "writes": 0,
    }, sort_keys=True))
    if snapshot.outgoing_spend_usd != Decimal("0"):
        return 2
    if missing:
        return 3
    if polluted:
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
