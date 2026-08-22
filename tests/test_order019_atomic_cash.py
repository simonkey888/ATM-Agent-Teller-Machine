from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from atm_core.models import Opportunity
from atm_core.order019_atomic_cash import atomic_cash_metrics, choose_atomic_swarm_candidate


def _op(
    canonical_id: str,
    *,
    source: str,
    reward: str,
    competition: int = 0,
    capability_ready: bool | None = None,
    maker_supported: bool | None = None,
    payout_latency_hours: str = "1",
) -> Opportunity:
    now = datetime.now(timezone.utc)
    extras = {}
    if capability_ready is not None:
        extras["capability_ready"] = capability_ready
    if maker_supported is not None:
        extras["maker_supported"] = maker_supported
    return Opportunity(
        canonical_opportunity_id=canonical_id,
        source=source,
        authoritative_url="https://example.com/" + canonical_id.replace(":", "/"),
        upstream_status="open",
        updated_at=now,
        deadline=now + timedelta(days=1),
        reward_gross=Decimal(reward),
        expected_fees=Decimal("0"),
        funding_proof={"escrow_tx_hash": "0xabc"},
        competition=competition,
        open_prs=competition,
        eligibility="PUBLIC_AGENT",
        payment_method="base:USDC" if source == "workprotocol" else "USDC Base award settlement",
        expected_agent_hours=Decimal("1"),
        payout_latency_hours=Decimal(payout_latency_hours),
        p_eligible=Decimal("1"),
        p_claim=Decimal("1"),
        p_complete=Decimal("1"),
        p_accept=Decimal("0.8"),
        p_pay=Decimal("0.95"),
        p_withdrawable=Decimal("0.98"),
        external_state_hash="hash-" + canonical_id,
        **extras,
    )


class Order019AtomicCashTests(unittest.TestCase):
    def test_workprotocol_verification_window_is_a1_not_a2_or_a3(self):
        opportunity = _op(
            "workprotocol:w1",
            source="workprotocol",
            reward="75",
            capability_ready=False,
            payout_latency_hours="24",
        )
        metrics = atomic_cash_metrics(opportunity)
        self.assertEqual(metrics.atomicity_tier, "A1")
        self.assertEqual(metrics.expected_verify_minutes, "1440")
        self.assertEqual(metrics.expected_settlement_minutes, "0")
        self.assertFalse(metrics.p0_first_cash_ready)
        self.assertEqual(metrics.atomicity_proof, "WORKPROTOCOL_API_V2_AUTO_VERIFY_WINDOW")

    def test_legacy_money_velocity_rejection_is_diagnostic_not_selector_veto(self):
        # Legacy ranker calls competition>=8 HIGH_COMPETITION for WorkProtocol.
        # A1 forbids that ranker rule from becoming a second admission authority.
        workprotocol = _op(
            "workprotocol:w2",
            source="workprotocol",
            reward="25",
            competition=12,
            capability_ready=True,
            payout_latency_hours="1",
        )
        ordinary = _op(
            "github:g1",
            source="github",
            reward="25",
            competition=0,
            capability_ready=True,
            payout_latency_hours="1",
        )
        selected, ledger = choose_atomic_swarm_candidate(
            [workprotocol, ordinary],
            ["workprotocol:w2", "github:g1"],
            adapters={},
            config={"min_reward_usd": 1},
            hard_cap=Decimal("3"),
        )
        self.assertIs(selected, workprotocol)
        row = next(item for item in ledger if item["canonical_id"] == "workprotocol:w2")
        self.assertEqual(row["legacy_ranker_reason_diagnostic_only"], "HIGH_COMPETITION")
        self.assertEqual(row["result"], "SELECTED_FOR_CANON_VERIFY")
        self.assertEqual(row["a1_selection_authority"], "RANK_ONLY_CANON_AND_EFFECT_BOUNDARY_REMAIN_AUTHORITATIVE")

    def test_atomic_lane_without_capability_does_not_beat_executable_secondary_lane(self):
        workprotocol = _op(
            "workprotocol:w3",
            source="workprotocol",
            reward="75",
            capability_ready=False,
            payout_latency_hours="24",
        )
        taskmarket = _op(
            "taskmarket:t1",
            source="taskmarket",
            reward="8",
            maker_supported=True,
            payout_latency_hours="48",
        )
        selected, ledger = choose_atomic_swarm_candidate(
            [workprotocol, taskmarket],
            ["workprotocol:w3", "taskmarket:t1"],
            adapters={},
            config={"min_reward_usd": 1},
            hard_cap=Decimal("3"),
        )
        self.assertIs(selected, taskmarket)
        wp = next(item for item in ledger if item["canonical_id"] == "workprotocol:w3")
        tm = next(item for item in ledger if item["canonical_id"] == "taskmarket:t1")
        self.assertEqual(wp["atomicity_tier"], "A1")
        self.assertFalse(wp["capability_ready"])
        self.assertEqual(tm["atomicity_tier"], "A0")
        self.assertTrue(tm["capability_ready"])


if __name__ == "__main__":
    unittest.main()
