from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from atm_cloud import (
    AuthorityManager,
    CasConflict,
    CloudSafetyError,
    CloudStateSession,
    COMMAND_RE,
    MemoryBackend,
    _restore_tar,
    sanitize_status,
)


class CloudMasterContractTests(unittest.TestCase):
    def test_fresh_runner_state_recovery_and_cas(self):
        backend = MemoryBackend()
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            p = Path(first)
            (p / "state.json").write_text('{"schema_version":2,"phase":"DISCOVER"}')
            a = CloudStateSession(backend, p, "a" * 40)
            a.restore()
            a.checkpoint()
            b = CloudStateSession(backend, Path(second), "a" * 40)
            manifest = b.restore()
            self.assertEqual(manifest["schema"], "ATM_CLOUD_STATE_V1")
            self.assertEqual((Path(second) / "state.json").read_text(), (p / "state.json").read_text())
            (p / "state.json").write_text('{"schema_version":2,"phase":"VERIFY"}')
            a.checkpoint()
            (Path(second) / "state.json").write_text('{"schema_version":2,"phase":"CLAIM"}')
            with self.assertRaises(CasConflict):
                b.checkpoint()

    def test_authority_cas_race_has_one_gha_winner(self):
        backend = MemoryBackend()
        out = []
        lock = threading.Lock()

        def acquire():
            try:
                lease = AuthorityManager(backend, "a" * 40).acquire_github(300)
            except CasConflict:
                lease = None
            with lock:
                out.append(lease)

        threads = [threading.Thread(target=acquire) for _ in range(12)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sum(x is not None for x in out), 1)

    def test_oci_running_fences_gha_and_terminated_allows_fallback(self):
        backend = MemoryBackend()
        manager = AuthorityManager(backend, "a" * 40)
        lease = manager.acquire_github()
        manager.release(lease)
        manager.promote_oci("ocid1.instance.test")
        self.assertIsNone(AuthorityManager(backend, "b" * 40, lambda _: "RUNNING").acquire_github())
        self.assertIsNotNone(AuthorityManager(backend, "b" * 40, lambda _: "TERMINATED").acquire_github())

    def test_corrupt_remote_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(CloudSafetyError):
                _restore_tar(b"not-a-tar", Path(d))

    def test_command_parser_is_whitelist_only(self):
        self.assertIsNotNone(COMMAND_RE.fullmatch("ATM_CMD_V1\nCOMMAND=STATUS\nCOMMAND_ID=cloud-1\nARGS={}"))
        self.assertIsNone(COMMAND_RE.fullmatch("ATM_CMD_V1\nCOMMAND=SHELL\nCOMMAND_ID=cloud-2\nARGS={}"))

    def test_dashboard_status_sanitizer(self):
        self.assertNotIn("ignored", sanitize_status({"authority": "GITHUB_ACTIONS", "ignored": "x"}))
        with self.assertRaises(CloudSafetyError):
            sanitize_status({"active_task": {"authorization": "Bearer secret"}})

    def test_cloud_workflow_has_zero_spend_runner_and_no_windows(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/atm-cloud-cycle.yml").read_text()
        scheduler = (root / ".github/workflows/atm-cloud-schedule.yml").read_text()
        self.assertIn("workflow_call:", workflow)
        self.assertIn("runs-on: ubuntu-latest", workflow)
        self.assertNotIn("windows-", workflow.lower())
        self.assertNotIn("self-hosted", workflow.lower())
        self.assertNotIn("larger", workflow.lower())
        self.assertIn("group: atm-economic-authority", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertNotIn("schedule:", scheduler)
        self.assertIn("workflow_dispatch:", scheduler)
        self.assertIn("ATM_LEGACY_GITHUB_CRON=DISABLED", scheduler)
        self.assertNotIn("uses: ./.github/workflows/atm-cloud-cycle.yml", scheduler)
        wrangler = (root / "wrangler.toml").read_text()
        self.assertIn('crons = ["*/5 * * * *"]', wrangler)
        self.assertIn("expected_sha:", workflow)
        self.assertIn("CLOUDFLARE_CRON_V1", workflow)
        deploy = (root / ".github/workflows/deploy-cloudflare.yml").read_text()
        self.assertIn("actions: write", deploy)
        self.assertIn('base + "/disable"', deploy)
        self.assertIn('state != "disabled_manually"', deploy)
        self.assertIn("ATM_LEGACY_GITHUB_CRON=DISABLED_REMOTE", deploy)
        self.assertIn("len(parts) != 3", deploy)
