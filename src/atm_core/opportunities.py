from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from .models import HumanGate, Opportunity, Phase
from .payments import HttpJsonClient, PaymentAdapter, WorkProtocolPaymentAdapter, TaskmarketPaymentAdapter, AlgoraPaymentAdapter
from .security import assert_external_task_safe


class OpportunityValidationError(ValueError):
    pass


class HumanGateRequired(RuntimeError):
    def __init__(self, gate: HumanGate):
        super().__init__(gate.reason)
        self.gate = gate


ATM_ROOT = Path(__file__).resolve().parents[2]
ATM_SECRETS = ATM_ROOT / ".atm" / "secrets.env"


def local_atm_value(name: str) -> str | None:
    """Read ATM rail credentials without placing them in the worker environment."""
    if ATM_SECRETS.exists():
        prefix = name + "="
        for line in ATM_SECRETS.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if stripped.startswith(prefix):
                value = stripped.split("=", 1)[1].strip()
                if value:
                    return value
    # Backward-compatible fallback for explicitly supplied process env. run_agent scrubs it from worker children.
    value = os.getenv(name)
    return value.strip() if value and value.strip() else None


def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def external_state_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class OpportunityAdapter(ABC):
    name: str

    @abstractmethod
    def discover(self, min_reward_usd: Decimal) -> list[Opportunity]: ...

    @abstractmethod
    def fetch_authoritative(self, opportunity: Opportunity) -> dict[str, Any]: ...

    @abstractmethod
    def verify_freshness(self, opportunity: Opportunity, snapshot: dict[str, Any]) -> None: ...

    @abstractmethod
    def verify_funding(self, opportunity: Opportunity, snapshot: dict[str, Any]) -> None: ...

    @abstractmethod
    def verify_eligibility(self, opportunity: Opportunity, snapshot: dict[str, Any]) -> None: ...

    @abstractmethod
    def inspect_competition(self, opportunity: Opportunity, snapshot: dict[str, Any]) -> dict[str, int]: ...

    @abstractmethod
    def claim(self, opportunity: Opportunity) -> dict[str, Any]: ...

    @abstractmethod
    def submit(self, opportunity: Opportunity, deliverable_url: str) -> dict[str, Any]: ...

    @abstractmethod
    def monitor(self, opportunity: Opportunity) -> dict[str, Any]: ...

    @abstractmethod
    def fetch_payment(self, opportunity: Opportunity, expected_recipient: str): ...


