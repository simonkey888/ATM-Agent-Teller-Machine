from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from atm_core.economic_ingress import (
    AuthoritativeFundingProof,
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
    order005_authoritative_funding_fixture,
)
from atm_swarm import MoneyBoard


AS_OF = "2026-08-18T09:27:00Z"


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

    def test_requester_and_repo_identity_do_not_alias_to_buyer_or_payer(self):
        semantics = EconomicSemantics.from_structured({
            "requester_id": "issue-author",
            "repo_owner": "maintainer",
            "repository": "owner/repo",
        })
        self.assertEqual(semantics.requester_identified, EvidenceState.PROVEN)
        self.assertEqual(semantics.economic_buyer_identified, EvidenceState.UNKNOWN)
        self.assertEqual(semantics.payer_authority_verified, EvidenceState.UNKNOWN)

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

    def test_raw_source_funding_claims_and_arbitrary_refs_have_zero_verification_authority(self):
        payloads = [
            {"funding_verified": True},
            {"funding_verified": True, "economic_evidence_refs": ["https://evil.example/fake-proof"]},
            {"funding_verified": True, "economic_evidence_refs": ["https://github.com/example/repo/issues/1"]},
            {"external_funding_ref": "0x" + "1" * 64},
            {
                "funding_verified": True,
                "payer_authority_verified": True,
                "economic_evidence_refs": ["https://authoritative.example/escrow/1"],
                "authoritative_funding_proof": {"verdict": "PROVEN"},
            },
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                semantics = EconomicSemantics.from_structured(payload)
                self.assertNotEqual(semantics.budget_evidence, BudgetEvidence.FUNDING_VERIFIED)
                self.assertFalse(semantics.funded_for_admission)

    def test_only_typed_atm_funding_proof_can_verify_and_payer_authority_remains_separate(self):
        proof = order005_authoritative_funding_fixture()
        self.assertTrue(proof.is_atm_authoritative)
        evidence_without_payer = EconomicSemantics.from_structured(
            {"funding_verified": False},
            authoritative_funding_proof=proof,
        )
        self.assertEqual(evidence_without_payer.budget_evidence, BudgetEvidence.FUNDING_VERIFIED)
        self.assertFalse(evidence_without_payer.funded_for_admission)
        self.assertIn("atm-proof://order005/deterministic-funding-fixture-v1", evidence_without_payer.evidence_refs)

        both = EconomicSemantics.from_structured(
            {"payer_authority_verified": True},
            authoritative_funding_proof=proof,
        )
        self.assertEqual(both.budget_evidence, BudgetEvidence.FUNDING_VERIFIED)
        self.assertEqual(both.payer_authority_verified, EvidenceState.PROVEN)
        self.assertTrue(both.funded_for_admission)

        with self.assertRaisesRegex(ValueError, "only be minted by ATM verifier"):
            AuthoritativeFundingProof(
                verifier_id="ATM_AUTHORITATIVE_FUNDING_VERIFIER_V1",
                rail="FORGED",
                evidence_ref="https://evil.example/proof",
                evidence_hash="a" * 64,
                verdict=EvidenceState.PROVEN,
                _authority_token=object(),
            )

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
        ], evidence_as_of=AS_OF, inventory_state=EvidenceState.PROVEN)
        self.assertEqual(snapshot.VISIBLE_USD, Decimal("75"))
        self.assertEqual(snapshot.FRESH_USD, Decimal("0"))
        self.assertEqual(snapshot.FUNDED_USD, Decimal("0"))
        self.assertEqual(snapshot.ELIGIBLE_USD, Decimal("0"))
        self.assertEqual(snapshot.CLAIMABLE_UNDER_CURRENT_AUTHORITY_USD, Decimal("0"))
        self.assertEqual(snapshot.OPEN_CANDIDATE_COUNT, 1)
        self.assertEqual(snapshot.FRESH_CANDIDATE_COUNT, 0)
        self.assertEqual(snapshot.STALE_RATIO, Decimal("1"))
        self.assertEqual(snapshot.MAX_TICKET_USD, Decimal("75"))
        self.assertEqual(snapshot.MEDIAN_TICKET_USD, Decimal("75.0"))
        self.assertEqual(snapshot.REALIZED_REVENUE_CREDITED, Decimal("0"))
        self.assertIn("FUNDING_UNVERIFIED_BASE_TX_NOT_FOUND", snapshot.TOP_BLOCKER_REASONS)

    def test_supply_unknown_is_never_coerced_to_zero(self):
        unknown = build_supply_adequacy([], evidence_as_of=AS_OF, inventory_state=EvidenceState.UNKNOWN)
        self.assertIsNone(unknown.VISIBLE_USD)
        self.assertIsNone(unknown.FUNDED_USD)
        self.assertIsNone(unknown.OPEN_CANDIDATE_COUNT)
        proven_empty = build_supply_adequacy([], evidence_as_of=AS_OF, inventory_state=EvidenceState.PROVEN)
        self.assertEqual(proven_empty.VISIBLE_USD, Decimal("0"))
        self.assertEqual(proven_empty.OPEN_CANDIDATE_COUNT, 0)

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

    def test_rail_health_separates_platform_and_authority_failures_from_worker(self):
        payer = build_rail_health(
            source_reachable=True,
            claimability=False,
            failure_domain=FailureDomain.EXTERNAL_PAYER,
            reason="advertised Base funding tx not found",
            evidence_refs=["https://basescan.org/tx/example"],
            evidence_as_of=AS_OF,
        )
        self.assertEqual(payer.RAIL_HEALTH.value, "DEGRADED")
        self.assertEqual(payer.CLAIMABILITY.value, "BLOCKED")
        self.assertEqual(payer.FAILURE_DOMAIN, FailureDomain.EXTERNAL_PAYER)
        platform = build_rail_health(
            source_reachable=True,
            claimability=False,
            failure_domain=FailureDomain.PLATFORM,
            reason="claim endpoint unavailable",
            evidence_refs=["https://example.invalid/claim-status"],
            evidence_as_of=AS_OF,
        )
        self.assertEqual(platform.FAILURE_DOMAIN, FailureDomain.PLATFORM)
        authority = build_rail_health(
            source_reachable=True,
            claimability=False,
            failure_domain=FailureDomain.AUTHORITY,
            reason="outgoing spend required and prohibited",
            evidence_refs=["owner-order:zero-spend"],
            evidence_as_of=AS_OF,
        )
        self.assertEqual(authority.FAILURE_DOMAIN, FailureDomain.AUTHORITY)

    def test_read_only_radars_normalize_into_existing_money_board_and_cannot_mutate(self):
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
        self.assertEqual(superteam.economic_semantics.economic_buyer_identified, EvidenceState.UNKNOWN)
        self.assertEqual(dispute.economic_semantics.economic_buyer_identified, EvidenceState.UNKNOWN)
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
        for fn in (SuperteamRadar.submit, WorkProtocolEvaluatorRadar.vote, WorkProtocolEvaluatorRadar.claim, DealworkProviderRadar.bid):
            with self.assertRaisesRegex(ValueError, "read-only radar forbids"):
                fn("x")
        with self.assertRaisesRegex(ValueError, "client/hire/subcontract"):
            DealworkProviderRadar.client_action("hire")
        with self.assertRaisesRegex(ValueError, "downstream payment"):
            DealworkProviderRadar.normalize_provider_job({"id": "bad", "role": "provider", "requires_downstream_payment": True})

    def test_workprotocol_evaluator_surface_is_read_only_and_zero_action(self):
        status = WorkProtocolEvaluatorRadar.surface_status(open_disputes=0, active_arbitrators=3)
        self.assertTrue(status["ROLE_EXISTS"])
        self.assertEqual(status["ACTIVE_CASE_SUPPLY"], 0)
        self.assertEqual(status["REPUTATION_REQUIREMENT"], "AGENT_ARBITRATOR_REPUTATION_GTE_10")
        self.assertEqual(status["EVALUATOR_REVENUE"], "UNPROVEN")
        self.assertEqual(status["vote_actions"], 0)
        self.assertEqual(status["claim_actions"], 0)

    def test_demand_foundry_real_public_issue_shadow_has_no_executable_action(self):
        record = DemandFoundryShadow.from_public_issue({
            "number": 23,
            "html_url": "https://github.com/simonkey888/ATM-Agent-Teller-Machine/issues/23",
            "requester_id": "simonkey888",
            "title": "USD $PATH unpaid budget-looking words",
            "body": "This is a technical issue, not buyer funding evidence.",
        }, evidence_as_of=AS_OF)
        self.assertEqual(record.state, "SHADOW_ONLY")
        self.assertFalse(record.funded_opportunity)
        self.assertEqual(record.economic_semantics.requester_identified, EvidenceState.PROVEN)
        self.assertEqual(record.economic_semantics.economic_buyer_identified, EvidenceState.UNKNOWN)
        self.assertEqual(record.economic_semantics.payer_authority_verified, EvidenceState.UNKNOWN)
        self.assertEqual(record.economic_semantics.budget_evidence, BudgetEvidence.NO_BUDGET_EVIDENCE)
        self.assertEqual(record.contract.disposition, "KILL")
        self.assertEqual(record.contract.external_action_requirement, "NONE_AUTHORIZED_ORDER_005")
        self.assertEqual(record.outreach_actions, [])
        self.assertEqual(record.claim_actions, [])
        self.assertEqual(record.executable_actions, [])


if __name__ == "__main__":
    unittest.main()
