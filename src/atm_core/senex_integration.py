from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .actuator import safe_worker_env
from .capabilities import CapabilityResolver
from .execution_jobs import ExecutionAck, ExecutionJob, ExecutionJobStore, ExecutionStatus, ProgressReceipt, utcnow_iso
from .workers import WorkerJobSpec, WorkerManifest, WorkerResult, WorkLease, canonical_hash, canonical_json


SENEX_WORKER_ID = "senex-prophet"
SENEX_ACCEPTED_SOURCE_SHA = "b8dd6666f2113ab2689594d4afc4a6771e56b753"
SENEX_ACCEPTED_TREE_SHA = "66af604185d599c3abbe324d4207c10df1b9dbea"
SENEX_PROTOCOL = "atm-worker.v1"
SENEX_NATIVE_ENTRYPOINT = "senecio_polymarket/atm_worker/__main__.py"
SENEX_ATM_CAPABILITIES = {
    "deterministic_replay",
    "causal_cutoff_validation",
    "provenance_hash_validation",
    "statistical_evaluation",
    "robustness_regime_stress",
    "bounded_python_data_pipeline_repair",
}
SENEX_NATIVE_CAPABILITY_MAP = {
    "deterministic_replay": "deterministic_data_replay",
    "causal_cutoff_validation": "causal_cutoff_validation",
    "provenance_hash_validation": "provenance_hash_validation",
    "statistical_evaluation": "statistical_evaluation",
    "robustness_regime_stress": "robustness_regime_stress",
    "bounded_python_data_pipeline_repair": "bounded_python_pipeline_repair",
}
_NATIVE_REQUIRED = {
    "protocol_version",
    "job_id",
    "canonical_opportunity_id",
    "worker_id",
    "work_lease_id",
    "attempt",
    "scope_hash",
    "target_repository_or_dataset",
    "target_base_sha_or_snapshot",
    "target_snapshot_manifest",
    "allowed_paths",
    "required_capabilities",
    "structured_requirements",
    "frozen_acceptance_criteria",
    "expected_deliverable",
    "deterministic_checks",
    "data_provenance",
    "as_of",
    "cutoff",
    "max_spend_usd",
    "lease_state",
    "lease_expires_at",
    "job_deadline",
    "workspace_id",
}
_FORBIDDEN_TARGET_MARKERS = (
    "runtime017",
    "runtime_017",
    "/app/polymarket/results",
    "northflank",
    "supabase",
    "production-runtime",
)
_FORBIDDEN_REQUEST_KEYS = {
    "allow_network",
    "network_allowlist",
    "live_trading",
    "order_placement",
    "wallet",
    "payment",
    "production_write",
    "runtime017_write",
    "supabase_write",
    "northflank_write",
    "secret_access",
}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _git_blob_sha(value: bytes) -> str:
    return hashlib.sha1(f"blob {len(value)}\0".encode("utf-8") + value).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    ).encode("utf-8")


def senex_native_scope_hash(native_job: dict[str, Any]) -> str:
    material = {key: native_job[key] for key in sorted(_NATIVE_REQUIRED - {"scope_hash"}) if key in native_job}
    return _sha256_bytes(_canonical_bytes(material))


def _safe_rel(value: str) -> str:
    text = str(value).replace("\\", "/").strip()
    path = PurePosixPath(text)
    if not text or path.is_absolute() or ".." in path.parts or ".git" in path.parts:
        raise ValueError("unsafe SENEX bridge relative path")
    return path.as_posix()


def _is_allowed(path: str, allowed_paths: list[str]) -> bool:
    candidate = PurePosixPath(path)
    return any(candidate == PurePosixPath(item) or PurePosixPath(item) in candidate.parents for item in allowed_paths)


class SenexWorkerJobSpec(WorkerJobSpec):
    """Typed ATM WorkerJobSpec extension carrying deterministic SENEX bridge material."""

    senex_payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def senex_payload_required(self) -> "SenexWorkerJobSpec":
        if not self.senex_payload:
            raise ValueError("senex_payload required")
        return self


class SenexActuatorProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str
    source_sha: str
    task_entrypoint: str
    task_module: str
    task_cwd: str
    protocol: str
    supporting_commands: list[list[str]] = Field(default_factory=list)

    @field_validator("source_sha")
    @classmethod
    def exact_sha(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{40}", value):
            raise ValueError("SENEX source pin must be exact lowercase SHA")
        return value

    @field_validator("task_entrypoint", "task_cwd")
    @classmethod
    def safe_worker_path(cls, value: str) -> str:
        return _safe_rel(value)

    @field_validator("supporting_commands")
    @classmethod
    def bounded_commands(cls, commands: list[list[str]]) -> list[list[str]]:
        if len(commands) > 6:
            raise ValueError("too many SENEX supporting commands")
        for command in commands:
            if not command or len(command) > 20:
                raise ValueError("invalid SENEX supporting command")
            if any(not str(token).strip() or "\n" in str(token) or "\r" in str(token) for token in command):
                raise ValueError("invalid SENEX supporting command token")
        return [[str(token) for token in command] for command in commands]

    @model_validator(mode="after")
    def exact_native_contract(self) -> "SenexActuatorProfile":
        if self.worker_id != SENEX_WORKER_ID:
            raise ValueError("SENEX actuator worker_id mismatch")
        if self.source_sha != SENEX_ACCEPTED_SOURCE_SHA:
            raise ValueError("SENEX actuator source is not independently accepted pin")
        if self.task_entrypoint != SENEX_NATIVE_ENTRYPOINT:
            raise ValueError("SENEX native entrypoint mismatch")
        if self.task_module != "atm_worker" or self.task_cwd != "senecio_polymarket":
            raise ValueError("SENEX native module invocation mismatch")
        if self.protocol != SENEX_PROTOCOL:
            raise ValueError("SENEX protocol mismatch")
        return self


def load_senex_profile(path: Path) -> SenexActuatorProfile:
    return SenexActuatorProfile.model_validate_json(Path(path, "senex-prophet.json").read_text(encoding="utf-8"))


@dataclass(frozen=True)
class CommandReceipt:
    argv: tuple[str, ...]
    cwd: str
    returncode: int
    stdout_sha256: str
    stderr_sha256: str

    @property
    def receipt_hash(self) -> str:
        return canonical_hash({
            "argv": list(self.argv),
            "cwd": self.cwd,
            "returncode": self.returncode,
            "stdout_sha256": self.stdout_sha256,
            "stderr_sha256": self.stderr_sha256,
        })


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
        argv=tuple(argv),
        cwd=str(cwd),
        returncode=int(completed.returncode),
        stdout_sha256=_sha256_bytes(completed.stdout),
        stderr_sha256=_sha256_bytes(completed.stderr),
    )


