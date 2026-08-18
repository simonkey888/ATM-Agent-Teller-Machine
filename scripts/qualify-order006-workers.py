from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from atm_core.actuator import CheckoutSubprocessActuator, load_actuator_profile
from atm_core.execution_integrity import ProductionExecutionIntegrity, RecoveryOutcome
from atm_core.execution_jobs import ExecutionJobStore, ExecutionStatus
from atm_core.worker_integration import WorkerIntegrationRegistry
from atm_core.workers import WorkerJobSpec, WorkerRegistry, WorkLease, canonical_hash, canonical_json

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ROOT / "workers" / "manifests"
PROFILES = ROOT / "workers" / "actuators"
INTEGRATIONS = ROOT / "workers" / "integrations"

TARGET_REPO = "https://github.com/simonkey888/VOY"
TARGET_SHA = "4c7b36b8690f8aaa0582e7b118dd7274befa559d"
TARGET_JSON = "package.json"
PINS = {
    "zungun": "f3f59767cbc5f10b78d838b2ab8fbc28f9559a7b",
    "across-edge": "0a757f83f1a51cc2c8d130a232ec217bc1cab178",
}
READINESS = {
    "zungun": "c95002f94d5c5316e83bfca7215cf1d591a62739",
    "across-edge": "f65e597a650bfaa5b09824b299811c8da713249e",
}
WORKER_REPOS = {
    "zungun": "https://github.com/simonkey888/Zungun",
    "across-edge": "https://github.com/simonkey888/Across-Edge",
}


def command(argv: list[str], cwd: Path, timeout: int = 240) -> str:
    completed = subprocess.run(
        argv,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=timeout,
    )
    if completed.returncode:
        raise RuntimeError(f"command_failed:{argv}:{completed.stderr.strip()[:800]}")
    return completed.stdout.strip()


def make_lease(spec: WorkerJobSpec, worker_id: str, suffix: str, *, expired: bool = False) -> WorkLease:
    now = datetime.now(timezone.utc)
    expires = now - timedelta(seconds=1) if expired else now + timedelta(minutes=20)
    return WorkLease(
        lease_id=f"order006-r3-{worker_id}-{suffix}",
        canonical_opportunity_id=spec.canonical_opportunity_id,
        worker_id=worker_id,
        scope_hash=spec.scope_hash,
        acquired_at=now - timedelta(seconds=2),
        expires_at=expires,
        heartbeat_at=now - timedelta(seconds=1),
        terminal_state=None,
    )


def zungun_spec(suffix: str = "real-target", *, task_type: str = "zungun_reliability_audit_v1", target_sha: str = TARGET_SHA) -> WorkerJobSpec:
    return WorkerJobSpec(
        job_id=f"order006-r3-zungun-{suffix}",
        canonical_opportunity_id=f"controlled:order006-r3:zungun:{suffix}",
        external_source="order006-r3-controlled-target",
        external_url="https://github.com/simonkey888/ATM-Agent-Teller-Machine/issues/31#issuecomment-5328444912",
        task_type=task_type,
        frozen_acceptance_criteria=["min_findings:0", "no_authority_expansion", "outgoing_spend_usd:0"],
        repository_or_input=TARGET_REPO,
        max_spend_usd=0,
        required_capabilities=[
            "git", "filesystem", "shell", "node", "evidence",
            "zungun.network_resilience", "zungun.reconciliation",
            "zungun.blackout_testing", "zungun.reliability_audit",
        ],
        structured_requirements=["network_resilience", "reconciliation", "blackout_testing", "reliability_audit"],
        target_base_sha=target_sha,
        allowed_paths=[TARGET_JSON],
        expected_deliverable="read-only reliability evidence over exact controlled non-self target",
        deterministic_checks=["link_doctor", "target_clean", "scope_unchanged", "hash_artifacts", "blackout_core", "no_authority_expansion"],
    )


