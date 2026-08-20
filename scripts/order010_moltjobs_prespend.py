from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

CLI = ["npx", "--yes", "@moltjobs/cli@latest"]
ENV = dict(os.environ)
ENV["MOLT_NO_UPDATE_CHECK"] = "1"
CERT_COST_USDC = Decimal("5")
MIN_NET_FOR_CERT_USDC = Decimal("10")


def _json_command(*args: str) -> Any:
    proc = subprocess.run(
        [*CLI, *args, "--json"],
        env=ENV,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"CLI_READ_FAILED:{' '.join(args)}:exit={proc.returncode}")
    text = proc.stdout.strip()
    if not text:
        raise RuntimeError(f"CLI_READ_EMPTY:{' '.join(args)}")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"CLI_READ_NONJSON:{' '.join(args)}") from exc


def _unwrap(value: Any) -> Any:
    if isinstance(value, dict) and "data" in value and len(value) <= 4:
        return value["data"]
    return value


def _rows(value: Any) -> list[dict[str, Any]]:
    value = _unwrap(value)
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        for key in ("jobs", "items", "results", "bids", "certifications"):
            rows = value.get(key)
            if isinstance(rows, list):
                return [x for x in rows if isinstance(x, dict)]
    return []


def _pick(obj: Any, *paths: str) -> Any:
    for path in paths:
        cur = obj
        ok = True
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                ok = False
                break
            cur = cur[part]
        if ok and cur not in (None, ""):
            return cur
    return None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _job_id(job: dict[str, Any]) -> str:
    return _text(_pick(job, "id", "jobId", "job_id"))


def _status(job: dict[str, Any]) -> str:
    return _text(_pick(job, "status", "state", "jobStatus")).upper()


def _deadline(job: dict[str, Any]) -> datetime | None:
    return _dt(_pick(job, "deadline", "deadlineAt", "expiresAt", "availableUntil", "closesAt"))


def _gross(job: dict[str, Any]) -> Decimal | None:
    return _decimal(
        _pick(job, "budgetUsdc", "proposedUsdc", "payUsdc", "pay_usdc", "rewardUsdc", "reward", "budget")
    )


def _net(job: dict[str, Any]) -> Decimal | None:
    explicit = _decimal(_pick(job, "netRewardUsdc", "netReward", "netUsdc"))
    if explicit is not None:
        return explicit
    gross = _gross(job)
    return (gross * Decimal("0.95")).quantize(Decimal("0.0001")) if gross is not None else None


