from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .workers import WorkerJobSpec, WorkLease, canonical_hash


@dataclass(frozen=True)
class IntegrationCheckerReceipt:
    checker_id: str
    worker_id: str
    verdict: str
    reason: str
    predicates: tuple[dict[str, Any], ...]

    @property
    def receipt_hash(self) -> str:
        return canonical_hash({
            "checker_id": self.checker_id,
            "worker_id": self.worker_id,
            "verdict": self.verdict,
            "reason": self.reason,
            "predicates": list(self.predicates),
        })


def _predicate(name: str, expected: Any, actual: Any) -> dict[str, Any]:
    return {"predicate": name, "expected": expected, "actual": actual, "passed": actual == expected}


def _norm_repo(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith(".git"):
        text = text[:-4]
    return text.rstrip("/")


def _base_bindings(task_result: dict[str, Any], spec: WorkerJobSpec, lease: WorkLease, worker_id: str, source_sha: str) -> list[dict[str, Any]]:
    producer = task_result.get("producer") if isinstance(task_result.get("producer"), dict) else {}
    return [
        _predicate("ATM_SCHEMA", "ATM_TASK_RESULT_V1", task_result.get("schema")),
        _predicate("WORK_LEASE_ID", lease.lease_id, task_result.get("work_lease_id")),
        _predicate("SCOPE_HASH", spec.scope_hash, task_result.get("scope_hash")),
        _predicate("JOB_SPEC_HASH", canonical_hash(spec.model_dump(mode="json")), task_result.get("job_spec_hash")),
        _predicate("TASK_TYPE", spec.task_type, task_result.get("task_type")),
        _predicate("PRODUCER_WORKER_ID", worker_id, producer.get("worker_id")),
        _predicate("PRODUCER_SOURCE_SHA", source_sha, producer.get("worker_source_sha")),
        _predicate("OUTGOING_SPEND_USD", 0, task_result.get("outgoing_spend_usd")),
    ]


def _bridge_target_predicates(task_result: dict[str, Any], spec: WorkerJobSpec) -> list[dict[str, Any]]:
    bridge = task_result.get("bridge_target") if isinstance(task_result.get("bridge_target"), dict) else {}
    return [
        _predicate("BRIDGE_TARGET_REPOSITORY", _norm_repo(spec.repository_or_input), _norm_repo(bridge.get("repository"))),
        _predicate("BRIDGE_TARGET_BASE_SHA", spec.target_base_sha, bridge.get("target_base_sha")),
        _predicate("BRIDGE_CHECKED_OUT_HEAD", spec.target_base_sha, bridge.get("checked_out_head")),
        _predicate("BRIDGE_ALLOWED_PATHS", list(spec.allowed_paths), bridge.get("allowed_paths")),
    ]


def check_zungun(task_result: dict[str, Any], spec: WorkerJobSpec, lease: WorkLease, source_sha: str) -> IntegrationCheckerReceipt:
    predicates = _base_bindings(task_result, spec, lease, "zungun", source_sha)
    predicates.extend(_bridge_target_predicates(task_result, spec))
    native = task_result.get("native_worker_result") if isinstance(task_result.get("native_worker_result"), dict) else {}
    checks = native.get("tests_checks") if isinstance(native.get("tests_checks"), list) else []
    changed = native.get("changed_paths") if isinstance(native.get("changed_paths"), list) else None
    state = native.get("execution_state")
    safe_terminal = state in {"COMPLETED", "COMPLETED_WITH_FINDINGS", "UNKNOWN", "AMBIGUOUS"}
    predicates.extend([
        _predicate("NATIVE_SCHEMA", "ZUNGUN_WORKER_RESULT_V1", native.get("schema")),
        _predicate("NATIVE_ORIGIN", "ZUNGUN", native.get("origin")),
        _predicate("NATIVE_WORKER_ID", "zungun", native.get("worker_id")),
        _predicate("NATIVE_EXECUTION_JOB_ID", task_result.get("execution_job_id"), native.get("execution_job_id")),
        _predicate("NATIVE_WORK_LEASE_ID", lease.lease_id, native.get("work_lease_id")),
        _predicate("NATIVE_SCOPE_HASH", spec.scope_hash, native.get("scope_hash")),
        _predicate("NATIVE_JOB_SPEC_HASH", canonical_hash(spec.model_dump(mode="json")), native.get("job_spec_hash")),
        _predicate("NATIVE_SOURCE_SHA", source_sha, native.get("worker_source_sha")),
        _predicate("NATIVE_TARGET_REPOSITORY", _norm_repo(spec.repository_or_input), _norm_repo(native.get("target_repository"))),
        _predicate("TARGET_BASE_SHA", spec.target_base_sha, native.get("target_base_sha")),
        _predicate("FINAL_TARGET_SHA", spec.target_base_sha, native.get("final_target_sha")),
        _predicate("NATIVE_ALLOWED_PATHS", list(spec.allowed_paths), native.get("allowed_paths")),
        _predicate("TARGET_UNCHANGED", [], changed),
        _predicate("NATIVE_SPEND_ZERO", 0, native.get("outgoing_spend_usd")),
        _predicate("LINK_DOCTOR_EXECUTED", True, "link_doctor" in checks),
        _predicate("BLACKOUT_CORE_EXECUTED", True, "blackout_core" in checks),
        _predicate("NATIVE_SAFE_TERMINAL_RESULT", True, safe_terminal),
        _predicate("CONTENT_HASHES_PRESENT", True, bool(native.get("content_hashes"))),
    ])
    passed = all(bool(row["passed"]) for row in predicates)
    return IntegrationCheckerReceipt(
        checker_id="ATM_ORDER006_ZUNGUN_INDEPENDENT_CHECKER_V2",
        worker_id="zungun",
        verdict="PASS" if passed else "FAIL",
        reason="MATERIAL_ZUNGUN_NATIVE_RESULT_VERIFIED" if passed else "MATERIAL_ZUNGUN_NATIVE_RESULT_MISMATCH",
        predicates=tuple(predicates),
    )


def check_across(task_result: dict[str, Any], spec: WorkerJobSpec, lease: WorkLease, source_sha: str) -> IntegrationCheckerReceipt:
    predicates = _base_bindings(task_result, spec, lease, "across-edge", source_sha)
    predicates.extend(_bridge_target_predicates(task_result, spec))
    native = task_result.get("native_worker_result") if isinstance(task_result.get("native_worker_result"), dict) else {}
    analysis = task_result.get("native_analysis") if isinstance(task_result.get("native_analysis"), dict) else {}
    observations = analysis.get("outputs") if isinstance(analysis.get("outputs"), list) else []
    unsigned = next((row for row in observations if isinstance(row, dict) and row.get("capability") == "unsigned_transaction_validation"), {})
    observed = unsigned.get("observed") if isinstance(unsigned.get("observed"), dict) else {}
    checks = analysis.get("checks") if isinstance(analysis.get("checks"), list) else []
    target_check = next((str(row).split(":", 1)[1] for row in spec.deterministic_checks if str(row).startswith("target_json_parse:") and ":" in str(row)), None)
    expected_to = "0x" + str(spec.target_base_sha or "")[:40]
    target_check_passed = bool(target_check) and any(
        isinstance(row, dict)
        and row.get("kind") == "json_parse"
        and row.get("path") == target_check
        and row.get("passed") is True
        for row in checks
    )
    predicates.extend([
        _predicate("NATIVE_WORKER_ID", "across-edge", native.get("worker_id")),
        _predicate("NATIVE_STATUS", "RESULT_READY", native.get("status")),
        _predicate("NATIVE_SOURCE_SHA", source_sha, native.get("source_sha")),
        _predicate("NATIVE_WORK_LEASE_ID", lease.lease_id, native.get("work_lease_id")),
        _predicate("NATIVE_SPEND_ZERO", 0, native.get("outgoing_spend_usd")),
        _predicate("AUTHORITATIVE_ACCEPTANCE_ZERO", False, native.get("authoritative_acceptance")),
        _predicate("PAID_ZERO", False, native.get("paid")),
        _predicate("WITHDRAWABLE_ZERO", False, native.get("withdrawable")),
        _predicate("EXECUTED_ONCHAIN_ZERO", False, native.get("executed_onchain")),
        _predicate("PAYOUT_SUCCESS_ZERO", False, native.get("payout_success")),
        _predicate("ANALYSIS_TARGET_REPOSITORY", _norm_repo(spec.repository_or_input), _norm_repo(analysis.get("target_repository"))),
        _predicate("ANALYSIS_TARGET_BASE_SHA", spec.target_base_sha, analysis.get("target_base_sha")),
        _predicate("ANALYSIS_EXTERNAL_MUTATION_ZERO", False, analysis.get("external_mutation")),
        _predicate("ANALYSIS_SPEND_ZERO", 0, analysis.get("outgoing_spend_usd")),
        _predicate("UNSIGNED_STATUS", "VALID_AS_DATA_ONLY", observed.get("status")),
        _predicate("UNSIGNED_CHAIN_ID", 8453, observed.get("chain_id")),
        _predicate("UNSIGNED_TARGET_DERIVED_TO", expected_to.lower(), str(observed.get("to") or "").lower()),
        _predicate("UNSIGNED_DATA_BYTES", 32, observed.get("data_bytes")),
        _predicate("UNSIGNED_VALUE_ZERO", 0, observed.get("value")),
        _predicate("UNSIGNED_SIGNED_ZERO", False, observed.get("signed")),
        _predicate("UNSIGNED_EXECUTED_ZERO", False, observed.get("executed")),
        _predicate("TARGET_STATIC_CHECK_PASSED", True, target_check_passed),
        _predicate("PROJECT_CHECKOUT_UNCHANGED", analysis.get("project_hash_before"), analysis.get("project_hash_after")),
    ])
    passed = all(bool(row["passed"]) for row in predicates)
    return IntegrationCheckerReceipt(
        checker_id="ATM_ORDER006_ACROSS_INDEPENDENT_CHECKER_V2",
        worker_id="across-edge",
        verdict="PASS" if passed else "FAIL",
        reason="MATERIAL_ACROSS_READONLY_RESULT_VERIFIED" if passed else "MATERIAL_ACROSS_READONLY_RESULT_MISMATCH",
        predicates=tuple(predicates),
    )


WorkerMaterialChecker = Callable[[dict[str, Any], WorkerJobSpec, WorkLease, str], IntegrationCheckerReceipt]

CHECKER_REGISTRY: dict[tuple[str, str], WorkerMaterialChecker] = {
    ("zungun", "zungun_reliability_audit_v1"): check_zungun,
    ("across-edge", "across_readonly_unsigned_tx_v1"): check_across,
}


def check_registered_worker_material(
    worker_id: str,
    task_result: dict[str, Any],
    spec: WorkerJobSpec,
    lease: WorkLease,
    source_sha: str,
) -> IntegrationCheckerReceipt:
    checker = CHECKER_REGISTRY.get((worker_id, spec.task_type))
    if checker is None:
        raise ValueError(f"unsupported worker material checker: {worker_id}:{spec.task_type}")
    return checker(task_result, spec, lease, source_sha)