def across_spec(suffix: str = "real-target", *, target_sha: str = TARGET_SHA) -> WorkerJobSpec:
    return WorkerJobSpec(
        job_id=f"order006-r3-across-{suffix}",
        canonical_opportunity_id=f"controlled:order006-r3:across:{suffix}",
        external_source="order006-r3-controlled-target",
        external_url="https://github.com/simonkey888/ATM-Agent-Teller-Machine/issues/31#issuecomment-5328444912",
        task_type="across_readonly_unsigned_tx_v1",
        frozen_acceptance_criteria=["data-only unsigned validation", "no signing", "no broadcast", "independent checker"],
        repository_or_input=TARGET_REPO,
        max_spend_usd=0,
        required_capabilities=["web3_readonly", "unsigned_transaction_validation", "evidence"],
        structured_requirements=["web3_readonly", "unsigned_transaction_validation", "no_signing", "no_broadcast"],
        target_base_sha=target_sha,
        allowed_paths=[TARGET_JSON],
        expected_deliverable="read-only unsigned transaction validation bound to exact controlled non-self target",
        deterministic_checks=[f"target_json_parse:{TARGET_JSON}"],
    )


def prove_lineage(worker_id: str, root: Path) -> dict[str, object]:
    repo = WORKER_REPOS[worker_id]
    source = PINS[worker_id]
    ancestor = READINESS[worker_id]
    checkout = root / f"lineage-{worker_id}"
    command(["git", "clone", "--no-checkout", "--filter=blob:none", repo, str(checkout)], root)
    command(["git", "fetch", "--depth=4", "origin", source], checkout)
    command(["git", "checkout", "--detach", source], checkout)
    if command(["git", "rev-parse", "HEAD"], checkout) != source:
        raise RuntimeError(f"source_pin_checkout_mismatch:{worker_id}")
    if subprocess.run(["git", "merge-base", "--is-ancestor", ancestor, source], cwd=str(checkout), check=False).returncode != 0:
        command(["git", "fetch", "--depth=4", "origin", ancestor], checkout)
    if subprocess.run(["git", "merge-base", "--is-ancestor", ancestor, source], cwd=str(checkout), check=False).returncode != 0:
        raise RuntimeError(f"readiness_not_ancestor:{worker_id}")
    changed = [line for line in command(["git", "diff", "--name-only", ancestor, source], checkout).splitlines() if line]
    if changed != ["tools/atm-worker-entrypoint.mjs"]:
        raise RuntimeError(f"worker_delta_widened:{worker_id}:{changed}")
    return {
        "worker_id": worker_id,
        "readiness_ancestor": ancestor,
        "source_pin": source,
        "changed_paths": changed,
        "minimal_descendant": True,
    }