def _funding(job: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    status = _text(
        _pick(job, "escrow.status", "escrowStatus", "fundingStatus", "funding.status", "payment.escrowStatus")
    ).upper()
    tx = _text(
        _pick(job, "escrow.txHash", "escrow.tx_hash", "escrowTxHash", "fundingTxHash", "payment.escrowTxHash")
    )
    funded_flag = _pick(job, "escrowFunded", "funded", "isFunded")
    proven = status in {"FUNDED", "LOCKED", "ESCROWED", "ACTIVE"} or bool(tx) or funded_flag is True
    evidence = {
        "escrow_status": status or "UNKNOWN",
        "escrow_tx_present": bool(tx),
        "funded_flag": funded_flag if isinstance(funded_flag, bool) else "UNKNOWN",
    }
    return proven, evidence


def _required_certs(job: dict[str, Any]) -> list[str]:
    values: list[str] = []
    raw = _pick(
        job,
        "requiredCertifications",
        "requiredCertification",
        "certificationsRequired",
        "requirements.certifications",
        "requirements.requiredCertifications",
        "requiredEvalPacks",
        "requirements.evalPacks",
    )
    if isinstance(raw, str) and raw.strip():
        values.append(raw.strip())
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                label = _text(_pick(item, "slug", "name", "topic", "id"))
            else:
                label = _text(item)
            if label:
                values.append(label)
    elif isinstance(raw, dict):
        label = _text(_pick(raw, "slug", "name", "topic", "id"))
        if label:
            values.append(label)
    req_flag = _pick(job, "requiresCertification", "certificationRequired", "requiresEval")
    if req_flag is True and not values:
        values.append("REQUIRED_UNSPECIFIED")
    return sorted(set(values))


def _held_certs(agent: dict[str, Any]) -> set[str]:
    raw = _pick(agent, "certifications", "evalCertifications", "badges")
    out: set[str] = set()
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                for key in ("slug", "name", "topic", "id"):
                    value = _text(item.get(key))
                    if value:
                        out.add(value.casefold())
            else:
                value = _text(item)
                if value:
                    out.add(value.casefold())
    return out


def _agent_id(agent: dict[str, Any]) -> str:
    return _text(_pick(agent, "id", "agentId", "agent.id"))


def _agent_eligible(job: dict[str, Any], agent_id: str) -> bool:
    if _pick(job, "agentEligible", "eligible") is False:
        return False
    allowed = _pick(job, "eligibleAgentIds", "allowedAgentIds")
    if isinstance(allowed, list) and allowed and agent_id:
        return agent_id in {str(x) for x in allowed}
    return True


def _execution_fit(job: dict[str, Any]) -> tuple[bool, str]:
    text = " ".join(
        _text(_pick(job, key))
        for key in ("title", "description", "vertical", "category", "template", "jobType")
    ).casefold()
    reject = (
        "instagram reel",
        "video",
        "physical",
        "phone call",
        "cold call",
        "outreach",
        "referral",
        "recruit",
        "affiliate",
        "social promotion",
        "in-person",
    )
    if any(term in text for term in reject):
        return False, "REJECTED_NON_AUTONOMOUS_OR_PROMOTION"
    accept = (
        "research",
        "analysis",
        "writing",
        "content",
        "data",
        "code",
        "software",
        "documentation",
        "fact check",
        "summar",
        "api",
        "web",
    )
    if any(term in text for term in accept):
        return True, "ATM_DIGITAL_CAPABILITY_MATCH"
    return False, "UNPROVEN_EXECUTION_FIT"


def _bid_count(payload: Any) -> int | str:
    rows = _rows(payload)
    if rows:
        return len(rows)
    root = _unwrap(payload)
    if isinstance(root, dict):
        for key in ("count", "total", "bidCount", "bidsCount", "totalCount"):
            val = root.get(key)
            if isinstance(val, int):
                return val
    return "UNKNOWN"


def _allowance(payload: Any) -> dict[str, Any]:
    root = _unwrap(payload)
    if not isinstance(root, dict):
        return {"remaining": "UNKNOWN", "limit": "UNKNOWN"}
    return {
        "remaining": _pick(root, "remaining", "remainingBids", "freeRemaining", "allowanceRemaining") or "UNKNOWN",
        "limit": _pick(root, "limit", "monthlyLimit", "freeLimit", "allowance") or "UNKNOWN",
    }


def main() -> int:
    if not os.environ.get("MOLTJOBS_API_KEY", "").strip():
        raise RuntimeError("MOLTJOBS_API_KEY_ABSENT")

    now = datetime.now(timezone.utc)
    agent_raw = _json_command("agent", "me")
    allowance_raw = _json_command("bids", "allowance")
    jobs_raw = _json_command("jobs", "list", "--limit", "100")

    agent = _unwrap(agent_raw)
    if not isinstance(agent, dict):
        raise RuntimeError("AGENT_ME_UNEXPECTED_SHAPE")
    agent_id = _agent_id(agent)
    held = _held_certs(agent)
    candidates: list[dict[str, Any]] = []

    for listed in _rows(jobs_raw):
        jid = _job_id(listed)
        if not jid:
            continue
        detail_raw = _json_command("jobs", "show", jid)
        detail = _unwrap(detail_raw)
        if not isinstance(detail, dict):
            continue
        status = _status(detail) or _status(listed)
        deadline = _deadline(detail) or _deadline(listed)
        deadline_current = deadline is not None and deadline > now
        funded, funding_evidence = _funding(detail)
        gross = _gross(detail)
        net = _net(detail)
        certs = _required_certs(detail)
        eligible = _agent_eligible(detail, agent_id)
        fit, fit_reason = _execution_fit(detail)

        try:
            bids_raw = _json_command("bids", "list", jid)
            bid_count = _bid_count(bids_raw)
        except RuntimeError:
            bid_count = "UNKNOWN"

        cert_unheld = [
            cert for cert in certs
            if cert != "REQUIRED_UNSPECIFIED" and cert.casefold() not in held
        ]
        cert_exact = bool(certs) and "REQUIRED_UNSPECIFIED" not in certs
        materially_exceeds = net is not None and net > MIN_NET_FOR_CERT_USDC
        open_now = status in {"OPEN", "ACTIVE", "POSTED"}
        competition_known = isinstance(bid_count, int)

        qualifies = all(
            (
                open_now,
                deadline_current,
                funded,
                eligible,
                fit,
                competition_known,
                materially_exceeds,
                cert_exact,
                bool(cert_unheld),
            )
        )

        candidates.append(
            {
                "job_id": jid,
                "title": _text(_pick(detail, "title"))[:160],
                "status": status or "UNKNOWN",
                "deadline": deadline.isoformat().replace("+00:00", "Z") if deadline else "UNKNOWN",
                "deadline_current": deadline_current,
                "budget_usdc": str(gross) if gross is not None else "UNKNOWN",
                "expected_net_usdc": str(net) if net is not None else "UNKNOWN",
                "funded_current": funded,
                "escrow_evidence": funding_evidence,
                "agent_eligible_current": eligible,
                "required_certifications": certs or [],
                "unheld_required_certifications": cert_unheld,
                "competition_bid_count": bid_count,
                "execution_fit": fit,
                "execution_fit_evidence": fit_reason,
                "materially_exceeds_5usdc": materially_exceeds,
                "cert_spend_candidate": qualifies,
            }
        )

    recommended = [x for x in candidates if x["cert_spend_candidate"]]
    receipt = {
        "schema": "ATM_ORDER010_MOLTJOBS_PRESPEND_V1",
        "observed_at": now.isoformat().replace("+00:00", "Z"),
        "auth_probe": "PASS",
        "agent_me": "PASS",
        "agent_id_present": bool(agent_id),
        "bid_allowance": _allowance(allowance_raw),
        "current_job_count": len(candidates),
        "candidates": candidates,
        "certification_cost_usdc": str(CERT_COST_USDC),
        "recommendation_threshold_net_usdc": str(MIN_NET_FOR_CERT_USDC),
        "moltjobs_cert_spend_recommended": "YES" if recommended else "NO",
        "recommended_job_ids": [x["job_id"] for x in recommended],
        "paid_certification_performed": False,
        "bid_performed": False,
        "outgoing_spend_usd": "0",
        "lane_state": "QUALIFIED_FOR_OWNER_CERT_AUTH" if recommended else "DISCOVERY_ONLY",
        "autonomous_lane_must_continue": True,
        "simon_gate_parallel": True,
        "account_gate_global_block": False,
    }
    with open("order010-moltjobs-prespend-receipt.json", "w", encoding="utf-8") as fh:
        json.dump(receipt, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(json.dumps(receipt, sort_keys=True))
    print("MOLTJOBS_CERT_SPEND_RECOMMENDED=" + receipt["moltjobs_cert_spend_recommended"])
    print("OUTGOING_SPEND_USD=0")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"MOLTJOBS_PRESPEND_FAIL_CLOSED={type(exc).__name__}:{exc}", file=sys.stderr)
        raise
