from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "src" / "atm.py"
spec = importlib.util.spec_from_file_location("atm_r3a", MODULE)
atm = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = atm
spec.loader.exec_module(atm)

from atm_core.models import Opportunity, PaymentStatus, RuntimeState  # noqa: E402
from atm_core.payments import PaymentLedger, PaymentNotFinal, build_validated_proof  # noqa: E402
from atm_core.taskmarket_cli import (  # noqa: E402
    CANONICAL_TASKMARKET_WALLET,
    TaskmarketCapabilityBlocked,
    TaskmarketCliError,
    TaskmarketCliLane,
    TaskmarketDuplicateSubmission,
    evaluate_taskmarket_legal_status,
    materialize_supervisor_keystore,
)
from atm_core.taskmarket_maker import MakerResult  # noqa: E402


TASK_ID = "0x" + "12" * 32


def task_payload(*, payment=False, window=True, stake=False):
    return {
        "id": TASK_ID,
        "status": "open",
        "mode": "bounty",
        "submissionWindowOpen": window,
        "stakeRequired": stake,
        "pendingActions": [
            {
                "role": "worker",
                "action": "submit",
                "eligibleAddress": None,
                "requiresPayment": payment,
                "paymentAmount": "1000" if payment else None,
                "availableUntil": "2099-01-01T00:00:00Z",
            }
        ],
    }


class StatefulHttp:
    def __init__(self, state, artifact_sha=""):
        self.state = state
        self.artifact_sha = artifact_sha

    def get(self, url, headers=None):
        del headers
        if url.endswith("/submissions"):
            if not self.state.get("submitted") and not self.state.get("duplicate"):
                return []
            return [
                {
                    "id": self.state.get("submission_id", "sub-1"),
                    "workerAddress": CANONICAL_TASKMARKET_WALLET,
                }
            ]
        if "/manifest" in url:
            return {"artifacts": [{"sha256Hash": self.artifact_sha}]}
        raise AssertionError(url)


class CliRunner:
    def __init__(self, state, *, address=CANONICAL_TASKMARKET_WALLET, legal=None, task=None, fail_submit=False):
        self.state = state
        self.address_value = address
        self.legal = (
            legal
            if legal is not None
            else {"accepted": False, "enforcementEnabled": False, "status": "draft"}
        )
        self.task = task if task is not None else task_payload()
        self.fail_submit = fail_submit
        self.calls = []

    def __call__(self, args, home, env):
        self.calls.append(list(args))
        self.state["last_home"] = str(home)
        self.state["last_env"] = dict(env)
        if args == ["address"]:
            payload = {"address": self.address_value}
        elif args == ["legal", "status"]:
            payload = self.legal
        elif args[:2] == ["task", "get"]:
            payload = self.task
        elif args[:2] == ["task", "submit"]:
            self.state["submitted"] = True
            if self.fail_submit:
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="ambiguous transport failure")
            payload = {"ok": True, "submissionId": "sub-1", "idempotencyKey": "idem-1"}
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(payload), stderr="")


class FakeTaskmarketAdapter:
    def __init__(self, submit_result=None, snapshot=None, proofs=None):
        self.submit_result = submit_result or {
            "submissionId": "sub-1",
            "idempotencyKey": "idem-1",
            "artifactSha256": "a" * 64,
            "manifestVerified": True,
            "workerAddress": CANONICAL_TASKMARKET_WALLET,
            "outgoingSpendUsd": "0",
        }
        self.snapshot = snapshot or {"status": "open", "awards": []}
        self.proofs = proofs

    def fetch_authoritative(self, opportunity):
        del opportunity
        return self.snapshot

    def verify_freshness(self, opportunity, snapshot):
        del opportunity, snapshot

    def verify_funding(self, opportunity, snapshot):
        del opportunity, snapshot

    def verify_eligibility(self, opportunity, snapshot):
        del opportunity, snapshot

    def submit(self, opportunity, deliverable_url):
        del opportunity, deliverable_url
        return self.submit_result

    def monitor(self, opportunity):
        del opportunity
        return self.snapshot

    def fetch_payment(self, opportunity, recipient):
        del opportunity, recipient
        if self.proofs is None:
            raise PaymentNotFinal("not settled")
        return self.proofs


