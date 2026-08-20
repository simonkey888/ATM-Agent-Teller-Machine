from __future__ import annotations

import threading
import unittest

from atm_core.autonomy_fabric import Doctor, PromotionEvidence, SkillForge, deterministic_promotion
from atm_core.capability_registry_v3 import REGISTRY_SCHEMA_V3, public_registry
from atm_core.provider_fabric import public_provider_fabric


class Order016FinalChaosTests(unittest.TestCase):
    def good_evidence(self):
        return PromotionEvidence(
            owner_cost_usd="0",
            commercial_use_ok=True,
            supply_chain_pinned=True,
            no_secret_requirement=True,
            sandbox_pass=True,
            happy_pass=True,
            edge_pass=True,
            adversarial_pass=True,
            checker_independent_enough=True,
            artifact_contract_pass=True,
            resource_limit_pass=True,
            no_existing_capability_duplicate=True,
            license_verified=True,
            tool_contract_explicit=True,
            network_contract_explicit=True,
        )

    def test_registry_v3_preserves_proven_capabilities_and_explicit_contracts(self):
        registry = public_registry()
        self.assertEqual(registry["schema"], REGISTRY_SCHEMA_V3)
        self.assertTrue(registry["preserved_proven_capabilities"])
        self.assertFalse(registry["promotion_policy"]["llm_self_enable"])
        self.assertFalse(registry["promotion_policy"]["forge_self_enable"])
        self.assertFalse(registry["skill_forge"]["in_process_candidate_execution"])
        required = {"executor", "checker", "tools", "network", "license_commercial", "resources", "artifact", "failure"}
        for row in registry["capabilities"]:
            self.assertEqual(set(row["contracts"]), required)

    def test_free_provider_fabric_has_no_paid_fallback_or_auto_overage(self):
        status = public_provider_fabric()
        self.assertFalse(status["paid_fallback"])
        self.assertFalse(status["auto_overage"])
        self.assertEqual(status["all_free_down_behavior"], "DEGRADED_DETERMINISTIC_SEARCH")
        self.assertNotIn("opencode-zen-free-catalog", status["route"].get("provider") or "")

    def test_license_and_tool_drift_fail_promotion_closed(self):
        base = self.good_evidence()
        drift = PromotionEvidence(**{**base.__dict__, "license_verified": False})
        self.assertIn("LICENSE_VERIFIED", deterministic_promotion("LICENSE_DRIFT", drift).reasons)
        drift = PromotionEvidence(**{**base.__dict__, "tool_contract_explicit": False})
        self.assertIn("TOOL_CONTRACT_EXPLICIT", deterministic_promotion("TOOL_DRIFT", drift).reasons)

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

    def test_scheduler_delay_is_not_relabelled_green(self):
        doctor = Doctor()
        self.assertEqual(doctor.choose("HEARTBEAT_FAULT"), "REVERIFY_EXACT_HEAD")
        self.assertIn("CHANGE_HEALTH_SLO_TO_HIDE_FAILURE", Doctor.FORBIDDEN)

    def test_payment_mismatch_has_no_doctor_recovery_that_writes_money_truth(self):
        doctor = Doctor()
        recovery = doctor.choose("PAYMENT_MISMATCH")
        self.assertEqual(recovery, "RETRY_EPHEMERAL_WORKER")
        forbidden_text = " ".join(sorted(Doctor.FORBIDDEN))
        self.assertNotIn("PAYMENT", " ".join(sorted(Doctor.ALLOWED)))
        self.assertIn("DECLARE_GREEN_BY_LLM", forbidden_text)


if __name__ == "__main__":
    unittest.main()
