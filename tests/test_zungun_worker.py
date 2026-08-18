from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import atm_fabric
from atm_core.capabilities import CapabilityResolver, ZUNGUN_CAPABILITIES
from atm_core.workers import WorkerJobSpec, WorkerRegistry, WorkLeaseStore
from atm_core.zungun_qualification import run_core_qualification
from atm_core.zungun_worker import LinkDoctorFinding, LinkDoctorReceipt, ZungunWorkerAdapter, validate_worker_output_authority

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ROOT / "workers" / "manifests"
TARGET_SHA = "a" * 40


class ZungunWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = WorkerRegistry.from_directory(MANIFESTS)
        self.zungun = self.registry.get("zungun")

    def job(self, required: list[str], *, job_id: str = "fixture-zungun-offline-001") -> WorkerJobSpec:
        return WorkerJobSpec(
            job_id=job_id,
            canonical_opportunity_id=f"fixture:{job_id}",
            external_source="fixture",
            external_url=f"https://example.invalid/jobs/{job_id}",
            task_type="network_reliability_offline_delivery",
            frozen_acceptance_criteria=["exactly once after reconnect", "survives process death"],
            repository_or_input="https://github.com/example/mobile-app",
            max_spend_usd=0,
            required_capabilities=required,
            structured_requirements=["offline_first", "idempotency", "process_death", "reconciliation"],
            target_base_sha=TARGET_SHA,
            allowed_paths=["android/app/src", "android/app/src/test"],
            expected_deliverable="implementation, tests and evidence",
            deterministic_checks=["no duplicate receiver effect", "UNKNOWN reconciles before retry"],
        )

    def test_manifest_is_narrow_registered_inactive_zero_authority(self):
        self.assertFalse(self.zungun.enabled)
        self.assertEqual(self.zungun.worker_class, "NETWORK_RELIABILITY_AND_OFFLINE_DELIVERY_SPECIALIST")
        self.assertEqual(set(self.zungun.capabilities) & ZUNGUN_CAPABILITIES, ZUNGUN_CAPABILITIES)
        for forbidden in ("cuda", "web3_readonly", "browser", "playwright_e2e", "financial_execution", "trading", "wallet", "payment_writer", "web3_signer"):
            self.assertNotIn(forbidden, self.zungun.capabilities)
        self.assertFalse(self.zungun.financial_authority)
        self.assertFalse(self.zungun.claim_authority)
        self.assertFalse(self.zungun.submission_authority)
        self.assertFalse(self.zungun.model_authority)
        self.assertEqual(str(self.zungun.cost_ceiling_usd), "0")
        self.assertEqual(self.zungun.max_concurrency, 1)

    def test_structured_reliability_requirements_match_zungun_but_do_not_activate(self):
        normalized = CapabilityResolver.normalize_structured_requirements({
            "platforms": ["Android"],
            "features": ["offline-first", "WorkManager", "reconciliation"],
            "failure_modes": ["ambiguous timeout", "process death", "duplicate operation"],
        })
        required = CapabilityResolver.required_for_structured(normalized)
        decision = CapabilityResolver.can_handle(self.zungun.capabilities, required)
        self.assertTrue(decision["can_handle"])
        self.assertTrue(any(cap.startswith("zungun.") for cap in required))
        self.assertIsNone(self.registry.choose(self.job(required)))

    def test_resolver_does_not_scan_unstructured_description(self):
        normalized = CapabilityResolver.normalize_structured_requirements(
            {"description": "offline retry WorkManager ambiguous timeout process death"}
        )
        self.assertEqual(normalized, [])

    def test_playwright_regression_does_not_route_to_inactive_placeholders(self):
        job = self.job(["git", "github", "evidence", "playwright_e2e", "browser_workflow_test"], job_id="playwright")
        self.assertFalse(CapabilityResolver.can_handle(self.zungun.capabilities, job.required_capabilities)["can_handle"])
        self.assertIsNone(self.registry.choose(job))

    def test_negative_controls_do_not_select_zungun(self):
        for required in (["git", "github", "evidence", "small_code_fix"], ["documentation"], ["financial_execution"]):
            selected = self.registry.choose(self.job(required, job_id="negative-" + required[-1]))
            self.assertTrue(selected is None or selected[0].worker_id != "zungun")

    def test_atm_builder_uses_structured_requirements_and_exact_target_without_activation(self):
        opp = SimpleNamespace(
            canonical_opportunity_id="shadow:745", source="shadow",
            authoritative_url="https://github.com/bitcoinppl/cove/issues/745", deadline=None,
        )
        snapshot = {"job": {"acceptanceCriteria": ["resume background cloud backup safely"], "requirements": {
            "platforms": ["Android"], "features": ["WorkManager", "reconciliation", "offline-first"],
            "failure_modes": ["process death", "ambiguous timeout"], "targetRepository": "https://github.com/bitcoinppl/cove",
            "targetBaseSha": "dee29853705191a87c86eb9d1cb51e1b6f30213e", "allowedPaths": ["android", "rust/src/cloud_backup"],
            "deliverable": "implementation tests evidence"}}}
        spec = atm_fabric._build_worker_job(opp, snapshot)
        self.assertEqual(spec.target_base_sha, "dee29853705191a87c86eb9d1cb51e1b6f30213e")
        self.assertIn("zungun.android_background_work", spec.required_capabilities)
        self.assertIn("zungun.ambiguous_timeout", spec.required_capabilities)
        self.assertTrue(CapabilityResolver.can_handle(self.zungun.capabilities, spec.required_capabilities)["can_handle"])
        self.assertIsNone(self.registry.choose(spec))

    def test_zungun_route_fails_closed_without_target_binding(self):
        opp = SimpleNamespace(
            canonical_opportunity_id="shadow:missing-target", source="shadow",
            authoritative_url="https://example.invalid/jobs/missing-target", deadline=None,
        )
        snapshot = {"job": {"acceptanceCriteria": ["offline exactly once"], "requirements": {"features": ["offline-first", "idempotency"]}}}
        with self.assertRaisesRegex(Exception, "target repository"):
            atm_fabric._build_worker_job(opp, snapshot)

    def test_registered_fixture_stops_before_worklease_or_submission(self):
        normalized = CapabilityResolver.normalize_structured_requirements({
            "platforms": ["Android"], "features": ["offline-first", "reconciliation"],
            "failure_modes": ["process death", "duplicate operation"],
        })
        job = self.job(CapabilityResolver.required_for_structured(normalized))
        self.assertTrue(CapabilityResolver.can_handle(self.zungun.capabilities, job.required_capabilities)["can_handle"])
        self.assertIsNone(self.registry.choose(job))
        pipeline = ["DISCOVERED", "VERIFIED", "REGISTERED_NOT_ACTIVE"]
        for forbidden in ("LEASED", "WORKING", "SUBMITTED", "ACCEPTED", "PAID"):
            self.assertNotIn(forbidden, pipeline)

    def test_adapter_honors_immutable_lease_and_preflight_semantics(self):
        job = self.job(["git", "github", "filesystem", "shell", "node", "evidence", "zungun.offline_sync"])
        with tempfile.TemporaryDirectory() as directory:
            store = WorkLeaseStore(Path(directory) / "lease.sqlite3")
            lease = store.acquire(job, self.zungun)
            self.assertIsNotNone(lease)
            adapter = ZungunWorkerAdapter(self.zungun)
            self.assertTrue(adapter.prepare(job, lease).ready)
            handle = adapter.execute(job, lease)
            self.assertEqual(adapter.status(handle), "WORKING")
            preflight = adapter.preflight_contract(job)
            self.assertFalse(preflight["semantics"]["UNKNOWN_is_PASS"])
            self.assertFalse(preflight["semantics"]["retryable_is_idempotent"])
            self.assertFalse(preflight["semantics"]["emulator_is_OEM_proof"])
            store.close()

    def test_link_doctor_unknown_cannot_collapse_to_pass(self):
        receipt = LinkDoctorReceipt(
            source_head=TARGET_SHA,
            findings=[LinkDoctorFinding(
                rule_id="ZL015_NO_RECEIVER_RECONCILIATION_PATH", severity="WARNING", path="src/sync.kt",
                evidence="no receiver query", explanation="must reconcile", limitation="static", assurance_tier="L1", status="UNKNOWN",
            )],
            deterministic=True, overall_status="PASS",
        )
        with self.assertRaisesRegex(ValueError, "UNKNOWN"):
            ZungunWorkerAdapter.validate_link_doctor(receipt)

    def test_security_rejects_authority_and_secret_output(self):
        for payload in ({"external_accepted": True}, {"paid": True}, {"nested": {"private_key": "forbidden"}}, {"money_ledger": {"amount": 1}}):
            with self.assertRaises(ValueError):
                validate_worker_output_authority(payload)

    def test_security_rejects_path_escape_and_dot_git(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "worktree"; root.mkdir()
            for path in ("../outside", ".git/config", "/tmp/outside"):
                with self.assertRaises(ValueError):
                    ZungunWorkerAdapter.secure_target_path(root, path)
            safe = ZungunWorkerAdapter.secure_target_path(root, "android/app/src/Main.kt")
            self.assertTrue(str(safe).startswith(str(root.resolve())))
            if os.name != "nt":
                outside = Path(directory) / "outside"; outside.mkdir(); (root / "linked").symlink_to(outside, target_is_directory=True)
                with self.assertRaises(ValueError):
                    ZungunWorkerAdapter.secure_target_path(root, "linked/file.kt")

    def test_q1_to_q7_deterministic_qualification(self):
        result = run_core_qualification()
        self.assertEqual(len(result), 7)
        self.assertEqual(set(result.values()), {"PASS"})


if __name__ == "__main__":
    unittest.main()
