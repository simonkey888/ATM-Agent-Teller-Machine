from __future__ import annotations

import unittest
from unittest.mock import patch

from atm_core.swarm_runtime import current_pass_swarm_shadow


class FakeGovernor:
    max_scouts = 3
    def validate(self): return None


class FakeBoard:
    def __init__(self): self.rows = {}
    def expire_stale(self): return None
    def stats(self): return {"eligible": 0}
    def get(self, cid): return self.rows.get(cid)
    def upsert_candidate(self, payload, **kwargs):
        row = {"canonical_id": payload["canonical_opportunity_id"], "status": kwargs.get("status", "NORMALIZED"), "signal": 0, "touched_at": ""}
        self.rows[payload["canonical_opportunity_id"]] = row
        return row


class Order016ScoutCoverageTests(unittest.TestCase):
    def test_runtime_scans_every_configured_source_with_max_three_concurrent_children(self):
        seen = []
        probes = []
        def fake_run(_runner, role, operation, payload, **kwargs):
            if role == "SCOUT":
                sources = list(payload["sources"])
                self.assertEqual(len(sources), 1)
                seen.extend(sources)
                return {"observations": [{"source": sources[0], "state": "OK", "error_class": None, "candidates": []}]}
            if (role, operation) == ("FALSIFIER", "BOUNDARY_PROBE"):
                probes.append("FALSIFIER")
                return {"state": "BOUNDARY_READY"}
            if (role, operation) == ("DOCTOR", "INSPECT"):
                probes.append("DOCTOR")
                return {"state": "BOUNDARY_READY", "summary": {}}
            raise AssertionError((role, operation))
        adapters = {f"source-{index}": object() for index in range(8)}
        config = {"discovery_order": list(adapters), "min_reward_usd": 1}
        board = FakeBoard()
        with patch("atm_core.swarm_runtime.ResourceGovernor", return_value=FakeGovernor()):
            with patch("atm_core.swarm_runtime.IsolatedRoleRunner.run", new=fake_run):
                with patch("atm_core.swarm_runtime.canonical_non_actionable_ids", return_value=set()):
                    result = current_pass_swarm_shadow(config, adapters, board)
        self.assertEqual(sorted(seen), sorted(adapters))
        self.assertEqual(sorted(probes), ["DOCTOR", "FALSIFIER"])
        self.assertEqual(result["stats"]["scout_fabric"]["configured_sources_scanned"], 8)
        self.assertEqual(result["stats"]["scout_fabric"]["bounded_concurrency"], 3)
        self.assertEqual(result["stats"]["scout_fabric"]["process_boundary"], "RECEIPT_REQUIRED")


if __name__ == "__main__": unittest.main()