def _run_with_ack(
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout: int,
    store: ExecutionJobStore,
    job: ExecutionJob,
    profile: SenexActuatorProfile,
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
        raise TimeoutError("SENEX native worker timeout")
    return CommandReceipt(
        argv=tuple(argv),
        cwd=str(cwd),
        returncode=int(process.returncode),
        stdout_sha256=_sha256_bytes(stdout),
        stderr_sha256=_sha256_bytes(stderr),
    )


class SenexCheckoutSubprocessActuator:
    """ORDER-007 isolated actuator for exact accepted SENEX source; registration is not activation."""

    def __init__(self, store: ExecutionJobStore, workspace_root: Path, artifact_root: Path):
        self.store = store
        self.workspace_root = Path(workspace_root)
        self.artifact_root = Path(artifact_root)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.artifact_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _validate_input(
        spec: SenexWorkerJobSpec,
        lease: WorkLease,
        manifest: WorkerManifest,
        profile: SenexActuatorProfile,
    ) -> None:
        if profile.worker_id != manifest.worker_id or manifest.worker_id != SENEX_WORKER_ID:
            raise ValueError("wrong SENEX worker identity")
        if profile.source_sha != SENEX_ACCEPTED_SOURCE_SHA:
            raise ValueError("wrong SENEX source SHA")
        if manifest.enabled:
            raise ValueError("ORDER-007 forbids SENEX activation")
        if manifest.max_concurrency != 1:
            raise ValueError("ORDER-007 concurrency must remain one")
        if manifest.cost_ceiling_usd != 0 or spec.max_spend_usd != 0:
            raise ValueError("SENEX bridge is zero-spend only")
        for attr in ("financial_authority", "claim_authority", "submission_authority", "model_authority"):
            if bool(getattr(manifest, attr, False)):
                raise ValueError("SENEX authority expansion forbidden")
        if lease.worker_id != SENEX_WORKER_ID:
            raise ValueError("wrong worker for WorkLease")
        if lease.scope_hash != spec.scope_hash or lease.canonical_opportunity_id != spec.canonical_opportunity_id:
            raise ValueError("WorkLease immutable binding mismatch")
        if lease.terminal_state is not None:
            raise ValueError("terminal WorkLease cannot execute")
        if lease.expires_at <= datetime.now(timezone.utc):
            raise ValueError("expired WorkLease cannot execute")
        requested = {str(value).strip().lower() for value in spec.required_capabilities}
        if not requested or not requested.issubset(SENEX_ATM_CAPABILITIES):
            raise ValueError("unsupported SENEX capability")
        decision = CapabilityResolver.can_handle(manifest.capabilities, spec.required_capabilities)
        if not decision["can_handle"]:
            raise ValueError("SENEX manifest cannot handle requested capability")
        target_label = (spec.repository_or_input + " " + str(spec.senex_payload.get("target_label", ""))).lower()
        if any(marker in target_label for marker in _FORBIDDEN_TARGET_MARKERS):
            raise ValueError("SENEX production/RUNTIME017 target forbidden")

    def _materialize_spec(self, spec: SenexWorkerJobSpec, job: ExecutionJob) -> tuple[Path, Path, Path]:
        io_root = self.artifact_root / "worker-io" / job.execution_job_id
        io_root.mkdir(parents=True, exist_ok=True)
        spec_path = io_root / "atm-job-spec.json"
        task_result_path = io_root / "task-result.json"
        native_output_path = io_root / "senex-native-completion.json"
        raw = canonical_json(spec.model_dump(mode="json")).encode("utf-8")
        if _sha256_bytes(raw) != job.job_spec_hash:
            raise ValueError("materialized SENEX ATM spec hash mismatch")
        if spec_path.exists() and spec_path.read_bytes() != raw:
            raise ValueError("immutable SENEX ATM spec mutation detected")
        if not spec_path.exists():
            spec_path.write_bytes(raw)
        if task_result_path.exists() or native_output_path.exists():
            raise ValueError("preexisting SENEX result before first launch")
        return spec_path, task_result_path, native_output_path

    def _checkout(self, manifest: WorkerManifest, profile: SenexActuatorProfile, checkout: Path, env: dict[str, str]) -> None:
        if checkout.exists():
            shutil.rmtree(checkout)
        clone = _run(["git", "clone", "--no-checkout", "--filter=blob:none", manifest.repo_url, str(checkout)], self.workspace_root, env, 180)
        if clone.returncode != 0:
            raise RuntimeError("public SENEX clone failed")
        fetch = _run(["git", "fetch", "--depth=1", "origin", profile.source_sha], checkout, env, 180)
        if fetch.returncode != 0:
            raise RuntimeError("accepted SENEX source fetch failed")
        checked = _run(["git", "checkout", "--detach", profile.source_sha], checkout, env, 60)
        if checked.returncode != 0:
            raise RuntimeError("accepted SENEX source checkout failed")

    @staticmethod
    def _verify_entrypoint(checkout: Path, profile: SenexActuatorProfile, env: dict[str, str]) -> dict[str, str]:
        entrypoint = (checkout / Path(*PurePosixPath(profile.task_entrypoint).parts)).resolve()
        root = checkout.resolve()
        if entrypoint == root or root not in entrypoint.parents or not entrypoint.is_file() or entrypoint.is_symlink():
            raise ValueError("PINNED_ENTRYPOINT_REMOVED_OR_TAMPERED")
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "--", profile.task_entrypoint], cwd=checkout, env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, shell=False)
        committed = subprocess.run(["git", "rev-parse", f"{profile.source_sha}:{profile.task_entrypoint}"], cwd=checkout, env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, shell=False)
        actual = subprocess.run(["git", "hash-object", profile.task_entrypoint], cwd=checkout, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, shell=False)
        if tracked.returncode != 0 or committed.returncode != 0 or actual.returncode != 0:
            raise ValueError("PINNED_ENTRYPOINT_REMOVED_OR_TAMPERED")
        blob = committed.stdout.decode("utf-8").strip()
        actual_blob = actual.stdout.decode("utf-8").strip()
        if not re.fullmatch(r"[0-9a-f]{40}", blob) or blob != actual_blob:
            raise ValueError("PINNED_ENTRYPOINT_REMOVED_OR_TAMPERED")
        return {"path": profile.task_entrypoint, "git_blob_oid": blob, "sha256": _sha256_bytes(entrypoint.read_bytes()), "worker_source_sha": profile.source_sha}

    @staticmethod
    def _prepare_native_job(
        spec: SenexWorkerJobSpec,
        lease: WorkLease,
        execution_job: ExecutionJob,
        io_root: Path,
    ) -> tuple[dict[str, Any], Path, Path, dict[str, Any]]:
        payload = dict(spec.senex_payload)
        operation = str(payload.get("operation") or "").strip().lower()
        if operation not in SENEX_ATM_CAPABILITIES or operation not in {str(x).strip().lower() for x in spec.required_capabilities}:
            raise ValueError("SENEX operation/capability binding mismatch")
        native_operation = SENEX_NATIVE_CAPABILITY_MAP[operation]
        op_config = payload.get("operation_config")
        if not isinstance(op_config, dict):
            raise ValueError("SENEX operation_config required")
        for key in _FORBIDDEN_REQUEST_KEYS:
            if op_config.get(key):
                raise ValueError("SENEX prohibited authority request")

        workspace = io_root / "senex-workspace"
        target = workspace / "target"
        state = workspace / "state"
        target.mkdir(parents=True, exist_ok=False)
        state.mkdir(parents=True, exist_ok=False)

        allowed_paths = [_safe_rel(value) for value in spec.allowed_paths]
        if not allowed_paths:
            raise ValueError("SENEX allowed_paths required")
        files = payload.get("input_files")
        if not isinstance(files, list) or not files or len(files) > 64:
            raise ValueError("bounded SENEX input_files required")
        manifest_files: list[dict[str, Any]] = []
        evidence_files: list[dict[str, Any]] = []
        total = 0
        seen: set[str] = set()
        for row in files:
            if not isinstance(row, dict):
                raise ValueError("malformed SENEX input file")
            rel = _safe_rel(str(row.get("path") or ""))
            if rel in seen or not _is_allowed(rel, allowed_paths):
                raise ValueError("SENEX input path outside frozen allowed scope")
            seen.add(rel)
            text = row.get("text")
            if not isinstance(text, str):
                raise ValueError("SENEX input text must be UTF-8 string")
            data = text.encode("utf-8")
            total += len(data)
            if len(data) > 1_048_576 or total > 4_194_304:
                raise ValueError("SENEX input byte bound exceeded")
            digest = _sha256_bytes(data)
            if row.get("expected_sha256") is not None and row.get("expected_sha256") != digest:
                raise ValueError("TAMPERED_TARGET_SNAPSHOT")
            path = target / Path(*PurePosixPath(rel).parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            manifest_files.append({"path": rel, "bytes": len(data), "sha256": digest})
            evidence_files.append({"path": rel, "bytes": len(data), "sha256": digest, "text": text})

        snapshot_id = _sha256_bytes(_canonical_bytes({"kind": "dataset", "files": manifest_files}))
        target_manifest = {"kind": "dataset", "snapshot_id": snapshot_id, "files": manifest_files}
        data_provenance = payload.get("data_provenance")
        if not isinstance(data_provenance, dict) or not data_provenance.get("source"):
            raise ValueError("SENEX data_provenance required")
        now = datetime.now(timezone.utc)
        deadline = spec.deadline or lease.expires_at
        if deadline > lease.expires_at:
            deadline = lease.expires_at
        if deadline <= now:
            raise ValueError("SENEX job deadline expired before execution")
        native_caps = [SENEX_NATIVE_CAPABILITY_MAP[str(value).strip().lower()] for value in spec.required_capabilities]
        structured = dict(op_config)
        structured["operation"] = native_operation
        native_job: dict[str, Any] = {
            "protocol_version": SENEX_PROTOCOL,
            "job_id": spec.job_id,
            "canonical_opportunity_id": spec.canonical_opportunity_id,
            "worker_id": SENEX_WORKER_ID,
            "work_lease_id": lease.lease_id,
            "attempt": 1,
            "scope_hash": "",
            "target_repository_or_dataset": spec.repository_or_input,
            "target_base_sha_or_snapshot": snapshot_id,
            "target_snapshot_manifest": target_manifest,
            "allowed_paths": allowed_paths,
            "required_capabilities": native_caps,
            "structured_requirements": structured,
            "frozen_acceptance_criteria": [{"schema_version": "atm-acceptance.v1", "type": "zero_spend"}],
            "expected_deliverable": spec.expected_deliverable or "SENEX local evidence only",
            "deterministic_checks": list(spec.deterministic_checks),
            "data_provenance": data_provenance,
            "as_of": str(payload.get("as_of") or ""),
            "cutoff": str(payload.get("cutoff") or ""),
            "max_spend_usd": 0,
            "lease_state": "ACTIVE",
            "lease_expires_at": lease.expires_at.isoformat(),
            "job_deadline": deadline.isoformat(),
            "workspace_id": execution_job.execution_job_id,
        }
        native_job["scope_hash"] = senex_native_scope_hash(native_job)
        native_job_path = io_root / "senex-native-job.json"
        native_job_path.write_bytes(_canonical_bytes(native_job) + b"\n")
        bridge_evidence = {
            "input_files": evidence_files,
            "snapshot_manifest_hash": _sha256_bytes(_canonical_bytes(target_manifest)),
            "native_job_hash": _sha256_bytes(_canonical_bytes(native_job)),
        }
        return native_job, workspace, state, bridge_evidence

    @staticmethod
    def _translate_result(
        spec: SenexWorkerJobSpec,
        lease: WorkLease,
        execution_job: ExecutionJob,
        profile: SenexActuatorProfile,
        provenance: dict[str, str],
        native_job: dict[str, Any],
        native_output_path: Path,
        target_root: Path,
        bridge_evidence: dict[str, Any],
        command: list[str],
    ) -> dict[str, Any]:
        native = json.loads(native_output_path.read_text(encoding="utf-8"))
        if not isinstance(native, dict):
            raise ValueError("SENEX native completion must be object")
        expected = {
            "protocol_version": SENEX_PROTOCOL,
            "job_id": spec.job_id,
            "canonical_opportunity_id": spec.canonical_opportunity_id,
            "worker_id": SENEX_WORKER_ID,
            "source_sha": profile.source_sha,
            "work_lease_id": lease.lease_id,
            "scope_hash": native_job["scope_hash"],
            "outgoing_spend_usd": 0,
        }
        for key, value in expected.items():
            if native.get(key) != value:
                raise ValueError("SENEX native completion binding mismatch:" + key)
        if native.get("status") not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            raise ValueError("SENEX native completion status invalid")
        side = native.get("side_effects") if isinstance(native.get("side_effects"), dict) else {}
        for key in ("production_mutations", "supabase_writes", "northflank_mutations", "runtime017_mutations", "threshold_weight_tuning", "real_trading", "wallet_or_payment", "outgoing_spend_usd", "network_requests", "child_processes"):
            if side.get(key) != 0:
                raise ValueError("SENEX native side effect boundary violated:" + key)
        if (native.get("external_acceptance") or {}).get("authoritative") is not False:
            raise ValueError("SENEX worker claimed external acceptance authority")

        artifact_material: dict[str, dict[str, Any]] = {}
        for artifact in native.get("artifacts", []) if isinstance(native.get("artifacts"), list) else []:
            if not isinstance(artifact, dict):
                raise ValueError("malformed SENEX native artifact")
            rel = _safe_rel(str(artifact.get("path") or ""))
            path = target_root / Path(*PurePosixPath(rel).parts)
            data = path.read_bytes()
            if _sha256_bytes(data) != artifact.get("sha256") or len(data) != artifact.get("bytes"):
                raise ValueError("SENEX artifact material/hash mismatch")
            artifact_material[rel] = {
                "sha256": _sha256_bytes(data),
                "bytes": len(data),
                "text": data.decode("utf-8", "strict"),
            }
        bridge_evidence = dict(bridge_evidence)
        bridge_evidence["artifact_material"] = artifact_material

        native_hash = _sha256_bytes(_canonical_bytes(native))
        worker_result = WorkerResult(
            worker_id=SENEX_WORKER_ID,
            job_id=spec.job_id,
            status=str(native["status"]),
            worker_version=str(native.get("worker_version") or "aud067.r2"),
            work_lease_id=lease.lease_id,
            test_results={
                "native_status": native["status"],
                "operation": (native.get("task_result") or {}).get("operation"),
                "worker_assessment": native.get("worker_assessment"),
            },
            evidence_refs=[
                "senex-source:" + profile.source_sha,
                "senex-entrypoint-blob:" + provenance["git_blob_oid"],
                "senex-native-completion:" + native_hash,
            ],
            content_hashes=[native_hash, bridge_evidence["native_job_hash"], bridge_evidence["snapshot_manifest_hash"]],
            target={
                "repository_or_dataset": native_job["target_repository_or_dataset"],
                "snapshot": native_job["target_base_sha_or_snapshot"],
                "snapshot_manifest_hash": bridge_evidence["snapshot_manifest_hash"],
            },
            execution={
                "source_sha": profile.source_sha,
                "protocol": SENEX_PROTOCOL,
                "native_entrypoint": profile.task_entrypoint,
                "native_scope_hash": native_job["scope_hash"],
                "native_completion_hash": native_hash,
            },
            verification={
                "side_effects_zero": True,
                "external_acceptance_authoritative": False,
                "native_worker_assessment_authoritative": False,
            },
            findings={"task_result": native.get("task_result")},
            delivery={"artifacts": native.get("artifacts", [])},
            limitations=["Local worker evidence only; ATM independent checker remains authoritative for integration acceptance."],
            cost={"outgoing_spend_usd": 0},
            started_at=native["started_at"],
            finished_at=native["finished_at"],
            error_class=native.get("error_class"),
        )
        canonical_result = worker_result.model_dump(mode="json")
        canonical_result_hash = worker_result.envelope_hash
        return {
            "schema": "ATM_TASK_RESULT_V1",
            "execution_job_id": execution_job.execution_job_id,
            "work_lease_id": lease.lease_id,
            "scope_hash": spec.scope_hash,
            "job_spec_hash": execution_job.job_spec_hash,
            "task_type": spec.task_type,
            "producer": {
                "worker_id": SENEX_WORKER_ID,
                "worker_source_sha": profile.source_sha,
                "worker_entrypoint": profile.task_entrypoint,
                "native_module": profile.task_module,
                "native_protocol": SENEX_PROTOCOL,
                "bridge": "ATM_ORDER007_SENEX_DETERMINISTIC_BRIDGE_V1",
            },
            "native_job": native_job,
            "native_worker_result": native,
            "canonical_worker_result": canonical_result,
            "canonical_worker_result_hash": canonical_result_hash,
            "bridge_evidence": bridge_evidence,
            "native_command": command,
            "task_output": {"kind": "SENEX_NATIVE_COMPLETION_SHA256_V1", "sha256": native_hash},
            "outgoing_spend_usd": 0,
        }

    def _progress(self, execution_job_id: str, *, before: str, after: str, tests: list[str], evidence: list[str], delta: str) -> None:
        job = self.store.get(execution_job_id)
        self.store.add_progress(ProgressReceipt(
            execution_job_id=execution_job_id,
            checkpoint_seq=job.checkpoint_seq + 1,
            objective_hash=job.scope_hash,
            artifact_before_hash=before,
            artifact_after_hash=after,
            tests_run=tests,
            new_evidence_refs=evidence,
            blocker_class="NONE",
            uncertainty_or_acceptance_delta=delta,
            recommendation="CONTINUE",
            created_at=utcnow_iso(),
        ))

    def dispatch(
        self,
        spec: SenexWorkerJobSpec,
        lease: WorkLease,
        manifest: WorkerManifest,
        profile: SenexActuatorProfile,
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
        checkout = self.workspace_root / current.execution_job_id
        home = self.workspace_root / (current.execution_job_id + "-home")
        home.mkdir(parents=True, exist_ok=True)
        try:
            spec_path, task_result_path, native_output_path = self._materialize_spec(spec, current)
            env = safe_worker_env(home, current, job_spec_path=spec_path, task_result_path=task_result_path)
            self._checkout(manifest, profile, checkout, env)
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, env=env, text=True).strip()
            if head != profile.source_sha:
                return self.store.fail(current.execution_job_id, ExecutionStatus.QUARANTINED, "STALE_SENEX_SOURCE")
            provenance = self._verify_entrypoint(checkout, profile, env)
            self._progress(current.execution_job_id, before="0" * 64, after=head, tests=[],
                           evidence=["senex-source:" + head, "senex-entrypoint:" + provenance["git_blob_oid"]],
                           delta="accepted SENEX source and native entrypoint provenance bound")
            last_hash = head

            task_cwd = checkout / Path(*PurePosixPath(profile.task_cwd).parts)
            for support in profile.supporting_commands:
                receipt = _run(support, task_cwd, env, manifest.timeout_seconds)
                if receipt.returncode != 0:
                    return self.store.fail(current.execution_job_id, ExecutionStatus.DISPATCH_FAILED, "SENEX_SUPPORTING_CHECK_FAILED")
                self._progress(current.execution_job_id, before=last_hash, after=receipt.receipt_hash,
                               tests=["senex-support:" + " ".join(support)], evidence=["command:" + receipt.receipt_hash],
                               delta="SENEX project supporting check passed")
                last_hash = receipt.receipt_hash

            io_root = task_result_path.parent
            native_job, workspace, state, bridge_evidence = self._prepare_native_job(spec, lease, current, io_root)
            native_job_path = io_root / "senex-native-job.json"
            target_root = workspace / "target"
            self._progress(current.execution_job_id, before=last_hash, after=bridge_evidence["native_job_hash"],
                           tests=["senex-bridge:translate"], evidence=["native-scope:" + native_job["scope_hash"], "snapshot:" + bridge_evidence["snapshot_manifest_hash"]],
                           delta="ATM WorkerJobSpec deterministically translated to SENEX atm-worker.v1")
            last_hash = bridge_evidence["native_job_hash"]

            command = [
                "python", "-m", profile.task_module,
                "--job", str(native_job_path),
                "--workspace-root", str(workspace),
                "--state-root", str(state),
                "--target-root", str(target_root),
                "--canonical-senex-root", str(checkout),
                "--source-sha", profile.source_sha,
                "--output", str(native_output_path),
            ]
            receipt = _run_with_ack(command, task_cwd, env, manifest.timeout_seconds, self.store, current, profile)
            if receipt.returncode != 0:
                return self.store.fail(current.execution_job_id, ExecutionStatus.DISPATCH_FAILED, "SENEX_NATIVE_WORKER_FAILED")
            task_result = self._translate_result(spec, lease, current, profile, provenance, native_job, native_output_path,
                                                 target_root, bridge_evidence, command)
            task_bytes = (canonical_json(task_result) + "\n").encode("utf-8")
            task_result_path.write_bytes(task_bytes)
            task_hash = canonical_hash(task_result)
            self._progress(current.execution_job_id, before=last_hash, after=task_hash,
                           tests=["senex-native:atm-worker.v1", "senex-bridge:workerresult-translation"],
                           evidence=["native-command:" + receipt.receipt_hash, "task-result:" + task_hash,
                                     "canonical-worker-result:" + str(task_result["canonical_worker_result_hash"])],
                           delta="pinned SENEX native worker consumed frozen job and ATM translated material result")

            artifact = {
                "schema": "ATM_EXECUTION_ARTIFACT_V1",
                "execution_job_id": current.execution_job_id,
                "worker_id": SENEX_WORKER_ID,
                "worker_source_sha": profile.source_sha,
                "work_lease_id": lease.lease_id,
                "scope_hash": spec.scope_hash,
                "job_spec_hash": current.job_spec_hash,
                "task_entrypoint_provenance": provenance,
                "task_result": task_result,
                "task_result_hash": task_hash,
                "command_receipts": [receipt.__dict__],
                "outgoing_spend_usd": 0,
            }
            artifact_bytes = (canonical_json(artifact) + "\n").encode("utf-8")
            artifact_hash = _sha256_bytes(artifact_bytes)
            (self.artifact_root / f"{current.execution_job_id}.json").write_bytes(artifact_bytes)
            self.store.result_ready(current.execution_job_id, artifact_hash)
            return self.store.checking(current.execution_job_id)
        except Exception as exc:
            return self.store.fail(current.execution_job_id, ExecutionStatus.DISPATCH_FAILED, type(exc).__name__)
