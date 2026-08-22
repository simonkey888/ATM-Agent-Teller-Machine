from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "worker" / "src" / "x402-entry.js"


class Order020SenderPayerBindingKill(unittest.TestCase):
    def test_authorized_payer_a_rejects_transfer_from_b_before_execute_or_publish(self):
        script = r'''
import { paymentTransferFromTo, X402_USDC, X402_PAY_TO } from "./worker/src/x402-entry.js";

const transferTopic = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef";
const payerA = "0x1111111111111111111111111111111111111111";
const senderB = "0x2222222222222222222222222222222222222222";
const topic = (address) => "0x" + "0".repeat(24) + address.slice(2).toLowerCase();
const log = {
  address: X402_USDC,
  topics: [transferTopic, topic(senderB), topic(X402_PAY_TO)],
  data: "0x2710",
};
let falsifierExecutionCount = 0;
let paymentEventPublishCount = 0;
const paymentAmount = paymentTransferFromTo(log, payerA, X402_PAY_TO, 10000n);
if (paymentAmount !== null) {
  falsifierExecutionCount += 1;
  paymentEventPublishCount += 1;
}
if (paymentAmount !== null) throw new Error("mismatched_sender_was_accepted");
if (falsifierExecutionCount !== 0) throw new Error("falsifier_executed_before_sender_binding");
if (paymentEventPublishCount !== 0) throw new Error("payment_event_published_before_sender_binding");
console.log(JSON.stringify({ paymentProof: false, falsifierExecutionCount, paymentEventPublishCount }));
'''
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        result = json.loads(completed.stdout.strip())
        self.assertFalse(result["paymentProof"])
        self.assertEqual(result["falsifierExecutionCount"], 0)
        self.assertEqual(result["paymentEventPublishCount"], 0)

    def test_authorized_payer_a_accepts_transfer_from_a(self):
        script = r'''
import { paymentTransferFromTo, X402_USDC, X402_PAY_TO } from "./worker/src/x402-entry.js";
const transferTopic = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef";
const payerA = "0x1111111111111111111111111111111111111111";
const topic = (address) => "0x" + "0".repeat(24) + address.slice(2).toLowerCase();
const log = { address: X402_USDC, topics: [transferTopic, topic(payerA), topic(X402_PAY_TO)], data: "0x2710" };
const paymentAmount = paymentTransferFromTo(log, payerA, X402_PAY_TO, 10000n);
if (paymentAmount !== 10000n) throw new Error("matching_sender_rejected");
console.log(JSON.stringify({ paymentProof: true, amountAtomic: paymentAmount.toString() }));
'''
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        result = json.loads(completed.stdout.strip())
        self.assertTrue(result["paymentProof"])
        self.assertEqual(result["amountAtomic"], "10000")

    def test_payment_path_uses_sender_bound_proof_but_generic_falsifier_stays_generic(self):
        entry = ENTRY.read_text(encoding="utf-8")
        self.assertIn(
            "paymentEvidence = await proveUsdcTransferForPayment(settlementTx, payer, X402_PAY_TO, X402_AMOUNT_ATOMIC)",
            entry,
        )
        self.assertIn(
            "const evidence = await proveUsdcTransfer(input.txHash, input.expectedRecipient, input.minAmountAtomic)",
            entry,
        )


if __name__ == "__main__":
    unittest.main()
