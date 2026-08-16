from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


class OciBlockVolumeNormalizerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.normalizer = cls.root / "scripts" / "oci-normalize-bv.py"
        cls.runner = (cls.root / "scripts" / "oci-provision-runner.sh").read_text()
        cls.bootstrap = (cls.root / "scripts" / "bootstrap-oci-atm.sh").read_text()

    def run_normalizer(self, payload: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.normalizer)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )

    def test_empty_inventory_is_exact_zero_safe(self):
        p = self.run_normalizer({"data": []})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout), {"data": []})

    def test_scientific_string_is_canonicalized_to_integer(self):
        p = self.run_normalizer({"data": [{"lifecycle-state": "AVAILABLE", "size-in-gbs": "5E+1"}]})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["data"][0]["size-in-gbs"], 50)

    def test_size_in_mbs_is_safe_fallback(self):
        p = self.run_normalizer({"data": [{"lifecycle-state": "AVAILABLE", "size-in-gbs": "", "size-in-mbs": 51200}]})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["data"][0]["size-in-gbs"], 50)

    def test_active_unresolvable_size_fails_closed(self):
        p = self.run_normalizer({"data": [{"lifecycle-state": "AVAILABLE", "size-in-gbs": ""}]})
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("ACTIVE_VOLUME_SIZE_UNRESOLVABLE", p.stderr)

    def test_terminated_invalid_size_does_not_count(self):
        payload = {"data": [{"lifecycle-state": "TERMINATED", "size-in-gbs": ""}]}
        p = self.run_normalizer(payload)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout), payload)

    def test_bootstrap_uses_normalizing_runner(self):
        self.assertIn("oci-provision-runner.sh", self.bootstrap)
        self.assertIn("oci-normalize-bv.py", self.bootstrap)
        self.assertIn('"$D/oci-provision-runner.sh" "$D/infra.env"', self.bootstrap)
        self.assertIn('--output json', self.runner)
        self.assertIn('export -f oci ssh-keygen', self.runner)

    def test_compute_inventory_confirms_zero_count_before_scalar_a1_sums(self):
        self.assertIn('OCI_COMPUTE_INVENTORY=FALLBACK class=EMPTY_JSON_STDOUT mode=COUNT_THEN_SCALAR_SUMS', self.runner)
        self.assertIn('length(data[?shape==`VM.Standard.A1.Flex`', self.runner)
        self.assertIn('OCI_COMPUTE_INVENTORY=EMPTY_A1_CONFIRMED count=0', self.runner)
        self.assertIn('SCALAR_COUNT_QUERY_FAILED', self.runner)
        self.assertIn('SCALAR_COUNT_INVALID', self.runner)
        self.assertIn('sum(data[?shape==`VM.Standard.A1.Flex`', self.runner)
        self.assertIn('SCALAR_OCPU_QUERY_FAILED', self.runner)
        self.assertIn('SCALAR_MEMORY_QUERY_FAILED', self.runner)
        self.assertIn('SCALAR_OCPU_INVALID', self.runner)
        self.assertIn('SCALAR_MEMORY_INVALID', self.runner)
        self.assertNotIn('EMPTY_STDOUT_AFTER_FULL_JSON_RETRY', self.runner)

    def test_cloud_shell_fips_breakglass_key_is_rsa3072(self):
        self.assertIn('REAL_SSH_KEYGEN="$(command -v ssh-keygen)"', self.runner)
        self.assertIn('out+=("-t" "rsa" "-b" "3072")', self.runner)
        self.assertIn('OCI_BREAKGLASS_KEY_ALGORITHM=RSA3072 reason=FIPS_COMPATIBILITY', self.runner)
        self.assertNotIn('"$REAL_SSH_KEYGEN" -t ed25519', self.runner)

    def test_object_version_inventory_normalizes_data_items_shape(self):
        self.assertIn('"${3:-}" = "list-object-versions"', self.runner)
        self.assertIn('{data:(.data.items // [])}', self.runner)
        self.assertIn('OBJECT_VERSION_INVENTORY_SHAPE_INVALID', self.runner)


if __name__ == "__main__":
    unittest.main()
