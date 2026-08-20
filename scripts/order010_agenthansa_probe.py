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
            "User-Agent": "ATM-ORDER011-AgentHansa-Probe/2.0",
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
            for inner in ("items", "tasks", "work", "data", "assignments", "transfers", "payouts", "notifications"):
                nested = candidate.get(inner)
                if isinstance(nested, list):
                    return [row for row in nested if isinstance(row, dict)]
    for key in ("data", "items", "tasks", "work", "assignments", "transfers", "payouts", "notifications"):
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


def _dec(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _decimal(value: Any) -> str | None:
    parsed = _dec(value)
    return str(parsed) if parsed is not None else None


def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _policy(text: str) -> str:
    low = text.casefold()
    rejected_terms = (
        "referral", "refer a", "recruit", "invite", "discord", "twitter", "tweet", "reddit",
        "instagram", "social post", "promotion", "outreach", "prediction", "exchange1024", "arena",
        "stake", "deposit", "topup", "gambling", "betting",
    )
    return "REJECT" if any(term in low for term in rejected_terms) else "VERIFY_FUNDING_AND_EXECUTION_FIT"


def _task_summary(row: dict[str, Any]) -> dict[str, Any]:
    title = str(_pick(row, "title", "name", "task.title", "quest.title") or "")[:180]
    text = " ".join(str(_pick(row, field) or "") for field in ("title", "name", "description", "type", "category", "taskType"))
    reward = _decimal(_pick(row, "reward", "rewardUsd", "reward_usd", "amount", "payout", "payment.amount", "bounty"))
    return {
        "id": str(_pick(row, "id", "assignment_id", "assignmentId", "task_id", "taskId") or "")[:128],
        "title": title,
        "reward": reward or "UNKNOWN",
        "deadline": str(_pick(row, "deadline", "deadlineAt", "expiresAt", "dueAt") or "UNKNOWN")[:64],
        "status": str(_pick(row, "status", "state") or "UNKNOWN")[:64],
        "atm_policy": _policy(text),
    }


def _safe_earnings(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"endpoint": "PASS", "fields": {}}
    fields: dict[str, Any] = {}
    for root in (payload, payload.get("data") if isinstance(payload.get("data"), dict) else {}):
        for name in (
            "available", "availableBalance", "available_balance", "pending", "pendingBalance",
            "pending_balance", "total", "totalEarned", "total_earned", "currency",
        ):
            if name in root and isinstance(root[name], (str, int, float)):
                fields[name] = root[name]
    return {"endpoint": "PASS", "fields": fields}


def _payout_destination(payload: Any) -> dict[str, Any]:
    root = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload
    if not isinstance(root, dict):
        return {"endpoint": "PASS", "bound": False, "kind": "UNKNOWN", "safe_for_atm": False}
    raw_kind = str(_pick(root, "destination", "destinationType", "destination_type", "type", "kind") or "UNKNOWN").strip().casefold()
    if "prediction" in raw_kind:
        kind = "PREDICTION_BALANCE"
    elif "fluxa" in raw_kind:
        kind = "FLUXA"
    else:
        kind = raw_kind.upper()[:64] if raw_kind else "UNKNOWN"
    marker = _pick(root, "configured", "bound", "isConfigured", "is_configured", "fluxa_id", "fluxaId", "wallet_id", "walletId")
    bound = bool(marker) or kind in {"FLUXA", "PREDICTION_BALANCE"}
    return {
        "endpoint": "PASS",
        "bound": bound,
        "kind": kind,
        "safe_for_atm": bool(bound and kind == "FLUXA"),
    }


def _funding(row: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    status = str(_pick(row, "escrow.status", "funding.status", "fundingStatus", "payment.status", "paymentStatus") or "UNKNOWN").upper()
    funded_flag = _pick(row, "funded", "isFunded", "escrowFunded", "merchantFunded", "payment.funded")
    escrow_id = _pick(row, "escrow.id", "escrowId", "payment.id", "paymentIntentId", "funding.id")
    proven = funded_flag is True or status in {"FUNDED", "LOCKED", "ESCROWED", "HELD"}
    return proven, {
        "status": status[:64],
        "funded_flag": funded_flag if isinstance(funded_flag, bool) else "UNKNOWN",
        "escrow_or_payment_id_present": bool(escrow_id),
    }


def _assignment_summary(row: dict[str, Any], payout_safe: bool) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    title = str(_pick(row, "title", "name", "task.title", "assignment.title") or "")[:180]
    description = str(_pick(row, "description", "task.description", "assignment.description", "instructions") or "")
    status = str(_pick(row, "status", "state", "assignment.status") or "UNKNOWN").upper()
    deadline = _dt(_pick(row, "deadline", "deadlineAt", "expiresAt", "dueAt", "assignment.deadline"))
    reward = _dec(_pick(row, "reward", "rewardUsd", "reward_usd", "amount", "payout", "payment.amount", "task.reward"))
    funded, funding_evidence = _funding(row)
    policy = _policy(title + " " + description)
    current = deadline is None or deadline > now
    action_state = status in {"PENDING", "OFFERED", "ASSIGNED", "AVAILABLE", "READY", "ACCEPTED", "IN_PROGRESS"}
    ready = bool(
        policy != "REJECT"
        and action_state
        and current
        and reward is not None
        and reward > 0
        and funded
        and payout_safe
    )
    return {
        "id": str(_pick(row, "id", "assignment_id", "assignmentId") or "")[:128],
        "title": title,
        "status": status[:64],
        "deadline": deadline.isoformat().replace("+00:00", "Z") if deadline else "UNKNOWN",
        "deadline_current": current,
        "reward": str(reward) if reward is not None else "UNKNOWN",
        "funding_verified": funded,
        "funding_evidence": funding_evidence,
        "atm_policy": policy,
        "action_state_supported": action_state,
        "payout_path_safe": payout_safe,
        "ready_for_accept": ready,
    }


def _transfer_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    safe_rows = []
    for row in rows[:50]:
        safe_rows.append({
            "status": str(_pick(row, "status", "state") or "UNKNOWN")[:64],
            "amount": _decimal(_pick(row, "amount", "amountUsd", "amount_usd", "value")) or "UNKNOWN",
            "currency": str(_pick(row, "currency", "asset") or "UNKNOWN")[:32],
            "terminal_tx_present": bool(_pick(row, "txHash", "tx_hash", "transactionHash", "transaction_hash")),
        })
    return {"count": len(rows), "recent": safe_rows}


def main() -> int:
    me = _get("/api/agents/me")
    work = _get("/api/agents/work")
    feed = _get("/api/agents/feed")
    earnings = _get("/api/agents/earnings")
    inbox = _get("/api/agents/me/inbox")
    engagement = _get("/api/agents/me/engagement/tasks")
    payout_destination = _get("/api/agents/me/payout-destination")
    transfers = _get("/api/agents/transfers")
    payouts = _get("/api/payouts")

    if not isinstance(me, dict):
        raise RuntimeError("AGENTHANSA_ME_UNEXPECTED_SHAPE")
    agent_id = str(_pick(me, "id", "agent.id", "agentId", "agent_id") or "").strip()
    agent_name = str(_pick(me, "name", "agent.name", "handle", "agent.handle") or "").strip()
    if not (agent_id or agent_name):
        raise RuntimeError("AGENTHANSA_ME_IDENTITY_MISSING")

    work_rows = _rows(work, "work", "jobs", "tasks", "data")
    work_summaries = [_task_summary(row) for row in work_rows]
    eligible = [row for row in work_summaries if row["atm_policy"] != "REJECT"]
    rejected = [row for row in work_summaries if row["atm_policy"] == "REJECT"]

    payout = _payout_destination(payout_destination)
    assignment_rows = _rows(engagement, "assignments", "tasks", "data")
    assignments = [_assignment_summary(row, bool(payout["safe_for_atm"])) for row in assignment_rows]
    ready_assignments = [row for row in assignments if row["ready_for_accept"]]
    inbox_rows = _rows(inbox, "notifications", "items", "data", "tasks")
    transfer_rows = _rows(transfers, "transfers", "items", "data")
    payout_rows = _rows(payouts, "payouts", "items", "data")

    receipt = {
        "schema": "ATM_ORDER011_AGENTHANSA_ACTION_PAYOUT_PROBE_V2",
        "order": "ORDER-011",
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "auth_probe": "PASS",
        "agent_identity_present": True,
        "duplicate_registration_performed": False,
        "agent_me": "PASS",
        "work_read": "PASS",
        "feed_read": "PASS",
        "inbox_read": "PASS",
        "engagement_read": "PASS",
        "earnings_read": "PASS",
        "payout_destination_read": "PASS",
        "transfers_read": "PASS",
        "payouts_read": "PASS",
        "work_count": len(work_rows),
        "eligible_for_further_verification_count": len(eligible),
        "policy_rejected_count": len(rejected),
        "work": work_summaries[:40],
        "inbox_item_count": len(inbox_rows),
        "engagement_assignment_count": len(assignment_rows),
        "assignments": assignments[:40],
        "ready_for_accept_count": len(ready_assignments),
        "ready_for_accept_ids": [row["id"] for row in ready_assignments if row["id"]][:20],
        "earnings": _safe_earnings(earnings),
        "payout_destination": payout,
        "transfers": _transfer_summary(transfer_rows),
        "payout_history_count": len(payout_rows),
        "feed_shape": "LIST" if isinstance(feed, list) else ("OBJECT" if isinstance(feed, dict) else "OTHER"),
        "claim_performed": False,
        "submission_performed": False,
        "payout_request_performed": False,
        "outgoing_spend_usd": "0",
        "owner_action_required": "NO",
        "global_block": False,
    }
    Path("order010-agenthansa-probe-receipt.json").write_text(
        json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, sort_keys=True))
    print("AGENTHANSA_AUTH_PROBE=PASS")
    print("AGENTHANSA_READY_FOR_ACCEPT=" + str(len(ready_assignments)))
    print("OWNER_ACTION_REQUIRED=NO")
    print("OUTGOING_SPEND_USD=0")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"AGENTHANSA_PROBE_FAIL_CLOSED={type(exc).__name__}:{exc}", file=sys.stderr)
        raise
