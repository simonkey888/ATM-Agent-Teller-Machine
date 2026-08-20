from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

BASE = "https://api.moltjobs.io/v1"
CERT_COST_USDC = Decimal("5")
MIN_NET_FOR_CERT_USDC = Decimal("10")


def _get(path: str, *, query: dict[str, Any] | None = None) -> Any:
    key = os.environ.get("MOLTJOBS_API_KEY", "").strip()
    if not key:
        raise RuntimeError("MOLTJOBS_API_KEY_ABSENT")
    url = BASE + path
    if query:
        url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": "ATM-ORDER010-MoltJobs-PreSpend/2.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            payload = json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"MOLTJOBS_READ_HTTP_{exc.code}:{path}") from exc
    except Exception as exc:
        raise RuntimeError(f"MOLTJOBS_READ_FAILED:{path}:{type(exc).__name__}") from exc
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


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


def _txt(v: Any) -> str:
    return str(v or "").strip()


def _dec(v: Any) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _dt(v: Any) -> datetime | None:
    if v in (None, ""):
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _rows(v: Any) -> list[dict[str, Any]]:
    if isinstance(v, list):
        return [x for x in v if isinstance(x, dict)]
    if isinstance(v, dict):
        for k in ("jobs", "items", "results", "bids"):
            if isinstance(v.get(k), list):
                return [x for x in v[k] if isinstance(x, dict)]
    return []


def _certs(job: dict[str, Any]) -> list[str]:
    raw = _pick(
        job,
        "requiredCertifications",
        "requiredCertification",
        "requirements.certifications",
        "requirements.requiredCertifications",
        "requiredEvalPacks",
        "requirements.evalPacks",
    )
    out: list[str] = []
    if isinstance(raw, str):
        out = [raw]
    elif isinstance(raw, list):
        for x in raw:
            if isinstance(x, dict):
                s = _txt(_pick(x, "id", "slug", "name", "topic"))
            else:
                s = _txt(x)
            if s:
                out.append(s)
    elif isinstance(raw, dict):
        s = _txt(_pick(raw, "id", "slug", "name", "topic"))
        if s:
            out.append(s)
    required = _pick(job, "requiresCertification", "certificationRequired", "requiresEval")
    if required is True and not out:
        out.append("REQUIRED_UNSPECIFIED")
    return sorted(set(out))


def _held_certs(agent: dict[str, Any]) -> set[str]:
    raw = _pick(agent, "certifications", "evalCertifications", "badges")
    out: set[str] = set()
    if isinstance(raw, list):
        for x in raw:
            if isinstance(x, dict):
                for k in ("id", "slug", "name", "topic"):
                    if _txt(x.get(k)):
                        out.add(_txt(x[k]).casefold())
            elif _txt(x):
                out.add(_txt(x).casefold())
    return out


