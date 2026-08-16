from pathlib import Path
import unittest


class OciResumeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.configure = (root / "scripts" / "oci-configure.sh").read_text()

    def test_existing_remote_secrets_are_reused_before_prompting(self):
        self.assertIn("/etc/atm/control.env", self.configure)
        self.assertIn("/etc/atm/runtime.env", self.configure)
        self.assertIn('${GITHUB_TOKEN:-${GH_TOKEN:-}}', self.configure)
        self.assertIn('${GOOGLE_API_KEY:-}', self.configure)

    def test_windows_stop_gap_ends_in_safe_github_continuation_state(self):
        marker = "ORDER_002C_BOOTSTRAP_STATUS=WAITING_WINDOWS_STOP_GITHUB_CONTINUATION"
        start = 'ID="order002c-oci-start-$SHA"'
        self.assertIn(marker, self.configure)
        self.assertIn('"supervisor_systemd":"STOPPED"', self.configure)
        self.assertLess(self.configure.index(marker), self.configure.index(start))

    def test_waiting_state_exits_without_starting_supervisor(self):
        waiting_block = self.configure.split('if [ -z "$WIN" ]; then', 1)[1].split("fi", 1)[0]
        self.assertIn("exit 0", waiting_block)
        self.assertNotIn("order002c-oci-start", waiting_block)


if __name__ == "__main__":
    unittest.main()
