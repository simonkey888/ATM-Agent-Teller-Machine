from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Order021OwnerBoundaryTests(unittest.TestCase):
    def test_expired_future_and_cross_origin_owner_mutations_fail_closed(self):
        script = r'''
import {enforceOwnerMutationBoundary} from "./worker/src/human-gate-entry.js";
const base="https://atm.simondalmasso44.workers.dev";
const body=(exp)=>JSON.stringify({nonce:"0x"+"11".repeat(32),expires_at:exp,signature:"0x"+"22".repeat(65)});
const mk=(path,exp,origin=base,auth=true)=>new Request(base+path,{method:"POST",headers:{"content-type":"application/json",...(origin?{origin}:{}),...(auth?{authorization:"Bearer fixture"}:{})},body:body(exp)});
const now=1800000000;
const expired=await enforceOwnerMutationBoundary(mk("/api/human-gates/g1/owner-action",now-1),now*1000);
if(!expired||expired.status!==400||(await expired.json()).error!=="owner_action_envelope_expired")throw new Error("expired_owner_action_accepted");
const future=await enforceOwnerMutationBoundary(mk("/api/human-gates/g1/opened",now+181),now*1000);
if(!future||future.status!==400)throw new Error("unbounded_future_owner_action_accepted");
const valid=await enforceOwnerMutationBoundary(mk("/api/human-gates/g1/owner-action",now+120),now*1000);
if(valid!==null)throw new Error("valid_owner_action_blocked");
const cross=await enforceOwnerMutationBoundary(mk("/api/human-gates/g1/recheck",now+120,"https://evil.example"),now*1000);
if(!cross||cross.status!==403||(await cross.json()).error!=="owner_origin_mismatch")throw new Error("cross_origin_mutation_accepted");
const unauth=await enforceOwnerMutationBoundary(mk("/api/human-gates/g1/owner-action",now-1,"",false),now*1000);
if(unauth!==null)throw new Error("boundary_masked_downstream_owner_auth");
const seller=await enforceOwnerMutationBoundary(new Request(base+"/x402/falsify",{method:"POST"}),now*1000);
if(seller!==null)throw new Error("order020_seller_intercepted");
console.log(JSON.stringify({expiry:true,origin:true,order020:true}));
'''
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        self.assertEqual(
            json.loads(completed.stdout),
            {"expiry": True, "origin": True, "order020": True},
        )

    def test_boundary_contains_no_economic_or_signing_authority(self):
        source = (ROOT / "worker/src/human-gate-entry.js").read_text(encoding="utf-8")
        self.assertIn("owner_action_envelope_expired", source)
        self.assertIn("owner_origin_mismatch", source)
        for forbidden in ("eth_sendTransaction", "wallet_sendCalls", "approve(", "Permit2", "private key", "seed phrase"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
