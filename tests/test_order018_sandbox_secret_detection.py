from __future__ import annotations

import unittest
from unittest.mock import patch

import atm_container_entry as entry


class Order018SandboxSecretDetectionTests(unittest.TestCase):
    def test_pinned_public_gpg_metadata_is_not_supervisor_secret(self):
        with patch.dict("os.environ", {"GPG_KEY": "PUBLIC_RELEASE_SIGNING_FINGERPRINT"}, clear=True):
            self.assertEqual(entry._secret_env_count(), 0)
            self.assertEqual(entry._secret_env_names(), ())

    def test_any_non_allowlisted_secret_bearing_variable_is_detected(self):
        with patch.dict(
            "os.environ",
            {"GPG_KEY": "PUBLIC_RELEASE_SIGNING_FINGERPRINT", "ATTACKER_API_KEY": "must-not-enter-child"},
            clear=True,
        ):
            self.assertEqual(entry._secret_env_count(), 1)
            self.assertEqual(entry._secret_env_names(), ("ATTACKER_API_KEY",))

    def test_empty_marker_variable_has_no_secret_value(self):
        with patch.dict("os.environ", {"SOME_TOKEN": ""}, clear=True):
            self.assertEqual(entry._secret_env_count(), 0)


if __name__ == "__main__":
    unittest.main()
