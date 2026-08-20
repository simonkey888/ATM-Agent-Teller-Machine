#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import urllib.request
from decimal import Decimal
from typing import Any

from atm_core.order009_sources import RAIL_CAPABILITIES
from atm_core.radar_registry import build_canonical_registry
from atm_core.universal_radar import OperationalState, SourceState, UniversalRadar

MARKER = "ATM UNIVERSAL RADAR SNAPSHOT"
DEFAULT_REPO = "simonkey888/ATM-Agent-Teller-Machine"
DEFAULT_ISSUE = 7


def safe_opp(item: Any) -> dict[str, Any]:
    return {
        "id": item.canonical_id,
        "source": item.source,
        "source_object_type": getattr(item, "source_object_type", "job"),
        "url": item.canonical_url,
        "title": str(item.title)[:180],
        "category": item.category,
        "observed_at": str(getattr(item, "observed_at", "")) or None,
        "deadline": item.deadline.isoformat() if item.deadline else None,
        "payout_usd": str(item.payout_usd) if item.payout_usd is not None else None,
        "payout_net": str(item.payout_net),
        "currency": getattr(item, "currency", item.payout_currency),
        "funding_status": item.funding_status.value,
        "funding_state": getattr(item, "funding_state", item.funding_status.value),
        "competition": item.competition,
        "claims": item.claims,
        "submissions": getattr(item, "submissions", None),
        "open_prs": item.open_prs,
        "agent_policy": item.agent_policy.value,
        "eligibility": str(item.eligibility)[:180],
        "human_gate": bool(item.human_gate),
        "executor": item.executor_class.value,
        "executor_health": getattr(item, "executor_health", "UNPROBED"),
        "freshness_state": getattr(item, "freshness_state", "UNKNOWN"),
        "disposition": item.disposition.value,
        "operational_state": item.operational_state.value,
        "money_velocity": str(item.money_velocity_score),
        "payment_rail": str(item.payment_rail)[:120],
        "withdrawal_path": str(item.withdrawal_path)[:160],
        "rejection_reasons": list(item.rejection_reasons)[:8],
        "payment_state": getattr(item, "payment_state", None),
        "authoritative_paid": bool(getattr(item, "authoritative_paid", False)),
        "source_state_hash": item.source_state_hash,
    }


def build_payload(source_sha: str) -> dict[str, Any]:
    registry = build_canonical_registry()
    snapshot = UniversalRadar(registry, floor_usd=Decimal("5"), per_source_timeout=4).scan()
    executable = [x for x in snapshot.opportunities if x.operational_state == OperationalState.EXECUTABLE]
    rows = sorted(executable, key=lambda x: (x.money_velocity_score, x.payout_net), reverse=True)
    healthy = sum(1 for row in snapshot.platform_health if row.state == SourceState.HEALTHY)
    slop_rejected = sum(1 for row in snapshot.opportunities if row.operational_state == OperationalState.REJECTED_SLOP)
    return {
        "schema": "ATM_UNIVERSAL_RADAR_PUBLIC_V3",
        "source_sha": source_sha,
        "source_branch": "pivot/universal-money-radar-r1",
        "generated_at": snapshot.generated_at.isoformat(),
        "outgoing_spend_usd": "0",
        "source_count": len(registry.names),
        "radar_sources": registry.names,
        "scanned_count": len(snapshot.opportunities),
        "opportunity_count": len(executable),
        "current_candidates": len(executable),
        "executable_now": len(executable),
        "attack_now_count": len(executable),
        "healthy_source_count": healthy,
        "slop_rejected_last_scan": slop_rejected,
        "canonical_duplicate_count": snapshot.canonical_duplicate_count,
        "rejection_counts": snapshot.rejection_counts,
        "opportunities": [safe_opp(x) for x in rows[:60]],
        "platform_health": [
            {
                "source": row.source,
                "state": row.state.value,
                "checked_at": row.checked_at.isoformat(),
                "open_count": row.open_count,
                "detail": str(row.detail)[:220],
            }
            for row in snapshot.platform_health
        ],
        "rail_capabilities": RAIL_CAPABILITIES,
        "source_errors": {str(k): str(v)[:180] for k, v in snapshot.source_errors.items()},
    }


def request_json(method: str, url: str, token: str, body: dict[str, Any] | None = None) -> Any:
    raw = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    req = urllib.request.Request(
        url,
        data=raw,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ATM-Order009-Radar-Publisher/1.0",
            **({"Content-Type": "application/json"} if raw is not None else {}),
        },
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        data = response.read()
        return json.loads(data.decode()) if data else {}


def publish(payload: dict[str, Any], *, repo: str, issue: int, token: str) -> str:
    api = f"https://api.github.com/repos/{repo}/issues/{issue}/comments"
    issue_data = request_json("GET", f"https://api.github.com/repos/{repo}/issues/{issue}", token)
    comment_count = int(issue_data.get("comments") or 0) if isinstance(issue_data, dict) else 0
    page = max(1, (comment_count + 99) // 100)
    comments = request_json("GET", api + f"?per_page=100&page={page}", token)
    body = MARKER + "\n```json\n" + json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n```"
    target = None
    if isinstance(comments, list):
        for row in reversed(comments):
            login = str((row.get("user") or {}).get("login") or "") if isinstance(row, dict) else ""
            association = str(row.get("author_association") or "") if isinstance(row, dict) else ""
            trusted = login == "github-actions[bot]" or (login == "simonkey888" and association in {"OWNER", "MEMBER", "COLLABORATOR"})
            if trusted and str(row.get("body") or "").startswith(MARKER):
                target = row.get("url")
                break
    if target:
        result = request_json("PATCH", str(target), token, {"body": body})
        return str(result.get("html_url") or target)
    result = request_json("POST", api, token, {"body": body})
    return str(result.get("html_url") or api)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    source_sha = os.getenv("RADAR_SOURCE_SHA") or os.getenv("GITHUB_SHA") or "UNKNOWN"
    payload = build_payload(source_sha)
    if payload["outgoing_spend_usd"] != "0":
        raise SystemExit("RADAR_PUBLICATION_SPEND_GUARD")
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, indent=2)
            handle.write("\n")
    ref = "NOT_PUBLISHED"
    if args.publish:
        token = os.getenv("GITHUB_TOKEN", "").strip()
        if not token:
            raise SystemExit("GITHUB_TOKEN_REQUIRED_FOR_DURABLE_RADAR_PUBLICATION")
        ref = publish(payload, repo=os.getenv("GITHUB_REPOSITORY", DEFAULT_REPO), issue=DEFAULT_ISSUE, token=token)
    print(json.dumps({
        "schema": payload["schema"],
        "source_sha": payload["source_sha"],
        "source_count": payload["source_count"],
        "opportunity_count": payload["opportunity_count"],
        "attack_now_count": payload["attack_now_count"],
        "outgoing_spend_usd": payload["outgoing_spend_usd"],
        "durable_ref": ref,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
