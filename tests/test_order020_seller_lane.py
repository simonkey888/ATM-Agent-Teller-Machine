from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import order020_x402_reconcile as reconcile
from atm_core.payments import BASE_USDC, ERC20_TRANSFER_TOPIC, PaymentLedger

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "worker" / "src" / "x402-entry.js"
DISCOVERY = ROOT / "worker" / "src" / "order020-discovery.js"
WORKFLOW = ROOT / ".github" / "workflows" / "atm-cloud-cycle.yml"
CANONICAL = reconcile.CANONICAL_WALLET.lower()
PAYER = "0x1111111111111111111111111111111111111111"
TX = "0x" + "ab" * 32
EVENT_SECRET = "order020-unit-test-event-secret"


def topic(address: str) -> str:
    return "0x" + "0" * 24 + address.lower()[2:]


class FakeHttp:
    def __init__(self, receipt):
        self.receipt = receipt
        self.calls = []

    def post_json(self, url, payload, headers=None):
        self.calls.append((url, payload))
        return {"jsonrpc": "2.0", "id": 1, "result": self.receipt}


def good_receipt(*, payer: str = PAYER, recipient: str = CANONICAL, amount: int = 10_000, status: str = "0x1"):
    return {
        "status": status,
        "blockNumber": "0x123",
        "logs": [
            {
                "address": BASE_USDC,
                "topics": [ERC20_TRANSFER_TOPIC, topic(payer), topic(recipient)],
                "data": hex(amount),
            }
        ],
    }


def good_event(**overrides):
    base = {
        "schema": reconcile.EVENT_SCHEMA,
        "payment_event_id": "e" * 64,
        "resource": reconcile.RESOURCE_URL,
        "settlement_tx": TX,
        "payer": PAYER,
        "recipient": CANONICAL,
        "network": "eip155:8453",
        "chain_id": 8453,
        "token": BASE_USDC,
        "amount_atomic": "10000",
        "input_sha256": "a" * 64,
        "source_sha": "b" * 40,
        "observed_at": "2026-08-22T12:00:00+00:00",
        "onchain_proven": True,
        "external_buyer": True,
        "outgoing_spend_usd": "0",
        "_comment_id": 123,
        "_comment_url": "https://github.com/simonkey888/ATM-Agent-Teller-Machine/issues/48#issuecomment-123",
        "_comment_created_at": "2026-08-22T12:00:00Z",
    }
    base.update(overrides)
    return base


def signed_event(**overrides):
    event = good_event(**overrides)
    event[reconcile.EVENT_AUTH_FIELD] = reconcile._event_auth_tag(EVENT_SECRET, event)
    return event


class Order020SellerLaneContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entry = ENTRY.read_text(encoding="utf-8")
        cls.discovery = DISCOVERY.read_text(encoding="utf-8")
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_discovery_openapi_bazaar_and_live_routes_are_wired(self):
        self.assertIn('openapi: "3.1.0"', self.discovery)
        self.assertIn('"x-payment-info"', self.discovery)
        self.assertIn('protocols: ["x402"]', self.discovery)
        self.assertIn('"https://json-schema.org/draft/2020-12/schema"', self.discovery)
        self.assertIn('extensions: { bazaar: bazaar() }', self.entry)
        self.assertIn('url.pathname === "/openapi.json"', self.entry)
        self.assertIn('url.pathname === "/.well-known/x402"', self.entry)
        self.assertIn('X402_SELLER_FUNNEL_PATH = "/api/seller-funnel"', self.entry)
        self.assertIn('wellKnownDocument(RESOURCE_URL)', self.entry)

    def test_unpaid_402_precedes_facilitator_and_body_validation(self):
        no_header = self.entry.index('if (!paymentHeader) return payment402()')
        facilitator = self.entry.index("await requireFacilitator()")
        parse_body = self.entry.index("input = await parseCapabilityInput(request)")
        self.assertLess(no_header, facilitator)
        self.assertLess(no_header, parse_body)
        self.assertIn('"PAYMENT-REQUIRED"', self.entry)

    def test_settle_and_independent_receipt_precede_execution_and_event(self):
        settle = self.entry.index('settlement = await facilitatorCall("/settle", paymentPayload)')
        proof = self.entry.index("paymentEvidence = await proveUsdcTransfer", settle)
        execute = self.entry.index("const result = await runFalsifier(input)", proof)
        event = self.entry.index("publishPaymentEventAfterResponse", execute)
        self.assertLess(settle, proof)
        self.assertLess(proof, execute)
        self.assertLess(execute, event)
        self.assertIn("payment_not_proven_to_canonical_wallet", self.entry)

    def test_self_payment_bounded_input_zero_spend_and_legacy_delegation(self):
        self.assertIn("owner_self_payment_rejected", self.entry)
        self.assertIn("length > 4096", self.entry)
        self.assertIn("text.length > 4096", self.entry)
        self.assertIn('outgoing_spend_usd: "0"', self.entry)
        self.assertIn('import legacy from "./index.js"', self.entry)
        self.assertIn("return legacy.fetch(request, env, ctx)", self.entry)
        self.assertIn("return legacy.scheduled(controller, env, ctx)", self.entry)

    def test_existing_recurring_cloud_cycle_reconciles_before_status_cycle_with_secret_boundary(self):
        reconcile_pos = self.workflow.index("python src/order020_x402_reconcile.py")
        cloud_pos = self.workflow.index("python src/atm_cloud_fabric.py")
        self.assertLess(reconcile_pos, cloud_pos)
        self.assertEqual(self.workflow.count("python src/order020_x402_reconcile.py"), 1)
        self.assertIn('export ATM_ORDER020_EVENT_SECRET="$_BUNDLE_GH_PAT"', self.workflow)
        self.assertIn("unset PAYOUT _BUNDLE_GH_PAT _LEGACY_MODEL_KEY ATM_BASE_WALLET_ADDRESS ATM_ORDER020_EVENT_SECRET", self.workflow)


