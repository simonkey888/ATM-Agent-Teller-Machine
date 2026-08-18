from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from atm_core.learning import CalibrationObservation, HighTicketCandidate, HighTicketRadar, LearningStore, SKILL_PIPELINE, SkillCapsule, SkillStage


class LearningTests(unittest.TestCase):
    def _advance_to_canary(self, store: LearningStore, skill_id: str, *, baseline: float = 10.0) -> SkillCapsule:
        skill = store.put_skill(SkillCapsule(
            skill_id=skill_id,
            capability="ci_triage",
            code_refs=[f"code:{skill_id}"],
            test_refs=[f"test:{skill_id}"],
            fixture_refs=[f"fixture:{skill_id}"],
            baseline_metric=baseline,
            metric_name="median_time_to_valid_artifact",
        ))
        for stage in SKILL_PIPELINE[1:-1]:
            skill = store.advance_skill(skill.skill_id, supervisor="ATM_SUPERVISOR_TEST", evidence_ref=f"{skill_id}:{stage}")
        self.assertEqual(skill.stage, SkillStage.LOW_RISK_CANARY)
        return skill

    def test_skill_pipeline_exact_and_no_self_promotion(self):
        self.assertEqual(SKILL_PIPELINE, ["SOLVE", "DISTILL", "REPLAY", "ADVERSARIAL_TEST", "SHADOW_LIVE", "LOW_RISK_CANARY", "PROMOTE"])
        with tempfile.TemporaryDirectory() as directory:
            store = LearningStore(Path(directory) / "learning.sqlite3")
            skill = store.put_skill(SkillCapsule(
                skill_id="skill-1", capability="ci_triage", code_refs=["code:a"], test_refs=["test:a"], fixture_refs=["fixture:a"],
                baseline_metric=10.0, metric_name="median_time_to_valid_artifact"
            ))
            with self.assertRaisesRegex(ValueError, "self-promote"):
                store.advance_skill(skill.skill_id, supervisor="boqa", evidence_ref="x")
            for stage in SKILL_PIPELINE[1:-1]:
                skill = store.advance_skill(skill.skill_id, supervisor="ATM_SUPERVISOR_TEST", evidence_ref=stage)
                self.assertEqual(skill.stage.value, stage)
            self.assertEqual(skill.stage, SkillStage.LOW_RISK_CANARY)
            with self.assertRaisesRegex(ValueError, "durable measured canary"):
                store.advance_skill(skill.skill_id, supervisor="ATM_SUPERVISOR_TEST", evidence_ref="no-canary", candidate_metric=8.0)
            with self.assertRaisesRegex(ValueError, "all bounded samples"):
                store.record_canary_result(
                    skill.skill_id, supervisor="ATM_SUPERVISOR_TEST", evidence_ref="failed-canary",
                    sample_size=2, success_count=1, candidate_metric=8.0,
                )
            canary = store.record_canary_result(
                skill.skill_id, supervisor="ATM_SUPERVISOR_TEST", evidence_ref="canary-pass",
                sample_size=2, success_count=2, candidate_metric=8.0,
            )
            self.assertEqual(canary.success_count, 2)
            with self.assertRaisesRegex(ValueError, "match durable canary"):
                store.advance_skill(skill.skill_id, supervisor="ATM_SUPERVISOR_TEST", evidence_ref="metric-mismatch", candidate_metric=7.0)
            promoted = store.advance_skill(skill.skill_id, supervisor="ATM_SUPERVISOR_TEST", evidence_ref="promote", candidate_metric=8.0)
            self.assertEqual(promoted.stage, SkillStage.PROMOTE)
            self.assertEqual(promoted.promoted_by, "ATM_SUPERVISOR_TEST")
            self.assertEqual(promoted.candidate_metric, 8.0)
            self.assertEqual(len(store.history(skill.skill_id)), 6)
            store.close()

    def test_durable_canary_without_measurable_improvement_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LearningStore(Path(directory) / "learning.sqlite3")
            skill = self._advance_to_canary(store, "skill-no-improvement", baseline=10.0)
            store.record_canary_result(
                skill.skill_id,
                supervisor="ATM_SUPERVISOR_TEST",
                evidence_ref="canary-measured-but-worse",
                sample_size=2,
                success_count=2,
                candidate_metric=11.0,
            )
            with self.assertRaisesRegex(ValueError, "measurable improvement"):
                store.advance_skill(
                    skill.skill_id,
                    supervisor="ATM_SUPERVISOR_TEST",
                    evidence_ref="reject-non-improving-canary",
                    candidate_metric=11.0,
                )
            self.assertEqual(store.get_skill(skill.skill_id).stage, SkillStage.LOW_RISK_CANARY)
            store.close()

    def test_skill_requires_code_tests_fixtures(self):
        for field in ("code_refs", "test_refs", "fixture_refs"):
            values = dict(skill_id="x", capability="x", code_refs=["c"], test_refs=["t"], fixture_refs=["f"])
            values[field] = []
            with self.assertRaises(ValueError):
                SkillCapsule(**values)

    def test_direct_canary_registration_and_baseline_reregistration_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LearningStore(Path(directory) / "learning.sqlite3")
            with self.assertRaisesRegex(ValueError, "begin at SOLVE"):
                store.put_skill(SkillCapsule(
                    skill_id="skip", capability="ci_triage", code_refs=["c"], test_refs=["t"], fixture_refs=["f"],
                    stage=SkillStage.LOW_RISK_CANARY, baseline_metric=10, metric_name="error_rate"
                ))
            original = store.put_skill(SkillCapsule(
                skill_id="stable", capability="ci_triage", code_refs=["c"], test_refs=["t"], fixture_refs=["f"],
                baseline_metric=10, metric_name="error_rate"
            ))
            with self.assertRaisesRegex(ValueError, "identity/baseline mutation forbidden"):
                store.put_skill(original.model_copy(update={"baseline_metric": 999}))
            with self.assertRaisesRegex(ValueError, "re-registration cannot reset or advance"):
                store.put_skill(original.model_copy(update={"stage": SkillStage.LOW_RISK_CANARY}))
            self.assertEqual(store.get_skill("stable").baseline_metric, 10)
            self.assertEqual(store.get_skill("stable").stage, SkillStage.SOLVE)
            store.close()

    def test_calibration_preserves_unknown_until_real_evidence_and_excludes_synthetic(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LearningStore(Path(directory) / "learning.sqlite3")
            for i in range(3):
                store.observe(CalibrationObservation(
                    observation_id=f"o{i}", source="fixture", opportunity_class="qa",
                    predicted_band="UNKNOWN", observed_outcome="NO_VALID_INGRESS", evidence_refs=[f"e:{i}"], synthetic=True
                ))
            summary = store.calibration_summary("qa")
            self.assertEqual(summary["sample_size"], 0)
            self.assertEqual(summary["synthetic_sample_size"], 3)
            self.assertEqual(summary["posterior_band"], "UNKNOWN")
            self.assertEqual(summary["outcomes"], {})
            self.assertTrue(summary["synthetic_excluded_from_production_metrics"])
            self.assertFalse(summary["fake_precision"])
            store.close()

    def test_high_ticket_radar_is_external_paid_demand_only(self):
        base = dict(
            canonical_opportunity_id="x", source="market", explicit_paid_demand=True, funding_verified=True,
            reward_usd=1000, estimated_hours=6, acceptance_contract_hash="a" * 64,
            payment_evidence_refs=["funding:proof"]
        )
        self.assertEqual(HighTicketRadar.classify(HighTicketCandidate(**base)), "OPTION_BOOK_HIGH_TICKET_SHADOW")
        self.assertEqual(HighTicketRadar.classify(HighTicketCandidate(**{**base, "explicit_paid_demand": False})), "REJECT_NO_EXTERNAL_PAID_DEMAND")
        self.assertEqual(HighTicketRadar.classify(HighTicketCandidate(**{**base, "funding_verified": False})), "REJECT_UNVERIFIED_PAYMENT_PATH")
        self.assertEqual(HighTicketRadar.classify(HighTicketCandidate(**{**base, "estimated_hours": 2})), "CASHFLOW_HORIZON")


if __name__ == "__main__":
    unittest.main()
