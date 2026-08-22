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
from .payments import (
    AlgoraPaymentAdapter,
    HttpJsonClient,
    PaymentAdapter,
    TaskmarketPaymentAdapter,
    WorkProtocolPaymentAdapter,
)
from .security import PromptInjectionRisk, assert_external_task_safe
from .taskmarket_cli import TaskmarketCliLane, TaskmarketDuplicateSubmission
from .taskmarket_maker import TaskmarketZeroCostMaker


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
    value = os.getenv(name)
    return value.strip() if value and value.strip() else None


def persist_local_atm_values(values: dict[str, str]) -> None:
    """Atomically persist rail credentials outside git-tracked config."""
    ATM_SECRETS.parent.mkdir(parents=True, exist_ok=True)
    existing: list[str] = []
    if ATM_SECRETS.exists():
        existing = ATM_SECRETS.read_text(encoding="utf-8", errors="ignore").splitlines()
    keys = set(values)
    kept = [line for line in existing if line.split("=", 1)[0].strip() not in keys]
    kept.extend(f"{key}={value}" for key, value in values.items())
    temp = ATM_SECRETS.with_name(ATM_SECRETS.name + ".tmp")
    temp.write_text("\n".join(kept) + "\n", encoding="utf-8")
    os.replace(temp, ATM_SECRETS)


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


