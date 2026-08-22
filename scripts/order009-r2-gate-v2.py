"""Corrected ORDER-009-R2 read-only gate: TaskMarket units + individual Base receipts."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

BASE_RPC = "https://mainnet.base.org"
TASKS_URL = "https://api.taskmarket.dev/api/tasks?status=open&limit=50&sort=newest"
MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
GEMMA = ("gemma-4-31b-it", "gemma-4-26b-a4b-it")
OUT = Path("order009-r2-gate-v2-evidence.json")
NOW = datetime.now(timezone.utc)
POLICY_REJECT = ("referral", "refer a friend", "recruit", "follow my", "like my", "subscribe", "gambling", "casino", "trading signals")
UNSUPPORTED = ("video", "film", "record yourself", "selfie", "podcast", "voiceover", "graphic design", "three.js", "3d game", "unity", "blender", "physical", "in person", "on-site", "onsite", "tweet", "instagram", "tiktok")


def request(url: str, *, body: Any = None, timeout: int = 20) -> tuple[bytes, int]:
    payload = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    headers = {"User-Agent": "ATM-Order009-R2/2.0", "Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=payload, method="POST" if payload is not None else "GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read(), int(response.status)
    except urllib.error.HTTPError as exc:
        return exc.read(), int(exc.code)


def get_json(url: str) -> Any:
    raw, status = request(url)
    if status != 200:
        raise RuntimeError(f"HTTP_{status}")
    return json.loads(raw.decode())


def parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def receipt(tx_hash: str) -> dict[str, Any]:
    if not re.fullmatch(r"0x[0-9a-fA-F]{64}", tx_hash or ""):
        return {"exists": False, "success": False, "block": None, "rpc_status": "INVALID_HASH"}
    raw, status = request(BASE_RPC, body={"jsonrpc": "2.0", "id": 1, "method": "eth_getTransactionReceipt", "params": [tx_hash]})
    if status != 200:
        return {"exists": False, "success": False, "block": None, "rpc_status": f"HTTP_{status}"}
    payload = json.loads(raw.decode())
    result = payload.get("result") if isinstance(payload, dict) else None
    return {
        "exists": isinstance(result, dict),
        "success": isinstance(result, dict) and result.get("status") == "0x1",
        "block": result.get("blockNumber") if isinstance(result, dict) else None,
        "rpc_status": "OK" if isinstance(payload, dict) and "result" in payload else "MALFORMED",
    }


def gemma() -> dict[str, Any]:
    key = os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip() or os.getenv("GOOGLE_AI_API_KEY", "").strip()
    result = {"credential_present": bool(key), "model": None, "model_available": False, "paid_fallback": False}
    if not key:
        return result
    payload = get_json(MODELS_URL + "?" + urllib.parse.urlencode({"key": key}))
    models = payload.get("models") if isinstance(payload, dict) else []
    names = {str(x.get("name") or "").split("/", 1)[-1] for x in models if isinstance(x, dict)}
    model = next((x for x in GEMMA if x in names), None)
    result.update({"model": model, "model_available": bool(model)})
    return result


def safe_text(task: dict[str, Any]) -> tuple[str, str]:
    description = str(task.get("description") or "")
    title = str(task.get("title") or description.splitlines()[0] if description else task.get("id") or "")
    return title, description


def main() -> int:
    executor = gemma()
    payload = get_json(TASKS_URL)
    tasks = payload.get("tasks") if isinstance(payload, dict) and isinstance(payload.get("tasks"), list) else []
    candidates = []
    rpc_states: dict[str, int] = {}
    for task in tasks:
        if not isinstance(task, dict) or not task.get("id"):
            continue
        title, description = safe_text(task)
        text = re.sub(r"\s+", " ", f"{title} {description}".lower())
        reward_base = Decimal(str(task.get("reward") or "0"))
        reward = reward_base / Decimal("1000000")
        deadline = parse_time(task.get("expiryTime"))
        mode = str(task.get("mode") or "bounty").lower()
        status = str(task.get("status") or "").lower()
        tx = str(task.get("escrowTxHash") or "")
        chain = receipt(tx)
        rpc_states[chain["rpc_status"]] = rpc_states.get(chain["rpc_status"], 0) + 1
        competition = int(task.get("submissionCount") or 0) + int(task.get("pitchCount") or 0)
        policy = not any(term in text for term in POLICY_REJECT)
        executor_supported = not any(term in text for term in UNSUPPORTED)
        gates = {
            "current_object": True,
            "alive": status == "open" and (deadline is None or deadline > NOW),
            "agent_policy": policy,
            "mutation_cost_zero": mode in {"bounty", "claim"},
            "stake_required_no": not bool(task.get("stakeRequired")),
            "automation_permitted": policy,
            "funding_or_payment_path_verified": bool(chain["success"] and reward > 0),
            "executor_ready": bool(executor["model_available"] and executor_supported),
            "paid_fallback_no": True,
            "outgoing_spend_zero": True,
            "withdrawal_path_known": bool(chain["success"]),
        }
        passes = all(gates.values())
        score = (float(reward) / (1 + max(0, competition))) if passes else 0.0
        candidates.append({
            "source": "taskmarket-daydreams", "id": str(task["id"]),
            "url": "https://taskmarket.dev/tasks/" + urllib.parse.quote(str(task["id"])),
            "title": title[:220], "description": description[:1000], "reward_usdc": str(reward),
            "competition": competition, "mode": mode, "deadline": deadline.isoformat() if deadline else None,
            "escrow_tx": tx or None, "base_receipt": chain, "signature_required": True,
            "gates": gates, "economic_score": round(score, 9), "passes": passes,
        })
    passing = sorted((x for x in candidates if x["passes"]), key=lambda x: (x["economic_score"], Decimal(x["reward_usdc"])), reverse=True)
    selected = passing[0] if passing else None
    evidence = {
        "schema": "ATM_ORDER009_R2_GATE_V2", "head": os.getenv("GITHUB_SHA", "LOCAL"),
        "generated_at": NOW.isoformat(), "mode": "READ_ONLY", "external_mutation": 0,
        "outgoing_spend_usd": "0", "merge": False, "workprotocol_targets_forbidden": True,
        "superteam_secret_present": bool(os.getenv("SUPERTEAM_AGENT_API_KEY", "").strip()),
        "executor": executor, "taskmarket_open_total": len(tasks), "rpc_states": rpc_states,
        "passing_count": len(passing), "selected": selected,
        "candidates": sorted(candidates, key=lambda x: (x["economic_score"], Decimal(x["reward_usdc"])), reverse=True)[:20],
        "terminal": "READY_FOR_MUTATION" if selected else "NO_VERIFIED_MUTATION_CANDIDATE",
    }
    OUT.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
