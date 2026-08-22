from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from .universal_radar import (
    AgentPolicy,
    FundingStatus,
    JsonHttpClient,
    RadarRegistry,
    SourceState,
    SourceUnavailable,
    UniversalOpportunity,
    source_hash,
)


SECRET_FILE = Path(__file__).resolve().parents[2] / ".atm" / "radar-secrets.json"


def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _decimal(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value if value not in (None, "") else default))
    except Exception:
        return Decimal(default)


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _load_secret_state() -> dict[str, Any]:
    if not SECRET_FILE.exists():
        return {}
    try:
        return json.loads(SECRET_FILE.read_text(encoding="utf-8"))
    except Exception:
        raise RuntimeError("radar secret state is corrupt")


def _persist_secret_state(payload: dict[str, Any]) -> None:
    SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SECRET_FILE.with_name(SECRET_FILE.name + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, SECRET_FILE)
    try:
        os.chmod(SECRET_FILE, 0o600)
    except OSError:
        pass


def _agent_policy(value: Any, *, default: AgentPolicy = AgentPolicy.UNKNOWN) -> AgentPolicy:
    text = str(value or "").strip().upper().replace("-", "_")
    if text in AgentPolicy.__members__:
        return AgentPolicy[text]
    if text in {"ALLOW", "ALLOWED", "AGENT", "AI_ALLOWED"}:
        return AgentPolicy.AGENT_ALLOWED
    if text in {"AGENTS_ONLY", "AI_ONLY"}:
        return AgentPolicy.AGENT_ONLY
    if text in {"HUMAN", "HUMAN_ONLY"}:
        return AgentPolicy.HUMAN_ONLY
    return default


def _payment_fields(reward: Decimal, currency: str, fee: Decimal = Decimal("0")) -> tuple[Decimal | None, Decimal]:
    code = currency.upper()
    usd = reward if code in {"USD", "USDC"} else None
    net = max(Decimal("0"), (usd if usd is not None else reward) - fee)
    return usd, net


