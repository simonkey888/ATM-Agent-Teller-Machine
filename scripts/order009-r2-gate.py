"""ORDER-009-R2 read-only mutation gate over already-observed current supply."""
from __future__ import annotations

import hashlib
import html.parser
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

BASE_RPC = "https://mainnet.base.org"
SUPERTEAM = "https://superteam.fun"
TASKMARKET = "https://api.taskmarket.dev"
GEMINI_MODELS = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_PRICING = "https://ai.google.dev/gemini-api/docs/pricing"
GEMMA_ALLOWLIST = ("gemma-4-31b-it", "gemma-4-26b-a4b-it")
OUT = Path("order009-r2-gate-evidence.json")
NOW = datetime.now(timezone.utc)
SUPPORTED_REGION = {"", "GLOBAL", "REMOTE", "WORLDWIDE", "ARGENTINA", "LATAM", "LATIN AMERICA", "LATIN_AMERICA"}
UNSUPPORTED_EXECUTION = (
    "video", "film", "record yourself", "selfie", "podcast", "voiceover", "voice over",
    "image design", "graphic design", "3d game", "three.js game", "unity", "blender",
    "physical", "in person", "on-site", "onsite", "tweet", "x post", "instagram", "tiktok",
)
POLICY_REJECT = ("referral", "refer a friend", "recruit", "follow my", "like my", "subscribe", "gambling", "casino", "trading signals")


