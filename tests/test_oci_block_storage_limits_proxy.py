from __future__ import annotations

import unittest
from pathlib import Path


class OciBlockStorageLimitsProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.runner = (root / "scripts" / "oci-provision-runner.sh").read_text()
        cls.provision = (root / "scripts" / "oci-provision.sh").read_text()

    def test_uses_authoritative_combined_block_storage_limit(self):
        self.assertIn("limits resource-availability get", self.runner)
        self.assertIn("--service-name block-storage", self.runner)
        self.assertIn("--limit-name total-storage-gb", self.runner)
        self.assertIn("--availability-domain", self.runner)

    def test_does_not_double_count_boot_and_block_usage(self):
        self.assertIn('if [ "${2:-}" = "volume" ]; then', self.runner)
        self.assertIn("'{\"data\":[]}'", self.runner)
        self.assertIn('"size-in-gbs":$used', self.runner)

    def test_usage_response_is_strict_and_fail_closed(self):
        self.assertIn("fractional-usage", self.runner)
        self.assertIn("fractionalUsage", self.runner)
        self.assertIn("USAGE_FIELD_MISSING_OR_INVALID", self.runner)
        self.assertIn("RESOURCE_AVAILABILITY_UNSUPPORTED", self.runner)
        self.assertIn("NOT_AUTHORIZED_OR_NOT_FOUND", self.runner)

    def test_original_200gb_hard_cap_is_still_independent(self):
        self.assertIn("c+1<=2 and m+6<=12 and v+50<=200", self.provision)
        self.assertNotIn("Pay As You Go", self.provision)


if __name__ == "__main__":
    unittest.main()
