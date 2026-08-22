from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from atm_core import role_runtime
from atm_core.sandbox_boundary_v2 import SandboxError, SandboxLimitExceeded, SandboxPolicy, StructuralSandbox


@unittest.skipUnless(sys.platform.startswith("linux"), "canonical economic resource-kill lane is Linux/Docker")
class Order018ResourceBombTests(unittest.TestCase):
    def _limits(self, **overrides):
        values = dict(cpu_seconds=2, memory_mb=128, max_file_bytes=65536, max_open_files=32, max_processes=256, wall_seconds=4)
        values.update(overrides)
        return role_runtime.SandboxLimits(**values)

    def _run(self, mode: str, *, writable: bool = False, network: bool = False, **limit_overrides):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / ("out.bin" if mode == "FILE" else "out")
            policy = SandboxPolicy(
                network_policy="READ_ONLY_PUBLIC_HTTPS" if network else "DENY",
                tool_policy="ORDER018_RESOURCE_KILL",
                limits=self._limits(**limit_overrides),
                allowed_hosts=("example.com",) if network else (),
            )
            return StructuralSandbox().run(
                worker_name="atm_sandbox_resource_probe.py",
                request={"mode": mode, "target": str(target)},
                policy=policy,
                writable_paths=(target,) if writable else (),
            )

    def assertKilled(self, mode: str, **kwargs):
        with self.assertRaises((SandboxError, SandboxLimitExceeded), msg=f"{mode} must be killed"):
            self._run(mode, **kwargs)

    def test_cpu_bomb_is_killed(self): self.assertKilled("CPU", cpu_seconds=1)
    def test_memory_bomb_is_killed(self): self.assertKilled("MEMORY", memory_mb=128)
    def test_fork_bomb_is_killed(self): self.assertKilled("PIDS")
    def test_single_file_bomb_is_killed(self): self.assertKilled("FILE", writable=True, max_file_bytes=65536)
    def test_fd_bomb_is_killed(self): self.assertKilled("FD", writable=True, max_open_files=32)
    def test_wall_time_bomb_is_killed(self): self.assertKilled("WALL", wall_seconds=2)
    def test_aggregate_multifile_output_is_killed_before_writeback(self): self.assertKilled("FILES", writable=True, max_file_bytes=65536, max_open_files=32)
    def test_child_forged_broker_response_cannot_be_authoritative(self): self.assertKilled("FORGE_BROKER", network=True)


if __name__ == "__main__":
    unittest.main()
