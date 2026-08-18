from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .actuator import (
    ActuatorProfile,
    CheckoutSubprocessActuator,
    CommandReceipt,
    _sha256_bytes,
    safe_worker_env,
)
from .execution_jobs import ExecutionAck, ExecutionJob, ExecutionJobStore, ExecutionStatus, utcnow_iso
from .workers import WorkerJobSpec, WorkerManifest, WorkLease, canonical_hash, canonical_json


class LeaseExpiredDuringExecution(TimeoutError):
    """Raised when the authoritative ATM WorkLease ceases to be executable."""


def _lease_remaining_seconds(lease: WorkLease, cap_seconds: float) -> float:
    now = datetime.now(timezone.utc)
    if lease.expires_at.tzinfo is None:
        raise ValueError("lease expiry must be timezone-aware")
    remaining = (lease.expires_at.astimezone(timezone.utc) - now).total_seconds()
    if remaining <= 0:
        raise LeaseExpiredDuringExecution("ATM WorkLease expired during execution")
    return min(float(cap_seconds), remaining)


def _assert_lease_live(lease: WorkLease) -> None:
    _lease_remaining_seconds(lease, 1.0)


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        process.kill()
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()


def _run_bound(
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
    lease: WorkLease,
    cap_seconds: float,
) -> CommandReceipt:
    timeout = _lease_remaining_seconds(lease, cap_seconds)
    process = subprocess.Popen(
        argv,
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _terminate_process(process)
        raise LeaseExpiredDuringExecution(f"lease-bounded subprocess timeout: {argv[0]}") from exc
    _assert_lease_live(lease)
    return CommandReceipt(
        argv=list(argv),
        returncode=int(process.returncode),
        stdout_sha256=_sha256_bytes(stdout),
        stderr_sha256=_sha256_bytes(stderr),
    )


def _run_first_bound_with_ack(
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
    lease: WorkLease,
    cap_seconds: float,
    store: ExecutionJobStore,
    job: ExecutionJob,
    profile: ActuatorProfile,
) -> CommandReceipt:
    timeout = _lease_remaining_seconds(lease, cap_seconds)
    process = subprocess.Popen(
        argv,
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        start_new_session=True,
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
    except subprocess.TimeoutExpired as exc:
        _terminate_process(process)
        raise LeaseExpiredDuringExecution(f"lease-bounded worker timeout: {argv[0]}") from exc
    _assert_lease_live(lease)
    return CommandReceipt(
        argv=list(argv),
        returncode=int(process.returncode),
        stdout_sha256=_sha256_bytes(stdout),
        stderr_sha256=_sha256_bytes(stderr),
    )


class LeaseBoundCheckoutSubprocessActuator(CheckoutSubprocessActuator):
    """ORDER-006 production actuator whose every subprocess is bounded by WorkLease expiry."""

    @staticmethod
    def _verify_worker_entrypoint_bound(
        checkout: Path,
        profile: ActuatorProfile,
        env: dict[str, str],
        lease: WorkLease,
    ) -> tuple[Path, str, str]:
        root = checkout.resolve()
        relative = PurePosixPath(profile.task_entrypoint)
        entrypoint = (checkout / Path(*relative.parts)).resolve()
        if entrypoint == root or root not in entrypoint.parents:
            raise ValueError("worker task entrypoint escapes pinned checkout")
        if not entrypoint.is_file() or entrypoint.is_symlink():
            raise ValueError("worker task entrypoint missing or unsafe")

        tracked = _run_bound(
            ["git", "ls-files", "--error-unmatch", "--", profile.task_entrypoint],
            checkout,
            env,
            lease,
            30,
        )
        if tracked.returncode != 0:
            raise ValueError("worker task entrypoint is not tracked by pinned source")

        committed = subprocess.Popen(
            ["git", "rev-parse", f"{profile.source_sha}:{profile.task_entrypoint}"],
            cwd=str(checkout), env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, start_new_session=True,
        )
        try:
            committed_out, committed_err = committed.communicate(timeout=_lease_remaining_seconds(lease, 30))
        except subprocess.TimeoutExpired as exc:
            _terminate_process(committed)
            raise LeaseExpiredDuringExecution("lease-bounded git rev-parse timeout") from exc
        _assert_lease_live(lease)
        if committed.returncode != 0:
            raise ValueError("worker task entrypoint provenance unavailable")

        actual = subprocess.Popen(
            ["git", "hash-object", profile.task_entrypoint],
            cwd=str(checkout), env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, start_new_session=True,
        )
        try:
            actual_out, actual_err = actual.communicate(timeout=_lease_remaining_seconds(lease, 30))
        except subprocess.TimeoutExpired as exc:
            _terminate_process(actual)
            raise LeaseExpiredDuringExecution("lease-bounded git hash-object timeout") from exc
        _assert_lease_live(lease)
        if actual.returncode != 0:
            raise ValueError("worker task entrypoint provenance unavailable")

        committed_blob = committed_out.decode("utf-8").strip()
        actual_blob = actual_out.decode("utf-8").strip()
        if not re.fullmatch(r"[0-9a-f]{40}", committed_blob) or committed_blob != actual_blob:
            raise ValueError("worker task entrypoint differs from pinned source bytes")
        return entrypoint, committed_blob, _sha256_bytes(entrypoint.read_bytes())

    def _checkout_bound(
        self,
        manifest: WorkerManifest,
        profile: ActuatorProfile,
        checkout: Path,
        env: dict[str, str],
        lease: WorkLease,
    ) -> None:
        if checkout.exists():
            shutil.rmtree(checkout)
        clone = _run_bound(
            ["git", "clone", "--no-checkout", "--filter=blob:none", manifest.repo_url, str(checkout)],
            self.workspace_root,
            env,
            lease,
            180,
        )
        if clone.returncode != 0:
            raise RuntimeError("public worker clone failed")
        fetch = _run_bound(
            ["git", "fetch", "--depth=1", "origin", profile.source_sha],
            checkout,
            env,
            lease,
            180,
        )
        if fetch.returncode != 0:
            raise RuntimeError("pinned worker source fetch failed")
        checked = _run_bound(
            ["git", "checkout", "--detach", profile.source_sha],
            checkout,
            env,
            lease,
            60,
        )
        if checked.returncode != 0:
            raise RuntimeError("pinned worker checkout failed")

    @staticmethod
    def _git_head_bound(checkout: Path, env: dict[str, str], lease: WorkLease) -> str:
        timeout = _lease_remaining_seconds(lease, 30)
        process = subprocess.Popen(
            ["git", "rev-parse", "HEAD"], cwd=str(checkout), env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            shell=False, start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _terminate_process(process)
            raise LeaseExpiredDuringExecution("lease-bounded git identity timeout") from exc
        _assert_lease_live(lease)
        if process.returncode != 0:
            raise RuntimeError("worker git identity unavailable")
        return stdout.decode("utf-8").strip()

    def dispatch(
        self,
        spec: WorkerJobSpec,
        lease: WorkLease,
        manifest: WorkerManifest,
        profile: ActuatorProfile,
    ) -> ExecutionJob:
        self._validate_input(spec, lease, manifest, profile)
        _assert_lease_live(lease)
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
            env["ATM_WORK_LEASE_EXPIRES_AT"] = lease.expires_at.isoformat()
            self._checkout_bound(manifest, profile, checkout, env, lease)
            source_hash = self._git_head_bound(checkout, env, lease)
            if source_hash != profile.source_sha:
                return self.store.fail(job.execution_job_id, ExecutionStatus.QUARANTINED, "STALE_WORKER_SOURCE")
            entrypoint_path, entrypoint_blob, entrypoint_sha256 = self._verify_worker_entrypoint_bound(
                checkout, profile, env, lease
            )
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
                    f"work-lease-expires-at:{lease.expires_at.isoformat()}",
                ],
                delta="pinned worker source, worker-owned entrypoint, frozen job, and authoritative lease expiry materialized",
            )
            last_hash = source_hash

            for command in profile.prepare_commands:
                receipt = _run_bound(command, checkout, env, lease, manifest.timeout_seconds)
                if receipt.returncode != 0:
                    return self.store.fail(job.execution_job_id, ExecutionStatus.DISPATCH_FAILED, "PREPARE_COMMAND_FAILED")
                self._progress(
                    job.execution_job_id,
                    objective_hash=spec.scope_hash,
                    before=last_hash,
                    after=receipt.hash,
                    tests=["prepare:" + " ".join(command)],
                    evidence=["command:" + receipt.hash],
                    delta="lease-bounded worker preparation evidence",
                )
                last_hash = receipt.hash

            task_receipt = _run_first_bound_with_ack(
                ["node", str(entrypoint_path)],
                checkout,
                env,
                lease,
                manifest.timeout_seconds,
                self.store,
                current,
                profile,
            )
            if task_receipt.returncode != 0:
                return self.store.fail(job.execution_job_id, ExecutionStatus.DISPATCH_FAILED, "FROZEN_TASK_CONSUMER_FAILED")
            _assert_lease_live(lease)
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
                    f"work-lease-expires-at:{lease.expires_at.isoformat()}",
                ],
                delta="pinned worker consumed frozen job under exact ATM lease expiry",
            )
            last_hash = task_result_hash

            execution_receipts: list[CommandReceipt] = [task_receipt]
            for command in profile.execute_commands:
                receipt = _run_bound(command, checkout, env, lease, manifest.timeout_seconds)
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
                    delta="lease-bounded worker supporting repository evidence",
                )
                last_hash = receipt.hash

            self_check_receipts: list[CommandReceipt] = []
            for command in profile.checker_commands:
                receipt = _run_bound(command, checkout, env, lease, manifest.timeout_seconds)
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
                    delta="lease-bounded worker self-check supporting evidence",
                )
                last_hash = receipt.hash

            _assert_lease_live(lease)
            artifact = {
                "schema": "ATM_EXECUTION_ARTIFACT_V1",
                "execution_job_id": job.execution_job_id,
                "worker_id": manifest.worker_id,
                "worker_source_sha": profile.source_sha,
                "work_lease_id": lease.lease_id,
                "work_lease_expires_at": lease.expires_at.isoformat(),
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
            _assert_lease_live(lease)
            self.store.result_ready(job.execution_job_id, artifact_hash)
            return self.store.checking(job.execution_job_id)
        except LeaseExpiredDuringExecution:
            return self.store.fail(
                job.execution_job_id,
                ExecutionStatus.EXPIRED,
                "LEASE_EXPIRED_DURING_EXECUTION",
            )
        except Exception as exc:
            return self.store.fail(job.execution_job_id, ExecutionStatus.DISPATCH_FAILED, type(exc).__name__)
