from __future__ import annotations

import os
import unittest
from decimal import Decimal

from atm_core.models import Opportunity, Phase, RuntimeState
from atm_core.work_conserving import (
    MUTATION_SECRETS,
    detach_external_wait,
    isolated_lane_environment,
    next_delay_seconds,
    watch_generic_in_flight,
)


class _Ledger:
    def append(self, proof):
        return True


class _Adapter:
    def monitor(self, opp):
        return {"status": "accepted"}

    def fetch_payment(self, opp, recipient):
        return []


class WorkConservingRuntimeTests(unittest.TestCase):
    def _opp(self, source: str = "workprotocol") -> Opportunity:
        return Opportunity(
            canonical_opportunity_id=f"{source}:1",
            source=source,
            authoritative_url="https://example.com/jobs/1",
            upstream_status="OPEN",
            reward_gross=Decimal("25"),
            deliverable_url="https://example.com/deliverable/1",
            submission_id="submission-1",
        )

    def test_untrusted_lane_cannot_see_mutation_secrets(self):
        old = {name: os.environ.get(name) for name in MUTATION_SECRETS}
        try:
            for name in MUTATION_SECRETS:
                os.environ[name] = "sentinel-secret"
            with isolated_lane_environment("MAKERS"):
                self.assertTrue(all(name not in os.environ for name in MUTATION_SECRETS))
            self.assertTrue(all(os.environ.get(name) == "sentinel-secret" for name in MUTATION_SECRETS))
        finally:
            for name, value in old.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_submit_monitor_wait_is_detached_and_capacity_released(self):
        state = RuntimeState(phase=Phase.MONITOR, active_opportunity=self._opp())
        changed = detach_external_wait(state, Phase.SUBMIT)
        self.assertTrue(changed)
        self.assertIsNone(state.active_opportunity)
        self.assertEqual(state.phase, Phase.DISCOVER)
        self.assertEqual(len(state.in_flight), 1)
        self.assertEqual(getattr(state.in_flight[0], "inflight_stage"), "SUBMITTED")
        self.assertEqual(state.last_result["status"], "SUBMITTED_NONBLOCKING")

    def test_generic_watcher_advances_acceptance_without_blocking(self):
        opp = self._opp("other")
        opp = opp.model_copy(update={"inflight_stage": "SUBMITTED", "inflight_terminal": False})
        state = RuntimeState(phase=Phase.DISCOVER, in_flight=[opp])
        config = {"payment_recipient_public_identifier": "0xd89Ef03bC3105C538529AC2657Bc4488c94ff4E4"}
        checked = watch_generic_in_flight(
            config,
            state,
            {"other": _Adapter()},
            _Ledger(),
            lambda adapters, opportunity: adapters[opportunity.source],
        )
        self.assertEqual(checked, 1)
        self.assertEqual(getattr(state.in_flight[0], "inflight_stage"), "ACCEPTED")
        self.assertFalse(bool(getattr(state.in_flight[0], "inflight_terminal", False)))
        self.assertEqual(state.phase, Phase.DISCOVER)

    def test_delay_is_work_conserving_but_never_busy_loops(self):
        config = {
            "loop": {
                "work_conserving_min_sleep_seconds": 1,
                "active_work_poll_seconds": 1,
                "watcher_poll_seconds": 15,
                "idle_discovery_poll_seconds": 30,
                "phase_delay_seconds": {
                    "DISCOVER": 300,
                    "VERIFY": 2,
                    "WORK": 2,
                    "MONITOR": 300,
                },
            }
        }
        active = RuntimeState(phase=Phase.VERIFY, active_opportunity=self._opp())
        self.assertEqual(next_delay_seconds(config, active), 1)

        inflight = self._opp().model_copy(update={"inflight_terminal": False})
        watching = RuntimeState(phase=Phase.DISCOVER, in_flight=[inflight])
        self.assertEqual(next_delay_seconds(config, watching), 15)

        idle = RuntimeState(phase=Phase.DISCOVER)
        self.assertEqual(next_delay_seconds(config, idle), 30)
        self.assertGreaterEqual(next_delay_seconds(config, idle), 1)


if __name__ == "__main__":
    unittest.main()
