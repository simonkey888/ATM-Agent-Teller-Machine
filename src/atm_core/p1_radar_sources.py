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
from .universal_radar import AgentPolicy, FundingStatus, SourceState, SourceUnavailable, UniversalOpportunity, source_hash


def _get_text(url: str, *, timeout: float = 10) -> str:
    if not url.startswith("https://"):
        raise ValueError("HTTPS required")
    req = urllib.request.Request(url, headers={"Accept": "text/html,application/json", "User-Agent": "ATM-Universal-Radar/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


class AlgoraSource:
    """Read-only scanner over Algora's official per-organization bounty pages.

    Algora exposes bounties per organization. A public page proves an advertised
    bounty, but not by itself a withdrawable payment for ATM, so funding remains an
    external signal until the linked GitHub issue/project payout terms are verified.
    """

    name = "algora"
    priority = 7
    DEFAULT_ORGS = ("projectdiscovery", "spaceandtimelabs", "getkyo", "algora")

    def __init__(self, orgs: tuple[str, ...] | None = None):
        configured = tuple(x.strip() for x in os.getenv("ATM_ALGORA_ORGS", "").split(",") if x.strip())
        self.orgs = orgs or configured or self.DEFAULT_ORGS

    @staticmethod
    def _parse(org: str, text: str) -> list[UniversalOpportunity]:
        # The official page renders rows as "$100 repo#123 Title ... N claims".
        # Keep parsing conservative: if canonical repo/issue identity is absent, do not
        # create an economic candidate.
        plain = _clean_text(text)
        pattern = re.compile(
            r"\$([0-9][0-9,]*(?:\.[0-9]+)?)\s+([A-Za-z0-9_.-]+)#(\d+)\s+(.{3,220}?)(?:\s+\d+\s+(?:minute|minutes|hour|hours|day|days|week|weeks|month|months|year|years)\s+ago|\s+(\d+)\s+claims)",
            re.I,
        )
        out: list[UniversalOpportunity] = []
        seen: set[str] = set()
        for match in pattern.finditer(plain):
            amount = _decimal(match.group(1).replace(",", ""))
            repo = match.group(2)
            issue = match.group(3)
            title = match.group(4).strip(" |-")
            claims = int(match.group(5) or 0)
            external_id = f"{org}/{repo}#{issue}"
            if external_id in seen:
                continue
            seen.add(external_id)
            canonical = f"https://github.com/{org}/{repo}/issues/{issue}"
            page = f"https://algora.io/{urllib.parse.quote(org)}/bounties?status=open"
            out.append(
                UniversalOpportunity(
                    source="algora",
                    external_id=external_id,
                    canonical_url=canonical,
                    title=title or f"Algora bounty {repo}#{issue}",
                    description=f"Algora official open-bounty listing for {repo}#{issue}.",
                    category="github",
                    payout_gross=amount,
                    payout_currency="USD",
                    payout_usd=amount,
                    payout_net=amount,
                    funding_status=FundingStatus.FUNDED_SIGNAL,
                    funding_proof={"official_algora_page": page, "advertised_bounty_usd": str(amount)},
                    competition=claims,
                    claims=claims,
                    eligibility="PROJECT_CONTRIBUTION_TERMS_REQUIRED",
                    agent_policy=AgentPolicy.UNKNOWN,
                    claim_method="Algora/GitHub project-specific bounty claim",
                    submission_method="GitHub pull request + Algora claim if project permits",
                    payment_rail="Stripe via Algora",
                    payout_latency="after maintainer acceptance/payment",
                    withdrawal_path="Stripe Connect payout onboarding",
                    human_gate="ALGORA_STRIPE_PAYOUT_ONBOARDING",
                    estimated_execution_minutes=180,
                    required_capabilities=["github", "code"],
                    execution_cost_usd=Decimal("0"),
                    source_state_hash=source_hash({"org": org, "repo": repo, "issue": issue, "amount": str(amount), "claims": claims, "page": page}),
                )
            )
        return out

    def discover(self, floor_usd: Decimal) -> list[UniversalOpportunity]:
        rows: list[UniversalOpportunity] = []
        successful = 0
        for org in self.orgs:
            try:
                page = f"https://algora.io/{urllib.parse.quote(org)}/bounties?status=open"
                text = _get_text(page, timeout=8)
                successful += 1
                rows.extend(self._parse(org, text))
            except Exception:
                continue
        if successful == 0:
            raise SourceUnavailable(self.name, SourceState.DEGRADED, "all official Algora organization reads failed")
        return rows


class ZeroXWorkSource:
    """Official public 0xWork task-board radar; never stakes or registers.

    Current protocol participation requires an on-chain registration/stake and task
    claims stake AXOBOTL. ATM's zero-spend constitution rejects the claim path even if
    a faucet could sponsor tokens/gas. Discovery remains useful and read-only.
    """

    name = "0xwork"
    priority = 10
    board_url = "https://www.0xwork.org/tasks"

    @staticmethod
    def _parse(text: str) -> list[UniversalOpportunity]:
        plain = _clean_text(text)
        # Prefer explicit task links from rendered/RSC HTML. A task can appear more than
        # once, so canonical task id deduplication is mandatory.
        ids = list(dict.fromkeys(re.findall(r'(?:href=["\']/tasks/|/tasks/)(\d+)', text, re.I)))
        if not ids and re.search(r"\b0\s+tasks\b", plain, re.I):
            return []
        out: list[UniversalOpportunity] = []
        for task_id in ids[:100]:
            # Extract a bounded neighborhood only; if payout cannot be read, preserve
            # the task with UNKNOWN funding rather than inventing an amount.
            pos = text.find(f"/tasks/{task_id}")
            window = _clean_text(text[max(0, pos - 800): pos + 2500]) if pos >= 0 else ""
            amount_match = re.search(r"\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)", window)
            amount = _decimal(amount_match.group(1).replace(",", "")) if amount_match else Decimal("0")
            title_match = re.search(r"(?:Task\s*#?" + re.escape(task_id) + r"[:\s-]*)(.{3,180}?)(?:Bounty|Deadline|Poster|Timeline|\$)", window, re.I)
            title = title_match.group(1).strip() if title_match else f"0xWork task #{task_id}"
            out.append(
                UniversalOpportunity(
                    source="0xwork",
                    external_id=task_id,
                    canonical_url=f"https://www.0xwork.org/tasks/{task_id}",
                    title=title,
                    description="Public 0xWork task. Task content remains untrusted until detail verification.",
                    category="agent-work",
                    payout_gross=amount,
                    payout_currency="USDC",
                    payout_usd=amount if amount > 0 else None,
                    fees=amount * Decimal("0.05") if amount > 0 else Decimal("0"),
                    payout_net=amount * Decimal("0.95") if amount > 0 else Decimal("0"),
                    funding_status=FundingStatus.FUNDED_SIGNAL if amount > 0 else FundingStatus.UNKNOWN,
                    funding_proof={"official_0xwork_task_url": f"https://www.0xwork.org/tasks/{task_id}"},
                    escrow={"chain": "Base", "asset": "USDC"},
                    eligibility="AGENT_REGISTRATION_AND_COLLATERAL_REQUIRED",
                    agent_policy=AgentPolicy.AGENT_ALLOWED,
                    claim_method="0xWork claim requires AXOBOTL stake",
                    submission_method="0xWork submit with on-chain proof",
                    payment_rail="Base USDC escrow",
                    payout_latency="instant on approval; timeout rules platform-defined",
                    withdrawal_path="Base USDC wallet",
                    human_gate="ZERO_SPEND_BLOCK_AXOBOTL_STAKE_REQUIRED",
                    estimated_execution_minutes=90,
                    required_capabilities=["agent-work"],
                    execution_cost_usd=Decimal("0"),
                    source_state_hash=source_hash({"task_id": task_id, "amount": str(amount), "board": ZeroXWorkSource.board_url}),
                )
            )
        return out

    def discover(self, floor_usd: Decimal) -> list[UniversalOpportunity]:
        try:
            return self._parse(_get_text(self.board_url, timeout=10))
        except Exception as exc:
            raise SourceUnavailable(self.name, SourceState.DEGRADED, f"official 0xWork task board unavailable:{type(exc).__name__}") from exc


class ClawEarnSource:
    """Official Claw Earn public task API, read-only under zero-spend mode."""

    name = "claw-earn"
    priority = 11
    base_url = "https://aiagentstore.ai"

    @staticmethod
    def _rows(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ("tasks", "data", "items", "bounties"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
            if isinstance(value, dict):
                for inner in ("tasks", "items", "data"):
                    nested = value.get(inner)
                    if isinstance(nested, list):
                        return [x for x in nested if isinstance(x, dict)]
        return []

    @staticmethod
    def _normalize(task: dict[str, Any]) -> UniversalOpportunity | None:
        task_id = task.get("taskId") or task.get("id") or task.get("task_id")
        contract = str(task.get("contractAddress") or task.get("contract") or task.get("contract_address") or "").strip()
        if task_id in (None, ""):
            return None
        ext_id = f"{contract.lower()}:{task_id}" if contract else str(task_id)
        amount = _decimal(task.get("amountUsdc") or task.get("taskAmount") or task.get("amount") or task.get("bounty") or task.get("reward"))
        status_raw = str(task.get("status") or task.get("statusName") or task.get("workflowStatus") or "FUNDED").upper()
        funded = status_raw in {"0", "FUNDED", "OPEN", "AVAILABLE"}
        title = str(task.get("title") or task.get("metadata", {}).get("title") if isinstance(task.get("metadata"), dict) else task.get("title") or f"Claw Earn task {task_id}")
        description = str(task.get("description") or (task.get("metadata") or {}).get("description") if isinstance(task.get("metadata"), dict) else task.get("description") or "")
        category = str(task.get("category") or (task.get("metadata") or {}).get("category") if isinstance(task.get("metadata"), dict) else task.get("category") or "agent-work")
        deadline = _dt(task.get("deadline") or task.get("submitDeadline") or task.get("expiresAt"))
        fee = amount * Decimal("0.10") if amount > 0 else Decimal("0")
        task_url = f"https://aiagentstore.ai/claw-earn/task/{contract}_{task_id}" if contract else "https://aiagentstore.ai/claw-earn/ai-agent-tasks/available"
        return UniversalOpportunity(
            source="claw-earn",
            external_id=ext_id,
            canonical_url=task_url,
            title=title,
            description=description,
            category=category,
            created_at=_dt(task.get("createdAt") or task.get("created_at")),
            updated_at=_dt(task.get("updatedAt") or task.get("updated_at")),
            deadline=deadline,
            payout_gross=amount,
            payout_currency="USDC",
            payout_usd=amount if amount > 0 else None,
            fees=fee,
            payout_net=max(Decimal("0"), amount - fee),
            funding_status=FundingStatus.VERIFIED if funded and amount > 0 and bool(contract) else (FundingStatus.FUNDED_SIGNAL if funded and amount > 0 else FundingStatus.UNKNOWN),
            funding_proof={"official_claw_status": status_raw, "contractAddress": contract or None, "taskId": str(task_id)},
            escrow={"chain": "Base", "asset": "USDC", "status": status_raw, "contract": contract or None},
            competition=int(task.get("interestCount") or task.get("workerInterestCount") or 0),
            claims=int(task.get("interestCount") or task.get("workerInterestCount") or 0),
            eligibility="PERMISSIONLESS_AGENT_WORKER_BUT_STAKE_REQUIRED",
            agent_policy=AgentPolicy.AGENT_ALLOWED,
            claim_method="Claw /agentStakeAndConfirm requires worker stake + local wallet signing",
            submission_method="Claw /agentSubmitWork prepare -> local sign/send -> confirm",
            payment_rail="Base USDC non-custodial escrow",
            payout_latency="approval/auto-approval per Claw state machine",
            withdrawal_path="Base USDC wallet after escrow release",
            human_gate="ZERO_SPEND_BLOCK_WORKER_STAKE_REQUIRED",
            estimated_execution_minutes=int(task.get("estimatedMinutes") or 90),
            required_capabilities=[str(x) for x in _list(task.get("tags") or task.get("skills"))] or [category],
            execution_cost_usd=Decimal("0"),
            source_state_hash=source_hash(task),
        )

    def discover(self, floor_usd: Decimal) -> list[UniversalOpportunity]:
        try:
            raw = _get_text(f"{self.base_url}/claw/tasks", timeout=10)
            payload = json.loads(raw)
        except Exception as exc:
            raise SourceUnavailable(self.name, SourceState.DEGRADED, f"official Claw Earn GET /claw/tasks unavailable:{type(exc).__name__}") from exc
        out: list[UniversalOpportunity] = []
        for task in self._rows(payload):
            normalized = self._normalize(task)
            if normalized is not None:
                out.append(normalized)
        return out
