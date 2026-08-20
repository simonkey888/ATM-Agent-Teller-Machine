from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from atm_core.browser_worker import BrowserJob, BrowserPolicyError
from atm_core.capability_registry import REGISTRY_SCHEMA, public_registry
from atm_core.cash_canon import taskmarket_cash_decision
from atm_core.effect_boundary import UniversalEffectBoundary, effect_key
from atm_core.provider_registry import admitted_provider_ids, public_registry as provider_registry
from atm_core.video_worker import VideoJob, VideoShortFormWorker, VideoWorkerError


NOW = datetime(2026, 8, 20, 15, 0, tzinfo=timezone.utc)
WALLET = "0x" + "12" * 20


def task(**changes):
    value = {
        "id": "0x" + "34" * 32,
        "status": "open",
        "phase": "active",
        "mode": "bounty",
        "description": "Assemble exactly one MP4 short-form video from owner-provided local footage, required duration 30 seconds and subtitles",
        "submissionCount": 1,
        "pitchCount": 0,
        "submissionWindowOpen": True,
        "stakeRequired": False,
        "escrowTxHash": "0x" + "56" * 32,
        "netReward": "12000000",
        "expiryTime": "2026-08-21T15:00:00Z",
        "pendingActions": [{"role": "worker", "action": "submit", "eligibleAddress": None, "requiresPayment": False, "paymentAmount": None}],
    }
    value.update(changes)
    return value


class CapabilityAndProviderRegistryTests(unittest.TestCase):
    def test_registry_contract_is_complete_and_only_passed_zero_cost_rows_enable(self):
        registry = public_registry()
        self.assertEqual(registry["schema"], REGISTRY_SCHEMA)
        required = {
            "capability_id", "version", "executor_id", "checker_id", "required_tools", "sandbox_profile",
            "supported_input_types", "supported_output_types", "cost_ceiling_usd", "commercial_use_contract",
            "benchmark_fixture_ids", "benchmark_result", "max_runtime_seconds", "max_memory_mb", "network_policy",
            "artifact_contract", "failure_policy", "enabled",
        }
        for row in registry["capabilities"]:
            self.assertEqual(set(row), required)
            if row["enabled"]:
                self.assertEqual(row["cost_ceiling_usd"], "0")
                self.assertEqual(row["benchmark_result"], "PASS")

    def test_free_provider_fabric_is_fail_closed_and_public(self):
        registry = provider_registry()
        by_id = {row["provider_id"]: row for row in registry["providers"]}
        self.assertEqual(admitted_provider_ids(), ("gemini-api-free",))
        self.assertFalse(by_id["gemini-api-free"]["auto_overage_possible"])
        self.assertIn("PUBLIC_NONCONFIDENTIAL", by_id["gemini-api-free"]["data_retention_training_disclosure"])
        self.assertEqual(by_id["opencode-zen-free-catalog"]["admission"], "DISCOVERY_ONLY_UNTIL_MODEL_COMMERCIAL_TERMS_AND_NO_OVERAGE_PROVEN")


class CashCanonR2Tests(unittest.TestCase):
    def test_video_is_executable_only_inside_proven_rights_and_zero_spend_contract(self):
        allowed = taskmarket_cash_decision(task(), canonical_wallet=WALLET, existing_submission=False, signer_ready=True, now=NOW, capability_runtime_ready=True)
        self.assertEqual(allowed.disposition, "EXECUTABLE")
        self.assertTrue(allowed.allocation_allowed)
        self.assertEqual(allowed.work_class, "VIDEO_SHORT_FORM_ASSEMBLY")
        for description in (
            "Assemble exactly one MP4 using paid stock API, required duration 30 seconds",
            "Assemble exactly one celebrity likeness video, required duration 30 seconds",
            "Assemble exactly one MP4 and post to TikTok, required duration 30 seconds",
        ):
            with self.subTest(description=description):
                rejected = taskmarket_cash_decision(task(description=description), canonical_wallet=WALLET, existing_submission=False, signer_ready=True, now=NOW, capability_runtime_ready=True)
                self.assertFalse(rejected.allocation_allowed)
                self.assertIn("VIDEO_RIGHTS_OR_PAID_DEPENDENCY_UNSUPPORTED", rejected.reasons)

    def test_competition_and_shadow_contract_are_fail_closed(self):
        crowded = taskmarket_cash_decision(task(submissionCount=13), canonical_wallet=WALLET, existing_submission=False, signer_ready=True, now=NOW, capability_runtime_ready=True)
        self.assertIn("COMPETITION_ABOVE_THRESHOLD", crowded.reasons)
        invalid = taskmarket_cash_decision(task(), canonical_wallet=WALLET, existing_submission=False, signer_ready=False, now=NOW, admission_mode="SHADOW_BENCHMARK", shadow_contract={})
        self.assertFalse(invalid.allocation_allowed)
        valid = taskmarket_cash_decision(
            task(), canonical_wallet=WALLET, existing_submission=False, signer_ready=False, now=NOW,
            admission_mode="SHADOW_BENCHMARK",
            shadow_contract={"mutation_authority": False, "signer_visible": False, "economic_state_unchanged": True, "not_counted_as_execution": True, "resource_budget_seconds": 60},
            capability_runtime_ready=True,
        )
        self.assertEqual(valid.disposition, "SHADOW_BENCHMARK")
        self.assertTrue(valid.allocation_allowed)


