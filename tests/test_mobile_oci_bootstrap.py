from __future__ import annotations

import unittest
from pathlib import Path


class MobileOciBootstrapContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.mobile = (root / "scripts" / "mobile-oci-bootstrap.sh").read_text()

    def test_one_hidden_credential_action(self):
        self.assertEqual(self.mobile.count("read -rsp '"), 1)
        self.assertIn("PAT|GOOGLE_API_KEY", self.mobile)
        self.assertIn("unset BUNDLE", self.mobile)

    def test_credentials_are_not_put_on_command_line_or_exported(self):
        self.assertIn("umask 077", self.mobile)
        self.assertIn('ATM_BOOTSTRAP_CRED_FILE="$(mktemp)"', self.mobile)
        self.assertIn("trap 'rm -f", self.mobile)
        self.assertNotIn("export GH_ONCE", self.mobile)
        self.assertNotIn("export MODEL_ONCE", self.mobile)

    def test_read_override_only_supplies_configure_secrets(self):
        self.assertIn("GH)", self.mobile)
        self.assertIn("MODELKEY)", self.mobile)
        self.assertIn('builtin read "$@"', self.mobile)
        self.assertIn("export -f read", self.mobile)

    def test_mobile_wrapper_keeps_exact_sha_and_wallet_binding(self):
        self.assertIn("ATM_SOURCE_SHA_MUST_BE_EXACT_40HEX", self.mobile)
        self.assertIn("ATM_BASE_WALLET_ADDRESS_MUST_BE_CANONICAL_PUBLIC_BASE_ADDRESS", self.mobile)
        self.assertIn('RAW="https://raw.githubusercontent.com/$REPO/$SHA"', self.mobile)
        self.assertIn('ATM_BASE_WALLET_ADDRESS="$PAYOUT" ATM_SOURCE_SHA="$SHA" bash <(curl -fsSL "$RAW/scripts/bootstrap-oci-atm.sh")', self.mobile)


if __name__ == "__main__":
    unittest.main()
