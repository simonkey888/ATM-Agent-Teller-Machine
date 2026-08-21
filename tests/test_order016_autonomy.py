from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from atm_core.autonomy_fabric import (
    CapabilityGap, CapabilityGapLedger, DemandClass, DeterministicSupervisor, EventKind,
    Falsifier, PromotionEvidence, ScoutFabric, SkillForge, WorkerRole, classify_gap, deterministic_promotion,
)
from atm_core.durable_doctor import DurableDoctor


class Order016AutonomyTests(unittest.TestCase):
    def test_event_router_is_deterministic_and_non_mutating(self):
        supervisor = DeterministicSupervisor()
        jobs = supervisor.dispatch(EventKind.DISCOVERY_TICK, {"tick": 1})
        self.assertEqual([job.role for job in jobs], [WorkerRole.SCOUT])
        self.assertTrue(supervisor.single_controller)
        self.assertFalse(supervisor.cash_canon_authority)
        self.assertFalse(supervisor.effect_authority)
        self.assertIn("ISOLATED_PROCESS", jobs[0].spec.network_policy)
        self.assertFalse(jobs[0].spec.mutation_authority)

    def test_gap_event_routes_only_to_secretless_forge_receipt_consumer(self):
        jobs = DeterministicSupervisor().dispatch(EventKind.CAPABILITY_GAP_OBSERVED, {"gap": "x"})
        self.assertEqual([job.role for job in jobs], [WorkerRole.SKILL_FORGE])
        self.assertEqual(jobs[0].spec.network_policy, "SECRETLESS_SANDBOX_ONLY")
        self.assertEqual(jobs[0].spec.tool_allowlist, ("sandbox_receipt_read",))

    def test_in_process_scout_falsifier_and_forge_are_disabled(self):
        with self.assertRaisesRegex(RuntimeError, "IN_PROCESS_SCOUT_DISABLED"):
            ScoutFabric().scan({}, [], 1)
        with self.assertRaisesRegex(RuntimeError, "IN_PROCESS_FALSIFIER_DISABLED"):
            Falsifier().verify(object(), object())
        with self.assertRaisesRegex(RuntimeError, "IN_PROCESS_SKILL_FORGE_DISABLED"):
            SkillForge().evaluate("x")

    def test_doctor_has_no_arbitrary_callable_recovery_api(self):
        with tempfile.TemporaryDirectory() as td:
            doctor = DurableDoctor(Path(td) / "doctor.sqlite3")
            try:
                self.assertFalse(hasattr(doctor, "recover"))
                self.assertEqual(doctor.choose("PROVIDER_429"), "ROTATE_TO_VERIFIED_FREE_PROVIDER")
                self.assertIn("CHANGE_HEALTH_SLO_TO_HIDE_FAILURE", doctor.FORBIDDEN)
            finally:
                doctor.close()

    def test_gap_ledger_dedupes_and_is_not_money_truth(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = CapabilityGapLedger(Path(d) / "gaps.sqlite3")
            try:
                gap = CapabilityGap(
                    canonical_opportunity_id="task:1", source="taskmarket", external_id="1",
                    observed_at="2026-08-20T00:00:00+00:00", fresh_object_hash="a" * 64,
                    work_class_candidate="SPREADSHEET_TRANSFORM", requirements=("xlsx",),
                    net_reward_if_verified="25", funding_verification_state="VERIFIED", competition_if_known=1,
                    rejection_reason="WORK_CLASS_NOT_FIXTURE_QUALIFIED", capability_gap_reason="WORK_CLASS_NOT_FIXTURE_QUALIFIED",
                    evidence_refs=("authoritative:1",), currentness_state="CURRENT", demand_class=DemandClass.VERIFIED_MISSED_CASH,
                )
                self.assertTrue(ledger.record(gap))
                self.assertFalse(ledger.record(gap))
                self.assertFalse(ledger.stats()["earnings_authority"])
            finally:
                ledger.close()

    def test_gap_priority_never_calls_synthetic_fixture_missed_cash(self):
        self.assertEqual(classify_gap(synthetic=True, current=True, funding_verified=True), DemandClass.SYNTHETIC_FIXTURE)
        self.assertEqual(classify_gap(synthetic=False, current=False, funding_verified=True), DemandClass.STALE_SIGNAL)
        self.assertEqual(classify_gap(synthetic=False, current=True, funding_verified=True), DemandClass.VERIFIED_MISSED_CASH)

    def good_promotion_evidence(self):
        return PromotionEvidence(
            owner_cost_usd="0", commercial_use_ok=True, supply_chain_pinned=True, no_secret_requirement=True,
            sandbox_pass=True, happy_pass=True, edge_pass=True, adversarial_pass=True,
            checker_independent_enough=True, artifact_contract_pass=True, resource_limit_pass=True,
            no_existing_capability_duplicate=True, license_verified=True, tool_contract_explicit=True,
            network_contract_explicit=True,
        )

    def test_deterministic_promotion_passes_complete_and_fails_drift(self):
        self.assertTrue(deterministic_promotion("DEMO_CAP", self.good_promotion_evidence()).promoted)
        base = self.good_promotion_evidence()
        drift = PromotionEvidence(**{**base.__dict__, "supply_chain_pinned": False, "license_verified": False})
        decision = deterministic_promotion("MALICIOUS", drift)
        self.assertFalse(decision.promoted)
        self.assertIn("SUPPLY_CHAIN_PINNED", decision.reasons)
        self.assertIn("LICENSE_VERIFIED", decision.reasons)

    def test_paid_promotion_fails_closed(self):
        base = self.good_promotion_evidence()
        paid = PromotionEvidence(**{**base.__dict__, "owner_cost_usd": "0.01"})
        self.assertFalse(deterministic_promotion("PAID", paid).promoted)


if __name__ == "__main__":
    unittest.main()
