from __future__ import annotations

import importlib.util
import sys
import unittest
from decimal import Decimal
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "src" / "atm.py"
spec = importlib.util.spec_from_file_location("atm_live", MODULE)
atm = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = atm
spec.loader.exec_module(atm)

from atm_core.opportunities import TaskmarketOpportunityAdapter, WorkProtocolOpportunityAdapter  # noqa: E402


class LiveDiscoveryContractTests(unittest.TestCase):
    def _verified_live(self, adapter):
        try:
            found = adapter.discover(Decimal("1"))
        except Exception as exc:
            return [], [], [f"{adapter.__class__.__name__}: {type(exc).__name__}: {exc}"]
        verified = []
        errors: list[str] = []
        for opportunity in found[:20]:
            try:
                snapshot = adapter.fetch_authoritative(opportunity)
                adapter.verify_freshness(opportunity, snapshot)
                adapter.verify_funding(opportunity, snapshot)
                adapter.verify_eligibility(opportunity, snapshot)
                competition = adapter.inspect_competition(opportunity, snapshot)
                opportunity.competition = max(competition.values() or [0])
                opportunity.claims = int(competition.get("claims", opportunity.claims))
                opportunity.open_prs = int(competition.get("open_prs", opportunity.open_prs))
                verified.append(opportunity)
            except Exception as exc:
                errors.append(f"{opportunity.canonical_opportunity_id}: {exc}")
        return found, verified, errors

    def test_workprotocol_live_discovery_has_open_explicitly_funded_code_work(self):
        found, verified, errors = self._verified_live(WorkProtocolOpportunityAdapter())
        if not found and errors:
            self.skipTest("WorkProtocol unavailable; source isolated: " + " | ".join(errors[-2:]))
        self.assertTrue(found, "WorkProtocol returned no current code opportunities")
        if not verified and errors and all(
            "escrow contract is not configured for authoritative binding" in error for error in errors
        ):
            self.skipTest("WorkProtocol visible but strict chain binding is not configured; source is non-executable")
        self.assertTrue(verified, "no fresh funded WorkProtocol opportunity: " + " | ".join(errors[-5:]))

    def test_taskmarket_live_discovery_has_open_funded_zero_stake_work(self):
        found, verified, errors = self._verified_live(TaskmarketOpportunityAdapter())
        self.assertTrue(found, "Taskmarket returned no current zero-upfront opportunities")
        self.assertTrue(verified, "no fresh funded Taskmarket opportunity: " + " | ".join(errors[-5:]))

    def test_supervisor_selects_live_opportunity_by_realized_ev_per_effort(self):
        candidates = []
        diagnostics = []
        for adapter in (WorkProtocolOpportunityAdapter(), TaskmarketOpportunityAdapter()):
            _, verified, errors = self._verified_live(adapter)
            candidates.extend(verified)
            diagnostics.extend(errors)
        self.assertTrue(candidates, "no verified live paid candidates: " + " | ".join(diagnostics[-8:]))
        selected = atm.choose_opportunity(candidates, max_competition=8)
        self.assertIsNotNone(selected)
        print(
            "LIVE_EV_SELECTION "
            f"id={selected.canonical_opportunity_id} source={selected.source} "
            f"reward_gross={selected.reward_gross} ev_realized={selected.ev_realized} "
            f"ev_per_effort_hour={selected.ev_per_effort_hour} competition={selected.competition}"
        )


if __name__ == "__main__":
    unittest.main()
