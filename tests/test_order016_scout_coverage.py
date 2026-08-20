from __future__ import annotations

import unittest
from unittest.mock import patch

from atm_core.swarm_runtime import current_pass_swarm_shadow


class FakeGovernor:
    max_scouts = 3

    def validate(self):
        return None


class FakeBoard:
    def expire_stale(self):
        return None

    def stats(self):
        return {"eligible": 0}


class Order016ScoutCoverageTests(unittest.TestCase):
    def test_runtime_scans_every_configured_source_in_bounded_batches(self):
        seen = []
        batch_sizes = []

        def fake_scan(_fabric, adapters, discovery_order, minimum):
            batch_sizes.append(len(discovery_order))
            seen.extend(discovery_order)
            return ()

        adapters = {f"source-{index}": object() for index in range(8)}
        config = {"discovery_order": list(adapters), "min_reward_usd": 1}
        with patch("atm_core.swarm_runtime.ResourceGovernor", return_value=FakeGovernor()):
            with patch("atm_core.swarm_runtime.ScoutFabric.scan", new=fake_scan):
                result = current_pass_swarm_shadow(config, adapters, FakeBoard())
        self.assertEqual(seen, list(adapters))
        self.assertEqual(batch_sizes, [3, 3, 2])
        self.assertTrue(all(size <= 3 for size in batch_sizes))
        self.assertEqual(result["stats"]["scout_fabric"]["configured_sources_scanned"], 8)
        self.assertEqual(result["stats"]["scout_fabric"]["bounded_concurrency"], 3)


if __name__ == "__main__":
    unittest.main()