class WorkProtocolRadarSource:
    name = "workprotocol"
    priority = 2

    def __init__(self, http: JsonHttpClient | None = None, base_url: str = "https://workprotocol.ai"):
        self.http = http or JsonHttpClient()
        self.base_url = base_url.rstrip("/")

    def discover(self, floor_usd: Decimal) -> list[UniversalOpportunity]:
        query = urllib.parse.urlencode({"status": "open", "min_pay": str(floor_usd), "sort": "newest", "limit": 50})
        data = self.http.get_json(f"{self.base_url}/api/jobs?{query}", timeout=10)
        jobs = _list(data.get("jobs")) if isinstance(data, dict) else []
        out: list[UniversalOpportunity] = []
        for job in jobs:
            if not isinstance(job, dict) or not job.get("id"):
                continue
            reward = _decimal(job.get("paymentAmount"))
            currency = str(job.get("paymentCurrency") or "USDC").upper()
            fee = reward * Decimal("0.05")
            usd, net = _payment_fields(reward, currency, fee)
            escrow_status = str(job.get("escrowStatus") or "").lower()
            funded = bool(job.get("escrowFunded")) or bool(job.get("escrowTxHash")) or escrow_status in {"funded", "locked", "escrowed"}
            claims = len(job.get("claims")) if isinstance(job.get("claims"), list) else int(job.get("claimCount") or 0)
            out.append(
                UniversalOpportunity(
                    source=self.name,
                    external_id=str(job["id"]),
                    canonical_url=f"{self.base_url}/jobs/{job['id']}",
                    title=str(job.get("title") or "WorkProtocol job"),
                    description=str(job.get("description") or ""),
                    category=str(job.get("category") or "custom"),
                    created_at=_dt(job.get("createdAt")),
                    updated_at=_dt(job.get("updatedAt")),
                    deadline=_dt(job.get("deadline")),
                    payout_gross=reward,
                    payout_currency=currency,
                    payout_usd=usd,
                    fees=fee,
                    payout_net=net,
                    funding_status=FundingStatus.VERIFIED if funded else FundingStatus.UNKNOWN,
                    funding_proof={"escrowFunded": bool(job.get("escrowFunded")), "escrowTxHash": job.get("escrowTxHash"), "escrowStatus": job.get("escrowStatus")},
                    escrow={"rail": job.get("paymentRail"), "status": job.get("escrowStatus")},
                    competition=claims,
                    claims=claims,
                    eligibility="PUBLIC_AGENT",
                    agent_policy=AgentPolicy.AGENT_ONLY,
                    claim_method="POST /api/jobs/:id/claim",
                    submission_method="POST /api/jobs/:id/deliver",
                    payment_rail=str(job.get("paymentRail") or "base") + ":" + currency,
                    payout_latency=f"verification_window={job.get('verificationWindowHours', 24)}h",
                    withdrawal_path="Base USDC payout wallet" if str(job.get("paymentRail") or "base").lower() == "base" else "platform settlement",
                    estimated_execution_minutes=int(job.get("estimatedMinutes") or 90),
                    required_capabilities=[str(job.get("category") or "custom"), *[str(x) for x in _list((job.get("requirements") or {}).get("capabilities"))]],
                    source_state_hash=source_hash(job),
                )
            )
        return out

    def register_agent_once(self, payout_address: str) -> dict[str, str]:
        if not re.fullmatch(r"0x[0-9a-fA-F]{40}", payout_address or ""):
            raise ValueError("public Base payout address required")
        state = _load_secret_state()
        existing_key = os.getenv("WORKPROTOCOL_API_KEY", "").strip() or str(state.get("workprotocol_api_key") or "")
        existing_id = os.getenv("WORKPROTOCOL_AGENT_ID", "").strip() or str(state.get("workprotocol_agent_id") or "")
        if existing_key and existing_id:
            return {"agent_id": existing_id, "status": "EXISTING"}
        if bool(existing_key) != bool(existing_id):
            raise RuntimeError("partial WorkProtocol credential state; fail closed")
        data, _, _ = self.http.request_json(
            "POST",
            f"{self.base_url}/api/agents/register",
            body={
                "name": "atm-universal-money-radar",
                "description": "Zero-spend autonomous digital work executor",
                "walletAddress": payout_address,
            },
            timeout=12,
        )
        agent = data.get("agent") if isinstance(data, dict) and isinstance(data.get("agent"), dict) else data
        api_key = str((data or {}).get("apiKey") or (agent or {}).get("apiKey") or "").strip()
        agent_id = str((agent or {}).get("id") or (data or {}).get("agentId") or "").strip()
        if not api_key or not agent_id:
            raise RuntimeError("WorkProtocol registration returned incomplete credentials")
        state.update({"workprotocol_api_key": api_key, "workprotocol_agent_id": agent_id})
        _persist_secret_state(state)
        return {"agent_id": agent_id, "status": "CREATED"}


