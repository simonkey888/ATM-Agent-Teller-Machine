#!/usr/bin/env python3
from __future__ import annotations

import json
from decimal import Decimal

from atm_core.radar_registry import build_canonical_registry
from atm_core.universal_radar import UniversalRadar


def main() -> int:
    registry = build_canonical_registry()
    snapshot = UniversalRadar(registry, floor_usd=Decimal("5"), per_source_timeout=4).scan()
    health = {
        row.source: {
            "state": row.state.value,
            "open_count": row.open_count,
        }
        for row in snapshot.platform_health
    }
    # Never print listing descriptions, credentials, claim codes, wallet material or
    # unredacted platform responses from this probe. It is only a contract/health check.
    print(
        json.dumps(
            {
                "schema": snapshot.schema,
                "radar_sources": registry.names,
                "source_count": len(registry.names),
                "normalized_count": len(snapshot.opportunities),
                "platform_health": health,
                "outgoing_spend_usd": str(snapshot.outgoing_spend_usd),
                "writes": 0,
            },
            sort_keys=True,
        )
    )
    if snapshot.outgoing_spend_usd != Decimal("0"):
        return 2
    if len(registry.names) < 11:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