def _probability_from_competition(count: int, *, floor: Decimal = Decimal("0.05")) -> Decimal:
    value = Decimal("1") / Decimal(max(1, count + 1))
    return max(floor, min(Decimal("1"), value))


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

    def __init__(
        self,
        base_url: str = "https://workprotocol.ai",
        http: HttpJsonClient | None = None,
        payment_recipient_public_identifier: str = "",
    ):
        self.base_url = base_url.rstrip("/")
        self.http = http or HttpJsonClient()
        self.payment_recipient_public_identifier = payment_recipient_public_identifier.strip()
        self.payment_adapter: PaymentAdapter = WorkProtocolPaymentAdapter(base_url=self.base_url, http=self.http)

    def discover(self, min_reward_usd: Decimal) -> list[Opportunity]:
        query = urllib.parse.urlencode(
            {"status": "open", "category": "code", "min_pay": str(min_reward_usd), "sort": "pay_desc", "limit": 50}
        )
        data = self.http.get(f"{self.base_url}/api/jobs?{query}")
        result: list[Opportunity] = []
        for job in data.get("jobs", []):
            description = "\n".join(
                [str(job.get("title", "")), str(job.get("description", "")), json.dumps(job.get("requirements", {}))]
            )
            try:
                assert_external_task_safe(description)
            except PromptInjectionRisk:
                continue
            reward = Decimal(str(job.get("paymentAmount") or "0"))
            if reward < min_reward_usd:
                continue
            claims_raw = job.get("claims")
            claim_count = len(claims_raw) if isinstance(claims_raw, list) else int(job.get("claimCount") or 0)
            window_hours = Decimal(str(job.get("verificationWindowHours") or 24))
            explicit_funding = {
                "escrow_funded": bool(job.get("escrowFunded")),
                "escrow_tx_hash": job.get("escrowTxHash"),
                "escrow_status": job.get("escrowStatus"),
            }
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
                    funding_proof=explicit_funding,
                    competition=claim_count,
                    claims=claim_count,
                    ai_policy="AGENT_NATIVE",
                    eligibility="PUBLIC_AGENT",
                    claim_method="POST /api/jobs/:id/claim",
                    submission_method="POST /api/jobs/:id/deliver",
                    payment_method=str(job.get("paymentRail") or "base") + ":" + str(job.get("paymentCurrency") or "USDC"),
                    payout_latency=f"verification window {window_hours}h",
                    payout_latency_hours=window_hours,
                    payment_proof_method="platform payments + settlementTxHash + Base receipt",
                    expected_agent_hours=Decimal("3"),
                    expected_owner_minutes=Decimal("0") if self.payment_recipient_public_identifier else Decimal("2"),
                    p_eligible=Decimal("0.95"),
                    p_claim=_probability_from_competition(claim_count),
                    p_complete=Decimal("0.85"),
                    p_accept=Decimal("0.70"),
                    p_pay=Decimal("0.98") if explicit_funding["escrow_funded"] or explicit_funding["escrow_tx_hash"] else Decimal("0.50"),
                    p_withdrawable=Decimal("0.98"),
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
        escrow_status = str(job.get("escrowStatus") or "").lower()
        payments = snapshot.get("payments") or []
        has_explicit_lock = (
            bool(job.get("escrowFunded"))
            or bool(job.get("escrowTxHash"))
            or escrow_status in {"funded", "locked", "escrowed"}
            or any(
                str(p.get("status") or p.get("escrowStatus") or "").lower() in {"funded", "locked", "escrowed"}
                or p.get("escrowTxHash")
                for p in payments
            )
        )
        if not has_explicit_lock:
            raise OpportunityValidationError("WorkProtocol job lacks explicit per-job escrow evidence")
        from .funding_gate import verify_base_escrow_receipt

        verify_base_escrow_receipt(self, snapshot)

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

    def _register(self) -> tuple[str, str]:
        if not self.payment_recipient_public_identifier:
            raise HumanGateRequired(
                HumanGate(
                    kind="PAYOUT_ONBOARDING",
                    reason="WorkProtocol registration can be automated, but a public payout wallet is required before economic claim",
                    exact_human_action="Set payment_recipient_public_identifier in config/atm.json to the OWNER public Base-compatible payout address; never provide a private key",
                    resume_phase=Phase.CLAIM,
                )
            )
        response = self.http.post_json(
            f"{self.base_url}/api/agents/register",
            {
                "name": "atm-agent-teller-machine",
                "description": "Autonomous code-work agent with deterministic verification and payment-proof accounting",
                "walletAddress": self.payment_recipient_public_identifier,
                "capabilities": {"code": True, "testing": True, "github": True},
            },
        )
        agent = response.get("agent") if isinstance(response, dict) and isinstance(response.get("agent"), dict) else response
        if not isinstance(agent, dict):
            raise OpportunityValidationError("WorkProtocol registration returned malformed response")
        api_key = str(response.get("apiKey") or response.get("api_key") or agent.get("apiKey") or agent.get("api_key") or "").strip()
        agent_id = str(agent.get("id") or agent.get("agentId") or response.get("agentId") or "").strip()
        if not api_key or not agent_id:
            raise OpportunityValidationError("WorkProtocol registration response lacks api key or agent id")
        persist_local_atm_values({"WORKPROTOCOL_API_KEY": api_key, "WORKPROTOCOL_AGENT_ID": agent_id})
        return api_key, agent_id

    def _auth(self) -> tuple[str, str]:
        api_key = local_atm_value("WORKPROTOCOL_API_KEY")
        agent_id = local_atm_value("WORKPROTOCOL_AGENT_ID")
        if api_key and agent_id:
            return api_key, agent_id
        if bool(api_key) != bool(agent_id):
            raise HumanGateRequired(
                HumanGate(
                    kind="CREDENTIAL_REPAIR",
                    reason="WorkProtocol local credential pair is incomplete and cannot be recovered without risking duplicate registration",
                    exact_human_action="Restore the matching WORKPROTOCOL_API_KEY and WORKPROTOCOL_AGENT_ID pair in .atm/secrets.env, or remove both entries to allow clean automatic registration",
                    resume_phase=Phase.CLAIM,
                )
            )
        return self._register()

    def claim(self, opportunity: Opportunity) -> dict[str, Any]:
        api_key, agent_id = self._auth()
        job_id = opportunity.canonical_opportunity_id.split(":", 1)[1]
        snapshot = self.http.get(f"{self.base_url}/api/jobs/{urllib.parse.quote(job_id)}")
        self.verify_freshness(opportunity, snapshot)
        self.verify_funding(opportunity, snapshot)
        self.verify_eligibility(opportunity, snapshot)
        current_hash = external_state_hash(snapshot)
        if opportunity.external_state_hash and current_hash != opportunity.external_state_hash:
            raise OpportunityValidationError("WorkProtocol state changed after VERIFY; re-verification cycle required")
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
        def reconcile(snapshot: dict[str, Any]) -> dict[str, Any] | None:
            job = snapshot.get("job") if isinstance(snapshot.get("job"), dict) else {}
            rows = [*(snapshot.get("deliveries") or []), *(job.get("deliveries") or [])]
            for row in rows:
                if not isinstance(row, dict) or str(row.get("claimId") or row.get("claim_id") or "") != opportunity.claim_id:
                    continue
                delivered = row.get("deliverable") if isinstance(row.get("deliverable"), dict) else {}
                if str(delivered.get("url") or row.get("deliverableUrl") or "") == deliverable_url and row.get("id"):
                    return {"delivery": row, "reused": True}
            return None

        existing = reconcile(self.fetch_authoritative(opportunity))
        if existing:
            return existing
        key = hashlib.sha256(f"{job_id}|{opportunity.claim_id}|{deliverable_url}".encode()).hexdigest()
        try:
            return self.http.post_json(
                f"{self.base_url}/api/jobs/{urllib.parse.quote(job_id)}/deliver",
                {"claimId": opportunity.claim_id, "deliverable": {"type": "url", "url": deliverable_url}},
                {"Authorization": f"Bearer {api_key}", "Idempotency-Key": key},
            )
        except Exception:
            recovered = reconcile(self.fetch_authoritative(opportunity))
            if recovered:
                return recovered
            raise

    def monitor(self, opportunity: Opportunity) -> dict[str, Any]:
        return self.fetch_authoritative(opportunity)

    def fetch_payment(self, opportunity: Opportunity, expected_recipient: str):
        job_id = opportunity.canonical_opportunity_id.split(":", 1)[1]
        return self.payment_adapter.fetch_payment(job_id, expected_recipient)


