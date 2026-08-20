from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from atm_core.zero_cost_model import ZeroCostModelGate


class FakeGemmaHttp:
    def __init__(self, model="gemma-4-31b-it", generation_ok=True):
        self.model = model
        self.generation_ok = generation_ok
        self.urls = []

    def get_json(self, url, *, headers=None, timeout=8):
        self.urls.append(url)
        if "generativelanguage.googleapis.com" in url and "/models?key=test-key" in url:
            return {"models": [{"name": f"models/{self.model}"}]}
        raise AssertionError(url)

    def request_json(self, method, url, *, headers=None, body=None, timeout=10):
        self.urls.append(url)
        if not self.generation_ok:
            raise RuntimeError("429")
        return {"candidates": [{"content": {"parts": [{"text": "OK"}]}}]}, {}, 200


class Order009R1ExecutorTests(unittest.TestCase):
    def test_gemma4_does_not_require_billable_gcp_project(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=True):
            health = ZeroCostModelGate(FakeGemmaHttp()).google_health("gemma-4-31b-it")
        self.assertTrue(health.usable)
        self.assertTrue(health.zero_cost_tier_proven)
        self.assertEqual(health.reason, "PASS_GEMMA4_FREE_ONLY_LIVE_PROBE")

    def test_gemma4_rate_failure_fails_closed(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=True):
            health = ZeroCostModelGate(FakeGemmaHttp(generation_ok=False)).google_health("gemma-4-31b-it")
        self.assertFalse(health.usable)
        self.assertEqual(health.reason, "GEMMA4_FREE_INFERENCE_UNUSABLE")

    def test_paid_fallback_remains_disabled(self):
        self.assertFalse(ZeroCostModelGate.PAID_FALLBACK)
        self.assertEqual(
            ZeroCostModelGate.GEMMA4_ALLOWLIST,
            {"gemma-4-31b-it", "gemma-4-26b-a4b-it"},
        )


if __name__ == "__main__":
    unittest.main()
