from __future__ import annotations

import unittest
import os
from decimal import Decimal
from unittest.mock import patch

from atm_core.funding_gate import verify_base_escrow_receipt
from atm_core.models import Opportunity
from atm_core.opportunities import OpportunityValidationError, WorkProtocolOpportunityAdapter


class FakeHttp:
    def __init__(self, rpc_result):
        self.rpc_result = rpc_result
        self.posts = []

    def get(self, url, headers=None):
        return {}

    def post_json(self, url, payload, headers=None):
        self.posts.append((url, payload, headers or {}))
        if payload.get("method") == "eth_chainId":
            return {"jsonrpc": "2.0", "id": 1, "result": "0x2105"}
        return {"jsonrpc": "2.0", "id": 1, "result": self.rpc_result}


class WorkProtocolFundingGateTests(unittest.TestCase):
    TX = "0x" + "11" * 32
    CONTRACT = "0x" + "22" * 20

    def bound_receipt(self, status="0x1"):
        return {
            "status": status,
            "to": self.CONTRACT,
            "transactionHash": self.TX,
            "logs": [{
                "address": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
                "topics": [
                    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
                    "0x" + "00" * 32,
                    "0x" + "00" * 12 + self.CONTRACT[2:],
                ],
                "data": hex(75_000_000),
            }],
        }

    def snapshot(self):
        return {
            "job": {
                "paymentAmount": "75.00",
                "paymentRail": "base",
                "escrowFunded": True,
                "escrowTxHash": self.TX,
                "status": "open",
            },
            "payments": [
                {
                    "amount": "75.00",
                    "currency": "USDC",
                    "rail": "base",
                    "onchainTxHash": self.TX,
                    "escrowStatus": "locked",
                }
            ],
        }

    def test_missing_base_receipt_is_rejected_even_when_platform_says_locked(self):
        adapter = WorkProtocolOpportunityAdapter(http=FakeHttp(None))
        with patch.dict(os.environ, {"ATM_WORKPROTOCOL_ESCROW_CONTRACT": self.CONTRACT}):
            with self.assertRaisesRegex(OpportunityValidationError, "no authoritative receipt"):
                verify_base_escrow_receipt(adapter, self.snapshot())

    def test_reverted_base_receipt_is_rejected(self):
        adapter = WorkProtocolOpportunityAdapter(http=FakeHttp(self.bound_receipt("0x0")))
        with patch.dict(os.environ, {"ATM_WORKPROTOCOL_ESCROW_CONTRACT": self.CONTRACT}):
            with self.assertRaisesRegex(OpportunityValidationError, "not successful"):
                verify_base_escrow_receipt(adapter, self.snapshot())

    def test_successful_base_receipt_is_accepted(self):
        fake = FakeHttp(self.bound_receipt())
        adapter = WorkProtocolOpportunityAdapter(http=fake)
        with patch.dict(os.environ, {"ATM_WORKPROTOCOL_ESCROW_CONTRACT": self.CONTRACT}):
            evidence = verify_base_escrow_receipt(adapter, self.snapshot())
        self.assertEqual(evidence["tx_hash"], self.TX)
        self.assertEqual(evidence["status"], "0x1")
        self.assertEqual(fake.posts[0][1]["method"], "eth_chainId")
        self.assertEqual(fake.posts[1][1]["method"], "eth_getTransactionReceipt")

    def test_platform_funding_gate_remains_required_before_chain_gate(self):
        fake = FakeHttp({"status": "0x1"})
        adapter = WorkProtocolOpportunityAdapter(http=fake)
        opportunity = Opportunity(
            canonical_opportunity_id="workprotocol:test",
            source="workprotocol",
            authoritative_url="https://workprotocol.ai/jobs/test",
            upstream_status="open",
            reward_gross=Decimal("75"),
        )
        with self.assertRaises(OpportunityValidationError):
            adapter.verify_funding(opportunity, {"job": {"paymentAmount": "75", "paymentRail": "base"}})


if __name__ == "__main__":
    unittest.main()