class TaskmarketOpportunityAdapter(OpportunityAdapter):
    name = "taskmarket"
    PAID_UPFRONT_MODES = {"pitch", "auction", "benchmark"}

    def __init__(
        self,
        base_url: str = "https://api.taskmarket.dev",
        http: HttpJsonClient | None = None,
        lane: TaskmarketCliLane | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.http = http or HttpJsonClient()
        self.lane = lane or TaskmarketCliLane(http=self.http)
        self.payment_adapter: PaymentAdapter = TaskmarketPaymentAdapter(base_url=self.base_url, http=self.http)
        self.skipped_task_ids: set[str] = set()

    def discover(self, min_reward_usd: Decimal) -> list[Opportunity]:
        min_base = int(min_reward_usd * Decimal(1_000_000))
        query = urllib.parse.urlencode({"status": "open", "minReward": str(min_base), "sort": "reward_desc", "limit": 50})
        data = self.http.get(f"{self.base_url}/api/tasks?{query}")
        result: list[Opportunity] = []
        for task in data.get("tasks", []):
            task_id = str(task.get("id") or task.get("taskId") or "")
            if not task_id or task_id in self.skipped_task_ids:
                continue
            mode = str(task.get("mode") or "bounty").lower()
            if bool(task.get("stakeRequired")) or mode in self.PAID_UPFRONT_MODES:
                continue
            description = str(task.get("description", ""))
            try:
                assert_external_task_safe(description)
            except PromptInjectionRisk:
                continue
            reward = Decimal(str(task.get("reward") or "0")) / Decimal(1_000_000)
            if reward < min_reward_usd:
                continue
            submissions_raw = task.get("submissions")
            submissions = (
                len(submissions_raw)
                if isinstance(submissions_raw, list)
                else int(task.get("submissionCount") or task.get("submissionsCount") or 0)
            )
            p_accept = max(Decimal("0.05"), Decimal("1") / Decimal(submissions + 2))
            maker_supported = TaskmarketZeroCostMaker.supported_task(description)
            result.append(
                Opportunity(
                    canonical_opportunity_id=f"taskmarket:{task_id}",
                    source=self.name,
                    authoritative_url=f"https://taskmarket.dev/tasks/{task_id}",
                    upstream_status=str(task.get("status", "unknown")),
                    created_at=_dt(task.get("createdAt")),
                    updated_at=_dt(task.get("updatedAt")),
                    deadline=_dt(task.get("expiryTime") or task.get("deadline")),
                    reward_gross=reward,
                    expected_fees=reward * Decimal("0.075"),
                    funding_proof={
                        "escrow": task.get("escrowTxHash") or task.get("fundingTxHash") or task.get("createTxHash"),
                        "reward_base_units": task.get("reward"),
                    },
                    competition=submissions,
                    claims=1 if task.get("claimedBy") else 0,
                    open_prs=submissions,
                    ai_policy="AGENT_NATIVE",
                    eligibility="PUBLIC_AGENT_TRUSTED_SIGNER_LANE",
                    claim_method="not required for bounty",
                    submission_method="first-party TaskMarket CLI after CHECK=PASS",
                    payment_method="USDC Base award settlement",
                    payout_latency="on settlement",
                    payout_latency_hours=Decimal("48"),
                    payment_proof_method="TaskDetailResponse.awards + settlementTxHash + Base receipt",
                    expected_agent_hours=Decimal("1.5") if maker_supported else Decimal("6"),
                    expected_owner_minutes=Decimal("0"),
                    p_eligible=Decimal("0.95"),
                    p_claim=Decimal("1"),
                    p_complete=Decimal("0.85") if maker_supported else Decimal("0"),
                    p_accept=p_accept,
                    p_pay=Decimal("0.98"),
                    p_withdrawable=Decimal("0.98"),
                    external_state_hash=external_state_hash(task),
                    task_mode=mode,
                    task_description=description,
                    task_title=str(task.get("title") or description[:160]),
                    maker_supported=maker_supported,
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
        if snapshot.get("submissionWindowOpen") is not True:
            raise OpportunityValidationError("Taskmarket submission window is not open")

    def verify_funding(self, opportunity: Opportunity, snapshot: dict[str, Any]) -> None:
        reward = Decimal(str(snapshot.get("reward") or "0"))
        if reward <= 0:
            raise OpportunityValidationError("Taskmarket task reward is zero")
        if not (snapshot.get("escrowTxHash") or snapshot.get("fundingTxHash") or snapshot.get("createTxHash")):
            raise OpportunityValidationError("Taskmarket task lacks explicit escrow/funding tx")

    def verify_eligibility(self, opportunity: Opportunity, snapshot: dict[str, Any]) -> None:
        assert_external_task_safe("\n".join([str(snapshot.get("title", "")), str(snapshot.get("description", ""))]))
        if bool(snapshot.get("stakeRequired")):
            raise OpportunityValidationError("Taskmarket stakeRequired=true is prohibited")
        mode = str(snapshot.get("mode") or getattr(opportunity, "task_mode", "bounty")).lower()
        if mode != "bounty" or mode in self.PAID_UPFRONT_MODES:
            raise OpportunityValidationError(f"Taskmarket mode {mode} is outside zero-spend autonomous lane")
        # Work class, runtime executor availability and duplicate truth are owned by
        # the single Cash Canon invoked immediately after this untrusted-text gate.

    def inspect_competition(self, opportunity: Opportunity, snapshot: dict[str, Any]) -> dict[str, int]:
        submissions = snapshot.get("submissions") or []
        return {
            "claims": 1 if snapshot.get("claimedBy") else 0,
            "open_prs": len(submissions) if isinstance(submissions, list) else int(snapshot.get("submissionCount") or 0),
        }

    def canonical_admission(self, opportunity: Opportunity, snapshot: dict[str, Any]):
        """Resolve signer, duplicate, capability and attack thresholds before allocation."""
        from .cash_canon import taskmarket_cash_decision

        task_id = opportunity.canonical_opportunity_id.split(":", 1)[1]
        signer_ready = False
        try:
            signer_ready = bool(self.lane.preflight_signer().get("signer_present"))
        except Exception:
            signer_ready = False
        decision = taskmarket_cash_decision(
            snapshot,
            canonical_wallet=self.lane.canonical_wallet,
            existing_submission=self.lane.existing_submission(task_id) is not None,
            signer_ready=signer_ready,
            capability_runtime_ready=TaskmarketZeroCostMaker.supported_task(
                str(snapshot.get("description") or getattr(opportunity, "task_description", ""))
            ),
        )
        if not decision.allocation_allowed:
            raise OpportunityValidationError("TaskMarket Cash Canon rejected allocation: " + ",".join(decision.reasons))
        return decision

    def claim(self, opportunity: Opportunity) -> dict[str, Any]:
        mode = str(getattr(opportunity, "task_mode", "bounty")).lower()
        if mode == "bounty":
            return {"claim_required": False, "mode": "bounty"}
        raise OpportunityValidationError("Taskmarket non-bounty claim is outside zero-spend autonomous lane")

    def submit(self, opportunity: Opportunity, deliverable_url: str) -> dict[str, Any]:
        task_id = opportunity.canonical_opportunity_id.split(":", 1)[1]
        try:
            receipt = self.lane.submit_checked(
                task_id,
                deliverable_url,
                checker_passed=bool(getattr(opportunity, "checker_passed", False)),
            )
        except TaskmarketDuplicateSubmission as exc:
            self.skipped_task_ids.add(task_id)
            artifact_sha256 = str(getattr(opportunity, "artifact_sha256", ""))
            manifest_verified = bool(
                artifact_sha256 and self.lane._verify_manifest(task_id, exc.submission_id, artifact_sha256)
            )
            return {
                "ok": True,
                "submissionId": exc.submission_id,
                "duplicate": True,
                "idempotencyKey": "EXISTING_AUTHORITATIVE_SUBMISSION",
                "artifactSha256": artifact_sha256,
                "manifestVerified": manifest_verified,
                "workerAddress": self.lane.canonical_wallet,
                "outgoingSpendUsd": "0",
            }
        return {
            "ok": True,
            "submissionId": receipt.submission_id,
            "idempotencyKey": receipt.idempotency_key,
            "artifactSha256": receipt.artifact_sha256,
            "manifestVerified": receipt.manifest_verified,
            "workerAddress": receipt.wallet,
            "outgoingSpendUsd": receipt.outgoing_spend_usd,
        }

    def monitor(self, opportunity: Opportunity) -> dict[str, Any]:
        return self.fetch_authoritative(opportunity)

    def fetch_payment(self, opportunity: Opportunity, expected_recipient: str):
        task_id = opportunity.canonical_opportunity_id.split(":", 1)[1]
        return self.payment_adapter.fetch_payment(task_id, expected_recipient)


class WatchOnlyOpportunityAdapter(OpportunityAdapter):
    """Fail-closed adapter for rails lacking a verified write/payment contract."""

    def __init__(self, name: str, discovery_url: str, payment_adapter: PaymentAdapter | None = None):
        self.name = name
        self.discovery_url = discovery_url
        self.payment_adapter = payment_adapter

    def discover(self, min_reward_usd: Decimal) -> list[Opportunity]:
        del min_reward_usd
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
            base_url=adapter_cfg.get("workprotocol", {}).get("base_url", "https://workprotocol.ai"),
            payment_recipient_public_identifier=str(config.get("payment_recipient_public_identifier") or ""),
        )
    if adapter_cfg.get("taskmarket", {}).get("enabled", True):
        adapters["taskmarket"] = TaskmarketOpportunityAdapter(
            base_url=adapter_cfg.get("taskmarket", {}).get("base_url", "https://api.taskmarket.dev")
        )
    algora_cfg = adapter_cfg.get("algora", {})
    adapters["algora"] = WatchOnlyOpportunityAdapter(
        "algora",
        "https://algora.io/bounties",
        AlgoraPaymentAdapter(payout_api_template=algora_cfg.get("payout_api_template")),
    )
    adapters["opire"] = WatchOnlyOpportunityAdapter("opire", "https://opire.dev")
    adapters["moltjobs"] = WatchOnlyOpportunityAdapter("moltjobs", "UNVERIFIED_WATCH_ONLY")
    return adapters
