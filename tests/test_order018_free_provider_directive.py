from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from atm_core.order018_free_provider_directive import CANDIDATES, _persist_dynamic_probe, probe_candidate
from atm_core.provider_fabric import DurableFreeProviderFabric
from atm_core.provider_registry import admitted_provider_ids, public_registry


class Order018FreeProviderDirectiveTests(unittest.TestCase):
    def test_directive_candidates_are_visible_but_not_config_admitted(self):
        registry = public_registry()
        ids = {row["provider_id"] for row in registry["providers"]}
        for expected in {
            "opencode-zen-x-preview-f-free",
            "openrouter-stealth-ox-alpha",
            "openrouter-free-router",
            "tokenrouter-deepseek-v4-pro",
            "gorouter-opus-5",
        }:
            self.assertIn(expected, ids)
        self.assertEqual(admitted_provider_ids(), ("gemini-api-free",))
        self.assertEqual(registry["discovery_feeds"][0]["feed_id"], "ripienaar/free-for-dev")
        self.assertFalse(registry["discovery_feeds"][0]["activation_authority"])

    def test_promo_and_dynamic_alias_candidates_can_never_self_activate(self):
        by_id = {row.provider_id: row for row in CANDIDATES}
        for provider_id in ("tokenrouter-deepseek-v4-pro", "gorouter-opus-5", "openrouter-free-router"):
            evidence = probe_candidate(by_id[provider_id])
            self.assertEqual(evidence["state"], "CANDIDATE_ONLY")
            self.assertFalse(evidence["hard_zero_spend_proven"])
            self.assertFalse(by_id[provider_id].activation_capable)

    def test_no_credential_never_becomes_healthy(self):
        candidate = next(row for row in CANDIDATES if row.provider_id == "opencode-zen-x-preview-f-free")
        with patch.dict("os.environ", {}, clear=True):
            evidence = probe_candidate(candidate)
        self.assertEqual(evidence["state"], "NO_CREDENTIAL")
        self.assertFalse(evidence["hard_zero_spend_proven"])

    def test_alias_or_nonzero_price_fails_closed_before_generation(self):
        candidate = next(row for row in CANDIDATES if row.provider_id == "opencode-zen-x-preview-f-free")
        with patch.dict("os.environ", {candidate.credential_env: "secret-test-key"}, clear=True), patch(
            "atm_core.order018_free_provider_directive._request_json",
            return_value=({"data": [{"id": candidate.model_id, "pricing": {"input": "0", "output": "0.01"}}]}, 200),
        ) as request:
            evidence = probe_candidate(candidate)
        self.assertEqual(evidence["reason"], "EXACT_ZERO_PRICE_NOT_PROVEN")
        self.assertFalse(evidence["hard_zero_spend_proven"])
        self.assertEqual(request.call_count, 1)

    def test_live_zero_price_exact_model_and_terms_can_promote_only_runtime_route(self):
        candidate = next(row for row in CANDIDATES if row.provider_id == "opencode-zen-x-preview-f-free")
        calls = [
            ({"data": [{"id": candidate.model_id, "pricing": {"input": "0", "output": "0"}}]}, 200),
            ({"model": candidate.model_id, "choices": [{"message": {"content": "OK"}}], "usage": {"cost": "0"}}, 200),
        ]
        with patch.dict("os.environ", {candidate.credential_env: "secret-test-key"}, clear=True), patch(
            "atm_core.order018_free_provider_directive._request_json", side_effect=calls
        ), patch(
            "atm_core.order018_free_provider_directive._terms_proof",
            return_value={"fresh": True, "sha256": "a" * 64, "usable_for_paid_task": True},
        ):
            evidence = probe_candidate(candidate)
        self.assertTrue(evidence["hard_zero_spend_proven"])
        self.assertEqual(evidence["state"], "HEALTHY")
        with tempfile.TemporaryDirectory() as td:
            fabric = DurableFreeProviderFabric(Path(td) / "providers.sqlite3")
            try:
                _persist_dynamic_probe(fabric, candidate, evidence)
                routed = fabric.route([(candidate.provider_id, candidate.model_id)])
            finally:
                fabric.close()
        self.assertEqual(routed["state"], "READY")
        self.assertEqual(routed["provider_id"], candidate.provider_id)
        self.assertFalse(routed["paid_fallback"])

    def test_missing_price_metadata_is_not_free_proof(self):
        candidate = next(row for row in CANDIDATES if row.provider_id == "openrouter-stealth-ox-alpha")
        with patch.dict("os.environ", {candidate.credential_env: "secret-test-key"}, clear=True), patch(
            "atm_core.order018_free_provider_directive._request_json",
            return_value=({"data": [{"id": candidate.model_id}]}, 200),
        ):
            evidence = probe_candidate(candidate)
        self.assertFalse(evidence["hard_zero_spend_proven"])
        self.assertEqual(evidence["reason"], "EXACT_ZERO_PRICE_NOT_PROVEN")


if __name__ == "__main__":
    unittest.main()
