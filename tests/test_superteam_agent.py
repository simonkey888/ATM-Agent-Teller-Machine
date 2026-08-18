from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from atm_core import radar_sources
from atm_core.superteam_agent import SuperteamAgentApi, SuperteamAgentError


class FakeHttp:
    def __init__(self, *, gets=None, posts=None):
        self.gets = list(gets or [])
        self.posts = list(posts or [])
        self.calls = []

    def get_json(self, url, **kwargs):
        self.calls.append(("GET", url))
        if not self.gets:
            raise AssertionError(f"unexpected GET {url}")
        return self.gets.pop(0)

    def request_json(self, method, url, **kwargs):
        self.calls.append((method.upper(), url, kwargs.get("body")))
        if not self.posts:
            raise AssertionError(f"unexpected POST {url}")
        return self.posts.pop(0), {}, 200


class SuperteamAgentApiTests(unittest.TestCase):
    def test_register_exactly_once_never_returns_secret_fields(self):
        with tempfile.TemporaryDirectory() as td, patch.object(radar_sources, "SECRET_FILE", Path(td) / "radar-secrets.json"), patch.dict(os.environ, {}, clear=True):
            fake = FakeHttp(posts=[{"agentId": "agent-1", "username": "atm", "apiKey": "sk-secret", "claimCode": "claim-secret"}])
            api = SuperteamAgentApi(fake)
            first = api.register_exactly_once()
            second = api.register_exactly_once()
            self.assertEqual(first.agent_id, "agent-1")
            self.assertEqual(first.status, "CREATED")
            self.assertEqual(second.status, "EXISTING")
            self.assertEqual(len([call for call in fake.calls if call[0] == "POST"]), 1)
            self.assertFalse(hasattr(first, "apiKey"))
            self.assertFalse(hasattr(first, "claimCode"))
            stored = json.loads(radar_sources.SECRET_FILE.read_text())
            self.assertEqual(stored["superteam_agent_id"], "agent-1")

    def test_partial_identity_never_creates_second_agent(self):
        with tempfile.TemporaryDirectory() as td, patch.object(radar_sources, "SECRET_FILE", Path(td) / "radar-secrets.json"), patch.dict(os.environ, {}, clear=True):
            radar_sources.SECRET_FILE.write_text(json.dumps({"superteam_agent_id": "agent-existing"}))
            api = SuperteamAgentApi(FakeHttp(posts=[{"agentId": "bad"}]))
            with self.assertRaises(SuperteamAgentError):
                api.register_exactly_once()

    def test_official_submission_update_comment_and_monitor_surfaces(self):
        with tempfile.TemporaryDirectory() as td, patch.object(radar_sources, "SECRET_FILE", Path(td) / "radar-secrets.json"), patch.dict(os.environ, {}, clear=True):
            radar_sources.SECRET_FILE.write_text(json.dumps({
                "superteam_agent_id": "agent-1",
                "superteam_api_key": "sk-secret",
                "superteam_claim_code": "claim-secret",
            }))
            fake = FakeHttp(
                gets=[
                    {"listing": {"id": "listing-1", "slug": "one", "agentAccess": "AGENT_ONLY", "status": "CLOSED"}, "submission": {"status": "WINNER"}},
                    {"comments": [{"id": "c1", "message": "scope?"}]},
                ],
                posts=[
                    {"submission": {"id": "sub-1"}},
                    {"submission": {"id": "sub-1", "updated": True}},
                    {"comment": {"id": "c2"}},
                ],
            )
            api = SuperteamAgentApi(fake)
            monitored = api.monitor_listing("one")
            self.assertTrue(monitored["is_winner"])
            self.assertTrue(monitored["human_claim_required"])
            created = api.create_submission(listing_id="listing-1", link="https://example.test/deliverable", other_info="bounded deliverable")
            self.assertEqual(created["submission"]["id"], "sub-1")
            updated = api.update_submission({"listingId": "listing-1", "link": "https://example.test/deliverable-v2", "otherInfo": "updated"})
            self.assertTrue(updated["submission"]["updated"])
            comment = api.create_comment({"refType": "BOUNTY", "refId": "listing-1", "message": "scope clarification", "pocId": "poc-1"})
            self.assertEqual(comment["comment"]["id"], "c2")
            comments = api.comments("listing-1")
            self.assertEqual(comments[0]["id"], "c1")
            gate = api.winner_human_gate()
            self.assertEqual(gate["claim_code_exposed"], "false")
            self.assertNotIn("claim-secret", json.dumps(gate))


if __name__ == "__main__":
    unittest.main()