def positive_production_path(worker_id: str, spec: WorkerJobSpec, root: Path, manifests: WorkerRegistry) -> dict[str, object]:
    manifest = manifests.get(worker_id)
    profile = load_actuator_profile(PROFILES, worker_id)
    if profile.source_sha != PINS[worker_id]:
        raise RuntimeError(f"source_pin_profile_mismatch:{worker_id}")
    lease = make_lease(spec, worker_id, "positive")
    store = ExecutionJobStore(root / f"production-{worker_id}.sqlite3")
    integrity = ProductionExecutionIntegrity(store, root / f"work-{worker_id}", root / f"artifacts-{worker_id}")
    job, outcome = integrity.execute(spec, lease, manifest, profile)
    if job.status != ExecutionStatus.TERMINAL or outcome != RecoveryOutcome.RECONCILE_RESULT:
        raise RuntimeError(f"canonical_production_path_failed:{worker_id}:{job.status}:{job.terminal_reason}")
    if job.launch_count != 1:
        raise RuntimeError(f"canonical_duplicate_launch:{worker_id}:{job.launch_count}")
    checker = integrity.checker_receipt(job.execution_job_id)
    if checker is None or checker.verdict != "PASS":
        raise RuntimeError(f"canonical_checker_not_pass:{worker_id}")
    artifact = json.loads(integrity.artifact_path(job.execution_job_id).read_text(encoding="utf-8"))
    task = artifact.get("task_result") if isinstance(artifact.get("task_result"), dict) else {}
    bridge = task.get("bridge_target") if isinstance(task.get("bridge_target"), dict) else {}
    if bridge.get("repository") != TARGET_REPO or bridge.get("target_base_sha") != TARGET_SHA or bridge.get("checked_out_head") != TARGET_SHA:
        raise RuntimeError(f"nonself_target_binding_failed:{worker_id}:{bridge}")
    if bridge.get("repository") == WORKER_REPOS[worker_id] or bridge.get("target_base_sha") == profile.source_sha:
        raise RuntimeError(f"self_target_not_eliminated:{worker_id}")
    if bridge.get("allowed_paths") != [TARGET_JSON]:
        raise RuntimeError(f"target_scope_not_consumed:{worker_id}")
    recovered, recovery = integrity.execute(spec, lease, manifest, profile)
    recovered_checker = integrity.checker_receipt(recovered.execution_job_id)
    if recovered.execution_job_id != job.execution_job_id or recovered.launch_count != 1:
        raise RuntimeError(f"terminal_reentry_duplicate_launch:{worker_id}")
    if recovered.status != ExecutionStatus.TERMINAL or recovery != RecoveryOutcome.RECONCILE_RESULT:
        raise RuntimeError(f"terminal_reentry_failed:{worker_id}")
    if recovered_checker is None or recovered_checker.receipt_hash != checker.receipt_hash:
        raise RuntimeError(f"terminal_checker_not_durable:{worker_id}")
    evidence = {
        "worker_id": worker_id,
        "source_pin": profile.source_sha,
        "target_repository": TARGET_REPO,
        "target_base_sha": TARGET_SHA,
        "target_allowed_paths": [TARGET_JSON],
        "non_self_target": True,
        "execution_job_id": recovered.execution_job_id,
        "work_lease_id": lease.lease_id,
        "scope_hash": spec.scope_hash,
        "job_spec_hash": recovered.job_spec_hash,
        "status": recovered.status.value,
        "terminal_reason": recovered.terminal_reason,
        "launch_count": recovered.launch_count,
        "result_hash": recovered.result_hash,
        "checker_id": recovered_checker.checker_id,
        "checker_hash": recovered_checker.receipt_hash,
        "checker_verdict": recovered_checker.verdict,
        "checker_reason": recovered_checker.reason,
        "material_predicates": recovered_checker.predicate_results,
        "outgoing_spend_usd": 0,
    }
    store.close()
    return evidence


def negative_expired_lease(worker_id: str, factory, root: Path, manifests: WorkerRegistry) -> dict[str, object]:
    spec = factory("negative-expired")
    lease = make_lease(spec, worker_id, "expired", expired=True)
    manifest = manifests.get(worker_id); profile = load_actuator_profile(PROFILES, worker_id)
    store = ExecutionJobStore(root / f"neg-expired-{worker_id}.sqlite3")
    integrity = ProductionExecutionIntegrity(store, root / f"neg-expired-work-{worker_id}", root / f"neg-expired-artifacts-{worker_id}")
    try:
        integrity.execute(spec, lease, manifest, profile)
    except ValueError:
        pass
    execution_id = ExecutionJobStore.identity(spec.canonical_opportunity_id, worker_id, lease.lease_id, spec.scope_hash, canonical_hash(spec.model_dump(mode="json")))
    job = store.get(execution_id)
    passed = job.status == ExecutionStatus.EXPIRED and job.launch_count == 0
    out = {"status": job.status.value, "launch_count": job.launch_count, "passed": passed}
    store.close()
    if not passed:
        raise RuntimeError(f"expired_lease_negative_failed:{worker_id}:{out}")
    return out


def negative_wrong_source(worker_id: str, factory, root: Path, manifests: WorkerRegistry) -> dict[str, object]:
    spec = factory("negative-wrong-source")
    lease = make_lease(spec, worker_id, "wrong-source")
    manifest = manifests.get(worker_id)
    profile = load_actuator_profile(PROFILES, worker_id).model_copy(update={"source_sha": "0" * 40})
    store = ExecutionJobStore(root / f"neg-source-{worker_id}.sqlite3")
    integrity = ProductionExecutionIntegrity(store, root / f"neg-source-work-{worker_id}", root / f"neg-source-artifacts-{worker_id}")
    job, _ = integrity.execute(spec, lease, manifest, profile)
    passed = job.status != ExecutionStatus.TERMINAL and job.launch_count <= 1
    out = {"status": job.status.value, "reason": job.terminal_reason, "launch_count": job.launch_count, "passed": passed}
    store.close()
    if not passed:
        raise RuntimeError(f"wrong_source_negative_failed:{worker_id}:{out}")
    return out


