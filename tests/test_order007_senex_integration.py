from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import ValidationError

from atm_core.execution_jobs import ExecutionJobStore
from atm_core.senex_integration import (
    SENEX_ACCEPTED_SOURCE_SHA,
    SENEX_ATM_CAPABILITIES,
    SENEX_NATIVE_ENTRYPOINT,
    SENEX_PROTOCOL,
    SenexActuatorProfile,
    SenexCheckoutSubprocessActuator,
    SenexWorkerJobSpec,
    load_senex_profile,
)
from atm_core.worker_integration import WorkerIntegrationRegistry
from atm_core.workers import WorkerJobSpec, WorkerRegistry, WorkLease


ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ROOT / "workers" / "manifests"
INTEGRATIONS = ROOT / "workers" / "integrations"
PROFILES = ROOT / "workers" / "actuators"


class Order007SenexIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.manifests = WorkerRegistry.from_directory(MANIFESTS)
        self.integrations = WorkerIntegrationRegistry.from_directory(INTEGRATIONS)
        self.manifest = self.manifests.get("senex-prophet")
        self.profile = load_senex_profile(PROFILES)

    def generic_spec(self, name: str, capabilities: list[str], max_spend_usd: int = 0) -> WorkerJobSpec:
        return WorkerJobSpec(
            job_id=f"order007-router-{name}",
            canonical_opportunity_id=f"fixture:order007:router:{name}",
            external_source="order007-fixture",
            external_url="https://github.com/simonkey888/ATM-Agent-Teller-Machine/issues/33",
            task_type=f"order007_{name}",
            frozen_acceptance_criteria=["routing-only"],
            repository_or_input="order007-structured-fixture",
            max_spend_usd=max_spend_usd,
            required_capabilities=capabilities,
            structured_requirements=[name],
            expected_deliverable="routing decision only; no WorkLease",
        )

    def senex_spec(self, *, operation: str = "statistical_evaluation", target_label: str = "order007-isolated") -> SenexWorkerJobSpec:
        rows = "[{\"candidate\":0.8,\"baseline\":1.0},{\"candidate\":0.7,\"baseline\":1.0},{\"candidate\":0.75,\"baseline\":1.0},{\"candidate\":0.72,\"baseline\":1.0}]"
        return SenexWorkerJobSpec(
            job_id="order007-senex-unit",
            canonical_opportunity_id="fixture:order007:senex:unit",
            external_source="order007-fixture",
            external_url="https://github.com/simonkey888/ATM-Agent-Teller-Machine/issues/33",
            task_type="senex_statistical_evaluation_v1",
            frozen_acceptance_criteria=["ATM independently recomputes statistical result", "outgoing_spend_usd=0"],
            repository_or_input="order007-frozen-dataset",
            max_spend_usd=0,
            required_capabilities=[operation],
            structured_requirements=["statistical_data_research"],
            allowed_paths=["data", "artifacts"],
            expected_deliverable="SENEX local statistical evidence",
            deterministic_checks=["independent_recompute"],
            senex_payload={
                "operation": operation,
                "target_label": target_label,
                "input_files": [{"path": "data/eval.json", "text": rows}],
                "operation_config": {
                    "input": "data/eval.json",
                    "candidate_field": "candidate",
                    "baseline_field": "baseline",
                    "min_evidence_n": 3,
                },
                "data_provenance": {"source": "order007-frozen-fixture", "complete": True},
                "as_of": "2026-08-18T00:00:00+00:00",
                "cutoff": "2026-08-18T00:00:00+00:00",
            },
        )

    def lease(self, spec: WorkerJobSpec, *, worker_id: str = "senex-prophet", expires_delta: int = 600) -> WorkLease:
        now = datetime.now(timezone.utc)
        return WorkLease(
            lease_id="order007-unit-lease",
            canonical_opportunity_id=spec.canonical_opportunity_id,
            worker_id=worker_id,
            scope_hash=spec.scope_hash,
            acquired_at=now - timedelta(seconds=1),
            expires_at=now + timedelta(seconds=expires_delta),
            heartbeat_at=now,
            terminal_state=None,
        )

    def test_exact_readiness_registration_is_not_activation(self):
        record = self.integrations.get("senex-prophet")
        self.assertTrue(record.registered)
        self.assertFalse(record.active)
        self.assertEqual(record.source_pin, SENEX_ACCEPTED_SOURCE_SHA)
        self.assertEqual(record.source_pin_ancestor, SENEX_ACCEPTED_SOURCE_SHA)
        self.assertEqual(record.source_pin_readiness_ref, "https://github.com/simonkey888/SeneX-Prophet/issues/23#issuecomment-5327721492")
        self.assertFalse(record.claim_authority)
        self.assertFalse(record.submission_authority)
        self.assertFalse(record.financial_authority)
        self.assertFalse(record.payment_authority)
        self.assertFalse(record.live_trading_authority)
        self.assertFalse(record.production_senex_authority)
        self.assertEqual(str(record.max_spend_usd), "0")
        self.assertFalse(self.manifest.enabled)
        self.assertEqual(self.manifest.max_concurrency, 1)
        self.assertEqual(set(self.manifest.capabilities), SENEX_ATM_CAPABILITIES)
        self.assertEqual(self.profile.source_sha, SENEX_ACCEPTED_SOURCE_SHA)
        self.assertEqual(self.profile.task_entrypoint, SENEX_NATIVE_ENTRYPOINT)
        self.assertEqual(self.profile.protocol, SENEX_PROTOCOL)

    def test_router_positive_negative_matrix(self):
        statistical = self.generic_spec("statistical", ["statistical_evaluation"])
        causal = self.generic_spec("causal", ["causal_cutoff_validation"])
        provenance_repair = self.generic_spec("provenance_repair", ["provenance_hash_validation", "bounded_python_data_pipeline_repair"])
        network = self.generic_spec("network", ["zungun.network_resilience", "evidence"])
        browser = self.generic_spec("browser", ["browser", "qa_repro"])
        web3 = self.generic_spec("web3", ["web3_readonly", "unsigned_transaction_validation", "evidence"])
        unknown = self.generic_spec("unknown", ["unknown.capability"])

        self.assertEqual(self.integrations.route_registered(statistical, self.manifests)[0].worker_id, "senex-prophet")
        self.assertEqual(self.integrations.route_registered(causal, self.manifests)[0].worker_id, "senex-prophet")
        self.assertEqual(self.integrations.route_registered(provenance_repair, self.manifests)[0].worker_id, "senex-prophet")
        self.assertEqual(self.integrations.route_registered(network, self.manifests)[0].worker_id, "zungun")
        self.assertIsNone(self.integrations.route_registered(browser, self.manifests))
        self.assertEqual(self.integrations.route_registered(web3, self.manifests)[0].worker_id, "across-edge")
        self.assertIsNone(self.integrations.route_registered(unknown, self.manifests))
        self.assertIsNone(self.integrations.route_active(statistical, self.manifests))
        with self.assertRaisesRegex(ValueError, "signing/broadcast/financial"):
            self.integrations.route_registered(self.generic_spec("trading", ["live_trading"]), self.manifests)
        with self.assertRaisesRegex(ValueError, "signing/broadcast/financial"):
            self.integrations.route_registered(self.generic_spec("order_placement", ["order_placement"]), self.manifests)
        with self.assertRaises(ValidationError):
            self.generic_spec("paid", ["statistical_evaluation"], max_spend_usd=1)

    def test_wrong_source_sha_rejected_before_execution(self):
        raw = self.profile.model_dump(mode="json")
        raw["source_sha"] = "0" * 40
        with self.assertRaisesRegex(ValidationError, "independently accepted pin"):
            SenexActuatorProfile.model_validate(raw)

    def test_expired_wrong_worker_unsupported_and_production_targets_reject_pre_execution(self):
        spec = self.senex_spec()
        expired = self.lease(spec, expires_delta=-1)
        with self.assertRaisesRegex(ValueError, "expired"):
            SenexCheckoutSubprocessActuator._validate_input(spec, expired, self.manifest, self.profile)
        wrong_worker = self.lease(spec, worker_id="zungun")
        with self.assertRaisesRegex(ValueError, "wrong worker"):
            SenexCheckoutSubprocessActuator._validate_input(spec, wrong_worker, self.manifest, self.profile)
        unsupported = self.senex_spec(operation="unknown.capability")
        with self.assertRaisesRegex(ValueError, "unsupported SENEX capability"):
            SenexCheckoutSubprocessActuator._validate_input(unsupported, self.lease(unsupported), self.manifest, self.profile)
        production = self.senex_spec(target_label="RUNTIME017-production-runtime")
        with self.assertRaisesRegex(ValueError, "production/RUNTIME017"):
            SenexCheckoutSubprocessActuator._validate_input(production, self.lease(production), self.manifest, self.profile)

    def test_execution_store_identity_is_durable_before_launch(self):
        spec = self.senex_spec(); lease = self.lease(spec)
        with tempfile.TemporaryDirectory() as directory:
            store = ExecutionJobStore(Path(directory) / "execution.sqlite3")
            job = store.create_or_get(
                opportunity_id=spec.canonical_opportunity_id,
                worker_id="senex-prophet",
                worker_version_or_source_sha=SENEX_ACCEPTED_SOURCE_SHA,
                work_lease_id=lease.lease_id,
                scope_hash=spec.scope_hash,
                job_spec_hash=__import__("atm_core.workers", fromlist=["canonical_hash"]).canonical_hash(spec.model_dump(mode="json")),
            )
            again = store.create_or_get(
                opportunity_id=spec.canonical_opportunity_id,
                worker_id="senex-prophet",
                worker_version_or_source_sha=SENEX_ACCEPTED_SOURCE_SHA,
                work_lease_id=lease.lease_id,
                scope_hash=spec.scope_hash,
                job_spec_hash=job.job_spec_hash,
            )
            self.assertEqual(job.execution_job_id, again.execution_job_id)
            store.close()


if __name__ == "__main__":
    unittest.main()
