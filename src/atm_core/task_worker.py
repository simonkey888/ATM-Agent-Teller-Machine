from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any


def _fail(message: str, code: int) -> int:
    print(message, file=sys.stderr)
    return code


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"missing {name}")
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def main() -> int:
    """Bounded zero-secret worker-side consumer for frozen deterministic task contracts.

    This process runs inside the selected worker checkout with the same zero-secret
    environment as the worker. It must consume the exact supervisor-materialized
    WorkerJobSpec before any task result can exist. It intentionally supports only
    the deterministic digest fixture used to prove the real actuator/checker
    boundary; unsupported task types fail closed rather than pretending canned
    worker-repository tests solved the job.
    """

    try:
        spec_path = Path(_required_env("ATM_JOB_SPEC_PATH"))
        result_path = Path(_required_env("ATM_TASK_RESULT_PATH"))
        execution_job_id = _required_env("ATM_EXECUTION_JOB_ID")
        work_lease_id = _required_env("ATM_WORK_LEASE_ID")
        scope_hash = _required_env("ATM_SCOPE_HASH")
        expected_job_spec_hash = _required_env("ATM_JOB_SPEC_HASH")
        if os.environ.get("ATM_MAX_SPEND_USD") != "0":
            return _fail("nonzero spend boundary", 20)

        raw = spec_path.read_bytes()
        actual_job_spec_hash = hashlib.sha256(raw).hexdigest()
        if actual_job_spec_hash != expected_job_spec_hash:
            return _fail("materialized job spec hash mismatch", 21)
        spec = json.loads(raw.decode("utf-8"))
        if not isinstance(spec, dict):
            return _fail("job spec must be object", 22)
        if str(spec.get("scope_hash") or "") != scope_hash:
            return _fail("materialized scope mismatch", 23)
        if str(spec.get("canonical_opportunity_id") or "") == "":
            return _fail("missing opportunity binding", 24)

        task_type = str(spec.get("task_type") or "")
        if task_type != "deterministic_digest_v1":
            return _fail("unsupported frozen task type", 25)
        source_input = spec.get("repository_or_input")
        if not isinstance(source_input, str):
            return _fail("deterministic digest input must be string", 26)
        input_bytes = source_input.encode("utf-8")
        digest = hashlib.sha256(input_bytes).hexdigest()

        result = {
            "schema": "ATM_TASK_RESULT_V1",
            "execution_job_id": execution_job_id,
            "work_lease_id": work_lease_id,
            "scope_hash": scope_hash,
            "job_spec_hash": actual_job_spec_hash,
            "task_type": task_type,
            "consumed_contract": {
                "canonical_opportunity_id": str(spec.get("canonical_opportunity_id")),
                "input_sha256": digest,
                "frozen_acceptance_hash": hashlib.sha256(
                    _canonical_json(spec.get("frozen_acceptance_criteria") or []).encode("utf-8")
                ).hexdigest(),
                "allowed_scope_hash": hashlib.sha256(
                    _canonical_json(spec.get("allowed_paths") or []).encode("utf-8")
                ).hexdigest(),
                "expected_deliverable_hash": hashlib.sha256(
                    str(spec.get("expected_deliverable") or "").encode("utf-8")
                ).hexdigest(),
            },
            "task_output": {
                "kind": "SHA256_UTF8_INPUT_V1",
                "sha256": digest,
                "byte_length": len(input_bytes),
            },
            "outgoing_spend_usd": 0,
        }
        result_path.parent.mkdir(parents=True, exist_ok=True)
        with result_path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(_canonical_json(result) + "\n")
        return 0
    except Exception as exc:  # fail closed; never print secrets or raw env
        return _fail(type(exc).__name__, 30)


if __name__ == "__main__":
    raise SystemExit(main())