def negative_tampered_target(worker_id: str, factory, root: Path, manifests: WorkerRegistry) -> dict[str, object]:
    spec = factory("negative-target", target_sha="0" * 40)
    lease = make_lease(spec, worker_id, "tampered-target")
    manifest = manifests.get(worker_id); profile = load_actuator_profile(PROFILES, worker_id)
    store = ExecutionJobStore(root / f"neg-target-{worker_id}.sqlite3")
    integrity = ProductionExecutionIntegrity(store, root / f"neg-target-work-{worker_id}", root / f"neg-target-artifacts-{worker_id}")
    job, _ = integrity.execute(spec, lease, manifest, profile)
    passed = job.status != ExecutionStatus.TERMINAL and job.launch_count == 1
    out = {"status": job.status.value, "reason": job.terminal_reason, "launch_count": job.launch_count, "passed": passed}
    store.close()
    if not passed:
        raise RuntimeError(f"tampered_target_negative_failed:{worker_id}:{out}")
    raw = factory("negative-scope").model_dump(mode="json")
    raw["scope_hash"] = "0" * 64
    scope_rejected = False
    try:
        WorkerJobSpec.model_validate(raw)
    except Exception:
        scope_rejected = True
    if not scope_rejected:
        raise RuntimeError(f"tampered_scope_negative_failed:{worker_id}")
    out["tampered_scope_rejected"] = True
    return out


def negative_corrupt_material(worker_id: str, factory, root: Path, manifests: WorkerRegistry) -> dict[str, object]:
    spec = factory("negative-corrupt-material")
    lease = make_lease(spec, worker_id, "corrupt-material")
    manifest = manifests.get(worker_id); profile = load_actuator_profile(PROFILES, worker_id)
    store = ExecutionJobStore(root / f"neg-corrupt-{worker_id}.sqlite3")
    integrity = ProductionExecutionIntegrity(store, root / f"neg-corrupt-work-{worker_id}", root / f"neg-corrupt-artifacts-{worker_id}")
    created = store.create_or_get(
        opportunity_id=spec.canonical_opportunity_id,
        worker_id=worker_id,
        worker_version_or_source_sha=profile.source_sha,
        work_lease_id=lease.lease_id,
        scope_hash=spec.scope_hash,
        job_spec_hash=canonical_hash(spec.model_dump(mode="json")),
    )
    integrity.freeze_acceptance(created, spec)
    actuator = CheckoutSubprocessActuator(store, integrity.workspace_root, integrity.artifact_root)
    job = actuator.dispatch(spec, lease, manifest, profile)
    if job.status != ExecutionStatus.CHECKING:
        raise RuntimeError(f"corrupt_material_setup_failed:{worker_id}:{job.status}")
    path = integrity.artifact_path(job.execution_job_id)
    artifact = json.loads(path.read_text(encoding="utf-8"))
    task = artifact["task_result"]
    task["worker_self_check_pass"] = True
    if worker_id == "zungun":
        task["bridge_target"]["target_base_sha"] = "0" * 40
    else:
        observation = next(row for row in task["native_analysis"]["outputs"] if row.get("capability") == "unsigned_transaction_validation")
        observation["observed"]["signed"] = True
    artifact["task_result_hash"] = canonical_hash(task)
    encoded = (canonical_json(artifact) + "\n").encode("utf-8")
    path.write_bytes(encoded)
    store.result_ready(job.execution_job_id, hashlib.sha256(encoded).hexdigest())
    store.checking(job.execution_job_id)
    receipt = integrity.independently_check(store.get(job.execution_job_id), spec, lease, manifest, profile)
    checked, outcome = integrity.recover(store.get(job.execution_job_id), spec, lease, manifest, profile)
    passed = receipt.verdict == "FAIL" and checked.status == ExecutionStatus.QUARANTINED and outcome == RecoveryOutcome.CANCEL
    out = {"checker_verdict": receipt.verdict, "status": checked.status.value, "worker_self_pass_present": True, "passed": passed}
    store.close()
    if not passed:
        raise RuntimeError(f"corrupt_material_negative_failed:{worker_id}:{out}")
    return out


