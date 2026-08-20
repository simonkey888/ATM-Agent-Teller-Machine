from __future__ import annotations

import time
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from atm_core.taskmarket_cli import TASKMARKET_PACKAGE, TASKMARKET_PACKAGE_INTEGRITY
from atm_core.universal_radar import (
    AgentPolicy,
    ExecutorClass,
    FundingStatus,
    OperationalState,
    RadarRegistry,
    SourceState,
    UniversalOpportunity,
    UniversalRadar,
    qualify,
    source_hash,
    taskmarket_admission,
    route_executor,
)

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def admitted(**changes):
    raw = dict(
        source="market",
        external_id="1",
        canonical_url="https://example.test/tasks/1",
        title="Improve test coverage",
        description="bounded work",
        category="code",
        created_at=NOW - timedelta(hours=1),
        updated_at=NOW - timedelta(minutes=1),
        deadline=NOW + timedelta(hours=1),
        payout_gross=Decimal("10"),
        payout_currency="USDC",
        payout_usd=Decimal("10"),
        payout_net=Decimal("10"),
        funding_status=FundingStatus.VERIFIED,
        funding_proof={"escrow_tx": "0xabc"},
        eligibility="PUBLIC_AGENT",
        agent_policy=AgentPolicy.AGENT_ALLOWED,
        claim_method="API",
        submission_method="API",
        payment_rail="Base USDC",
        payout_latency="1h",
        withdrawal_path="canonical wallet",
        required_capabilities=["tests"],
        source_state_hash=source_hash({"id": 1}),
        source_state=SourceState.HEALTHY,
        source_checked_at=NOW,
        source_ttl_seconds=900,
        external_object_exists=True,
        external_status="OPEN",
        submission_window_open=True,
        stake_required=False,
        paid_action_required=False,
        current_worker_action_available=True,
        canonical_identity_eligible=True,
        credential_boundary_ready=True,
        already_submitted_by_atm=False,
    )
    raw.update(changes)
    return UniversalOpportunity(**raw)


class Source:
    name = "market"
    priority = 1

    def __init__(self, rows, delay=0):
        self.rows = rows
        self.delay = delay

    def discover(self, floor_usd):
        del floor_usd
        time.sleep(self.delay)
        return [row.model_copy(deep=True) for row in self.rows]


