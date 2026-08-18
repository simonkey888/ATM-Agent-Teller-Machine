from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
    """Fail-closed execution provider gate; radar discovery never depends on model credentials."""

    PAID_FALLBACK = False
    GEMMA4_ALLOWLIST = {"gemma-4-31b-it", "gemma-4-26b-a4b-it"}
    # Current Gemini API pricing has a Standard Free Tier for Flash-Lite, but not
    # for gemini-2.5-flash. Keep the allowlist deliberately narrower than model availability.
    GEMINI_FREE_ALLOWLIST = {"gemini-2.5-flash-lite"}
    OPENCODE_FREE_ALLOWLIST = {"deepseek-v4-flash-free"}
    VERIFICATION_MAX_AGE = timedelta(hours=24)

    def __init__(self, http: JsonHttpClient | None = None):
        self.http = http or JsonHttpClient()

    def _google_models(self, api_key: str) -> set[str]:
        data = self.http.get_json(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
            timeout=8,
        )
        rows = data.get("models", []) if isinstance(data, dict) else []
        return {str(row.get("name") or "").split("/", 1)[-1] for row in rows if isinstance(row, dict)}

    def _google_project_is_unbilled(self, project_id: str, access_token: str) -> bool:
        data = self.http.get_json(
            f"https://cloudbilling.googleapis.com/v1/projects/{project_id}/billingInfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=8,
        )
        # Free-tier execution is only authorized when Cloud Billing itself says no
        # billing account is enabled for the declared API-key project.
        return isinstance(data, dict) and data.get("billingEnabled") is False

    def _google_rate_probe(self, api_key: str, model: str) -> bool:
        # This one-token inference probe runs only after the project is proven unbilled,
        # so it cannot silently cross into paid usage. A 429/auth/model failure is unusable.
        try:
            data, _, status = self.http.request_json(
                "POST",
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
                body={
                    "contents": [{"role": "user", "parts": [{"text": "Reply with OK."}]}],
                    "generationConfig": {"maxOutputTokens": 1, "temperature": 0},
                },
                timeout=10,
            )
            return status == 200 and isinstance(data, dict)
        except Exception:
            return False

    def google_health(self, model: str) -> ProviderHealth:
        key = os.getenv("GEMINI_API_KEY", "").strip()
        project_id = os.getenv("GEMINI_FREE_TIER_PROJECT_ID", "").strip()
        gcp_token = os.getenv("GCP_ACCESS_TOKEN", "").strip()
        declared_tier = os.getenv("GEMINI_USAGE_TIER", "").strip().lower()
        is_allowed = model in self.GEMMA4_ALLOWLIST | self.GEMINI_FREE_ALLOWLIST
        if not key:
            return ProviderHealth("gemini-api", model, False, False, False, False, False, "NO_CREDENTIAL")
        if declared_tier != "free" or not project_id or not gcp_token:
            return ProviderHealth("gemini-api", model, True, False, False, False, False, "FREE_TIER_PROJECT_BILLING_PROOF_MISSING")
        if not is_allowed:
            return ProviderHealth("gemini-api", model, True, False, False, False, False, "MODEL_NOT_ZERO_COST_ALLOWLISTED")
        try:
            unbilled = self._google_project_is_unbilled(project_id, gcp_token)
        except Exception as exc:
            return ProviderHealth("gemini-api", model, True, False, False, False, False, f"BILLING_PROBE_FAILED:{type(exc).__name__}")
        if not unbilled:
            return ProviderHealth("gemini-api", model, True, False, False, False, False, "BILLING_ENABLED_FAIL_CLOSED")
        try:
            available = model in self._google_models(key)
        except Exception as exc:
            return ProviderHealth("gemini-api", model, True, False, True, False, False, f"MODEL_PROBE_FAILED:{type(exc).__name__}")
        if not available:
            return ProviderHealth("gemini-api", model, True, False, True, False, False, "MODEL_CURRENTLY_UNAVAILABLE")
        rate_ok = self._google_rate_probe(key, model)
        return ProviderHealth(
            "gemini-api", model, True, True, True, rate_ok, rate_ok,
            "PASS_ZERO_COST_LIVE_PROBE" if rate_ok else "FREE_TIER_RATE_OR_INFERENCE_UNUSABLE",
        )

    @classmethod
    def _fresh_attestation(cls, value: str) -> bool:
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)
            return timedelta(0) <= age <= cls.VERIFICATION_MAX_AGE
        except Exception:
            return False

    def _opencode_rate_probe(self, api_key: str, model: str) -> bool:
        try:
            data, _, status = self.http.request_json(
                "POST",
                "https://opencode.ai/zen/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                body={"model": model, "messages": [{"role": "user", "content": "Reply OK."}], "max_tokens": 1},
                timeout=10,
            )
            return status == 200 and isinstance(data, dict)
        except Exception:
            return False

    def opencode_health(self) -> ProviderHealth:
        key = os.getenv("OPENCODE_API_KEY", "").strip()
        model = os.getenv("ATM_OPENCODE_FREE_MODEL", "deepseek-v4-flash-free").strip()
        verified = os.getenv("ATM_OPENCODE_ZERO_COST_VERIFIED", "").strip().lower() == "true"
        verified_at = os.getenv("ATM_OPENCODE_ZERO_COST_VERIFIED_AT", "").strip()
        if not key:
            return ProviderHealth("opencode-free", model, False, False, False, False, False, "NO_CREDENTIAL")
        if model not in self.OPENCODE_FREE_ALLOWLIST:
            return ProviderHealth("opencode-free", model, True, False, False, False, False, "MODEL_NOT_ZERO_COST_ALLOWLISTED")
        if not verified or not self._fresh_attestation(verified_at):
            return ProviderHealth("opencode-free", model, True, False, False, False, False, "CURRENT_ZERO_COST_METADATA_NOT_PROVEN")
        rate_ok = self._opencode_rate_probe(key, model)
        return ProviderHealth(
            "opencode-free", model, True, rate_ok, True, rate_ok, rate_ok,
            "PASS_ZERO_COST_LIVE_PROBE" if rate_ok else "FREE_MODEL_RATE_OR_INFERENCE_UNUSABLE",
        )

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
