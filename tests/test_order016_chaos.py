from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from atm_core.autonomy_fabric import PromotionEvidence, SkillForge, deterministic_promotion
from atm_core.capability_registry_v3 import REGISTRY_SCHEMA_V3, public_registry
from atm_core.durable_doctor import DurableDoctor
from atm_core.provider_fabric import public_provider_fabric


class Order016FinalChaosTests(unittest.TestCase):
    def good_evidence(self):
        return PromotionEvidence(
            owner_cost_usd="0", commercial_use_ok=True, supply_chain_pinned=True, no_secret_requirement=True,
            sandbox_pass=True, happy_pass=True, edge_pass=True, adversarial_pass=True,
            checker_independent_enough=True, artifact_contract_pass=True, resource_limit_pass=True,
            no_existing_capability_duplicate=True, license_verified=True, tool_contract_explicit=True,
            network_contract_explicit=True,
        )

    def test_registry_v3_preserves_proven_capabilities_and_explicit_contracts(self):
        registry = public_registry()
        self.assertEqual(registry["schema"], REGISTRY_SCHEMA_V3)
        self.assertTrue(registry["preserved_proven_capabilities"])
        self.assertFalse(registry["promotion_policy"]["llm_self_enable"])
        self.assertFalse(registry["promotion_policy"]["forge_self_enable"])
        self.assertFalse(registry["promotion_policy"]["synthetic_can_enable_economic_execution"])
        required = {"executor", "checker", "tools", "network", "license_commercial", "resources", "artifact", "failure"}
        for row in registry["capabilities"]:
            self.assertEqual(set(row["contracts"]), required)

    def test_free_provider_fabric_has_no_paid_fallback_or_auto_overage(self):
        with tempfile.TemporaryDirectory() as td:
            status = public_provider_fabric(Path(td) / "providers.sqlite3")
        self.assertFalse(status["paid_fallback"])
        self.assertFalse(status["auto_overage"])
        self.assertTrue(status["healthy_requires_live_probe"])
        self.assertEqual(status["all_free_down_behavior"], "DEGRADED_DETERMINISTIC_SEARCH")
        self.assertEqual(status["healthy_route_count"], 0)

    def test_license_and_tool_drift_fail_promotion_closed(self):
        base = self.good_evidence()
        self.assertIn("LICENSE_VERIFIED", deterministic_promotion("LICENSE_DRIFT", PromotionEvidence(**{**base.__dict__, "license_verified": False})).reasons)
        self.assertIn("TOOL_CONTRACT_EXPLICIT", deterministic_promotion("TOOL_DRIFT", PromotionEvidence(**{**base.__dict__, "tool_contract_explicit": False})).reasons)

    def test_concurrent_promotion_gate_is_pure_and_registry_immutable(self):
        outcomes = []
        lock = threading.Lock()
        def run():
            decision = deterministic_promotion("CONCURRENT_CANDIDATE", self.good_evidence())
            with lock:
                outcomes.append(decision.promoted)
        threads = [threading.Thread(target=run) for _ in range(8)]
        [thread.start() for thread in threads]
        [thread.join() for thread in threads]
        self.assertEqual(outcomes, [True] * 8)
        self.assertNotIn("CONCURRENT_CANDIDATE", public_registry()["preserved_proven_capabilities"])

    def test_forge_crash_surface_is_removed_from_supervisor_process(self):
        touched = {"value": False}
        def crash(value):
            touched["value"] = True
            raise RuntimeError("forge crash")
        with self.assertRaisesRegex(RuntimeError, "IN_PROCESS_SKILL_FORGE_DISABLED"):
            SkillForge().evaluate("CRASHING", crash, crash, {})
        self.assertFalse(touched["value"])

    def test_scheduler_delay_and_payment_mismatch_cannot_relabel_truth(self):
        with tempfile.TemporaryDirectory() as td:
            doctor = DurableDoctor(Path(td) / "doctor.sqlite3")
            try:
                self.assertEqual(doctor.choose("HEARTBEAT_FAULT"), "REVERIFY_EXACT_HEAD")
                self.assertEqual(doctor.choose("PAYMENT_MISMATCH"), "RETRY_EPHEMERAL_WORKER")
                self.assertFalse(hasattr(doctor, "recover"))
                self.assertIn("CHANGE_HEALTH_SLO_TO_HIDE_FAILURE", doctor.FORBIDDEN)
                self.assertIn("DECLARE_GREEN_BY_LLM", doctor.FORBIDDEN)
            finally:
                doctor.close()


if __name__ == "__main__":
    unittest.main()