class SuperteamEarnSource:
    name = "superteam"
    priority = 1

    def __init__(self, http: JsonHttpClient | None = None, base_url: str = "https://superteam.fun"):
        self.http = http or JsonHttpClient()
        self.base_url = base_url.rstrip("/")

    def _credential(self) -> str:
        state = _load_secret_state()
        return os.getenv("SUPERTEAM_AGENT_API_KEY", "").strip() or str(state.get("superteam_api_key") or "").strip()

    def register_agent_once(self, name: str = "atm-universal-money-radar") -> dict[str, str]:
        state = _load_secret_state()
        if state.get("superteam_agent_id") and state.get("superteam_api_key"):
            return {"agent_id": str(state["superteam_agent_id"]), "status": "EXISTING"}
        if state.get("superteam_agent_id") or state.get("superteam_api_key") or state.get("superteam_claim_code"):
            raise RuntimeError("partial Superteam credential state; refuse duplicate identity")
        data, _, _ = self.http.request_json("POST", f"{self.base_url}/api/agents", body={"name": name}, timeout=12)
        api_key = str((data or {}).get("apiKey") or "").strip()
        claim_code = str((data or {}).get("claimCode") or "").strip()
        agent_id = str((data or {}).get("agentId") or "").strip()
        if not api_key or not claim_code or not agent_id:
            raise RuntimeError("Superteam registration response missing credential boundary fields")
        state.update({"superteam_api_key": api_key, "superteam_claim_code": claim_code, "superteam_agent_id": agent_id})
        _persist_secret_state(state)
        return {"agent_id": agent_id, "status": "CREATED"}  # never return apiKey or claimCode

    def discover(self, floor_usd: Decimal) -> list[UniversalOpportunity]:
        key = self._credential()
        if not key:
            raise SourceUnavailable(self.name, SourceState.AUTH_REQUIRED, "SUPERTEAM_AGENT_API_KEY absent; registration is explicit and single-identity")
        data = self.http.get_json(
            f"{self.base_url}/api/agents/listings/live?take=50",
            headers={"Authorization": f"Bearer {key}"},
            timeout=10,
        )
        listings = _list(data.get("listings") or data.get("data")) if isinstance(data, dict) else _list(data)
        out: list[UniversalOpportunity] = []
        for listing in listings:
            if not isinstance(listing, dict):
                continue
            ext_id = str(listing.get("id") or listing.get("slug") or "").strip()
            if not ext_id:
                continue
            reward = _decimal(listing.get("reward") or listing.get("rewardAmount") or listing.get("amount"))
            currency = str(listing.get("rewardCurrency") or listing.get("currency") or "USD").upper()
            usd, net = _payment_fields(reward, currency)
            access = _agent_policy(listing.get("agentAccess"), default=AgentPolicy.AGENT_ALLOWED)
            out.append(
                UniversalOpportunity(
                    source=self.name,
                    external_id=ext_id,
                    canonical_url=str(listing.get("url") or f"{self.base_url}/earn/listing/{listing.get('slug') or ext_id}"),
                    title=str(listing.get("title") or "Superteam Earn listing"),
                    description=str(listing.get("description") or listing.get("summary") or ""),
                    category=str(listing.get("type") or listing.get("category") or "bounty"),
                    created_at=_dt(listing.get("createdAt")),
                    updated_at=_dt(listing.get("updatedAt")),
                    deadline=_dt(listing.get("deadline")),
                    payout_gross=reward,
                    payout_currency=currency,
                    payout_usd=usd,
                    payout_net=net,
                    funding_status=FundingStatus.FUNDED_SIGNAL if reward > 0 else FundingStatus.UNKNOWN,
                    funding_proof={"official_listing_reward": str(reward), "listing_id": ext_id},
                    competition=int(listing.get("submissionCount") or 0),
                    claims=int(listing.get("submissionCount") or 0),
                    eligibility=str(listing.get("eligibility") or "AGENT_ENDPOINT"),
                    agent_policy=access,
                    claim_method="NO_PRECLAIM" if listing.get("type") != "project" else "AGENT_SUBMISSION",
                    submission_method="POST /api/agents/submissions/create",
                    payment_rail=str(listing.get("paymentRail") or "Superteam human claim"),
                    payout_latency=str(listing.get("reviewPeriod") or "winner selection + human claim"),
                    withdrawal_path="human talent profile claim after win",
                    human_gate="SUPERTEAM_HUMAN_CLAIM_AFTER_WIN",
                    estimated_execution_minutes=int(listing.get("estimatedMinutes") or 90),
                    required_capabilities=[str(x) for x in _list(listing.get("skills"))] or [str(listing.get("type") or "content")],
                    source_state_hash=source_hash(listing),
                )
            )
        return out


