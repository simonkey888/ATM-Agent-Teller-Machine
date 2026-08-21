from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from atm_core.durable_doctor import DurableDoctor
from atm_core.models import Opportunity
from atm_core.role_runtime import IsolatedRoleRunner, RoleReceiptStore
from atm_core.sandbox_boundary_v2 import SandboxUnavailable


class Order016P1IsolationDoctorHistoryTests(unittest.TestCase):
    def assert_sterile_child_receipt(self, receipt, supervisor_home: Path):
        child_home=Path(receipt["child_home_resolved"]); repo_root=Path(__file__).resolve().parents[1]
        self.assertNotEqual(child_home,supervisor_home.resolve()); self.assertNotEqual(child_home,repo_root); self.assertFalse(child_home.is_relative_to(repo_root)); self.assertFalse(child_home.exists())
        self.assertTrue(receipt["child_home_matches_request"]); self.assertEqual(receipt["child_home_initial_entry_count"],0); self.assertEqual(receipt["child_home_credential_files_visible"],0); self.assertTrue(receipt["isolated_home_proven"]); self.assertEqual(receipt["secret_env_count"],0); self.assertTrue(receipt["policy_enforced"]); self.assertNotEqual(receipt["child_pid"],os.getpid())

    def _run_or_real_windows_unavailable(self, call, *, receipt_db: Path, mutation_path: Path | None=None):
        try: return call()
        except SandboxUnavailable as exc:
            if not sys.platform.startswith("win"): raise
            message=str(exc); self.assertNotIn("TypeError",message); self.assertRegex(message,r"WINDOWS|APPCONTAINER|ACL|SANDBOX"); self.assertFalse(receipt_db.exists())
            if mutation_path is not None: self.assertFalse(mutation_path.exists())
            return None

    def test_scout_real_child_process_has_cross_platform_sterile_home_even_without_sources(self):
        supervisor_home=Path.home().resolve()
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/"roles.sqlite3"; runner=IsolatedRoleRunner(db)
            result=self._run_or_real_windows_unavailable(lambda: runner.run("SCOUT","DISCOVER",{"config":{},"sources":[],"minimum":1},network_policy="READ_ONLY_PUBLIC_HTTPS",tool_policy="READ_ONLY_SOURCE_DISCOVERY"),receipt_db=db)
            if result is None: return
            self.assertEqual(result["observations"],[]); store=RoleReceiptStore(db)
            try: receipt=store.latest_by_role()["SCOUT"]; self.assert_sterile_child_receipt(receipt,supervisor_home); self.assertIn("SCOUT",store.summary()["proven_roles"])
            finally: store.close()

    def test_falsifier_duplicate_is_killed_before_network_and_keeps_valid_sterile_receipt(self):
        supervisor_home=Path.home().resolve()
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/"roles.sqlite3"; opp=Opportunity(canonical_opportunity_id="taskmarket:already-submitted",source="taskmarket",authoritative_url="https://example.com/task",upstream_status="OPEN",reward_gross="10",funding_proof={"escrow":"verified"})
            result=self._run_or_real_windows_unavailable(lambda: IsolatedRoleRunner(db).run("FALSIFIER","VERIFY",{"config":{},"opportunity":opp.model_dump(mode="json"),"submitted_ids":[opp.canonical_opportunity_id]},network_policy="READ_ONLY_PUBLIC_HTTPS",tool_policy="AUTHORITATIVE_READ_VERIFY"),receipt_db=db)
            if result is None: return
            self.assertEqual((result["verdict"],result["reason"]),("KILL","ALREADY_SUBMITTED_OR_IN_FLIGHT")); store=RoleReceiptStore(db)
            try: receipt=store.latest_by_role()["FALSIFIER"]; self.assert_sterile_child_receipt(receipt,supervisor_home); self.assertIn("FALSIFIER",store.summary()["proven_roles"])
            finally: store.close()

    def test_doctor_typed_state_survives_process_restart_and_keeps_valid_sterile_receipt(self):
        supervisor_home=Path.home().resolve()
        with tempfile.TemporaryDirectory() as td:
            roles=Path(td)/"roles.sqlite3"; doctor_db=Path(td)/"doctor.sqlite3"; runner=IsolatedRoleRunner(roles)
            first=self._run_or_real_windows_unavailable(lambda: runner.run("DOCTOR","HANDLE",{"state_path":str(doctor_db),"fault":"SOURCE_LIED","key":"source:x","threshold":1,"cooldown_seconds":60},network_policy="DENY",tool_policy="DURABLE_TYPED_RECOVERY"),receipt_db=roles,mutation_path=doctor_db)
            if first is None: return
            self.assertEqual(first["decision"]["action"],"QUARANTINE_BAD_SOURCE")
            reopened=DurableDoctor(doctor_db,threshold=1,cooldown_seconds=60)
            try: self.assertFalse(reopened.available("source:x")); self.assertTrue(reopened.status("source:x")["quarantined"]); self.assertFalse(hasattr(reopened,"recover"))
            finally: reopened.close()
            store=RoleReceiptStore(roles)
            try: receipt=store.latest_by_role()["DOCTOR"]; self.assert_sterile_child_receipt(receipt,supervisor_home); summary=store.summary(); self.assertIn("DOCTOR",summary["proven_roles"]); self.assertFalse(summary["sterile_home_proven"])
            finally: store.close()


if __name__=="__main__": unittest.main()