def negative_unsupported_checker(root: Path, manifests: WorkerRegistry) -> dict[str, object]:
    worker_id = "zungun"
    spec = zungun_spec("negative-unsupported-checker", task_type="zungun_unregistered_checker_v1")
    lease = make_lease(spec, worker_id, "unsupported-checker")
    manifest = manifests.get(worker_id); profile = load_actuator_profile(PROFILES, worker_id)
    store = ExecutionJobStore(root / "neg-unsupported-checker.sqlite3")
    integrity = ProductionExecutionIntegrity(store, root / "neg-unsupported-work", root / "neg-unsupported-artifacts")
    job, _ = integrity.execute(spec, lease, manifest, profile)
    receipt = integrity.checker_receipt(job.execution_job_id)
    passed = job.status == ExecutionStatus.QUARANTINED and receipt is not None and receipt.verdict == "FAIL"
    out = {"status": job.status.value, "checker_verdict": receipt.verdict if receipt else None, "passed": passed}
    store.close()
    if not passed:
        raise RuntimeError(f"unsupported_checker_negative_failed:{out}")
    return out


def negative_broken_entrypoint(worker_id: str, factory, root: Path, manifests: WorkerRegistry) -> dict[str, object]:
    spec = factory("negative-broken-entrypoint")
    lease = make_lease(spec, worker_id, "broken-entrypoint")
    manifest = manifests.get(worker_id)
    profile = load_actuator_profile(PROFILES, worker_id).model_copy(update={"task_entrypoint": "tools/missing-order006-r3-entrypoint.mjs"})
    store = ExecutionJobStore(root / f"neg-entrypoint-{worker_id}.sqlite3")
    integrity = ProductionExecutionIntegrity(store, root / f"neg-entrypoint-work-{worker_id}", root / f"neg-entrypoint-artifacts-{worker_id}")
    job, _ = integrity.execute(spec, lease, manifest, profile)
    result_path = integrity.artifact_root / "worker-io" / job.execution_job_id / "task-result.json"
    passed = job.status == ExecutionStatus.DISPATCH_FAILED and job.launch_count == 1 and not result_path.exists()
    out = {"status": job.status.value, "reason": job.terminal_reason, "launch_count": job.launch_count, "task_result_exists": result_path.exists(), "passed": passed}
    store.close()
    if not passed:
        raise RuntimeError(f"broken_entrypoint_negative_failed:{worker_id}:{out}")
    return out


def negative_midflight_recovery(worker_id: str, factory, root: Path, manifests: WorkerRegistry) -> dict[str, object]:
    spec = factory("negative-midflight")
    lease = make_lease(spec, worker_id, "midflight")
    manifest = manifests.get(worker_id); profile = load_actuator_profile(PROFILES, worker_id)
    store = ExecutionJobStore(root / f"neg-midflight-{worker_id}.sqlite3")
    integrity = ProductionExecutionIntegrity(store, root / f"neg-midflight-work-{worker_id}", root / f"neg-midflight-artifacts-{worker_id}")
    job = store.create_or_get(
        opportunity_id=spec.canonical_opportunity_id,
        worker_id=worker_id,
        worker_version_or_source_sha=profile.source_sha,
        work_lease_id=lease.lease_id,
        scope_hash=spec.scope_hash,
        job_spec_hash=canonical_hash(spec.model_dump(mode="json")),
    )
    integrity.freeze_acceptance(job, spec)
    dispatched, launch = store.begin_dispatch(job.execution_job_id)
    if not launch or dispatched.launch_count != 1:
        raise RuntimeError(f"midflight_setup_failed:{worker_id}")
    recovered, outcome = integrity.execute(spec, lease, manifest, profile)
    passed = recovered.status == ExecutionStatus.CANCELLED and recovered.launch_count == 1 and outcome == RecoveryOutcome.CANCEL
    out = {"status": recovered.status.value, "launch_count": recovered.launch_count, "passed": passed}
    store.close()
    if not passed:
        raise RuntimeError(f"midflight_negative_failed:{worker_id}:{out}")
    return out


