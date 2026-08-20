from __future__ import annotations

import unittest

from atm_core.autonomy_fabric import Doctor
from atm_core.learning import SkillCapsule, SkillStage
from atm_core.playbook_learning import playbook_candidate
from atm_core.skill_forge import DemandDrivenSkillForge, FORGE_STAGES, public_skill_forge_contract


class Order016FlywheelTests(unittest.TestCase):
    def fixtures(self):
        return {
            "happy": ["abc"],
            "edge": [""],
            "adversarial": ["<script>alert(1)</script>"],
            "market_shaped": ["Customer deliverable: normalize these 3 rows; preserve quoted commas."],
        }

    def test_forge_pipeline_contains_full_order016_sequence(self):
        expected = (
            "GAP", "SPEC", "EXISTING_PATTERN_SEARCH", "EXECUTOR", "CHECKER", "SECRETLESS_SANDBOX",
            "HAPPY", "EDGE", "ADVERSARIAL", "MARKET_SHAPED_FIXTURE", "FALSIFY", "BENCHMARK",
            "DETERMINISTIC_PROMOTION",
        )
        self.assertEqual(FORGE_STAGES, expected)
        contract = public_skill_forge_contract()
        self.assertFalse(contract["registry_mutation_authority"])
        self.assertFalse(contract["llm_can_self_enable"])
        self.assertEqual(contract["owner_cost_ceiling_usd"], "0")

    def test_market_shaped_fixture_is_mandatory(self):
        forge = DemandDrivenSkillForge()
        incomplete = self.fixtures()
        incomplete.pop("market_shaped")
        decision = forge.evaluate(
            "CSV_QUOTED_COMMAS",
            lambda value: value,
            lambda source, artifact: source == artifact,
            incomplete,
            commercial_use_ok=True,
            supply_chain_pinned=True,
            license_verified=True,
        )
        self.assertFalse(decision.promoted)
        self.assertIn("MARKET_SHAPED_PASS", decision.failures)

    def test_complete_forge_promotes_decision_but_does_not_mutate_registry(self):
        forge = DemandDrivenSkillForge()
        decision = forge.evaluate(
            "CSV_QUOTED_COMMAS",
            lambda value: value,
            lambda source, artifact: source == artifact,
            self.fixtures(),
            commercial_use_ok=True,
            supply_chain_pinned=True,
            license_verified=True,
        )
        self.assertTrue(decision.promoted)
        self.assertFalse(decision.registry_mutated)

    def test_falsifier_or_benchmark_failure_blocks_promotion(self):
        forge = DemandDrivenSkillForge()
        for kwargs, expected in (
            ({"falsifier": lambda capability, results: False}, "FALSIFICATION_PASS"),
            ({"benchmark": lambda capability, results: False}, "BENCHMARK_PASS"),
        ):
            with self.subTest(expected=expected):
                decision = forge.evaluate(
                    "CANDIDATE",
                    lambda value: value,
                    lambda source, artifact: source == artifact,
                    self.fixtures(),
                    commercial_use_ok=True,
                    supply_chain_pinned=True,
                    license_verified=True,
                    **kwargs,
                )
                self.assertFalse(decision.promoted)
                self.assertIn(expected, decision.failures)

    def test_duplicate_capability_and_license_drift_fail_closed(self):
        forge = DemandDrivenSkillForge()
        duplicate = forge.evaluate(
            "DUP",
            lambda value: value,
            lambda source, artifact: True,
            self.fixtures(),
            commercial_use_ok=True,
            supply_chain_pinned=True,
            license_verified=True,
            existing_duplicate=True,
        )
        self.assertIn("NO_EXISTING_CAPABILITY_DUPLICATE", duplicate.failures)
        drift = forge.evaluate(
            "LICENSE_DRIFT",
            lambda value: value,
            lambda source, artifact: True,
            self.fixtures(),
            commercial_use_ok=True,
            supply_chain_pinned=True,
            license_verified=False,
        )
        self.assertIn("LICENSE_VERIFIED", drift.failures)

    def test_checker_failure_has_bounded_doctor_recovery(self):
        doctor = Doctor()
        self.assertEqual(doctor.choose("CHECK_FAILED"), "RESUME_NONCOMMITTED_JOB")
        attempts = {"n": 0}

        def checker_recovery():
            attempts["n"] += 1
            raise RuntimeError("checker remains red")

        result = doctor.recover("CHECK_FAILED", checker_recovery, attempts=2)
        self.assertEqual(result["state"], "FAULT_ESCALATED")
        self.assertEqual(attempts["n"], 2)

    def skill(self, stage: SkillStage) -> SkillCapsule:
        return SkillCapsule(
            skill_id="skill-1",
            capability="CSV_JSON_TRANSFORM",
            code_refs=["code:sha"],
            test_refs=["test:sha"],
            fixture_refs=["fixture:sha"],
            stage=stage,
        )

    def test_learning_requires_terminal_checker_payment_replay_and_test(self):
        evidence = {
            "terminal_outcome": "SETTLED",
            "checker_pass": True,
            "payment_evidence_ref": "chain:tx",
            "replay_pass": True,
            "replay_ref": "replay:sha",
            "adversarial_pass": True,
            "test_ref": "test:sha",
        }
        candidate = playbook_candidate(self.skill(SkillStage.ADVERSARIAL_TEST), evidence)
        self.assertEqual(candidate["state"], "PLAYBOOK_CANDIDATE")
        self.assertFalse(candidate["opaque_self_praise"])

    def test_learning_never_promotes_success_story_without_replay(self):
        evidence = {
            "terminal_outcome": "PAID",
            "checker_pass": True,
            "payment_evidence_ref": "chain:tx",
            "replay_pass": False,
            "adversarial_pass": True,
            "test_ref": "test:sha",
        }
        candidate = playbook_candidate(self.skill(SkillStage.ADVERSARIAL_TEST), evidence)
        self.assertFalse(candidate["eligible"])
        self.assertIn("REPLAY_NOT_PROVEN", candidate["failures"])


if __name__ == "__main__":
    unittest.main()