class TextExtractor(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def req(url: str, *, method: str = "GET", headers: dict[str, str] | None = None, body: Any = None, timeout: int = 25) -> tuple[bytes, int, dict[str, str]]:
    payload = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    hdr = {"User-Agent": "ATM-Order009-R2/1.0", "Accept": "application/json,text/plain,text/html"}
    hdr.update(headers or {})
    if payload is not None:
        hdr["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=payload, method=method, headers=hdr)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read(), int(response.status), {k.lower(): v for k, v in response.headers.items()}
    except urllib.error.HTTPError as exc:
        return exc.read(), int(exc.code), {k.lower(): v for k, v in exc.headers.items()}


def get_json(url: str, *, headers: dict[str, str] | None = None) -> Any:
    raw, status, _ = req(url, headers=headers)
    if status != 200:
        raise RuntimeError(f"HTTP_{status}:{urllib.parse.urlsplit(url).netloc}")
    return json.loads(raw.decode())


def dec(value: Any) -> Decimal:
    try:
        return Decimal(str(value if value not in (None, "") else "0"))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def iso(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000
        return datetime.fromtimestamp(number, tz=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def rows(payload: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        for key in ("data", "items", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def batch_receipts(tx_hashes: list[str]) -> dict[str, bool]:
    clean = list(dict.fromkeys(x for x in tx_hashes if re.fullmatch(r"0x[0-9a-fA-F]{64}", x or "")))
    if not clean:
        return {}
    body = [{"jsonrpc": "2.0", "id": i + 1, "method": "eth_getTransactionReceipt", "params": [tx]} for i, tx in enumerate(clean)]
    raw, status, _ = req(BASE_RPC, method="POST", body=body)
    if status != 200:
        return {tx: False for tx in clean}
    payload = json.loads(raw.decode())
    by_id = {int(item.get("id")): item.get("result") for item in payload if isinstance(item, dict) and item.get("id") is not None} if isinstance(payload, list) else {}
    return {tx: bool((by_id.get(i + 1) or {}).get("status") == "0x1") for i, tx in enumerate(clean)}


def gemma_gate() -> dict[str, Any]:
    key = os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip() or os.getenv("GOOGLE_AI_API_KEY", "").strip()
    result = {"credential_present": bool(key), "model": None, "model_available": False, "free_only_proven": False, "paid_fallback": False}
    if not key:
        return result
    raw, status, _ = req(GEMINI_PRICING, headers={"Accept": "text/html"})
    if status == 200:
        parser = TextExtractor()
        parser.feed(raw.decode(errors="replace"))
        text = " ".join(parser.parts)
        start = text.find("Gemma 4")
        end = text.find("Pricing for tools", start) if start >= 0 else -1
        section = text[start : end if end > start else start + 5000] if start >= 0 else ""
        result["free_only_proven"] = section.count("Free of charge") >= 4 and section.count("Not available") >= 4
    payload = get_json(GEMINI_MODELS + "?" + urllib.parse.urlencode({"key": key}))
    available = {str(x.get("name") or "").split("/", 1)[-1] for x in rows(payload, "models")}
    model = next((name for name in GEMMA_ALLOWLIST if name in available), None)
    result["model"] = model
    result["model_available"] = bool(model)
    return result


def superteam_terms() -> bool:
    raw, status, _ = req(SUPERTEAM + "/skill.md", headers={"Accept": "text/plain"})
    if status != 200:
        return False
    text = raw.decode(errors="replace")
    required = ("Agent Eligibility Rules", "Human", "claimCode", "/api/agents/submissions/create")
    return all(token in text for token in required)


def region_allowed(item: dict[str, Any]) -> bool:
    raw = item.get("regions") if item.get("regions") not in (None, "", []) else item.get("region")
    if raw in (None, "", []):
        return True
    values = raw if isinstance(raw, list) else [raw]
    normalized = set()
    for value in values:
        if isinstance(value, dict):
            value = value.get("name") or value.get("slug") or value.get("code")
        if value not in (None, ""):
            normalized.add(str(value).strip().upper().replace("-", "_"))
    return bool(normalized) and any(value in SUPPORTED_REGION for value in normalized)


def executor_supported(title: str, description: str) -> bool:
    text = re.sub(r"\s+", " ", f"{title} {description}".lower())
    return not any(term in text for term in UNSUPPORTED_EXECUTION)


def policy_ok(title: str, description: str) -> bool:
    text = re.sub(r"\s+", " ", f"{title} {description}".lower())
    return not any(term in text for term in POLICY_REJECT)


def superteam_candidates(executor: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    key = os.getenv("SUPERTEAM_AGENT_API_KEY", "").strip()
    meta = {"credential_present": bool(key), "terms_current": False, "live_total": None, "detail_failures": 0}
    if not key:
        return [], meta
    headers = {"Authorization": f"Bearer {key}"}
    meta["terms_current"] = superteam_terms()
    live = get_json(SUPERTEAM + "/api/agents/listings/live?take=50", headers=headers)
    live_rows = rows(live, "listings", "data")
    meta["live_total"] = len(live_rows)
    out = []
    for row in live_rows:
        slug = str(row.get("slug") or "").strip()
        if not slug:
            continue
        try:
            detail = get_json(SUPERTEAM + "/api/agents/listings/details/" + urllib.parse.quote(slug), headers=headers)
        except Exception:
            meta["detail_failures"] += 1
            continue
        listing = detail.get("listing") if isinstance(detail, dict) and isinstance(detail.get("listing"), dict) else detail
        if not isinstance(listing, dict):
            meta["detail_failures"] += 1
            continue
        status = str(listing.get("status") or "").upper()
        access = str(listing.get("agentAccess") or "").upper()
        deadline = iso(listing.get("deadline"))
        title = str(listing.get("title") or row.get("title") or slug)
        description = str(listing.get("description") or listing.get("summary") or row.get("description") or "")
        reward = dec(listing.get("reward") or listing.get("rewardAmount") or listing.get("amount") or row.get("reward") or row.get("rewardAmount"))
        currency = str(listing.get("rewardCurrency") or listing.get("currency") or row.get("rewardCurrency") or "USD").upper()
        competition = int(listing.get("submissionCount") or row.get("submissionCount") or 0)
        gates = {
            "current_object": True,
            "alive": status in {"OPEN", "LIVE", "ACTIVE", "PUBLISHED"} and (deadline is None or deadline > NOW),
            "agent_policy": access in {"AGENT_ALLOWED", "AGENT_ONLY"},
            "mutation_cost_zero": True,
            "stake_required_no": True,
            "automation_permitted": access in {"AGENT_ALLOWED", "AGENT_ONLY"} and policy_ok(title, description),
            "funding_or_payment_path_verified": bool(meta["terms_current"] and reward > 0 and currency in {"USD", "USDC"}),
            "executor_ready": bool(executor.get("model_available") and executor.get("free_only_proven") and executor_supported(title, description)),
            "paid_fallback_no": not bool(executor.get("paid_fallback")),
            "outgoing_spend_zero": True,
            "withdrawal_path_known": bool(meta["terms_current"]),
            "region_allowed": region_allowed(listing),
        }
        score = float(reward) / (1 + max(0, competition)) if all(gates.values()) else 0.0
        out.append({
            "source": "superteam", "id": str(listing.get("id") or row.get("id") or slug), "slug": slug,
            "url": str(listing.get("url") or f"{SUPERTEAM}/earn/listing/{slug}"), "title": title[:220],
            "description": description[:1200], "type": str(listing.get("type") or row.get("type") or ""),
            "reward": str(reward), "currency": currency, "competition": competition,
            "deadline": deadline.isoformat() if deadline else None, "agent_access": access,
            "compensation_type": listing.get("compensationType"), "eligibility_questions": listing.get("eligibilityQuestions") or [],
            "gates": gates, "economic_score": round(score, 6), "signature_required": False,
            "payment_path": "Superteam agent winner -> human claimCode claim -> verified payout profile",
            "source_state_hash": hashlib.sha256(json.dumps(listing, sort_keys=True, default=str).encode()).hexdigest(),
        })
    return out, meta


def taskmarket_candidates(executor: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = get_json(TASKMARKET + "/api/tasks?status=open&limit=50&sort=newest")
    items = rows(payload, "tasks")
    txs = [str(x.get("escrowTxHash") or "") for x in items]
    receipts = batch_receipts(txs)
    out = []
    for task in items:
        tid = str(task.get("id") or "")
        if not tid:
            continue
        mode = str(task.get("mode") or "bounty").lower()
        stake = bool(task.get("stakeRequired"))
        deadline = iso(task.get("expiryTime"))
        status = str(task.get("status") or "").lower()
        title = str(task.get("title") or task.get("description") or tid)
        description = str(task.get("description") or title)
        raw_reward = dec(task.get("reward"))
        reward = raw_reward / Decimal("1000000") if raw_reward >= Decimal("10000") else raw_reward
        tx = str(task.get("escrowTxHash") or "")
        competition = int(task.get("submissionCount") or 0) + int(task.get("pitchCount") or 0)
        chain_ok = bool(receipts.get(tx))
        gates = {
            "current_object": True,
            "alive": status == "open" and (deadline is None or deadline > NOW),
            "agent_policy": policy_ok(title, description),
            "mutation_cost_zero": mode in {"bounty", "claim"},
            "stake_required_no": not stake,
            "automation_permitted": policy_ok(title, description),
            "funding_or_payment_path_verified": chain_ok and reward > 0,
            "executor_ready": bool(executor.get("model_available") and executor.get("free_only_proven") and executor_supported(title, description)),
            "paid_fallback_no": not bool(executor.get("paid_fallback")),
            "outgoing_spend_zero": True,
            "withdrawal_path_known": chain_ok,
        }
        score = float(reward) / (1 + max(0, competition)) if all(gates.values()) else 0.0
        out.append({
            "source": "taskmarket-daydreams", "id": tid, "slug": None,
            "url": f"https://taskmarket.dev/tasks/{urllib.parse.quote(tid)}", "title": title[:220],
            "description": description[:1200], "type": mode, "reward": str(reward), "currency": "USDC",
            "competition": competition, "deadline": deadline.isoformat() if deadline else None,
            "agent_access": "AGENT_ALLOWED", "compensation_type": "fixed", "eligibility_questions": [],
            "gates": gates, "economic_score": round(score, 6), "signature_required": True,
            "payment_path": "Base USDC escrow -> accepted award settlement -> worker wallet",
            "escrow_tx": tx or None, "escrow_tx_confirmed_on_base": chain_ok,
            "source_state_hash": hashlib.sha256(json.dumps(task, sort_keys=True, default=str).encode()).hexdigest(),
        })
    return out, {"open_total": len(items), "base_receipts_checked": len(receipts)}


def main() -> int:
    executor = gemma_gate()
    super_rows, super_meta = superteam_candidates(executor)
    task_rows, task_meta = taskmarket_candidates(executor)
    candidates = super_rows + task_rows
    passing = [row for row in candidates if all(row["gates"].values())]
    passing.sort(key=lambda x: (x["economic_score"], dec(x["reward"])), reverse=True)
    selected = passing[0] if passing else None
    safe_candidates = sorted(candidates, key=lambda x: (x["economic_score"], dec(x["reward"])), reverse=True)[:20]
    evidence = {
        "schema": "ATM_ORDER009_R2_GATE_V1", "head": os.getenv("GITHUB_SHA", "LOCAL"),
        "generated_at": NOW.isoformat(), "mode": "READ_ONLY", "external_mutation": 0,
        "outgoing_spend_usd": "0", "merge": False, "workprotocol_targets_forbidden": True,
        "executor": executor, "superteam": super_meta, "taskmarket": task_meta,
        "passing_count": len(passing), "selected": selected, "candidates": safe_candidates,
        "terminal": "READY_FOR_MUTATION" if selected else "NO_VERIFIED_MUTATION_CANDIDATE",
    }
    OUT.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    compact = dict(evidence)
    compact["candidates"] = [{k: row[k] for k in ("source", "id", "slug", "url", "title", "reward", "currency", "competition", "type", "economic_score", "signature_required", "gates", "source_state_hash") if k in row} for row in safe_candidates]
    if compact.get("selected"):
        selected_safe = compact["selected"]
        compact["selected"] = {k: selected_safe[k] for k in ("source", "id", "slug", "url", "title", "reward", "currency", "competition", "type", "economic_score", "signature_required", "gates", "source_state_hash") if k in selected_safe}
    print(json.dumps(compact, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
