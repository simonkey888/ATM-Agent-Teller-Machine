from __future__ import annotations

import unittest

from atm_core.autonomy_fabric import Doctor
from atm_core.learning import SkillCapsule, SkillStage
from atm_core.playbook_learning import playbook_candidate
from atm_core.skill_forge import (
    DemandDrivenSkillForge,
    FORGE_STAGES,
    FIXTURE_CLASSES,
    SandboxFixtureEvidence,
    public_skill_forge_contract,
)


class Order016FlywheelTests(unittest.TestCase):
    def sandbox_evidence(self, *, omit: str | None = None, secret_count: int = 0, network_policy: str = "DENY"):
        return [
            SandboxFixtureEvidence(
                fixture_class=name,
                sandbox_id=f"sandbox-{name}",
                process_isolated=True,
                environment_secret_count=secret_count,
                network_policy=network_policy,
                tool_allowlist_enforced=True,
                executor_pass=True,
                checker_pass=True,
                artifact_contract_pass=True,
            )
            for name in FIXTURE_CLASSES
            if name != omit
        ]

    def evaluate(self, evidence, **overrides):
        kwargs = {
            "commercial_use_ok": True,
            "supply_chain_pinned": True,
            "license_verified": True,
            "existing_duplicate": False,
            "falsification_pass": True,
            "benchmark_pass": True,
            "owner_cost_usd": "0",
        }
        kwargs.update(overrides)
        return DemandDrivenSkillForge().evaluate("CANDIDATE", evidence, **kwargs)

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
        self.assertFalse(contract["in_process_candidate_execution"])
        self.assertEqual(contract["candidate_code_execution"], "EXTERNAL_SECRETLESS_SANDBOX_WORKER_ONLY")
        self.assertEqual(contract["owner_cost_ceiling_usd"], "0")

    def test_market_shaped_fixture_is_mandatory(self):
        decision = self.evaluate(self.sandbox_evidence(omit="market_shaped"))
        self.assertFalse(decision.promoted)
        self.assertIn("MARKET_SHAPED_PASS", decision.failures)

    def test_complete_sandbox_evidence_promotes_decision_but_not_registry(self):
        decision = self.evaluate(self.sandbox_evidence())
        self.assertTrue(decision.promoted)
        self.assertFalse(decision.registry_mutated)

    def test_falsifier_or_benchmark_failure_blocks_promotion(self):
        decision = self.evaluate(self.sandbox_evidence(), falsification_pass=False)
        self.assertIn("FALSIFICATION_PASS", decision.failures)
        decision = self.evaluate(self.sandbox_evidence(), benchmark_pass=False)
        self.assertIn("BENCHMARK_PASS", decision.failures)

    def test_duplicate_license_tool_and_cost_drift_fail_closed(self):
        self.assertIn("NO_EXISTING_CAPABILITY_DUPLICATE", self.evaluate(self.sandbox_evidence(), existing_duplicate=True).failures)
        self.assertIn("LICENSE_VERIFIED", self.evaluate(self.sandbox_evidence(), license_verified=False).failures)
        self.assertIn("ZERO_SPEND", self.evaluate(self.sandbox_evidence(), owner_cost_usd="0.01").failures)

    def test_malicious_sandbox_attestation_with_secret_or_network_escape_fails(self):
        secret = self.evaluate(self.sandbox_evidence(secret_count=1))
        self.assertFalse(secret.promoted)
        self.assertIn("ADVERSARIAL_PASS", secret.failures)
        network = self.evaluate(self.sandbox_evidence(network_policy="UNRESTRICTED"))
        self.assertFalse(network.promoted)
        self.assertIn("MARKET_SHAPED_PASS", network.failures)

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
