from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from atm_core.zero_cost_model import ZeroCostModelGate


class FakeGoogleHealthHttp:
    def __init__(self, *, billing_enabled=False, model="gemini-2.5-flash-lite", generation_ok=True):
        self.billing_enabled = billing_enabled
        self.model = model
        self.generation_ok = generation_ok
        self.calls = []

    def get_json(self, url, *, headers=None, timeout=8):
        self.calls.append(("GET", url, headers))
        if "cloudbilling.googleapis.com" in url:
            return {"billingEnabled": self.billing_enabled}
        if "generativelanguage.googleapis.com" in url and url.endswith("/models?key=test-key"):
            return {"models": [{"name": f"models/{self.model}"}]}
        raise AssertionError(url)

    def request_json(self, method, url, *, headers=None, body=None, timeout=10):
        self.calls.append((method, url, headers))
        if ":generateContent?key=test-key" in url:
            if not self.generation_ok:
                raise RuntimeError("429")
            return {"candidates": [{"content": {"parts": [{"text": "OK"}]}}]}, {}, 200
        raise AssertionError(url)


class R1HardeningTests(unittest.TestCase):
    def _google_env(self):
        return {
            "GEMINI_API_KEY": "test-key",
            "GEMINI_USAGE_TIER": "free",
            "GEMINI_FREE_TIER_PROJECT_ID": "radar-oportunidades",
            "GCP_ACCESS_TOKEN": "gcp-read-token",
        }

    def test_gemini_paid_only_model_is_not_allowlisted(self):
        self.assertNotIn("gemini-2.5-flash", ZeroCostModelGate.GEMINI_FREE_ALLOWLIST)
        self.assertIn("gemini-2.5-flash-lite", ZeroCostModelGate.GEMINI_FREE_ALLOWLIST)

    def test_google_execution_requires_cloud_billing_disabled_and_live_rate_probe(self):
        with patch.dict(os.environ, self._google_env(), clear=True):
            healthy = ZeroCostModelGate(FakeGoogleHealthHttp()).google_health("gemini-2.5-flash-lite")
            self.assertTrue(healthy.zero_cost_tier_proven)
            self.assertTrue(healthy.rate_limit_usable)
            self.assertTrue(healthy.usable)

            paid = ZeroCostModelGate(FakeGoogleHealthHttp(billing_enabled=True)).google_health("gemini-2.5-flash-lite")
            self.assertFalse(paid.usable)
            self.assertEqual(paid.reason, "BILLING_ENABLED_FAIL_CLOSED")

            limited = ZeroCostModelGate(FakeGoogleHealthHttp(generation_ok=False)).google_health("gemini-2.5-flash-lite")
            self.assertFalse(limited.usable)
            self.assertEqual(limited.reason, "FREE_TIER_RATE_OR_INFERENCE_UNUSABLE")

    def test_google_missing_billing_proof_degrades_execution_only(self):
        env = {"GEMINI_API_KEY": "test-key", "GEMINI_USAGE_TIER": "free"}
        with patch.dict(os.environ, env, clear=True):
            health = ZeroCostModelGate(FakeGoogleHealthHttp()).google_health("gemini-2.5-flash-lite")
            self.assertFalse(health.usable)
            self.assertEqual(health.reason, "FREE_TIER_PROJECT_BILLING_PROOF_MISSING")

    def test_cloudflare_has_sanitized_money_board_and_raw_byte_webhook_boundary(self):
        text = (Path(__file__).resolve().parents[1] / "worker" / "src" / "index.js").read_text(encoding="utf-8")
        self.assertIn("/api/money-board", text)
        for key in ("pending_usd", "accepted_usd", "settled_usd", "withdrawable_usd"):
            self.assertIn(key, text)
        self.assertIn("request.arrayBuffer()", text)
        self.assertIn("SAFE_MONEY", text)
        self.assertNotIn("seed phrase", text.lower())

    def test_superteam_uses_documented_listing_and_comment_monitoring_surfaces(self):
        text = (Path(__file__).resolve().parents[1] / "src" / "atm_core" / "superteam_agent.py").read_text(encoding="utf-8")
        self.assertIn("/api/agents/listings/details/", text)
        self.assertIn("/api/agents/comments/", text)
        self.assertIn("/api/agents/comments/create", text)
        self.assertIn("/api/agents/submissions/create", text)
        self.assertIn("/api/agents/submissions/update", text)
        self.assertNotIn('f"{self.base_url}/api/agents/submissions/{urllib.parse.quote', text)


if __name__ == "__main__":
    unittest.main()
