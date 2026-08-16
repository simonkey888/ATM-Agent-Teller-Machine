from __future__ import annotations

import unittest
from decimal import Decimal

from atm_core.opportunities import TaskmarketOpportunityAdapter, WorkProtocolOpportunityAdapter


class LiveDiscoveryContractTests(unittest.TestCase):
    def test_workprotocol_live_discovery_has_open_explicitly_funded_code_work(self):
        adapter = WorkProtocolOpportunityAdapter()
        found = adapter.discover(Decimal("1"))
        self.assertTrue(found, "WorkProtocol returned no current code opportunities")
        verified = None
        errors: list[str] = []
        for opportunity in found[:20]:
            try:
                snapshot = adapter.fetch_authoritative(opportunity)
                adapter.verify_freshness(opportunity, snapshot)
                adapter.verify_funding(opportunity, snapshot)
                adapter.verify_eligibility(opportunity, snapshot)
                verified = opportunity
                break
            except Exception as exc:
                errors.append(f"{opportunity.canonical_opportunity_id}: {exc}")
        self.assertIsNotNone(verified, "no fresh funded WorkProtocol opportunity: " + " | ".join(errors[-5:]))

    def test_taskmarket_live_discovery_has_open_funded_zero_stake_work(self):
        adapter = TaskmarketOpportunityAdapter()
        found = adapter.discover(Decimal("1"))
        self.assertTrue(found, "Taskmarket returned no current zero-upfront opportunities")
        verified = None
        errors: list[str] = []
        for opportunity in found[:20]:
            try:
                snapshot = adapter.fetch_authoritative(opportunity)
                adapter.verify_freshness(opportunity, snapshot)
                adapter.verify_funding(opportunity, snapshot)
                adapter.verify_eligibility(opportunity, snapshot)
                verified = opportunity
                break
            except Exception as exc:
                errors.append(f"{opportunity.canonical_opportunity_id}: {exc}")
        self.assertIsNotNone(verified, "no fresh funded Taskmarket opportunity: " + " | ".join(errors[-5:]))


if __name__ == "__main__":
    unittest.main()
