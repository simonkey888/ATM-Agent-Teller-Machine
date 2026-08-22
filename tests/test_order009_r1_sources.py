from __future__ import annotations

import os
import unittest
from decimal import Decimal
from unittest.mock import patch

from atm_core.order009_r1_sources import SuperteamR1Source, WorkProtocolR1Source


class FakeHttp:
    def __init__(self):
        self.urls = []

    def get_json(self, url, *, headers=None, timeout=12):
        self.urls.append(url)
        if "/api/jobs?" in url:
            return {
                "jobs": [
                    {
                        "id": "job-1",
                        "title": "small",
                        "status": "open",
                        "paymentAmount": "1",
                        "paymentCurrency": "USDC",
                        "paymentRail": "base",
                        "escrowFunded": True,
                        "claims": [],
                    },
                    {
                        "id": "job-2",
                        "title": "target",
                        "status": "open",
                        "paymentAmount": "75",
                        "paymentCurrency": "USDC",
                        "paymentRail": "base",
                        "escrowFunded": True,
                        "claims": [],
                    },
                ]
            }
        if "/api/agents/listings/live?" in url:
            return {
                "listings": [
                    {
                        "id": "a",
                        "slug": "expired",
                        "title": "expired",
                        "reward": "50",
                        "rewardCurrency": "USD",
                        "agentAccess": "AGENT_ALLOWED",
                    },
                    {
                        "id": "b",
                        "slug": "current",
                        "title": "current",
                        "reward": "100",
                        "rewardCurrency": "USD",
                        "agentAccess": "AGENT_ALLOWED",
                    },
                ]
            }
        if url.endswith("/details/expired"):
            return {
                "listing": {
                    "id": "a",
                    "slug": "expired",
                    "status": "OPEN",
                    "deadline": "2025-01-01T00:00:00Z",
                    "agentAccess": "AGENT_ALLOWED",
                    "region": "GLOBAL",
                }
            }
        if url.endswith("/details/current"):
            return {
                "listing": {
                    "id": "b",
                    "slug": "current",
                    "status": "OPEN",
                    "deadline": "2030-01-01T00:00:00Z",
                    "agentAccess": "AGENT_ONLY",
                    "region": "Argentina",
                }
            }
        raise AssertionError(url)


class Order009R1SourceTests(unittest.TestCase):
    def test_workprotocol_open_truth_is_not_hidden_by_active_floor(self):
        http = FakeHttp()
        rows = WorkProtocolR1Source(http=http).discover(Decimal("5"))
        self.assertEqual(len(rows), 2)
        self.assertIn("min_pay=0", http.urls[0])

    def test_superteam_live_membership_is_revalidated_per_listing(self):
        http = FakeHttp()
        env = {"SUPERTEAM_AGENT_API_KEY": "secret"}
        with patch.dict(os.environ, env, clear=True), patch(
            "atm_core.radar_sources._load_secret_state",
            return_value={},
        ):
            rows = SuperteamR1Source(http=http).discover(Decimal("5"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].external_id, "b")
        self.assertEqual(rows[0].agent_policy.value, "AGENT_ONLY")
        self.assertTrue(getattr(rows[0], "superteam_detail_validated"))
        self.assertFalse(any("deadline=" in url for url in http.urls if "/listings/live?" in url))


if __name__ == "__main__":
    unittest.main()
