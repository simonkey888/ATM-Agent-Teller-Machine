from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from atm_core.lease_bound_actuator import LeaseExpiredDuringExecution, _lease_remaining_seconds, _run_bound
from atm_core.workers import WorkLease


class Order006R4LeaseTests(unittest.TestCase):
    def _lease(self, seconds: float) -> WorkLease:
        now = datetime.now(timezone.utc)
        return WorkLease(
            lease_id="order006-r4-unit-lease",
            canonical_opportunity_id="fixture:order006-r4-unit",
            worker_id="zungun",
            scope_hash="a" * 64,
            acquired_at=now - timedelta(seconds=1),
            expires_at=now + timedelta(seconds=seconds),
            heartbeat_at=now,
            terminal_state=None,
        )

    def test_remaining_runtime_never_exceeds_real_lease(self):
        lease = self._lease(3)
        remaining = _lease_remaining_seconds(lease, 99)
        self.assertGreater(remaining, 0)
        self.assertLessEqual(remaining, 3)

    def test_slow_subprocess_is_killed_at_lease_expiry(self):
        lease = self._lease(0.8)
        with tempfile.TemporaryDirectory() as directory:
            started = time.monotonic()
            with self.assertRaises(LeaseExpiredDuringExecution):
                _run_bound(
                    [sys.executable, "-c", "import time; time.sleep(5)"],
                    Path(directory),
                    dict(os.environ),
                    lease,
                    30,
                )
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 3)

    def test_already_expired_lease_never_starts_budget(self):
        lease = self._lease(-1)
        with self.assertRaises(LeaseExpiredDuringExecution):
            _lease_remaining_seconds(lease, 30)


if __name__ == "__main__":
    unittest.main()
