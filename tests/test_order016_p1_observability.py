from __future__ import annotations

import unittest
from pathlib import Path


class Order016P1ObservabilityTests(unittest.TestCase):
    def worker(self) -> str:
        return (Path(__file__).resolve().parents[1] / "worker" / "src" / "index.js").read_text(encoding="utf-8")

    def test_edge_health_requires_edge_runtime_radar_exact_sha(self):
        worker = self.worker()
        self.assertIn("runtimeSha===deployed&&radarSha===deployed", worker)
        self.assertIn("edge_runtime_radar_exact", worker)
        self.assertNotIn("version_coherent:/^[0-9a-f]{40}$/.test(deployed)&&radarSha===deployed", worker)

    def test_p1_runtime_claims_are_receipt_fields_not_static_true_flags(self):
        worker = self.worker()
        for field in ("process_boundary_proven", "secretless_env_proven", "tool_network_policy_proven", "role_receipts"):
            self.assertIn(field, worker)
        self.assertNotIn('"ephemeral_workers":true', worker.lower())
        self.assertNotIn('"structural_tool_allowlists":true', worker.lower())

    def test_provider_flywheel_doctor_and_gap_are_sanitized_status_surfaces(self):
        worker = self.worker()
        for field in ("free_provider_fabric", "capability_gap_ledger", "capability_flywheel", "doctor_runtime", "autonomy_fabric"):
            self.assertIn(field, worker)
        self.assertIn("healthy_requires_live_probe", worker)
        self.assertIn("synthetic_is_non_economic", worker)


if __name__ == "__main__":
    unittest.main()
