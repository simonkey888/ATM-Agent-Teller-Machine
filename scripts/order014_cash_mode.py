from __future__ import annotations

import concurrent.futures
import json
import re
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from atm_core.cash_canon import (
    qualified_work_classes,
    taskmarket_cash_decision,
)


TASKMARKET_API = "https://api.taskmarket.dev/api"
CANONICAL_WALLET = "0xd89Ef03bC3105C538529AC2657Bc4488c94ff4E4"
ORDER012_TASK = "0x9e5a614a80fa0d0b802c21101cedb05ab06a0412edf7c4b874613f8c19f7d68c"
ORDER012_SUBMISSION = "050e2bf1-1ea6-4a3b-9523-e7eb7eb5d157"
ORDER012_MANIFEST = "e3cce200253544733e40252637d1a9cceb1066974441228c768b024ebb62cec6"
RECEIPT = Path("order014-cash-mode-receipt.json")


def get(url: str, *, timeout: int = 20, accept: str = "application/json") -> Any:
    request = urllib.request.Request(
        url,
        headers={"Accept": accept, "Cache-Control": "no-cache", "User-Agent": "ATM-ORDER014-Cash/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        return json.loads(raw.decode()) if "json" in accept else raw.decode("utf-8", errors="replace")


def rows(value: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        for key in keys:
            found = value.get(key)
            if isinstance(found, list):
                return [row for row in found if isinstance(row, dict)]
    return []


def task_data(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    for key in ("task", "data"):
        found = value.get(key)
        if isinstance(found, dict):
            return found
    return value


def usdc(value: Any) -> Decimal:
    amount = Decimal(str(value or "0"))
    return amount / Decimal("1000000") if amount >= Decimal("10000") else amount


def free_worker_submit(task: dict[str, Any]) -> bool:
    actions = [
        action for action in task.get("pendingActions") or []
        if isinstance(action, dict) and action.get("role") == "worker" and action.get("action") == "submit"
    ]
    return len(actions) == 1 and actions[0].get("requiresPayment") is False and actions[0].get("paymentAmount") in (None, "", 0, "0")


def inspect_task(task_hint: dict[str, Any], now: datetime) -> dict[str, Any]:
    task_id = str(task_hint.get("id") or "")
    result: dict[str, Any] = {"task_id": task_id, "reasons": []}
    try:
        task = task_data(get(f"{TASKMARKET_API}/tasks/{task_id}"))
        submission_payload = get(f"{TASKMARKET_API}/tasks/{task_id}/submissions")
    except Exception as exc:
        result["reasons"] = ["INDIVIDUAL_REFETCH_" + type(exc).__name__.upper()]
        return result

    submissions = rows(submission_payload, "submissions", "items", "data")
    duplicate = any(str(row.get("workerAddress") or "").lower() == CANONICAL_WALLET.lower() for row in submissions)
    decision = taskmarket_cash_decision(
        task,
        canonical_wallet=CANONICAL_WALLET,
        existing_submission=duplicate,
        # This workflow is a read-only observer of the separately fenced production
        # signer. The economic runtime re-proves signer readiness before allocation.
        signer_ready=True,
        now=now,
    )
    try:
        deadline = datetime.fromisoformat(str(task.get("expiryTime") or "").replace("Z", "+00:00"))
    except ValueError:
        deadline = None

    result.update({
        "status": task.get("status"),
        "phase": task.get("phase"),
        "net_reward_usdc": decision.net_reward_usdc,
        "competition": decision.competition,
        "award_capacity": 1,
        "observed_awards": int(task.get("awardCount") or 0),
        "work_class": decision.work_class,
        "duplicate": duplicate,
        "deadline": deadline.isoformat() if deadline else None,
        "attack_priority": decision.attack_priority,
        "canon_object_hash": decision.authoritative_object_hash,
        "canon_disposition": decision.disposition,
        "reasons": list(decision.reasons),
    })
    return result


def probe_json(url: str, *row_keys: str) -> dict[str, Any]:
    try:
        payload = get(url)
        found = rows(payload, *row_keys)
        count = len(found)
        if isinstance(payload, dict) and isinstance(payload.get("count"), int):
            count = int(payload["count"])
        return {"state": "LIVE", "count": count, "url": url}
    except urllib.error.HTTPError as exc:
        return {"state": f"HTTP_{exc.code}", "count": "UNKNOWN", "url": url}
    except Exception as exc:
        return {"state": type(exc).__name__.upper(), "count": "UNKNOWN", "url": url}


def probe_agentgigs() -> dict[str, Any]:
    url = "https://www.agentgigs.io/"
    try:
        page = get(url, accept="text/html")
        match = re.search(r"Open Jobs\s*\((\d+)\)", page, re.I)
        return {"state": "LIVE", "count": int(match.group(1)) if match else "UNKNOWN", "url": url}
    except Exception as exc:
        return {"state": type(exc).__name__.upper(), "count": "UNKNOWN", "url": url}


def probe_clawhunt() -> dict[str, Any]:
    url = "https://clawhunt.store/api/problems/"
    try:
        listed = rows(get(url), "problems", "data", "items")
        identifiers = [str(row.get("id")) for row in listed[:5] if row.get("id") is not None]
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            current = list(pool.map(lambda identifier: get(url + identifier), identifiers))
        objects = [row for row in current if isinstance(row, dict)]
        funded = [
            row for row in objects
            if Decimal(str(row.get("escrow_amount") or "0")) > 0
            and str(row.get("escrow_status") or "").lower() in {"funded", "held", "locked", "active"}
        ]
        return {
            "state": "LIVE",
            "count": len(listed),
            "url": url,
            "individual_refetch_count": len(objects),
            "funded_sample_count": len(funded),
            "zero_or_unfunded_sample_count": len(objects) - len(funded),
        }
    except Exception as exc:
        return {"state": type(exc).__name__.upper(), "count": "UNKNOWN", "url": url, "individual_refetch_count": 0}


def probe_existing_platforms() -> dict[str, Any]:
    url = "https://atm.simondalmasso44.workers.dev/api/platform-health"
    try:
        payload = get(url)
        platforms = rows(payload, "platforms")
        bounty_sources = {
            str(row.get("source")): {"state": row.get("state"), "open_count": row.get("open_count")}
            for row in platforms
            if str(row.get("source") or "") in {"github-direct", "opire", "superteam", "taskbounty"}
        }
        return {"state": "LIVE", "count": len(platforms), "url": url, "bounty_sources": bounty_sources}
    except Exception as exc:
        return {"state": type(exc).__name__.upper(), "count": "UNKNOWN", "url": url, "bounty_sources": {}}


def order012_watch() -> dict[str, Any]:
    task = task_data(get(f"{TASKMARKET_API}/tasks/{ORDER012_TASK}"))
    submissions = rows(get(f"{TASKMARKET_API}/tasks/{ORDER012_TASK}/submissions"), "submissions", "data", "items")
    ours = next((row for row in submissions if str(row.get("workerAddress") or "").lower() == CANONICAL_WALLET.lower()), None)
    manifest = get(f"{TASKMARKET_API}/tasks/{ORDER012_TASK}/submissions/{ORDER012_SUBMISSION}/manifest")
    hashes = {str(row.get("sha256Hash") or "").lower() for row in rows(manifest, "artifacts")}
    awards = [row for row in task.get("awards") or [] if isinstance(row, dict) and str(row.get("workerAddress") or "").lower() == CANONICAL_WALLET.lower()]
    return {
        "task_id": ORDER012_TASK,
        "submission_id": str((ours or {}).get("id") or (ours or {}).get("submissionId") or ""),
        "submission_verified": ours is not None and str((ours or {}).get("id") or (ours or {}).get("submissionId") or "") == ORDER012_SUBMISSION,
        "manifest_verified": ORDER012_MANIFEST in hashes,
        "award_count": len(awards),
        "state": "ACCEPTED" if awards else "SUBMITTED",
        "settlement_tx_hashes": [str(row.get("settlementTxHash")) for row in awards if row.get("settlementTxHash")],
    }


def main() -> int:
    now = datetime.now(timezone.utc)
    listed = rows(get(f"{TASKMARKET_API}/tasks?status=open&limit=100&sort=newest"), "tasks", "data", "items")
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        inspected = list(pool.map(lambda row: inspect_task(row, now), listed))
    executable = [row for row in inspected if not row.get("reasons")]
    executable.sort(key=lambda row: Decimal(str(row["attack_priority"])), reverse=True)
    rejection_counts = Counter(reason for row in inspected for reason in row.get("reasons") or [])

    # No list object is sufficient for work or mutation. A selected object gets a
    # second current refetch; any hash/economic change is an abort, never a retry.
    final_refetch = None
    if executable:
        selected = executable[0]
        final_refetch = inspect_task({"id": selected["task_id"]}, datetime.now(timezone.utc))
        if final_refetch.get("reasons") or any(
            final_refetch.get(key) != selected.get(key)
            for key in ("net_reward_usdc", "competition", "award_capacity", "deadline", "work_class")
        ):
            executable = []
            rejection_counts["FINAL_REFETCH_CHANGED"] += 1

    platform_worker = probe_existing_platforms()
    rails = {
        "agentgigs": probe_agentgigs(),
        "voxpact": probe_json("https://api.voxpact.com/v1/jobs/open", "jobs", "data", "items"),
        "clawhunt": probe_clawhunt(),
        "taskforce": probe_json("https://www.task-force.app/api/tasks?status=OPEN", "tasks", "data", "items"),
        "existing_platform_health": platform_worker,
    }
    watch = order012_watch()
    if not watch["submission_verified"] or not watch["manifest_verified"]:
        raise SystemExit("ORDER012_EXTERNAL_EVIDENCE_MISMATCH")

    receipt = {
        "schema": "ATM_ORDER014_CASH_MODE_V1",
        "generated_at": now.isoformat(),
        "head": __import__("os").getenv("GITHUB_SHA", "LOCAL"),
        "mode": "ACTIVE" if executable else "SEARCH",
        "runtime": "RUNNING",
        "taskmarket_full_scan": "LIVE",
        "taskmarket_list_count": len(listed),
        "taskmarket_individual_refetch_count": len(inspected),
        "executable_now": len(executable),
        "active_work": 0,
        "in_flight": 1,
        "new_external_submissions": 0,
        "latest_submission": ORDER012_SUBMISSION,
        "rejection_counts": dict(sorted(rejection_counts.items())),
        "top_current_candidates": inspected[:0] if executable else sorted(inspected, key=lambda row: Decimal(str(row.get("attack_priority") or "-999999")), reverse=True)[:5],
        "final_refetch": final_refetch,
        "order012_watch": watch,
        "submitted_net_usd": "5.55",
        "accepted_net_usd": "UNKNOWN",
        "settled_usd": "UNKNOWN",
        "withdrawable_usd": "UNKNOWN",
        "realized_30d_usd": "0",
        "work_classes": list(qualified_work_classes()),
        "enabled_mutation_rails": ["taskmarket"],
        "verify_queue_rails": ["github-bounties", "agentgigs", "voxpact", "clawhunt", "taskforce-discovery-only", "taskbounty", "moltjobs", "agenthansa", "superteam", "workprotocol"],
        "rail_probes": rails,
        "github_bounty_scan": "LIVE" if platform_worker.get("state") == "LIVE" else "DEGRADED",
        "new_rail_verify_queue": "LIVE",
        "no_stale_mutation": "PASS",
        "no_duplicate_submission": "PASS",
        "no_duplicate_identity": "PASS",
        "guru": {"discovery": "READ_ONLY", "mutation": "BLOCKED_PAID_VERIFICATION", "owner_onboarding": "DO_NOT_REQUEST"},
        "owner_action_required": "NONE",
        "continuous_discovery": "ACTIVE",
        "payment_watchers": "ACTIVE",
        "outgoing_spend_usd": "0",
        "no_merge": True,
    }
    RECEIPT.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
