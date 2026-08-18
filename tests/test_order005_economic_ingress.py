from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from atm_core.economic_ingress import (
    BudgetEvidence,
    DealworkProviderRadar,
    DemandFoundryShadow,
    EconomicSemantics,
    EvidenceState,
    FailureDomain,
    GateInputs,
    SuperteamRadar,
    SupplyCandidate,
    WorkProtocolEvaluatorRadar,
    build_gate_feasibility,
    build_rail_health,
    build_supply_adequacy,
    normalize_radar_to_money_board,
)
from atm_swarm import MoneyBoard


AS_OF = "2026-08-18T08:45:00Z"


class Order005EconomicIngressTests(unittest.TestCase):
    def test_false_positive_money_words_never_upgrade_structured_economics(self):
        for text in (
            "Unpaid generic bug report budget $500",
            "$PATH regression breaks CI",
            "USD parser broken around currency tokens",
        ):
            semantics = EconomicSemantics.from_structured({"title": text, "description": text})
            self.assertEqual(semantics.budget_evidence, BudgetEvidence.NO_BUDGET_EVIDENCE)
            self.assertEqual(semantics.economic_buyer_identified, EvidenceState.UNKNOWN)
            self.assertEqual(semantics.payer_authority_verified, EvidenceState.UNKNOWN)
            self.assertFalse(semantics.funded_for_admission)

    def test_price_hypothesis_and_community_signal_never_become_funding(self):
        semantics = EconomicSemantics.from_structured({
            "price_hypothesis_usd": "1000",
            "community_signal": True,
            "reputation_signal": True,
        })
        self.assertEqual(semantics.price_hypothesis_usd, Decimal("1000"))
        self.assertTrue(semantics.non_economic_shadow)
        self.assertEqual(semantics.budget_evidence, BudgetEvidence.NO_BUDGET_EVIDENCE)
        self.assertFalse(semantics.funded_for_admission)

    def test_funding_admission_requires_verified_funding_and_payer_authority(self):
        funding_only = EconomicSemantics.from_structured({"funding_verified": True})
        self.assertEqual(funding_only.budget_evidence, BudgetEvidence.FUNDING_VERIFIED)
        self.assertFalse(funding_only.funded_for_admission)
        both = EconomicSemantics.from_structured({"funding_verified": True, "payer_authority_verified": True})
        self.assertTrue(both.funded_for_admission)

    def test_current_workprotocol_supply_snapshot_stays_nonrealized_and_fail_closed(self):
        snapshot = build_supply_adequacy([
            SupplyCandidate(
                canonical_opportunity_id="workprotocol:f82a9ca9-4b7f-4bdf-91c1-b5ae0516b4eb",
                source="workprotocol",
                reward_usd="75",
                is_open=True,
                is_fresh=False,
                funding_verified=False,
                eligible=False,
                claimable_under_current_authority=False,
                expected_withdrawable_usd=None,
                blocker_reasons=["STALE_GT_30D", "FUNDING_UNVERIFIED_BASE_TX_NOT_FOUND"],
            )
        ], evidence_as_of=AS_OF)
        self.assertEqual(snapshot.VISIBLE_USD, Decimal("75"))
        self.assertEqual(snapshot.FRESH_USD, Decimal("0"))
        self.assertEqual(snapshot.FUNDED_USD, Decimal("0"))
        self.assertEqual(snapshot.ELIGIBLE_USD, Decimal("0"))
        self.assertEqual(snapshot.CLAIMABLE_UNDER_CURRENT_AUTHORITY_USD, Decimal("0"))
        self.assertEqual(snapshot.STALE_RATIO, Decimal("1"))
        self.assertEqual(snapshot.MAX_TICKET_USD, Decimal("75"))
        self.assertEqual(snapshot.MEDIAN_TICKET_USD, Decimal("75.0"))
        self.assertEqual(snapshot.REALIZED_REVENUE_CREDITED, Decimal("0"))
        self.assertIn("FUNDING_UNVERIFIED_BASE_TX_NOT_FOUND", snapshot.TOP_BLOCKER_REASONS)

    def test_gate_feasibility_distinguishes_blocked_from_unknown(self):
        blocked = build_gate_feasibility(GateInputs(
            work_supported=True,
            claim_supported=False,
            submit_supported=None,
            payment_path_verified=False,
            withdrawal_verified=None,
            blockers=["STALE", "FUNDING_UNVERIFIED"],
            evidence_refs=["https://workprotocol.ai/api/jobs"],
        ))
        self.assertEqual(blocked.WORK_FEASIBLE.state, EvidenceState.PROVEN)
        self.assertEqual(blocked.CLAIM_FEASIBLE.state, EvidenceState.BLOCKED)
        self.assertEqual(blocked.SUBMIT_FEASIBLE.state, EvidenceState.UNKNOWN)
        self.assertEqual(blocked.PAYMENT_FEASIBLE.state, EvidenceState.BLOCKED)
        self.assertEqual(blocked.WITHDRAWAL_FEASIBLE.state, EvidenceState.UNKNOWN)

    def test_rail_health_classifies_authoritative_external_payer_failure(self):
        health = build_rail_health(
            source_reachable=True,
            claimability=False,
            failure_domain=FailureDomain.EXTERNAL_PAYER,
            reason="advertised Base funding tx not found",
            evidence_refs=["https://basescan.org/tx/example"],
            evidence_as_of=AS_OF,
        )
        self.assertEqual(health.RAIL_HEALTH.value, "DEGRADED")
        self.assertEqual(health.CLAIMABILITY.value, "BLOCKED")
        self.assertEqual(health.FAILURE_DOMAIN, FailureDomain.EXTERNAL_PAYER)

    def test_read_only_radars_normalize_into_existing_money_board(self):
        superteam = SuperteamRadar.normalize_listing({
            "slug": "order005-agent-sample",
            "url": "https://superteam.fun/earn/listing/order005-agent-sample",
            "agentAccess": "AGENT_ALLOWED",
            "reward_usd": "100",
            "sponsor": "sample-sponsor",
            "submissions": 2,
        })
        dispute = WorkProtocolEvaluatorRadar.normalize_dispute({
            "id": "disp-order005",
            "requesterId": "requester-1",
        })
        dealwork = DealworkProviderRadar.normalize_provider_job({
            "id": "job-order005",
            "url": "https://dealwork.ai/jobs/job-order005",
            "role": "provider",
            "budget": "25",
            "buyer_id": "buyer-1",
        })
        with tempfile.TemporaryDirectory() as directory:
            board = MoneyBoard(Path(directory) / "board.sqlite3")
            ids = normalize_radar_to_money_board(board, [superteam, dispute, dealwork])
            self.assertEqual(ids, [
                "superteam:order005-agent-sample",
                "workprotocol-evaluator:disp-order005",
                "dealwork:job-order005",
            ])
            for cid in ids:
                row = board.get(cid)
                self.assertIsNotNone(row)
                self.assertEqual(row["status"], "NORMALIZED")
            board.close()
        self.assertEqual(superteam.human_gate, "HUMAN_CLAIMS_PAYOUT")
        self.assertTrue(superteam.read_only)
        self.assertTrue(dispute.read_only)
        self.assertTrue(dealwork.read_only)
        with self.assertRaisesRegex(ValueError, "client/hire/subcontract"):
            DealworkProviderRadar.client_action("hire")

    def test_workprotocol_evaluator_surface_is_read_only_and_zero_action(self):
        status = WorkProtocolEvaluatorRadar.surface_status(open_disputes=0, active_arbitrators=3)
        self.assertTrue(status["role_exists"])
        self.assertEqual(status["claimability"], "BLOCKED_ZERO_SPEND_STAKE_REQUIREMENT")
        self.assertEqual(status["vote_actions"], 0)
        self.assertEqual(status["claim_actions"], 0)

    def test_demand_foundry_real_public_issue_shadow_has_no_executable_action(self):
        record = DemandFoundryShadow.from_public_issue({
            "number": 23,
            "html_url": "https://github.com/simonkey888/ATM-Agent-Teller-Machine/issues/23",
            "title": "USD $PATH unpaid budget-looking words",
            "body": "This is a technical issue, not buyer funding evidence.",
        }, evidence_as_of=AS_OF)
        self.assertEqual(record.state, "SHADOW_ONLY")
        self.assertFalse(record.funded_opportunity)
        self.assertEqual(record.economic_semantics.budget_evidence, BudgetEvidence.NO_BUDGET_EVIDENCE)
        self.assertEqual(record.outreach_actions, [])
        self.assertEqual(record.claim_actions, [])
        self.assertEqual(record.executable_actions, [])


if __name__ == "__main__":
    unittest.main()
