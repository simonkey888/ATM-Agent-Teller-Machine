import importlib.util
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "src" / "atm.py"
spec = importlib.util.spec_from_file_location("atm", MODULE)
atm = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(atm)


class ATMTests(unittest.TestCase):
    def test_extract_plain_json(self):
        self.assertEqual(atm.extract_json('{"status":"PASS"}')["status"], "PASS")

    def test_extract_fenced_json(self):
        value = atm.extract_json('x\n```json\n{"status":"PASS"}\n```')
        self.assertEqual(value["status"], "PASS")

    def test_default_state(self):
        state = atm.default_state()
        self.assertEqual(state["phase"], "DISCOVER")
        self.assertEqual(state["paid_usd"], 0)

    def test_discover_found_routes_to_claim(self):
        state = atm.default_state()
        atm.apply_result(
            state,
            "DISCOVER",
            {"status": "FOUND", "active_opportunity": {"reward_usd": 200}},
        )
        self.assertEqual(state["phase"], "CLAIM")

    def test_checker_failure_routes_to_work(self):
        state = atm.default_state()
        state["phase"] = "CHECK"
        atm.apply_result(state, "CHECK", {"status": "FAIL"})
        self.assertEqual(state["phase"], "WORK")

    def test_paid_target(self):
        state = atm.default_state()
        state["paid_usd"] = 200
        self.assertTrue(atm.should_stop({"target_paid_usd": 200}, state))


if __name__ == "__main__":
    unittest.main()
