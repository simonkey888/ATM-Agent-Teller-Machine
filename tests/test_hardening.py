from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from atm_core.models import ModelAuthorityViolation, PaymentStatus, RuntimeState, parse_agent_result
from atm_core.payments import PaymentLedger, PaymentNotFinal, build_validated_proof
from atm_core.runtime import ProcessLock, SingletonLockError, classify_provider_failure
from atm_core.security import PromptInjectionRisk, assert_external_task_safe, redact_text
from atm_core.state import StateStore


class EconomicTruthTests(unittest.TestCase):
    def test_llm_cannot_write_paid_usd(self):
        with self.assertRaises(ModelAuthorityViolation):
            parse_agent_result({"status": "PASS", "paid_usd": 200})

    def test_llm_cannot_hide_realized_usd_nested(self):
        with self.assertRaises(ModelAuthorityViolation):
            parse_agent_result({"status": "PASS", "notes": [{"realized_withdrawable_usd": 200}]})

    def test_escrow_and_pending_do_not_build_proofs(self):
        for status in (PaymentStatus.FUNDED_ESCROW, PaymentStatus.ACCEPTED):
            with self.assertRaises(PaymentNotFinal):
                build_validated_proof(
                    source="platform_api", platform="test", payout_id_or_txid="0x1",
                    event_index_or_unique_id="0", amount=Decimal("200"), currency="USDC",
                    recipient="0x1111111111111111111111111111111111111111", status=status,
                    timestamp=datetime.now(timezone.utc), authoritative_url="https://example.test/payout/1",
                )

    def test_duplicate_settlement_counts_once(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = PaymentLedger(Path(td) / "ledger.jsonl")
            proof = build_validated_proof(
                source="platform_api+chain_receipt", platform="test", payout_id_or_txid="0xabc",
                event_index_or_unique_id="7", amount=Decimal("75"), currency="USDC",
                recipient="0x1111111111111111111111111111111111111111", status=PaymentStatus.RELEASED,
                timestamp=datetime.now(timezone.utc), authoritative_url="https://example.test/payout/7",
            )
            self.assertTrue(ledger.append(proof))
            self.assertFalse(ledger.append(proof))
            self.assertEqual(ledger.realized_withdrawable_usd(), Decimal("75"))

    def test_screenshot_cannot_be_payment_truth(self):
        with self.assertRaises(ValueError):
            build_validated_proof(
                source="screenshot", platform="test", payout_id_or_txid="x", event_index_or_unique_id="1",
                amount=Decimal("200"), currency="USD", recipient="public", status=PaymentStatus.PAID,
                timestamp=datetime.now(timezone.utc), authoritative_url="https://example.test/image",
            )


class RuntimeSafetyTests(unittest.TestCase):
    def test_429_is_classified(self):
        failure = classify_provider_failure("gemini", "gemini-3.5-flash", "HTTP 429 usage limit reached session_id: abc")
        self.assertEqual(failure.http_status, 429)
        self.assertEqual(failure.session_id, "abc")

    def test_second_live_lock_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "atm.lock"
            first = ProcessLock(path)
            first.acquire()
            try:
                with self.assertRaises(SingletonLockError):
                    ProcessLock(path).acquire()
            finally:
                first.release()

    def test_corrupt_state_is_quarantined(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            path.write_text("{broken", encoding="utf-8")
            state = StateStore(path).load(200)
            self.assertEqual(state.schema_version, 2)
            self.assertTrue(list(Path(td).glob("state.json.corrupt-*")))

    def test_v1_money_is_not_migrated(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            path.write_text(json.dumps({"version": 1, "phase": "DISCOVER", "paid_usd": 999, "target_paid_usd": 200}), encoding="utf-8")
            state = StateStore(path).load(200)
            self.assertFalse(hasattr(state, "paid_usd"))


class ExternalTrustTests(unittest.TestCase):
    def test_prompt_context_exfiltration_blocked(self):
        text = (Path(__file__).parent / "fixtures" / "clankernation.txt").read_text(encoding="utf-8")
        with self.assertRaises(PromptInjectionRisk):
            assert_external_task_safe(text)

    def test_secret_exfiltration_blocked(self):
        text = (Path(__file__).parent / "fixtures" / "unsafelabs.txt").read_text(encoding="utf-8")
        with self.assertRaises(PromptInjectionRisk):
            assert_external_task_safe(text)

    def test_authorization_and_query_tokens_redacted(self):
        redacted = redact_text("Authorization: Bearer secret123 https://x.test/?token=abc")
        self.assertNotIn("secret123", redacted)
        self.assertNotIn("token=abc", redacted)


if __name__ == "__main__":
    unittest.main()
