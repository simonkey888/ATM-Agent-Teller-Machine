from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .universal_radar import JsonHttpClient


@dataclass(frozen=True)
class ProviderHealth:
    provider: str
    model: str
    credential_present: bool
    model_available: bool
    zero_cost_tier_proven: bool
    rate_limit_usable: bool
    usable: bool
    reason: str


class ZeroCostModelGate:
    """No paid fallback. Missing/uncertain provider capacity degrades execution, never radar discovery."""

    PAID_FALLBACK = False
    GEMMA4_ALLOWLIST = {"gemma-4-31b-it", "gemma-4-26b-a4b-it"}
    GEMINI_FREE_ALLOWLIST = {"gemini-2.5-flash", "gemini-2.5-flash-lite"}

    def __init__(self, http: JsonHttpClient | None = None):
        self.http = http or JsonHttpClient()

    def _google_models(self, api_key: str) -> set[str]:
        data = self.http.get_json(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
            timeout=8,
        )
        rows = data.get("models", []) if isinstance(data, dict) else []
        return {str(row.get("name") or "").split("/", 1)[-1] for row in rows if isinstance(row, dict)}

    def google_health(self, model: str) -> ProviderHealth:
        key = os.getenv("GEMINI_API_KEY", "").strip()
        declared_tier = os.getenv("GEMINI_USAGE_TIER", "").strip().lower()
        is_allowed = model in self.GEMMA4_ALLOWLIST | self.GEMINI_FREE_ALLOWLIST
        if not key:
            return ProviderHealth("gemini-api", model, False, False, False, False, False, "NO_CREDENTIAL")
        if declared_tier != "free":
            return ProviderHealth("gemini-api", model, True, False, False, False, False, "FREE_TIER_NOT_EXPLICITLY_PROVEN")
        if not is_allowed:
            return ProviderHealth("gemini-api", model, True, False, True, False, False, "MODEL_NOT_ZERO_COST_ALLOWLISTED")
        try:
            models = self._google_models(key)
        except Exception as exc:
            return ProviderHealth("gemini-api", model, True, False, True, False, False, f"MODEL_OR_RATE_PROBE_FAILED:{type(exc).__name__}")
        available = model in models
        # A successful authenticated list-models call proves the current API credential is not
        # hard rate-limited at the health boundary; generation still handles later 429s fail-closed.
        return ProviderHealth(
            "gemini-api", model, True, available, True, True, bool(available),
            "PASS" if available else "MODEL_CURRENTLY_UNAVAILABLE",
        )

    def opencode_health(self) -> ProviderHealth:
        key = os.getenv("OPENCODE_API_KEY", "").strip()
        model = os.getenv("ATM_OPENCODE_FREE_MODEL", "deepseek-v4-flash-free").strip()
        verified = os.getenv("ATM_OPENCODE_ZERO_COST_VERIFIED", "").strip().lower() == "true"
        if not key:
            return ProviderHealth("opencode-free", model, False, False, False, False, False, "NO_CREDENTIAL")
        if not verified:
            return ProviderHealth("opencode-free", model, True, False, False, False, False, "ZERO_COST_METADATA_NOT_VERIFIED")
        # Do not issue a paid-capable model call merely to probe health. A caller may set the
        # verified flag only from current provider metadata; runtime 429/auth failures degrade it.
        return ProviderHealth("opencode-free", model, True, True, True, True, True, "PASS_BY_VERIFIED_FREE_METADATA")

    def inspect(self) -> list[ProviderHealth]:
        gemini = os.getenv("ATM_GEMINI_FREE_MODEL", "gemini-2.5-flash-lite").strip()
        gemma = os.getenv("ATM_GEMMA4_FREE_MODEL", "gemma-4-31b-it").strip()
        return [self.google_health(gemini), self.google_health(gemma), self.opencode_health()]

    def any_usable(self) -> bool:
        return any(row.usable for row in self.inspect())

    @staticmethod
    def assert_no_paid_fallback(config: dict[str, Any]) -> None:
        if bool(config.get("provider", {}).get("paid_model_fallback")):
            raise ValueError("paid model fallback is constitutionally disabled")
        for provider in [config.get("provider", {}).get("primary", {}), *config.get("provider", {}).get("fallbacks", [])]:
            if provider and provider.get("zero_cost_required") is not True:
                raise ValueError("every configured execution provider must be explicitly zero-cost")
