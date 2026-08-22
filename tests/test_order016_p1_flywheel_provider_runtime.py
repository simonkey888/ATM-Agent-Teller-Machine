from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from atm_core.autonomy_fabric import CapabilityGapLedger
from atm_core.capability_flywheel import ensure_synthetic_plumbing_if_no_real_gap, run_flywheel_once
from atm_core.capability_registry_v3 import CapabilityRegistryV3Store
from atm_core.provider_fabric import DurableFreeProviderFabric
from atm_core.zero_cost_model import ProviderHealth


class Order016P1FlywheelProviderRuntimeTests(unittest.TestCase):
    def test_synthetic_plumbing_runs_external_sandbox_and_cannot_enable_economic_execution(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ledger = root / "gaps.sqlite3"
            flywheel = root / "flywheel.sqlite3"
            episodes = root / "episodes.sqlite3"
            registry = root / "registry.sqlite3"
            self.assertTrue(ensure_synthetic_plumbing_if_no_real_gap(ledger_path=ledger, flywheel_path=flywheel, episodes_path=episodes))
            result = run_flywheel_once(flywheel_path=flywheel, registry_path=registry)
            self.assertEqual(result["state"], "SYNTHETIC_PROMOTION_PROOF")
            self.assertTrue(result["synthetic_non_economic"])
            self.assertFalse(result["activation_enabled"])
            self.assertEqual(len(result["sandbox_receipt_digest"]), 64)
            store = CapabilityRegistryV3Store(registry)
            try:
                rows = store.rows()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["synthetic"], 1)
                self.assertEqual(rows[0]["activation_enabled"], 0)
                self.assertEqual(store.enabled_ids(), ())
            finally:
                store.close()
            gap_ledger = CapabilityGapLedger(ledger)
            try:
                self.assertEqual(gap_ledger.stats()["synthetic"], 1)
                self.assertEqual(gap_ledger.stats()["verified_missed_cash_signals"], 0)
            finally:
                gap_ledger.close()

    def test_provider_never_healthy_from_config_and_429_failover_survives_restart(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "providers.sqlite3"
            fabric = DurableFreeProviderFabric(path)
            try:
                fabric.ensure_route("gemini-api-free", "model-a")
                self.assertEqual(fabric.status("gemini-api-free", "model-a")["state"], "UNVERIFIED")
                health_a = ProviderHealth(provider="google-gemini-api", model="model-a", credential_present=True, model_available=True, zero_cost_tier_proven=True, rate_limit_usable=True, usable=True, reason="LIVE_ZERO_COST_RATE_PROBE_OK")
                health_b = ProviderHealth(provider="google-gemini-api", model="model-b", credential_present=True, model_available=True, zero_cost_tier_proven=True, rate_limit_usable=True, usable=True, reason="LIVE_ZERO_COST_RATE_PROBE_OK")
                fabric.record_probe("gemini-api-free", "model-a", health_a)
                fabric.record_probe("gemini-api-free", "model-b", health_b)
                self.assertEqual(fabric.route([("gemini-api-free", "model-a"), ("gemini-api-free", "model-b")])["model_id"], "model-a")
                fabric.record_failure("gemini-api-free", "model-a", "PROVIDER_429")
                routed = fabric.route([("gemini-api-free", "model-a"), ("gemini-api-free", "model-b")])
                self.assertEqual(routed["model_id"], "model-b")
                self.assertFalse(routed["paid_fallback"])
            finally:
                fabric.close()
            restarted = DurableFreeProviderFabric(path)
            try:
                self.assertFalse(restarted.status("gemini-api-free", "model-a")["available"])
                self.assertEqual(restarted.route([("gemini-api-free", "model-a"), ("gemini-api-free", "model-b")])["model_id"], "model-b")
            finally:
                restarted.close()


if __name__ == "__main__":
    unittest.main()
