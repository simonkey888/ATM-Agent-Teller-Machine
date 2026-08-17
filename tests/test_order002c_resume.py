from pathlib import Path
import unittest


class OciResumeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.configure = (root / "scripts" / "oci-configure.sh").read_text()
        cls.authority = (root / "scripts" / "oci-authority.py").read_text()
        cls.fence = (root / "scripts" / "atm-authority-fence.py").read_text()

    def test_existing_remote_secrets_are_reused_before_prompting(self):
        self.assertIn("/etc/atm/control.env", self.configure)
        self.assertIn("/etc/atm/runtime.env", self.configure)
        self.assertIn('${GITHUB_TOKEN:-${GH_TOKEN:-}}', self.configure)
        self.assertIn('${GOOGLE_API_KEY:-}', self.configure)

    def test_windows_is_absent_from_cloud_only_handover(self):
        self.assertNotIn("WINDOWS", self.configure.upper())
        self.assertNotIn("cutover-stop-windows", self.configure)
        self.assertNotIn("WAITING_WINDOWS_STOP", self.configure)

    def test_cloud_state_restore_precedes_authority_promotion(self):
        restore = '/opt/atm/scripts/atm-state-backup.sh restore'
        promote = 'oci-authority.py'
        self.assertIn(restore, self.configure)
        self.assertIn(promote, self.configure)
        self.assertLess(self.configure.index(restore), self.configure.index(promote))

    def test_oci_controller_starts_only_after_cas_and_remote_fence(self):
        promote = 'promote --instance-id "$INSTANCE_ID" --source-sha "$SHA"'
        fence = '/opt/atm/scripts/atm-authority-fence.py'
        controller = 'systemctl enable --now atm-publisher.service atm-controller.service atm-state-backup.timer'
        self.assertIn(promote, self.configure)
        self.assertIn(fence, self.configure)
        self.assertIn(controller, self.configure)
        self.assertLess(self.configure.index(promote), self.configure.index(fence))
        self.assertLess(self.configure.index(fence), self.configure.index(controller))

    def test_post_promotion_failure_stops_instance_before_cas_rollback(self):
        self.assertIn('compute instance action --instance-id "$INSTANCE_ID" --action STOP', self.configure)
        self.assertIn('rollback --instance-id "$INSTANCE_ID" --source-sha "$SHA"', self.configure)
        self.assertIn("ROLLBACK_REFUSED_INSTANCE_NOT_PROVEN_STOPPED", self.authority)

    def test_authority_fence_requires_oci_instance_epoch_and_sha(self):
        for marker in ("holder", "oci_instance_ocid", "epoch", "source_sha"):
            self.assertIn(marker, self.fence)
        self.assertIn('rec.get("holder") == "OCI"', self.fence)


if __name__ == "__main__":
    unittest.main()
