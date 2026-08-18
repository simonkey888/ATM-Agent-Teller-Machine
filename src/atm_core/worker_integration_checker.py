from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

from .execution_jobs import ExecutionJobStore
from .senex_integration import (
    SENEX_ATM_CAPABILITIES,
    SENEX_NATIVE_CAPABILITY_MAP,
    SENEX_NATIVE_ENTRYPOINT,
    SENEX_PROTOCOL,
    SENEX_WORKER_ID,
    SenexWorkerJobSpec,
    senex_native_scope_hash,
)
from .workers import WorkerJobSpec, WorkerResult, WorkLease, canonical_hash


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


def check_zungun(task_result: dict[str, Any], spec: WorkerJobSpec, lease: WorkLease, source_sha: str) -> IntegrationCheckerReceipt:
    predicates = _base_bindings(task_result, spec, lease, "zungun", source_sha)
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
        _predicate("TARGET_BASE_SHA", source_sha, native.get("target_base_sha")),
        _predicate("FINAL_TARGET_SHA", source_sha, native.get("final_target_sha")),
        _predicate("TARGET_UNCHANGED", [], changed),
        _predicate("NATIVE_SPEND_ZERO", 0, native.get("outgoing_spend_usd")),
        _predicate("LINK_DOCTOR_EXECUTED", True, "link_doctor" in checks),
        _predicate("BLACKOUT_CORE_EXECUTED", True, "blackout_core" in checks),
        _predicate("NATIVE_SAFE_TERMINAL_RESULT", True, safe_terminal),
        _predicate("CONTENT_HASHES_PRESENT", True, bool(native.get("content_hashes"))),
    ])
    passed = all(bool(row["passed"]) for row in predicates)
    return IntegrationCheckerReceipt(
        checker_id="ATM_ORDER006_ZUNGUN_INDEPENDENT_CHECKER_V1",
        worker_id="zungun",
        verdict="PASS" if passed else "FAIL",
        reason="MATERIAL_ZUNGUN_NATIVE_RESULT_VERIFIED" if passed else "MATERIAL_ZUNGUN_NATIVE_RESULT_MISMATCH",
        predicates=tuple(predicates),
    )


def check_across(task_result: dict[str, Any], spec: WorkerJobSpec, lease: WorkLease, source_sha: str) -> IntegrationCheckerReceipt:
    predicates = _base_bindings(task_result, spec, lease, "across-edge", source_sha)
    native = task_result.get("native_worker_result") if isinstance(task_result.get("native_worker_result"), dict) else {}
    analysis = task_result.get("native_analysis") if isinstance(task_result.get("native_analysis"), dict) else {}
    observations = analysis.get("outputs") if isinstance(analysis.get("outputs"), list) else []
    unsigned = next((row for row in observations if isinstance(row, dict) and row.get("capability") == "unsigned_transaction_validation"), {})
    observed = unsigned.get("observed") if isinstance(unsigned.get("observed"), dict) else {}
    checks = analysis.get("checks") if isinstance(analysis.get("checks"), list) else []
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
        _predicate("ANALYSIS_EXTERNAL_MUTATION_ZERO", False, analysis.get("external_mutation")),
        _predicate("ANALYSIS_SPEND_ZERO", 0, analysis.get("outgoing_spend_usd")),
        _predicate("UNSIGNED_STATUS", "VALID_AS_DATA_ONLY", observed.get("status")),
        _predicate("UNSIGNED_CHAIN_ID", 8453, observed.get("chain_id")),
        _predicate("UNSIGNED_SIGNED_ZERO", False, observed.get("signed")),
        _predicate("UNSIGNED_EXECUTED_ZERO", False, observed.get("executed")),
        _predicate("STATIC_CHECK_PASSED", True, bool(checks) and all(isinstance(row, dict) and row.get("passed") is True for row in checks)),
        _predicate("PROJECT_CHECKOUT_UNCHANGED", analysis.get("project_hash_before"), analysis.get("project_hash_after")),
    ])
    passed = all(bool(row["passed"]) for row in predicates)
    return IntegrationCheckerReceipt(
        checker_id="ATM_ORDER006_ACROSS_INDEPENDENT_CHECKER_V1",
        worker_id="across-edge",
        verdict="PASS" if passed else "FAIL",
        reason="MATERIAL_ACROSS_READONLY_RESULT_VERIFIED" if passed else "MATERIAL_ACROSS_READONLY_RESULT_MISMATCH",
        predicates=tuple(predicates),
    )


