from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from atm_core.role_runtime import IsolatedRoleRunner, RoleReceipt, RoleReceiptStore


class Order017StructuralSandboxTests(unittest.TestCase):
    def test_scout_real_structural_child_is_secretless_and_owner_home_denied(self):
        base = Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir()).resolve()
        with tempfile.TemporaryDirectory(dir=str(base)) as raw:
            receipt_db = Path(raw) / "roles.sqlite3"
            runner = IsolatedRoleRunner(receipt_db, timeout_seconds=30)
            result = runner.run(
                "SCOUT", "DISCOVER", {"sources": [], "config": {}, "minimum": 1},
                network_policy="READ_ONLY_PUBLIC_HTTPS", tool_policy="READ_ONLY_SOURCE_DISCOVERY",
            )
            self.assertEqual(result, {"observations": []})
            store = RoleReceiptStore(receipt_db)
            try:
                receipt = RoleReceipt(**store.latest_by_role()["SCOUT"])
            finally:
                store.close()
            self.assertTrue(receipt.proven)
            self.assertTrue(receipt.structural_sandbox_proven)
            self.assertTrue(receipt.owner_home_read_denied_observed)
            self.assertTrue(receipt.canonical_repo_write_denied_observed)
            self.assertTrue(receipt.direct_network_denied_observed)
            self.assertEqual(receipt.secret_env_count, 0)
            self.assertNotEqual(Path(receipt.child_home_resolved), Path.home().resolve())
            self.assertTrue(receipt.resource_limits_observed)

    def test_falsifier_and_doctor_keep_valid_structural_receipts(self):
        base = Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir()).resolve()
        with tempfile.TemporaryDirectory(dir=str(base)) as raw:
            root = Path(raw)
            runner = IsolatedRoleRunner(root / "roles.sqlite3", timeout_seconds=30)
            f = runner.run(
                "FALSIFIER", "BOUNDARY_PROBE", {"submitted_ids": ["already:submitted"]},
                network_policy="READ_ONLY_PUBLIC_HTTPS", tool_policy="AUTHORITATIVE_READ_VERIFY",
            )
            self.assertEqual(f["state"], "BOUNDARY_READY")
            d = runner.run(
                "DOCTOR", "INSPECT", {"state_path": str(root / "doctor.sqlite3"), "threshold": 3, "cooldown_seconds": 30},
                network_policy="DENY", tool_policy="DURABLE_TYPED_RECOVERY",
            )
            self.assertEqual(d["state"], "BOUNDARY_READY")
            store = RoleReceiptStore(root / "roles.sqlite3")
            try:
                summary = store.summary()
            finally:
                store.close()
            for role in ("FALSIFIER", "DOCTOR"):
                receipt = RoleReceipt(**summary["latest_receipts"][role])
                self.assertTrue(receipt.proven, role)
                self.assertTrue(receipt.owner_home_read_denied_observed, role)
                self.assertEqual(receipt.secret_env_count, 0, role)
                self.assertTrue(receipt.resource_limits_applied, role)


if __name__ == "__main__":
    unittest.main()