class MoltJobsSource:
    name = "moltjobs"
    priority = 5

    def __init__(self, http: JsonHttpClient | None = None, base_url: str = "https://api.moltjobs.io/v1"):
        self.http = http or JsonHttpClient()
        self.base_url = base_url.rstrip("/")

    def discover(self, floor_usd: Decimal) -> list[UniversalOpportunity]:
        key = os.getenv("MOLTJOBS_API_KEY", "").strip()
        if not key:
            raise SourceUnavailable(self.name, SourceState.AUTH_REQUIRED, "MOLTJOBS_API_KEY absent")
        data = self.http.get_json(f"{self.base_url}/jobs?status=OPEN", headers={"Authorization": f"Bearer {key}"}, timeout=10)
        root = data.get("data") if isinstance(data, dict) and "data" in data else data
        jobs = _list((root or {}).get("jobs")) if isinstance(root, dict) else _list(root)
        return [self._normalize(job) for job in jobs if isinstance(job, dict) and (job.get("id") or job.get("jobId"))]

    def _normalize(self, job: dict[str, Any]) -> UniversalOpportunity:
        ext_id = str(job.get("id") or job.get("jobId"))
        reward = _decimal(job.get("pay_usdc") or job.get("payUsdc") or job.get("budget") or job.get("reward"))
        escrow = job.get("escrow") if isinstance(job.get("escrow"), dict) else {}
        funded = str(escrow.get("status") or job.get("fundingStatus") or "").upper() in {"FUNDED", "LOCKED", "ESCROWED"}
        return UniversalOpportunity(
            source=self.name,
            external_id=ext_id,
            canonical_url=str(job.get("url") or f"https://moltjobs.io/jobs/{ext_id}"),
            title=str(job.get("title") or "MoltJobs job"),
            description=str(job.get("description") or ""),
            category=str(job.get("vertical") or job.get("category") or "unknown"),
            created_at=_dt(job.get("createdAt") or job.get("created_at")),
            updated_at=_dt(job.get("updatedAt") or job.get("updated_at")),
            deadline=_dt(job.get("deadline")),
            payout_gross=reward,
            payout_currency="USDC",
            payout_usd=reward,
            payout_net=reward,
            funding_status=FundingStatus.VERIFIED if funded else FundingStatus.FUNDED_SIGNAL,
            funding_proof={"escrow": escrow, "official_job": ext_id},
            escrow=escrow,
            competition=int(job.get("bidCount") or job.get("bids") or 0),
            claims=int(job.get("bidCount") or job.get("bids") or 0),
            eligibility=str(job.get("eligibility") or "AGENT"),
            agent_policy=AgentPolicy.AGENT_ONLY,
            claim_method="bid/apply via official API",
            submission_method="official MoltJobs job submission API",
            payment_rail="USDC escrow",
            payout_latency=str(job.get("payoutLatency") or "platform settlement"),
            withdrawal_path="USDC agent payout",
            estimated_execution_minutes=int(job.get("estimatedMinutes") or 90),
            required_capabilities=[str(x) for x in _list(job.get("skills"))],
            source_state_hash=source_hash(job),
        )


class NearAgentMarketSource:
    name = "near-agent-market"
    priority = 6

    def __init__(self, http: JsonHttpClient | None = None, base_url: str = "https://market.near.ai"):
        self.http = http or JsonHttpClient(); self.base_url = base_url.rstrip("/")

    def discover(self, floor_usd: Decimal) -> list[UniversalOpportunity]:
        data = self.http.get_json(f"{self.base_url}/v1/jobs?status=open", timeout=10)
        jobs = _list(data.get("jobs") or data.get("data")) if isinstance(data, dict) else _list(data)
        out: list[UniversalOpportunity] = []
        for job in jobs:
            if not isinstance(job, dict) or not (job.get("id") or job.get("job_id")):
                continue
            ext_id = str(job.get("id") or job.get("job_id")); reward = _decimal(job.get("budget") or job.get("reward") or job.get("amount"))
            currency = str(job.get("currency") or "USDC").upper(); usd, net = _payment_fields(reward, currency)
            escrow_status = str(job.get("escrow_status") or job.get("funding_status") or "").upper()
            out.append(UniversalOpportunity(
                source=self.name, external_id=ext_id, canonical_url=f"{self.base_url}/jobs/{ext_id}", title=str(job.get("title") or "NEAR Agent Market job"),
                description=str(job.get("description") or ""), category=str(job.get("category") or "unknown"), created_at=_dt(job.get("created_at")), updated_at=_dt(job.get("updated_at")), deadline=_dt(job.get("deadline")),
                payout_gross=reward, payout_currency=currency, payout_usd=usd, payout_net=net,
                funding_status=FundingStatus.VERIFIED if escrow_status in {"FUNDED", "LOCKED", "ESCROWED"} else FundingStatus.UNKNOWN,
                funding_proof={"escrow_status": escrow_status}, escrow={"status": escrow_status}, competition=int(job.get("bid_count") or 0), claims=int(job.get("bid_count") or 0),
                eligibility="AGENT_MARKET", agent_policy=AgentPolicy.AGENT_ONLY, claim_method="POST /v1/jobs/:id/bids or award flow", submission_method="POST /v1/jobs/:id/submit", payment_rail=str(job.get("payment_rail") or "NEAR escrow"),
                payout_latency="escrow release after acceptance", withdrawal_path="NEAR Agent Market wallet withdrawal", human_gate=None,
                estimated_execution_minutes=int(job.get("estimated_minutes") or 90), required_capabilities=[str(x) for x in _list(job.get("skills"))], execution_cost_usd=Decimal("0"), source_state_hash=source_hash(job)))
        return out


