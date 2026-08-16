from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from atm_core.models import Opportunity, Phase, RuntimeState
from atm_core.money_velocity import MonthlyTargetStore, choose_money_velocity, money_velocity, monthly_metrics, reject_reason
from atm_core.opportunities import OpportunityValidationError

control = importlib.import_module("atm_control")
runtime = importlib.import_module("atm_v2")


def funded_opportunity(identity, *, reward="50", hours="1", payer="0.9", updated_hours_ago=1, competition=0):
    return Opportunity(
        canonical_opportunity_id=identity,
        source="test",
        authoritative_url="https://example.test/task",
        upstream_status="open",
        created_at=datetime.now(timezone.utc) - timedelta(hours=updated_hours_ago),
        updated_at=datetime.now(timezone.utc) - timedelta(hours=updated_hours_ago),
        reward_gross=Decimal(reward),
        expected_fees=Decimal("0"),
        funding_proof={"escrow_funded": True, "escrow_tx_hash": "0xabc"},
        competition=competition,
        eligibility="PUBLIC_AGENT",
        payment_method="base:USDC",
        expected_agent_hours=Decimal(hours),
        payout_latency_hours=Decimal("1"),
        p_eligible=Decimal("1"),
        p_claim=Decimal("1"),
        p_complete=Decimal("1"),
        p_accept=Decimal("0.9"),
        p_pay=Decimal("1"),
        p_withdrawable=Decimal("1"),
        p_payer_active=Decimal(payer),
    )


class MoneyVelocityTests(unittest.TestCase):
    def test_live_small_task_beats_large_dead_payer(self):
        big = funded_opportunity("big", reward="500", hours="3", payer="0.01")
        small = funded_opportunity("small", reward="50", hours="1", payer="0.95")
        self.assertEqual(choose_money_velocity([big, small]).canonical_opportunity_id, "small")
        self.assertGreater(money_velocity(small), money_velocity(big))

    def test_stale_task_rejected(self):
        self.assertEqual(reject_reason(funded_opportunity("stale", updated_hours_ago=24 * 31)), "STALE_DEMAND")

    def test_no_funding_is_rejected(self):
        opp = funded_opportunity("no-funding")
        opp.funding_proof = {}
        self.assertEqual(reject_reason(opp), "NO_FUNDING_PROOF")

    def test_unfunded_status_string_is_not_funding_proof(self):
        opp = funded_opportunity("unfunded")
        opp.funding_proof = {"escrow_status": "pending"}
        self.assertEqual(reject_reason(opp), "NO_FUNDING_PROOF")

    def test_short_task_wins_hard_default_cap(self):
        selected = choose_money_velocity(
            [funded_opportunity("short", reward="10", hours="1"), funded_opportunity("long", reward="1000", hours="8")]
        )
        self.assertEqual(selected.canonical_opportunity_id, "short")

    def test_monthly_target_growth_law(self):
        with tempfile.TemporaryDirectory() as td:
            store = MonthlyTargetStore(Path(td) / "targets.json", Decimal("500"))
            aug = datetime(2026, 8, 16, tzinfo=timezone.utc)
            self.assertEqual(store.target_for(aug, []), Decimal("500"))
            proofs = [
                SimpleNamespace(
                    timestamp=datetime(2026, 8, 20, tzinfo=timezone.utc),
                    normalized_usd=Decimal("700"),
                    platform="rail",
                )
            ]
            self.assertEqual(store.target_for(datetime(2026, 9, 2, tzinfo=timezone.utc), proofs), Decimal("735.00"))

    def test_only_authoritative_proof_objects_feed_monthly_metrics(self):
        now = datetime(2026, 8, 16, tzinfo=timezone.utc)
        metrics = monthly_metrics(
            [SimpleNamespace(timestamp=now, normalized_usd=Decimal("75"), platform="workprotocol")],
            Decimal("500"),
            now,
        )
        self.assertEqual(metrics.monthly_realized_withdrawable_usd, Decimal("75"))
        self.assertEqual(metrics.monthly_gap_usd, Decimal("425"))
        self.assertEqual(metrics.payouts_count, 1)


class FakeWorkProtocolAdapter:
    name = "workprotocol"

    def __init__(self, claims):
        self.claims = claims

    def fetch_authoritative(self, opportunity):
        return {
            "job": {"status": "open", "maxWorkers": 1, "escrowFunded": True, "paymentAmount": "75"},
            "claims": self.claims,
            "payments": [],
        }

    def verify_freshness(self, opportunity, snapshot):
        return None

    def verify_funding(self, opportunity, snapshot):
        return None

    def verify_eligibility(self, opportunity, snapshot):
        return None

    def inspect_competition(self, opportunity, snapshot):
        return {"claims": len(self.claims), "open_prs": 0}


class ClaimCapacityTests(unittest.TestCase):
    def _opp(self):
        opp = funded_opportunity("workprotocol:job-1")
        opp.source = "workprotocol"
        opp.authoritative_url = "https://workprotocol.ai/jobs/job-1"
        return opp

    def test_first_wins_full_capacity_is_reroute_before_claim(self):
        state = RuntimeState(phase=Phase.VERIFY, active_opportunity=self._opp())
        adapter = FakeWorkProtocolAdapter([{"id": "claim-other", "agentId": "other", "status": "claimed"}])
        with patch.object(runtime, "local_atm_value", return_value="ours"):
            with self.assertRaises(OpportunityValidationError):
                runtime.phase_verify_v2({}, state, {"workprotocol": adapter})

    def test_existing_own_claim_is_recoverable_not_rejected(self):
        state = RuntimeState(phase=Phase.VERIFY, active_opportunity=self._opp())
        adapter = FakeWorkProtocolAdapter([{"id": "claim-ours", "agentId": "ours", "status": "claimed"}])
        with patch.object(runtime, "local_atm_value", return_value="ours"):
            runtime.phase_verify_v2({}, state, {"workprotocol": adapter})
        self.assertEqual(state.phase, Phase.CLAIM)
        self.assertEqual(state.active_opportunity.claims, 1)


