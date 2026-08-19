from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from atm_core.simon_gate import GuruPublicSource, SimonGateError, TelegramBot, notification_text


RECEIPT = Path("order010-simon-gate-receipt.json")


def _repo_variable(name: str, value: str) -> str:
    """Persist non-secret SimonGate state in a GitHub Actions repository variable."""
    token = os.getenv("GITHUB_TOKEN", "").strip()
    repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    if not token or not repo:
        return "NO_GITHUB_WRITE_CONTEXT"
    base = f"https://api.github.com/repos/{repo}/actions/variables"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "ATM-SimonGate/1.0",
    }
    encoded = urllib.parse.quote(name, safe="")
    get_req = urllib.request.Request(f"{base}/{encoded}", headers=headers)
    exists = False
    try:
        with urllib.request.urlopen(get_req, timeout=15) as response:
            exists = response.status == 200
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            return f"VARIABLE_READ_HTTP_{exc.code}"
    except Exception as exc:
        return f"VARIABLE_READ_{type(exc).__name__}"
    body = json.dumps({"name": name, "value": str(value)}).encode()
    req = urllib.request.Request(
        f"{base}/{encoded}" if exists else base,
        data=body,
        headers=headers,
        method="PATCH" if exists else "POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            if exists:
                return "UPDATED" if response.status in {200, 204} else f"HTTP_{response.status}"
            return "CREATED" if response.status in {201, 204} else f"HTTP_{response.status}"
    except urllib.error.HTTPError as exc:
        return f"VARIABLE_WRITE_HTTP_{exc.code}"
    except Exception as exc:
        return f"VARIABLE_WRITE_{type(exc).__name__}"


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    head = os.getenv("ORDER010_HEAD", os.getenv("GITHUB_SHA", "UNKNOWN")).strip() or "UNKNOWN"
    source = GuruPublicSource()
    receipt: dict[str, Any] = {
        "schema": "ATM_ORDER010_SG2_SIMON_GATE_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "head": head,
        "lane": "SIMON_GATE",
        "parallel_to_autonomous": True,
        "source": "guru",
        "source_url": source.url,
        "discovery_mode": "PUBLIC_CREDENTIALLESS",
        "platform_password_or_cookie_required": False,
        "outgoing_spend_usd": "0",
        "paid_boost_or_membership": False,
        "state": "DISCOVERY_STARTED",
    }

    try:
        leads = source.discover()
    except Exception as exc:
        receipt.update({"state": "SOURCE_DEGRADED", "source_error": type(exc).__name__, "qualified_count": 0})
        RECEIPT.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print("SIMON_GATE_STATE=SOURCE_DEGRADED")
        print("OUTGOING_SPEND_USD=0")
        return 0

    receipt["qualified_count"] = len(leads)
    if not leads:
        receipt["state"] = "NO_CURRENT_QUALIFIED_LEAD"
        RECEIPT.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print("SIMON_GATE_STATE=NO_CURRENT_QUALIFIED_LEAD")
        print("OUTGOING_SPEND_USD=0")
        return 0

    selected = leads[0]
    receipt["selected"] = selected.public_dict()
    receipt["state"] = "PROPOSAL_READY"

    bot_token = os.getenv("ATM_RADAR_BOT_API_KEY", "").strip()
    if not bot_token:
        receipt["telegram_state"] = "BLOCKED_CAPABILITY_ATM_RADAR_BOT_API_KEY"
        RECEIPT.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print("SIMON_GATE_STATE=PROPOSAL_READY")
        print("TELEGRAM_STATE=BLOCKED_CAPABILITY_ATM_RADAR_BOT_API_KEY")
        print("OUTGOING_SPEND_USD=0")
        return 0

    bot = TelegramBot(bot_token)
    updates = bot.updates()
    chat_raw = os.getenv("ATM_TELEGRAM_CHAT_ID", "").strip()
    newly_paired = False
    try:
        chat_id = int(chat_raw) if chat_raw else bot.pair_chat_id(updates)
    except (ValueError, SimonGateError):
        receipt["telegram_state"] = "PAIRING_REQUIRED_ONE_PRIVATE_START"
        RECEIPT.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print("SIMON_GATE_STATE=PROPOSAL_READY")
        print("TELEGRAM_STATE=PAIRING_REQUIRED_ONE_PRIVATE_START")
        print("OUTGOING_SPEND_USD=0")
        return 0

    if not chat_raw:
        newly_paired = True
        receipt["chat_id_persist"] = _repo_variable("ATM_TELEGRAM_CHAT_ID", str(chat_id))
        bot.send(chat_id, "ATM SG2 paired. Credentialless public lead radar is active. No platform password, cookie, OTP or payment credential is requested by ATM.")
    receipt["chat_id_bound"] = True

    owner_action = bot.latest_owner_action(updates, chat_id)
    if owner_action:
        receipt["latest_owner_action"] = owner_action
    activated = _truthy(os.getenv("ATM_GURU_ACTIVATED")) or bool(owner_action and owner_action.upper().startswith("ACTIVATED GURU"))
    if owner_action and owner_action.upper().startswith("ACTIVATED GURU"):
        receipt["guru_activation_persist"] = _repo_variable("ATM_GURU_ACTIVATED", "true")
        activated = True

    if owner_action and owner_action.upper().startswith(("POSTULÉ", "POSTULE")):
        receipt["owner_application_state"] = "OWNER_APPLIED"
        receipt["state"] = "OWNER_APPLIED_DISCOVERY_CONTINUES"

    last_notified = os.getenv("SIMON_GATE_LAST_NOTIFIED_ID", "").strip()
    should_notify = last_notified != selected.source_id or newly_paired
    if should_notify:
        message_id = bot.send(chat_id, notification_text(selected, activated=activated))
        receipt["telegram_message_id"] = message_id
        receipt["last_notified_persist"] = _repo_variable("SIMON_GATE_LAST_NOTIFIED_ID", selected.source_id)
        receipt["telegram_state"] = "NOTIFIED_SIMON"
        receipt["state"] = "NOTIFIED_SIMON"
    else:
        receipt["telegram_state"] = "DEDUPED_ALREADY_NOTIFIED"
        if receipt["state"] == "PROPOSAL_READY":
            receipt["state"] = "MONITORING_AFTER_NOTIFICATION"

    RECEIPT.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print("SIMON_GATE_STATE=" + str(receipt["state"]))
    print("TELEGRAM_STATE=" + str(receipt.get("telegram_state", "UNKNOWN")))
    print("QUALIFIED_COUNT=" + str(receipt["qualified_count"]))
    print("SELECTED_ID=" + selected.source_id)
    print("OUTGOING_SPEND_USD=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