class WorkProtocolOpportunityAdapter(OpportunityAdapter):
    name = "workprotocol"

    def __init__(self, base_url: str = "https://workprotocol.ai", http: HttpJsonClient | None = None):
        self.base_url = base_url.rstrip("/")
        self.http = http or HttpJsonClient()
        self.payment_adapter: PaymentAdapter = WorkProtocolPaymentAdapter(base_url=self.base_url, http=self.http)

    def discover(self, min_reward_usd: Decimal) -> list[Opportunity]:
        query = urllib.parse.urlencode(
            {"status": "open", "category": "code", "min_pay": str(min_reward_usd), "sort": "pay_desc", "limit": 50}
        )
        data = self.http.get(f"{self.base_url}/api/jobs?{query}")
        result: list[Opportunity] = []
        for job in data.get("jobs", []):
            description = "\n".join([str(job.get("title", "")), str(job.get("description", "")), json.dumps(job.get("requirements", {}))])
            assert_external_task_safe(description)
            reward = Decimal(str(job.get("paymentAmount") or "0"))
            if reward < min_reward_usd:
                continue
            claims = job.get("claims") or []
            result.append(
                Opportunity(
                    canonical_opportunity_id=f"workprotocol:{job['id']}",
                    source=self.name,
                    authoritative_url=f"{self.base_url}/jobs/{job['id']}",
                    upstream_status=str(job.get("status", "unknown")),
                    created_at=_dt(job.get("createdAt")),
                    updated_at=_dt(job.get("updatedAt")),
                    deadline=_dt(job.get("deadline")),
                    reward_gross=reward,
                    expected_fees=reward * Decimal("0.05"),
                    funding_proof={"platform": self.name, "claim": "job creation locks escrow", "escrow_status": job.get("escrowStatus")},
                    competition=len(claims) if isinstance(claims, list) else int(job.get("claimCount") or 0),
                    claims=len(claims) if isinstance(claims, list) else int(job.get("claimCount") or 0),
                    ai_policy="AGENT_NATIVE",
                    eligibility="PUBLIC_AGENT",
                    claim_method="POST /api/jobs/:id/claim",
                    submission_method="POST /api/jobs/:id/deliver",
                    payment_method=str(job.get("paymentRail") or "base") + ":" + str(job.get("paymentCurrency") or "USDC"),
                    payout_latency=f"verification window {job.get('verificationWindowHours', 'unknown')}h",
                    payment_proof_method="platform payments + settlementTxHash + Base receipt",
                    external_state_hash=external_state_hash(job),
                )
            )
        return result

    def fetch_authoritative(self, opportunity: Opportunity) -> dict[str, Any]:
        job_id = opportunity.canonical_opportunity_id.split(":", 1)[1]
        return self.http.get(f"{self.base_url}/api/jobs/{urllib.parse.quote(job_id)}")

    def verify_freshness(self, opportunity: Opportunity, snapshot: dict[str, Any]) -> None:
        job = snapshot.get("job") or snapshot
        if str(job.get("status", "")).lower() != "open":
            raise OpportunityValidationError("WorkProtocol job is not open")
        deadline = _dt(job.get("deadline"))
        if deadline and deadline <= datetime.now(timezone.utc):
            raise OpportunityValidationError("WorkProtocol job deadline passed")

    def verify_funding(self, opportunity: Opportunity, snapshot: dict[str, Any]) -> None:
        job = snapshot.get("job") or snapshot
        reward = Decimal(str(job.get("paymentAmount") or "0"))
        if reward <= 0:
            raise OpportunityValidationError("WorkProtocol job has no positive payment")
        # Protocol contract: an API job is created only after escrow lock. Prefer explicit per-job evidence when present.
        escrow_status = str(job.get("escrowStatus") or "").lower()
        payments = snapshot.get("payments") or []
        has_explicit_lock = escrow_status in {"funded", "locked", "escrowed"} or any(
            str(p.get("status") or p.get("escrowStatus") or "").lower() in {"funded", "locked", "escrowed"}
            or p.get("escrowTxHash")
            for p in payments
        )
        if not has_explicit_lock:
            # Fail closed for economic work: docs alone are not per-job funding proof.
            raise OpportunityValidationError("WorkProtocol job lacks explicit per-job escrow evidence")

    def verify_eligibility(self, opportunity: Opportunity, snapshot: dict[str, Any]) -> None:
        job = snapshot.get("job") or snapshot
        external_text = "\n".join(
            [str(job.get("title", "")), str(job.get("description", "")), json.dumps(job.get("requirements", {}))]
        )
        assert_external_task_safe(external_text)
        min_rep = Decimal(str(job.get("minReputation") or "0"))
        if min_rep > 0 and not local_atm_value("WORKPROTOCOL_AGENT_ID"):
            raise OpportunityValidationError("agent reputation cannot be verified without WORKPROTOCOL_AGENT_ID")

    def inspect_competition(self, opportunity: Opportunity, snapshot: dict[str, Any]) -> dict[str, int]:
        claims = snapshot.get("claims") or []
        return {"claims": len(claims), "open_prs": 0}

    def _auth(self) -> tuple[str, str]:
        api_key = local_atm_value("WORKPROTOCOL_API_KEY")
        agent_id = local_atm_value("WORKPROTOCOL_AGENT_ID")
        if not api_key or not agent_id:
            raise HumanGateRequired(
                HumanGate(
                    kind="ACCOUNT_ONBOARDING",
                    reason="WorkProtocol claim needs one-time agent registration/API key and agent id",
                    exact_human_action="Register ATM once on WorkProtocol and add WORKPROTOCOL_API_KEY + WORKPROTOCOL_AGENT_ID to .atm/secrets.env",
                    resume_phase=Phase.CLAIM,
                )
            )
        return api_key, agent_id

    def claim(self, opportunity: Opportunity) -> dict[str, Any]:
        api_key, agent_id = self._auth()
        job_id = opportunity.canonical_opportunity_id.split(":", 1)[1]
        # Crash/retry safety: authoritative re-read before writing, and reuse our existing claim if present.
        snapshot = self.http.get(f"{self.base_url}/api/jobs/{urllib.parse.quote(job_id)}")
        for claim in snapshot.get("claims") or []:
            if str(claim.get("agentId") or claim.get("agent_id") or "") == agent_id and claim.get("id"):
                return {"claim": claim, "reused": True}
        return self.http.post_json(
            f"{self.base_url}/api/jobs/{urllib.parse.quote(job_id)}/claim",
            {"agentId": agent_id},
            {"Authorization": f"Bearer {api_key}"},
        )

    def submit(self, opportunity: Opportunity, deliverable_url: str) -> dict[str, Any]:
        api_key, _ = self._auth()
        job_id = opportunity.canonical_opportunity_id.split(":", 1)[1]
        if not opportunity.claim_id:
            raise OpportunityValidationError("WorkProtocol submission requires claim_id")
        return self.http.post_json(
            f"{self.base_url}/api/jobs/{urllib.parse.quote(job_id)}/deliver",
            {"claimId": opportunity.claim_id, "deliverable": {"type": "url", "url": deliverable_url}},
            {"Authorization": f"Bearer {api_key}"},
        )

    def monitor(self, opportunity: Opportunity) -> dict[str, Any]:
        return self.fetch_authoritative(opportunity)

    def fetch_payment(self, opportunity: Opportunity, expected_recipient: str):
        job_id = opportunity.canonical_opportunity_id.split(":", 1)[1]
        return self.payment_adapter.fetch_payment(job_id, expected_recipient)


