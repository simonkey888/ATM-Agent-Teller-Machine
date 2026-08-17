from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


@unittest.skipIf(os.name == "nt", "OCI controller is Linux-only")
class OciControlRecoveryTests(unittest.TestCase):
    def test_executing_record_becomes_indeterminate_without_replay(self):
        import atm_control_oci_entry as entry

        class Bus:
            posts = []
            def __init__(self, cfg):
                self.cfg = cfg
            def post_result(self, body):
                self.posts.append(body)
                return 999

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state_path = root / "state.json"
            cfg_path = root / "control.json"
            lock_path = root / "controller.lock"
            cfg_path.write_text(json.dumps({"repository":"simonkey888/ATM-Agent-Teller-Machine","owner_login":"simonkey888","control_issue":4,"observatory_issue":7}))
            state_path.write_text(json.dumps({
                "processed_comments": {"123": {"phase":"EXECUTING","body_hash":"h","command_id":"cmd-1","started_at":"start"}},
                "processed_commands": {"cmd-1": {"phase":"EXECUTING","body_hash":"h","command_id":"cmd-1","started_at":"start"}},
            }))
            Bus.posts = []
            with patch.object(entry.ctl, "CONFIG_PATH", cfg_path), patch.object(entry.ctl, "STATE_FILE", state_path), patch.object(entry.ctl, "LOCK", lock_path), patch.object(entry.ctl, "GitHubBus", Bus), patch.object(entry.ctl, "atm_lock_summary", return_value={"active":False}):
                self.assertEqual(entry.recover(), 0)
                self.assertEqual(len(Bus.posts), 1)
                self.assertIn("STATUS=INDETERMINATE_AFTER_CRASH", Bus.posts[0])
                state = json.loads(state_path.read_text())
                self.assertEqual(state["processed_comments"]["123"]["phase"], "DONE")
                self.assertEqual(state["processed_commands"]["cmd-1"]["phase"], "DONE")
                self.assertEqual(entry.recover(), 0)
                self.assertEqual(len(Bus.posts), 1)


if __name__ == "__main__":
    unittest.main()