def authority_negatives(manifests: WorkerRegistry, integrations: WorkerIntegrationRegistry) -> dict[str, object]:
    nonzero_rejected = False
    try:
        WorkerJobSpec(
            job_id="order006-r3-nonzero", canonical_opportunity_id="negative:nonzero", external_source="negative",
            external_url="https://github.com/simonkey888/ATM-Agent-Teller-Machine/issues/31", task_type="negative",
            frozen_acceptance_criteria=["reject"], repository_or_input=TARGET_REPO, max_spend_usd=1,
            required_capabilities=["web3_readonly"], target_base_sha=TARGET_SHA, allowed_paths=[TARGET_JSON],
        )
    except Exception:
        nonzero_rejected = True
    signing_rejected = False
    try:
        WorkerJobSpec(
            job_id="order006-r3-signing", canonical_opportunity_id="negative:signing", external_source="negative",
            external_url="https://github.com/simonkey888/ATM-Agent-Teller-Machine/issues/31", task_type="negative",
            frozen_acceptance_criteria=["reject"], repository_or_input=TARGET_REPO, max_spend_usd=0,
            required_capabilities=["web3_signer"], target_base_sha=TARGET_SHA, allowed_paths=[TARGET_JSON],
        )
    except Exception:
        signing_rejected = True
    broadcast = WorkerJobSpec(
        job_id="order006-r3-broadcast", canonical_opportunity_id="negative:broadcast", external_source="negative",
        external_url="https://github.com/simonkey888/ATM-Agent-Teller-Machine/issues/31", task_type="negative",
        frozen_acceptance_criteria=["reject"], repository_or_input=TARGET_REPO, max_spend_usd=0,
        required_capabilities=["blockchain_broadcast"], target_base_sha=TARGET_SHA, allowed_paths=[TARGET_JSON],
    )
    broadcast_rejected = False
    try:
        integrations.route_registered(broadcast, manifests)
    except ValueError:
        broadcast_rejected = True
    if not (nonzero_rejected and signing_rejected and broadcast_rejected):
        raise RuntimeError("authority_negative_failed")
    return {"nonzero_spend_rejected": nonzero_rejected, "signing_rejected": signing_rejected, "broadcast_rejected": broadcast_rejected}


