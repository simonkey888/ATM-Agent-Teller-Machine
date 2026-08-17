from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import atm_v2
from atm_core.models import Opportunity
from atm_swarm import MoneyBoard, ResourceGovernor, swarm_signal


class SwarmContractTests(unittest.TestCase):
    def candidate(self, cid="same", updated=None):
        return {
            "canonical_opportunity_id": cid,
            "source": "lane",
            "authoritative_url": "https://example.invalid/" + cid,
            "updated_at": updated or datetime.now(timezone.utc).isoformat(),
            "reward_gross": "20",
        }

    def opportunity(self, cid: str, reward: str) -> Opportunity:
        return Opportunity(
            canonical_opportunity_id=cid,
            source="lane",
            authoritative_url="https://example.invalid/" + cid,
            upstream_status="OPEN",
            updated_at=datetime.now(timezone.utc),
            reward_gross=Decimal(reward),
            funding_proof={"funded": True},
            eligibility="ELIGIBLE",
            payment_method="USDC",
            expected_agent_hours=Decimal("1"),
            payout_latency_hours=Decimal("1"),
        )

    def test_two_scouts_collapse_to_one_record(self):
        with tempfile.TemporaryDirectory() as d:
            board = MoneyBoard(Path(d) / "board.sqlite3")
            try:
                board.upsert_candidate(self.candidate(), scout="SCOUT_AGENT_NATIVE", base_score=10)
                board.upsert_candidate(self.candidate(), scout="SCOUT_OSS", base_score=10)
                self.assertEqual(len(board.top()), 1)
                self.assertEqual(board.stats()["scout_count"], 2)
            finally:
                board.close()

    def test_claim_lease_race_has_one_winner(self):
        with tempfile.TemporaryDirectory() as d:
            board = MoneyBoard(Path(d) / "board.sqlite3")
            try:
                board.upsert_candidate(self.candidate(), scout="A", base_score=1)
                out = []
                lock = threading.Lock()

                def run(i):
                    lease = board.acquire_lease("same", "CLAIM", f"worker-{i}", 300)
                    with lock:
                        out.append(lease)

                threads = [threading.Thread(target=run, args=(i,)) for i in range(16)]
                [t.start() for t in threads]
                [t.join() for t in threads]
                self.assertEqual(sum(x is not None for x in out), 1)
            finally:
                board.close()

    def test_freshness_and_negative_evidence_decay(self):
        now = datetime.now(timezone.utc)
        old = now - timedelta(days=10)
        self.assertGreater(swarm_signal(10, now, 1, []), swarm_signal(10, old, 1, []))
        self.assertGreater(swarm_signal(10, now, 1, []), swarm_signal(10, now, 1, ["UNFUNDED"]))

    def test_falsifier_kill_deprioritizes(self):
        with tempfile.TemporaryDirectory() as d:
            board = MoneyBoard(Path(d) / "board.sqlite3")
            try:
                board.upsert_candidate(self.candidate(), scout="A", base_score=10)
                board.mark_falsifier("same", "KILL", reason="UNFUNDED")
                self.assertEqual(board.get("same")["status"], "REJECTED")
                self.assertEqual(board.top(), [])
            finally:
                board.close()

    def test_resource_governor_keeps_one_worker(self):
        governor = ResourceGovernor()
        governor.validate()
        self.assertEqual(governor.max_workers, 1)
        self.assertEqual(governor.shed_order()[0], "LOW_SCORE_SCOUTS")

    def test_supervisor_uses_only_falsifier_confirmed_money_board_candidate(self):
        with tempfile.TemporaryDirectory() as d:
            board_path = Path(d) / "board.sqlite3"
            high_unconfirmed = self.opportunity("high-unconfirmed", "100")
            lower_confirmed = self.opportunity("lower-confirmed", "20")
            board = MoneyBoard(board_path)
            try:
                board.upsert_candidate(
                    high_unconfirmed.model_dump(mode="json"),
                    scout="SCOUT_A",
                    base_score=100,
                )
                board.upsert_candidate(
                    lower_confirmed.model_dump(mode="json"),
                    scout="SCOUT_B",
                    base_score=20,
                )
                board.mark_falsifier("lower-confirmed", "CONFIRM", confidence=0.99)
            finally:
                board.close()

            with patch.object(atm_v2, "MONEY_BOARD_FILE", board_path):
                selected, selector = atm_v2._choose_discovery_candidate(
                    [high_unconfirmed, lower_confirmed], Decimal("3")
                )

            self.assertEqual(selector, "SWARM_MONEY_BOARD")
            self.assertIsNotNone(selected)
            self.assertEqual(selected.canonical_opportunity_id, "lower-confirmed")

    def test_supervisor_never_executes_stale_board_only_candidate(self):
        with tempfile.TemporaryDirectory() as d:
            board_path = Path(d) / "board.sqlite3"
            stale_confirmed = self.opportunity("stale-confirmed", "100")
            fresh_unconfirmed = self.opportunity("fresh-unconfirmed", "20")
            board = MoneyBoard(board_path)
            try:
                board.upsert_candidate(
                    stale_confirmed.model_dump(mode="json"),
                    scout="SCOUT_A",
                    base_score=100,
                )
                board.mark_falsifier("stale-confirmed", "CONFIRM", confidence=0.99)
                board.upsert_candidate(
                    fresh_unconfirmed.model_dump(mode="json"),
                    scout="SCOUT_B",
                    base_score=20,
                )
            finally:
                board.close()

            with patch.object(atm_v2, "MONEY_BOARD_FILE", board_path):
                selected, selector = atm_v2._choose_discovery_candidate(
                    [fresh_unconfirmed], Decimal("3")
                )

            self.assertEqual(selector, "SWARM_MONEY_BOARD")
            self.assertIsNone(selected)
