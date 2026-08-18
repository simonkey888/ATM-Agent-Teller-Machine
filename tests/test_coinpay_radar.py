from __future__ import annotations

import hashlib
import hmac
import json
import unittest

from atm_core.coinpay import CoinPayClient, CoinPayVerificationError, coinpay_event_to_payment_proof, verify_coinpay_webhook
from atm_core.models import COUNTABLE_PAYMENT_STATUSES, PaymentStatus


SECRET = "fixture-webhook-secret"
NOW = 1_787_075_200
RECIPIENT = "0x1111111111111111111111111111111111111111"


def signed(event: dict, timestamp: int = NOW):
    raw = json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hmac.new(SECRET.encode("utf-8"), str(timestamp).encode("ascii") + b"." + raw, hashlib.sha256).hexdigest()
    return raw, f"t={timestamp},v1={digest}"


class CoinPayRadarTests(unittest.TestCase):
    def test_forwarded_payment_is_withdrawable_only_after_signed_settlement(self):
        event = {
            "id": "evt-forward-1",
            "type": "payment.forwarded",
            "created_at": "2026-08-18T18:00:00Z",
            "data": {
                "payment_id": "pay-1",
                "amount": "12.50",
                "currency": "USDC",
                "tx_hash": "0xabc123",
                "forwarded_to": RECIPIENT,
            },
        }
        raw, signature = signed(event)
        proof = coinpay_event_to_payment_proof(
            raw,
            signature_header=signature,
            webhook_secret=SECRET,
            expected_recipient=RECIPIENT,
            now_unix=NOW,
        )
        self.assertIsNotNone(proof)
        self.assertEqual(proof.status, PaymentStatus.WITHDRAWABLE)
        self.assertIn(proof.status, COUNTABLE_PAYMENT_STATUSES)
        self.assertEqual(str(proof.normalized_usd), "12.50")

    def test_payment_confirmed_is_not_realized_withdrawable(self):
        event = {
            "id": "evt-confirm-1",
            "type": "payment.confirmed",
            "created_at": "2026-08-18T18:00:00Z",
            "data": {"payment_id": "pay-1", "amount": "12.50", "currency": "USDC"},
        }
        raw, signature = signed(event)
        proof = coinpay_event_to_payment_proof(
            raw,
            signature_header=signature,
            webhook_secret=SECRET,
            expected_recipient=RECIPIENT,
            now_unix=NOW,
        )
        self.assertIsNone(proof)

    def test_bad_signature_and_replay_fail_closed(self):
        event = {"id": "evt", "type": "payment.confirmed", "data": {}}
        raw, signature = signed(event)
        with self.assertRaises(CoinPayVerificationError):
            verify_coinpay_webhook(raw + b" ", signature, SECRET, now_unix=NOW)
        with self.assertRaises(CoinPayVerificationError):
            verify_coinpay_webhook(raw, signature, SECRET, now_unix=NOW + 301)

    def test_recipient_mismatch_fails_closed(self):
        event = {
            "id": "evt-forward-2",
            "type": "payment.forwarded",
            "data": {
                "payment_id": "pay-2",
                "amount": "8",
                "currency": "USDC",
                "tx_hash": "0xdef456",
                "forwarded_to": "0x2222222222222222222222222222222222222222",
            },
        }
        raw, signature = signed(event)
        with self.assertRaises(CoinPayVerificationError):
            coinpay_event_to_payment_proof(
                raw,
                signature_header=signature,
                webhook_secret=SECRET,
                expected_recipient=RECIPIENT,
                now_unix=NOW,
            )

    def test_client_has_no_spending_or_wallet_creation_surface(self):
        self.assertFalse(CoinPayClient.spending_enabled)
        self.assertFalse(CoinPayClient.creates_wallets)
        self.assertFalse(CoinPayClient.x402_purchasing_enabled)
        self.assertFalse(hasattr(CoinPayClient, "purchase"))
        self.assertFalse(hasattr(CoinPayClient, "create_wallet"))


if __name__ == "__main__":
    unittest.main()