class FakeBus:
    def __init__(self):
        self.posts = []
        self.fail_once = False

    def post_result(self, body):
        if self.fail_once:
            self.fail_once = False
            raise control.GitHubTransientError("temporary", 1)
        self.posts.append(body)
        return 1000 + len(self.posts)

    def get_comments(self, etag=None, since=None):
        return [], etag, True

    def get_issue(self, issue):
        return {"number": issue, "state": "open", "title": "ORDER"}


def command_comment(comment_id=100, author="simonkey888", command="STATUS", command_id="cmd-1", args=None):
    return {
        "id": comment_id,
        "user": {"login": author},
        "body": "\n".join(
            ["ATM_CMD_V1", f"COMMAND={command}", f"COMMAND_ID={command_id}", "ARGS=" + json.dumps(args or {}, separators=(",", ":"))]
        ),
    }


class CommandBusTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "repository": "simonkey888/ATM-Agent-Teller-Machine",
            "owner_login": "simonkey888",
            "control_issue": 4,
            "branch": "agent/atm-v1",
            "poll_seconds": 30,
            "ignore_comment_ids_before_or_equal": 0,
        }

    def test_result_prefix_cannot_parse_as_command(self):
        with self.assertRaises(control.CommandRejected):
            control.parse_command("ATM_RESULT_V1\nCOMMAND=STATUS\nCOMMAND_ID=x\nARGS={}")

    def test_unknown_shell_surface_rejected(self):
        with self.assertRaises(control.CommandRejected):
            control.parse_command("ATM_CMD_V1\nCOMMAND=SHELL\nCOMMAND_ID=x\nARGS={}")

    def test_unauthorized_author_rejected(self):
        with tempfile.TemporaryDirectory() as td, patch.object(control, "CONTROLLER_STATE", Path(td) / "state.json"):
            c = control.Controller(self.config, FakeBus())
            with self.assertRaises(control.CommandRejected):
                c.authorize(command_comment(author="mallory"))

    def test_command_executes_at_most_once_if_result_post_retries(self):
        with (
            tempfile.TemporaryDirectory() as td,
            patch.object(control, "CONTROLLER_STATE", Path(td) / "state.json"),
            patch.object(control, "git_head", return_value="abc"),
            patch.object(control, "lock_active", return_value=False),
        ):
            bus = FakeBus()
            bus.fail_once = True
            c = control.Controller(self.config, bus)
            calls = []
            c.execute = lambda parsed: calls.append(parsed["command_id"]) or {"rc": 0, "output": "ok"}
            comment = command_comment()
            with self.assertRaises(control.GitHubTransientError):
                c.process_comment(comment)
            self.assertEqual(calls, ["cmd-1"])
            self.assertEqual(c.state["processed_comment_ids"]["100"]["phase"], "RESULT_PENDING")
            self.assertTrue(c.process_comment(comment))
            self.assertEqual(calls, ["cmd-1"])
            self.assertEqual(c.state["processed_comment_ids"]["100"]["phase"], "DONE")
            self.assertEqual(len(bus.posts), 1)

    def test_duplicate_command_id_on_new_comment_rejected(self):
        with (
            tempfile.TemporaryDirectory() as td,
            patch.object(control, "CONTROLLER_STATE", Path(td) / "state.json"),
            patch.object(control, "git_head", return_value="abc"),
            patch.object(control, "lock_active", return_value=False),
        ):
            bus = FakeBus()
            c = control.Controller(self.config, bus)
            c.execute = lambda parsed: {"rc": 0, "output": "ok"}
            self.assertTrue(c.process_comment(command_comment(comment_id=101, command_id="same")))
            self.assertFalse(c.process_comment(command_comment(comment_id=102, command_id="same")))

    def test_tail_logs_schema_bounds(self):
        control.command_schema("TAIL_LOGS", {"lines": 200})
        with self.assertRaises(control.CommandRejected):
            control.command_schema("TAIL_LOGS", {"lines": 201})


class PersistenceAndDeployContractTests(unittest.TestCase):
    def test_scheduled_task_contract_is_singleton_restartable(self):
        text = (Path(__file__).resolve().parents[1] / "scripts" / "atm-control-task.xml.template").read_text()
        self.assertIn("<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>", text)
        self.assertIn("<RestartOnFailure>", text)
        self.assertIn("<Count>999</Count>", text)

    def test_canonical_runner_has_no_reset_surface(self):
        text = (Path(__file__).resolve().parents[1] / "scripts" / "run-atm.ps1").read_text()
        self.assertIn("atm_v2.py", text)
        self.assertNotIn("[switch]$Reset", text)

    def test_cloudflare_deploy_is_exact_sha_smoked(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github" / "workflows" / "deploy-cloudflare.yml").read_text()
        wrangler = (root / "wrangler.toml").read_text()
        worker = (root / "worker" / "src" / "index.js").read_text()
        self.assertIn("ATM_GIT_SHA:${{ github.sha }}", workflow)
        self.assertIn("EXPECTED_SHA: ${{ github.sha }}", workflow)
        self.assertIn("atm.simondalmasso44.workers.dev/health", workflow)
        self.assertIn('name = "atm"', wrangler)
        self.assertIn("workers_dev = true", wrangler)
        self.assertNotIn("private_key", worker.lower())
        self.assertNotIn("api_token", worker.lower())


if __name__ == "__main__":
    unittest.main()