class TaskmarketCliLaneTests(unittest.TestCase):
    def _lane(self, td, *, runner=None, state=None, artifact_content="<html>ok</html>"):
        root = Path(td)
        signer = root / "signer"
        (signer / ".taskmarket").mkdir(parents=True)
        (signer / ".taskmarket" / "keystore.json").write_text("ENCRYPTED", encoding="utf-8")
        staging = root / "staging"
        staging.mkdir()
        artifact = staging / "index.html"
        artifact.write_text(artifact_content, encoding="utf-8")
        sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
        state = state if state is not None else {}
        runner = runner or CliRunner(state)
        lane = TaskmarketCliLane(signer_home=signer, staging_root=staging, runner=runner, http=StatefulHttp(state, sha))
        return lane, artifact, state, runner

    def test_missing_keystore_no_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            runner = CliRunner({})
            lane = TaskmarketCliLane(signer_home=Path(td) / "missing", staging_root=Path(td), runner=runner)
            with self.assertRaises(TaskmarketCapabilityBlocked):
                lane.address()
            self.assertEqual(runner.calls, [])

    def test_supervisor_materializes_existing_encrypted_keystore_then_removes_transport(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state = {}
            runner = CliRunner(state)
            lane = TaskmarketCliLane(signer_home=root / "signer", staging_root=root / "staging", runner=runner)
            keystore = {
                "encryptedKey": "ciphertext-only",
                "walletAddress": CANONICAL_TASKMARKET_WALLET,
                "deviceId": "device-1",
                "apiToken": "opaque-device-token",
            }
            env = {
                "TASKMARKET_KEYSTORE_B64": base64.b64encode(json.dumps(keystore).encode()).decode(),
                "TASKMARKET_API_URL": "https://api.taskmarket.dev",
            }
            result = materialize_supervisor_keystore(lane=lane, environ=env)
            self.assertTrue(result["ready"])
            self.assertTrue(result["materialized"])
            self.assertEqual(result["wallet"].lower(), CANONICAL_TASKMARKET_WALLET.lower())
            self.assertNotIn("TASKMARKET_KEYSTORE_B64", env)
            self.assertEqual(lane.keystore_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(lane.keystore_path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(runner.calls, [["address"]])

    def test_supervisor_rejects_conflicting_wallet_and_still_removes_transport(self):
        with tempfile.TemporaryDirectory() as td:
            lane = TaskmarketCliLane(signer_home=Path(td) / "signer", staging_root=Path(td) / "staging")
            keystore = {
                "encryptedKey": "ciphertext-only",
                "walletAddress": "0x" + "11" * 20,
                "deviceId": "device-1",
                "apiToken": "opaque-device-token",
            }
            env = {"TASKMARKET_KEYSTORE_B64": base64.b64encode(json.dumps(keystore).encode()).decode()}
            with self.assertRaisesRegex(TaskmarketCliError, "wallet mismatch"):
                materialize_supervisor_keystore(lane=lane, environ=env)
            self.assertNotIn("TASKMARKET_KEYSTORE_B64", env)

    def test_supervisor_rejects_symlinked_signer_directory(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            signer = root / "signer"
            signer.mkdir()
            target = root / "attacker-controlled"
            target.mkdir()
            (signer / ".taskmarket").symlink_to(target, target_is_directory=True)
            lane = TaskmarketCliLane(signer_home=signer, staging_root=root / "staging")
            keystore = {
                "encryptedKey": "ciphertext-only",
                "walletAddress": CANONICAL_TASKMARKET_WALLET,
                "deviceId": "device-1",
                "apiToken": "opaque-device-token",
            }
            env = {"TASKMARKET_KEYSTORE_B64": base64.b64encode(json.dumps(keystore).encode()).decode()}
            with self.assertRaisesRegex(TaskmarketCliError, "private real directory"):
                materialize_supervisor_keystore(lane=lane, environ=env)
            self.assertFalse((target / "keystore.json").exists())
            self.assertNotIn("TASKMARKET_KEYSTORE_B64", env)

    def test_wallet_mismatch_no_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            state = {}
            runner = CliRunner(state, address="0x" + "11" * 20)
            lane, _, _, _ = self._lane(td, runner=runner, state=state)
            with self.assertRaisesRegex(TaskmarketCliError, "wallet mismatch"):
                lane.address()
            self.assertNotIn(["task", "submit", TASK_ID], runner.calls)

    def test_legal_draft_enforcement_false_not_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            state = {}
            runner = CliRunner(
                state,
                legal={"accepted": False, "enforcementEnabled": False, "status": "draft"},
            )
            lane, _, _, _ = self._lane(td, runner=runner, state=state)
            self.assertTrue(lane.legal_allows_write())

    def test_legal_enforced_and_unaccepted_no_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            state = {}
            runner = CliRunner(
                state,
                legal={"accepted": False, "enforcementEnabled": True, "status": "current"},
            )
            lane, artifact, _, _ = self._lane(td, runner=runner, state=state)
            with self.assertRaisesRegex(TaskmarketCliError, "enforced"):
                lane.submit_checked(TASK_ID, artifact, checker_passed=True)
            self.assertFalse(state.get("submitted"))

    def test_legal_write_readiness_truth_table_and_unknown_fail_closed(self):
        accepted = evaluate_taskmarket_legal_status(
            {"accepted": True, "enforcementEnabled": True, "status": "current"}
        )
        self.assertTrue(accepted["write_ready"])
        self.assertTrue(accepted["parse_ok"])

        enforced = evaluate_taskmarket_legal_status(
            {"accepted": False, "enforcementEnabled": True, "status": "current"}
        )
        self.assertFalse(enforced["write_ready"])
        self.assertEqual(enforced["blocker"], "TASKMARKET_LEGAL_ACCEPTANCE_REQUIRED")

        draft = evaluate_taskmarket_legal_status(
            {
                "accepted": False,
                "enforcementEnabled": False,
                "status": "draft",
                "bundleVersion": "2026-07-draft-2",
                "bundleDigest": "sha256:test",
            }
        )
        self.assertTrue(draft["write_ready"])
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(draft["bundle_version"], "2026-07-draft-2")

        unsafe_metadata = evaluate_taskmarket_legal_status(
            {
                "accepted": False,
                "enforcementEnabled": False,
                "status": "draft",
                "bundleVersion": "draft\ninjected=true",
                "bundleDigest": "sha256:test\ninjected=true",
            }
        )
        self.assertTrue(unsafe_metadata["write_ready"])
        self.assertEqual(unsafe_metadata["bundle_version"], "UNAVAILABLE")
        self.assertEqual(unsafe_metadata["bundle_digest"], "UNAVAILABLE")

        for malformed in (
            {},
            {"accepted": False, "enforcementEnabled": False},
            {"accepted": "false", "enforcementEnabled": False, "status": "draft"},
            {"accepted": False, "enforcementEnabled": "false", "status": "draft"},
            {"accepted": False, "enforcementEnabled": False, "status": "unknown"},
        ):
            with self.subTest(malformed=malformed):
                result = evaluate_taskmarket_legal_status(malformed)
                self.assertFalse(result["write_ready"])
                self.assertFalse(result["parse_ok"])
                self.assertEqual(result["blocker"], "TASKMARKET_LEGAL_STATUS_UNKNOWN")

    def test_requires_payment_true_no_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            state = {}
            runner = CliRunner(state, task=task_payload(payment=True))
            lane, artifact, _, _ = self._lane(td, runner=runner, state=state)
            with self.assertRaisesRegex(TaskmarketCliError, "outgoing payment"):
                lane.submit_checked(TASK_ID, artifact, checker_passed=True)
            self.assertFalse(state.get("submitted"))

    def test_stake_required_no_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            state = {}
            runner = CliRunner(state, task=task_payload(stake=True))
            lane, artifact, _, _ = self._lane(td, runner=runner, state=state)
            with self.assertRaisesRegex(TaskmarketCliError, "stake"):
                lane.submit_checked(TASK_ID, artifact, checker_passed=True)
            self.assertFalse(state.get("submitted"))

    def test_artifact_outside_staging_root_no_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            lane, _, state, _ = self._lane(td)
            outside = Path(td) / "outside.html"
            outside.write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(TaskmarketCliError, "outside"):
                lane.submit_checked(TASK_ID, outside, checker_passed=True)
            self.assertFalse(state.get("submitted"))

    def test_checker_fail_no_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            lane, artifact, state, _ = self._lane(td)
            with self.assertRaisesRegex(TaskmarketCliError, "checker"):
                lane.submit_checked(TASK_ID, artifact, checker_passed=False)
            self.assertFalse(state.get("submitted"))

    def test_already_submitted_wallet_task_skip_no_duplicate(self):
        with tempfile.TemporaryDirectory() as td:
            state = {"duplicate": True, "submission_id": "existing-1"}
            runner = CliRunner(state)
            lane, artifact, _, _ = self._lane(td, runner=runner, state=state)
            with self.assertRaises(TaskmarketDuplicateSubmission) as ctx:
                lane.submit_checked(TASK_ID, artifact, checker_passed=True)
            self.assertEqual(ctx.exception.submission_id, "existing-1")
            self.assertNotIn("submitted", state)

    def test_free_submit_success_verifies_manifest_and_identity(self):
        with tempfile.TemporaryDirectory() as td:
            lane, artifact, state, runner = self._lane(td, artifact_content="<!doctype html><html><script>1</script></html>")
            receipt = lane.submit_checked(TASK_ID, artifact, checker_passed=True)
            self.assertEqual(receipt.submission_id, "sub-1")
            self.assertEqual(receipt.idempotency_key, "idem-1")
            self.assertTrue(receipt.manifest_verified)
            self.assertEqual(receipt.outgoing_spend_usd, "0")
            self.assertEqual(sum(1 for c in runner.calls if c[:2] == ["task", "submit"]), 1)
            self.assertGreaterEqual(sum(1 for c in runner.calls if c[:2] == ["task", "get"]), 2)
            self.assertTrue(state["submitted"])

    def test_ambiguous_submit_is_resolved_without_retry(self):
        with tempfile.TemporaryDirectory() as td:
            state = {}
            runner = CliRunner(state, fail_submit=True)
            lane, artifact, _, _ = self._lane(td, runner=runner, state=state, artifact_content="<!doctype html><html><script>1</script></html>")
            receipt = lane.submit_checked(TASK_ID, artifact, checker_passed=True)
            self.assertEqual(receipt.submission_id, "sub-1")
            self.assertEqual(sum(1 for c in runner.calls if c[:2] == ["task", "submit"]), 1)


class TaskmarketSupervisorTests(unittest.TestCase):
    def _opp(self, artifact):
        return Opportunity(
            canonical_opportunity_id=f"taskmarket:{TASK_ID}",
            source="taskmarket",
            authoritative_url=f"https://taskmarket.dev/tasks/{TASK_ID}",
            upstream_status="open",
            reward_gross=Decimal("6"),
            deliverable_url=str(artifact),
            checker_passed=True,
            task_mode="bounty",
        )

    def test_worker_cannot_inherit_taskmarket_signer_context(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            "os.environ",
            {
                "TASKMARKET_KEYSTORE_B64": "secret",
                "TASKMARKET_DEVICE_TOKEN": "secret2",
                "ATM_TASKMARKET_SIGNER_HOME": "/real/signer",
            },
            clear=False,
        ):
            home = Path(td) / "worker-home"
            env = atm.sanitized_worker_env(home)
            self.assertNotIn("TASKMARKET_KEYSTORE_B64", env)
            self.assertNotIn("TASKMARKET_DEVICE_TOKEN", env)
            self.assertNotIn("ATM_TASKMARKET_SIGNER_HOME", env)
            self.assertEqual(env["HOME"], str(home))
            self.assertEqual(env["USERPROFILE"], str(home))

    def test_submit_moves_task_to_in_flight_and_immediately_discovers_again(self):
        with tempfile.TemporaryDirectory() as td:
            artifact = Path(td) / "index.html"
            artifact.write_text("<!doctype html><html></html>", encoding="utf-8")
            opp = self._opp(artifact)
            state = RuntimeState(phase=atm.Phase.SUBMIT, active_opportunity=opp)
            atm.phase_submit({}, state, {"taskmarket": FakeTaskmarketAdapter()})
            self.assertEqual(state.phase, atm.Phase.DISCOVER)
            self.assertIsNone(state.active_opportunity)
            self.assertEqual(len(state.in_flight), 1)
            self.assertEqual(state.in_flight[0].inflight_stage, "SUBMITTED")
            self.assertEqual(state.last_result["status"], "SUBMITTED_NONBLOCKING")

    def test_first_task_monitoring_does_not_block_second_discover_cycle(self):
        with tempfile.TemporaryDirectory() as td:
            artifact = Path(td) / "index.html"
            artifact.write_text("<!doctype html><html></html>", encoding="utf-8")
            inflight = self._opp(artifact).model_copy(update={"submission_id": "sub-1", "inflight_stage": "SUBMITTED"})
            state = RuntimeState(phase=atm.Phase.DISCOVER, in_flight=[inflight])
            ledger = PaymentLedger(Path(td) / "ledger.jsonl")
            config = {"min_reward_usd": 5, "discovery_order": [], "payment_recipient_public_identifier": CANONICAL_TASKMARKET_WALLET}
            atm.run_cycle(config, state, {"taskmarket": FakeTaskmarketAdapter()}, ledger)
            self.assertEqual(state.phase, atm.Phase.DISCOVER)
            self.assertEqual(len(state.in_flight), 1)
            self.assertEqual(state.last_result["status"], "NO_ELIGIBLE_OPPORTUNITY")

    def test_settlement_for_other_wallet_is_not_money(self):
        with tempfile.TemporaryDirectory() as td:
            artifact = Path(td) / "index.html"
            artifact.write_text("x", encoding="utf-8")
            inflight = self._opp(artifact).model_copy(update={"submission_id": "sub-1", "inflight_stage": "SUBMITTED"})
            snapshot = {"awards": [{"workerAddress": "0x" + "33" * 20, "settlementTxHash": "0x" + "44" * 32}]}
            state = RuntimeState(in_flight=[inflight])
            ledger = PaymentLedger(Path(td) / "ledger.jsonl")
            atm.monitor_in_flight(
                {"payment_recipient_public_identifier": CANONICAL_TASKMARKET_WALLET},
                state,
                {"taskmarket": FakeTaskmarketAdapter(snapshot=snapshot)},
                ledger,
            )
            self.assertEqual(ledger.realized_withdrawable_usd(), Decimal("0"))
            self.assertEqual(state.in_flight[0].inflight_stage, "SUBMITTED")

    def test_validated_award_and_base_transfer_can_advance_withdrawable(self):
        with tempfile.TemporaryDirectory() as td:
            artifact = Path(td) / "index.html"
            artifact.write_text("x", encoding="utf-8")
            inflight = self._opp(artifact).model_copy(update={"submission_id": "sub-1", "inflight_stage": "SUBMITTED"})
            proof = build_validated_proof(
                source="taskmarket_award+base_receipt",
                platform="taskmarket",
                payout_id_or_txid="0x" + "55" * 32,
                event_index_or_unique_id="award-1",
                amount=Decimal("5.55"),
                currency="USDC",
                recipient=CANONICAL_TASKMARKET_WALLET,
                status=PaymentStatus.RELEASED,
                timestamp=datetime.now(timezone.utc),
                authoritative_url=f"https://api.taskmarket.dev/api/tasks/{TASK_ID}",
                chain_id=8453,
                token_address="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            )
            snapshot = {"awards": [{"workerAddress": CANONICAL_TASKMARKET_WALLET}]}
            state = RuntimeState(in_flight=[inflight])
            ledger = PaymentLedger(Path(td) / "ledger.jsonl")
            atm.monitor_in_flight(
                {"payment_recipient_public_identifier": CANONICAL_TASKMARKET_WALLET},
                state,
                {"taskmarket": FakeTaskmarketAdapter(snapshot=snapshot, proofs=[proof])},
                ledger,
            )
            self.assertEqual(ledger.realized_withdrawable_usd(), Decimal("5.55"))
            self.assertEqual(state.in_flight[0].inflight_stage, "WITHDRAWABLE")
            self.assertTrue(state.in_flight[0].inflight_terminal)

    def test_taskmarket_work_uses_supervised_zero_cost_maker_not_hermes(self):
        with tempfile.TemporaryDirectory() as td:
            artifact = Path(td) / "generated" / "index.html"
            artifact.parent.mkdir()
            artifact.write_text("<!doctype html><html><script>const x=1;</script></html>", encoding="utf-8")
            made = MakerResult(artifact, "index.html", True, ("complete",), "gemma-4-31b-it")

            class FakeMaker:
                def __init__(self, model):
                    self.model = model

                def make(self, **kwargs):
                    self.kwargs = kwargs
                    return made

            opp = Opportunity(
                canonical_opportunity_id=f"taskmarket:{TASK_ID}",
                source="taskmarket",
                authoritative_url=f"https://taskmarket.dev/tasks/{TASK_ID}",
                upstream_status="open",
                reward_gross=Decimal("6"),
                task_description="Build one self-contained index.html",
            )
            state = RuntimeState(phase=atm.Phase.WORK, active_opportunity=opp)
            with patch.object(atm, "TaskmarketZeroCostMaker", FakeMaker), patch.object(atm, "agent_workspace", return_value=artifact.parent):
                atm.phase_work({"taskmarket_autopilot": {"maker_model": "gemma-4-31b-it"}}, state)
            self.assertEqual(state.phase, atm.Phase.CHECK)
            self.assertEqual(state.active_opportunity.maker_model, "gemma-4-31b-it")
            self.assertTrue(state.active_opportunity.maker_checker_passed)


if __name__ == "__main__":
    unittest.main()