def _sha256_json(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _senex_material_verdict(task_result: dict[str, Any], spec: SenexWorkerJobSpec) -> tuple[bool, str]:
    payload = spec.senex_payload
    operation = str(payload.get("operation") or "").strip().lower()
    if operation not in SENEX_ATM_CAPABILITIES:
        return False, "unsupported_operation"
    native = task_result.get("native_worker_result") if isinstance(task_result.get("native_worker_result"), dict) else {}
    result = native.get("task_result") if isinstance(native.get("task_result"), dict) else {}
    config = payload.get("operation_config") if isinstance(payload.get("operation_config"), dict) else {}
    input_files = payload.get("input_files") if isinstance(payload.get("input_files"), list) else []
    text_by_path = {str(row.get("path")): row.get("text") for row in input_files if isinstance(row, dict) and isinstance(row.get("text"), str)}
    expected_native = SENEX_NATIVE_CAPABILITY_MAP[operation]
    if result.get("operation") != expected_native:
        return False, "native_operation_mismatch"

    if operation == "statistical_evaluation":
        text = text_by_path.get(str(config.get("input")))
        if text is None:
            return False, "missing_frozen_statistical_input"
        try:
            rows = json.loads(text)
            deltas = [float(row[config["candidate_field"]]) - float(row[config["baseline_field"]]) for row in rows]
            min_n = int(config.get("min_evidence_n", 3))
        except Exception:
            return False, "invalid_frozen_statistical_input"
        if not deltas:
            expected = {"n": 0, "orientation": "INCONCLUSIVE", "mean_delta": None, "ci95": None}
        else:
            mean = sum(deltas) / len(deltas)
            if len(deltas) < min_n:
                expected = {"n": len(deltas), "orientation": "INCONCLUSIVE", "mean_delta": mean, "ci95": None}
            else:
                var = sum((value - mean) ** 2 for value in deltas) / (len(deltas) - 1)
                se = math.sqrt(var / len(deltas))
                lo, hi = mean - 1.96 * se, mean + 1.96 * se
                orientation = "IMPROVEMENT" if hi < 0 else "DEGRADATION" if lo > 0 else "INCONCLUSIVE"
                if (payload.get("data_provenance") or {}).get("complete") is not True:
                    orientation = "INCONCLUSIVE"
                expected = {"n": len(deltas), "orientation": orientation, "mean_delta": mean, "ci95": [lo, hi]}
        for key, value in expected.items():
            if result.get(key) != value:
                return False, "statistical_recompute_mismatch:" + key
        return True, "statistical_recomputed"

    if operation == "bounded_python_data_pipeline_repair":
        text = text_by_path.get(str(config.get("input")))
        if text is None:
            return False, "missing_frozen_repair_input"
        patched = text
        try:
            for item in config.get("replacements", []):
                old, new = str(item["old"]), str(item["new"])
                expected_count = int(item.get("expected_count", 1))
                if patched.count(old) != expected_count:
                    return False, "repair_expected_count_mismatch"
                patched = patched.replace(old, new)
            for check in config.get("static_checks", []):
                if "contains" in check and str(check["contains"]) not in patched:
                    return False, "repair_static_contains_mismatch"
                if "not_contains" in check and str(check["not_contains"]) in patched:
                    return False, "repair_static_not_contains_mismatch"
        except Exception:
            return False, "invalid_frozen_repair_spec"
        expected_hash = hashlib.sha256(patched.encode("utf-8")).hexdigest()
        if result.get("status") != "PASS" or result.get("patched_sha256") != expected_hash:
            return False, "repair_native_hash_mismatch"
        output = str(config.get("patch_output") or "artifacts/repair.py")
        bridge = task_result.get("bridge_evidence") if isinstance(task_result.get("bridge_evidence"), dict) else {}
        materials = bridge.get("artifact_material") if isinstance(bridge.get("artifact_material"), dict) else {}
        material = materials.get(output) if isinstance(materials.get(output), dict) else {}
        if material.get("text") != patched or material.get("sha256") != expected_hash:
            return False, "repair_final_artifact_mismatch"
        return True, "repair_recomputed"

    if operation == "causal_cutoff_validation":
        text = text_by_path.get(str(config.get("input")))
        if text is None:
            return False, "missing_frozen_causal_input"
        try:
            rows = json.loads(text)
            cutoff = datetime.fromisoformat(str(payload["cutoff"]).replace("Z", "+00:00")).timestamp()
            violations = []
            missing = False
            for index, row in enumerate(rows):
                decision = float(row["decision_ts"])
                if decision > cutoff:
                    violations.append({"row": index, "kind": "decision_after_cutoff"})
                for field in config.get("availability_time_fields", []):
                    if float(row[field]) > decision:
                        violations.append({"row": index, "field": field, "kind": "future_availability"})
                for field in config.get("label_time_fields", []):
                    if float(row[field]) <= decision:
                        violations.append({"row": index, "field": field, "kind": "nonfuture_label"})
                for field in config.get("required_history_fields", []):
                    if row.get(field) is None:
                        missing = True
            status = "FAIL" if violations else "UNKNOWN" if missing or (payload.get("data_provenance") or {}).get("complete") is not True else "PASS"
        except Exception:
            return False, "invalid_frozen_causal_input"
        if result.get("status") != status or result.get("violations") != violations or result.get("missing_prior_history") != missing:
            return False, "causal_recompute_mismatch"
        return True, "causal_recomputed"

    return result.get("operation") == expected_native, "bounded_native_shape_only"


def check_senex(task_result: dict[str, Any], spec: SenexWorkerJobSpec, lease: WorkLease, source_sha: str) -> IntegrationCheckerReceipt:
    predicates = _base_bindings(task_result, spec, lease, SENEX_WORKER_ID, source_sha)
    producer = task_result.get("producer") if isinstance(task_result.get("producer"), dict) else {}
    native_job = task_result.get("native_job") if isinstance(task_result.get("native_job"), dict) else {}
    native = task_result.get("native_worker_result") if isinstance(task_result.get("native_worker_result"), dict) else {}
    canonical = task_result.get("canonical_worker_result") if isinstance(task_result.get("canonical_worker_result"), dict) else {}
    side = native.get("side_effects") if isinstance(native.get("side_effects"), dict) else {}
    expected_exec = ExecutionJobStore.identity(
        spec.canonical_opportunity_id,
        SENEX_WORKER_ID,
        lease.lease_id,
        spec.scope_hash,
        canonical_hash(spec.model_dump(mode="json")),
    )
    material_ok, material_reason = _senex_material_verdict(task_result, spec)
    try:
        canonical_model = WorkerResult.model_validate(canonical)
        canonical_valid = True
        canonical_envelope_hash = canonical_model.envelope_hash
    except Exception:
        canonical_valid = False
        canonical_envelope_hash = None
    command = task_result.get("native_command") if isinstance(task_result.get("native_command"), list) else []
    command_shape = len(command) >= 3 and command[:3] == ["python", "-m", "atm_worker"] and source_sha in command
    side_zero = bool(side) and all(side.get(key) == 0 for key in (
        "production_mutations", "supabase_writes", "northflank_mutations", "runtime017_mutations",
        "threshold_weight_tuning", "real_trading", "wallet_or_payment", "outgoing_spend_usd",
        "network_requests", "child_processes",
    ))
    native_hash = _sha256_json(native) if native else None
    output = task_result.get("task_output") if isinstance(task_result.get("task_output"), dict) else {}
    predicates.extend([
        _predicate("EXECUTION_JOB_ID", expected_exec, task_result.get("execution_job_id")),
        _predicate("NATIVE_PROTOCOL", SENEX_PROTOCOL, native.get("protocol_version")),
        _predicate("NATIVE_WORKER_ID", SENEX_WORKER_ID, native.get("worker_id")),
        _predicate("NATIVE_SOURCE_SHA", source_sha, native.get("source_sha")),
        _predicate("NATIVE_WORK_LEASE_ID", lease.lease_id, native.get("work_lease_id")),
        _predicate("NATIVE_JOB_ID", spec.job_id, native.get("job_id")),
        _predicate("NATIVE_SCOPE_RECOMPUTED", senex_native_scope_hash(native_job) if native_job else None, native_job.get("scope_hash")),
        _predicate("COMPLETION_SCOPE_BOUND", native_job.get("scope_hash"), native.get("scope_hash")),
        _predicate("NATIVE_STATUS_SAFE", True, native.get("status") in {"SUCCEEDED", "FAILED", "CANCELLED"}),
        _predicate("NATIVE_SPEND_ZERO", 0, native.get("outgoing_spend_usd")),
        _predicate("NATIVE_SIDE_EFFECTS_ZERO", True, side_zero),
        _predicate("NATIVE_EXTERNAL_ACCEPTANCE_NONAUTHORITATIVE", False, (native.get("external_acceptance") or {}).get("authoritative")),
        _predicate("ENTRYPOINT_PRODUCER_BINDING", SENEX_NATIVE_ENTRYPOINT, producer.get("worker_entrypoint")),
        _predicate("NATIVE_COMMAND_SHAPE", True, command_shape),
        _predicate("TASK_OUTPUT_NATIVE_HASH", native_hash, output.get("sha256")),
        _predicate("CANONICAL_WORKERRESULT_VALID", True, canonical_valid),
        _predicate("CANONICAL_WORKERRESULT_HASH", canonical_envelope_hash, task_result.get("canonical_worker_result_hash")),
        _predicate("CANONICAL_WORKERRESULT_SOURCE", source_sha, (canonical.get("execution") or {}).get("source_sha")),
        _predicate("CANONICAL_WORKERRESULT_PROTOCOL", SENEX_PROTOCOL, (canonical.get("execution") or {}).get("protocol")),
        _predicate("MATERIAL_RESULT_RECOMPUTED", True, material_ok),
    ])
    passed = all(bool(row["passed"]) for row in predicates)
    return IntegrationCheckerReceipt(
        checker_id="ATM_ORDER007_SENEX_INDEPENDENT_CHECKER_V1",
        worker_id=SENEX_WORKER_ID,
        verdict="PASS" if passed else "FAIL",
        reason=("MATERIAL_SENEX_RESULT_VERIFIED:" + material_reason) if passed else ("MATERIAL_SENEX_RESULT_MISMATCH:" + material_reason),
        predicates=tuple(predicates),
    )
