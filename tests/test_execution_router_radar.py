from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from atm_core.execution_router import UniversalExecutionRouter
from atm_core.universal_radar import AgentPolicy, ExecutorClass, FundingStatus, RadarDisposition, UniversalOpportunity, source_hash


class FakeProviderGate:
    def __init__(self, usable):
        self.usable = usable

    def inspect(self):
        return [type("Health", (), {"usable": self.usable})()]


class FakeGithubExecutor:
    def __init__(self, ready):
        self.ready = ready

    def capability_preflight(self):
        return {"fork_branch_pr_possible": self.ready}


class FakeGcp:
    def validate_e2_micro(self, **kwargs):
        return type("Decision", (), {"eligible": kwargs.get("estimated_billable_usd") == "0"})()


def item(klass=ExecutorClass.HTTP_RESEARCH, **updates):
    payload = dict(
        source="fixture",
        external_id="r1",
        canonical_url="https://example.test/r1",
        title="Research task",
        description="public facts",
        category="research",
        payout_gross=Decimal("20"),
        payout_currency="USDC",
        payout_usd=Decimal("20"),
        payout_net=Decimal("20"),
        funding_status=FundingStatus.VERIFIED,
        funding_proof={"escrow": "locked"},
        eligibility="PUBLIC_AGENT",
        agent_policy=AgentPolicy.AGENT_ALLOWED,
        claim_method="API",
        submission_method="API",
        payment_rail="Base USDC",
        payout_latency="1h",
        withdrawal_path="owner wallet",
        estimated_execution_minutes=20,
        required_capabilities=["research"],
        execution_cost_usd=Decimal("0"),
        source_state_hash=source_hash({"id": "r1"}),
        executor_class=klass,
        disposition=RadarDisposition.ATTACK_NOW,
    )
    payload.update(updates)
    return UniversalOpportunity(**payload)


class UniversalExecutionRouterTests(unittest.TestCase):
    def router(self, model=True, github=True):
        return UniversalExecutionRouter(
            provider_gate=FakeProviderGate(model),
            github_executor=FakeGithubExecutor(github),
            gcp_backend=FakeGcp(),
        )

    def test_research_requires_zero_cost_model_before_claim(self):
        allowed, proof = self.router(model=True).claim_allowed(item(ExecutorClass.HTTP_RESEARCH))
        self.assertTrue(allowed)
        self.assertTrue(proof.material_execution_path)
        blocked, proof = self.router(model=False).claim_allowed(item(ExecutorClass.HTTP_RESEARCH))
        self.assertFalse(blocked)
        self.assertEqual(proof.reason, "ZERO_COST_MODEL_UNAVAILABLE")

    def test_github_patch_requires_model_and_external_github_capability(self):
        blocked, proof = self.router(model=True, github=False).claim_allowed(item(ExecutorClass.GITHUB_BOUNDED_PATCH))
        self.assertFalse(blocked)
        self.assertEqual(proof.reason, "MODEL_OR_GITHUB_WRITE_CAPABILITY_UNAVAILABLE")
        allowed, proof = self.router(model=True, github=True).claim_allowed(item(ExecutorClass.GITHUB_BOUNDED_PATCH))
        self.assertTrue(allowed)
        self.assertTrue(proof.zero_cost)

    def test_unsupported_never_claimed_even_when_every_provider_ready(self):
        blocked, proof = self.router(model=True, github=True).claim_allowed(item(ExecutorClass.UNSUPPORTED))
        self.assertFalse(blocked)
        self.assertEqual(proof.reason, "UNSUPPORTED_CAPABILITY")

    def test_human_gate_unverified_funding_and_nonzero_cost_never_claimed(self):
        for candidate in (
            item(human_gate="KYC"),
            item(funding_status=FundingStatus.UNVERIFIED),
            item(execution_cost_usd=Decimal("0.01")),
        ):
            allowed, _ = self.router(model=True, github=True).claim_allowed(candidate)
            self.assertFalse(allowed)


if __name__ == "__main__":
    unittest.main()
