from __future__ import annotations

import json
import os
import unittest
import urllib.error
from unittest.mock import patch

from atm_core.maker_router import ZeroCostMakerRouter
from atm_core.zero_cost_model import ProviderHealth


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class FakeGate:
    def __init__(self, *, gemini_ready=True, second_gemini_ready=False, opencode_ready=True):
        self.gemini_ready = gemini_ready
        self.second_gemini_ready = second_gemini_ready
        self.opencode_ready = opencode_ready

    @staticmethod
    def _health(provider, model, ready, reason):
        return ProviderHealth(provider, model, True, ready, ready, ready, ready, reason)

    def google_health(self, model):
        ready = self.gemini_ready if model == "gemma-4-31b-it" else self.second_gemini_ready
        return self._health("gemini-api", model, ready, "PASS" if ready else "BLOCKED")

    def opencode_health(self):
        return self._health("opencode-free", "deepseek-v4-flash-free", self.opencode_ready, "PASS" if self.opencode_ready else "BLOCKED")


class MakerFallbackGateTests(unittest.TestCase):
    def test_github_models_is_retired_and_never_ready(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "token-present"}, clear=False):
            routes = ZeroCostMakerRouter(gate=FakeGate()).routes("gemma-4-31b-it", ("gemma-4-26b-a4b-it",))
        github = next(row for row in routes if row.provider == "github-models")
        self.assertFalse(github.ready)
        self.assertFalse(github.zero_cost_proven)
        self.assertIsNone(github.billed_usd)
        self.assertEqual(github.reason, "GITHUB_MODELS_RETIRED_2026_07_30")

    def test_bounded_failover_from_gemini_to_existing_opencode_route(self):
        calls = []

        def opener(req, timeout=0):
            del timeout
            calls.append(req.full_url)
            if "generativelanguage.googleapis.com" in req.full_url:
                raise urllib.error.URLError("transient")
            return FakeResponse({"choices": [{"message": {"content": "FALLBACK_OK"}}]})

        router = ZeroCostMakerRouter(gate=FakeGate(), opener=opener)
        with patch.dict(os.environ, {"GEMINI_API_KEY": "g", "OPENCODE_API_KEY": "o"}, clear=False):
            result = router.generate(
                "return fallback",
                preferred_gemini_model="gemma-4-31b-it",
                gemini_failovers=("gemma-4-26b-a4b-it",),
                max_output_tokens=16,
            )
        self.assertEqual(result.text, "FALLBACK_OK")
        self.assertEqual(result.route.provider, "opencode-free")
        self.assertEqual(result.route.billed_usd, "0")
        self.assertEqual(len(calls), 2)

    def test_router_attempts_at_most_three_ready_routes(self):
        calls = []

        def opener(req, timeout=0):
            del timeout
            calls.append(req.full_url)
            raise urllib.error.URLError("down")

        router = ZeroCostMakerRouter(
            gate=FakeGate(gemini_ready=True, second_gemini_ready=True, opencode_ready=True),
            opener=opener,
        )
        with patch.dict(os.environ, {"GEMINI_API_KEY": "g", "OPENCODE_API_KEY": "o"}, clear=False):
            with self.assertRaisesRegex(Exception, "no READY zero-cost maker route"):
                router.generate(
                    "x",
                    preferred_gemini_model="gemma-4-31b-it",
                    gemini_failovers=("gemma-4-26b-a4b-it",),
                    max_output_tokens=4,
                )
        self.assertEqual(len(calls), 3)

    def test_model_request_never_receives_marketplace_or_payout_secrets(self):
        captured = []

        def opener(req, timeout=0):
            del timeout
            captured.append(
                "\n".join(
                    [
                        req.full_url,
                        str(dict(req.header_items())),
                        (req.data or b"").decode("utf-8", errors="replace"),
                    ]
                )
            )
            return FakeResponse({"candidates": [{"content": {"parts": [{"text": "SAFE"}]}}]})

        secrets = {
            "GEMINI_API_KEY": "model-key-only",
            "TASKMARKET_KEYSTORE_B64": "forbidden-keystore",
            "TASKBOUNTY_API_KEY": "forbidden-taskbounty",
            "MOLTJOBS_API_KEY": "forbidden-moltjobs",
            "SUPERTEAM_AGENT_API_KEY": "forbidden-superteam",
            "AGENTHANSA_API_KEY": "forbidden-agenthansa",
            "GROQ_API_KEY": "optional-not-wired-groq",
            "CEREBRAS_API_KEY": "optional-not-wired-cerebras",
        }
        router = ZeroCostMakerRouter(gate=FakeGate(opencode_ready=False), opener=opener)
        with patch.dict(os.environ, secrets, clear=False):
            result = router.generate(
                "safe prompt",
                preferred_gemini_model="gemma-4-31b-it",
                gemini_failovers=("gemma-4-26b-a4b-it",),
                max_output_tokens=8,
            )
        self.assertEqual(result.text, "SAFE")
        wire = "\n".join(captured)
        for key, value in secrets.items():
            if key == "GEMINI_API_KEY":
                continue
            self.assertNotIn(value, wire, key)


if __name__ == "__main__":
    unittest.main()
