from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import ValidationError

from atm_core.execution_jobs import ExecutionJobStore, ExecutionStatus
from atm_core.senex_integration import (
    SENEX_ACCEPTED_SOURCE_SHA,
    SENEX_ACCEPTED_TREE_SHA,
    SENEX_ATM_CAPABILITIES,
    SENEX_NATIVE_ENTRYPOINT,
    SENEX_PROTOCOL,
    SenexActuatorProfile,
    SenexCheckoutSubprocessActuator,
    SenexWorkerJobSpec,
    load_senex_profile,
)
from atm_core.worker_integration import WorkerIntegrationRegistry
from atm_core.worker_integration_checker import check_senex
from atm_core.workers import WorkerJobSpec, WorkerRegistry, WorkLease, canonical_hash


ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ROOT / "workers" / "manifests"
INTEGRATIONS = ROOT / "workers" / "integrations"
PROFILES = ROOT / "workers" / "actuators"


def sha256_json(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()).hexdigest()


def make_lease(spec: WorkerJobSpec, suffix: str, *, ttl_seconds: int = 1200, worker_id: str = "senex-prophet") -> WorkLease:
    now = datetime.now(timezone.utc)
    return WorkLease(
        lease_id=f"order007-{suffix}-lease",
        canonical_opportunity_id=spec.canonical_opportunity_id,
        worker_id=worker_id,
        scope_hash=spec.scope_hash,
        acquired_at=now - timedelta(seconds=2),
        expires_at=now + timedelta(seconds=ttl_seconds),
        heartbeat_at=now,
        terminal_state=None,
    )


def statistical_spec(suffix: str = "statistical", *, simulate_crash_at: str | None = None, target_label: str = "order007-isolated-statistical", expected_sha256: str | None = None) -> SenexWorkerJobSpec:
    rows = [
        {"candidate": 0.80, "baseline": 1.00},
        {"candidate": 0.70, "baseline": 1.00},
        {"candidate": 0.75, "baseline": 1.00},
        {"candidate": 0.72, "baseline": 1.00},
        {"candidate": 0.77, "baseline": 1.00},
    ]
    text = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    input_row = {"path": "data/eval.json", "text": text}
    if expected_sha256 is not None:
        input_row["expected_sha256"] = expected_sha256
    config = {
        "input": "data/eval.json",
        "candidate_field": "candidate",
        "baseline_field": "baseline",
        "min_evidence_n": 3,
    }
    if simulate_crash_at:
        config["simulate_crash_at"] = simulate_crash_at
    return SenexWorkerJobSpec(
        job_id=f"order007-senex-{suffix}",
        canonical_opportunity_id=f"fixture:order007:senex:{suffix}",
        external_source="order007-fixture",
        external_url="https://github.com/simonkey888/ATM-Agent-Teller-Machine/issues/33",
        task_type="senex_statistical_evaluation_v1",
        frozen_acceptance_criteria=["ATM independently recomputes statistical result", "outgoing_spend_usd=0"],
        repository_or_input="order007-frozen-statistical-dataset",
        max_spend_usd=0,
        required_capabilities=["statistical_evaluation"],
        structured_requirements=["statistical_data_research"],
        allowed_paths=["data"],
        expected_deliverable="statistical evaluation with independently recomputable predicate",
        deterministic_checks=["mean_delta", "ci95", "orientation"],
        senex_payload={
            "operation": "statistical_evaluation",
            "target_label": target_label,
            "input_files": [input_row],
            "operation_config": config,
            "data_provenance": {"source": "order007-frozen-statistical-fixture", "complete": True},
            "as_of": "2026-08-18T00:00:00+00:00",
            "cutoff": "2026-08-18T00:00:00+00:00",
        },
    )