def routing_matrix(manifests: WorkerRegistry, integrations: WorkerIntegrationRegistry) -> dict[str, object]:
    z = zungun_spec("route-z")
    a = across_spec("route-a")
    if integrations.route_registered(z, manifests)[0].worker_id != "zungun":
        raise RuntimeError("zungun_router_failed")
    if integrations.route_registered(a, manifests)[0].worker_id != "across-edge":
        raise RuntimeError("across_router_failed")
    if integrations.route_active(z, manifests) is not None or integrations.route_active(a, manifests) is not None:
        raise RuntimeError("inactive_worker_routed_active")
    return {"NETWORK_RELIABILITY_TASK": "zungun", "WEB3_READONLY_TASK": "across-edge", "ACTIVE_ZUNGUN_ROUTE": None, "ACTIVE_ACROSS_ROUTE": None}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out")
    args = parser.parse_args()
    manifests = WorkerRegistry.from_directory(MANIFESTS)
    integrations = WorkerIntegrationRegistry.from_directory(INTEGRATIONS)
    for worker_id in ("zungun", "across-edge"):
        record = integrations.get(worker_id); manifest = manifests.get(worker_id); profile = load_actuator_profile(PROFILES, worker_id)
        if not record.registered or record.active or manifest.enabled:
            raise RuntimeError(f"registration_activation_boundary:{worker_id}")
        if record.source_pin != PINS[worker_id] or profile.source_sha != PINS[worker_id] or record.source_pin_ancestor != READINESS[worker_id]:
            raise RuntimeError(f"source_pin_boundary:{worker_id}")
        if record.claim_authority or record.submission_authority or record.financial_authority or record.max_spend_usd != 0 or manifest.max_concurrency != 1:
            raise RuntimeError(f"authority_spend_boundary:{worker_id}")

    with tempfile.TemporaryDirectory(prefix="atm-order006-r3-") as directory:
        root = Path(directory)
        z_lineage = prove_lineage("zungun", root)
        a_lineage = prove_lineage("across-edge", root)
        z_positive = positive_production_path("zungun", zungun_spec(), root, manifests)
        a_positive = positive_production_path("across-edge", across_spec(), root, manifests)
        negatives = {
            "expired_lease_zungun": negative_expired_lease("zungun", zungun_spec, root, manifests),
            "expired_lease_across": negative_expired_lease("across-edge", across_spec, root, manifests),
            "wrong_source_zungun": negative_wrong_source("zungun", zungun_spec, root, manifests),
            "wrong_source_across": negative_wrong_source("across-edge", across_spec, root, manifests),
            "tampered_target_zungun": negative_tampered_target("zungun", zungun_spec, root, manifests),
            "tampered_target_across": negative_tampered_target("across-edge", across_spec, root, manifests),
            "corrupt_material_zungun": negative_corrupt_material("zungun", zungun_spec, root, manifests),
            "corrupt_material_across": negative_corrupt_material("across-edge", across_spec, root, manifests),
            "unsupported_checker": negative_unsupported_checker(root, manifests),
            "broken_entrypoint_zungun": negative_broken_entrypoint("zungun", zungun_spec, root, manifests),
            "broken_entrypoint_across": negative_broken_entrypoint("across-edge", across_spec, root, manifests),
            "midflight_zungun": negative_midflight_recovery("zungun", zungun_spec, root, manifests),
            "midflight_across": negative_midflight_recovery("across-edge", across_spec, root, manifests),
            "authority": authority_negatives(manifests, integrations),
        }
        payload = {
            "schema": "ATM_ORDER006_R3_CANONICAL_PRODUCTION_PATH_V1",
            "controlled_target": {"repository": TARGET_REPO, "base_sha": TARGET_SHA, "allowed_paths": [TARGET_JSON], "generated_fixture": False},
            "lineage": {"zungun": z_lineage, "across_edge": a_lineage},
            "routing_matrix": routing_matrix(manifests, integrations),
            "zungun": z_positive,
            "across_edge": a_positive,
            "mandatory_negatives": negatives,
            "F010_CANONICAL_PRODUCTION_EXECUTION": "PASS",
            "F011_GENERAL_REAL_JOB_TARGET_BRIDGE": "PASS",
            "F012_CANONICAL_ACTUATOR_GATE_REQUIRED": "PASS",
            "F010_F012": "CLOSED",
            "EXTERNAL_CLAIMS": 0,
            "EXTERNAL_SUBMISSIONS": 0,
            "WORKER_ACTIVATION": 0,
            "OUTGOING_SPEND_USD": 0,
        }
        encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        if args.out:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(encoded)
        print(f"ZUNGUN_SOURCE_PIN={PINS['zungun']}")
        print(f"ACROSS_SOURCE_PIN={PINS['across-edge']}")
        print("ZUNGUN_PRODUCTION_PATH_TERMINAL=PASS")
        print("ACROSS_PRODUCTION_PATH_TERMINAL=PASS")
        print("CANONICAL_PRODUCTION_EXECUTION=PASS")
        print(f"CONTROLLED_TARGET={TARGET_REPO}@{TARGET_SHA}")
        print("NON_SELF_NON_CANNED_TARGET=PASS")
        print("MANDATORY_NEGATIVES=PASS")
        print("F010_F012=CLOSED")
        print("EXTERNAL_CLAIMS=0")
        print("EXTERNAL_SUBMISSIONS=0")
        print("WORKER_ACTIVATION=0")
        print("OUTGOING_SPEND_USD=0")
        print(f"QUALIFICATION_SHA256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
