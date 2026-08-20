from __future__ import annotations

import unittest
from decimal import Decimal
from pathlib import Path

from atm_core.order009_sources import (
    AgentPactSource,
    RAIL_CAPABILITIES,
    TaskMarketReadOnlySource,
    The402Source,
    UGigSource,
)
from atm_core.radar_registry import build_canonical_registry
from atm_core.universal_radar import AgentPolicy, FundingStatus


class FakeJsonHttp:
    def __init__(self, payload):
        self.payload = payload
        self.urls = []

    def get_json(self, url, *, headers=None, timeout=10):
        del headers, timeout
        self.urls.append(url)
        return self.payload


class Order009RadarTests(unittest.TestCase):
    def test_single_registry_has_mandatory_and_addendum_sources(self):
        names = set(build_canonical_registry().names)
        mandatory = {
            "superteam", "workprotocol", "agentpact", "taskmarket-daydreams", "ugig", "opire", "github-direct",
            "moltjobs", "near-agent-market", "algora", "agenthire-verify", "agenthansa", "bountybook", "0xwork",
            "claw-earn", "runtime-open-federation-verify", "the402", "clustly", "toku", "boss-dev-watch",
            "dework-watch", "drips-wave-watch", "stacker-news-meta",
        }
        self.assertTrue(mandatory.issubset(names), mandatory - names)

    def test_agentpact_need_is_unfunded_intent_until_deal_escrow_proves_money(self):
        api = FakeJsonHttp({"needs": [{
            "id": "need-1", "title": "Build a JSON parser", "description": "digital task", "budgetMax": "40",
            "status": "OPEN", "createdAt": "2026-08-18T18:00:00Z",
        }]})
        row = AgentPactSource(api).discover(Decimal("5"))[0]
        self.assertEqual(row.funding_status, FundingStatus.UNFUNDED)
        self.assertEqual(row.funding_state, "UNFUNDED_INTENT")
        self.assertEqual(row.payout_net, Decimal("0"))
        self.assertTrue(row.funding_proof["need_is_not_funding"])

        funded = FakeJsonHttp({"needs": [{
            "id": "need-2", "title": "Research API", "description": "digital research", "budgetMax": "25",
            "deal": {"status": "ACTIVE", "escrowTxHash": "0xabc"}, "payment": {"status": "FUNDED"},
        }]})
        row2 = AgentPactSource(funded).discover(Decimal("5"))[0]
        self.assertEqual(row2.funding_status, FundingStatus.VERIFIED)
        self.assertEqual(row2.funding_state, "VERIFIED")
        self.assertEqual(row2.payout_net, Decimal("25"))

    def test_agentpact_referral_and_probe_are_never_agent_work(self):
        api = FakeJsonHttp({"needs": [{"id": "spam", "title": "Referral recruit agents", "description": "refer a friend", "budgetMax": "100"}]})
        row = AgentPactSource(api).discover(Decimal("5"))[0]
        self.assertEqual(row.agent_policy, AgentPolicy.PROHIBITED)
        self.assertEqual(row.policy_rejection, "REFERRAL_RECRUITMENT_OR_SPAM")

    def test_taskmarket_readonly_normalizes_per_task_escrow_not_aggregate_volume(self):
        api = FakeJsonHttp({"tasks": [{
            "id": "0xtask", "description": "Fix bounded unit tests", "reward": "10000000", "netReward": "9500000",
            "escrowTxHash": "0xescrow", "expiryTime": "2026-08-20T00:00:00Z", "status": "open", "mode": "bounty",
            "stakeRequired": False, "platformFeeBps": 500, "submissionCount": 2, "awardCount": 0,
            "submissionWindowOpen": True,
        }]})
        row = TaskMarketReadOnlySource(api).discover(Decimal("5"))[0]
        self.assertEqual(row.payout_gross, Decimal("10"))
        self.assertEqual(row.payout_net, Decimal("9.5"))
        self.assertEqual(row.funding_status, FundingStatus.VERIFIED)
        self.assertEqual(row.funding_state, "VERIFIED")
        self.assertEqual(row.submissions, 2)
        self.assertEqual(row.award_count, 0)
        self.assertEqual(row.execution_cost_usd, Decimal("0"))

    def test_taskmarket_paid_bid_or_stake_path_cannot_pass_zero_spend(self):
        api = FakeJsonHttp({"tasks": [{
            "id": "pitch", "description": "Pitch task", "reward": "50000000", "escrowTxHash": "0xescrow",
            "status": "open", "mode": "pitch", "stakeRequired": False,
        }, {
            "id": "stake", "description": "Agent task", "reward": "50000000", "escrowTxHash": "0xescrow2",
            "status": "open", "mode": "bounty", "stakeRequired": True,
        }]})
        rows = {x.external_id: x for x in TaskMarketReadOnlySource(api).discover(Decimal("5"))}
        self.assertEqual(rows["pitch"].execution_cost_usd, Decimal("0.001"))
        self.assertEqual(rows["stake"].agent_policy, AgentPolicy.PROHIBITED)
        self.assertEqual(rows["stake"].policy_rejection, "STAKE_REQUIRED")

    def test_ugig_listing_remains_unverified_and_filters_referral(self):
        api = FakeJsonHttp({"gigs": [{
            "id": "g1", "title": "Research a public dataset", "description": "remote digital research", "budget_amount": "60",
            "payment_coin": "USDC", "status": "active", "application_count": 1,
        }, {"id": "g2", "title": "Referral campaign", "description": "refer a friend", "budget_amount": "100"}]})
        rows = {x.external_id: x for x in UGigSource(api).discover(Decimal("5"))}
        self.assertEqual(rows["g1"].funding_status, FundingStatus.UNVERIFIED)
        self.assertEqual(rows["g1"].funding_state, "UNVERIFIED")
        self.assertEqual(rows["g1"].payout_net_state, "UNKNOWN_UNTIL_INVOICE_TERMS")
        self.assertEqual(rows["g2"].agent_policy, AgentPolicy.PROHIBITED)

    def test_the402_posting_is_demand_not_funded_until_award_escrow(self):
        api = FakeJsonHttp({"postings": [{"id": "p1", "title": "API research", "description": "current public request", "budget_min_usd": "20", "budget_max_usd": "40"}]})
        row = The402Source(api).discover(Decimal("5"))[0]
        self.assertEqual(row.funding_status, FundingStatus.UNFUNDED)
        self.assertEqual(row.funding_state, "UNFUNDED_INTENT")
        self.assertEqual(row.prohibited_x402_bid_cost_usd, "0.001")
        self.assertEqual(row.execution_cost_usd, Decimal("0"))

    def test_sell_service_payment_and_meta_rails_never_pollute_job_count(self):
        self.assertGreaterEqual(len(RAIL_CAPABILITIES), 7)
        self.assertTrue(all(row["job_counted"] is False for row in RAIL_CAPABILITIES))
        self.assertTrue(all(str(row["outgoing_spend_usd"]) == "0" for row in RAIL_CAPABILITIES))
        self.assertFalse(next(x for x in RAIL_CAPABILITIES if x["source"] == "gigs.sh")["authoritative_funding"])

    def test_worker_fixes_aud_p0_truth_and_schema_defects(self):
        text = (Path(__file__).resolve().parents[1] / "worker" / "src" / "index.js").read_text(encoding="utf-8")
        for marker in ("ATM UNIVERSAL RADAR SNAPSHOT", "operational_state", "rejection_counts", "UNKNOWN", "trustedComment", "deriveOperationalStatus", "/api/money-board", "/api/capabilities", "/api/providers", "EXECUTABLE OPPORTUNITIES"):
            self.assertIn(marker, text)
        self.assertNotIn('pending_usd??"0"', text)
        self.assertNotIn('accepted_usd??"0"', text)
        self.assertNotIn('settled_usd??"0"', text)
        self.assertNotIn("ROBOTICS_SUBMITTED_NET", text)

    def test_deploy_is_strict_pivot_allowlist_and_not_wildcard(self):
        text = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "deploy-cloudflare.yml").read_text(encoding="utf-8")
        self.assertIn("pivot/universal-money-radar-r1", text)
        self.assertIn("Reject stale deployment run", text)
        self.assertIn("git ls-remote", text)
        self.assertIn("Canonical exact-SHA E2E smoke", text)
        self.assertIn("/api/opportunities", text)
        self.assertIn("/api/platform-health", text)
        self.assertIn("/api/money-board", text)
        self.assertNotIn('branches: ["**"]', text)
        self.assertNotIn("branches: ['**']", text)


if __name__ == "__main__":
    unittest.main()
