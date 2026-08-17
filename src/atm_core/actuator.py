from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .execution_jobs import ExecutionAck, ExecutionJob, ExecutionJobStore, ExecutionStatus, ProgressReceipt, utcnow_iso
from .workers import WorkerJobSpec, WorkerManifest, WorkLease, canonical_hash, canonical_json


class ActuatorProfile(BaseModel):
    """Pinned, supervisor-owned process entrypoint for a registered worker."""

    model_config = ConfigDict(extra="forbid")
    worker_id: str
    source_sha: str
    prepare_commands: list[list[str]] = Field(default_factory=list)
    execute_commands: list[list[str]]
    checker_commands: list[list[str]]

    @field_validator("source_sha")
    @classmethod
    def exact_source(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{40}", value):
            raise ValueError("actuator source_sha must be exact lowercase git SHA")
        return value

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


def safe_worker_env(home: Path, job: ExecutionJob) -> dict[str, str]:
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
    """Zero-secret actuator: ATM runner executes a pinned public worker checkout."""

    def __init__(self, store: ExecutionJobStore, workspace_root: Path, artifact_root: Path):
        self.store = store
        self.workspace_root = Path(workspace_root)
        self.artifact_root = Path(artifact_root)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.artifact_root.mkdir(parents=True, exist_ok=True)

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
        env = safe_worker_env(home, current)
        try:
            self._checkout(manifest, profile, checkout, env)
            source_hash = self._git_head(checkout, env)
            if source_hash != profile.source_sha:
                return self.store.fail(job.execution_job_id, ExecutionStatus.QUARANTINED, "STALE_WORKER_SOURCE")
            self._progress(
                job.execution_job_id,
                objective_hash=spec.scope_hash,
                before="0" * 64,
                after=source_hash,
                tests=[],
                evidence=[f"worker-source:{source_hash}"],
                delta="pinned worker source independently verified",
            )
            for command in profile.prepare_commands:
                receipt = _run(command, checkout, env, manifest.timeout_seconds)
                if receipt.returncode != 0:
                    return self.store.fail(job.execution_job_id, ExecutionStatus.DISPATCH_FAILED, "PREPARE_COMMAND_FAILED")
                self._progress(
                    job.execution_job_id,
                    objective_hash=spec.scope_hash,
                    before=source_hash,
                    after=receipt.hash,
                    tests=["prepare:" + " ".join(command)],
                    evidence=["command:" + receipt.hash],
                    delta="worker preparation evidence",
                )
            execution_receipts: list[CommandReceipt] = []
            for index, command in enumerate(profile.execute_commands):
                if index == 0:
                    receipt = _run_first_with_ack(command, checkout, env, manifest.timeout_seconds, self.store, current, profile)
                else:
                    receipt = _run(command, checkout, env, manifest.timeout_seconds)
                execution_receipts.append(receipt)
                if receipt.returncode != 0:
                    return self.store.fail(job.execution_job_id, ExecutionStatus.DISPATCH_FAILED, "WORKER_COMMAND_FAILED")
                before = execution_receipts[index - 1].hash if index else source_hash
                self._progress(
                    job.execution_job_id,
                    objective_hash=spec.scope_hash,
                    before=before,
                    after=receipt.hash,
                    tests=["worker:" + " ".join(command)],
                    evidence=["worker-command:" + receipt.hash],
                    delta="worker produced new deterministic execution evidence",
                )
            if not execution_receipts:
                return self.store.fail(job.execution_job_id, ExecutionStatus.DISPATCH_FAILED, "NO_WORKER_ENTRYPOINT")
            artifact = {
                "schema": "ATM_EXECUTION_ARTIFACT_V1",
                "execution_job_id": job.execution_job_id,
                "worker_id": manifest.worker_id,
                "worker_source_sha": profile.source_sha,
                "work_lease_id": lease.lease_id,
                "scope_hash": spec.scope_hash,
                "job_spec_hash": job.job_spec_hash,
                "command_receipts": [receipt.__dict__ for receipt in execution_receipts],
                "outgoing_spend_usd": 0,
            }
            artifact_bytes = (canonical_json(artifact) + "\n").encode("utf-8")
            artifact_hash = _sha256_bytes(artifact_bytes)
            artifact_path = self.artifact_root / f"{job.execution_job_id}.json"
            artifact_path.write_bytes(artifact_bytes)
            self.store.result_ready(job.execution_job_id, artifact_hash)
            self.store.checking(job.execution_job_id)
            checker_receipts: list[CommandReceipt] = []
            for command in profile.checker_commands:
                receipt = _run(command, checkout, env, manifest.timeout_seconds)
                checker_receipts.append(receipt)
                if receipt.returncode != 0:
                    return self.store.fail(job.execution_job_id, ExecutionStatus.QUARANTINED, "INDEPENDENT_CHECKER_FAILED")
            if not checker_receipts:
                return self.store.fail(job.execution_job_id, ExecutionStatus.QUARANTINED, "NO_INDEPENDENT_CHECKER")
            checker_hash = canonical_hash([receipt.__dict__ for receipt in checker_receipts])
            self._progress(
                job.execution_job_id,
                objective_hash=spec.scope_hash,
                before=artifact_hash,
                after=checker_hash,
                tests=["checker:" + " ".join(item.argv) for item in checker_receipts],
                evidence=["artifact:" + artifact_hash, "checker:" + checker_hash],
                delta="independent checker produced acceptance evidence",
            )
            return self.store.terminal(job.execution_job_id, checker_hash=checker_hash, reason="FIXTURE_CHECKER_PASS")
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