class Order013CanonTests(unittest.TestCase):
    def test_only_fully_proven_row_is_executable(self):
        row = qualify(admitted(), now=NOW)
        self.assertEqual(row.operational_state, OperationalState.EXECUTABLE)
        self.assertGreater(row.money_velocity_score, 0)

    def test_unknown_critical_facts_never_pass(self):
        cases = [
            ({"funding_status": FundingStatus.UNVERIFIED}, "UNVERIFIED_FUNDING"),
            ({"funding_proof": {}}, "UNVERIFIED_FUNDING"),
            ({"external_object_exists": None}, "REMOTE_404"),
            ({"external_status": "UNKNOWN"}, "CLOSED"),
            ({"submission_window_open": None}, "NO_CURRENT_WORKER_ACTION"),
            ({"canonical_identity_eligible": None}, "IDENTITY_NOT_READY"),
        ]
        for changes, reason in cases:
            with self.subTest(reason=reason):
                row = qualify(admitted(**changes), now=NOW)
                self.assertNotEqual(row.operational_state, OperationalState.EXECUTABLE)
                self.assertEqual(row.money_velocity_score, 0)
                self.assertIn(reason, row.rejection_reasons)

    def test_ttl_and_expiry_boundaries_are_exact(self):
        current = qualify(admitted(source_checked_at=NOW - timedelta(seconds=900)), now=NOW)
        self.assertEqual(current.operational_state, OperationalState.EXECUTABLE)
        stale = qualify(admitted(source_checked_at=NOW - timedelta(seconds=901)), now=NOW)
        self.assertEqual(stale.operational_state, OperationalState.STALE)
        expired = qualify(admitted(deadline=NOW), now=NOW)
        self.assertEqual(expired.operational_state, OperationalState.STALE)

    def test_spend_stake_action_and_duplicate_kills(self):
        cases = [
            ({"paid_action_required": True}, "PAID_ENTRY"),
            ({"execution_cost_usd": Decimal("0.01")}, "PAID_ENTRY"),
            ({"stake_required": True}, "STAKE_REQUIRED"),
            ({"current_worker_action_available": False}, "NO_CURRENT_WORKER_ACTION"),
            ({"already_submitted_by_atm": True}, "ALREADY_SUBMITTED"),
        ]
        for changes, reason in cases:
            row = qualify(admitted(**changes), now=NOW)
            self.assertNotEqual(row.operational_state, OperationalState.EXECUTABLE)
            self.assertIn(reason, row.rejection_reasons)

    def test_fixture_semantics_quarantined_but_title_test_is_not(self):
        real = qualify(admitted(title="test: improve coverage"), now=NOW)
        self.assertEqual(real.operational_state, OperationalState.EXECUTABLE)
        for changes in ({"source": "fixture"}, {"provenance_tags": ["MOCK"]}, {"funding_state": "SIGNAL_ONLY"}):
            row = qualify(admitted(**changes), now=NOW)
            self.assertEqual(row.operational_state, OperationalState.REJECTED_SLOP)

    def test_duplicate_is_counted_once_by_source_and_authoritative_id(self):
        a = admitted(source="market", external_id="same", payout_net=Decimal("10"))
        b = admitted(source="market", external_id="same", payout_net=Decimal("11"))
        snap = UniversalRadar(RadarRegistry([Source([a, b])]), per_source_timeout=1).scan()
        self.assertEqual(len(snap.opportunities), 1)
        self.assertEqual(snap.canonical_duplicate_count, 1)
        self.assertEqual(snap.rejection_counts["CANONICAL_DUPLICATE"], 1)

    def test_source_timeout_returns_without_waiting_for_hung_source(self):
        start = time.monotonic()
        snap = UniversalRadar(RadarRegistry([Source([], delay=1.2)]), per_source_timeout=1).scan()
        self.assertLess(time.monotonic() - start, 1.15)
        self.assertEqual(snap.source_errors["market"], "TIMEOUT")

    def test_taskmarket_first_party_admission_and_duplicate_preservation(self):
        task = {
            "id": "0x" + "12" * 32,
            "status": "open",
            "phase": "active",
            "mode": "bounty",
            "description": "Produce one source-backed Markdown report in one file with exact pass/fail acceptance criteria",
            "submissionCount": 0,
            "submissionWindowOpen": True,
            "stakeRequired": False,
            "escrowTxHash": "0xabc",
            "netReward": "5550000",
            "expiryTime": "2026-08-20T23:08:24.949Z",
            "pendingActions": [{"role": "worker", "action": "submit", "eligibleAddress": None, "requiresPayment": False, "paymentAmount": None}],
        }
        state, reasons = taskmarket_admission(task, canonical_wallet="0x"+"1"*40, existing_submission=False, signer_ready=True, now=NOW)
        self.assertEqual((state, reasons), (OperationalState.EXECUTABLE, ()))
        state, reasons = taskmarket_admission(task, canonical_wallet="0x"+"1"*40, existing_submission=True, signer_ready=True, now=NOW)
        self.assertEqual(state, OperationalState.WATCH_ONLY)
        self.assertEqual(reasons, ("ALREADY_SUBMITTED",))

    def test_signer_dependency_is_immutable(self):
        self.assertEqual(TASKMARKET_PACKAGE, "@lucid-agents/taskmarket@1.11.0")
        self.assertTrue(TASKMARKET_PACKAGE_INTEGRITY.startswith("sha512-"))
        self.assertNotIn("@latest", TASKMARKET_PACKAGE)

    def test_unproven_non_code_toolchains_remain_unsupported(self):
        for capability in (
            "document OCR extraction",
            "French translation with glossary",
            "raster brand design",
            "structured file transform and data normalization",
        ):
            row = admitted(required_capabilities=[capability])
            self.assertEqual(route_executor(row), ExecutorClass.UNSUPPORTED)


if __name__ == "__main__":
    unittest.main()
