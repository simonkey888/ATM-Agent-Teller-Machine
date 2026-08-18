"""Narrow ORDER-009-R1 source truth corrections for the first live money vertical."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .radar_sources import SuperteamEarnSource, WorkProtocolRadarSource, _dt
from .universal_radar import AgentPolicy, SourceState, SourceUnavailable, UniversalOpportunity, source_hash


class WorkProtocolR1Source(WorkProtocolRadarSource):
    """Preserve the full current open feed; qualification applies the payout floor later."""

    def discover(self, floor_usd: Decimal) -> list[UniversalOpportunity]:
        del floor_usd
        return super().discover(Decimal("0"))


class SuperteamR1Source(SuperteamEarnSource):
    """Treat `/live` as discovery only and revalidate each listing from details."""

    _LIVE_STATUSES = {"OPEN", "LIVE", "ACTIVE", "PUBLISHED"}
    _SUPPORTED_REGIONS = {
        "GLOBAL", "REMOTE", "WORLDWIDE", "ARGENTINA", "LATAM", "LATIN AMERICA", "LATIN_AMERICA",
    }

    @classmethod
    def _region_allowed(cls, listing: dict[str, Any]) -> bool:
        raw = listing.get("regions")
        if raw in (None, "", []):
            raw = listing.get("region")
        if raw in (None, "", []):
            return True
        values = raw if isinstance(raw, list) else [raw]
        normalized: set[str] = set()
        for value in values:
            if isinstance(value, dict):
                value = value.get("name") or value.get("slug") or value.get("code")
            if value not in (None, ""):
                normalized.add(str(value).strip().upper().replace("-", "_"))
        if not normalized:
            return False
        return any(value in cls._SUPPORTED_REGIONS for value in normalized)

    def discover(self, floor_usd: Decimal) -> list[UniversalOpportunity]:
        candidates = super().discover(floor_usd)
        if not candidates:
            return []
        headers = {"Authorization": f"Bearer {self._credential()}"}
        verified: list[UniversalOpportunity] = []
        detail_failures = 0
        now = datetime.now(timezone.utc)
        for opportunity in candidates:
            slug = opportunity.canonical_url.rstrip("/").rsplit("/", 1)[-1]
            if not slug or "/" in slug or slug in {".", ".."}:
                continue
            try:
                payload = self.http.get_json(
                    f"{self.base_url}/api/agents/listings/details/{slug}",
                    headers=headers,
                    timeout=10,
                )
            except Exception:
                detail_failures += 1
                continue
            listing = payload.get("listing") if isinstance(payload, dict) and isinstance(payload.get("listing"), dict) else payload
            if not isinstance(listing, dict):
                detail_failures += 1
                continue

            status = str(listing.get("status") or "").upper()
            if status not in self._LIVE_STATUSES:
                continue
            deadline = _dt(listing.get("deadline"))
            if deadline is not None and deadline <= now:
                continue
            access = str(listing.get("agentAccess") or "").upper()
            if access not in {AgentPolicy.AGENT_ALLOWED.value, AgentPolicy.AGENT_ONLY.value}:
                continue
            if not self._region_allowed(listing):
                continue

            verified.append(
                opportunity.model_copy(
                    update={
                        "deadline": deadline,
                        "agent_policy": AgentPolicy(access),
                        "source_state_hash": source_hash(listing),
                        "superteam_detail_validated": True,
                        "superteam_detail_status": status,
                    }
                )
            )

        if not verified and detail_failures == len(candidates):
            raise SourceUnavailable(
                self.name,
                SourceState.DEGRADED,
                "Superteam /live membership observed but per-listing detail validation failed closed",
            )
        return verified