class TaskmarketOpportunityAdapter(OpportunityAdapter):
    name = "taskmarket"

    def __init__(self, base_url: str = "https://api.taskmarket.dev", http: HttpJsonClient | None = None):
        self.base_url = base_url.rstrip("/")
        self.http = http or HttpJsonClient()
        self.payment_adapter: PaymentAdapter = TaskmarketPaymentAdapter(base_url=self.base_url, http=self.http)

    def discover(self, min_reward_usd: Decimal) -> list[Opportunity]:
        min_base = int(min_reward_usd * Decimal(1_000_000))
        query = urllib.parse.urlencode({"status": "open", "minReward": str(min_base), "sort": "reward_desc", "limit": 50})
        data = self.http.get(f"{self.base_url}/api/tasks?{query}")
        result: list[Opportunity] = []
        for task in data.get("tasks", []):
            assert_external_task_safe(str(task.get("description", "")))
            if bool(task.get("stakeRequired")):
                continue
            reward = Decimal(str(task.get("reward") or "0")) / Decimal(1_000_000)
            if reward < min_reward_usd:
                continue
            submissions = int(task.get("submissionCount") or task.get("submissionsCount") or 0)
            result.append(
                Opportunity(
                    canonical_opportunity_id=f"taskmarket:{task['id'] if 'id' in task else task.get('taskId')}",
                    source=self.name,
                    authoritative_url=f"https://taskmarket.dev/tasks/{task['id'] if 'id' in task else task.get('taskId')}",
                    upstream_status=str(task.get("status", "unknown")),
                    created_at=_dt(task.get("createdAt")),
                    updated_at=_dt(task.get("updatedAt")),
                    deadline=_dt(task.get("expiryTime") or task.get("deadline")),
                    reward_gross=reward,
                    expected_fees=Decimal("0"),
                    funding_proof={"escrow": task.get("escrowTxHash") or task.get("fundingTxHash"), "reward_base_units": task.get("reward")},
                    competition=submissions,
                    claims=1 if task.get("claimedBy") else 0,
                    ai_policy="UNKNOWN",
                    eligibility="PUBLIC_WITH_WALLET_SIGNATURE",
                    claim_method="EIP-191 signature or bounty submission",
                    submission_method="POST /api/tasks/:id/submissions with EIP-191 signature",
                    payment_method="USDC Base award settlement",
                    payout_latency="on settlement",
                    payment_proof_method="TaskDetailResponse.awards + settlementTxHash + Base receipt",
                    external_state_hash=external_state_hash(task),
                )
            )
        return result

    def fetch_authoritative(self, opportunity: Opportunity) -> dict[str, Any]:
        task_id = opportunity.canonical_opportunity_id.split(":", 1)[1]
        return self.http.get(f"{self.base_url}/api/tasks/{urllib.parse.quote(task_id)}")

    def verify_freshness(self, opportunity: Opportunity, snapshot: dict[str, Any]) -> None:
        if str(snapshot.get("status", "")).lower() != "open":
            raise OpportunityValidationError("Taskmarket task is not open")
        deadline = _dt(snapshot.get("expiryTime") or snapshot.get("deadline"))
        if deadline and deadline <= datetime.now(timezone.utc):
            raise OpportunityValidationError("Taskmarket task expired")

    def verify_funding(self, opportunity: Opportunity, snapshot: dict[str, Any]) -> None:
        reward = Decimal(str(snapshot.get("reward") or "0"))
        if reward <= 0:
            raise OpportunityValidationError("Taskmarket task reward is zero")
        # Task creation is x402-funded escrow, but require an explicit onchain/funding identifier for ATM auto-work.
        if not (snapshot.get("escrowTxHash") or snapshot.get("fundingTxHash") or snapshot.get("createTxHash")):
            raise OpportunityValidationError("Taskmarket task lacks explicit escrow/funding tx")

    def verify_eligibility(self, opportunity: Opportunity, snapshot: dict[str, Any]) -> None:
        assert_external_task_safe("\n".join([str(snapshot.get("title", "")), str(snapshot.get("description", ""))]))
        if bool(snapshot.get("stakeRequired")):
            raise OpportunityValidationError("Taskmarket stakeRequired=true is prohibited")

    def inspect_competition(self, opportunity: Opportunity, snapshot: dict[str, Any]) -> dict[str, int]:
        submissions = snapshot.get("submissions") or []
        return {
            "claims": 1 if snapshot.get("claimedBy") else 0,
            "open_prs": len(submissions) if isinstance(submissions, list) else int(snapshot.get("submissionCount") or 0),
        }

    def claim(self, opportunity: Opportunity) -> dict[str, Any]:
        raise HumanGateRequired(
            HumanGate(
                kind="SIGNATURE",
                reason="Taskmarket claim/submission requires EIP-191 wallet signature; ATM never stores private keys",
                exact_human_action="Use a separately controlled signer to authorize the taskmarket claim without exposing the private key to ATM",
                resume_phase=Phase.CLAIM,
                opportunity_id=opportunity.canonical_opportunity_id,
            )
        )

    def submit(self, opportunity: Opportunity, deliverable_url: str) -> dict[str, Any]:
        raise HumanGateRequired(
            HumanGate(
                kind="SIGNATURE",
                reason="Taskmarket submission requires an EIP-191 signature",
                exact_human_action="Authorize the Taskmarket submission with a separately controlled signer",
                resume_phase=Phase.SUBMIT,
                opportunity_id=opportunity.canonical_opportunity_id,
            )
        )

    def monitor(self, opportunity: Opportunity) -> dict[str, Any]:
        return self.fetch_authoritative(opportunity)

    def fetch_payment(self, opportunity: Opportunity, expected_recipient: str):
        task_id = opportunity.canonical_opportunity_id.split(":", 1)[1]
        return self.payment_adapter.fetch_payment(task_id, expected_recipient)


