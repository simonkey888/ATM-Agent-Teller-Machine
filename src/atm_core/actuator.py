from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .execution_jobs import ExecutionAck, ExecutionJob, ExecutionJobStore, ExecutionStatus, ProgressReceipt, utcnow_iso
from .workers import WorkerJobSpec, WorkerManifest, WorkLease, canonical_hash, canonical_json


class ActuatorProfile(BaseModel):
    """Pinned, supervisor-owned process entrypoint for a registered worker."""

    model_config = ConfigDict(extra="forbid")
    worker_id: str
    source_sha: str
    task_entrypoint: str
    prepare_commands: list[list[str]] = Field(default_factory=list)
    execute_commands: list[list[str]]
    checker_commands: list[list[str]]

    @field_validator("source_sha")
    @classmethod
    def exact_source(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{40}", value):
            raise ValueError("actuator source_sha must be exact lowercase git SHA")
        return value

    @field_validator("task_entrypoint")
    @classmethod
    def worker_owned_entrypoint(cls, value: str) -> str:
        value = str(value).replace("\\", "/").strip()
        path = PurePosixPath(value)
        if not value or path.is_absolute() or ".." in path.parts or ".git" in path.parts:
            raise ValueError("task entrypoint must be a safe worker-relative path")
        if len(path.parts) < 2 or path.suffix not in {".js", ".mjs"}:
            raise ValueError("task entrypoint must be a versioned worker JavaScript module")
        return path.as_posix()

    @field_validator("prepare_commands", "execute_commands", "checker_commands")
    @classmethod
    def bounded_commands(cls, commands: list[list[str]]) -> list[list[str]]:
        if len(commands) > 8:
            raise ValueError("too many actuator commands")
        for command in commands:
            if not command or len(command) > 20:
                raise ValueError("invalid actuator command width")
            if any(not str(token).strip() or "\n" in str(token) or "\r" in str(token) for token in command):
                raise ValueError("invalid actuator command token")
        return [[str(token) for token in command] for command in commands]


def load_actuator_profile(path: Path, worker_id: str) -> ActuatorProfile:
    profile = ActuatorProfile.model_validate_json(Path(path, f"{worker_id}.json").read_text(encoding="utf-8"))
    if profile.worker_id != worker_id:
        raise ValueError("actuator profile worker identity mismatch")
    return profile


@dataclass(frozen=True)
class CommandReceipt:
    argv: list[str]
    returncode: int
    stdout_sha256: str
    stderr_sha256: str

    @property
    def hash(self) -> str:
        return canonical_hash(self.__dict__)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def safe_worker_env(
    home: Path,
    job: ExecutionJob,
    *,
    job_spec_path: Path | None = None,
    task_result_path: Path | None = None,
) -> dict[str, str]:
    """No ambient ATM/GitHub/OCI/payment credentials cross the worker boundary."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "USERPROFILE": str(home),
        "CI": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "ATM_EXECUTION_JOB_ID": job.execution_job_id,
        "ATM_WORK_LEASE_ID": job.work_lease_id,
        "ATM_SCOPE_HASH": job.scope_hash,
        "ATM_MAX_SPEND_USD": "0",
        "ATM_JOB_SPEC_HASH": job.job_spec_hash,
    }
    if job_spec_path is not None:
        env["ATM_JOB_SPEC_PATH"] = str(job_spec_path)
    if task_result_path is not None:
        env["ATM_TASK_RESULT_PATH"] = str(task_result_path)
    for key in ("SystemRoot", "WINDIR", "COMSPEC", "PATHEXT"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def _run(argv: list[str], cwd: Path, env: dict[str, str], timeout: int) -> CommandReceipt:
    completed = subprocess.run(
        argv,
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=timeout,
        shell=False,
    )
    return CommandReceipt(
        argv=list(argv),
        returncode=int(completed.returncode),
        stdout_sha256=_sha256_bytes(completed.stdout),
        stderr_sha256=_sha256_bytes(completed.stderr),
    )


def _run_first_with_ack(
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout: int,
    store: ExecutionJobStore,
    job: ExecutionJob,
    profile: ActuatorProfile,
) -> CommandReceipt:
    process = subprocess.Popen(
        argv,
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    ack = ExecutionAck(
        execution_job_id=job.execution_job_id,
        work_lease_id=job.work_lease_id,
        scope_hash=job.scope_hash,
        worker_id=job.worker_id,
        source_sha=profile.source_sha,
        acknowledged_at=utcnow_iso(),
    )
    store.acknowledge(ack)
    store.running(job.execution_job_id)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise TimeoutError(f"worker command timeout: {argv[0]}")
    return CommandReceipt(
        argv=list(argv),
        returncode=int(process.returncode),
        stdout_sha256=_sha256_bytes(stdout),
        stderr_sha256=_sha256_bytes(stderr),
    )


class CheckoutSubprocessActuator:
    """Zero-secret actuator: ATM runner executes a pinned worker against one frozen job."""

    def __init__(self, store: ExecutionJobStore, workspace_root: Path, artifact_root: Path):
        self.store = store
        self.workspace_root = Path(workspace_root)
        self.artifact_root = Path(artifact_root)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.artifact_root.mkdir(parents=True, exist_ok=True)

    def _worker_io_paths(self, execution_job_id: str) -> tuple[Path, Path]:
        io_root = self.artifact_root / "worker-io" / execution_job_id
        io_root.mkdir(parents=True, exist_ok=True)
        return io_root / "job-spec.json", io_root / "task-result.json"

    def _materialize_job_spec(self, spec: WorkerJobSpec, job: ExecutionJob) -> tuple[Path, Path]:
        job_spec_path, task_result_path = self._worker_io_paths(job.execution_job_id)
        raw = canonical_json(spec.model_dump(mode="json")).encode("utf-8")
        if _sha256_bytes(raw) != job.job_spec_hash:
            raise ValueError("materialized WorkerJobSpec hash mismatch")
        if job_spec_path.exists():
            if job_spec_path.read_bytes() != raw:
                raise ValueError("immutable materialized WorkerJobSpec mutation detected")
        else:
            job_spec_path.write_bytes(raw)
            try:
                job_spec_path.chmod(0o444)
            except OSError:
                pass
        if task_result_path.exists():
            raise ValueError("preexisting task result before first launch")
        return job_spec_path, task_result_path

    @staticmethod
    def _load_bound_task_result(
        task_result_path: Path,
        job: ExecutionJob,
        spec: WorkerJobSpec,
        lease: WorkLease,
    ) -> tuple[dict[str, object], str]:
        if not task_result_path.exists():
            raise ValueError("task-specific result missing")
        value = json.loads(task_result_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("task-specific result must be object")
        expected = {
            "schema": "ATM_TASK_RESULT_V1",
            "execution_job_id": job.execution_job_id,
            "work_lease_id": lease.lease_id,
            "scope_hash": spec.scope_hash,
            "job_spec_hash": job.job_spec_hash,
            "task_type": spec.task_type,
            "outgoing_spend_usd": 0,
        }
        for key, expected_value in expected.items():
            if value.get(key) != expected_value:
                raise ValueError(f"task result binding mismatch: {key}")
        output = value.get("task_output")
        if not isinstance(output, dict) or output.get("kind") != "SHA256_UTF8_INPUT_V1":
            raise ValueError("task result output missing or unsupported")
        digest = str(output.get("sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("task result digest invalid")
        return value, canonical_hash(value)

    @staticmethod
    def _verify_worker_entrypoint(
        checkout: Path,
        profile: ActuatorProfile,
        env: dict[str, str],
    ) -> tuple[Path, str, str]:
        root = checkout.resolve()
        relative = PurePosixPath(profile.task_entrypoint)
        entrypoint = (checkout / Path(*relative.parts)).resolve()
        if entrypoint == root or root not in entrypoint.parents:
            raise ValueError("worker task entrypoint escapes pinned checkout")
        if not entrypoint.is_file() or entrypoint.is_symlink():
            raise ValueError("worker task entrypoint missing or unsafe")

        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", profile.task_entrypoint],
            cwd=str(checkout),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            shell=False,
        )
        if tracked.returncode != 0:
            raise ValueError("worker task entrypoint is not tracked by pinned source")

        committed = subprocess.run(
            ["git", "rev-parse", f"{profile.source_sha}:{profile.task_entrypoint}"],
            cwd=str(checkout),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            shell=False,
        )
        actual = subprocess.run(
            ["git", "hash-object", profile.task_entrypoint],
            cwd=str(checkout),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            shell=False,
        )
        if committed.returncode != 0 or actual.returncode != 0:
            raise ValueError("worker task entrypoint provenance unavailable")
        committed_blob = committed.stdout.decode("utf-8").strip()
        actual_blob = actual.stdout.decode("utf-8").strip()
        if not re.fullmatch(r"[0-9a-f]{40}", committed_blob) or committed_blob != actual_blob:
            raise ValueError("worker task entrypoint differs from pinned source bytes")
        return entrypoint, committed_blob, _sha256_bytes(entrypoint.read_bytes())

    def dispatch(
        self,
        spec: WorkerJobSpec,
        lease: WorkLease,
        manifest: WorkerManifest,
        profile: ActuatorProfile,
    ) -> ExecutionJob:
        self._validate_input(spec, lease, manifest, profile)
        job = self.store.create_or_get(
            opportunity_id=spec.canonical_opportunity_id,
            worker_id=manifest.worker_id,
            worker_version_or_source_sha=profile.source_sha,
            work_lease_id=lease.lease_id,
            scope_hash=spec.scope_hash,
            job_spec_hash=canonical_hash(spec.model_dump(mode="json")),
        )
        current, should_launch = self.store.begin_dispatch(job.execution_job_id)
        if not should_launch:
            return current
        checkout = self.workspace_root / job.execution_job_id
        home = self.workspace_root / (job.execution_job_id + "-home")
        home.mkdir(parents=True, exist_ok=True)
        try:
            job_spec_path, task_result_path = self._materialize_job_spec(spec, current)
            env = safe_worker_env(
                home,
                current,
                job_spec_path=job_spec_path,
                task_result_path=task_result_path,
            )
            env["ATM_WORKER_SOURCE_SHA"] = profile.source_sha
            self._checkout(manifest, profile, checkout, env)
            source_hash = self._git_head(checkout, env)
            if source_hash != profile.source_sha:
                return self.store.fail(job.execution_job_id, ExecutionStatus.QUARANTINED, "STALE_WORKER_SOURCE")
            entrypoint_path, entrypoint_blob, entrypoint_sha256 = self._verify_worker_entrypoint(checkout, profile, env)
            self._progress(
                job.execution_job_id,
                objective_hash=spec.scope_hash,
                before="0" * 64,
                after=source_hash,
                tests=[],
                evidence=[
                    f"worker-source:{source_hash}",
                    f"job-spec:{job.job_spec_hash}",
                    f"worker-entrypoint:{profile.task_entrypoint}:{entrypoint_blob}:{entrypoint_sha256}",
                ],
                delta="pinned worker source, worker-owned task entrypoint, and frozen job contract materialized",
            )
            last_hash = source_hash
            for command in profile.prepare_commands:
                receipt = _run(command, checkout, env, manifest.timeout_seconds)
                if receipt.returncode != 0:
                    return self.store.fail(job.execution_job_id, ExecutionStatus.DISPATCH_FAILED, "PREPARE_COMMAND_FAILED")
                self._progress(
                    job.execution_job_id,
                    objective_hash=spec.scope_hash,
                    before=last_hash,
                    after=receipt.hash,
                    tests=["prepare:" + " ".join(command)],
                    evidence=["command:" + receipt.hash],
                    delta="worker preparation evidence",
                )
                last_hash = receipt.hash

            task_command = ["node", str(entrypoint_path)]
            task_receipt = _run_first_with_ack(
                task_command,
                checkout,
                env,
                manifest.timeout_seconds,
                self.store,
                current,
                profile,
            )
            if task_receipt.returncode != 0:
                return self.store.fail(job.execution_job_id, ExecutionStatus.DISPATCH_FAILED, "FROZEN_TASK_CONSUMER_FAILED")
            task_result, task_result_hash = self._load_bound_task_result(task_result_path, current, spec, lease)
            producer = task_result.get("producer")
            if not isinstance(producer, dict):
                return self.store.fail(job.execution_job_id, ExecutionStatus.DISPATCH_FAILED, "WORKER_PRODUCER_BINDING_MISSING")
            if producer.get("worker_source_sha") != profile.source_sha or producer.get("worker_entrypoint") != profile.task_entrypoint:
                return self.store.fail(job.execution_job_id, ExecutionStatus.DISPATCH_FAILED, "WORKER_PRODUCER_BINDING_MISMATCH")
            self._progress(
                job.execution_job_id,
                objective_hash=spec.scope_hash,
                before=last_hash,
                after=task_result_hash,
                tests=["worker:frozen-job-consumer"],
                evidence=[
                    "worker-command:" + task_receipt.hash,
                    "task-result:" + task_result_hash,
                    f"worker-entrypoint-blob:{entrypoint_blob}",
                ],
                delta="pinned worker-owned entrypoint consumed exact frozen job and materialized task-specific result",
            )
            last_hash = task_result_hash

            execution_receipts: list[CommandReceipt] = [task_receipt]
            for command in profile.execute_commands:
                receipt = _run(command, checkout, env, manifest.timeout_seconds)
                execution_receipts.append(receipt)
                if receipt.returncode != 0:
                    return self.store.fail(job.execution_job_id, ExecutionStatus.DISPATCH_FAILED, "WORKER_COMMAND_FAILED")
                self._progress(
                    job.execution_job_id,
                    objective_hash=spec.scope_hash,
                    before=last_hash,
                    after=receipt.hash,
                    tests=["worker-supporting-check:" + " ".join(command)],
                    evidence=["worker-command:" + receipt.hash],
                    delta="worker supporting repository evidence",
                )
                last_hash = receipt.hash

            self_check_receipts: list[CommandReceipt] = []
            for command in profile.checker_commands:
                receipt = _run(command, checkout, env, manifest.timeout_seconds)
                self_check_receipts.append(receipt)
                if receipt.returncode != 0:
                    return self.store.fail(job.execution_job_id, ExecutionStatus.QUARANTINED, "WORKER_SELF_CHECK_FAILED")
                self._progress(
                    job.execution_job_id,
                    objective_hash=spec.scope_hash,
                    before=last_hash,
                    after=receipt.hash,
                    tests=["worker-self-check:" + " ".join(command)],
                    evidence=["worker-self-check:" + receipt.hash],
                    delta="worker self-check recorded as non-authoritative supporting evidence",
                )
                last_hash = receipt.hash

            artifact = {
                "schema": "ATM_EXECUTION_ARTIFACT_V1",
                "execution_job_id": job.execution_job_id,
                "worker_id": manifest.worker_id,
                "worker_source_sha": profile.source_sha,
                "work_lease_id": lease.lease_id,
                "scope_hash": spec.scope_hash,
                "job_spec_hash": job.job_spec_hash,
                "task_entrypoint_provenance": {
                    "path": profile.task_entrypoint,
                    "git_blob_oid": entrypoint_blob,
                    "sha256": entrypoint_sha256,
                    "worker_source_sha": profile.source_sha,
                },
                "task_result": task_result,
                "task_result_hash": task_result_hash,
                "command_receipts": [receipt.__dict__ for receipt in execution_receipts],
                "worker_self_check_receipts": [receipt.__dict__ for receipt in self_check_receipts],
                "outgoing_spend_usd": 0,
            }
            artifact_bytes = (canonical_json(artifact) + "\n").encode("utf-8")
            artifact_hash = _sha256_bytes(artifact_bytes)
            artifact_path = self.artifact_root / f"{job.execution_job_id}.json"
            artifact_path.write_bytes(artifact_bytes)
            self.store.result_ready(job.execution_job_id, artifact_hash)
            return self.store.checking(job.execution_job_id)
        except Exception as exc:
            return self.store.fail(job.execution_job_id, ExecutionStatus.DISPATCH_FAILED, type(exc).__name__)

    def _checkout(self, manifest: WorkerManifest, profile: ActuatorProfile, checkout: Path, env: dict[str, str]) -> None:
        if checkout.exists():
            shutil.rmtree(checkout)
        clone = _run(["git", "clone", "--no-checkout", "--filter=blob:none", manifest.repo_url, str(checkout)], self.workspace_root, env, 180)
        if clone.returncode != 0:
            raise RuntimeError("public worker clone failed")
        fetch = _run(["git", "fetch", "--depth=1", "origin", profile.source_sha], checkout, env, 180)
        if fetch.returncode != 0:
            raise RuntimeError("pinned worker source fetch failed")
        checkout_receipt = _run(["git", "checkout", "--detach", profile.source_sha], checkout, env, 60)
        if checkout_receipt.returncode != 0:
            raise RuntimeError("pinned worker checkout failed")

    @staticmethod
    def _git_head(checkout: Path, env: dict[str, str]) -> str:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(checkout),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            shell=False,
        )
        if completed.returncode != 0:
            raise RuntimeError("worker git identity unavailable")
        return completed.stdout.decode("utf-8").strip()

    def _progress(
        self,
        execution_job_id: str,
        *,
        objective_hash: str,
        before: str,
        after: str,
        tests: Iterable[str],
        evidence: Iterable[str],
        delta: str,
    ) -> None:
        job = self.store.get(execution_job_id)
        self.store.add_progress(
            ProgressReceipt(
                execution_job_id=execution_job_id,
                checkpoint_seq=job.checkpoint_seq + 1,
                objective_hash=objective_hash,
                artifact_before_hash=before,
                artifact_after_hash=after,
                tests_run=list(tests),
                new_evidence_refs=list(evidence),
                blocker_class="NONE",
                uncertainty_or_acceptance_delta=delta,
                recommendation="CONTINUE",
                created_at=utcnow_iso(),
            )
        )

    @staticmethod
    def _validate_input(
        spec: WorkerJobSpec, lease: WorkLease, manifest: WorkerManifest, profile: ActuatorProfile
    ) -> None:
        if profile.worker_id != manifest.worker_id:
            raise ValueError("actuator profile worker mismatch")
        if manifest.cost_ceiling_usd != 0 or spec.max_spend_usd != 0:
            raise ValueError("zero-spend actuator only")
        for attr in ("financial_authority", "claim_authority", "submission_authority", "model_authority"):
            if bool(getattr(manifest, attr, False)):
                raise ValueError("worker authority forbidden")
        if lease.worker_id != manifest.worker_id:
            raise ValueError("wrong worker for WorkLease")
        if lease.scope_hash != spec.scope_hash or lease.canonical_opportunity_id != spec.canonical_opportunity_id:
            raise ValueError("WorkLease immutable binding mismatch")
        if lease.terminal_state is not None:
            raise ValueError("terminal WorkLease cannot execute")
