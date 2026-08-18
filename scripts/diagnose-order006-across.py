from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from atm_core.actuator import CheckoutSubprocessActuator, load_actuator_profile, safe_worker_env
from atm_core.execution_jobs import ExecutionJobStore
from atm_core.workers import WorkerRegistry, canonical_hash, canonical_json
from scripts.qualify_order006_workers import across_spec  # type: ignore[import-not-found]


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    profile = load_actuator_profile(ROOT / "workers" / "actuators", "across-edge")
    manifest = WorkerRegistry.from_directory(ROOT / "workers" / "manifests").get("across-edge")
    spec = across_spec(profile.source_sha, "diagnostic")
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
        for command in profile.prepare_commands:
            p = subprocess.run(command, cwd=checkout, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            print(f"PREPARE={command!r} RC={p.returncode}")
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
