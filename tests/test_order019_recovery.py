from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from atm_core.models import Opportunity
from atm_core.money_velocity import has_funding_evidence, reject_reason
from atm_core.opportunities import OpportunityValidationError, TaskmarketOpportunityAdapter
from atm_core.order019_recovery import (
    MAX_SAFE_MAKER_WORDS,
    capability_reason,
    choose_swarm_candidate,
    mandatory_core_ambiguous,
)
from atm_core.taskmarket_maker import TaskmarketMakerError, TaskmarketZeroCostMaker
from atm_core.universal_radar import (
    AgentPolicy,
    FundingStatus,
    OperationalState,
    SourceState,
    UniversalOpportunity,
    qualify,
)


class _NoSignerLane:
    def signer_present(self):
        return False


class Order019RecoveryTests(unittest.TestCase):
    def test_unknown_mutation_facts_are_typed_not_false_positive_claims(self):
        now = datetime.now(timezone.utc)
        opportunity = UniversalOpportunity(
            source="taskmarket",
            external_id="x",
            canonical_url="https://taskmarket.dev/tasks/x",
            title="research brief",
            description="research a public technical fact",
            category="research",
            payout_gross=Decimal("10"),
            payout_net=Decimal("10"),
            funding_status=FundingStatus.VERIFIED,
            funding_proof={"escrow_tx_hash": "0xabc"},
            agent_policy=AgentPolicy.AGENT_ALLOWED,
            payment_rail="USDC_BASE",
            withdrawal_path="CANONICAL_BASE_WALLET",
            source_state_hash="abc",
            source_state=SourceState.HEALTHY,
            source_checked_at=now,
            external_object_exists=None,
            external_status="UNKNOWN",
            submission_window_open=None,
            stake_required=None,
            paid_action_required=None,
            current_worker_action_available=None,
            canonical_identity_eligible=None,
            credential_boundary_ready=None,
            already_submitted_by_atm=None,
        )
        result = qualify(opportunity, now=now)
        reasons = set(result.rejection_reasons)
        self.assertEqual(result.operational_state, OperationalState.UNVERIFIED)
        self.assertEqual(result.money_velocity_score, Decimal("0"))
        for false_positive in (
            "REMOTE_404", "CLOSED", "PAID_ENTRY", "STAKE_REQUIRED",
            "NO_CURRENT_WORKER_ACTION", "IDENTITY_NOT_READY", "ALREADY_SUBMITTED",
        ):
            self.assertNotIn(false_positive, reasons)
        for typed_unknown in (
            "REMOTE_EXISTENCE_UNKNOWN", "EXTERNAL_STATUS_UNKNOWN", "ACTION_COST_UNKNOWN",
            "STAKE_STATUS_UNKNOWN", "SUBMISSION_WINDOW_UNKNOWN", "WORKER_ACTION_UNKNOWN",
            "IDENTITY_ELIGIBILITY_UNKNOWN", "CREDENTIAL_BOUNDARY_UNKNOWN", "SUBMISSION_HISTORY_UNKNOWN",
        ):
            self.assertIn(typed_unknown, reasons)

    def test_taskmarket_escrow_key_is_funding_and_competition_is_not_precanon_veto(self):
        now = datetime.now(timezone.utc)
        opportunity = Opportunity(
            canonical_opportunity_id="taskmarket:t",
            source="taskmarket",
            authoritative_url="https://taskmarket.dev/tasks/t",
            upstream_status="open",
            updated_at=now,
            deadline=now + timedelta(days=1),
            reward_gross=Decimal("20"),
            expected_fees=Decimal("1.5"),
            funding_proof={"escrow": "0xabc"},
            competition=12,
            open_prs=12,
            eligibility="PUBLIC_AGENT_TRUSTED_SIGNER_LANE",
            payment_method="USDC Base award settlement",
            expected_agent_hours=Decimal("1"),
            payout_latency_hours=Decimal("1"),
        )
        self.assertTrue(has_funding_evidence(opportunity))
        self.assertIsNone(reject_reason(opportunity, now=now))

    def test_optional_creative_freedom_is_not_mandatory_core_ambiguity(self):
        objective = (
            "Acceptance: exact required columns id,name,value and exactly 3 rows. "
            "The response is creatively open outside the mandatory core."
        )
        subjective = "Acceptance requires the reviewer to choose the strongest and best one."
        self.assertFalse(mandatory_core_ambiguous(objective))
        self.assertTrue(mandatory_core_ambiguous(subjective))

    def test_csv_single_file_support_and_deterministic_structure_check(self):
        description = (
            "Return one CSV file named result.csv. Required columns: id,name,value. "
            "Exactly 2 data rows. Must include \"alpha\"."
        )
        self.assertTrue(TaskmarketZeroCostMaker.supported_task(description))
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "result.csv"
            path.write_text("id,name,value\n1,alpha,10\n2,beta,20\n", encoding="utf-8")
            TaskmarketZeroCostMaker._static_check(path, description)
            path.write_text("name,id,value\nalpha,1,10\nbeta,2,20\n", encoding="utf-8")
            with self.assertRaises(TaskmarketMakerError):
                TaskmarketZeroCostMaker._static_check(path, description)

    def test_long_form_requirement_has_typed_capability_block(self):
        description = f"Return one markdown file with minimum {MAX_SAFE_MAKER_WORDS + 1} words."
        self.assertEqual(capability_reason(description), "CAPABILITY_WORD_BUDGET_NOT_READY")
        self.assertFalse(TaskmarketZeroCostMaker.supported_task(description))

    def test_money_board_miss_has_one_explicit_ledger_entry_per_candidate(self):
        selected, ledger = choose_swarm_candidate(
            [],
            ["taskmarket:a", "taskmarket:b", "taskmarket:c"],
            adapters={},
            config={"min_reward_usd": 5},
            hard_cap=Decimal("3"),
        )
        self.assertIsNone(selected)
        self.assertEqual([row["canonical_id"] for row in ledger], ["taskmarket:a", "taskmarket:b", "taskmarket:c"])
        self.assertTrue(all(row["result"] == "REJECTED" for row in ledger))
        self.assertTrue(all(row["reason"] == "SOURCE_ADAPTER_MISSING:taskmarket" for row in ledger))

    def test_absent_existing_signer_is_typed_before_canon(self):
        adapter = TaskmarketOpportunityAdapter(lane=_NoSignerLane())
        opportunity = Opportunity(
            canonical_opportunity_id="taskmarket:t",
            source="taskmarket",
            authoritative_url="https://taskmarket.dev/tasks/t",
            upstream_status="open",
            reward_gross=Decimal("10"),
            funding_proof={"escrow": "0xabc"},
            payment_method="USDC Base award settlement",
        )
        with self.assertRaisesRegex(OpportunityValidationError, "TASKMARKET_SIGNER_SECRET_ABSENT"):
            adapter.canonical_admission(opportunity, {})

    def test_cloud_cycle_reuses_existing_environment_keystore_secret(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / ".github" / "workflows" / "atm-cloud-cycle.yml").read_text(encoding="utf-8")
        self.assertIn("environment: atm-production", text)
        self.assertIn("TASKMARKET_KEYSTORE_B64: ${{ secrets.TASKMARKET_KEYSTORE_B64 }}", text)
        self.assertIn("materialize_supervisor_keystore", text)
        self.assertIn("TASKMARKET_SIGNER_SECRET_ABSENT", text)


if __name__ == "__main__":
    unittest.main()
