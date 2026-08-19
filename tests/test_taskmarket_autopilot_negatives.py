from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from tests.test_taskmarket_autopilot import CliRunner, TASK_ID, task_payload

import importlib.util
import sys

MODULE = Path(__file__).resolve().parents[1] / "src" / "atm.py"
spec = importlib.util.spec_from_file_location("atm_r3a_neg", MODULE)
atm = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = atm
spec.loader.exec_module(atm)

from atm_core.payments import PaymentValidationError, TaskmarketPaymentAdapter  # noqa: E402
from atm_core.taskmarket_cli import (  # noqa: E402
    CANONICAL_TASKMARKET_WALLET,
    TaskmarketCliError,
    TaskmarketCliLane,
)


class StaticHttp:
    def __init__(self, payload):
        self.payload = payload

    def get(self, url, headers=None):
        del url, headers
        return self.payload


class RejectingChain:
    def verify_usdc_transfer(self, tx_hash, recipient, amount):
        del tx_hash, recipient, amount
        raise PaymentValidationError("Base receipt does not contain canonical USDC transfer")


class MandatoryR3ANegativeControls(unittest.TestCase):
    def _lane(self, td: str, task: dict):
        root = Path(td)
        signer = root / "signer"
        (signer / ".taskmarket").mkdir(parents=True)
        (signer / ".taskmarket" / "keystore.json").write_text("ENCRYPTED", encoding="utf-8")
        staging = root / "staging"
        staging.mkdir()
        artifact = staging / "index.html"
        artifact.write_text("<!doctype html><html><script>const x=1;</script></html>" * 8, encoding="utf-8")
        state = {}
        runner = CliRunner(state, task=task)

        class NoRows:
            def get(self, url, headers=None):
                del headers
                if url.endswith("/submissions"):
                    return []
                raise AssertionError(url)

        return TaskmarketCliLane(signer_home=signer, staging_root=staging, runner=runner, http=NoRows()), artifact, state, runner

    def test_no_worker_submit_pending_action_no_mutation(self):
        task = task_payload()
        task["pendingActions"] = []
        with tempfile.TemporaryDirectory() as td:
            lane, artifact, state, runner = self._lane(td, task)
            with self.assertRaisesRegex(TaskmarketCliError, "pendingAction"):
                lane.submit_checked(TASK_ID, artifact, checker_passed=True)
            self.assertFalse(state.get("submitted"))
            self.assertEqual(sum(1 for call in runner.calls if call[:2] == ["task", "submit"]), 0)

    def test_wrong_eligible_address_no_mutation(self):
        task = task_payload()
        task["pendingActions"][0]["eligibleAddress"] = "0x" + "88" * 20
        with tempfile.TemporaryDirectory() as td:
            lane, artifact, state, runner = self._lane(td, task)
            with self.assertRaisesRegex(TaskmarketCliError, "not eligible"):
                lane.submit_checked(TASK_ID, artifact, checker_passed=True)
            self.assertFalse(state.get("submitted"))
            self.assertEqual(sum(1 for call in runner.calls if call[:2] == ["task", "submit"]), 0)

    def test_worker_process_cannot_resolve_real_signer_home_or_keystore(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            signer_home = root / "signer"
            (signer_home / ".taskmarket").mkdir(parents=True)
            secret = signer_home / ".taskmarket" / "keystore.json"
            secret.write_text("ENCRYPTED-ONLY", encoding="utf-8")
            worker_home = root / "worker"
            with patch.dict(
                os.environ,
                {
                    "HOME": str(signer_home),
                    "USERPROFILE": str(signer_home),
                    "ATM_TASKMARKET_SIGNER_HOME": str(signer_home),
                    "TASKMARKET_KEYSTORE_B64": "opaque",
                },
                clear=False,
            ):
                env = atm.sanitized_worker_env(worker_home)
                cp = subprocess.run(
                    [sys.executable, "-c", "from pathlib import Path; import os; p=Path.home()/'.taskmarket'/'keystore.json'; print(int(p.exists())); print(os.getenv('ATM_TASKMARKET_SIGNER_HOME',''))"],
                    env=env,
                    text=True,
                    capture_output=True,
                    check=True,
                )
            lines = cp.stdout.splitlines()
            self.assertEqual(lines[0], "0")
            self.assertEqual(lines[1], "")
            self.assertTrue(secret.exists())

    def test_settlement_fields_without_base_usdc_receipt_are_not_money(self):
        task = {
            "awards": [
                {
                    "id": "award-1",
                    "workerAddress": CANONICAL_TASKMARKET_WALLET,
                    "settlementTxHash": "0x" + "77" * 32,
                    "settledAt": datetime.now(timezone.utc).isoformat(),
                    "workerPayment": str(int(Decimal("5.55") * Decimal(1_000_000))),
                }
            ]
        }
        adapter = TaskmarketPaymentAdapter(http=StaticHttp(task), chain=RejectingChain())
        with self.assertRaisesRegex(PaymentValidationError, "Base receipt"):
            adapter.fetch_payment(TASK_ID, CANONICAL_TASKMARKET_WALLET)

    def test_first_party_actions_feed_uses_signer_lane_read_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            signer = root / "signer"
            (signer / ".taskmarket").mkdir(parents=True)
            (signer / ".taskmarket" / "keystore.json").write_text("ENCRYPTED", encoding="utf-8")
            calls = []

            def runner(args, home, env):
                del home, env
                calls.append(list(args))
                return subprocess.CompletedProcess(args, 0, stdout='{"actions":[]}', stderr="")

            lane = TaskmarketCliLane(signer_home=signer, staging_root=root / "staging", runner=runner)
            self.assertEqual(lane.actions(), {"actions": []})
            self.assertEqual(calls, [["actions"]])
            self.assertNotIn(["init"], calls)


if __name__ == "__main__":
    unittest.main()