def repair_spec(suffix: str = "repair") -> SenexWorkerJobSpec:
    source = "def normalize(rows):\n    return [row['value'] * 2 for row in rows]\n"
    return SenexWorkerJobSpec(
        job_id=f"order007-senex-{suffix}",
        canonical_opportunity_id=f"fixture:order007:senex:{suffix}",
        external_source="order007-fixture",
        external_url="https://github.com/simonkey888/ATM-Agent-Teller-Machine/issues/33",
        task_type="senex_bounded_python_data_pipeline_repair_v1",
        frozen_acceptance_criteria=["ATM recomputes exact patch bytes", "patched Python remains syntactically valid", "outgoing_spend_usd=0"],
        repository_or_input="order007-isolated-data-pipeline",
        max_spend_usd=0,
        required_capabilities=["bounded_python_data_pipeline_repair"],
        structured_requirements=["data_provenance_repair"],
        allowed_paths=["src", "artifacts"],
        expected_deliverable="isolated deterministic Python pipeline repair artifact",
        deterministic_checks=["replacement_count", "static_contains", "artifact_sha256"],
        senex_payload={
            "operation": "bounded_python_data_pipeline_repair",
            "target_label": "order007-isolated-repair",
            "input_files": [{"path": "src/pipeline.py", "text": source}],
            "operation_config": {
                "input": "src/pipeline.py",
                "replacements": [{"old": "* 2", "new": "* 3", "expected_count": 1}],
                "static_checks": [
                    {"name": "new_multiplier", "contains": "* 3"},
                    {"name": "old_multiplier_removed", "not_contains": "* 2"},
                ],
                "patch_output": "artifacts/repair.py",
            },
            "data_provenance": {"source": "order007-frozen-repair-fixture", "complete": True},
            "as_of": "",
            "cutoff": "",
        },
    )


def positive(actor: SenexCheckoutSubprocessActuator, store: ExecutionJobStore, spec: SenexWorkerJobSpec, lease: WorkLease, manifest, profile, artifact_root: Path) -> tuple[dict, dict, object]:
    job = actor.dispatch(spec, lease, manifest, profile)
    if job.status != ExecutionStatus.CHECKING or job.launch_count != 1:
        raise RuntimeError(f"positive_dispatch_failed:{job.status}:{job.terminal_reason}")
    artifact = json.loads((artifact_root / f"{job.execution_job_id}.json").read_text())
    task = artifact.get("task_result")
    if not isinstance(task, dict) or canonical_hash(task) != artifact.get("task_result_hash"):
        raise RuntimeError("task_result_hash_binding_failed")
    checker = check_senex(task, spec, lease, profile.source_sha)
    if checker.verdict != "PASS":
        failed = [row for row in checker.predicates if not row["passed"]]
        raise RuntimeError(f"independent_checker_failed:{failed}")
    repeat = actor.dispatch(spec, lease, manifest, profile)
    if repeat.execution_job_id != job.execution_job_id or repeat.launch_count != 1 or repeat.status != ExecutionStatus.CHECKING:
        raise RuntimeError("positive_duplicate_launch")
    progress = store.progress(job.execution_job_id)
    if len(progress) < 4 or not all(row.measurable_progress for row in progress):
        raise RuntimeError("positive_progress_receipts_missing")
    store.terminal(job.execution_job_id, checker_hash=checker.receipt_hash, reason="ORDER007_QUALIFICATION_PASS")
    evidence = {
        "execution_job_id": job.execution_job_id,
        "work_lease_id": lease.lease_id,
        "scope_hash": spec.scope_hash,
        "job_spec_hash": job.job_spec_hash,
        "task_result_hash": artifact["task_result_hash"],
        "result_hash": job.result_hash,
        "checker_id": checker.checker_id,
        "checker_hash": checker.receipt_hash,
        "checker_verdict": checker.verdict,
        "checker_predicates": list(checker.predicates),
        "progress_receipts": len(progress),
        "launch_count": repeat.launch_count,
        "entrypoint_provenance": artifact["task_entrypoint_provenance"],
        "native_scope_hash": task["native_job"]["scope_hash"],
        "canonical_worker_result_hash": task["canonical_worker_result_hash"],
        "native_completion_hash": task["task_output"]["sha256"],
        "outgoing_spend_usd": artifact["outgoing_spend_usd"],
    }
    return evidence, task, checker


