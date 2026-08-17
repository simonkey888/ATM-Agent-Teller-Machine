from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from atm_core.models import Opportunity
from atm_core.swarm_runtime import current_pass_swarm_shadow
from atm_swarm import MoneyBoard


class Adapter:
    def __init__(self, candidate: Opportunity, *, funding_ok: bool = True):
        self.candidate = candidate
        self.funding_ok = funding_ok
        self.funding_checks = 0

    def discover(self, minimum):
        return [self.candidate]

    def fetch_authoritative(self, opportunity):
        return {"status": "open", "id": opportunity.canonical_opportunity_id}

    def verify_freshness(self, opportunity, snapshot):
        return None

    def verify_funding(self, opportunity, snapshot):
        self.funding_checks += 1
        if not self.funding_ok:
            raise ValueError("funding invalid")

    def verify_eligibility(self, opportunity, snapshot):
        return None


class SwarmRuntimeTests(unittest.TestCase):
    def opportunity(self, canonical_id: str, source: str, reward: str = "20") -> Opportunity:
        return Opportunity(
            canonical_opportunity_id=canonical_id,
            source=source,
            authoritative_url=f"https://example.invalid/{canonical_id}",
            upstream_status="open",
            updated_at=datetime.now(timezone.utc),
            reward_gross=Decimal(reward),
            funding_proof={"funded": True},
            eligibility="PUBLIC_AGENT",
            payment_method="USDC",
            expected_agent_hours=Decimal("1"),
            payout_latency_hours=Decimal("1"),
        )

    def test_old_eligible_row_cannot_starve_current_pass_falsifier(self):
        with tempfile.TemporaryDirectory() as directory:
            board = MoneyBoard(Path(directory) / "board.sqlite3")
            try:
                orphan = self.opportunity("disabled:old", "disabled", "1000")
                board.upsert_candidate(
                    orphan.model_dump(mode="json"), scout="disabled", base_score=1000, status="NORMALIZED"
                )
                board.mark_falsifier(orphan.canonical_opportunity_id, "CONFIRM", confidence=1.0)

                current = self.opportunity("workprotocol:current", "workprotocol", "20")
                adapter = Adapter(current)
                result = current_pass_swarm_shadow(
                    {"discovery_order": ["workprotocol"], "min_reward_usd": 1},
                    {"workprotocol": adapter},
                    board,
                )

                row = board.get(current.canonical_opportunity_id)
                self.assertEqual(adapter.funding_checks, 1)
                self.assertEqual(row["falsifier_verdict"], "CONFIRM")
                self.assertEqual(row["status"], "ELIGIBLE")
                self.assertIn(current.canonical_opportunity_id, result["live_candidates"])
            finally:
                board.close()

    def test_current_candidate_funding_failure_is_killed_even_with_old_eligible_row(self):
        with tempfile.TemporaryDirectory() as directory:
            board = MoneyBoard(Path(directory) / "board.sqlite3")
            try:
                orphan = self.opportunity("disabled:old", "disabled", "1000")
                board.upsert_candidate(
                    orphan.model_dump(mode="json"), scout="disabled", base_score=1000, status="NORMALIZED"
                )
                board.mark_falsifier(orphan.canonical_opportunity_id, "CONFIRM", confidence=1.0)

                current = self.opportunity("workprotocol:current", "workprotocol", "20")
                adapter = Adapter(current, funding_ok=False)
                current_pass_swarm_shadow(
                    {"discovery_order": ["workprotocol"], "min_reward_usd": 1},
                    {"workprotocol": adapter},
                    board,
                )

                row = board.get(current.canonical_opportunity_id)
                self.assertEqual(adapter.funding_checks, 1)
                self.assertEqual(row["falsifier_verdict"], "KILL")
                self.assertEqual(row["status"], "REJECTED")
            finally:
                board.close()


if __name__ == "__main__":
    unittest.main()