class AgentHansaSource:
    name = "agenthansa"
    priority = 8

    def __init__(self, http: JsonHttpClient | None = None, base_url: str = "https://www.agenthansa.com"):
        self.http = http or JsonHttpClient(); self.base_url = base_url.rstrip("/")

    def discover(self, floor_usd: Decimal) -> list[UniversalOpportunity]:
        key = os.getenv("AGENTHANSA_API_KEY", "").strip()
        if not key:
            raise SourceUnavailable(self.name, SourceState.AUTH_REQUIRED, "AGENTHANSA_API_KEY absent")
        data = self.http.get_json(f"{self.base_url}/api/agents/work", headers={"Authorization": f"Bearer {key}"}, timeout=10)
        jobs = _list(data.get("work") or data.get("jobs") or data.get("data")) if isinstance(data, dict) else _list(data)
        out = []
        for job in jobs:
            if not isinstance(job, dict) or not (job.get("id") or job.get("assignment_id")):
                continue
            ext_id = str(job.get("id") or job.get("assignment_id")); reward = _decimal(job.get("reward_usd") or job.get("reward") or job.get("amount"))
            out.append(UniversalOpportunity(source=self.name, external_id=ext_id, canonical_url=str(job.get("url") or f"{self.base_url}/agents/work/{ext_id}"), title=str(job.get("title") or "AgentHansa work"), description=str(job.get("description") or ""), category=str(job.get("category") or "agent-work"), created_at=_dt(job.get("created_at")), updated_at=_dt(job.get("updated_at")), deadline=_dt(job.get("deadline")), payout_gross=reward, payout_currency="USD", payout_usd=reward, payout_net=reward, funding_status=FundingStatus.FUNDED_SIGNAL if reward > 0 else FundingStatus.UNKNOWN, funding_proof={"official_assignment": ext_id}, eligibility="REGISTERED_AGENT", agent_policy=AgentPolicy.AGENT_ONLY, claim_method="official assignment accept endpoint", submission_method="official assignment submit endpoint", payment_rail=str(job.get("payment_rail") or "platform payout"), payout_latency=str(job.get("payout_latency") or "UNKNOWN"), withdrawal_path=str(job.get("withdrawal_path") or "platform payout destination"), human_gate=str(job.get("human_gate") or "") or None, estimated_execution_minutes=int(job.get("estimated_minutes") or 90), required_capabilities=[str(x) for x in _list(job.get("skills"))], source_state_hash=source_hash(job)))
        return out


class BountyBookSource:
    name = "bountybook"
    priority = 9

    def __init__(self, http: JsonHttpClient | None = None, base_url: str = "https://www.bountybook.ai"):
        self.http = http or JsonHttpClient(); self.base_url = base_url.rstrip("/")

    def discover(self, floor_usd: Decimal) -> list[UniversalOpportunity]:
        data = self.http.get_json(f"{self.base_url}/jobs?status=open", timeout=10)
        jobs = _list(data.get("jobs") or data.get("data")) if isinstance(data, dict) else _list(data)
        out = []
        for job in jobs:
            if not isinstance(job, dict) or not job.get("id"):
                continue
            reward = _decimal(job.get("reward") or job.get("amount") or job.get("budget")); ext_id = str(job["id"])
            funded = str(job.get("escrow_status") or job.get("status") or "").upper() in {"FUNDED", "OPEN", "ESCROWED", "LOCKED"} and bool(job.get("escrow") or job.get("escrow_tx") or job.get("funding_proof"))
            out.append(UniversalOpportunity(source=self.name, external_id=ext_id, canonical_url=str(job.get("url") or f"{self.base_url}/jobs/{ext_id}"), title=str(job.get("title") or "BountyBook bounty"), description=str(job.get("description") or ""), category=str(job.get("category") or "unknown"), created_at=_dt(job.get("created_at")), updated_at=_dt(job.get("updated_at")), deadline=_dt(job.get("deadline")), payout_gross=reward, payout_currency="USDC", payout_usd=reward, payout_net=reward * Decimal("0.96"), fees=reward * Decimal("0.04"), funding_status=FundingStatus.VERIFIED if funded else FundingStatus.UNKNOWN, funding_proof={"escrow": job.get("escrow"), "tx": job.get("escrow_tx")}, escrow=job.get("escrow") if isinstance(job.get("escrow"), dict) else {}, competition=int(job.get("claims") or 0), claims=int(job.get("claims") or 0), eligibility="HEADLESS_AGENT", agent_policy=AgentPolicy.AGENT_ONLY, claim_method="POST /jobs/:id/claim", submission_method="POST /jobs/:id/submit", payment_rail="x402/USDC Base", payout_latency="oracle verification then USDC", withdrawal_path="Base USDC wallet", estimated_execution_minutes=int(job.get("estimated_minutes") or 90), required_capabilities=[str(x) for x in _list(job.get("skills"))], source_state_hash=source_hash(job)))
        return out


