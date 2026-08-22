from __future__ import annotations

import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from atm_core.maker_router import MakerRouteError, MakerRouteUnavailable, ZeroCostMakerRouter
from atm_core.provider_fabric import DurableFreeProviderFabric
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
    def __init__(self, *, first=True, second=True):
        self.first = first
        self.second = second
    def google_health(self, model):
        ready = self.first if model == "gemma-4-31b-it" else self.second
        return ProviderHealth("gemini-api", model, True, ready, ready, ready, ready, "PASS" if ready else "BLOCKED")


class MakerFallbackGateTests(unittest.TestCase):
    def router(self, td, *, gate=None, opener=None):
        return ZeroCostMakerRouter(gate=gate or FakeGate(), opener=opener, provider_state_path=Path(td) / "providers.sqlite3")

    def test_only_verified_free_gemini_routes_exist(self):
        with tempfile.TemporaryDirectory() as td:
            routes = self.router(td).routes("gemma-4-31b-it", ("gemma-4-26b-a4b-it",))
        self.assertEqual([row.provider_id for row in routes], ["gemini-api-free", "gemini-api-free"])
        self.assertNotIn("opencode-free", [row.provider for row in routes])
        self.assertNotIn("github-models", [row.provider for row in routes])
        self.assertTrue(all(row.billed_usd == "0" for row in routes if row.ready))

    def test_429_persists_circuit_and_advances_to_next_verified_free_route(self):
        calls = []
        def opener(req, timeout=0):
            del timeout
            calls.append(req.full_url)
            if "gemma-4-31b-it" in req.full_url:
                raise urllib.error.HTTPError(req.full_url, 429, "rate", {}, None)
            return FakeResponse({"candidates": [{"content": {"parts": [{"text": "FALLBACK_OK"}]}}]})
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"GEMINI_API_KEY": "g"}, clear=False):
            path = Path(td) / "providers.sqlite3"
            result = self.router(td, opener=opener).generate("x", preferred_gemini_model="gemma-4-31b-it", gemini_failovers=("gemma-4-26b-a4b-it",), max_output_tokens=16)
            self.assertEqual(result.text, "FALLBACK_OK")
            self.assertEqual(result.route.model, "gemma-4-26b-a4b-it")
            self.assertEqual(len(calls), 2)
            restarted = DurableFreeProviderFabric(path)
            try:
                first = restarted.status("gemini-api-free", "gemma-4-31b-it")
                self.assertFalse(first["available"])
                self.assertEqual(first["state"], "COOLDOWN")
                self.assertEqual(restarted.route([("gemini-api-free", "gemma-4-31b-it"), ("gemini-api-free", "gemma-4-26b-a4b-it")])["model_id"], "gemma-4-26b-a4b-it")
            finally:
                restarted.close()

    def test_semantic_failure_advances_to_next_ready_route(self):
        calls = []
        def opener(req, timeout=0):
            del timeout
            calls.append(req.full_url)
            text = "not-json" if "gemma-4-31b-it" in req.full_url else '{"status":"PASS","notes":[]}'
            return FakeResponse({"candidates": [{"content": {"parts": [{"text": text}]}}]})
        def validator(text):
            try:
                obj = json.loads(text)
            except json.JSONDecodeError as exc:
                raise MakerRouteError("semantic") from exc
            if obj.get("status") != "PASS":
                raise MakerRouteError("semantic")
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"GEMINI_API_KEY": "g"}, clear=False):
            result = self.router(td, opener=opener).generate("x", preferred_gemini_model="gemma-4-31b-it", gemini_failovers=("gemma-4-26b-a4b-it",), max_output_tokens=64, structured_json=True, semantic_validator=validator)
        self.assertEqual(result.route.model, "gemma-4-26b-a4b-it")
        self.assertEqual(len(calls), 2)

    def test_one_same_route_repair_then_next_route_respects_global_bound(self):
        calls = []
        def opener(req, timeout=0):
            del timeout
            calls.append(req.full_url)
            text = "bad" if len(calls) <= 2 else '{"status":"PASS"}'
            return FakeResponse({"candidates": [{"content": {"parts": [{"text": text}]}}]})
        def validator(text):
            try:
                obj = json.loads(text)
            except json.JSONDecodeError as exc:
                raise MakerRouteError("semantic") from exc
            if obj.get("status") != "PASS":
                raise MakerRouteError("semantic")
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"GEMINI_API_KEY": "g"}, clear=False):
            result = self.router(td, opener=opener).generate("x", preferred_gemini_model="gemma-4-31b-it", gemini_failovers=("gemma-4-26b-a4b-it",), max_output_tokens=64, structured_json=True, semantic_validator=validator, repair_prompt="repair")
        self.assertEqual(result.route.model, "gemma-4-26b-a4b-it")
        self.assertEqual(len(calls), 3)

    def test_all_verified_free_routes_down_degrades_without_paid_fallback(self):
        calls = []
        def opener(req, timeout=0):
            del timeout
            calls.append(req.full_url)
            raise urllib.error.URLError("down")
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"GEMINI_API_KEY": "g", "OPENCODE_API_KEY": "must-not-route"}, clear=False):
            with self.assertRaisesRegex(MakerRouteUnavailable, "DEGRADED_DETERMINISTIC_SEARCH"):
                self.router(td, opener=opener).generate("x", preferred_gemini_model="gemma-4-31b-it", gemini_failovers=("gemma-4-26b-a4b-it",), max_output_tokens=4)
            status = DurableFreeProviderFabric(Path(td) / "providers.sqlite3")
            try:
                self.assertTrue(all(not row["available"] for row in status.rows()))
            finally:
                status.close()
        self.assertEqual(len(calls), 2)

    def test_model_request_never_receives_marketplace_or_payout_secrets(self):
        captured = []
        def opener(req, timeout=0):
            del timeout
            captured.append("\n".join([req.full_url, str(dict(req.header_items())), (req.data or b"").decode("utf-8", errors="replace")]))
            return FakeResponse({"candidates": [{"content": {"parts": [{"text": "SAFE"}]}}]})
        secrets = {"GEMINI_API_KEY": "model-key-only", "TASKMARKET_KEYSTORE_B64": "forbidden-keystore", "TASKBOUNTY_API_KEY": "forbidden-taskbounty", "MOLTJOBS_API_KEY": "forbidden-moltjobs", "SUPERTEAM_AGENT_API_KEY": "forbidden-superteam", "AGENTHANSA_API_KEY": "forbidden-agenthansa", "OPENCODE_API_KEY": "forbidden-unverified-route"}
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, secrets, clear=False):
            result = self.router(td, gate=FakeGate(second=False), opener=opener).generate("safe prompt", preferred_gemini_model="gemma-4-31b-it", gemini_failovers=("gemma-4-26b-a4b-it",), max_output_tokens=8)
        self.assertEqual(result.text, "SAFE")
        wire = "\n".join(captured)
        for key, value in secrets.items():
            if key == "GEMINI_API_KEY":
                continue
            self.assertNotIn(value, wire, key)


if __name__ == "__main__":
    unittest.main()
