from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable


class SimonGateError(RuntimeError):
    pass


def _clean(raw: str) -> str:
    text = re.sub(r"<script\b[^>]*>[\s\S]*?</script>|<style\b[^>]*>[\s\S]*?</style>", " ", raw, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _money(value: str) -> Decimal:
    value = value.replace(",", "").strip().lower()
    mult = Decimal("1000") if value.endswith("k") else Decimal("1")
    if value.endswith("k"):
        value = value[:-1]
    return Decimal(value) * mult


def _plain(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class SimonGateLead:
    source_id: str
    platform: str
    title: str
    url: str
    description: str
    observed_at: str
    age_minutes: int
    budget_low: str
    budget_high: str
    currency: str
    exact_bid: str
    competition: int
    payment_risk: str
    account_gate: str
    score: str
    proposal: str

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


class GuruPublicSource:
    """Credentialless public Guru radar.

    This source only reads the public jobs surface. It never logs in, submits a
    quote, stores cookies, or treats an advertised budget as funded money.
    """

    name = "guru"
    url = "https://www.guru.com/d/jobs/"
    signup_url = "https://www.guru.com/registeraccount.aspx"

    _href = re.compile(
        r'<a\b[^>]*href=["\'](?P<href>(?:https://www\.guru\.com)?/jobs/[^"\']+/\d+[^"\']*)["\'][^>]*>(?P<title>[\s\S]{1,600}?)</a>',
        re.I,
    )

    _fit_terms = (
        "python", "javascript", "typescript", "react", "node", "api", "software", "developer",
        "development", "automation", "data entry", "data cleaning", "qa", "testing", "ai ",
        "artificial intelligence", "research", "wordpress", "web ", "database", "json", "csv",
    )

    _reject_execution = re.compile(
        r"\b(cold calling|telemarketing|telesales|outbound sales|door[- ]to[- ]door|must have dialers?|dialer software|phone calling|voice calls?|in[- ]person|on[- ]site|onsite)\b",
        re.I,
    )

    def __init__(self, *, now: datetime | None = None, max_age: timedelta = timedelta(days=3)):
        self.now = (now or _utcnow()).astimezone(timezone.utc)
        self.max_age = max_age

    def fetch(self) -> str:
        req = urllib.request.Request(
            self.url,
            headers={"Accept": "text/html", "User-Agent": "ATM-SimonGate/1.0 (+public-read-only)"},
        )
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.read().decode("utf-8", errors="replace")

    def discover(self, raw_html: str | None = None) -> list[SimonGateLead]:
        raw = raw_html if raw_html is not None else self.fetch()
        found: dict[str, SimonGateLead] = {}
        for match in self._href.finditer(raw):
            href = html.unescape(match.group("href"))
            href = re.sub(r"(?:&|%26)SearchUrl(?:=|%3D).*$", "", href, flags=re.I)
            if href.startswith("/"):
                href = "https://www.guru.com" + href
            title = _clean(match.group("title"))
            if not title or title.lower() in {"send a quote", "add to watchlist"}:
                continue
            job_id_match = re.search(r"/(\d+)(?:[/?&]|$)", href)
            if not job_id_match:
                continue
            job_id = job_id_match.group(1)
            start = max(0, match.start() - 900)
            end = min(len(raw), match.end() + 4200)
            block = _clean(raw[start:end])
            lead = self._normalize(job_id, title, href, block)
            if lead is not None:
                current = found.get(job_id)
                if current is None or Decimal(lead.score) > Decimal(current.score):
                    found[job_id] = lead
        return sorted(found.values(), key=lambda row: (Decimal(row.score), -row.competition), reverse=True)

    def _normalize(self, job_id: str, title: str, url: str, block: str) -> SimonGateLead | None:
        low, high, currency, bid = self._parse_budget(block)
        if low is None or high is None or not bid:
            return None
        age = self._parse_age(block)
        if age is None or age > self.max_age:
            return None
        lowered = f"{title} {block}".lower()
        if not any(term in lowered for term in self._fit_terms):
            return None
        if self._reject_execution.search(block):
            return None
        if re.search(r"\b(W9 required|U\.S\. only|US only|United States only|must reside in|must be based in)\b", block, re.I):
            return None
        if re.search(r"\b(unpaid|commission only|pay to apply|application fee)\b", block, re.I):
            return None
        competition = self._parse_competition(block)
        age_minutes = max(0, int(age.total_seconds() // 60))
        score = high / Decimal("10") + Decimal(max(0, 50 - competition)) - Decimal(age_minutes) / Decimal("720")
        description = self._description(block, title)
        proposal = self._proposal(title, description, bid)
        return SimonGateLead(
            source_id=f"guru:{job_id}",
            platform="GURU",
            title=title[:180],
            url=url,
            description=description[:700],
            observed_at=self.now.isoformat(),
            age_minutes=age_minutes,
            budget_low=_plain(low),
            budget_high=_plain(high),
            currency=currency,
            exact_bid=bid,
            competition=competition,
            payment_risk="GURU_SAFEPAY_AVAILABLE_NOT_FUNDED_UNTIL_AGREEMENT",
            account_gate="GURU_ACCOUNT_GATE",
            score=str(score.quantize(Decimal("0.001"))),
            proposal=proposal,
        )

    def _parse_budget(self, block: str) -> tuple[Decimal | None, Decimal | None, str, str]:
        hourly = re.search(r"Hourly\s*\|\s*\$\s*([\d,.]+(?:\.\d+)?k?)\s*-\s*\$\s*([\d,.]+(?:\.\d+)?k?)", block, re.I)
        if hourly:
            low, high = _money(hourly.group(1)), _money(hourly.group(2))
            return low, high, "USD", f"USD {_plain(low)}/hr"
        fixed = re.search(r"Fixed Price\s*\|\s*\$\s*([\d,.]+(?:\.\d+)?k?)\s*-\s*\$?\s*([\d,.]+(?:\.\d+)?k?)", block, re.I)
        if fixed:
            low, high = _money(fixed.group(1)), _money(fixed.group(2))
            return low, high, "USD", f"USD {_plain(low)} fixed"
        under = re.search(r"Fixed Price\s*\|\s*Under\s*\$\s*([\d,.]+(?:\.\d+)?k?)", block, re.I)
        if under:
            ceiling = _money(under.group(1))
            bid = max(Decimal("25"), (ceiling * Decimal("0.8")).quantize(Decimal("1")))
            return Decimal("0"), ceiling, "USD", f"USD {_plain(bid)} fixed"
        return None, None, "USD", ""

    def _parse_age(self, block: str) -> timedelta | None:
        relative = re.search(r"Posted\s+(\d+)\s+(min|mins|minute|minutes|hr|hrs|hour|hours|day|days)\s+ago", block, re.I)
        if relative:
            count = int(relative.group(1)); unit = relative.group(2).lower()
            if unit.startswith("min"):
                return timedelta(minutes=count)
            if unit.startswith("h"):
                return timedelta(hours=count)
            return timedelta(days=count)
        absolute = re.search(r"Posted(?:\s+on)?\s+([A-Z][a-z]+\s+\d{1,2},\s+20\d{2})", block)
        if absolute:
            try:
                posted = datetime.strptime(absolute.group(1), "%B %d, %Y").replace(tzinfo=timezone.utc)
                return max(timedelta(0), self.now - posted)
            except ValueError:
                return None
        return None

    @staticmethod
    def _parse_competition(block: str) -> int:
        if re.search(r"No Quotes? Received", block, re.I):
            return 0
        match = re.search(r"(\d+)\s+Quotes? Received", block, re.I)
        if not match:
            match = re.search(r"Quotes?\s*\((\d+)\)", block, re.I)
        return int(match.group(1)) if match else 999

    @staticmethod
    def _description(block: str, title: str) -> str:
        text = block
        marker = text.lower().find(title.lower())
        if marker >= 0:
            text = text[marker + len(title):]
        text = re.sub(r"Send before.*?Send Quote", " ", text, flags=re.I)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _proposal(title: str, description: str, bid: str) -> str:
        lowered = f"{title} {description}".lower()
        if "data entry" in lowered or "data cleaning" in lowered:
            body = (
                "I can handle this with an accuracy-first workflow: validate the source data, enter/clean it in controlled batches, "
                "run duplicate and consistency checks, and provide a concise completion report. I can start immediately and keep the work auditable rather than relying on blind bulk entry."
            )
        elif "automation" in lowered or "ai " in lowered:
            body = (
                "I can implement this as a bounded automation: map the current workflow, build the smallest reliable integration, add validation/error handling, and deliver a tested handoff with clear run instructions. I can start immediately and keep scope focused on the working outcome."
            )
        else:
            body = (
                "I can take this from scope to a tested deliverable with a short implementation plan, incremental verification, and a clean handoff. I work directly against acceptance criteria and can start immediately."
            )
        return f"{body} My quote is {bid}."


class TelegramBot:
    """Tiny Bot API boundary. The token is never returned or serialized."""

    def __init__(self, token: str, *, api_root: str = "https://api.telegram.org"):
        self._token = str(token or "").strip()
        self.api_root = api_root.rstrip("/")
        if not self._token:
            raise SimonGateError("ATM_RADAR_BOT_API_KEY_REQUIRED")

    def _call(self, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = urllib.parse.urlencode(payload or {}).encode()
        req = urllib.request.Request(
            f"{self.api_root}/bot{self._token}/{method}",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "ATM-SimonGate/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as response:
            out = json.loads(response.read().decode("utf-8"))
        if not isinstance(out, dict) or out.get("ok") is not True:
            raise SimonGateError(f"TELEGRAM_{method.upper()}_FAILED")
        return out

    def updates(self) -> list[dict[str, Any]]:
        out = self._call("getUpdates", {"limit": 100, "timeout": 0, "allowed_updates": json.dumps(["message"])})
        rows = out.get("result")
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    @staticmethod
    def pair_chat_id(updates: Iterable[dict[str, Any]]) -> int:
        chat_ids: set[int] = set()
        for update in updates:
            message = update.get("message") if isinstance(update, dict) else None
            chat = message.get("chat") if isinstance(message, dict) else None
            text = str(message.get("text") or "").strip() if isinstance(message, dict) else ""
            if isinstance(chat, dict) and chat.get("type") == "private" and re.match(r"^/start(?:\s|$)", text, re.I):
                try:
                    chat_ids.add(int(chat["id"]))
                except (KeyError, TypeError, ValueError):
                    pass
        if len(chat_ids) != 1:
            raise SimonGateError("TELEGRAM_PAIRING_REQUIRES_EXACTLY_ONE_PRIVATE_START")
        return next(iter(chat_ids))

    @staticmethod
    def latest_owner_action(updates: Iterable[dict[str, Any]], chat_id: int) -> str | None:
        latest: tuple[int, str] | None = None
        for update in updates:
            message = update.get("message") if isinstance(update, dict) else None
            chat = message.get("chat") if isinstance(message, dict) else None
            if not isinstance(chat, dict) or int(chat.get("id") or 0) != int(chat_id):
                continue
            text = str(message.get("text") or "").strip()
            if re.match(r"^(POSTUL[ÉE]|DESCARTAR|ACTIVATED\s+[A-Z0-9_-]+)(?:\s|$)", text, re.I):
                candidate = (int(update.get("update_id") or 0), text[:240])
                if latest is None or candidate[0] > latest[0]:
                    latest = candidate
        return latest[1] if latest else None

    def send(self, chat_id: int, text: str) -> int:
        out = self._call("sendMessage", {"chat_id": str(int(chat_id)), "text": text, "disable_web_page_preview": "true"})
        result = out.get("result") if isinstance(out.get("result"), dict) else {}
        return int(result.get("message_id") or 0)


def notification_text(lead: SimonGateLead, *, activated: bool = False) -> str:
    age = f"{lead.age_minutes} min" if lead.age_minutes < 120 else f"{lead.age_minutes // 60} h"
    activation = "" if activated else (
        "🔐 ACTIVATE GURU\n"
        "Reason: real current paid lead found / account required to quote\n"
        f"Signup: {GuruPublicSource.signup_url}\n"
        f"Lead: {lead.url}\n"
        "Action: create/verify the account if needed; never send ATM a password, cookie, OTP or payment credential.\n\n"
    )
    return (
        activation
        + "ATM SG2 — PROPOSAL_READY\n"
        + f"Platform: {lead.platform}\n"
        + f"Lead: {lead.title}\n"
        + f"URL: {lead.url}\n"
        + f"Age: {age}\n"
        + f"Budget: {lead.currency} {lead.budget_low}–{lead.budget_high}\n"
        + f"Exact bid: {lead.exact_bid}\n"
        + f"Competition: {lead.competition} quotes\n"
        + f"Payment risk: {lead.payment_risk}\n\n"
        + "Exact proposal:\n"
        + lead.proposal
        + "\n\nAfter the single platform action, reply: POSTULÉ "
        + lead.source_id
    )
