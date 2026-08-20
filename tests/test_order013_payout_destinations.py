from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from atm_core.models import PaymentStatus
from atm_core.payments import BASE_CHAIN_ID, BASE_USDC, build_validated_proof, payout_destination_earnings


WALLET = "0xd89Ef03bC3105C538529AC2657Bc4488c94ff4E4"
OTHER = "0x1111111111111111111111111111111111111111"
NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def proof(amount: str, when: datetime, event: str, recipient: str = WALLET):
    return build_validated_proof(
        source="platform_api+chain_receipt",
        platform="taskmarket",
        payout_id_or_txid="0x" + event.rjust(64, "0"),
        event_index_or_unique_id="log:0",
        amount=Decimal(amount),
        currency="USDC",
        recipient=recipient,
        status=PaymentStatus.WITHDRAWABLE,
        timestamp=when,
        authoritative_url=f"https://api.taskmarket.dev/settlements/{event}",
        chain_id=BASE_CHAIN_ID,
        token_address=BASE_USDC,
    )


class Order013PayoutDestinationTests(unittest.TestCase):
    def test_attribution_rolling_30d_dedupe_recipient_and_pending(self):
        boundary = NOW - timedelta(days=30)
        recent = proof("4", boundary, "1")
        old = proof("3", boundary - timedelta(microseconds=1), "2")
        wrong_recipient = proof("100", NOW, "3", OTHER)
        pending = proof("2", NOW, "4").model_copy(
            update={"status": PaymentStatus.ACCEPTED, "event_index_or_unique_id": "log:1"}
        )
        rows = payout_destination_earnings(
            [recent, recent, old, wrong_recipient, pending], WALLET, now=NOW
        )
        base, santander = rows
        self.assertEqual(base["last_30d_usd"], "4")
        self.assertEqual(base["lifetime_usd"], "7")
        self.assertEqual(base["pending_or_unsettled_usd"], "2")
        self.assertEqual(base["verification_state"], "VERIFIED_DESTINATION")
        self.assertEqual(santander["verification_state"], "NOT_CONNECTED")
        self.assertEqual(santander["last_30d_usd"], "UNKNOWN")
        self.assertEqual(santander["lifetime_usd"], "UNKNOWN")

    def test_wrong_chain_or_token_never_attributes(self):
        wrong_chain = proof("8", NOW, "5").model_copy(update={"chain_id": 1})
        wrong_token = proof("9", NOW, "6").model_copy(update={"token_address": OTHER})
        base = payout_destination_earnings([wrong_chain, wrong_token], WALLET, now=NOW)[0]
        self.assertEqual(base["last_30d_usd"], "0")
        self.assertEqual(base["lifetime_usd"], "0")
        self.assertEqual(base["pending_or_unsettled_usd"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
