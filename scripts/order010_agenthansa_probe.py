from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

BASE = "https://www.agenthansa.com"


def _get(path: str) -> Any:
    key = os.environ.get("AGENTHANSA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("AGENTHANSA_API_KEY_ABSENT")
    request = urllib.request.Request(
        BASE + path,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": "ATM-ORDER010-AgentHansa-Probe/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"AGENTHANSA_READ_HTTP_{exc.code}:{path}") from exc
    except Exception as exc:
        raise RuntimeError(f"AGENTHANSA_READ_FAILED:{path}:{type(exc).__name__}") from exc
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"AGENTHANSA_NONJSON:{path}") from exc


def _rows(value: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if not isinstance(value, dict):
        return []
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, list):
            return [row for row in candidate if isinstance(row, dict)]
        if isinstance(candidate, dict):
            for inner in ("items", "tasks", "work", "data", "assignments"):
                nested = candidate.get(inner)
                if isinstance(nested, list):
                    return [row for row in nested if isinstance(row, dict)]
    for key in ("data", "items", "tasks", "work", "assignments"):
        candidate = value.get(key)
        if isinstance(candidate, list):
            return [row for row in candidate if isinstance(row, dict)]
    return []


def _pick(value: Any, *paths: str) -> Any:
    for path in paths:
        current = value
        ok = True
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                ok = False
                break
            current = current[part]
        if ok and current not in (None, ""):
            return current
    return None


def _decimal(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        return str(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return None


def _task_summary(row: dict[str, Any]) -> dict[str, Any]:
    title = str(_pick(row, "title", "name", "task.title", "quest.title") or "")[:180]
    text = " ".join(
        str(_pick(row, field) or "")
        for field in ("title", "name", "description", "type", "category", "taskType")
    ).casefold()
    rejected_terms = (
        "referral",
        "refer a",
        "recruit",
        "invite",
        "discord",
        "twitter",
        "tweet",
        "reddit",
        "instagram",
        "social post",
        "promotion",
        "outreach",
        "prediction",
        "exchange1024",
        "arena",
        "stake",
        "deposit",
        "topup",
    )
    rejected = any(term in text for term in rejected_terms)
    reward = _decimal(
        _pick(row, "reward", "rewardUsd", "reward_usd", "amount", "payout", "payment.amount", "bounty")
    )
    return {
        "id": str(_pick(row, "id", "assignment_id", "assignmentId", "task_id", "taskId") or "")[:128],
        "title": title,
        "reward": reward or "UNKNOWN",
        "deadline": str(_pick(row, "deadline", "deadlineAt", "expiresAt", "dueAt") or "UNKNOWN")[:64],
        "status": str(_pick(row, "status", "state") or "UNKNOWN")[:64],
        "atm_policy": "REJECT" if rejected else "VERIFY_FUNDING_AND_EXECUTION_FIT",
    }


def _safe_earnings(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"endpoint": "PASS", "fields": {}}
    fields: dict[str, Any] = {}
    for name in (
        "available",
        "availableBalance",
        "available_balance",
        "pending",
        "pendingBalance",
        "pending_balance",
        "total",
        "totalEarned",
        "total_earned",
        "currency",
    ):
        if name in payload and isinstance(payload[name], (str, int, float)):
            fields[name] = payload[name]
    data = payload.get("data")
    if isinstance(data, dict):
        for name in (
            "available",
            "availableBalance",
            "available_balance",
            "pending",
            "pendingBalance",
            "pending_balance",
            "total",
            "totalEarned",
            "total_earned",
            "currency",
        ):
            if name in data and isinstance(data[name], (str, int, float)):
                fields[name] = data[name]
    return {"endpoint": "PASS", "fields": fields}


def main() -> int:
    me = _get("/api/agents/me")
    work = _get("/api/agents/work")
    feed = _get("/api/agents/feed")
    engagement = _get("/api/engagement")
    earnings = _get("/api/agents/earnings")

    if not isinstance(me, dict):
        raise RuntimeError("AGENTHANSA_ME_UNEXPECTED_SHAPE")
    agent_id = str(_pick(me, "id", "agent.id", "agentId", "agent_id") or "").strip()
    agent_name = str(_pick(me, "name", "agent.name", "handle", "agent.handle") or "").strip()
    if not (agent_id or agent_name):
        raise RuntimeError("AGENTHANSA_ME_IDENTITY_MISSING")

    work_rows = _rows(work, "work", "jobs", "tasks", "data")
    engagement_rows = _rows(engagement, "assignments", "tasks", "data")
    summaries = [_task_summary(row) for row in work_rows]
    eligible = [row for row in summaries if row["atm_policy"] != "REJECT"]
    rejected = [row for row in summaries if row["atm_policy"] == "REJECT"]

    payout_marker = _pick(
        me,
        "fluxa_id",
        "fluxaId",
        "wallet.fluxa_id",
        "wallet.fluxaId",
        "payout.fluxa_id",
        "payout.wallet_id",
        "payoutDestination",
    )
    receipt = {
        "schema": "ATM_ORDER010_AGENTHANSA_AUTH_PROBE_V1",
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "auth_probe": "PASS",
        "agent_identity_present": True,
        "duplicate_registration_performed": False,
        "agent_me": "PASS",
        "work_read": "PASS",
        "feed_read": "PASS",
        "engagement_read": "PASS",
        "earnings_read": "PASS",
        "work_count": len(work_rows),
        "engagement_count": len(engagement_rows),
        "eligible_for_further_verification_count": len(eligible),
        "policy_rejected_count": len(rejected),
        "work": summaries[:40],
        "earnings": _safe_earnings(earnings),
        "payout_destination_bound": bool(payout_marker),
        "feed_shape": "LIST" if isinstance(feed, list) else ("OBJECT" if isinstance(feed, dict) else "OTHER"),
        "claim_performed": False,
        "submission_performed": False,
        "payout_request_performed": False,
        "outgoing_spend_usd": "0",
        "global_block": False,
    }
    Path("order010-agenthansa-probe-receipt.json").write_text(
        json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, sort_keys=True))
    print("AGENTHANSA_AUTH_PROBE=PASS")
    print("OUTGOING_SPEND_USD=0")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"AGENTHANSA_PROBE_FAIL_CLOSED={type(exc).__name__}:{exc}", file=sys.stderr)
        raise
