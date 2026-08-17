from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from atm_core.immune import BehaviorFingerprint, CriticExecution, CriticPolicy, ImmuneStore, validate_critic_payload

ROOT = Path(__file__).resolve().parents[1]


class ImmuneTests(unittest.TestCase):
    def test_distinct_adversarial_sibling_and_unknown_blocks_required_submit(self):
        with self.assertRaisesRegex(ValueError, "distinct"):
            CriticExecution(
                execution_job_id="critic-1", critic_worker_id="boqa", implementer_worker_id="boqa",
                acceptance_contract_hash="a" * 64, artifact_hash="b" * 64, evidence_hash="c" * 64,
                limitations_hash="d" * 64, verdict="POSITIVE"
            )
        critic = CriticExecution(
            execution_job_id="critic-2", critic_worker_id="boqa-critic", implementer_worker_id="boqa",
            acceptance_contract_hash="a" * 64, artifact_hash="b" * 64, evidence_hash="c" * 64,
            limitations_hash="d" * 64, verdict="UNKNOWN", findings=["acceptance evidence incomplete"]
        )
        self.assertTrue(CriticPolicy.required(150, "LOW"))
        self.assertFalse(CriticPolicy.submit_allowed(True, critic))
        self.assertFalse(CriticPolicy.submit_allowed(True, critic.model_copy(update={"verdict": "NEGATIVE"})))
        self.assertTrue(CriticPolicy.submit_allowed(True, critic.model_copy(update={"verdict": "POSITIVE"})))

    def test_critic_cannot_claim_economic_truth(self):
        for payload in ({"paid": True}, {"nested": {"external_accepted": True}}, {"withdrawable_usdc": 5}):
            with self.assertRaisesRegex(ValueError, "economic"):
                validate_critic_payload(payload)

    def test_seed_is_append_only_and_evidence_backed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ImmuneStore(Path(directory) / "immune.sqlite3")
            self.assertEqual(store.seed_from_file(ROOT / "config" / "immune-seed.json"), 1)
            self.assertEqual(store.seed_from_file(ROOT / "config" / "immune-seed.json"), 0)
            row = store.conn.execute("SELECT signature_json FROM failure_signatures").fetchone()
            self.assertIn("basescan.org/tx/", row[0])
            store.close()

    def test_capability_granular_quarantine_and_evidence_reenable(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ImmuneStore(Path(directory) / "immune.sqlite3")
            store.quarantine("zungun", "zungun.offline_sync", "NO_PROGRESS_3X")
            self.assertTrue(store.is_quarantined("zungun", "zungun.offline_sync"))
            self.assertFalse(store.is_quarantined("zungun", "zungun.reconciliation"))
            with self.assertRaisesRegex(ValueError, "doctor"):
                store.reenable("zungun", "zungun.offline_sync", doctor_hash="", canary_hash="x")
            store.reenable("zungun", "zungun.offline_sync", doctor_hash="d" * 64, canary_hash="c" * 64)
            self.assertFalse(store.is_quarantined("zungun", "zungun.offline_sync"))
            store.close()

    def test_minimal_behavioral_fingerprint_is_append_only(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ImmuneStore(Path(directory) / "immune.sqlite3")
            fingerprint = BehaviorFingerprint(
                execution_job_id="exec-1", worker_id="boqa", worker_source_sha="f" * 40,
                command_hashes=["a" * 64], test_hashes=["b" * 64], evidence_hashes=["c" * 64],
                duration_bucket="LT_5M", terminal_status="TERMINAL"
            )
            self.assertTrue(store.record_fingerprint(fingerprint))
            self.assertFalse(store.record_fingerprint(fingerprint))
            store.close()


if __name__ == "__main__":
    unittest.main()
