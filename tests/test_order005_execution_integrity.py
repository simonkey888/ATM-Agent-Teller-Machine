from __future__ import annotations

import hashlib
import inspect
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import atm_fabric
import atm_core.actuator as actuator_module
import atm_core.execution_integrity as execution_integrity_module
from atm_core.actuator import ActuatorProfile, CheckoutSubprocessActuator
from atm_core.execution_integrity import ProductionExecutionIntegrity, RecoveryOutcome, lease_is_executable
from atm_core.execution_jobs import ExecutionAck, ExecutionJobStore, ExecutionStatus, ProgressReceipt, utcnow_iso
from atm_core.models import Opportunity, Phase, RuntimeState
from atm_core.workers import WorkerJobSpec, WorkerManifest, WorkerRegistry, WorkLease, WorkLeaseStore, canonical_hash, canonical_json


ROOT = Path(__file__).resolve().parents[1]
SOURCE = "1" * 40
ENTRYPOINT_REL = "tools/atm-worker-entrypoint.mjs"
ENTRYPOINT_SCRIPT = r"""#!/usr/bin/env node
import { createHash } from 'node:crypto';
import { closeSync, openSync, readFileSync, writeFileSync } from 'node:fs';
const req = (name) => { const value = String(process.env[name] || '').trim(); if (!value) throw new Error(`${name}_REQUIRED`); return value; };
const sha = (value) => createHash('sha256').update(value).digest('hex');
const specPath = req('ATM_JOB_SPEC_PATH');
const resultPath = req('ATM_TASK_RESULT_PATH');
const specHash = req('ATM_JOB_SPEC_HASH');
const raw = readFileSync(specPath);
if (sha(raw) !== specHash) throw new Error('MATERIALIZED_JOB_SPEC_HASH_MISMATCH');
const spec = JSON.parse(raw.toString('utf8'));
if (spec.task_type !== 'deterministic_digest_v1' || typeof spec.repository_or_input !== 'string') throw new Error('UNSUPPORTED_TASK');
const input = Buffer.from(spec.repository_or_input, 'utf8');
const result = {
  schema: 'ATM_TASK_RESULT_V1',
  execution_job_id: req('ATM_EXECUTION_JOB_ID'),
  work_lease_id: req('ATM_WORK_LEASE_ID'),
  scope_hash: req('ATM_SCOPE_HASH'),
  job_spec_hash: specHash,
  task_type: spec.task_type,
  producer: { worker_source_sha: req('ATM_WORKER_SOURCE_SHA'), worker_entrypoint: 'tools/atm-worker-entrypoint.mjs' },
  task_output: { kind: 'SHA256_UTF8_INPUT_V1', sha256: sha(input), byte_length: input.length },
  outgoing_spend_usd: 0
};
const fd = openSync(resultPath, 'wx', 0o600);
try { writeFileSync(fd, JSON.stringify(result), 'utf8'); } finally { closeSync(fd); }
"""


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

    def _profile(self, source_sha: str = SOURCE) -> ActuatorProfile:
        return ActuatorProfile(
            worker_id="fixture-worker",
            source_sha=source_sha,
            task_entrypoint=ENTRYPOINT_REL,
            execute_commands=[[sys.executable, "-c", "print('supporting-worker-check')"]],
            checker_commands=[[sys.executable, "-c", "print('worker-self-check')"]],
        )

    def _spec(self, frozen_input: str = "order005-material-task") -> WorkerJobSpec:
        expected = hashlib.sha256(frozen_input.encode("utf-8")).hexdigest()
        return WorkerJobSpec(
            job_id="fixture-job-" + expected[:8],
            canonical_opportunity_id="fixture:fixture-job:" + expected[:8],
            external_source="fixture",
            external_url="https://example.invalid/fixture-job/" + expected[:8],
            task_type="deterministic_digest_v1",
            frozen_acceptance_criteria=[
                "worker consumes exact frozen input",
                "ATM checker recomputes output independently",
            ],
            repository_or_input=frozen_input,
            max_spend_usd=0,
            required_capabilities=["git", "github", "filesystem", "shell", "evidence"],
            expected_deliverable="content-addressed digest result",
            deterministic_checks=[
                f"TASK_RESULT_SHA256_EQ:{expected}",
                f"TASK_RESULT_BYTE_LENGTH_EQ:{len(frozen_input.encode('utf-8'))}",
            ],
        )

    def _lease(self, spec: WorkerJobSpec, *, expires_at: datetime | None = None, lease_id: str = "lease-order005") -> WorkLease:
        now = datetime.now(timezone.utc)
        return WorkLease(
            lease_id=lease_id,
            canonical_opportunity_id=spec.canonical_opportunity_id,
            worker_id="fixture-worker",
            scope_hash=spec.scope_hash,
            acquired_at=now - timedelta(seconds=5),
            expires_at=expires_at or now + timedelta(minutes=5),
            heartbeat_at=now,
            terminal_state=None,
        )

    @staticmethod
    def _make_pinned_worker_source(root: Path) -> tuple[Path, str]:
        source = root / "pinned-worker-source"
        (source / "tools").mkdir(parents=True)
        (source / ENTRYPOINT_REL).write_text(ENTRYPOINT_SCRIPT, encoding="utf-8")
        (source / "support.txt").write_text("supporting worker bytes\n", encoding="utf-8")
        commands = (
            ["git", "init"],
            ["git", "config", "user.email", "fixture@example.invalid"],
            ["git", "config", "user.name", "ORDER005 Fixture"],
            ["git", "add", ENTRYPOINT_REL, "support.txt"],
            ["git", "commit", "-m", "fixture pinned worker source"],
        )
        for command in commands:
            completed = subprocess.run(command, cwd=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            if completed.returncode != 0:
                raise RuntimeError(f"fixture git command failed: {command}")
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
        return source, sha

    @staticmethod
    def _copy_checkout(source: Path, *, remove_entrypoint: bool = False):
        def apply(self_obj, manifest, profile, checkout, env):
            del self_obj, manifest, profile, env
            shutil.copytree(source, checkout)
            if remove_entrypoint:
                (checkout / ENTRYPOINT_REL).unlink()
        return apply

    @staticmethod
    def _task_result(job, spec, lease, *, wrong: bool = False) -> dict[str, object]:
        digest = hashlib.sha256(spec.repository_or_input.encode("utf-8")).hexdigest()
        if wrong:
            digest = ("0" if digest[0] != "0" else "1") + digest[1:]
        return {
            "schema": "ATM_TASK_RESULT_V1",
            "execution_job_id": job.execution_job_id,
            "work_lease_id": lease.lease_id,
            "scope_hash": spec.scope_hash,
            "job_spec_hash": canonical_hash(spec.model_dump(mode="json")),
            "task_type": spec.task_type,
            "consumed_contract": {"fixture": True},
            "task_output": {
                "kind": "SHA256_UTF8_INPUT_V1",
                "sha256": digest,
                "byte_length": len(spec.repository_or_input.encode("utf-8")),
            },
            "outgoing_spend_usd": 0,
        }

    def _seed_progress(self, store: ExecutionJobStore, job, spec: WorkerJobSpec) -> None:
        before = "0" * 64
        for seq, after in ((1, "c" * 64), (2, "d" * 64)):
            store.add_progress(ProgressReceipt(
                execution_job_id=job.execution_job_id,
                checkpoint_seq=seq,
                objective_hash=spec.scope_hash,
                artifact_before_hash=before,
                artifact_after_hash=after,
                tests_run=[f"material-{seq}"],
                new_evidence_refs=[f"material:{seq}"],
                uncertainty_or_acceptance_delta="measurable",
                created_at=utcnow_iso(),
            ))
            before = after

    def _seed_result(self, integrity, store, job, spec, lease, manifest, profile, *, wrong=False, self_cert=False, with_task=True):
        integrity.freeze_acceptance(job, spec)
        if store.get(job.execution_job_id).checkpoint_seq == 0:
            self._seed_progress(store, job, spec)
        artifact = {
            "schema": "ATM_EXECUTION_ARTIFACT_V1",
            "execution_job_id": job.execution_job_id,
            "worker_id": manifest.worker_id,
            "worker_source_sha": profile.source_sha,
            "work_lease_id": lease.lease_id,
            "scope_hash": spec.scope_hash,
            "job_spec_hash": canonical_hash(spec.model_dump(mode="json")),
            "command_receipts": [{"argv": ["frozen-task-consumer"], "returncode": 0}],
            "worker_self_check_receipts": [{"argv": ["canned-self-test"], "returncode": 0}],
            "outgoing_spend_usd": 0,
        }
        if with_task:
            task_result = self._task_result(job, spec, lease, wrong=wrong)
            artifact["task_result"] = task_result
            artifact["task_result_hash"] = canonical_hash(task_result)
        if self_cert:
            artifact["checker_independent"] = True
        raw = (canonical_json(artifact) + "\n").encode("utf-8")
        integrity.artifact_path(job.execution_job_id).write_bytes(raw)
        store.result_ready(job.execution_job_id, hashlib.sha256(raw).hexdigest())
        return hashlib.sha256(raw).hexdigest()

    def _fake_dispatch(self, self_actuator, spec, lease, manifest, profile):
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
        integrity = ProductionExecutionIntegrity(store, self_actuator.workspace_root, self_actuator.artifact_root)
        self._seed_result(integrity, store, job, spec, lease, manifest, profile)
        return store.checking(job.execution_job_id)

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
                        self.assertEqual(job.status, ExecutionStatus.TERMINAL)
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

    def test_real_actuator_uses_tracked_pinned_worker_entrypoint_for_two_frozen_specs(self):
        manifest = self._manifest()
        outputs = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, source_sha = self._make_pinned_worker_source(root)
            profile = self._profile(source_sha)
            for index, frozen_input in enumerate(("alpha-frozen-job", "beta-frozen-job")):
                spec = self._spec(frozen_input)
                lease = self._lease(spec, lease_id=f"lease-{index}")
                store = ExecutionJobStore(root / f"execution-{index}.sqlite3")
                integrity = ProductionExecutionIntegrity(store, root / "work", root / "artifacts")
                with patch.object(CheckoutSubprocessActuator, "_checkout", autospec=True, side_effect=self._copy_checkout(source)):
                    job, outcome = integrity.execute(spec, lease, manifest, profile)
                self.assertEqual(job.status, ExecutionStatus.TERMINAL)
                self.assertEqual(outcome, RecoveryOutcome.RECONCILE_RESULT)
                artifact = json.loads(integrity.artifact_path(job.execution_job_id).read_text(encoding="utf-8"))
                provenance = artifact["task_entrypoint_provenance"]
                task_result = artifact["task_result"]
                self.assertEqual(provenance["path"], ENTRYPOINT_REL)
                self.assertEqual(provenance["worker_source_sha"], source_sha)
                self.assertRegex(provenance["git_blob_oid"], r"^[0-9a-f]{40}$")
                self.assertRegex(provenance["sha256"], r"^[0-9a-f]{64}$")
                self.assertEqual(task_result["producer"]["worker_source_sha"], source_sha)
                self.assertEqual(task_result["producer"]["worker_entrypoint"], ENTRYPOINT_REL)
                self.assertEqual(task_result["job_spec_hash"], job.job_spec_hash)
                self.assertEqual(task_result["scope_hash"], spec.scope_hash)
                self.assertEqual(task_result["task_output"]["sha256"], hashlib.sha256(frozen_input.encode()).hexdigest())
                outputs.append((job.job_spec_hash, artifact["task_result_hash"], task_result["task_output"]["sha256"]))
                store.close()
        self.assertNotEqual(outputs[0], outputs[1])

    def test_broken_worker_entrypoint_fails_even_when_supporting_and_self_tests_are_green(self):
        spec, manifest = self._spec(), self._manifest()
        lease = self._lease(spec)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, source_sha = self._make_pinned_worker_source(root)
            profile = self._profile(source_sha)
            for command in (*profile.execute_commands, *profile.checker_commands):
                completed = subprocess.run(command, cwd=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
                self.assertEqual(completed.returncode, 0)
            store = ExecutionJobStore(root / "execution.sqlite3")
            integrity = ProductionExecutionIntegrity(store, root / "work", root / "artifacts")
            with patch.object(
                CheckoutSubprocessActuator,
                "_checkout",
                autospec=True,
                side_effect=self._copy_checkout(source, remove_entrypoint=True),
            ):
                job, outcome = integrity.execute(spec, lease, manifest, profile)
            self.assertEqual(job.status, ExecutionStatus.DISPATCH_FAILED)
            self.assertEqual(job.terminal_reason, "ValueError")
            self.assertEqual(outcome, RecoveryOutcome.CANCEL)
            self.assertIsNone(integrity.checker_receipt(job.execution_job_id))
            self.assertFalse((root / "artifacts" / "worker-io" / job.execution_job_id / "task-result.json").exists())
            store.close()

    def test_atm_owned_task_helper_is_absent_and_not_execution_path(self):
        self.assertFalse((ROOT / "src" / "atm_core" / "task_worker.py").exists())
        dispatch_source = inspect.getsource(actuator_module.CheckoutSubprocessActuator.dispatch)
        self.assertNotIn("task_worker.py", dispatch_source)
        self.assertNotIn("sys.executable", dispatch_source)
        self.assertIn('task_command = ["node", str(entrypoint_path)]', dispatch_source)

    def test_actual_production_phase_reaches_check_with_internal_task_artifact_and_never_submit(self):
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
                self.assertTrue(str(state.active_opportunity.deliverable_url).startswith("atm-artifact://sha256/"))
                self.assertEqual(dispatch.call_count, 1)
                passive.assert_not_called()
                execution_id = state.active_opportunity.execution_job_id
                store = ExecutionJobStore(root / "execution.sqlite3")
                integrity = ProductionExecutionIntegrity(store, root / "work", root / "artifacts")
                job = store.get(execution_id)
                checker = integrity.checker_receipt(execution_id)
                self.assertEqual(job.launch_count, 1)
                self.assertEqual(job.status, ExecutionStatus.TERMINAL)
                self.assertIsNotNone(checker)
                self.assertEqual(checker.verdict, "PASS")
                self.assertEqual(checker.receipt_hash, job.checker_hash)
                store.close()
                state.phase = Phase.WORK
                atm_fabric.phase_work({}, state)
                self.assertEqual(dispatch.call_count, 1)
                self.assertEqual(state.active_opportunity.execution_recovery, RecoveryOutcome.RECONCILE_RESULT.value)
                self.assertEqual(state.phase, Phase.CHECK)
                atm_fabric.phase_check({}, state)
                self.assertEqual(state.phase, Phase.CHECK)
                self.assertEqual(state.last_result["status"], "CHECKER_PASS_DELIVERABLE_UNMATERIALIZED")

    def test_structurally_valid_but_materially_wrong_result_is_checker_fail_and_submit_unreachable(self):
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
            job, launched = store.begin_dispatch(job.execution_job_id)
            self.assertTrue(launched)
            self._seed_result(integrity, store, job, spec, lease, manifest, profile, wrong=True)
            store.checking(job.execution_job_id)
            receipt = integrity.independently_check(store.get(job.execution_job_id), spec, lease, manifest, profile)
            self.assertEqual(receipt.verdict, "FAIL")
            self.assertTrue(any(not item["passed"] for item in receipt.predicate_results))
            recovered, outcome = integrity.recover(store.get(job.execution_job_id), spec, lease, manifest, profile)
            self.assertEqual(outcome, RecoveryOutcome.CANCEL)
            self.assertEqual(recovered.status, ExecutionStatus.QUARANTINED)
            self.assertEqual(recovered.launch_count, 1)
            store.close()

    def test_materially_correct_result_passes_in_distinct_checker_workspace(self):
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
            job, _ = store.begin_dispatch(job.execution_job_id)
            self._seed_result(integrity, store, job, spec, lease, manifest, profile)
            receipt = integrity.independently_check(store.get(job.execution_job_id), spec, lease, manifest, profile)
            self.assertEqual(receipt.verdict, "PASS")
            self.assertTrue(all(item["passed"] for item in receipt.predicate_results))
            checker_dir = integrity.checker_workspace(job.execution_job_id).resolve()
            worker_dir = (root / "work" / job.execution_job_id).resolve()
            self.assertNotEqual(checker_dir, worker_dir)
            self.assertNotIn(checker_dir, worker_dir.parents)
            self.assertNotIn(worker_dir, checker_dir.parents)
            store.close()

    def test_midflight_recovery_states_never_duplicate_launch_and_result_recovery_uses_material_checker(self):
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
                    self._seed_result(integrity, store, store.get(job.execution_job_id), spec, lease, manifest, profile)
                    if wanted == ExecutionStatus.CHECKING:
                        store.checking(job.execution_job_id)
                recovered, outcome = integrity.recover(store.get(job.execution_job_id), spec, lease, manifest, profile)
                if wanted in {ExecutionStatus.RESULT_READY, ExecutionStatus.CHECKING}:
                    self.assertEqual(outcome, RecoveryOutcome.RECONCILE_RESULT)
                    self.assertEqual(recovered.status, ExecutionStatus.TERMINAL)
                    self.assertEqual(integrity.checker_receipt(job.execution_job_id).verdict, "PASS")
                else:
                    self.assertEqual(outcome, RecoveryOutcome.CANCEL)
                    self.assertEqual(recovered.status, ExecutionStatus.CANCELLED)
                _, launch_again = store.begin_dispatch(job.execution_job_id)
                self.assertFalse(launch_again)
                self.assertEqual(store.get(job.execution_job_id).launch_count, 1)
                store.close()

    def test_worker_self_check_pass_alone_and_forged_checker_cannot_bypass(self):
        spec, manifest, profile = self._spec(), self._manifest(), self._profile()
        lease = self._lease(spec)
        for self_cert in (False, True):
            with self.subTest(self_cert=self_cert), tempfile.TemporaryDirectory() as directory:
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
                job, _ = store.begin_dispatch(job.execution_job_id)
                self._seed_result(
                    integrity, store, job, spec, lease, manifest, profile,
                    with_task=False, self_cert=self_cert,
                )
                if self_cert:
                    with self.assertRaisesRegex(ValueError, "self-certification"):
                        integrity.independently_check(store.get(job.execution_job_id), spec, lease, manifest, profile)
                else:
                    with self.assertRaisesRegex(ValueError, "task-specific result"):
                        integrity.independently_check(store.get(job.execution_job_id), spec, lease, manifest, profile)
                self.assertIsNone(integrity.checker_receipt(job.execution_job_id))
                recovered, outcome = integrity.recover(store.get(job.execution_job_id), spec, lease, manifest, profile)
                self.assertEqual(outcome, RecoveryOutcome.CANCEL)
                self.assertEqual(recovered.status, ExecutionStatus.QUARANTINED)
                store.close()


if __name__ == "__main__":
    unittest.main()
