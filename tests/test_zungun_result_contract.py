from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from atm_core.workers import WorkerJobSpec, WorkerRegistry, WorkerResult
from atm_core.zungun_worker import validate_zungun_result_contract

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ROOT / "workers" / "manifests"
BASE_SHA = "1" * 40
FINAL_SHA = "2" * 40
LEASE_ID = "lease-zungun-001"


class ZungunResultContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = WorkerRegistry.from_directory(MANIFESTS).get("zungun")
        self.job = WorkerJobSpec(
            job_id="fixture-zungun-offline-001",
            canonical_opportunity_id="fixture:fixture-zungun-offline-001",
            external_source="fixture",
            external_url="https://example.invalid/fixture-zungun-offline-001",
            task_type="network_reliability_offline_delivery",
            frozen_acceptance_criteria=["survives process death", "no duplicate receiver effect"],
            repository_or_input="https://github.com/example/mobile-app",
            required_capabilities=["git", "github", "filesystem", "shell", "node", "evidence", "zungun.offline_sync"],
            structured_requirements=["offline_first", "process_death", "reconciliation"],
            target_base_sha=BASE_SHA,
            allowed_paths=["android/app/src", "android/app/src/test"],
            expected_deliverable="implementation, tests and evidence",
            max_spend_usd=0,
        )

    def result(self, **updates) -> WorkerResult:
        now = datetime.now(timezone.utc)
        values = dict(
            worker_id="zungun",
            worker_version=self.manifest.worker_version,
            job_id=self.job.job_id,
            work_lease_id=LEASE_ID,
            status="READY_FOR_CHECK",
            artifact_urls=["https://github.com/simonkey888/Zungun/tree/atm-fixture-zungun-offline-001"],
            commit_sha=FINAL_SHA,
            test_results={"unit": "PASS"},
            evidence_refs=["evidence://zungun/fixture"],
            content_hashes=["sha256:" + "a" * 64],
            target={"repository": self.job.repository_or_input, "base_sha": BASE_SHA, "final_sha": FINAL_SHA},
            execution={"state": "DELIVERABLE_READY", "commands": ["npm test"], "changed_paths": ["android/app/src/OfflineQueue.kt", "android/app/src/test/OfflineQueueTest.kt"]},
            verification={"tests": ["unit:PASS"], "deterministic_checks": ["idempotency:PASS"], "resilience_checks": ["process_death:PASS", "blackout:PASS"]},
            findings={"resolved": ["retry ambiguity"], "unresolved": [], "unknown": []},
            evidence={"artifact_identifiers": ["artifact-1"], "log_identifiers": ["log-1"], "hashes": ["sha256:" + "b" * 64]},
            delivery={"candidate_url": "https://github.com/simonkey888/Zungun/tree/atm-fixture-zungun-offline-001", "candidate_commit": FINAL_SHA},
            limitations=["no OEM-device production claim"],
            cost={"outgoing_spend_usd": 0},
            started_at=now,
            finished_at=now,
        )
        values.update(updates)
        return WorkerResult(**values)

    def test_rich_result_passes_when_exactly_bound(self):
        validate_zungun_result_contract(self.result(), self.job, self.manifest, LEASE_ID)

    def test_result_requires_exact_worker_version_and_lease(self):
        with self.assertRaisesRegex(ValueError, "worker_version"):
            validate_zungun_result_contract(self.result(worker_version="wrong"), self.job, self.manifest, LEASE_ID)
        with self.assertRaisesRegex(ValueError, "WorkLease"):
            validate_zungun_result_contract(self.result(work_lease_id="wrong"), self.job, self.manifest, LEASE_ID)

    def test_result_cannot_write_outside_frozen_paths(self):
        result = self.result(execution={"state": "DELIVERABLE_READY", "commands": [], "changed_paths": [".github/workflows/escape.yml"]})
        with self.assertRaisesRegex(ValueError, "outside frozen scope"):
            validate_zungun_result_contract(result, self.job, self.manifest, LEASE_ID)

    def test_unknown_cannot_be_pass(self):
        result = self.result(status="PASS", findings={"resolved": [], "unresolved": [], "unknown": ["receiver effect not confirmed"]})
        with self.assertRaisesRegex(ValueError, "UNKNOWN"):
            validate_zungun_result_contract(result, self.job, self.manifest, LEASE_ID)

    def test_target_and_delivery_commit_must_match_final_commit(self):
        result = self.result(target={"repository": self.job.repository_or_input, "base_sha": BASE_SHA, "final_sha": "3" * 40})
        with self.assertRaisesRegex(ValueError, "final SHA"):
            validate_zungun_result_contract(result, self.job, self.manifest, LEASE_ID)


if __name__ == "__main__":
    unittest.main()