class WatchOnlyOpportunityAdapter(OpportunityAdapter):
    """Deterministic placeholder for rails lacking a verified write/payment API contract.

    It is intentionally incapable of CLAIM/SUBMIT so a web listing can never silently
    become economic authority.
    """

    def __init__(self, name: str, discovery_url: str, payment_adapter: PaymentAdapter | None = None):
        self.name = name
        self.discovery_url = discovery_url
        self.payment_adapter = payment_adapter

    def discover(self, min_reward_usd: Decimal) -> list[Opportunity]:
        return []

    def fetch_authoritative(self, opportunity: Opportunity) -> dict[str, Any]:
        raise OpportunityValidationError(f"{self.name} is WATCH_ONLY")

    def verify_freshness(self, opportunity: Opportunity, snapshot: dict[str, Any]) -> None:
        raise OpportunityValidationError(f"{self.name} is WATCH_ONLY")

    verify_funding = verify_freshness
    verify_eligibility = verify_freshness

    def inspect_competition(self, opportunity: Opportunity, snapshot: dict[str, Any]) -> dict[str, int]:
        return {"claims": 0, "open_prs": 0}

    def claim(self, opportunity: Opportunity) -> dict[str, Any]:
        raise OpportunityValidationError(f"{self.name} is WATCH_ONLY")

    def submit(self, opportunity: Opportunity, deliverable_url: str) -> dict[str, Any]:
        raise OpportunityValidationError(f"{self.name} is WATCH_ONLY")

    def monitor(self, opportunity: Opportunity) -> dict[str, Any]:
        raise OpportunityValidationError(f"{self.name} is WATCH_ONLY")

    def fetch_payment(self, opportunity: Opportunity, expected_recipient: str):
        if not self.payment_adapter:
            raise OpportunityValidationError(f"{self.name} has no authoritative payment adapter")
        external_id = opportunity.canonical_opportunity_id.split(":", 1)[1]
        return self.payment_adapter.fetch_payment(external_id, expected_recipient)


