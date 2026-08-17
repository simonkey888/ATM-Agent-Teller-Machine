from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

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

    def test_two_scouts_collapse_to_one_record(self):
        with tempfile.TemporaryDirectory() as d:
            board = MoneyBoard(Path(d) / "board.sqlite3")
            board.upsert_candidate(self.candidate(), scout="SCOUT_AGENT_NATIVE", base_score=10)
            board.upsert_candidate(self.candidate(), scout="SCOUT_OSS", base_score=10)
            self.assertEqual(len(board.top()), 1)
            self.assertEqual(board.stats()["scout_count"], 2)

    def test_claim_lease_race_has_one_winner(self):
        with tempfile.TemporaryDirectory() as d:
            board = MoneyBoard(Path(d) / "board.sqlite3")
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

    def test_freshness_and_negative_evidence_decay(self):
        now = datetime.now(timezone.utc)
        old = now - timedelta(days=10)
        self.assertGreater(swarm_signal(10, now, 1, []), swarm_signal(10, old, 1, []))
        self.assertGreater(swarm_signal(10, now, 1, []), swarm_signal(10, now, 1, ["UNFUNDED"]))

    def test_falsifier_kill_deprioritizes(self):
        with tempfile.TemporaryDirectory() as d:
            board = MoneyBoard(Path(d) / "board.sqlite3")
            board.upsert_candidate(self.candidate(), scout="A", base_score=10)
            board.mark_falsifier("same", "KILL", reason="UNFUNDED")
            self.assertEqual(board.get("same")["status"], "REJECTED")
            self.assertEqual(board.top(), [])

    def test_resource_governor_keeps_one_worker(self):
        governor = ResourceGovernor()
        governor.validate()
        self.assertEqual(governor.max_workers, 1)
        self.assertEqual(governor.shed_order()[0], "LOW_SCORE_SCOUTS")
