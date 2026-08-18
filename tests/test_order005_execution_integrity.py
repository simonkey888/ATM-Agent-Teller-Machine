from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import atm_fabric
import atm_core.execution_integrity as execution_integrity_module
from atm_core.actuator import ActuatorProfile, CheckoutSubprocessActuator
from atm_core.execution_integrity import ProductionExecutionIntegrity, RecoveryOutcome, lease_is_executable
from atm_core.execution_jobs import ExecutionAck, ExecutionJobStore, ExecutionStatus, ProgressReceipt, utcnow_iso
from atm_core.models import Opportunity, Phase, RuntimeState
from atm_core.workers import WorkerJobSpec, WorkerManifest, WorkerRegistry, WorkLease, WorkLeaseStore, canonical_hash, canonical_json


SOURCE = "1" * 40


class Order005ExecutionIntegrityTests(unittest.TestCase):
    def _manifest(self) -> WorkerManifest:
        return WorkerManifest(
            worker_id="fixture-worker",
            repo_url="https://github.com/example/fixture-worker",
            enabled=True,
            capabilities=["git", "github", "filesystem", "shell", "evidence"],
            execution_backend="github_repository_worker",
            entrypoint="repository-contract",
            max_concurrency=1,
            timeout_seconds=300,
            network_policy="public-github-zero-spend",
            expected_artifacts=["content_hashes"],
            cost_ceiling_usd=0,
            financial_authority=False,
        )

    def _profile(self) -> ActuatorProfile:
        return ActuatorProfile(
            worker_id="fixture-worker",
            source_sha=SOURCE,
            execute_commands=[["python", "-c", "print('execute')"]],
            checker_commands=[["python", "-c", "print('check')"]],
        )

    def _spec(self) -> WorkerJobSpec:
        return WorkerJobSpec(
            job_id="fixture-job",
            canonical_opportunity_id="fixture:fixture-job",
            external_source="fixture",
            external_url="https://example.invalid/fixture-job",
            task_type="fixture",
            frozen_acceptance_criteria=["exact artifact binding", "independent checker proof"],
            repository_or_input="https://github.com/example/fixture-worker",
            max_spend_usd=0,
            required_capabilities=["git", "github", "filesystem", "shell", "evidence"],
        )

    def _lease(self, spec: WorkerJobSpec, *, expires_at: datetime | None = None) -> WorkLease:
        now = datetime.now(timezone.utc)
        return WorkLease(
            lease_id="lease-order005",
            canonical_opportunity_id=spec.canonical_opportunity_id,
            worker_id="fixture-worker",
            scope_hash=spec.scope_hash,
            acquired_at=now - timedelta(seconds=5),
            expires_at=expires_at or now + timedelta(minutes=5),
            heartbeat_at=now,
            terminal_state=None,
        )

    @staticmethod
    def _fake_dispatch(self_actuator, spec, lease, manifest, profile):
        store = self_actuator.store
        job = store.create_or_get(
            opportunity_id=spec.canonical_opportunity_id,
            worker_id=manifest.worker_id,
            worker_version_or_source_sha=profile.source_sha,
            work_lease_id=lease.lease_id,
            scope_hash=spec.scope_hash,
            job_spec_hash=canonical_hash(spec.model_dump(mode="json")),
        )
        job, launch = store.begin_dispatch(job.execution_job_id)
        if not launch:
            return job
        store.acknowledge(ExecutionAck(
            execution_job_id=job.execution_job_id,
            work_lease_id=lease.lease_id,
            scope_hash=spec.scope_hash,
            worker_id=manifest.worker_id,
            source_sha=profile.source_sha,
            acknowledged_at=utcnow_iso(),
        ))
        store.running(job.execution_job_id)
        before = "0" * 64
        for seq, after in ((1, "a" * 64), (2, "b" * 64)):
            store.add_progress(ProgressReceipt(
                execution_job_id=job.execution_job_id,
                checkpoint_seq=seq,
                objective_hash=spec.scope_hash,
                artifact_before_hash=before,
                artifact_after_hash=after,
                tests_run=[f"fixture-step-{seq}"],
                new_evidence_refs=[f"fixture:evidence:{seq}"],
                blocker_class="NONE",
                uncertainty_or_acceptance_delta=f"progress-{seq}",
                recommendation="CONTINUE",
                created_at=utcnow_iso(),
            ))
            before = after
        artifact = {
            "schema": "ATM_EXECUTION_ARTIFACT_V1",
            "execution_job_id": job.execution_job_id,
            "worker_id": manifest.worker_id,
            "worker_source_sha": profile.source_sha,
            "work_lease_id": lease.lease_id,
            "scope_hash": spec.scope_hash,
            "job_spec_hash": canonical_hash(spec.model_dump(mode="json")),
            "command_receipts": [{"argv": ["fixture"], "returncode": 0}],
            "outgoing_spend_usd": 0,
        }
        raw = (canonical_json(artifact) + "\n").encode("utf-8")
        (self_actuator.artifact_root / f"{job.execution_job_id}.json").write_bytes(raw)
        store.result_ready(job.execution_job_id, hashlib.sha256(raw).hexdigest())
        return store.terminal(job.execution_job_id, checker_hash="weak-fixture-checker", reason="FIXTURE_BASE_CHECKER")

    def _seed_recovery_artifact(self, integrity, store, job, spec, lease, manifest, profile):
        integrity.freeze_acceptance(job, spec)
        before = "0" * 64
        for seq, after in ((1, "c" * 64), (2, "d" * 64)):
            store.add_progress(ProgressReceipt(
                execution_job_id=job.execution_job_id,
                checkpoint_seq=seq,
                objective_hash=spec.scope_hash,
                artifact_before_hash=before,
                artifact_after_hash=after,
                tests_run=[f"recovery-{seq}"],
                new_evidence_refs=[f"recovery:{seq}"],
                uncertainty_or_acceptance_delta="measurable",
                created_at=utcnow_iso(),
            ))
            before = after
        artifact = {
            "schema": "ATM_EXECUTION_ARTIFACT_V1",
            "execution_job_id": job.execution_job_id,
            "worker_id": manifest.worker_id,
            "worker_source_sha": profile.source_sha,
            "work_lease_id": lease.lease_id,
            "scope_hash": spec.scope_hash,
            "job_spec_hash": canonical_hash(spec.model_dump(mode="json")),
            "command_receipts": [{"argv": ["recovery-fixture"], "returncode": 0}],
            "outgoing_spend_usd": 0,
        }
        raw = (canonical_json(artifact) + "\n").encode("utf-8")
        integrity.artifact_path(job.execution_job_id).write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()

    def test_lease_boundary_and_zero_launch_for_expired_equal(self):
        spec, manifest, profile = self._spec(), self._manifest(), self._profile()
        t0 = datetime(2026, 8, 18, 8, 0, 0, tzinfo=timezone.utc)
        cases = [
            ("expired", t0 - timedelta(microseconds=1), False),
            ("equal", t0, False),
            ("future", t0 + timedelta(microseconds=1), True),
        ]
        for name, expiry, executable in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                lease = self._lease(spec, expires_at=expiry)
                self.assertEqual(lease_is_executable(lease, t0), executable)
                root = Path(directory)
                store = ExecutionJobStore(root / "execution.sqlite3")
                integrity = ProductionExecutionIntegrity(store, root / "work", root / "artifacts")
                original = execution_integrity_module.lease_is_executable
                with (
                    patch.object(execution_integrity_module, "lease_is_executable", side_effect=lambda value, now=None: original(value, t0)),
                    patch.object(CheckoutSubprocessActuator, "dispatch", autospec=True, side_effect=self._fake_dispatch) as dispatch,
                ):
                    if executable:
                        job, _ = integrity.execute(spec, lease, manifest, profile)
                        self.assertEqual(dispatch.call_count, 1)
                        self.assertEqual(job.launch_count, 1)
                    else:
                        with self.assertRaisesRegex(ValueError, "expired or expires exactly now"):
                            integrity.execute(spec, lease, manifest, profile)
                        dispatch.assert_not_called()
                        job_id = ExecutionJobStore.identity(
                            spec.canonical_opportunity_id, manifest.worker_id, lease.lease_id,
                            spec.scope_hash, canonical_hash(spec.model_dump(mode="json")),
                        )
                        job = store.get(job_id)
                        self.assertEqual(job.launch_count, 0)
                        self.assertEqual(job.status, ExecutionStatus.EXPIRED)
                store.close()

    def test_actual_production_phase_uses_executionjob_actuator_and_recovery(self):
        spec, manifest, profile = self._spec(), self._manifest(), self._profile()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fabric_db = root / "fabric.sqlite3"
            lease_store = WorkLeaseStore(fabric_db)
            lease = lease_store.acquire(spec, manifest, ttl_seconds=300)
            self.assertIsNotNone(lease)
            lease_store.close()
            opp = Opportunity(
                canonical_opportunity_id=spec.canonical_opportunity_id,
                source="fixture",
                authoritative_url=spec.external_url,
                upstream_status="claimed",
                reward_gross=1,
                worker_job_spec=spec.model_dump(mode="json"),
                worker_id=manifest.worker_id,
                worker_lease_id=lease.lease_id,
            )
            state = RuntimeState(phase=Phase.WORK, active_opportunity=opp)
            registry = WorkerRegistry([manifest])
            with (
                patch.object(atm_fabric, "FABRIC_DB", fabric_db),
                patch.object(atm_fabric, "EXECUTION_DB", root / "execution.sqlite3"),
                patch.object(atm_fabric, "EXECUTION_WORKSPACE", root / "work"),
                patch.object(atm_fabric, "EXECUTION_ARTIFACTS", root / "artifacts"),
                patch.object(atm_fabric, "_registry", return_value=registry),
                patch.object(atm_fabric, "load_actuator_profile", return_value=profile),
                patch.object(CheckoutSubprocessActuator, "dispatch", autospec=True, side_effect=self._fake_dispatch) as dispatch,
                patch.object(atm_fabric, "_fetch_public_worker_json") as passive,
            ):
                atm_fabric.phase_work({}, state)
                self.assertEqual(state.phase, Phase.CHECK)
                self.assertEqual(dispatch.call_count, 1)
                passive.assert_not_called()
                execution_id = state.active_opportunity.execution_job_id
                store = ExecutionJobStore(root / "execution.sqlite3")
                integrity = ProductionExecutionIntegrity(store, root / "work", root / "artifacts")
                job = store.get(execution_id)
                checker = integrity.checker_receipt(execution_id)
                self.assertEqual(job.launch_count, 1)
                self.assertEqual(job.status, ExecutionStatus.TERMINAL)
                self.assertGreaterEqual(len(store.progress(execution_id)), 2)
                self.assertIsNotNone(checker)
                self.assertEqual(checker.checker_id, "ATM_INDEPENDENT_CHECKER_V1")
                self.assertEqual(checker.receipt_hash, job.checker_hash)
                store.close()
                # Simulated supervisor re-entry uses the durable terminal identity; dispatch remains exactly once.
                state.phase = Phase.WORK
                atm_fabric.phase_work({}, state)
                self.assertEqual(dispatch.call_count, 1)
                self.assertEqual(state.active_opportunity.execution_recovery, RecoveryOutcome.RECONCILE_RESULT.value)
                atm_fabric.phase_check({}, state)
                self.assertEqual(state.phase, Phase.SUBMIT)

    def test_midflight_recovery_states_never_duplicate_launch_or_orphan(self):
        spec, manifest, profile = self._spec(), self._manifest(), self._profile()
        for wanted in (
            ExecutionStatus.DISPATCHING,
            ExecutionStatus.ACKNOWLEDGED,
            ExecutionStatus.RUNNING,
            ExecutionStatus.RESULT_READY,
            ExecutionStatus.CHECKING,
        ):
            with self.subTest(status=wanted), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                store = ExecutionJobStore(root / "execution.sqlite3")
                integrity = ProductionExecutionIntegrity(store, root / "work", root / "artifacts")
                lease = self._lease(spec)
                job = store.create_or_get(
                    opportunity_id=spec.canonical_opportunity_id,
                    worker_id=manifest.worker_id,
                    worker_version_or_source_sha=profile.source_sha,
                    work_lease_id=lease.lease_id,
                    scope_hash=spec.scope_hash,
                    job_spec_hash=canonical_hash(spec.model_dump(mode="json")),
                )
                integrity.freeze_acceptance(job, spec)
                job, launched = store.begin_dispatch(job.execution_job_id)
                self.assertTrue(launched)
                if wanted in {ExecutionStatus.ACKNOWLEDGED, ExecutionStatus.RUNNING}:
                    store.acknowledge(ExecutionAck(
                        execution_job_id=job.execution_job_id,
                        work_lease_id=lease.lease_id,
                        scope_hash=spec.scope_hash,
                        worker_id=manifest.worker_id,
                        source_sha=profile.source_sha,
                        acknowledged_at=utcnow_iso(),
                    ))
                if wanted == ExecutionStatus.RUNNING:
                    store.running(job.execution_job_id)
                if wanted in {ExecutionStatus.RESULT_READY, ExecutionStatus.CHECKING}:
                    result_hash = self._seed_recovery_artifact(integrity, store, store.get(job.execution_job_id), spec, lease, manifest, profile)
                    store.result_ready(job.execution_job_id, result_hash)
                    if wanted == ExecutionStatus.CHECKING:
                        store.checking(job.execution_job_id)
                recovered, outcome = integrity.recover(store.get(job.execution_job_id), spec, lease, manifest, profile)
                if wanted in {ExecutionStatus.RESULT_READY, ExecutionStatus.CHECKING}:
                    self.assertEqual(outcome, RecoveryOutcome.RECONCILE_RESULT)
                    self.assertEqual(recovered.status, ExecutionStatus.TERMINAL)
                else:
                    self.assertEqual(outcome, RecoveryOutcome.CANCEL)
                    self.assertEqual(recovered.status, ExecutionStatus.CANCELLED)
                _, launch_again = store.begin_dispatch(job.execution_job_id)
                self.assertFalse(launch_again)
                self.assertEqual(store.get(job.execution_job_id).launch_count, 1)
                self.assertIn(store.get(job.execution_job_id).status, {ExecutionStatus.TERMINAL, ExecutionStatus.CANCELLED})
                store.close()

    def test_checker_failure_quarantines_instead_of_orphaning_result(self):
        spec, manifest, profile = self._spec(), self._manifest(), self._profile()
        lease = self._lease(spec)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ExecutionJobStore(root / "execution.sqlite3")
            integrity = ProductionExecutionIntegrity(store, root / "work", root / "artifacts")
            job = store.create_or_get(
                opportunity_id=spec.canonical_opportunity_id,
                worker_id=manifest.worker_id,
                worker_version_or_source_sha=profile.source_sha,
                work_lease_id=lease.lease_id,
                scope_hash=spec.scope_hash,
                job_spec_hash=canonical_hash(spec.model_dump(mode="json")),
            )
            integrity.freeze_acceptance(job, spec)
            job, _ = store.begin_dispatch(job.execution_job_id)
            artifact = {
                "schema": "ATM_EXECUTION_ARTIFACT_V1",
                "execution_job_id": job.execution_job_id,
                "worker_id": manifest.worker_id,
                "worker_source_sha": profile.source_sha,
                "work_lease_id": lease.lease_id,
                "scope_hash": spec.scope_hash,
                "job_spec_hash": canonical_hash(spec.model_dump(mode="json")),
                "command_receipts": [{"argv": ["x"], "returncode": 0}],
                "outgoing_spend_usd": 0,
            }
            raw = (canonical_json(artifact) + "\n").encode()
            integrity.artifact_path(job.execution_job_id).write_bytes(raw)
            store.result_ready(job.execution_job_id, hashlib.sha256(raw).hexdigest())
            recovered, outcome = integrity.recover(store.get(job.execution_job_id), spec, lease, manifest, profile)
            self.assertEqual(outcome, RecoveryOutcome.CANCEL)
            self.assertEqual(recovered.status, ExecutionStatus.QUARANTINED)
            store.close()

    def test_forged_checker_declaration_and_hash_mismatch_cannot_bypass_proof(self):
        spec, manifest, profile = self._spec(), self._manifest(), self._profile()
        lease = self._lease(spec)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ExecutionJobStore(root / "execution.sqlite3")
            integrity = ProductionExecutionIntegrity(store, root / "work", root / "artifacts")
            job = store.create_or_get(
                opportunity_id=spec.canonical_opportunity_id,
                worker_id=manifest.worker_id,
                worker_version_or_source_sha=profile.source_sha,
                work_lease_id=lease.lease_id,
                scope_hash=spec.scope_hash,
                job_spec_hash=canonical_hash(spec.model_dump(mode="json")),
            )
            integrity.freeze_acceptance(job, spec)
            artifact = {
                "schema": "ATM_EXECUTION_ARTIFACT_V1",
                "execution_job_id": job.execution_job_id,
                "worker_id": manifest.worker_id,
                "worker_source_sha": profile.source_sha,
                "work_lease_id": lease.lease_id,
                "scope_hash": spec.scope_hash,
                "job_spec_hash": canonical_hash(spec.model_dump(mode="json")),
                "command_receipts": [{"argv": ["x"], "returncode": 0}],
                "outgoing_spend_usd": 0,
                "checker_independent": True,
            }
            raw = (json.dumps(artifact, sort_keys=True, separators=(",", ":")) + "\n").encode()
            integrity.artifact_path(job.execution_job_id).write_bytes(raw)
            store.result_ready(job.execution_job_id, hashlib.sha256(raw).hexdigest())
            with self.assertRaisesRegex(ValueError, "self-certification"):
                integrity.independently_check(store.get(job.execution_job_id), spec, lease, manifest, profile)
            self.assertIsNone(integrity.checker_receipt(job.execution_job_id))
            integrity.artifact_path(job.execution_job_id).write_text("tampered", encoding="utf-8")
            recovered, outcome = integrity.recover(store.get(job.execution_job_id), spec, lease, manifest, profile)
            self.assertEqual(outcome, RecoveryOutcome.CANCEL)
            self.assertEqual(recovered.status, ExecutionStatus.CANCELLED)
            store.close()


if __name__ == "__main__":
    unittest.main()