def _funding(job: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    status = _txt(_pick(job, "escrow.status", "escrowStatus", "fundingStatus", "funding.status")).upper()
    tx = _txt(_pick(job, "escrow.txHash", "escrowTxHash", "fundingTxHash"))
    flag = _pick(job, "escrowFunded", "funded", "isFunded")
    proven = status in {"FUNDED", "LOCKED", "ESCROWED", "ACTIVE"} or bool(tx) or flag is True
    return proven, {
        "escrow_status": status or "UNKNOWN",
        "escrow_tx_present": bool(tx),
        "funded_flag": flag if isinstance(flag, bool) else "UNKNOWN",
    }


def _fit(job: dict[str, Any]) -> tuple[bool, str]:
    text = " ".join(_txt(_pick(job, k)) for k in ("title", "description", "vertical", "category", "jobType")).casefold()
    reject = ("instagram", "video", "physical", "phone call", "outreach", "referral", "recruit", "affiliate", "in-person")
    if any(x in text for x in reject):
        return False, "REJECTED_NON_AUTONOMOUS_OR_PROMOTION"
    accept = ("research", "analysis", "writing", "content", "data", "code", "software", "documentation", "fact check", "api", "web")
    if any(x in text for x in accept):
        return True, "ATM_DIGITAL_CAPABILITY_MATCH"
    return False, "UNPROVEN_EXECUTION_FIT"


def _allowance(v: Any) -> dict[str, Any]:
    if not isinstance(v, dict):
        return {"remaining": "UNKNOWN", "limit": "UNKNOWN"}
    remaining = _pick(v, "remaining", "remainingBids", "freeRemaining", "allowanceRemaining")
    limit = _pick(v, "limit", "monthlyLimit", "freeLimit", "allowance")
    return {"remaining": remaining if remaining is not None else "UNKNOWN", "limit": limit if limit is not None else "UNKNOWN"}


def main() -> int:
    now = datetime.now(timezone.utc)
    agent = _get("/agents/me")
    if not isinstance(agent, dict):
        raise RuntimeError("AGENT_ME_UNEXPECTED_SHAPE")
    agent_id = _txt(_pick(agent, "id", "agentId"))
    if not agent_id:
        raise RuntimeError("AGENT_ME_MISSING_ID")

    # Official CLI source resolves `molt bids allowance` to this exact read endpoint.
    allowance_raw = _get("/bids/allowance/" + urllib.parse.quote(agent_id, safe=""))
    jobs = _rows(_get("/jobs", query={"status": "OPEN", "limit": 100}))
    held = _held_certs(agent)
    candidates: list[dict[str, Any]] = []

    for listed in jobs:
        jid = _txt(_pick(listed, "id", "jobId"))
        if not jid:
            continue
        detail = _get("/jobs/" + urllib.parse.quote(jid, safe=""))
        if not isinstance(detail, dict):
            continue
        bids = _rows(_get("/jobs/" + urllib.parse.quote(jid, safe="") + "/bids"))
        status = _txt(_pick(detail, "status", "state")).upper()
        deadline = _dt(_pick(detail, "deadlineAt", "deadline", "expiresAt", "availableUntil"))
        gross = _dec(_pick(detail, "budgetUsdc", "proposedUsdc", "payUsdc", "rewardUsdc", "reward", "budget"))
        explicit_net = _dec(_pick(detail, "netRewardUsdc", "netReward", "netUsdc"))
        net = explicit_net if explicit_net is not None else ((gross * Decimal("0.95")).quantize(Decimal("0.0001")) if gross is not None else None)
        funded, escrow = _funding(detail)
        certs = _certs(detail)
        unheld = [c for c in certs if c != "REQUIRED_UNSPECIFIED" and c.casefold() not in held]
        cert_exact = bool(certs) and "REQUIRED_UNSPECIFIED" not in certs
        eligible = _pick(detail, "agentEligible", "eligible") is not False
        allowed_ids = _pick(detail, "eligibleAgentIds", "allowedAgentIds")
        if isinstance(allowed_ids, list) and allowed_ids:
            eligible = eligible and agent_id in {str(x) for x in allowed_ids}
        fit, fit_reason = _fit(detail)
        deadline_current = deadline is not None and deadline > now
        materially_exceeds = net is not None and net > MIN_NET_FOR_CERT_USDC
        qualifies = all((
            status in {"OPEN", "ACTIVE", "POSTED"},
            deadline_current,
            funded,
            eligible,
            fit,
            materially_exceeds,
            cert_exact,
            bool(unheld),
        ))
        candidates.append({
            "job_id": jid,
            "title": _txt(detail.get("title"))[:160],
            "status": status or "UNKNOWN",
            "deadline": deadline.isoformat().replace("+00:00", "Z") if deadline else "UNKNOWN",
            "deadline_current": deadline_current,
            "budget_usdc": str(gross) if gross is not None else "UNKNOWN",
            "expected_net_usdc": str(net) if net is not None else "UNKNOWN",
            "funded_current": funded,
            "escrow_evidence": escrow,
            "agent_eligible_current": eligible,
            "required_certifications": certs,
            "unheld_required_certifications": unheld,
            "competition_bid_count": len(bids),
            "execution_fit": fit,
            "execution_fit_evidence": fit_reason,
            "materially_exceeds_5usdc": materially_exceeds,
            "cert_spend_candidate": qualifies,
        })

    recommended = [c for c in candidates if c["cert_spend_candidate"]]
    receipt = {
        "schema": "ATM_ORDER010_MOLTJOBS_PRESPEND_V1",
        "observed_at": now.isoformat().replace("+00:00", "Z"),
        "auth_probe": "PASS",
        "agent_me": "PASS",
        "agent_id_present": True,
        "bid_allowance": _allowance(allowance_raw),
        "current_job_count": len(candidates),
        "candidates": candidates,
        "certification_cost_usdc": str(CERT_COST_USDC),
        "recommendation_threshold_net_usdc": str(MIN_NET_FOR_CERT_USDC),
        "moltjobs_cert_spend_recommended": "YES" if recommended else "NO",
        "recommended_job_ids": [c["job_id"] for c in recommended],
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
