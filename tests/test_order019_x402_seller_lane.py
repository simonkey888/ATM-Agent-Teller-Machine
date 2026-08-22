from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "worker" / "src" / "x402-entry.js"
WRANGLER = ROOT / "wrangler.toml"
CANONICAL = "0xd89Ef03bC3105C538529AC2657Bc4488c94ff4E4"
BASE_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"


class Order019X402SellerLaneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = WRAPPER.read_text(encoding="utf-8")

    def test_exact_single_paid_path_and_existing_wallet(self):
        self.assertIn('export const X402_PATH = "/x402/falsify"', self.src)
        self.assertIn(f'export const X402_PAY_TO = "{CANONICAL}"', self.src)
        self.assertIn(f'export const X402_USDC = "{BASE_USDC}"', self.src)
        self.assertIn('export const X402_NETWORK = "eip155:8453"', self.src)
        self.assertIn('export const X402_AMOUNT_ATOMIC = "10000"', self.src)
        self.assertEqual(self.src.count('"/x402/falsify"'), 2)
        self.assertNotIn("PRIVATE_KEY", self.src)
        self.assertNotIn("mnemonic", self.src.lower())
        self.assertNotIn("wallet.create", self.src.lower())

    def test_zero_spend_facilitator_is_fail_closed_and_no_paid_api_key(self):
        self.assertIn('export const X402_FACILITATOR = "https://facilitator.xpay.sh"', self.src)
        self.assertIn('"/supported"', self.src)
        self.assertIn('"SELLER_LANE_KILLED_FACILITATOR_UNAVAILABLE"', self.src)
        self.assertNotRegex(self.src, re.compile(r"CDP[_A-Z]*KEY|COINBASE[_A-Z]*KEY"))
        self.assertNotIn("Authorization: Bearer", self.src)
        self.assertIn('outgoing_spend_usd: "0"', self.src)

    def test_payment_is_settled_and_independently_proven_before_work(self):
        settle = self.src.index('settlement = await facilitatorCall("/settle", paymentPayload)')
        proof = self.src.index("paymentEvidence = await proveUsdcTransfer", settle)
        work = self.src.index("const result = await runFalsifier(input)", proof)
        self.assertLess(settle, proof)
        self.assertLess(proof, work)
        self.assertIn("payment_not_proven_to_canonical_wallet", self.src)
        self.assertIn('"PAYMENT-RESPONSE"', self.src)
        self.assertIn('"PAYMENT-REQUIRED"', self.src)

    def test_falsifier_is_base_usdc_read_only_and_ssrf_closed(self):
        self.assertIn('export const BASE_RPC = "https://mainnet.base.org"', self.src)
        self.assertIn("eth_getTransactionReceipt", self.src)
        self.assertIn("TRANSFER_TOPIC", self.src)
        self.assertIn("EXPECTED_USDC_TRANSFER_ABSENT", self.src)
        self.assertIn("DETERMINISTIC_BASE_USDC_ONCHAIN_FALSIFIER", self.src)
        self.assertNotIn("eth_sendRawTransaction", self.src)
        self.assertNotIn("eth_sendTransaction", self.src)
        self.assertNotIn("body.rpc", self.src)
        self.assertNotIn("body.url", self.src)

    def test_legacy_worker_is_delegated_unchanged(self):
        self.assertIn('import legacy from "./index.js"', self.src)
        self.assertIn("return legacy.fetch(request, env, ctx)", self.src)
        self.assertIn("return legacy.scheduled(controller, env, ctx)", self.src)
        wrangler = WRANGLER.read_text(encoding="utf-8")
        self.assertIn('main = "worker/src/x402-entry.js"', wrangler)


if __name__ == "__main__":
    unittest.main()
