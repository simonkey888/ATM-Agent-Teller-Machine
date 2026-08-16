from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


class OciComputeNormalizerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.script = root / "scripts" / "oci-normalize-compute.py"
        cls.runner = (root / "scripts" / "oci-provision-runner.sh").read_text()
        cls.bootstrap = (root / "scripts" / "bootstrap-oci-atm.sh").read_text()

    def run_norm(self, payload: object):
        return subprocess.run(
            [sys.executable, str(self.script)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )

    def test_empty_inventory_is_zero_zero(self):
        p = self.run_norm({"data": []})
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout, "0\t0\n")

    def test_direct_data_array_is_supported(self):
        p = self.run_norm([])
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout, "0\t0\n")

    def test_numeric_strings_and_scientific_notation_are_canonicalized(self):
        p = self.run_norm([
            {"shape": "VM.Standard.A1.Flex", "lifecycle-state": "RUNNING", "shape-config": {"ocpus": "1E+0", "memory-in-gbs": "6.0"}},
            {"shape": "VM.Standard.A1.Flex", "lifecycle-state": "STOPPED", "shape-config": {"ocpus": 0.5, "memory-in-gbs": 3}},
        ])
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout, "1.5\t9\n")

    def test_terminated_a1_is_not_counted(self):
        p = self.run_norm({"data": [
            {"shape": "VM.Standard.A1.Flex", "lifecycle-state": "TERMINATED", "shape-config": None}
        ]})
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout, "0\t0\n")

    def test_active_a1_missing_shape_config_fails_closed(self):
        p = self.run_norm({"data": [
            {"shape": "VM.Standard.A1.Flex", "lifecycle-state": "RUNNING"}
        ]})
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("A1_SHAPE_CONFIG_MISSING_OR_INVALID", p.stderr)

    def test_active_a1_invalid_numeric_fails_closed(self):
        p = self.run_norm({"data": [
            {"shape": "VM.Standard.A1.Flex", "lifecycle-state": "RUNNING", "shape-config": {"ocpus": "NaN", "memory-in-gbs": 6}}
        ]})
        self.assertNotEqual(p.returncode, 0)

    def test_missing_data_envelope_fails_closed(self):
        p = self.run_norm({"items": []})
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("DATA_FIELD_MISSING", p.stderr)

    def test_runner_forces_json_data_query_and_preserves_display_lookup(self):
        self.assertIn('OCI_COMPUTE_INVENTORY=FAIL class=NORMALIZATION_FAILED', self.runner)
        self.assertIn('OCI_COMPUTE_INVENTORY=FAIL class=EMPTY_STDOUT', self.runner)
        self.assertIn('python3 "$D/oci-normalize-compute.py"', self.runner)
        self.assertIn('--output json --query data', self.runner)
        self.assertIn('"--display-name"', self.runner)
        self.assertIn('if [ "$has_display" -eq 0 ]', self.runner)

    def test_bootstrap_downloads_compute_normalizer(self):
        self.assertIn("oci-normalize-compute.py", self.bootstrap)


if __name__ == "__main__":
    unittest.main()