class UniversalEffectBoundaryChaosTests(unittest.TestCase):
    def test_crash_after_commit_requires_authoritative_absence_before_redrive(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "effects.sqlite3"
            boundary = UniversalEffectBoundary(path)
            record = boundary.prepare(canonical_identity=WALLET, canonical_opportunity_id="taskmarket:1", external_action="SUBMIT", canonical_args={"sha256": "a" * 64})
            boundary.precondition_refetched(record.effect_key)
            boundary.committing(record.effect_key)
            restarted = UniversalEffectBoundary(path)
            self.assertEqual(restarted.get(record.effect_key).state, "COMMITTING")
            with self.assertRaises(RuntimeError):
                restarted.committing(record.effect_key)
            restarted.redrive_proven_absent(record.effect_key)
            self.assertEqual(restarted.get(record.effect_key).state, "PREPARED")
            restarted.close()
            boundary.close()

    def test_authoritative_recovery_commits_once_and_corrupt_transition_fails(self):
        with tempfile.TemporaryDirectory() as td:
            boundary = UniversalEffectBoundary(Path(td) / "effects.sqlite3")
            record = boundary.prepare(canonical_identity=WALLET, canonical_opportunity_id="telegram:job-1", external_action="NOTIFY", canonical_args={"fingerprint": "abc"})
            boundary.recover_committed(record.effect_key, "telegram-message-7")
            committed = boundary.get(record.effect_key)
            self.assertEqual((committed.state, committed.external_receipt_id), ("COMMITTED", "telegram-message-7"))
            self.assertEqual(boundary.recover_committed(record.effect_key, "telegram-message-7").state, "COMMITTED")
            with self.assertRaises(RuntimeError):
                boundary.precondition_refetched(record.effect_key)
            boundary.close()

    def test_effect_identity_covers_external_object_hash(self):
        base = dict(canonical_identity=WALLET, canonical_opportunity_id="x", external_action="SUBMIT", canonical_args={"a": 1})
        self.assertNotEqual(effect_key(**base, current_external_object_hash="a"), effect_key(**base, current_external_object_hash="b"))


class BrowserAndVideoBoundaryTests(unittest.TestCase):
    @patch("atm_core.browser_worker.socket.getaddrinfo", return_value=[(None, None, None, None, ("127.0.0.1", 443))])
    def test_browser_rejects_private_dns_even_on_allowed_host(self, _):
        with self.assertRaises(BrowserPolicyError):
            BrowserJob("https://example.com/a", ("example.com",), True).validate()

    def test_browser_rejects_credentials_and_non_https(self):
        for job in (
            BrowserJob("http://example.com", ("example.com",), False),
            BrowserJob("https://user:pw@example.com", ("example.com",), False),
            BrowserJob("https://example.com", ("example.com",), False, credential_scope="SESSION"),
        ):
            with self.subTest(url=job.url):
                with self.assertRaises(BrowserPolicyError):
                    job.validate()

    def test_video_rejects_missing_staging_and_false_provenance_before_ffmpeg(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source.mp4"
            source.write_bytes(b"fixture")
            provenance = root / "rights.json"
            provenance.write_text(json.dumps({"rights": "OWNER_PROVIDED", "source_sha256": hashlib.sha256(b"other").hexdigest()}), encoding="utf-8")
            worker = object.__new__(VideoShortFormWorker)
            worker.ffmpeg = "ffmpeg"
            worker.ffprobe = "ffprobe"
            with self.assertRaisesRegex(VideoWorkerError, "staging"):
                worker.assemble(VideoJob(source, root / "out.mp4", provenance, 1.0))
            with self.assertRaisesRegex(VideoWorkerError, "provenance hash"):
                worker.assemble(VideoJob(source, root / "out.mp4", provenance, 1.0, staging_root=root))


if __name__ == "__main__":
    unittest.main()