class GitHubDirectBountySource:
    name = "github-direct"
    priority = 4

    def __init__(self, http: JsonHttpClient | None = None):
        self.http = http or JsonHttpClient()

    def discover(self, floor_usd: Decimal) -> list[UniversalOpportunity]:
        since = datetime.now(timezone.utc).date().isoformat()
        q = urllib.parse.quote(f'is:issue is:open label:bounty updated:>={since}')
        headers: dict[str, str] = {}
        token = os.getenv("GITHUB_TOKEN", "").strip() or os.getenv("BUNDLE_GITHUB_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = self.http.get_json(f"https://api.github.com/search/issues?q={q}&per_page=50&sort=updated&order=desc", headers=headers, timeout=10)
        items = _list(data.get("items")) if isinstance(data, dict) else []
        out = []
        amount_re = re.compile(r"(?:\$|USD\s*|USDC\s*)([0-9]+(?:\.[0-9]+)?)", re.I)
        for issue in items:
            if not isinstance(issue, dict) or not issue.get("html_url"):
                continue
            text = f"{issue.get('title','')}\n{issue.get('body','')}"; match = amount_re.search(text)
            reward = _decimal(match.group(1)) if match else Decimal("0")
            out.append(UniversalOpportunity(source=self.name, external_id=str(issue.get("id") or issue.get("node_id") or issue.get("html_url")), canonical_url=str(issue["html_url"]), title=str(issue.get("title") or "GitHub bounty issue"), description=str(issue.get("body") or ""), category="github", created_at=_dt(issue.get("created_at")), updated_at=_dt(issue.get("updated_at")), payout_gross=reward, payout_currency="USD", payout_usd=reward if reward else None, payout_net=reward, funding_status=FundingStatus.UNVERIFIED, funding_proof={"github_label": "bounty", "amount_text_only": bool(match)}, competition=int(issue.get("comments") or 0), eligibility="PROJECT_TERMS_REQUIRED", agent_policy=AgentPolicy.UNKNOWN, claim_method="PROJECT_SPECIFIC", submission_method="PULL_REQUEST_IF_ALLOWED", payment_rail="UNKNOWN", payout_latency="UNKNOWN", withdrawal_path="UNKNOWN", estimated_execution_minutes=120, required_capabilities=["github", "code"], source_state_hash=source_hash(issue)))
        return out


class OpireSource:
    name = "opire"
    priority = 3

    def __init__(self, base_url: str = "https://app.opire.dev/home"):
        self.base_url = base_url

    def discover(self, floor_usd: Decimal) -> list[UniversalOpportunity]:
        # Opire does not document a public REST feed. Read the public rewards page only;
        # never infer funding authority from text if structured listing data is unavailable.
        req = urllib.request.Request(self.base_url, headers={"User-Agent": "ATM-Universal-Radar/1.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            text = response.read().decode("utf-8", errors="replace")
        matches = re.findall(r'href=["\'](https://github\.com/[^"\']+/issues/\d+)["\'][^>]*>([^<]{0,200})', text, re.I)
        out: list[UniversalOpportunity] = []
        for url, title in matches[:50]:
            ext_id = url.split("github.com/", 1)[-1]
            amount_match = re.search(r"\$([0-9]+(?:\.[0-9]+)?)", title)
            reward = _decimal(amount_match.group(1)) if amount_match else Decimal("0")
            out.append(UniversalOpportunity(source=self.name, external_id=ext_id, canonical_url=url, title=re.sub(r"\s+", " ", title).strip() or "Opire reward", category="github", payout_gross=reward, payout_currency="USD", payout_usd=reward if reward else None, payout_net=reward, funding_status=FundingStatus.UNVERIFIED, funding_proof={"public_opire_listing": True}, eligibility="OPIRE_DEVELOPER", agent_policy=AgentPolicy.UNKNOWN, claim_method="/try or platform", submission_method="PR + /claim if project permits", payment_rail="Stripe", payout_latency="1-7 business days after creator pays", withdrawal_path="Stripe onboarding required", human_gate="OPIRE_STRIPE_PAYOUT_ONBOARDING", estimated_execution_minutes=120, required_capabilities=["github", "code"], source_state_hash=source_hash({"url": url, "title": title})))
        return out


class DisabledOrConfigurableSource:
    def __init__(self, name: str, priority: int, env_url: str, *, reason: str):
        self.name = name; self.priority = priority; self.env_url = env_url; self.reason = reason

    def discover(self, floor_usd: Decimal) -> list[UniversalOpportunity]:
        url = os.getenv(self.env_url, "").strip()
        if not url:
            raise SourceUnavailable(self.name, SourceState.UNKNOWN, self.reason)
        data = JsonHttpClient().get_json(url, timeout=10)
        rows = _list(data.get("jobs") or data.get("data")) if isinstance(data, dict) else _list(data)
        out = []
        for row in rows:
            if not isinstance(row, dict) or not (row.get("id") or row.get("url")):
                continue
            ext_id = str(row.get("id") or row.get("url")); reward = _decimal(row.get("reward") or row.get("amount") or row.get("pay"))
            out.append(UniversalOpportunity(source=self.name, external_id=ext_id, canonical_url=str(row.get("url") or url), title=str(row.get("title") or self.name), description=str(row.get("description") or ""), category=str(row.get("category") or "unknown"), created_at=_dt(row.get("created_at")), updated_at=_dt(row.get("updated_at")), deadline=_dt(row.get("deadline")), payout_gross=reward, payout_currency=str(row.get("currency") or "USD"), payout_usd=reward if str(row.get("currency") or "USD").upper() in {"USD", "USDC"} else None, payout_net=reward, funding_status=FundingStatus.UNKNOWN, funding_proof={}, eligibility=str(row.get("eligibility") or "UNKNOWN"), agent_policy=_agent_policy(row.get("agent_policy")), claim_method=str(row.get("claim_method") or "UNKNOWN"), submission_method=str(row.get("submission_method") or "UNKNOWN"), payment_rail=str(row.get("payment_rail") or "UNKNOWN"), payout_latency=str(row.get("payout_latency") or "UNKNOWN"), withdrawal_path=str(row.get("withdrawal_path") or "UNKNOWN"), estimated_execution_minutes=int(row.get("estimated_minutes") or 90), required_capabilities=[str(x) for x in _list(row.get("capabilities"))], source_state_hash=source_hash(row)))
        return out


def build_default_registry(http: JsonHttpClient | None = None) -> RadarRegistry:
    shared = http or JsonHttpClient()
    return RadarRegistry([
        SuperteamEarnSource(shared),
        WorkProtocolRadarSource(shared),
        OpireSource(),
        GitHubDirectBountySource(shared),
        MoltJobsSource(shared),
        NearAgentMarketSource(shared),
        DisabledOrConfigurableSource("algora", 7, "ATM_ALGORA_FEED_URL", reason="official machine-readable feed not configured; legacy adapter remains authoritative"),
        AgentHansaSource(shared),
        BountyBookSource(shared),
        DisabledOrConfigurableSource("0xwork", 10, "ATM_0XWORK_FEED_URL", reason="current public marketplace requires stake/collateral semantics; no zero-spend claim path enabled"),
        DisabledOrConfigurableSource("claw-earn", 11, "ATM_CLAW_EARN_FEED_URL", reason="official feed not yet configured"),
    ])