class Order020ReconcileSecurityTests(unittest.TestCase):
    def test_untrusted_event_is_rejected(self):
        row = {
            "id": 1,
            "author_association": "NONE",
            "user": {"login": "attacker"},
            "body": reconcile.EVENT_MARKER + "\n```json\n" + json.dumps(signed_event()) + "\n```",
        }
        self.assertIsNone(reconcile._parse_event(row))

    def test_static_event_rejects_wrong_chain_token_recipient_amount_and_self_payment(self):
        cases = [
            (signed_event(network="eip155:1"), "chain_mismatch"),
            (signed_event(token="0x" + "22" * 20), "token_mismatch"),
            (signed_event(recipient="0x" + "33" * 20), "recipient_mismatch"),
            (signed_event(amount_atomic="9999"), "amount_mismatch"),
            (signed_event(payer=CANONICAL), "owner_self_payment_rejected"),
        ]
        for event, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(ValueError, expected):
                    reconcile._validate_static_event(event, event_secret=EVENT_SECRET)

    def test_event_authentication_rejects_missing_and_tampered_event(self):
        with self.assertRaisesRegex(ValueError, "event_auth_missing"):
            reconcile._validate_static_event(good_event(), event_secret=EVENT_SECRET)
        event = signed_event()
        event[reconcile.EVENT_AUTH_FIELD] = "0" * 64
        with self.assertRaisesRegex(ValueError, "event_auth_invalid"):
            reconcile._validate_static_event(event, event_secret=EVENT_SECRET)

    def test_receipt_rejects_missing_reverted_wrong_recipient_and_short_amount(self):
        cases = [
            (None, "receipt_not_final"),
            (good_receipt(status="0x0"), "receipt_reverted"),
            (good_receipt(recipient="0x" + "44" * 20), "matching_external_base_usdc_transfer_absent"),
            (good_receipt(amount=9_999), "matching_external_base_usdc_transfer_absent"),
        ]
        for receipt, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(ValueError, expected):
                    reconcile._matching_transfer(TX, PAYER, FakeHttp(receipt))

    def test_valid_external_payment_appends_once_and_duplicate_counts_once(self):
        event = signed_event()
        http = FakeHttp(good_receipt())
        with tempfile.TemporaryDirectory() as tmp:
            ledger = PaymentLedger(Path(tmp) / "validated-payment-proofs.jsonl")
            with patch.object(reconcile, "_events_since", return_value=[event]):
                first = reconcile.reconcile_local_ledger(ledger, http=http, event_secret=EVENT_SECRET)
                second = reconcile.reconcile_local_ledger(ledger, http=http, event_secret=EVENT_SECRET)
            self.assertEqual(first["appended"], 1)
            self.assertEqual(first["withdrawable_usdc"], "0.01")
            self.assertEqual(second["appended"], 0)
            self.assertEqual(second["duplicates"], 1)
            proofs = ledger.load()
            self.assertEqual(len(proofs), 1)
            self.assertEqual(str(proofs[0].normalized_usd), "0.01")
            self.assertEqual(proofs[0].recipient_public_identifier.lower(), CANONICAL)


if __name__ == "__main__":
    unittest.main()