def build_adapters(config: dict[str, Any]) -> dict[str, OpportunityAdapter]:
    adapters: dict[str, OpportunityAdapter] = {}
    adapter_cfg = config.get("opportunity_adapters", {})
    if adapter_cfg.get("workprotocol", {}).get("enabled", True):
        adapters["workprotocol"] = WorkProtocolOpportunityAdapter(
            base_url=adapter_cfg.get("workprotocol", {}).get("base_url", "https://workprotocol.ai")
        )
    if adapter_cfg.get("taskmarket", {}).get("enabled", True):
        adapters["taskmarket"] = TaskmarketOpportunityAdapter(
            base_url=adapter_cfg.get("taskmarket", {}).get("base_url", "https://api.taskmarket.dev")
        )
    # Algora board/SDK is discovery context, not payment truth; keep writes fail-closed until payout API is configured.
    algora_cfg = adapter_cfg.get("algora", {})
    adapters["algora"] = WatchOnlyOpportunityAdapter(
        "algora",
        "https://algora.io/bounties",
        AlgoraPaymentAdapter(payout_api_template=algora_cfg.get("payout_api_template")),
    )
    adapters["opire"] = WatchOnlyOpportunityAdapter("opire", "https://opire.dev")
    adapters["moltjobs"] = WatchOnlyOpportunityAdapter("moltjobs", "UNVERIFIED_WATCH_ONLY")
    return adapters
