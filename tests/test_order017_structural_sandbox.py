from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from atm_core.role_runtime import IsolatedRoleRunner, RoleReceipt, RoleReceiptStore
from atm_core.sandbox_boundary_v2 import SandboxUnavailable


class Order017StructuralSandboxTests(unittest.TestCase):
    def _windows_fail_closed(self, call, receipt_db: Path, mutation_path: Path | None = None) -> bool:
        if not sys.platform.startswith("win"):
            return False
        with self.assertRaisesRegex(SandboxUnavailable, "SANDBOX_BACKEND_UNAVAILABLE_NON_LINUX"):
            call()
        self.assertFalse(receipt_db.exists())
        if mutation_path is not None:
            self.assertFalse(mutation_path.exists())
        return True

    def test_scout_real_structural_child_is_secretless_and_owner_home_denied(self):
        base=Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir()).resolve()
        with tempfile.TemporaryDirectory(dir=str(base)) as raw:
            receipt_db=Path(raw)/"roles.sqlite3"; runner=IsolatedRoleRunner(receipt_db,timeout_seconds=30)
            call=lambda: runner.run("SCOUT","DISCOVER",{"sources":[],"config":{},"minimum":1},network_policy="READ_ONLY_PUBLIC_HTTPS",tool_policy="READ_ONLY_SOURCE_DISCOVERY")
            if self._windows_fail_closed(call,receipt_db): return
            result=call(); self.assertEqual(result,{"observations":[]})
            store=RoleReceiptStore(receipt_db)
            try: receipt=RoleReceipt(**store.latest_by_role()["SCOUT"])
            finally: store.close()
            self.assertTrue(receipt.proven); self.assertTrue(receipt.structural_sandbox_proven); self.assertTrue(receipt.owner_home_read_denied_observed); self.assertTrue(receipt.canonical_repo_write_denied_observed); self.assertTrue(receipt.direct_network_denied_observed); self.assertEqual(receipt.secret_env_count,0); self.assertNotEqual(Path(receipt.child_home_resolved),Path.home().resolve()); self.assertTrue(receipt.resource_limits_observed); self.assertEqual(receipt.sandbox_backend,"DOCKER_HOST_MANAGED"); self.assertFalse(receipt.owner_home_mounted)

    def test_falsifier_and_doctor_keep_valid_structural_receipts(self):
        base=Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir()).resolve()
        with tempfile.TemporaryDirectory(dir=str(base)) as raw:
            root=Path(raw); receipt_db=root/"roles.sqlite3"; doctor_db=root/"doctor.sqlite3"; runner=IsolatedRoleRunner(receipt_db,timeout_seconds=30)
            fcall=lambda: runner.run("FALSIFIER","BOUNDARY_PROBE",{"submitted_ids":["already:submitted"]},network_policy="READ_ONLY_PUBLIC_HTTPS",tool_policy="AUTHORITATIVE_READ_VERIFY")
            if self._windows_fail_closed(fcall,receipt_db): return
            f=fcall(); self.assertEqual(f["state"],"BOUNDARY_READY")
            d=runner.run("DOCTOR","INSPECT",{"state_path":str(doctor_db),"threshold":3,"cooldown_seconds":30},network_policy="DENY",tool_policy="DURABLE_TYPED_RECOVERY"); self.assertEqual(d["state"],"BOUNDARY_READY")
            store=RoleReceiptStore(receipt_db)
            try: summary=store.summary()
            finally: store.close()
            for role in ("FALSIFIER","DOCTOR"):
                receipt=RoleReceipt(**summary["latest_receipts"][role]); self.assertTrue(receipt.proven,role); self.assertTrue(receipt.owner_home_read_denied_observed,role); self.assertEqual(receipt.secret_env_count,0,role); self.assertTrue(receipt.resource_limits_applied,role); self.assertFalse(receipt.owner_home_mounted,role)


if __name__=="__main__": unittest.main()
