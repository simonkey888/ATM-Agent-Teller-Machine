from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

oci = importlib.import_module("atm_control_oci") if os.name != "nt" else None


@unittest.skipIf(os.name == "nt", "OCI controller is Linux-only")
class OciCommandBusTests(unittest.TestCase):
    def test_targeted_oci_status_is_accepted(self):
        parsed = oci.parse_command('ATM_CMD_V1\nCOMMAND=STATUS\nCOMMAND_ID=oci-1\nARGS={"target_host":"OCI"}')
        self.assertEqual(parsed["command"], "STATUS")
        self.assertEqual(parsed["args"], {})

    def test_target_mismatch_is_fail_closed(self):
        with self.assertRaises(oci.CommandRejected):
            oci.parse_command('ATM_CMD_V1\nCOMMAND=STATUS\nCOMMAND_ID=win-1\nARGS={"target_host":"WINDOWS"}')

    def test_no_arbitrary_shell_surface(self):
        with self.assertRaises(oci.CommandRejected):
            oci.parse_command("ATM_CMD_V1\nCOMMAND=SHELL\nCOMMAND_ID=x\nARGS={}")

    def test_github_bus_prefers_environment_token(self):
        cfg = {"repository":"simonkey888/ATM-Agent-Teller-Machine","owner_login":"simonkey888","control_issue":4,"observatory_issue":7}
        with patch.dict(os.environ, {"GITHUB_TOKEN":"secret-from-env"}, clear=False):
            bus = oci.GitHubBus(cfg)
        self.assertEqual(bus.token, "secret-from-env")

    def test_result_identifies_oci_and_exact_sha(self):
        cfg = {"owner_login":"simonkey888","poll_seconds":30,"last_seen_comment_id":0}
        with tempfile.TemporaryDirectory() as td, patch.object(oci, "STATE_FILE", Path(td)/"state.json"), patch.object(oci, "SOURCE_SHA", "a"*40), patch.object(oci, "atm_lock_summary", return_value={"active":False}):
            controller = oci.Controller(cfg, object())
            body = controller.result_body({"command_id":"x","comment_id":1}, "s", "e", {"rc":0,"output":"ok"})
        self.assertIn("HOST_CLASS=OCI", body)
        self.assertIn("SOURCE_SHA=" + "a"*40, body)


class OciStaticContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.bootstrap = (cls.root / "scripts" / "bootstrap-oci-atm.sh").read_text()
        cls.remote_bootstrap = (cls.root / "scripts" / "bootstrap-oci-atm-api.sh").read_text()
        cls.provision = (cls.root / "scripts" / "oci-provision.sh").read_text()
        cls.configure = (cls.root / "scripts" / "oci-configure.sh").read_text()
        cls.cloudinit = (cls.root / "deploy" / "oci" / "cloud-init.sh").read_text()
        cls.backup = (cls.root / "scripts" / "atm-state-backup.sh").read_text()
        cls.authority = (cls.root / "scripts" / "oci-authority.py").read_text()
        cls.fence = (cls.root / "scripts" / "atm-authority-fence.py").read_text()
        cls.controller_unit = (cls.root / "deploy" / "oci" / "atm-controller.service").read_text()
        cls.supervisor_unit = (cls.root / "deploy" / "oci" / "atm-supervisor.service").read_text()
        cls.worker = (cls.root / "worker" / "src" / "index.js").read_text()
        cls.oci_runtime = (cls.root / "src" / "atm_v2_oci.py").read_text()
        cls.all_boot = "\n".join([cls.bootstrap, cls.remote_bootstrap, cls.provision, cls.configure, cls.cloudinit])

    def test_bootstrap_is_exact_sha_and_cloud_shell_only(self):
        self.assertIn("instance_obo_user", self.bootstrap)
        self.assertIn("ATM_SOURCE_SHA_MUST_BE_EXACT_40HEX", self.bootstrap)
        self.assertIn('RAW="https://raw.githubusercontent.com/$REPO/$SHA"', self.bootstrap)
        self.assertIn('git -C /opt/atm fetch --depth 1 origin "$SHA"', self.cloudinit)
        self.assertNotIn("git reset --hard", self.all_boot)
        self.assertNotIn("git checkout main", self.all_boot)

    def test_free_tier_has_conservative_hard_caps_and_no_paid_fallback(self):
        self.assertIn("SHAPE=VM.Standard.A1.Flex", self.provision)
        self.assertIn("c+1<=2 and m+6<=12 and v+50<=200", self.provision)
        self.assertIn('GOOD_ADS=("${ADS[@]}")', self.provision)
        self.assertIn("authority=ACTUAL_A1_LAUNCH", self.provision)
        self.assertIn("A1_SERVICE_LIMIT_EXCEEDED", self.provision)
        self.assertIn("A1_CAPACITY_UNAVAILABLE_ALL_ADS", self.provision)
        self.assertNotIn("query_a1_limit", self.provision)
        self.assertIn("OBJECT_FREE_LIMIT=$((20 * 1024 * 1024 * 1024))", self.provision)
        self.assertIn("list-object-versions", self.provision)
        self.assertIn("ALWAYS_FREE_OBJECT_STORAGE_HEADROOM_EXCEEDED", self.provision)
        self.assertNotIn("VM.Standard.E", self.all_boot)
        self.assertNotIn("Pay As You Go", self.all_boot)

    def test_inventory_spans_accessible_compartments(self):
        self.assertIn("--compartment-id-in-subtree true --access-level ACCESSIBLE", self.provision)
        self.assertIn('for c in "${COMPS[@]}"', self.provision)
        self.assertIn('for r in "${SUBSCRIBED_REGIONS[@]}"', self.provision)

    def test_empty_compute_inventory_is_reduced_to_zero_zero(self):
        self.assertIn('(.data // []) as $rows', self.provision)
        self.assertIn('map(.ocpus // 0)', self.provision)
        self.assertIn('map(."memory-in-gbs" // 0)', self.provision)
        self.assertIn('COMPUTE_INVENTORY_AMBIGUOUS', self.provision)
        self.assertIn('BLOCK_VOLUME_INVENTORY_AMBIGUOUS', self.provision)

    def test_private_key_and_runtime_secrets_are_not_printed_or_committed(self):
        self.assertIn('chmod 600 "$KEY"', self.provision)
        self.assertNotIn('cat "$KEY"', self.all_boot)
        self.assertNotIn("OCI_PRIVATE_KEY", self.all_boot)
        self.assertIn("read -rsp 'GitHub classic PAT with public_repo scope", self.configure)
        self.assertIn("read -rsp 'GOOGLE_API_KEY", self.configure)
        self.assertIn("chmod 600 /etc/atm/control.env", self.configure)
        self.assertIn("chmod 640 /etc/atm/runtime.env", self.configure)
        self.assertIn("chmod 640 /etc/atm/authority.env", self.configure)

    def test_canonical_payout_address_is_environment_bound_and_public_only(self):
        self.assertIn('PAYOUT="${ATM_BASE_WALLET_ADDRESS:-}"', self.bootstrap)
        self.assertIn("ATM_BASE_WALLET_ADDRESS_MUST_BE_CANONICAL_PUBLIC_BASE_ADDRESS", self.bootstrap)
        self.assertIn('PAYOUT="${ATM_BASE_WALLET_ADDRESS:-}"', self.configure)
        self.assertIn('d["payment_recipient_public_identifier"]', self.configure)
        self.assertNotIn("Public Base-compatible payout address", self.configure)
        self.assertNotIn("seed phrase", self.all_boot.lower())
        self.assertNotIn("private key", self.configure.lower())

    def test_systemd_separates_controller_and_supervisor_with_authority_fence(self):
        self.assertIn("ExecStart=/opt/atm/.venv/bin/python /opt/atm/src/atm_control_oci_fenced.py", self.controller_unit)
        self.assertIn("Restart=always", self.controller_unit)
        self.assertIn("ExecStartPre=/opt/atm/.venv/bin/python /opt/atm/scripts/atm-authority-fence.py", self.supervisor_unit)
        self.assertIn("ExecStart=/opt/atm/.venv/bin/python /opt/atm/src/atm_v2_oci.py", self.supervisor_unit)
        self.assertIn("Restart=on-failure", self.supervisor_unit)
        self.assertIn("User=atm", self.supervisor_unit)

    def test_pause_keeps_safe_monitor_payment_lanes(self):
        self.assertIn("Phase.MONITOR, Phase.PAYMENT_VERIFY", self.oci_runtime)
        self.assertIn("PAUSED_SAFE_MONITOR_ONLY", self.oci_runtime)
        self.assertIn("refresh_discovery_cache", self.oci_runtime)

    def test_backup_is_manifested_cloud_state_and_excludes_secrets_logs_workspaces(self):
        self.assertIn("ATM_CLOUD_STATE_V1", self.backup)
        self.assertIn("manifest.json", self.backup)
        self.assertIn("sha256", self.backup)
        self.assertIn("validated-payment-proofs.jsonl", self.backup)
        self.assertIn("money-board.sqlite3", self.backup)
        self.assertIn("cloud-control.json", self.backup)
        self.assertNotIn("controller-state-oci.json", self.backup)
        self.assertNotIn("secrets.env", self.backup)
        self.assertNotIn("workspaces", self.backup)
        self.assertNotIn("logs/", self.backup)

    def test_cloud_state_object_is_required_before_oci_promotion(self):
        self.assertIn("atm-cloud-state.tgz", self.configure)
        self.assertIn("atm-authority.json", self.configure)
        self.assertIn("CLOUD_BASE_STATE_OBJECT_MISSING", self.configure)
        self.assertIn("CLOUD_BASE_AUTHORITY_OBJECT_MISSING", self.configure)
        restore_pos = self.configure.index("atm-state-backup.sh restore")
        promote_pos = self.configure.index("oci-authority.py")
        self.assertLess(restore_pos, promote_pos)

    def test_ssh_rule_is_break_glass_only_and_removed(self):
        self.assertIn("destinationPortRange", self.provision)
        self.assertIn("22", self.provision)
        self.assertIn("--ingress-security-rules '[]'", self.configure)
        self.assertNotIn("9222", self.all_boot)
        self.assertNotIn("8000", self.all_boot)

    def test_cloud_only_cutover_uses_cas_fencing_and_no_windows(self):
        upper = self.configure.upper()
        self.assertNotIn("WINDOWS", upper)
        self.assertIn('promote --instance-id "$INSTANCE_ID" --source-sha "$SHA"', self.configure)
        self.assertIn("AUTHORITY_FENCE_REMOTE", self.configure)
        self.assertIn("GITHUB_ACTIONS_LEASE_STILL_LIVE", self.authority)
        self.assertIn("ROLLBACK_REFUSED_INSTANCE_NOT_PROVEN_STOPPED", self.authority)
        start_id = 'ID="master-oci-start-$SHA"'
        self.assertLess(self.configure.index("AUTHORITY_FENCE_REMOTE"), self.configure.index(start_id))

    def test_stopped_canonical_a1_is_reused_not_duplicated(self):
        self.assertIn("OCI_CANONICAL_A1=RESTARTING_FROM_SAFE_ROLLBACK", self.remote_bootstrap)
        self.assertIn("--action START --wait-for-state RUNNING", self.remote_bootstrap)

    def test_observatory_is_sanitized_read_only(self):
        self.assertIn("const OBS_ISSUE = 7", self.worker)
        self.assertIn("SAFE_KEYS", self.worker)
        for forbidden in ("private_key","seed_phrase","GITHUB_TOKEN","GOOGLE_API_KEY"):
            self.assertNotIn(forbidden, self.worker)
        self.assertNotIn('method:"POST"', self.worker)


if __name__ == "__main__":
    unittest.main()
