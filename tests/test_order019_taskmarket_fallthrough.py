from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from atm_core.opportunities import TaskmarketOpportunityAdapter
from atm_core.order019_recovery import choose_swarm_candidate
from atm_core.taskmarket_cli import CANONICAL_TASKMARKET_WALLET


TARGET = "0x4c887264d5ede369de6e98c6214e6c03ee8708af108305ecafa0341a675e6147"
NEXT_1 = "0x5e7facd1016471ff54f86afd73076ac100c03fbdb1b0698a50e8cb3336f8587c"
NEXT_2 = "0x664bc58934ca51fc34386bf58a5b1f0e6d4172e06c7df032d1c84b1d6960766c"


class _Lane:
    canonical_wallet = CANONICAL_TASKMARKET_WALLET

    def __init__(self):
        self.existing_calls: list[str] = []

    def preflight_signer(self):
        return {"signer_present": True}

    def existing_submission(self, task_id: str):
        self.existing_calls.append(task_id)
        if task_id == TARGET:
            # Deliberately use the canonical-wallet row for which ATM authorship
            # is not independently proven. Wallet match is duplicate authority,
            # never authorship authority.
            return {
                "id": "810de399-4575-4899-942a-a603bc6cee74",
                "workerAddress": CANONICAL_TASKMARKET_WALLET,
                "workerAgentId": "63975",
                "submittedAt": "2026-08-19T13:32:26.203Z",
                "rejectedAt": None,
                "deliverableHash": "0x24282ec35ee3430379ac3867b2ffb49352af65930fdc5fe03022e1e44ea8f9b5",
                "submitTxHash": "0x5ed6124ac955c857d25f7c78356fbf5fd2198875ea721694c8a1c5cd1c6efddd",
            }
        return None


class _Http:
    def __init__(self, details: dict[str, dict]):
        self.details = details
        self.urls: list[str] = []

    def get(self, url: str):
        self.urls.append(url)
        task_id = url.rstrip("/").split("/")[-1]
        return dict(self.details[task_id])


def _detail(task_id: str, *, expiry: datetime, window: bool = True) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "id": task_id,
        "title": "Deterministic CSV normalization",
        "description": "Return one CSV file named result.csv. Required columns: id,name,value. Exactly 2 data rows.",
        "mode": "bounty",
        "status": "open",
        "phase": "active",
        "stakeRequired": False,
        "reward": 10_000_000,
        "netReward": 9_250_000,
        "platformFeeBps": 750,
        "submissionCount": 1,
        "awardCount": 0,
        "awards": [],
        "escrowTxHash": "0xabc123",
        "submissionWindowOpen": window,
        "pendingActions": [
            {
                "role": "worker",
                "action": "submit",
                "eligibleAddress": CANONICAL_TASKMARKET_WALLET,
                "requiresPayment": False,
                "paymentAmount": "0",
            }
        ],
        "createdAt": (now - timedelta(hours=1)).isoformat(),
        "updatedAt": now.isoformat(),
        "expiryTime": expiry.isoformat(),
    }


class Order019TaskmarketFallthroughTests(unittest.TestCase):
    def test_existing_submission_is_watch_reconcile_and_selector_falls_through_all_eligible_ids(self):
        now = datetime.now(timezone.utc)
        details = {
            TARGET: _detail(TARGET, expiry=now + timedelta(days=1)),
            NEXT_1: _detail(NEXT_1, expiry=now - timedelta(hours=1)),
            NEXT_2: _detail(NEXT_2, expiry=now - timedelta(hours=2), window=False),
        }
        lane = _Lane()
        http = _Http(details)
        adapter = TaskmarketOpportunityAdapter(base_url="https://api.taskmarket.dev", http=http, lane=lane)
        board_ids = [
            f"taskmarket-daydreams:{TARGET}",
            f"taskmarket-daydreams:{NEXT_1}",
            f"taskmarket-daydreams:{NEXT_2}",
        ]

        selected, ledger = choose_swarm_candidate(
            [],
            board_ids,
            adapters={"taskmarket": adapter},
            config={"min_reward_usd": 5},
            hard_cap=Decimal("3"),
        )

        self.assertIsNone(selected)
        self.assertEqual([row["canonical_id"] for row in ledger], board_ids)

        target = ledger[0]
        self.assertEqual(target["result"], "WATCH_RECONCILE_EXISTING_SUBMISSION")
        self.assertEqual(target["first_terminal_rejection_reason"], "ALREADY_SUBMITTED")
        self.assertEqual(target["submission_id"], "810de399-4575-4899-942a-a603bc6cee74")
        self.assertEqual(target["worker_address"].lower(), CANONICAL_TASKMARKET_WALLET.lower())
        self.assertEqual(target["atm_origin_attribution"], "NOT_PROVEN_BY_WALLET_MATCH")
        self.assertEqual(target["payment_reconcile_state"], "WATCH_ACCEPTANCE_AWARD_SETTLEMENT_BASE_RECEIPT")
        self.assertFalse(target["duplicate_effect_allowed"])

        # The first duplicate does not stop the global allocator. Both next
        # MoneyBoard IDs are independently refetched and receive their own
        # terminal result instead of silently disappearing.
        self.assertEqual(ledger[1]["result"], "REJECTED_CANON_PREFLIGHT")
        self.assertEqual(ledger[1]["first_terminal_rejection_reason"], "EXPIRED")
        self.assertEqual(ledger[2]["result"], "REJECTED_CANON_PREFLIGHT")
        self.assertEqual(ledger[2]["first_terminal_rejection_reason"], "EXPIRED")
        for task_id in (TARGET, NEXT_1, NEXT_2):
            self.assertGreaterEqual(sum(f"/api/tasks/{task_id}" in url for url in http.urls), 2)

    def test_wallet_match_is_duplicate_authority_not_atm_origin_authority(self):
        now = datetime.now(timezone.utc)
        lane = _Lane()
        http = _Http({TARGET: _detail(TARGET, expiry=now + timedelta(days=1))})
        adapter = TaskmarketOpportunityAdapter(base_url="https://api.taskmarket.dev", http=http, lane=lane)

        selected, ledger = choose_swarm_candidate(
            [],
            [f"taskmarket-daydreams:{TARGET}"],
            adapters={"taskmarket": adapter},
            config={"min_reward_usd": 5},
            hard_cap=Decimal("3"),
        )

        self.assertIsNone(selected)
        self.assertEqual(ledger[0]["first_terminal_rejection_reason"], "ALREADY_SUBMITTED")
        self.assertEqual(ledger[0]["atm_origin_attribution"], "NOT_PROVEN_BY_WALLET_MATCH")
        self.assertFalse(ledger[0]["duplicate_effect_allowed"])
        self.assertNotIn("ATM_SUBMISSION_CONFIRMED", ledger[0].values())


if __name__ == "__main__":
    unittest.main()
