from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from atm_core import money_velocity as mv
from atm_core.models import Opportunity
from atm_core.order018_taskmarket import (
    _unknown_settlement,
    action_cost_proof,
    derive_settlement_evidence,
    reconcile_competition,
)


WALLET = "0x1111111111111111111111111111111111111111"


def opp(cid: str, *, tier: str, competition: int, reward: str, accept: str, pay: str) -> Opportunity:
    now = datetime.now(timezone.utc)
    return Opportunity(
        canonical_opportunity_id=f"taskmarket:{cid}",
        source="taskmarket",
        authoritative_url=f"https://taskmarket.dev/tasks/{cid}",
        upstream_status="open",
        created_at=now,
        updated_at=now,
        deadline=now + timedelta(days=1),
        reward_gross=Decimal(reward),
        expected_fees=Decimal("0"),
        funding_proof={"escrow_tx_hash": "0xabc"},
        competition=competition,
        open_prs=competition,
        eligibility="PUBLIC_AGENT_TRUSTED_SIGNER_LANE",
        payment_method="USDC Base award settlement",
        expected_agent_hours=Decimal("1"),
        payout_latency_hours=Decimal("24"),
        p_accept=Decimal(accept),
        p_pay=Decimal(pay),
        maker_supported=True,
        settlement_tier=tier,
    )


class _FailingHttp:
    def get(self, _url):
        raise RuntimeError("offline")


class _Adapter:
    base_url = "https://taskmarket.dev"
    http = _FailingHttp()


class Order018SettlementFirstTests(unittest.TestCase):
    def test_first_selector_prefers_settlement_tier_over_fabricated_probabilities(self):
        s1 = opp("s1", tier="S1", competition=1, reward="100", accept="1", pay="1")
        s3 = opp("s3", tier="S3", competition=1, reward="2", accept="0", pay="0")
        self.assertEqual(mv.choose_money_velocity([s1, s3]).canonical_opportunity_id, "taskmarket:s3")

    def test_same_tier_prefers_truthful_lower_competition_before_nominal_reward(self):
        low_comp = opp("low", tier="S2", competition=2, reward="2", accept="0", pay="0")
        high_comp = opp("high", tier="S2", competition=7, reward="200", accept="1", pay="1")
        self.assertEqual(mv.choose_money_velocity([high_comp, low_comp]).canonical_opportunity_id, "taskmarket:low")

    def test_known_79_detail_missing_never_becomes_zero(self):
        listed = opp("robotics", tier="S1", competition=79, reward="10", accept="1", pay="1")
        self.assertEqual(reconcile_competition(listed, {}, None), 79)
        self.assertEqual(reconcile_competition(listed, {"submissionCount": 4}, []), 79)

    def test_unknown_history_is_none_not_false_zero(self):
        task = {"requester": "payer-x", "escrowTxHash": "0xabc", "description": "must contain exact columns"}
        unknown = _unknown_settlement(task)
        self.assertFalse(unknown.history_observed)
        self.assertIsNone(unknown.prior_settled_tasks)
        self.assertIsNone(unknown.settlement_tx_count)
        self.assertIsNone(unknown.prior_awards)
        self.assertIsNone(unknown.prior_submissions)

        derived = derive_settlement_evidence(_Adapter(), task)
        self.assertFalse(derived.history_observed)
        self.assertIsNone(derived.prior_settled_tasks)
        self.assertIsNone(derived.settlement_tx_count)

    def test_mode_label_never_proves_cost(self):
        claim = {
            "mode": "claim",
            "stakeRequired": False,
            "pendingActions": [{"role": "worker", "action": "claim", "eligibleAddress": WALLET, "requiresPayment": False, "paymentAmount": 0}],
        }
        self.assertTrue(action_cost_proof(claim, WALLET).zero_cost_ready)

        auction = {
            "mode": "auction",
            "stakeRequired": False,
            "pendingActions": [{"role": "worker", "action": "bid", "eligibleAddress": WALLET, "requiresPayment": False, "paymentAmount": 0}],
        }
        proof = action_cost_proof(auction, WALLET)
        self.assertFalse(proof.zero_cost_ready)
        self.assertEqual(proof.mandatory_worker_cost_to_delivery_usd, "UNKNOWN_OR_NONZERO")


if __name__ == "__main__":
    unittest.main()
