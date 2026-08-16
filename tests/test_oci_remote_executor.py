from __future__ import annotations

import unittest
from pathlib import Path


class OciRemoteExecutorContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.workflow = (root / ".github" / "workflows" / "oci-remote-bootstrap.yml").read_text()
        cls.probe = (root / ".github" / "workflows" / "oci-remote-probe.yml").read_text()
        cls.api_boot = (root / "scripts" / "bootstrap-oci-atm-api.sh").read_text()
        cls.wrapper = (root / "scripts" / "github-oci-bootstrap.sh").read_text()
        cls.provision = (root / "scripts" / "oci-provision.sh").read_text()

    def test_remote_workflow_is_trigger_file_gated_not_pr_driven(self):
        self.assertIn("paths: [.github/oci-remote-trigger.txt]", self.workflow)
        self.assertNotIn("pull_request:", self.workflow)
        self.assertIn("cancel-in-progress: false", self.workflow)

    def test_expected_encrypted_secret_names_are_used(self):
        self.assertIn("secrets.OCI_CONFIG", self.workflow)
        self.assertIn("secrets.OCI_PRIVATE_KEY_PEM", self.workflow)
        self.assertIn("secrets.ATM_AGENT_TELLER_MACHINE", self.workflow)
        self.assertIn("OCI_REMOTE_SECRET_MISSING=OCI_CONFIG", self.workflow)
        self.assertIn("OCI_REMOTE_SECRET_MISSING=OCI_PRIVATE_KEY_PEM", self.workflow)
        self.assertIn("OCI_REMOTE_SECRET_MISSING=ATM_AGENT_TELLER_MACHINE", self.workflow)

    def test_oracle_preview_default_profile_and_todo_key_are_normalized(self):
        self.assertIn("stripped == '[DEFAULT]'", self.workflow)
        self.assertIn("out.append('[ATM_REMOTE]')", self.workflow)
        self.assertIn("startswith('key_file=')", self.workflow)
        self.assertIn("OCI_CONFIG_MISSING_KEY_FILE_TODO", self.workflow)
        self.assertIn("chmod 600", self.workflow)

    def test_read_only_probe_is_separate_from_mutating_bootstrap(self):
        self.assertIn("Read-only OCI authentication proof", self.probe)
        self.assertNotIn("github-oci-bootstrap.sh", self.probe)
        self.assertIn("Validate OCI API authentication read-only", self.workflow)
        self.assertIn("bash scripts/github-oci-bootstrap.sh", self.workflow)

    def test_remote_bootstrap_is_exact_sha_and_explicit_executor_only(self):
        self.assertIn("ATM_SOURCE_SHA_MUST_BE_EXACT_40HEX", self.api_boot)
        self.assertIn('ATM_OCI_REMOTE_EXECUTOR:-}', self.api_boot)
        self.assertIn("github_actions", self.api_boot)
        self.assertIn("OCI_API_AUTH_FAILED", self.api_boot)
        self.assertIn('RAW="https://raw.githubusercontent.com/$REPO/$SHA"', self.api_boot)

    def test_bundle_is_not_printed_and_is_removed_from_environment(self):
        self.assertIn('BUNDLE="${ATM_REMOTE_BUNDLE:-}"', self.wrapper)
        self.assertIn("unset BUNDLE REST ATM_REMOTE_BUNDLE", self.wrapper)
        self.assertIn('ATM_BOOTSTRAP_CRED_FILE="$(mktemp)"', self.wrapper)
        self.assertIn("trap cleanup_secret EXIT INT TERM", self.wrapper)
        self.assertNotIn('echo "$BUNDLE"', self.wrapper)
        self.assertNotIn('echo "$ATM_REMOTE_BUNDLE"', self.wrapper)
        self.assertNotIn("set -x", self.wrapper)

    def test_remote_path_reuses_same_a1_zero_spend_provisioner(self):
        self.assertIn("oci-provision.sh", self.api_boot)
        self.assertIn("SHAPE=VM.Standard.A1.Flex", self.provision)
        self.assertIn("c+1<=2 and m+6<=12 and v+50<=200", self.provision)
        self.assertIn("A1_CAPACITY_UNAVAILABLE_ALL_ADS", self.provision)
        self.assertNotIn("VM.Standard.E", self.provision)


if __name__ == "__main__":
    unittest.main()
