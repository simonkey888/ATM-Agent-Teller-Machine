from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ModelRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    base_url: str
    model_id: str
    capabilities: list[str] = Field(default_factory=list)
    input_cost_usd_per_million_tokens: Decimal
    output_cost_usd_per_million_tokens: Decimal
    metadata_source: str
    metadata_verified_at: datetime
    enabled: bool = True

    @field_validator("base_url", "metadata_source")
    @classmethod
    def https_only(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("model route metadata and base URL must use https")
        return value

    @property
    def verified_zero_cost(self) -> bool:
        return (
            self.enabled
            and self.input_cost_usd_per_million_tokens == Decimal("0")
            and self.output_cost_usd_per_million_tokens == Decimal("0")
        )


class ModelGateway:
    """Models are replaceable capabilities, never economic authorities."""

    paid_model_fallback = False
    model_may_author_economic_truth = False
    model_may_hold_private_key = False
    model_may_expand_network_scope = False

    def __init__(self, routes: Iterable[ModelRoute], max_metadata_age_hours: int = 24):
        self.routes = list(routes)
        self.max_metadata_age_hours = max(1, int(max_metadata_age_hours))

    def select(self, capability: str, now: datetime | None = None) -> ModelRoute | None:
        now = now or datetime.now(timezone.utc)
        eligible: list[ModelRoute] = []
        for route in self.routes:
            age_hours = (now - route.metadata_verified_at.astimezone(timezone.utc)).total_seconds() / 3600
            if age_hours > self.max_metadata_age_hours:
                continue
            if capability not in route.capabilities:
                continue
            if not route.verified_zero_cost:
                continue
            eligible.append(route)
        if not eligible:
            return None
        return sorted(eligible, key=lambda route: (route.provider, route.model_id))[0]


def opencode_zen_free_route_from_verified_metadata(
    *,
    model_ids: Iterable[str],
    official_free_model_ids: Iterable[str],
    verified_at: datetime,
) -> ModelRoute | None:
    """Construct a route only when current official discovery and free-list evidence agree."""
    models = {str(value) for value in model_ids}
    free = {str(value) for value in official_free_model_ids}
    model_id = "deepseek-v4-flash-free"
    if model_id not in models or model_id not in free:
        return None
    return ModelRoute(
        provider="OpenCode Zen",
        base_url="https://opencode.ai/zen/v1",
        model_id=model_id,
        capabilities=["reasoning", "coding", "falsification", "structured_output"],
        input_cost_usd_per_million_tokens=Decimal("0"),
        output_cost_usd_per_million_tokens=Decimal("0"),
        metadata_source="https://opencode.ai/docs/zen",
        metadata_verified_at=verified_at,
    )
