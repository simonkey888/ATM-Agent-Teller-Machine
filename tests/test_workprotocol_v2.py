from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from atm_core import opportunities as opportunity_module
from atm_core.workprotocol_v2 import CurrentWorkProtocolOpportunityAdapter


class FakeHttp:
    def __init__(self):
        self.posts = []

    def post_json(self, url, payload, headers=None):
        self.posts.append((url, payload, headers or {}))
        return {"agent": {"id": "agent-current", "apiKey": "wp-test-secret"}}


class CurrentWorkProtocolRegistrationTests(unittest.TestCase):
    def test_registration_uses_current_documented_capabilities_shape(self):
        http = FakeHttp()
        adapter = CurrentWorkProtocolOpportunityAdapter(
            http=http,
            payment_recipient_public_identifier="0x1111111111111111111111111111111111111111",
        )
        with tempfile.TemporaryDirectory() as td, patch.object(
            opportunity_module, "ATM_SECRETS", Path(td) / "secrets.env"
        ):
            key, agent_id = adapter._register()
        self.assertEqual((key, agent_id), ("wp-test-secret", "agent-current"))
        self.assertEqual(http.posts[0][0], "https://workprotocol.ai/api/agents/register")
        capabilities = http.posts[0][1]["capabilities"]
        self.assertEqual(capabilities["categories"], ["code"])
        self.assertIn("python", capabilities["languages"])
        self.assertNotIn("code", {k for k, v in capabilities.items() if isinstance(v, bool)})


if __name__ == "__main__":
    unittest.main()
