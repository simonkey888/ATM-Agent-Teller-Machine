from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from atm_core.actuator import ActuatorProfile, CheckoutSubprocessActuator, load_actuator_profile, safe_worker_env
from atm_core.execution_jobs import ExecutionAck, ExecutionJobStore, ExecutionStatus, ProgressReceipt, utcnow_iso
from atm_core.workers import WorkerJobSpec, WorkerRegistry, WorkLease, WorkLeaseStore, canonical_hash

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ROOT / "workers" / "manifests"
PROFILES = ROOT / "workers" / "actuators"


class ExecutionActuatorContractTests(unittest.TestCase):
    def setUp(self):
        self.registry = WorkerRegistry.from_directory(MANIFESTS)

    def _spec(self, worker_id="boqa"):
        profile = load_actuator_profile(PROFILES, worker_id)
        return WorkerJobSpec(
            job_id=f"fixture-{worker_id}", canonical_opportunity_id=f"fixture:{worker_id}",
            external_source="fixture", external_url="https://example.invalid/fixture", task_type="fixture",
            frozen_acceptance_criteria=["worker starts", "checker independently passes"],
            repository_or_input=self.registry.get(worker_id).repo_url, max_spend_usd=0,
            required_capabilities=["git", "filesystem", "shell", "node", "evidence"],
            target_base_sha=profile.source_sha, allowed_paths=["test"],
        )

    def test_profiles_are_pinned_zero_secret_workers(self):
        boqa = load_actuator_profile(PROFILES, "boqa")
        zungun = load_actuator_profile(PROFILES, "zungun")
        self.assertEqual(boqa.source_sha, "797dbf53e1cccf9521d3e2af9b8dc723fc7d1ca1")
        self.assertEqual(zungun.source_sha, "f3f59767cbc5f10b78d838b2ab8fbc28f9559a7b")
        self.assertEqual(boqa.task_entrypoint, "tools/atm-worker-entrypoint.mjs")
        self.assertEqual(zungun.task_entrypoint, "tools/atm-worker-entrypoint.mjs")
        for worker_id in ("boqa", "zungun"):
            manifest = self.registry.get(worker_id)
            self.assertFalse(manifest.enabled)
            self.assertEqual(str(manifest.cost_ceiling_usd), "0")
            self.assertFalse(manifest.financial_authority)
            self.assertFalse(getattr(manifest, "claim_authority", False))
            self.assertFalse(getattr(manifest, "submission_authority", False))
            self.assertFalse(getattr(manifest, "model_authority", False))

    def test_execution_identity_persisted_before_dispatch_and_duplicate_collapses(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ExecutionJobStore(Path(directory) / "execution.sqlite3")
            spec = self._spec(); source = load_actuator_profile(PROFILES, "boqa").source_sha
            job = store.create_or_get(opportunity_id=spec.canonical_opportunity_id, worker_id="boqa",
                worker_version_or_source_sha=source, work_lease_id="lease-1", scope_hash=spec.scope_hash,
                job_spec_hash=canonical_hash(spec.model_dump(mode="json")))
            first, launch = store.begin_dispatch(job.execution_job_id)
            self.assertTrue(launch); self.assertEqual(first.launch_count, 1)
            second, launch2 = store.begin_dispatch(job.execution_job_id)
            self.assertFalse(launch2); self.assertEqual(second.execution_job_id, first.execution_job_id); self.assertEqual(second.launch_count, 1)
            store.close()

    def test_ack_exact_binding_and_conflicting_duplicate_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ExecutionJobStore(Path(directory) / "execution.sqlite3")
            spec = self._spec(); source = load_actuator_profile(PROFILES, "boqa").source_sha
            job = store.create_or_get(opportunity_id=spec.canonical_opportunity_id, worker_id="boqa",
                worker_version_or_source_sha=source, work_lease_id="lease-1", scope_hash=spec.scope_hash,
                job_spec_hash=canonical_hash(spec.model_dump(mode="json")))
            store.begin_dispatch(job.execution_job_id)
            good = ExecutionAck(execution_job_id=job.execution_job_id, work_lease_id="lease-1", scope_hash=spec.scope_hash,
                worker_id="boqa", source_sha=source, acknowledged_at="2026-08-17T00:00:00+00:00")
            store.acknowledge(good)
            with self.assertRaisesRegex(ValueError, "binding mismatch"):
                store.acknowledge(good.model_copy(update={"worker_id": "zungun"}))
            with self.assertRaisesRegex(ValueError, "conflicting duplicate"):
                store.acknowledge(good.model_copy(update={"acknowledged_at": "2026-08-17T00:01:00+00:00"}))
            store.close()

    def test_progress_receipt_hash_and_measurable_progress(self):
        receipt = ProgressReceipt(execution_job_id="exec-x", checkpoint_seq=1, objective_hash="a" * 64,
            artifact_before_hash="b" * 64, artifact_after_hash="c" * 64, tests_run=["unit"], new_evidence_refs=[],
            blocker_class="NONE", uncertainty_or_acceptance_delta="", recommendation="CONTINUE", created_at=utcnow_iso())
        self.assertTrue(receipt.measurable_progress); self.assertRegex(receipt.receipt_hash, r"^[0-9a-f]{64}$")
        self.assertFalse(receipt.model_copy(update={"artifact_after_hash": "b" * 64}).measurable_progress)

    def test_worker_env_excludes_ambient_credentials(self):
        old = dict(os.environ)
        try:
            os.environ["GITHUB_TOKEN"] = "secret"; os.environ["OCI_PRIVATE_KEY_PEM"] = "secret"; os.environ["ATM_REMOTE_BUNDLE"] = "secret"
            with tempfile.TemporaryDirectory() as directory:
                store = ExecutionJobStore(Path(directory) / "execution.sqlite3")
                spec = self._spec(); source = load_actuator_profile(PROFILES, "boqa").source_sha
                job = store.create_or_get(opportunity_id=spec.canonical_opportunity_id, worker_id="boqa",
                    worker_version_or_source_sha=source, work_lease_id="lease-1", scope_hash=spec.scope_hash,
                    job_spec_hash=canonical_hash(spec.model_dump(mode="json")))
                env = safe_worker_env(Path(directory) / "home", job)
                self.assertNotIn("GITHUB_TOKEN", env); self.assertNotIn("OCI_PRIVATE_KEY_PEM", env); self.assertNotIn("ATM_REMOTE_BUNDLE", env)
                self.assertEqual(env["ATM_MAX_SPEND_USD"], "0"); store.close()
        finally:
            os.environ.clear(); os.environ.update(old)

    def test_terminal_or_mismatched_lease_cannot_execute(self):
        spec = self._spec(); manifest = self.registry.get("boqa"); profile = load_actuator_profile(PROFILES, "boqa")
        now = datetime.now(timezone.utc)
        bad = WorkLease(lease_id="lease-x", canonical_opportunity_id=spec.canonical_opportunity_id, worker_id="zungun",
            scope_hash=spec.scope_hash, acquired_at=now, expires_at=now + timedelta(minutes=5), heartbeat_at=now, terminal_state=None)
        with self.assertRaisesRegex(ValueError, "wrong worker"):
            CheckoutSubprocessActuator._validate_input(spec, bad, manifest, profile)
        terminal = bad.model_copy(update={"worker_id": "boqa", "terminal_state": "SUBMITTED"})
        with self.assertRaisesRegex(ValueError, "terminal"):
            CheckoutSubprocessActuator._validate_input(spec, terminal, manifest, profile)


if __name__ == "__main__":
    unittest.main()
