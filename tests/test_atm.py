import importlib.util
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "src" / "atm.py"
spec = importlib.util.spec_from_file_location("atm", MODULE)
atm = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = atm
spec.loader.exec_module(atm)

from atm_core import opportunities as opportunity_module  # noqa: E402
from atm_core.opportunities import TaskmarketOpportunityAdapter, WorkProtocolOpportunityAdapter  # noqa: E402


class FakeHttp:
    def __init__(self, get_payload=None, post_payload=None):
        self.get_payload = get_payload or {}
        self.post_payload = post_payload or {}
        self.posts = []

    def get(self, url, headers=None):
        return self.get_payload

    def post_json(self, url, payload, headers=None):
        self.posts.append((url, payload, headers or {}))
        return self.post_payload


class ATMTests(unittest.TestCase):
    def test_extract_plain_json(self):
        self.assertEqual(atm.extract_json('{"status":"PASS"}')["status"], "PASS")

    def test_extract_fenced_json(self):
        value = atm.extract_json('x\n```json\n{"status":"PASS"}\n```')
        self.assertEqual(value["status"], "PASS")

    def test_v2_default_state_has_no_model_money_counter(self):
        state = atm.RuntimeState()
        self.assertEqual(state.phase, atm.Phase.DISCOVER)
        self.assertFalse(hasattr(state, "paid_usd"))
        self.assertFalse(hasattr(state, "accepted_usd"))
        self.assertFalse(hasattr(state, "claimed_usd"))

    def test_state_machine_contains_verify_and_payment_verify(self):
        self.assertEqual(atm.Phase.VERIFY.value, "VERIFY")
        self.assertEqual(atm.Phase.PAYMENT_VERIFY.value, "PAYMENT_VERIFY")

    def test_agent_prompt_does_not_expose_cash_target(self):
        prompt = atm.build_agent_prompt(atm.Phase.WORK, {"allow_auto_pr": True}, atm.RuntimeState(target_paid_usd=Decimal("987654")))
        self.assertNotIn("target_paid_usd", prompt)
        self.assertNotIn("987654", prompt)

    def test_deterministic_opportunity_ranking_uses_realized_ev_per_effort(self):
        base = {
            "source": "test",
            "authoritative_url": "https://example.test/task",
            "upstream_status": "open",
            "expected_fees": Decimal("0"),
            "p_eligible": Decimal("1"),
            "p_claim": Decimal("1"),
            "p_complete": Decimal("1"),
            "p_pay": Decimal("1"),
            "p_withdrawable": Decimal("1"),
        }
        headline = atm.Opportunity(
            canonical_opportunity_id="test:headline",
            reward_gross=Decimal("300"),
            expected_agent_hours=Decimal("10"),
            p_accept=Decimal("0.05"),
            competition=20,
            **base,
        )
        realizable = atm.Opportunity(
            canonical_opportunity_id="test:realizable",
            reward_gross=Decimal("75"),
            expected_agent_hours=Decimal("3"),
            p_accept=Decimal("0.70"),
            competition=0,
            **base,
        )
        selected = atm.choose_opportunity([headline, realizable], max_competition=8)
        self.assertEqual(selected.canonical_opportunity_id, "test:realizable")
        self.assertGreater(realizable.ev_per_effort_hour, headline.ev_per_effort_hour)

    def test_status_derives_realized_value_from_empty_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = atm.PaymentLedger(Path(td) / "validated-payment-proofs.jsonl")
            state = atm.RuntimeState(target_paid_usd=Decimal("200"))
            status = atm.status_payload(state, ledger)
            self.assertEqual(status["realized_withdrawable_usd"], "0")
            self.assertEqual(status["validated_payment_proof_count"], 0)

    def test_workprotocol_accepts_live_style_explicit_escrow_evidence(self):
        adapter = WorkProtocolOpportunityAdapter(http=FakeHttp())
        opp = atm.Opportunity(
            canonical_opportunity_id="workprotocol:job",
            source="workprotocol",
            authoritative_url="https://workprotocol.ai/jobs/job",
            upstream_status="open",
            reward_gross=Decimal("75"),
        )
        adapter.verify_funding(
            opp,
            {
                "job": {
                    "paymentAmount": "75.00",
                    "status": "open",
                    "escrowFunded": True,
                    "escrowTxHash": "0xd8d4f28b42bb4dfda74aa38c143f8c7483bf9eb82082397489cb07d3acb68ecd",
                }
            },
        )

    def test_workprotocol_registration_is_automatic_when_public_payout_is_configured(self):
        with tempfile.TemporaryDirectory() as td:
            secrets = Path(td) / "secrets.env"
            fake = FakeHttp(post_payload={"agent": {"id": "agent-1"}, "apiKey": "secret-api-key"})
            adapter = WorkProtocolOpportunityAdapter(
                http=fake,
                payment_recipient_public_identifier="0x1111111111111111111111111111111111111111",
            )
            with patch.object(opportunity_module, "ATM_SECRETS", secrets):
                api_key, agent_id = adapter._auth()
                self.assertEqual((api_key, agent_id), ("secret-api-key", "agent-1"))
                persisted = secrets.read_text(encoding="utf-8")
                self.assertIn("WORKPROTOCOL_API_KEY=secret-api-key", persisted)
                self.assertIn("WORKPROTOCOL_AGENT_ID=agent-1", persisted)
            self.assertEqual(len(fake.posts), 1)
            self.assertEqual(fake.posts[0][1]["walletAddress"], "0x1111111111111111111111111111111111111111")

    def test_taskmarket_bounty_skips_paid_upfront_modes_and_does_not_require_claim(self):
        bounty = {
            "id": "bounty-1",
            "description": "Build a small public HTML game",
            "reward": "100000000",
            "escrowTxHash": "0xabc",
            "status": "open",
            "mode": "bounty",
            "stakeRequired": False,
            "submissionCount": 0,
        }
        pitch = {
            "id": "pitch-1",
            "description": "Pitch a paid proposal",
            "reward": "500000000",
            "escrowTxHash": "0xdef",
            "status": "open",
            "mode": "pitch",
            "stakeRequired": False,
        }
        adapter = TaskmarketOpportunityAdapter(http=FakeHttp(get_payload={"tasks": [bounty, pitch]}))
        found = adapter.discover(Decimal("1"))
        self.assertEqual([x.canonical_opportunity_id for x in found], ["taskmarket:bounty-1"])
        self.assertEqual(found[0].expected_fees, Decimal("7.500000000"))
        self.assertEqual(found[0].task_mode, "bounty")
        self.assertEqual(adapter.claim(found[0]), {"claim_required": False, "mode": "bounty"})

    def test_taskmarket_bounty_claim_phase_advances_to_work(self):
        opp = atm.Opportunity(
            canonical_opportunity_id="taskmarket:bounty-1",
            source="taskmarket",
            authoritative_url="https://taskmarket.dev/tasks/bounty-1",
            upstream_status="open",
            reward_gross=Decimal("100"),
            task_mode="bounty",
        )
        state = atm.RuntimeState(phase=atm.Phase.CLAIM, active_opportunity=opp)
        adapter = TaskmarketOpportunityAdapter(http=FakeHttp())
        atm.phase_claim({}, state, {"taskmarket": adapter})
        self.assertEqual(state.phase, atm.Phase.WORK)
        self.assertEqual(state.last_result["status"], "CLAIM_NOT_REQUIRED")


if __name__ == "__main__":
    unittest.main()
