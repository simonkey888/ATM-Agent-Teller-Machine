import importlib.util
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "src" / "atm.py"
spec = importlib.util.spec_from_file_location("atm", MODULE)
atm = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = atm
spec.loader.exec_module(atm)


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

    def test_deterministic_opportunity_ranking_penalizes_competition(self):
        base = {
            "source": "test",
            "authoritative_url": "https://example.test/task",
            "upstream_status": "open",
            "expected_fees": Decimal("0"),
        }
        high_comp = atm.Opportunity(
            canonical_opportunity_id="test:high",
            reward_gross=Decimal("200"),
            competition=7,
            **base,
        )
        low_comp = atm.Opportunity(
            canonical_opportunity_id="test:low",
            reward_gross=Decimal("100"),
            competition=0,
            **base,
        )
        selected = atm.choose_opportunity([high_comp, low_comp], max_competition=8)
        self.assertEqual(selected.canonical_opportunity_id, "test:low")

    def test_status_derives_realized_value_from_empty_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = atm.PaymentLedger(Path(td) / "validated-payment-proofs.jsonl")
            state = atm.RuntimeState(target_paid_usd=Decimal("200"))
            status = atm.status_payload(state, ledger)
            self.assertEqual(status["realized_withdrawable_usd"], "0")
            self.assertEqual(status["validated_payment_proof_count"], 0)


if __name__ == "__main__":
    unittest.main()
