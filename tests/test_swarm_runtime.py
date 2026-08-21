from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from atm_core.models import Opportunity
from atm_core.swarm_runtime import current_pass_swarm_shadow
from atm_swarm import MoneyBoard


class Adapter:
    def __init__(self, candidate: Opportunity): self.candidate = candidate


class SwarmRuntimeTests(unittest.TestCase):
    def opportunity(self, canonical_id: str, source: str, reward: str = "20") -> Opportunity:
        return Opportunity(canonical_opportunity_id=canonical_id, source=source, authoritative_url=f"https://example.invalid/{canonical_id}", upstream_status="open", updated_at=datetime.now(timezone.utc), reward_gross=Decimal(reward), funding_proof={"funded": True}, eligibility="PUBLIC_AGENT", payment_method="USDC", expected_agent_hours=Decimal("1"), payout_latency_hours=Decimal("1"))

    def isolated_run(self, candidate: Opportunity, verdict: str, reason=None):
        calls = {"scout": 0, "falsifier": 0, "boundary": 0}
        def run(_runner, role, operation, payload, **kwargs):
            if role == "SCOUT":
                calls["scout"] += 1
                source = payload["sources"][0]
                return {"observations": [{"source": source, "state": "OK", "error_class": None, "candidates": [candidate.model_dump(mode="json")]}]}
            if (role, operation) == ("FALSIFIER", "BOUNDARY_PROBE"):
                calls["boundary"] += 1
                return {"state": "BOUNDARY_READY"}
            if (role, operation) == ("FALSIFIER", "VERIFY"):
                calls["falsifier"] += 1
                self.assertEqual(payload["opportunity"]["canonical_opportunity_id"], candidate.canonical_opportunity_id)
                return {"verdict": verdict, "reason": reason, "fresh_object_hash": "a" * 64 if verdict == "CONFIRM" else None}
            if (role, operation) == ("DOCTOR", "INSPECT"):
                calls["boundary"] += 1
                return {"state": "BOUNDARY_READY", "summary": {}}
            if role == "DOCTOR":
                return {"decision": {"action": "REFRESH_STALE_STATE"}, "status": {}, "summary": {}}
            raise AssertionError((role, operation))
        return calls, run

    def test_old_eligible_row_cannot_starve_current_pass_isolated_falsifier(self):
        with tempfile.TemporaryDirectory() as directory:
            board = MoneyBoard(Path(directory) / "board.sqlite3")
            try:
                orphan = self.opportunity("disabled:old", "disabled", "1000")
                board.upsert_candidate(orphan.model_dump(mode="json"), scout="disabled", base_score=1000, status="NORMALIZED")
                board.mark_falsifier(orphan.canonical_opportunity_id, "CONFIRM", confidence=1.0)
                current = self.opportunity("workprotocol:current", "workprotocol", "20")
                calls, fake_run = self.isolated_run(current, "CONFIRM")
                with patch("atm_core.swarm_runtime.IsolatedRoleRunner.run", new=fake_run), patch("atm_core.swarm_runtime._doctor_available_sources", return_value=["workprotocol"]), patch("atm_core.swarm_runtime.canonical_non_actionable_ids", return_value=set()):
                    result = current_pass_swarm_shadow({"discovery_order": ["workprotocol"], "min_reward_usd": 1}, {"workprotocol": Adapter(current)}, board)
                row = board.get(current.canonical_opportunity_id)
                self.assertEqual(calls["falsifier"], 1)
                self.assertEqual(calls["boundary"], 2)
                self.assertEqual(row["falsifier_verdict"], "CONFIRM")
                self.assertEqual(row["status"], "ELIGIBLE")
                self.assertIn(current.canonical_opportunity_id, result["live_candidates"])
            finally: board.close()

    def test_current_candidate_funding_failure_is_killed_even_with_old_eligible_row(self):
        with tempfile.TemporaryDirectory() as directory:
            board = MoneyBoard(Path(directory) / "board.sqlite3")
            try:
                orphan = self.opportunity("disabled:old", "disabled", "1000")
                board.upsert_candidate(orphan.model_dump(mode="json"), scout="disabled", base_score=1000, status="NORMALIZED")
                board.mark_falsifier(orphan.canonical_opportunity_id, "CONFIRM", confidence=1.0)
                current = self.opportunity("workprotocol:current", "workprotocol", "20")
                calls, fake_run = self.isolated_run(current, "KILL", "ValueError")
                with patch("atm_core.swarm_runtime.IsolatedRoleRunner.run", new=fake_run), patch("atm_core.swarm_runtime._doctor_available_sources", return_value=["workprotocol"]), patch("atm_core.swarm_runtime.canonical_non_actionable_ids", return_value=set()):
                    current_pass_swarm_shadow({"discovery_order": ["workprotocol"], "min_reward_usd": 1}, {"workprotocol": Adapter(current)}, board)
                row = board.get(current.canonical_opportunity_id)
                self.assertEqual(calls["falsifier"], 1)
                self.assertEqual(row["falsifier_verdict"], "KILL")
                self.assertEqual(row["status"], "REJECTED")
            finally: board.close()


if __name__ == "__main__": unittest.main()
