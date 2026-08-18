from __future__ import annotations

import unittest
from pathlib import Path

from atm_core.worker_integration import WorkerIntegrationRegistry
from atm_core.workers import WorkerJobSpec, WorkerRegistry

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ROOT / "workers" / "manifests"
INTEGRATIONS = ROOT / "workers" / "integrations"

class Order006WorkerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.manifests = WorkerRegistry.from_directory(MANIFESTS)
        self.integrations = WorkerIntegrationRegistry.from_directory(INTEGRATIONS)

    def _spec(self, *, name: str, capabilities: list[str], max_spend_usd: int = 0) -> WorkerJobSpec:
        return WorkerJobSpec(job_id=f"order006-{name}", canonical_opportunity_id=f"fixture:order006:{name}",
            external_source="order006-fixture", external_url="https://github.com/simonkey888/ATM-Agent-Teller-Machine/issues/31",
            task_type=f"order006_{name}", frozen_acceptance_criteria=["registered router dry-run only"],
            repository_or_input="order006-structured-fixture", max_spend_usd=max_spend_usd,
            required_capabilities=capabilities, structured_requirements=[name], expected_deliverable="routing decision only; no WorkLease",
            deterministic_checks=["registered-not-active"])

    def test_readiness_registration_is_not_activation(self):
        zungun = self.integrations.get("zungun"); across = self.integrations.get("across-edge")
        self.assertTrue(zungun.registered); self.assertFalse(zungun.active)
        self.assertEqual(zungun.source_pin_ancestor, "c95002f94d5c5316e83bfca7215cf1d591a62739")
        self.assertEqual(zungun.source_pin, "2793b3e01f68b7fd0797a393486fb7db07fdeaf7")
        self.assertTrue(across.registered); self.assertFalse(across.active)
        self.assertEqual(across.source_pin_ancestor, "f65e597a650bfaa5b09824b299811c8da713249e")
        self.assertEqual(across.source_pin, "1f57f0a1604f4d75588599214768e26d7edf86a4")
        for worker_id in ("zungun", "across-edge"):
            manifest = self.manifests.get(worker_id); record = self.integrations.get(worker_id)
            self.assertFalse(manifest.enabled); self.assertEqual(manifest.max_concurrency, 1)
            self.assertFalse(record.claim_authority); self.assertFalse(record.submission_authority); self.assertFalse(record.financial_authority)
            self.assertEqual(str(record.max_spend_usd), "0")

    def test_boqa_and_senex_are_placeholders_without_final_source_pin(self):
        for worker_id in ("boqa", "senex-prophet"):
            record = self.integrations.get(worker_id)
            self.assertFalse(record.registered); self.assertFalse(record.active); self.assertIsNone(record.source_pin); self.assertIsNone(record.source_pin_ancestor)
            self.assertFalse(self.manifests.get(worker_id).enabled)

    def test_structured_router_positive_negative_matrix(self):
        zungun_job = self._spec(name="network_reliability", capabilities=["git", "filesystem", "shell", "node", "evidence", "zungun.network_resilience", "zungun.reconciliation"])
        across_job = self._spec(name="web3_readonly", capabilities=["web3_readonly", "unsigned_transaction_validation", "evidence"])
        browser_job = self._spec(name="browser_qa", capabilities=["browser", "qa_repro"])
        research_job = self._spec(name="statistical_research", capabilities=["research", "data_analysis"])
        unknown_job = self._spec(name="unknown", capabilities=["unknown.capability"])
        self.assertEqual(self.integrations.route_registered(zungun_job, self.manifests)[0].worker_id, "zungun")
        self.assertEqual(self.integrations.route_registered(across_job, self.manifests)[0].worker_id, "across-edge")
        self.assertIsNone(self.integrations.route_registered(browser_job, self.manifests)); self.assertIsNone(self.integrations.route_registered(research_job, self.manifests)); self.assertIsNone(self.integrations.route_registered(unknown_job, self.manifests))
        self.assertIsNone(self.integrations.route_active(zungun_job, self.manifests)); self.assertIsNone(self.integrations.route_active(across_job, self.manifests))

    def test_free_text_coincidence_cannot_route(self):
        job = WorkerJobSpec(job_id="order006-free-text", canonical_opportunity_id="fixture:order006:free-text",
            external_source="network resilience reconciliation across web3 zungun", external_url="https://github.com/simonkey888/ATM-Agent-Teller-Machine/issues/31",
            task_type="browser_qa", frozen_acceptance_criteria=["browser only"], repository_or_input="network resilience retry reconciliation web3 across edge",
            max_spend_usd=0, required_capabilities=["browser"], structured_requirements=[], expected_deliverable="browser evidence", deterministic_checks=[])
        self.assertIsNone(self.integrations.route_registered(job, self.manifests))

    def test_nonzero_spend_and_signing_or_broadcast_reject(self):
        with self.assertRaisesRegex(ValueError, "max_spend_usd"): self._spec(name="paid", capabilities=["web3_readonly"], max_spend_usd=1)
        with self.assertRaisesRegex(ValueError, "reserved disabled"): self._spec(name="signing", capabilities=["web3_signer"])
        broadcast = self._spec(name="broadcast", capabilities=["blockchain_broadcast"])
        with self.assertRaisesRegex(ValueError, "signing/broadcast/financial"): self.integrations.route_registered(broadcast, self.manifests)

if __name__ == "__main__": unittest.main()
