from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Order021OwnerInboxTests(unittest.TestCase):
    def test_owner_inbox_is_authenticated_and_not_public_detail(self):
        src = (ROOT / "worker/src/order021-owner-inbox.js").read_text(encoding="utf-8")
        self.assertIn('/api/human-gates/owner-inbox', src)
        self.assertIn('owner_auth_required', src)
        self.assertIn('ATM_HUMAN_GATE_OWNER_INBOX_V1', src)
        self.assertIn('await session(env,bearer(request))', src)
        self.assertIn('outgoing_spend_usd:"0"', src)

    def test_owner_ui_answers_required_questions_and_never_claims_click_completion(self):
        src = (ROOT / "worker/src/order021-owner-inbox.js").read_text(encoding="utf-8")
        for marker in ("WHERE ", "OWNER COST USD ", "FINANCIAL EFFECT ", "EXACT ACTION:", "RISK:", "EXPIRES:", "AFTERWARD:", "Evidence / contract", "OPEN EXACT PAGE", "I COMPLETED THE EXTERNAL STEP", "URL opened != completion", "Authoritative readback state"):
            self.assertIn(marker, src)
        self.assertIn("eth_signTypedData_v4", src)
        self.assertNotIn("eth_sendTransaction", src)
        self.assertNotIn("wallet_sendCalls", src)
        self.assertNotIn("Permit2", src)

    def test_entry_preserves_order020_and_routes_owner_inbox_first(self):
        src = (ROOT / "worker/src/human-gate-entry.js").read_text(encoding="utf-8")
        self.assertIn('import x402 from "./x402-entry.js"', src)
        self.assertIn('handleOwnerInbox(request,env)', src)
        self.assertLess(src.index('handleOwnerInbox(request,env)'), src.index('handleHumanGateRequest(request,env,ctx)'))
        self.assertIn('return x402.fetch(request,env,ctx)', src)
        self.assertIn('return x402.scheduled(controller,env,ctx)', src)

    def test_worker_modules_parse(self):
        for path in ("worker/src/human-gate-crypto.js", "worker/src/order021-human-gate.js", "worker/src/order021-owner-inbox.js", "worker/src/human-gate-entry.js"):
            subprocess.run(["node", "--check", path], cwd=ROOT, check=True, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