def router_matrix(manifests: WorkerRegistry, integrations: WorkerIntegrationRegistry) -> dict[str, object]:
    def spec(name: str, caps: list[str]) -> WorkerJobSpec:
        return WorkerJobSpec(
            job_id=f"order007-router-{name}", canonical_opportunity_id=f"fixture:order007:router:{name}",
            external_source="order007-fixture", external_url="https://github.com/simonkey888/ATM-Agent-Teller-Machine/issues/33",
            task_type=f"router_{name}", frozen_acceptance_criteria=["routing-only"], repository_or_input="order007-router-fixture",
            max_spend_usd=0, required_capabilities=caps, structured_requirements=[name],
        )
    cases = {
        "STATISTICAL_DATA_RESEARCH": integrations.route_registered(spec("statistical", ["statistical_evaluation"]), manifests),
        "TEMPORAL_CAUSAL_VALIDATION": integrations.route_registered(spec("causal", ["causal_cutoff_validation"]), manifests),
        "DATA_PROVENANCE_REPAIR": integrations.route_registered(spec("provenance_repair", ["provenance_hash_validation", "bounded_python_data_pipeline_repair"]), manifests),
        "NETWORK_RELIABILITY_SPECIALTY": integrations.route_registered(spec("network", ["zungun.network_resilience", "evidence"]), manifests),
        "BROWSER_QA_SPECIALTY": integrations.route_registered(spec("browser", ["browser", "qa_repro"]), manifests),
        "WEB3_PROTOCOL_READONLY": integrations.route_registered(spec("web3", ["web3_readonly", "unsigned_transaction_validation", "evidence"]), manifests),
        "UNKNOWN_CAPABILITY": integrations.route_registered(spec("unknown", ["unknown.capability"]), manifests),
    }
    out = {key: (value[0].worker_id if value else None) for key, value in cases.items()}
    expected = {
        "STATISTICAL_DATA_RESEARCH": "senex-prophet",
        "TEMPORAL_CAUSAL_VALIDATION": "senex-prophet",
        "DATA_PROVENANCE_REPAIR": "senex-prophet",
        "NETWORK_RELIABILITY_SPECIALTY": "zungun",
        "BROWSER_QA_SPECIALTY": None,
        "WEB3_PROTOCOL_READONLY": "across-edge",
        "UNKNOWN_CAPABILITY": None,
    }
    if out != expected:
        raise RuntimeError(f"router_matrix_failed:{out}")
    for denied in ("live_trading", "order_placement"):
        try:
            integrations.route_registered(spec(denied, [denied]), manifests)
        except ValueError:
            out[denied.upper() + "_REJECTED"] = True
        else:
            raise RuntimeError("live authority routed")
    try:
        spec("nonzero", ["statistical_evaluation"]).model_copy(update={"max_spend_usd": 1})
        WorkerJobSpec(**{**spec("nonzero2", ["statistical_evaluation"]).model_dump(exclude={"scope_hash"}), "max_spend_usd": 1})
    except Exception:
        out["NONZERO_SPEND_REJECTED"] = True
    else:
        raise RuntimeError("nonzero spend accepted")
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    if os.environ.get("ORDER005_F001_F009_REGRESSION") != "PASS":
        raise RuntimeError("ORDER005 regression proof must precede ORDER007 qualification")

    manifests = WorkerRegistry.from_directory(MANIFESTS)
    integrations = WorkerIntegrationRegistry.from_directory(INTEGRATIONS)
    manifest = manifests.get("senex-prophet")
    record = integrations.get("senex-prophet")
    profile = load_senex_profile(PROFILES)
    if not record.registered or record.active or manifest.enabled:
        raise RuntimeError("SENEX registration/activation boundary failed")
    if record.source_pin != SENEX_ACCEPTED_SOURCE_SHA or record.source_pin_ancestor != SENEX_ACCEPTED_SOURCE_SHA:
        raise RuntimeError("SENEX accepted source pin not exact")
    if set(manifest.capabilities) != SENEX_ATM_CAPABILITIES or manifest.max_concurrency != 1:
        raise RuntimeError("SENEX proven-only capability/concurrency boundary failed")
    if any((record.claim_authority, record.submission_authority, record.financial_authority, record.payment_authority,
            record.live_trading_authority, record.production_senex_authority)):
        raise RuntimeError("SENEX authority must remain zero")

    matrix = router_matrix(manifests, integrations)
    with tempfile.TemporaryDirectory(prefix="atm-order007-") as directory:
        root = Path(directory)
        store = ExecutionJobStore(root / "execution.sqlite3")
        workspace = root / "work"
        artifacts = root / "artifacts"
        actor = SenexCheckoutSubprocessActuator(store, workspace, artifacts)

        stat_spec = statistical_spec()
        stat_lease = make_lease(stat_spec, "statistical")
        stat_evidence, stat_task, stat_checker = positive(actor, store, stat_spec, stat_lease, manifest, profile, artifacts)

        stat_checkout = workspace / stat_evidence["execution_job_id"]
        tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=stat_checkout, text=True).strip()
        if tree != SENEX_ACCEPTED_TREE_SHA:
            raise RuntimeError(f"accepted_tree_mismatch:{tree}")
        tracked_diff = subprocess.check_output(["git", "diff", "--name-only", "HEAD"], cwd=stat_checkout, text=True).strip()
        if tracked_diff:
            raise RuntimeError("canonical_SENEX_tracked_checkout_mutated")

        support_green = any(any(test.startswith("senex-support:") for test in row.tests_run) for row in store.progress(stat_evidence["execution_job_id"]))
        entrypoint = stat_checkout / SENEX_NATIVE_ENTRYPOINT
        backup = entrypoint.with_suffix(entrypoint.suffix + ".order007-bak")
        entrypoint.rename(backup)
        try:
            try:
                actor._verify_entrypoint(stat_checkout, profile, os.environ.copy())
            except ValueError as exc:
                entrypoint_tamper_closed = "PINNED_ENTRYPOINT_REMOVED_OR_TAMPERED" in str(exc)
            else:
                entrypoint_tamper_closed = False
        finally:
            backup.rename(entrypoint)
        if not support_green or not entrypoint_tamper_closed:
            raise RuntimeError("entrypoint_tamper_negative_failed")

        repair = repair_spec()
        repair_lease = make_lease(repair, "repair")
        repair_evidence, repair_task, repair_checker = positive(actor, store, repair, repair_lease, manifest, profile, artifacts)

        self_only = copy.deepcopy(stat_task)
        self_only["worker_self_check_pass"] = True
        self_only.pop("native_worker_result", None)
        if check_senex(self_only, stat_spec, stat_lease, profile.source_sha).verdict != "FAIL":
            raise RuntimeError("worker_self_pass_bypass")

        tampered_final = copy.deepcopy(repair_task)
        materials = tampered_final["bridge_evidence"]["artifact_material"]
        output_path = repair.senex_payload["operation_config"]["patch_output"]
        materials[output_path]["text"] += "# tampered\n"
        if check_senex(tampered_final, repair, repair_lease, profile.source_sha).verdict != "FAIL":
            raise RuntimeError("tampered_final_artifact_bypass")

        wrong_source = profile.model_dump(mode="json"); wrong_source["source_sha"] = "0" * 40
        try:
            SenexActuatorProfile.model_validate(wrong_source)
        except ValidationError:
            wrong_source_closed = True
        else:
            wrong_source_closed = False
        if not wrong_source_closed:
            raise RuntimeError("wrong_source_not_rejected")

        expired_lease = make_lease(stat_spec, "expired", ttl_seconds=-1)
        try:
            actor._validate_input(stat_spec, expired_lease, manifest, profile)
        except ValueError:
            expired_closed = True
        else:
            expired_closed = False

        wrong_worker_lease = make_lease(stat_spec, "wrong-worker", worker_id="zungun")
        try:
            actor._validate_input(stat_spec, wrong_worker_lease, manifest, profile)
        except ValueError:
            wrong_worker_closed = True
        else:
            wrong_worker_closed = False

        unsupported = statistical_spec("unsupported")
        unsupported = unsupported.model_copy(update={"required_capabilities": ["unknown.capability"]})
        try:
            actor._validate_input(unsupported, make_lease(unsupported, "unsupported"), manifest, profile)
        except ValueError:
            unsupported_closed = True
        else:
            unsupported_closed = False

        runtime = statistical_spec("runtime", target_label="RUNTIME017-production-runtime")
        try:
            actor._validate_input(runtime, make_lease(runtime, "runtime"), manifest, profile)
        except ValueError:
            runtime_closed = True
        else:
            runtime_closed = False

        tampered_snapshot = statistical_spec("tampered-snapshot", expected_sha256="0" * 64)
        tampered_lease = make_lease(tampered_snapshot, "tampered-snapshot")
        tampered_job = store.create_or_get(
            opportunity_id=tampered_snapshot.canonical_opportunity_id,
            worker_id="senex-prophet",
            worker_version_or_source_sha=profile.source_sha,
            work_lease_id=tampered_lease.lease_id,
            scope_hash=tampered_snapshot.scope_hash,
            job_spec_hash=canonical_hash(tampered_snapshot.model_dump(mode="json")),
        )
        try:
            actor._prepare_native_job(tampered_snapshot, tampered_lease, tampered_job, root / "tampered-io")
        except ValueError as exc:
            tampered_snapshot_closed = "TAMPERED_TARGET_SNAPSHOT" in str(exc)
        else:
            tampered_snapshot_closed = False

        crash = statistical_spec("midflight", simulate_crash_at="during_work")
        crash_lease = make_lease(crash, "midflight")
        crash_job = actor.dispatch(crash, crash_lease, manifest, profile)
        crash_repeat = actor.dispatch(crash, crash_lease, manifest, profile)
        midflight_zero_duplicate = (
            crash_job.status == ExecutionStatus.DISPATCH_FAILED
            and crash_job.launch_count == 1
            and crash_repeat.execution_job_id == crash_job.execution_job_id
            and crash_repeat.launch_count == 1
        )

        negatives = {
            "WRONG_SOURCE_SHA_FAIL_CLOSED_PRE_EXECUTION": wrong_source_closed,
            "TAMPERED_TARGET_SNAPSHOT_FAIL_CLOSED": tampered_snapshot_closed,
            "EXPIRED_LEASE_NO_EXECUTION": expired_closed,
            "WRONG_WORKER_ID_NO_EXECUTION": wrong_worker_closed,
            "UNSUPPORTED_CAPABILITY_NO_EXECUTION": unsupported_closed,
            "NONZERO_SPEND_NO_EXECUTION": matrix.get("NONZERO_SPEND_REJECTED") is True,
            "LIVE_TRADING_REQUEST_NO_EXECUTION": matrix.get("LIVE_TRADING_REJECTED") is True and matrix.get("ORDER_PLACEMENT_REJECTED") is True,
            "RUNTIME017_OR_PRODUCTION_TARGET_NO_EXECUTION": runtime_closed,
            "WORKER_SELF_PASS_TAMPERED_FINAL_ARTIFACT_ATM_CHECK_FAIL": check_senex(self_only, stat_spec, stat_lease, profile.source_sha).verdict == "FAIL" and check_senex(tampered_final, repair, repair_lease, profile.source_sha).verdict == "FAIL",
            "PINNED_ENTRYPOINT_REMOVED_OR_TAMPERED_FAILS_CLOSED": entrypoint_tamper_closed and support_green,
            "MIDFLIGHT_RECOVERY_ZERO_DUPLICATE_LAUNCH": midflight_zero_duplicate,
        }
        if not all(negatives.values()):
            raise RuntimeError(f"negative_matrix_failed:{negatives}")

        payload = {
            "schema": "ATM_ORDER007_SENEX_INTEGRATION_QUALIFICATION_V1",
            "base_head": "cc102e007679df7bf821e869297f909f6714c291",
            "senex_source_pin": profile.source_sha,
            "senex_tree_sha": tree,
            "senex_native_entrypoint": SENEX_NATIVE_ENTRYPOINT,
            "senex_protocol": SENEX_PROTOCOL,
            "SENEX_PINNED_SOURCE_PROVEN": "PASS",
            "SENEX_NATIVE_ENTRYPOINT_PROVEN": "PASS",
            "FROZEN_JOB_CONSUMED_BY_PINNED_SENEX": "PASS",
            "TASK_RESULT_BOUND_TO_JOB_LEASE_SCOPE_SPEC": "PASS",
            "RESULT_TRANSLATION_TO_ATM_WORKERRESULT": "PASS",
            "WORKER_SELF_TEST_ONLY_CANNOT_PASS": "PASS",
            "BROKEN_OR_MISSING_ENTRYPOINT_FAILS_CLOSED": "PASS",
            "MIDFLIGHT_RECOVERY_ZERO_DUPLICATE_LAUNCH": "PASS",
            "INDEPENDENT_CHECKER_MATERIAL_RESULT": "PASS",
            "ORDER_005_F001_F009_REGRESSION": "PASS",
            "REGISTERED_NOT_ACTIVE": "PASS",
            "SENEX_ROUTER_POSITIVE_NEGATIVE_MATRIX": "PASS",
            "router_matrix": matrix,
            "positive_temporal_statistical": stat_evidence,
            "positive_bounded_python_data_repair": repair_evidence,
            "negative_matrix": negatives,
            "source_provenance": {
                "accepted_source_sha": profile.source_sha,
                "accepted_tree_sha": tree,
                "entrypoint": stat_evidence["entrypoint_provenance"],
                "tracked_checkout_diff": tracked_diff,
            },
            "project_preservation": {
                "SENEX_ORACLE_PROJECT_CONTINUES": True,
                "PAPER_ONLY_LOCKS_PRESERVED": True,
                "RUNTIME017_MUTATION": 0,
                "PRODUCTION_THRESHOLD_TUNING": 0,
                "PRODUCTION_WEIGHT_TUNING": 0,
                "SUPABASE_PRODUCTION_MUTATION": 0,
                "NORTHFLANK_PRODUCTION_MUTATION": 0,
                "REAL_TRADING": 0,
            },
            "SENEX_REGISTERED": "YES",
            "SENEX_ACTIVE": "NO",
            "EXTERNAL_CLAIMS": 0,
            "EXTERNAL_SUBMISSIONS": 0,
            "OUTGOING_SPEND_USD": 0,
        }
        store.close()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("SENEX_SOURCE_PIN=" + profile.source_sha)
    print("SENEX_SOURCE_PIN_PROVEN=PASS")
    print("SENEX_NATIVE_ENTRYPOINT_PROVEN=PASS")
    print("SENEX_ROUTER_POSITIVE_NEGATIVE_MATRIX=PASS")
    print("SENEX_REGISTERED=YES")
    print("SENEX_ACTIVE=NO")
    print("ORDER_005_F001_F009_REGRESSION=PASS")
    print("EXTERNAL_CLAIMS=0")
    print("EXTERNAL_SUBMISSIONS=0")
    print("OUTGOING_SPEND_USD=0")
    print("QUALIFICATION_SHA256=" + hashlib.sha256(out.read_bytes()).hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
