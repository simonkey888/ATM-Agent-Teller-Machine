from __future__ import annotations

import unittest
from datetime import datetime, timezone

from atm_core.simon_gate import GuruPublicSource, SimonGateError, TelegramBot, notification_text


NOW = datetime(2026, 8, 19, 21, 30, tzinfo=timezone.utc)


def card(*, job_id: str, title: str, age: str, budget: str, quotes: str, body: str = "Python automation and data entry work") -> str:
    return f'''<section class="jobRecord">
      <div>{age} · {quotes}</div>
      <a href="/jobs/{title.lower().replace(' ', '-')}/{job_id}&SearchUrl=search.aspx"><h2>{title}</h2></a>
      <div>{budget}</div><div>Send before Sep 18, 2026</div><a>Send Quote</a>
      <p>{body}</p><span>Programming &amp; Development</span>
    </section>'''


class GuruPublicSourceTests(unittest.TestCase):
    def test_discovers_scores_and_dedupes_current_zero_login_leads(self):
        raw = card(
            job_id="2121001",
            title="Remote Data Entry Clerk",
            age="Posted 1 hr ago",
            budget="Hourly|$35 - $45|10-30 hrs/wk|6+ months",
            quotes="No Quotes Received",
        ) + card(
            job_id="2121002",
            title="Python Automation",
            age="Posted 2 hrs ago",
            budget="Fixed Price | $250-$500",
            quotes="12 Quotes Received",
        ) + card(
            job_id="2121001",
            title="Remote Data Entry Clerk",
            age="Posted 1 hr ago",
            budget="Hourly|$35 - $45|10-30 hrs/wk|6+ months",
            quotes="No Quotes Received",
        )
        rows = GuruPublicSource(now=NOW).discover(raw)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].source_id, "guru:2121001")
        self.assertEqual(rows[0].exact_bid, "USD 35/hr")
        self.assertEqual(rows[0].competition, 0)
        self.assertEqual(rows[0].account_gate, "GURU_ACCOUNT_GATE")
        self.assertEqual(rows[0].payment_risk, "GURU_SAFEPAY_AVAILABLE_NOT_FUNDED_UNTIL_AGREEMENT")
        self.assertIn("accuracy-first", rows[0].proposal)
        self.assertTrue(rows[0].url.startswith("https://www.guru.com/jobs/"))
        self.assertNotIn("SearchUrl", rows[0].url)

    def test_stale_georestricted_and_no_exact_budget_are_rejected(self):
        raw = card(
            job_id="1", title="Python Developer", age="Posted 5 days ago", budget="Fixed Price | $250-$500", quotes="0 Quotes Received"
        ) + card(
            job_id="2", title="Software Developer", age="Posted 1 hr ago", budget="Fixed Price | $5k-$10k", quotes="0 Quotes Received", body="W9 Required for U.S. only"
        ) + card(
            job_id="3", title="AI Automation", age="Posted 1 hr ago", budget="Fixed Price", quotes="0 Quotes Received"
        )
        self.assertEqual(GuruPublicSource(now=NOW).discover(raw), [])

    def test_non_agentic_or_paid_tooling_leads_are_rejected(self):
        raw = card(
            job_id="2120332",
            title="Cold Calling and texting for a Roofing Company",
            age="Posted 12 mins ago",
            budget="Fixed Price | $250-$500",
            quotes="No Quotes Received",
            body="Cold calling homeowners. Must have dialers - software and CRM to import leads. Outbound Sales Telemarketing.",
        )
        self.assertEqual(GuruPublicSource(now=NOW).discover(raw), [])

    def test_under_budget_gets_exact_zero_spend_bid(self):
        raw = card(
            job_id="4", title="WordPress bug fix", age="Posted 3 hrs ago", budget="Fixed Price | Under $250", quotes="4 Quotes Received"
        )
        row = GuruPublicSource(now=NOW).discover(raw)[0]
        self.assertEqual(row.exact_bid, "USD 200 fixed")
        self.assertEqual(row.budget_high, "250")


class TelegramBoundaryTests(unittest.TestCase):
    def test_pair_requires_exactly_one_private_start(self):
        updates = [
            {"update_id": 1, "message": {"text": "/start", "chat": {"id": 123, "type": "private"}}},
            {"update_id": 2, "message": {"text": "hello", "chat": {"id": 999, "type": "private"}}},
        ]
        self.assertEqual(TelegramBot.pair_chat_id(updates), 123)
        with self.assertRaises(SimonGateError):
            TelegramBot.pair_chat_id([])
        with self.assertRaises(SimonGateError):
            TelegramBot.pair_chat_id(updates + [{"update_id": 3, "message": {"text": "/start", "chat": {"id": 456, "type": "private"}}}])

    def test_owner_action_is_state_only_and_notification_has_no_secret_request(self):
        updates = [
            {"update_id": 4, "message": {"text": "ACTIVATED GURU", "chat": {"id": 123, "type": "private"}}},
            {"update_id": 5, "message": {"text": "POSTULÉ guru:2121001", "chat": {"id": 123, "type": "private"}}},
        ]
        self.assertEqual(TelegramBot.latest_owner_action(updates, 123), "POSTULÉ guru:2121001")
        row = GuruPublicSource(now=NOW).discover(card(
            job_id="2121001", title="Remote Data Entry Clerk", age="Posted 1 hr ago", budget="Hourly|$35 - $45", quotes="No Quotes Received"
        ))[0]
        message = notification_text(row, activated=False)
        self.assertIn("🔐 ACTIVATE GURU", message)
        self.assertIn("Exact bid: USD 35/hr", message)
        self.assertIn("POSTULÉ guru:2121001", message)
        lowered = message.lower()
        self.assertIn("never send atm a password", lowered)
        self.assertNotIn("api_key=", lowered)
        self.assertNotIn("cookie=", lowered)


if __name__ == "__main__":
    unittest.main()
