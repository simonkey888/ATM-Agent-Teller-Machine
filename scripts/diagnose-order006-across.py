from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from atm_core.actuator import CheckoutSubprocessActuator, load_actuator_profile, safe_worker_env
from atm_core.execution_jobs import ExecutionJobStore
from atm_core.workers import WorkerJobSpec, WorkerRegistry, canonical_hash, canonical_json


ROOT = Path(__file__).resolve().parents[1]


def across_spec(source: str) -> WorkerJobSpec:
    return WorkerJobSpec(
        job_id="order006-across-diagnostic",
        canonical_opportunity_id="fixture:order006:across:diagnostic",
        external_source="order006-fixture",
        external_url="https://github.com/simonkey888/ATM-Agent-Teller-Machine/issues/31",
        task_type="across_readonly_unsigned_tx_v1",
        frozen_acceptance_criteria=["data-only unsigned validation", "no signing", "no broadcast", "independent checker"],
        repository_or_input="https://github.com/simonkey888/Across-Edge",
        max_spend_usd=0,
        required_capabilities=["web3_readonly", "unsigned_transaction_validation", "evidence"],
        structured_requirements=["web3_readonly", "unsigned_transaction_validation", "no_signing", "no_broadcast"],
        target_base_sha=source,
        allowed_paths=["src/across_edge_worker"],
        expected_deliverable="read-only unsigned transaction validation evidence",
        deterministic_checks=["unsigned_tx_data_only", "checkout_unchanged", "no_external_mutation"],
    )


def main() -> int:
    profile = load_actuator_profile(ROOT / "workers" / "actuators", "across-edge")
    manifest = WorkerRegistry.from_directory(ROOT / "workers" / "manifests").get("across-edge")
    spec = across_spec(profile.source_sha)
    with tempfile.TemporaryDirectory(prefix="atm-order006-across-diagnostic-") as td:
        root = Path(td)
        store = ExecutionJobStore(root / "execution.sqlite3")
        job = store.create_or_get(
            opportunity_id=spec.canonical_opportunity_id,
            worker_id=manifest.worker_id,
            worker_version_or_source_sha=profile.source_sha,
            work_lease_id="order006-across-diagnostic-lease",
            scope_hash=spec.scope_hash,
            job_spec_hash=canonical_hash(spec.model_dump(mode="json")),
        )
        checkout = root / "checkout"
        home = root / "home"
        home.mkdir()
        io_root = root / "io"
        io_root.mkdir()
        spec_path = io_root / "job-spec.json"
        result_path = io_root / "task-result.json"
        spec_path.write_text(canonical_json(spec.model_dump(mode="json")), encoding="utf-8")
        env = safe_worker_env(home, job, job_spec_path=spec_path, task_result_path=result_path)
        env["ATM_WORKER_SOURCE_SHA"] = profile.source_sha
        actuator = CheckoutSubprocessActuator(store, root / "work", root / "artifacts")
        actuator._checkout(manifest, profile, checkout, env)
        for prepare in profile.prepare_commands:
            p = subprocess.run(prepare, cwd=checkout, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            print(f"PREPARE={prepare!r} RC={p.returncode}")
            if p.stdout: print(p.stdout[-3000:])
            if p.stderr: print(p.stderr[-3000:])
            if p.returncode != 0: return p.returncode
        p = subprocess.run(["node", str(checkout / profile.task_entrypoint)], cwd=checkout, env=env, text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        print(f"ENTRYPOINT_RC={p.returncode}")
        print("ENTRYPOINT_STDOUT=" + p.stdout[-5000:])
        print("ENTRYPOINT_STDERR=" + p.stderr[-5000:])
        print(f"TASK_RESULT_EXISTS={result_path.exists()}")
        return p.returncode


if __name__ == "__main__":
    raise SystemExit(main())
