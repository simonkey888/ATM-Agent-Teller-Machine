from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from atm_core.autonomy_fabric import (
    CapabilityGap,
    CapabilityGapLedger,
    DemandClass,
    DeterministicSupervisor,
    Doctor,
    EventKind,
    Falsifier,
    FreeProviderFabric,
    PromotionEvidence,
    ScoutFabric,
    SkillForge,
    WorkerRole,
    classify_gap,
    deterministic_promotion,
)


class Opp:
    canonical_opportunity_id = "lane:1"
    upstream_status = "OPEN"
    funding_proof = {"escrow": "verified"}


class GoodAdapter:
    def discover(self, minimum):
        return [Opp()]

    def fetch_authoritative(self, opportunity):
        return {"id": opportunity.canonical_opportunity_id, "status": "OPEN", "reward": "10"}

    def verify_freshness(self, opportunity, snapshot):
        return None

    def verify_funding(self, opportunity, snapshot):
        return None

    def verify_eligibility(self, opportunity, snapshot):
        return None


class TimeoutAdapter(GoodAdapter):
    def discover(self, minimum):
        raise TimeoutError("source timeout")


class LieAdapter(GoodAdapter):
    def verify_funding(self, opportunity, snapshot):
        raise ValueError("funding mismatch")


class Order016AutonomyTests(unittest.TestCase):
    def test_event_driven_supervisor_is_deterministic_and_non_mutating(self):
        supervisor = DeterministicSupervisor()
        jobs = supervisor.dispatch(EventKind.DISCOVERY_TICK, {"tick": 1})
        self.assertEqual([job.role for job in jobs], [WorkerRole.SCOUT])
        self.assertTrue(supervisor.single_controller)
        self.assertFalse(supervisor.cash_canon_authority)
        self.assertFalse(supervisor.effect_authority)
        self.assertFalse(jobs[0].spec.mutation_authority)

    def test_gap_event_routes_to_secretless_forge_worker_only(self):
        jobs = DeterministicSupervisor().dispatch(EventKind.CAPABILITY_GAP_OBSERVED, {"gap": "x"})
        self.assertEqual([job.role for job in jobs], [WorkerRole.SKILL_FORGE])
        self.assertEqual(jobs[0].spec.network_policy, "SECRETLESS_SANDBOX_ONLY")
        self.assertNotIn("workspace", jobs[0].spec.tool_allowlist)
        self.assertFalse(jobs[0].spec.mutation_authority)

    def test_scout_fabric_isolates_source_timeout(self):
        rows = ScoutFabric(max_concurrent=3).scan({"ok": GoodAdapter(), "timeout": TimeoutAdapter()}, ["ok", "timeout"], 1)
        by_source = {row.source: row for row in rows}
        self.assertEqual(by_source["ok"].state, "OK")
        self.assertEqual(len(by_source["ok"].candidates), 1)
        self.assertEqual(by_source["timeout"].state, "DEGRADED")
        self.assertEqual(by_source["timeout"].error_class, "TimeoutError")

    def test_scout_concurrency_is_hard_bounded(self):
        self.assertEqual(ScoutFabric(max_concurrent=99).max_concurrent, 3)

    def test_falsifier_authoritative_confirm(self):
        result = Falsifier().verify(Opp(), GoodAdapter())
        self.assertEqual(result.verdict, "CONFIRM")
        self.assertEqual(len(result.fresh_object_hash or ""), 64)

    def test_falsifier_kills_source_lie_already_submitted_and_stale(self):
        self.assertEqual(Falsifier().verify(Opp(), LieAdapter()).verdict, "KILL")
        submitted = Falsifier().verify(Opp(), GoodAdapter(), submitted_ids={"lane:1"})
        self.assertEqual(submitted.reason, "ALREADY_SUBMITTED")
        opp = Opp()
        opp.upstream_status = "CLOSED"
        self.assertEqual(Falsifier().verify(opp, GoodAdapter()).reason, "STALE_OR_CLOSED")

    def test_doctor_provider_429_source_lie_and_bounded_crash_recovery(self):
        doctor = Doctor(threshold=2, cooldown_seconds=60)
        self.assertEqual(doctor.choose("PROVIDER_429"), "ROTATE_TO_VERIFIED_FREE_PROVIDER")
        doctor.record_failure("p", "HTTP429")
        self.assertFalse(doctor.record_failure("p", "HTTP429")["available"])
        doctor.quarantine("source-x")
        self.assertFalse(doctor.available("source-x"))
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("crash")
            return "ok"

        result = doctor.recover("WORKER_CRASH", flaky, attempts=2)
        self.assertEqual((result["state"], result["attempt"]), ("RECOVERED", 2))

    def test_doctor_failed_recovery_escalates_without_hiding_red(self):
        result = Doctor().recover("HEARTBEAT_FAULT", lambda: (_ for _ in ()).throw(RuntimeError("red")), attempts=2)
        self.assertEqual(result["state"], "FAULT_ESCALATED")
        self.assertIn("CHANGE_HEALTH_SLO_TO_HIDE_FAILURE", Doctor.FORBIDDEN)

    def test_gap_ledger_dedupes_and_is_not_money_truth(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = CapabilityGapLedger(Path(d) / "gaps.sqlite3")
            try:
                gap = CapabilityGap(
                    canonical_opportunity_id="task:1",
                    source="taskmarket",
                    external_id="1",
                    observed_at="2026-08-20T00:00:00+00:00",
                    fresh_object_hash="a" * 64,
                    work_class_candidate="SPREADSHEET_TRANSFORM",
                    requirements=("xlsx",),
                    net_reward_if_verified="25",
                    funding_verification_state="VERIFIED",
                    competition_if_known=1,
                    rejection_reason="WORK_CLASS_NOT_FIXTURE_QUALIFIED",
                    capability_gap_reason="WORK_CLASS_NOT_FIXTURE_QUALIFIED",
                    evidence_refs=("authoritative:1",),
                    currentness_state="CURRENT",
                    demand_class=DemandClass.VERIFIED_MISSED_CASH,
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

    def test_in_process_skill_forge_is_structurally_disabled(self):
        touched = {"value": False}

        def malicious(_):
            touched["value"] = True
            return "stole-secret"

        with self.assertRaisesRegex(RuntimeError, "IN_PROCESS_SKILL_FORGE_DISABLED"):
            SkillForge().evaluate("MALICIOUS", malicious, malicious, {})
        self.assertFalse(touched["value"])

    def test_free_provider_429_exhaustion_and_unverified_paths(self):
        fabric = FreeProviderFabric(["free-a", "free-b"], failure_threshold=1)
        fabric.record_failure("free-a", "HTTP429")
        self.assertEqual(fabric.route()["provider"], "free-b")
        fabric.record_failure("free-b", "QUOTA_EXHAUSTED")
        route = fabric.route()
        self.assertEqual(route["state"], "DEGRADED_DETERMINISTIC_SEARCH")
        self.assertFalse(route["paid_fallback"])
        other = FreeProviderFabric([])
        other.register_unverified("unknown")
        self.assertEqual(other.available(), ())


if __name__ == "__main__":
    unittest.main()
