from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from atm_core.durable_doctor import DurableDoctor
from atm_core.models import Opportunity
from atm_core.role_runtime import IsolatedRoleRunner, RoleReceiptStore


class Order016P1IsolationDoctorHistoryTests(unittest.TestCase):
    def test_scout_real_child_process_is_secretless_even_without_sources(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "roles.sqlite3"
            runner = IsolatedRoleRunner(db)
            result = runner.run(
                "SCOUT", "DISCOVER", {"config": {}, "sources": [], "minimum": 1},
                network_policy="READ_ONLY_PUBLIC_HTTPS", tool_policy="READ_ONLY_SOURCE_DISCOVERY",
            )
            self.assertEqual(result["observations"], [])
            store = RoleReceiptStore(db)
            try:
                receipt = store.latest_by_role()["SCOUT"]
                self.assertNotEqual(receipt["child_pid"], os.getpid())
                self.assertEqual(receipt["secret_env_count"], 0)
                self.assertTrue(receipt["policy_enforced"])
            finally:
                store.close()

    def test_falsifier_duplicate_is_killed_before_network_and_has_process_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "roles.sqlite3"
            opp = Opportunity(
                canonical_opportunity_id="taskmarket:already-submitted",
                source="taskmarket",
                authoritative_url="https://example.com/task",
                upstream_status="OPEN",
                reward_gross="10",
                funding_proof={"escrow": "verified"},
            )
            result = IsolatedRoleRunner(db).run(
                "FALSIFIER", "VERIFY",
                {"config": {}, "opportunity": opp.model_dump(mode="json"), "submitted_ids": [opp.canonical_opportunity_id]},
                network_policy="READ_ONLY_PUBLIC_HTTPS", tool_policy="AUTHORITATIVE_READ_VERIFY",
            )
            self.assertEqual((result["verdict"], result["reason"]), ("KILL", "ALREADY_SUBMITTED_OR_IN_FLIGHT"))
            store = RoleReceiptStore(db)
            try:
                self.assertIn("FALSIFIER", store.summary()["proven_roles"])
            finally:
                store.close()

    def test_doctor_typed_state_survives_process_and_restart(self):
        with tempfile.TemporaryDirectory() as td:
            roles = Path(td) / "roles.sqlite3"
            doctor_db = Path(td) / "doctor.sqlite3"
            runner = IsolatedRoleRunner(roles)
            first = runner.run(
                "DOCTOR", "HANDLE",
                {"state_path": str(doctor_db), "fault": "SOURCE_LIED", "key": "source:x", "threshold": 1, "cooldown_seconds": 60},
                network_policy="DENY", tool_policy="DURABLE_TYPED_RECOVERY",
            )
            self.assertEqual(first["decision"]["action"], "QUARANTINE_BAD_SOURCE")
            reopened = DurableDoctor(doctor_db, threshold=1, cooldown_seconds=60)
            try:
                self.assertFalse(reopened.available("source:x"))
                self.assertTrue(reopened.status("source:x")["quarantined"])
                self.assertFalse(hasattr(reopened, "recover"))
            finally:
                reopened.close()
            store = RoleReceiptStore(roles)
            try:
                self.assertIn("DOCTOR", store.summary()["proven_roles"])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
