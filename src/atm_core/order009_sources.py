from __future__ import annotations

import html
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .radar_sources import _decimal, _dt, _list
from .universal_radar import (
    AgentPolicy,
    FundingStatus,
    JsonHttpClient,
    SourceState,
    SourceUnavailable,
    UniversalOpportunity,
    source_hash,
)


TRASH_TERMS = (
    "referral", "refer a friend", "recruit", "subscribe to", "promote this", "hashtag",
    "follow my", "like my", "fake engagement", "auth test", "test need", "probe need",
)
HUMAN_ONLY_TERMS = ("video testimonial", "phone call", "video call", "on-site", "onsite", "in person", "physical delivery")


def _text(url: str, *, timeout: float = 10) -> str:
    if not url.startswith("https://"):
        raise ValueError("HTTPS required")
    req = urllib.request.Request(url, headers={"Accept": "text/html,application/json", "User-Agent": "ATM-Order009-Radar/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _rows(payload: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            for inner in ("items", "data", "results", "tasks", "gigs", "needs", "postings"):
                nested = value.get(inner)
                if isinstance(nested, list):
                    return [x for x in nested if isinstance(x, dict)]
    for key in ("items", "data", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    return []


def _trash(text: str) -> str | None:
    lowered = re.sub(r"\s+", " ", text.lower())
    if any(term in lowered for term in TRASH_TERMS):
        return "REFERRAL_RECRUITMENT_OR_SPAM"
    if any(term in lowered for term in HUMAN_ONLY_TERMS):
        return "HUMAN_ONLY_LABOR"
    if any(term in lowered for term in ("casino", "gambling", "sportsbook", "trade crypto", "trading signals")):
        return "SPECULATION_OR_GAMBLING"
    return None


def _contract(item: UniversalOpportunity, *, object_type: str = "job", funding_state: str | None = None, evidence_refs: list[str] | None = None) -> UniversalOpportunity:
    mapping = {
        FundingStatus.VERIFIED: "VERIFIED",
        FundingStatus.FUNDED_SIGNAL: "SIGNAL_ONLY",
        FundingStatus.UNVERIFIED: "UNVERIFIED",
        FundingStatus.UNFUNDED: "UNFUNDED_INTENT",
        FundingStatus.UNKNOWN: "UNVERIFIED",
    }
    latest = item.updated_at or item.created_at
    now = datetime.now(timezone.utc)
    freshness = "UNKNOWN"
    if item.deadline and item.deadline.astimezone(timezone.utc) <= now:
        freshness = "EXPIRED"
    elif latest:
        age = (now - latest.astimezone(timezone.utc)).total_seconds()
        freshness = "CURRENT" if age <= 30 * 86400 else "STALE"
    refs = list(dict.fromkeys([item.canonical_url, *(evidence_refs or [])]))
    return item.model_copy(update={
        "source_object_type": object_type,
        "observed_at": now,
        "currency": item.payout_currency,
        "funding_state": funding_state or mapping.get(item.funding_status, "UNVERIFIED"),
        "funding_evidence": [{"type": "source_proof", "keys": sorted(item.funding_proof.keys())[:16]}] if item.funding_proof else [],
        "escrow_or_payment_intent_id": item.escrow.get("tx") or item.escrow.get("tx_hash") or item.escrow.get("id") or item.escrow.get("payment_intent"),
        "submissions": getattr(item, "submissions", None),
        "executor_health": "UNPROBED",
        "freshness_state": freshness,
        "evidence_refs": refs[:8],
    })


class ContractSource:
    """Adds the R2 normalized evidence envelope without forking the core opportunity model."""

    def __init__(self, inner: Any):
        self.inner = inner
        self.name = inner.name
        self.priority = inner.priority

    def discover(self, floor_usd: Decimal) -> list[UniversalOpportunity]:
        return [_contract(item, object_type=getattr(item, "source_object_type", "job")) for item in self.inner.discover(floor_usd)]


class AgentPactSource:
    name = "agentpact"
    priority = 3
    base = "https://api.agentpact.xyz"

    def __init__(self, http: JsonHttpClient | None = None):
        self.http = http or JsonHttpClient()

    def discover(self, floor_usd: Decimal) -> list[UniversalOpportunity]:
        del floor_usd
        last: Exception | None = None
        data: Any = None
        for path in ("/api/needs?status=open&limit=100", "/api/needs"):
            try:
                data = self.http.get_json(self.base + path, timeout=10)
                break
            except Exception as exc:
                last = exc
        if data is None:
            raise SourceUnavailable(self.name, SourceState.DEGRADED, f"public needs discovery unavailable:{type(last).__name__ if last else 'unknown'}")
        out: list[UniversalOpportunity] = []
        for need in _rows(data, "needs"):
            nid = need.get("id") or need.get("needId") or need.get("need_id")
            if not nid:
                continue
            title = str(need.get("title") or "AgentPact need")
            description = str(need.get("descriptionMd") or need.get("description") or "")
            reject = _trash(title + " " + description)
            budget = _decimal(need.get("budgetMax") or need.get("budget_max") or need.get("budget") or need.get("maxBudget"))
            deal = need.get("deal") if isinstance(need.get("deal"), dict) else {}
            payment = need.get("payment") if isinstance(need.get("payment"), dict) else {}
            escrow_tx = str(deal.get("escrowTxHash") or payment.get("escrowTxHash") or need.get("escrowTxHash") or "").strip()
            deal_state = str(deal.get("status") or need.get("dealStatus") or "").upper()
            payment_state = str(payment.get("status") or need.get("paymentStatus") or "").upper()
            verified = bool(escrow_tx) and (deal_state in {"ACCEPTED", "ACTIVE", "FUNDED", "ESCROWED"} or payment_state in {"FUNDED", "ESCROWED", "HELD"})
            policy = AgentPolicy.PROHIBITED if reject else AgentPolicy.AGENT_ALLOWED
            item = UniversalOpportunity(
                source=self.name,
                external_id=str(nid),
                canonical_url="https://agentpact.xyz/needs",
                title=title,
                description=description,
                category=str(need.get("category") or "agent-service"),
                created_at=_dt(need.get("createdAt") or need.get("created_at")),
                updated_at=_dt(need.get("updatedAt") or need.get("updated_at")),
                deadline=_dt(need.get("deadline") or need.get("expiresAt")),
                payout_gross=budget,
                payout_currency="USDC",
                payout_usd=budget if budget > 0 else None,
                fees=Decimal("0"),
                payout_net=budget if verified else Decimal("0"),
                funding_status=FundingStatus.VERIFIED if verified else FundingStatus.UNFUNDED,
                funding_proof={"deal_status": deal_state or None, "payment_status": payment_state or None, "escrowTxHash": escrow_tx or None, "need_is_not_funding": True},
                escrow={"chain": "Base", "asset": "USDC", "tx": escrow_tx or None},
                competition=int(need.get("matchCount") or need.get("proposalCount") or 0),
                claims=int(need.get("proposalCount") or 0),
                eligibility="PUBLIC_NEED_REQUIRES_FUNDED_DEAL",
                agent_policy=policy,
                claim_method="PROPOSE_DEAL_ONLY_AFTER_FUNDED/POLICY/EXECUTOR_GATES",
                submission_method="AgentPact deal milestone delivery",
                payment_rail="Base USDC escrow",
                payout_latency="after delivery verification/settlement",
                withdrawal_path="Base USDC wallet after escrow release",
                human_gate=None,
                estimated_execution_minutes=int(need.get("estimatedMinutes") or 60),
                required_capabilities=[str(x) for x in _list(need.get("tags"))] or [str(need.get("category") or "agent-service")],
                execution_cost_usd=Decimal("0"),
                source_state_hash=source_hash(need),
                policy_rejection=reject,
                fees_state="UNKNOWN" if not verified else "SOURCE_NOT_STATED",
            )
            out.append(_contract(item, object_type="need", funding_state="VERIFIED" if verified else "UNFUNDED_INTENT"))
        return out


class TaskMarketReadOnlySource:
    name = "taskmarket-daydreams"
    priority = 4
    url = "https://api.taskmarket.dev/api/tasks?status=open&limit=50&sort=newest"

    def __init__(self, http: JsonHttpClient | None = None):
        self.http = http or JsonHttpClient()

    @staticmethod
    def _usdc(value: Any) -> Decimal:
        raw = _decimal(value)
        return raw / Decimal("1000000") if raw >= Decimal("10000") else raw

    def discover(self, floor_usd: Decimal) -> list[UniversalOpportunity]:
        del floor_usd
        try:
            data = self.http.get_json(self.url, timeout=10)
        except Exception as exc:
            raise SourceUnavailable(self.name, SourceState.DEGRADED, f"public TaskMarket list unavailable:{type(exc).__name__}") from exc
        out: list[UniversalOpportunity] = []
        for task in _rows(data, "tasks"):
            tid = task.get("id")
            if not tid:
                continue
            reward = self._usdc(task.get("reward"))
            fee_bps = int(task.get("platformFeeBps") or 0)
            net = self._usdc(task.get("netReward")) if task.get("netReward") not in (None, "") else reward * (Decimal("10000") - Decimal(fee_bps)) / Decimal("10000")
            status = str(task.get("status") or "").lower()
            mode = str(task.get("mode") or "bounty").lower()
            stake = bool(task.get("stakeRequired"))
            escrow_tx = str(task.get("escrowTxHash") or "").strip()
            desc = str(task.get("description") or f"TaskMarket task {tid}")
            reject = _trash(desc)
            mutation_cost = Decimal("0.001") if mode in {"pitch", "benchmark", "auction"} else Decimal("0")
            policy = AgentPolicy.PROHIBITED if (reject or stake) else AgentPolicy.AGENT_ALLOWED
            funded = status == "open" and bool(escrow_tx) and reward > 0
            submissions = int(task.get("submissionCount") or 0)
            pitches = int(task.get("pitchCount") or 0)
            item = UniversalOpportunity(
                source=self.name,
                external_id=str(tid),
                canonical_url=f"https://taskmarket.dev/tasks/{urllib.parse.quote(str(tid))}",
                title=desc[:180],
                description=desc,
                category="taskmarket",
                created_at=_dt(task.get("createdAt")),
                updated_at=_dt(task.get("updatedAt")),
                deadline=_dt(task.get("expiryTime")),
                payout_gross=reward,
                payout_currency="USDC",
                payout_usd=reward,
                fees=max(Decimal("0"), reward - net),
                payout_net=net,
                funding_status=FundingStatus.VERIFIED if funded else FundingStatus.UNVERIFIED,
                funding_proof={"escrowTxHash": escrow_tx or None, "status": status, "mode": mode, "platformFeeBps": fee_bps},
                escrow={"chain": "Base", "asset": "USDC", "tx": escrow_tx or None, "status": status},
                competition=submissions + pitches,
                claims=int(task.get("claimCount") or 0),
                eligibility="PUBLIC_DIGITAL_TASK_ZERO_SPEND_ONLY",
                agent_policy=policy,
                claim_method=f"TaskMarket {mode}; paid mutation modes blocked" if mutation_cost else f"TaskMarket {mode} signature path gated",
                submission_method="TaskMarket signed submission; no economic signing from radar",
                payment_rail="Base USDC escrow",
                payout_latency="acceptance/settlement per task state machine",
                withdrawal_path="Base USDC worker wallet after award settlement",
                human_gate="TASKMARKET_SIGNING_CAPABILITY_REQUIRED" if funded and not reject and not stake and mutation_cost == 0 else None,
                estimated_execution_minutes=int(task.get("estimatedMinutes") or 90),
                required_capabilities=[str(x) for x in _list(task.get("tags"))] or ["digital-task"],
                execution_cost_usd=mutation_cost,
                source_state_hash=source_hash(task),
                submissions=submissions,
                award_count=int(task.get("awardCount") or 0),
                stake_required=stake,
                submission_window_open=task.get("submissionWindowOpen"),
                policy_rejection=reject or ("STAKE_REQUIRED" if stake else None),
            )
            out.append(_contract(item, object_type="task", funding_state="VERIFIED" if funded else "UNVERIFIED"))
        return out


class UGigSource:
    name = "ugig"
    priority = 5
    url = "https://ugig.net/api/gigs?status=active&page=1&limit=100&sort=newest"

    def __init__(self, http: JsonHttpClient | None = None):
        self.http = http or JsonHttpClient()

    def discover(self, floor_usd: Decimal) -> list[UniversalOpportunity]:
        del floor_usd
        try:
            data = self.http.get_json(self.url, timeout=10)
        except Exception as exc:
            raise SourceUnavailable(self.name, SourceState.DEGRADED, f"public uGig GET /api/gigs unavailable:{type(exc).__name__}") from exc
        out: list[UniversalOpportunity] = []
        for gig in _rows(data, "gigs"):
            gid = gig.get("id") or gig.get("slug")
            if not gid:
                continue
            title = str(gig.get("title") or "uGig gig")
            desc = str(gig.get("description") or "")
            reject = _trash(title + " " + desc)
            amount = _decimal(gig.get("budget_amount") or gig.get("budget_min") or gig.get("budgetMin") or 0)
            payment_coin = str(gig.get("payment_coin") or gig.get("paymentCoin") or "UNKNOWN")
            policy = AgentPolicy.PROHIBITED if reject and reject != "HUMAN_ONLY_LABOR" else (AgentPolicy.HUMAN_ONLY if reject == "HUMAN_ONLY_LABOR" else AgentPolicy.AGENT_ALLOWED)
            item = UniversalOpportunity(
                source=self.name,
                external_id=str(gid),
                canonical_url=f"https://ugig.net/gigs/{urllib.parse.quote(str(gid))}",
                title=title,
                description=desc,
                category=str(gig.get("category") or "remote-work"),
                created_at=_dt(gig.get("created_at") or gig.get("createdAt")),
                updated_at=_dt(gig.get("updated_at") or gig.get("updatedAt")),
                deadline=_dt(gig.get("deadline") or gig.get("expires_at")),
                payout_gross=amount,
                payout_currency="USD",
                payout_usd=amount if amount > 0 else None,
                fees=Decimal("0"),
                payout_net=Decimal("0"),
                funding_status=FundingStatus.UNVERIFIED,
                funding_proof={"listing_status": gig.get("status"), "public_listing_is_not_funding": True},
                escrow={"status": "UNPROVEN"},
                competition=int(gig.get("application_count") or gig.get("applications_count") or 0),
                claims=int(gig.get("application_count") or gig.get("applications_count") or 0),
                eligibility="FREE_APPLICATION_ALLOWED_BUT_FUNDING_MUST_BE_PROVEN",
                agent_policy=policy,
                claim_method="POST /api/applications only after all ATM gates",
                submission_method="uGig application/messages/invoice workflow",
                payment_rail=payment_coin,
                payout_latency="invoice/payment status after client acceptance",
                withdrawal_path="CoinPay or listed public wallet after invoice payment" if payment_coin.upper() != "UNKNOWN" else "UNKNOWN",
                estimated_execution_minutes=120,
                required_capabilities=[str(x) for x in _list(gig.get("skills_required") or gig.get("skills"))] or [str(gig.get("category") or "remote-work")],
                execution_cost_usd=Decimal("0"),
                source_state_hash=source_hash(gig),
                policy_rejection=reject,
                payout_net_state="UNKNOWN_UNTIL_INVOICE_TERMS",
            )
            out.append(_contract(item, object_type="gig", funding_state="UNVERIFIED"))
        return out


class The402Source:
    name = "the402"
    priority = 6
    url = "https://api.the402.ai/v1/postings?status=open"

    def __init__(self, http: JsonHttpClient | None = None):
        self.http = http or JsonHttpClient()

    def discover(self, floor_usd: Decimal) -> list[UniversalOpportunity]:
        del floor_usd
        try:
            data = self.http.get_json(self.url, timeout=10)
        except Exception as exc:
            raise SourceUnavailable(self.name, SourceState.DEGRADED, f"public GET /v1/postings unavailable:{type(exc).__name__}") from exc
        out: list[UniversalOpportunity] = []
        for post in _rows(data, "postings"):
            pid = post.get("id") or post.get("posting_id")
            if not pid:
                continue
            title = str(post.get("title") or "the402 request")
            desc = str(post.get("description") or post.get("brief") or "")
            reject = _trash(title + " " + desc)
            minimum = _decimal(post.get("budget_min_usd") or post.get("budget_min") or 0)
            maximum = _decimal(post.get("budget_max_usd") or post.get("budget_max") or minimum)
            gross = minimum if minimum > 0 else maximum
            item = UniversalOpportunity(
                source=self.name,
                external_id=str(pid),
                canonical_url=f"https://api.the402.ai/v1/postings/{urllib.parse.quote(str(pid))}",
                title=title,
                description=desc,
                category=str(post.get("category") or "request"),
                created_at=_dt(post.get("created_at")),
                updated_at=_dt(post.get("updated_at")),
                deadline=_dt(post.get("expires_at") or post.get("deadline")),
                payout_gross=gross,
                payout_currency="USDC",
                payout_usd=gross if gross > 0 else None,
                fees=gross * Decimal("0.05"),
                payout_net=gross * Decimal("0.95"),
                funding_status=FundingStatus.UNFUNDED,
                funding_proof={"public_posting": True, "award_not_yet_escrow": True, "budget_max_usd": str(maximum)},
                escrow={"chain": "Base", "asset": "USDC", "status": "NOT_HELD_UNTIL_AWARD"},
                competition=int(post.get("bid_count") or post.get("bids_count") or 0),
                claims=int(post.get("bid_count") or post.get("bids_count") or 0),
                eligibility="PUBLIC_REQUEST; BID_REQUIRES_EXISTING_ZERO_COST_API_KEY_OR_SPEND_BLOCK",
                agent_policy=AgentPolicy.PROHIBITED if reject else AgentPolicy.AGENT_ALLOWED,
                claim_method="X-API-Key bid may be free; x402 $0.001 path forbidden",
                submission_method="job_dispatch callback after award",
                payment_rail="Base USDC escrow after award",
                payout_latency="release after verified delivery",
                withdrawal_path="provider wallet; 95% share after release",
                estimated_execution_minutes=int(post.get("estimated_minutes") or 90),
                required_capabilities=[str(post.get("category") or "request")],
                execution_cost_usd=Decimal("0"),
                source_state_hash=source_hash(post),
                zero_spend_bid_available=bool(os.getenv("THE402_API_KEY", "").strip()),
                prohibited_x402_bid_cost_usd="0.001",
                policy_rejection=reject,
            )
            out.append(_contract(item, object_type="posting", funding_state="UNFUNDED_INTENT"))
        return out


class ClustlySource:
    name = "clustly"
    priority = 7

    def __init__(self, http: JsonHttpClient | None = None):
        self.http = http or JsonHttpClient()

    def discover(self, floor_usd: Decimal) -> list[UniversalOpportunity]:
        del floor_usd
        data: Any = None
        last: Exception | None = None
        for url in ("https://clustly.ai/api/v1/tasks?status=open", "https://www.clustly.ai/api/v1/tasks?status=open"):
            try:
                data = self.http.get_json(url, timeout=10)
                break
            except Exception as exc:
                last = exc
        if data is None:
            raise SourceUnavailable(self.name, SourceState.DEGRADED, f"current task inventory not authoritatively readable:{type(last).__name__ if last else 'unknown'}")
        out: list[UniversalOpportunity] = []
        for task in _rows(data, "tasks"):
            tid = task.get("id") or task.get("task_id")
            if not tid:
                continue
            title = str(task.get("title") or task.get("brief") or f"Clustly task {tid}")
            desc = str(task.get("description") or task.get("brief") or "")
            reject = _trash(title + " " + desc)
            gross = _decimal(task.get("bounty_usdc") or task.get("bounty") or task.get("amount_usdc") or task.get("amount") or 0)
            escrow_status = str(task.get("escrow_status") or task.get("escrowStatus") or "").upper()
            escrow_tx = str(task.get("escrow_tx") or task.get("escrowTxHash") or task.get("tx_hash") or "").strip()
            verified = gross > 0 and escrow_status in {"FUNDED", "LOCKED", "CONFIRMED", "ESCROWED"} and bool(escrow_tx)
            item = UniversalOpportunity(
                source=self.name,
                external_id=str(tid),
                canonical_url="https://www.clustly.ai/",
                title=title,
                description=desc,
                category=str(task.get("category") or "agent-work"),
                created_at=_dt(task.get("created_at") or task.get("createdAt")),
                updated_at=_dt(task.get("updated_at") or task.get("updatedAt")),
                deadline=_dt(task.get("deadline") or task.get("expires_at")),
                payout_gross=gross,
                payout_currency="USDC",
                payout_usd=gross if gross > 0 else None,
                fees=gross * Decimal("0.04"),
                payout_net=gross * Decimal("0.96"),
                funding_status=FundingStatus.VERIFIED if verified else FundingStatus.UNVERIFIED,
                funding_proof={"escrow_status": escrow_status or None, "escrow_tx": escrow_tx or None},
                escrow={"chain": str(task.get("chain") or "Solana"), "asset": "USDC", "status": escrow_status, "tx": escrow_tx or None},
                competition=int(task.get("claim_count") or task.get("proposal_count") or 0),
                claims=int(task.get("claim_count") or 0),
                eligibility="AGENT_NATIVE_GAS_SPONSORED_CLAIM_REQUIRES_CURRENT_AGENT_ID",
                agent_policy=AgentPolicy.PROHIBITED if reject else AgentPolicy.AGENT_ALLOWED,
                claim_method="agent claim after funding/policy/executor gates",
                submission_method="agent deliverable + poster approval",
                payment_rail="USDC escrow; Solana primary/Base legacy",
                payout_latency="approval then escrow release",
                withdrawal_path="agent treasury wallet",
                estimated_execution_minutes=int(task.get("estimated_minutes") or 90),
                required_capabilities=[str(task.get("category") or "agent-work")],
                execution_cost_usd=Decimal("0"),
                source_state_hash=source_hash(task),
                policy_rejection=reject,
            )
            out.append(_contract(item, object_type="task", funding_state="VERIFIED" if verified else "UNVERIFIED"))
        return out


class VerifyQueueSource:
    """Current surface probe that deliberately creates no money object from HTTP liveness."""

    def __init__(self, name: str, priority: int, url: str, detail: str):
        self.name, self.priority, self.url, self.detail = name, priority, url, detail

    def discover(self, floor_usd: Decimal) -> list[UniversalOpportunity]:
        del floor_usd
        try:
            _text(self.url, timeout=8)
        except Exception as exc:
            raise SourceUnavailable(self.name, SourceState.DEGRADED, f"{self.detail}; surface read failed:{type(exc).__name__}") from exc
        raise SourceUnavailable(self.name, SourceState.DEGRADED, self.detail)


RAIL_CAPABILITIES = [
    {"source": "agentictrade", "kind": "SELL_SERVICE_RAIL", "job_counted": False, "outgoing_spend_usd": "0", "autonomous_buying": False},
    {"source": "clustly-sell-service", "kind": "SELL_SERVICE_OR_WORK_RAIL", "job_counted": False, "outgoing_spend_usd": "0", "autonomous_buying": False},
    {"source": "circle-agent-marketplace", "kind": "SELL_SERVICE_DISCOVERY_VERIFY_QUEUE", "job_counted": False, "outgoing_spend_usd": "0", "autonomous_buying": False},
    {"source": "agoragentic", "kind": "SELL_SERVICE_RAIL_NOT_JOB_RADAR", "job_counted": False, "outgoing_spend_usd": "0", "autonomous_buying": False},
    {"source": "coinpayportal", "kind": "PAYMENT_RAIL_X402_RECEIVING", "job_counted": False, "outgoing_spend_usd": "0", "autonomous_buying": False},
    {"source": "execution-market", "kind": "PHYSICAL_EXECUTION_NOT_PRIMARY_DIGITAL_WORKER", "job_counted": False, "outgoing_spend_usd": "0", "autonomous_buying": False},
    {"source": "gigs.sh", "kind": "META_DISCOVERY_ONLY", "job_counted": False, "outgoing_spend_usd": "0", "authoritative_funding": False},
]


def order009_sources(http: JsonHttpClient | None = None) -> list[Any]:
    shared = http or JsonHttpClient()
    return [
        AgentPactSource(shared),
        TaskMarketReadOnlySource(shared),
        UGigSource(shared),
        The402Source(shared),
        ClustlySource(shared),
        VerifyQueueSource("toku", 17, "https://www.toku.agency/jobs", "TRUTHFUL_VERIFY_QUEUE: open listing/bid price is not buyer funding; competition must be penalized"),
        VerifyQueueSource("agenthire-verify", 18, "https://agenthire.ai/", "JOB_DISCOVERY_BLOCKED_ZERO_SPEND until a free authoritative current job API is proven"),
        VerifyQueueSource("runtime-open-federation-verify", 19, "https://runtime.dev/", "VERIFY_QUEUE_ONLY: no funded job object proven"),
        VerifyQueueSource("boss-dev-watch", 20, "https://boss.dev/", "GITHUB_BOUNTY_WATCH: ordinary issues are not bounties"),
        VerifyQueueSource("dework-watch", 21, "https://dework.xyz/", "WEB3_BOUNTY_WATCH: payout/funding eligibility must be source-specific"),
        VerifyQueueSource("drips-wave-watch", 22, "https://www.drips.network/", "RECURRING_OSS_BOUNTY_WATCH: no generic HTTP-to-money promotion"),
        VerifyQueueSource("stacker-news-meta", 23, "https://stacker.news/", "LONG_TAIL_BOUNTY_META_DISCOVERY_ONLY"),
    ]
