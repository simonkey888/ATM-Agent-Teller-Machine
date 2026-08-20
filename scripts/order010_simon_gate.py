from __future__ import annotations

import json
import hashlib
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from atm_core.effect_boundary import UniversalEffectBoundary, effect_key
from atm_core.simon_gate import (
    GuruPublicSource,
    SimonGateError,
    TelegramBot,
    notification_due,
    notification_fingerprint,
    notification_text,
)


RECEIPT = Path("order010-simon-gate-receipt.json")
MARKER = "ATM ORDER010 SG2 SIMON GATE STATE"


def _repo_variable(name: str, value: str) -> str:
    """Best-effort repository-variable persistence for non-secret SimonGate state."""
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


def _github_json(url: str) -> Any:
    token = os.getenv("GITHUB_TOKEN", "").strip()
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "ATM-SimonGate/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _durable_state() -> dict[str, Any]:
    """Load the latest sanitized SG2 receipt from Issue #38.

    The numeric Telegram chat id is explicitly non-secret under SG2. This gives
    us a durable fallback when the ephemeral GITHUB_TOKEN cannot administer
    repository Actions variables.
    """
    repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    if not repo:
        return {}
    try:
        issue = _github_json(f"https://api.github.com/repos/{repo}/issues/38")
        count = int(issue.get("comments") or 0) if isinstance(issue, dict) else 0
        page = max(1, (count + 99) // 100)
        comments = _github_json(f"https://api.github.com/repos/{repo}/issues/38/comments?per_page=100&page={page}")
    except Exception:
        return {}
    if not isinstance(comments, list):
        return {}
    for row in reversed(comments):
        body = str(row.get("body") or "") if isinstance(row, dict) else ""
        if not body.startswith(MARKER):
            continue
        match = re.search(r"```json\s*([\s\S]*?)\s*```", body)
        if not match:
            continue
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("schema") == "ATM_ORDER010_SG2_SIMON_GATE_V1":
            return payload
    return {}


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    head = os.getenv("ORDER010_HEAD", os.getenv("GITHUB_SHA", "UNKNOWN")).strip() or "UNKNOWN"
    source = GuruPublicSource()
    prior = _durable_state()
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

    receipt["discovery_lead_count"] = len(leads)
    if not leads:
        receipt["qualified_count"] = 0
        receipt["state"] = "NO_CURRENT_QUALIFIED_LEAD"
        RECEIPT.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print("SIMON_GATE_STATE=NO_CURRENT_QUALIFIED_LEAD")
        print("OUTGOING_SPEND_USD=0")
        return 0

    # AUD #5356057214: the list page is only a hint. Every candidate must be
    # rebuilt from its current individual page before it can reach Telegram.
    # Guru's observed ID-verification path currently costs USD 4.95, so the
    # onboarding predicate intentionally fails closed under ZERO_SPEND.
    qualified = []
    rejection_counts: dict[str, int] = {}
    for lead in leads[:10]:
        try:
            refreshed, gate = source.refetch_individual(
                lead,
                platform_onboarding_compatible=False,
            )
        except Exception as exc:
            reason = "INDIVIDUAL_REFETCH_" + type(exc).__name__.upper()
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
            continue
        for reason in gate.rejection_reasons:
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
        if refreshed is not None and gate.executable:
            qualified.append(refreshed)
    receipt.update({
        "qualified_count": len(qualified),
        "individual_object_authority": True,
        "rejection_counts": dict(sorted(rejection_counts.items())),
        "guru_public_discovery": "ALLOWED",
        "guru_mutation": "BLOCKED_PAID_VERIFICATION",
        "guru_owner_onboarding": "DO_NOT_REQUEST",
        "guru_idv_cost_usd": "4.95",
        "telegram_state": "WITHHELD_NO_ZERO_COST_QUALIFIED_JOB",
    })
    if not qualified:
        receipt["state"] = "READ_ONLY_SEARCHING"
        RECEIPT.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print("SIMON_GATE_STATE=READ_ONLY_SEARCHING")
        print("TELEGRAM_STATE=WITHHELD_NO_ZERO_COST_QUALIFIED_JOB")
        print("QUALIFIED_COUNT=0")
        print("OUTGOING_SPEND_USD=0")
        return 0

    selected = qualified[0]
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
    prior_chat = prior.get("chat_id")
    chat_raw = os.getenv("ATM_TELEGRAM_CHAT_ID", "").strip() or (str(prior_chat) if prior_chat else "")
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

    receipt["chat_id"] = chat_id
    receipt["chat_id_bound"] = True
    if not chat_raw:
        newly_paired = True
        variable_result = _repo_variable("ATM_TELEGRAM_CHAT_ID", str(chat_id))
        receipt["chat_id_repository_variable"] = variable_result
        receipt["chat_id_persist"] = "REPOSITORY_VARIABLE" if variable_result in {"CREATED", "UPDATED"} else "SANITIZED_ISSUE_STATE"
    else:
        receipt["chat_id_persist"] = "DURABLE_STATE_REUSED"

    owner_action = bot.latest_owner_action(updates, chat_id)
    if owner_action:
        receipt["latest_owner_action"] = owner_action

    prior_activated = "GURU" in [str(x).upper() for x in (prior.get("activated_platforms") or [])]
    activated = _truthy(os.getenv("ATM_GURU_ACTIVATED")) or prior_activated
    receipt["activated_platforms"] = ["GURU"] if activated else []

    if owner_action and owner_action.upper().startswith(("POSTULÉ", "POSTULE")):
        receipt["owner_application_state"] = "OWNER_APPLIED"
        receipt["state"] = "OWNER_APPLIED_DISCOVERY_CONTINUES"

    required_action = "OPEN_EXACT_JOB_AND_SUBMIT_PREPARED_PROPOSAL"
    prior_fingerprint = str(prior.get("notification_fingerprint") or "") or None
    try:
        prior_notified_at = datetime.fromisoformat(str(prior.get("notified_at") or "").replace("Z", "+00:00"))
    except ValueError:
        prior_notified_at = None
    should_notify = notification_due(
        selected,
        required_action,
        prior_fingerprint=prior_fingerprint,
        prior_notified_at=prior_notified_at,
        now=datetime.now(timezone.utc),
        reminder_ttl=timedelta(hours=24),
    )
    if should_notify:
        fingerprint = notification_fingerprint(selected, required_action)
        selected_hash = hashlib.sha256(
            json.dumps(selected.public_dict(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        key = effect_key(
            canonical_identity=f"telegram-chat:{chat_id}",
            canonical_opportunity_id=f"guru:{selected.source_id}",
            external_action="NOTIFY_HUMAN_GATE",
            canonical_args={"notification_fingerprint": fingerprint},
            current_external_object_hash=selected_hash,
        )
        receipt["effect_key"] = key
        prior_lock = os.getenv("ATM_TELEGRAM_EFFECT_LOCK", "").strip()
        if prior_lock == key:
            receipt["telegram_state"] = "DEDUPED_DURABLE_EFFECT_LOCK"
            receipt["state"] = "MONITORING_AFTER_NOTIFICATION"
            should_notify = False
        else:
            lock_result = _repo_variable("ATM_TELEGRAM_EFFECT_LOCK", key)
            receipt["effect_lock_persist"] = lock_result
            if lock_result not in {"CREATED", "UPDATED"}:
                receipt["telegram_state"] = "BLOCKED_DURABLE_EFFECT_LOCK"
                receipt["state"] = "PROPOSAL_READY_FAIL_CLOSED"
                should_notify = False
        if should_notify:
            effects = UniversalEffectBoundary(Path(".atm") / "universal-effects.sqlite3")
            effect = effects.prepare(
                canonical_identity=f"telegram-chat:{chat_id}",
                canonical_opportunity_id=f"guru:{selected.source_id}",
                external_action="NOTIFY_HUMAN_GATE",
                canonical_args={"notification_fingerprint": fingerprint},
                current_external_object_hash=selected_hash,
            )
            effects.precondition_refetched(effect.effect_key)
            effects.committing(effect.effect_key)
            message_id = bot.send(chat_id, notification_text(selected, activated=activated))
            effects.authoritative_verify(effect.effect_key)
            effects.committed(effect.effect_key, str(message_id))
            receipt["telegram_message_id"] = message_id
            receipt["notification_fingerprint"] = fingerprint
            receipt["notified_at"] = datetime.now(timezone.utc).isoformat()
            receipt["telegram_state"] = "NOTIFIED_SIMON"
            receipt["state"] = "NOTIFIED_SIMON"
    else:
        receipt["telegram_state"] = "DEDUPED_ALREADY_NOTIFIED"
        if receipt["state"] == "PROPOSAL_READY":
            receipt["state"] = "MONITORING_AFTER_NOTIFICATION"

    RECEIPT.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print("SIMON_GATE_STATE=" + str(receipt["state"]))
    print("TELEGRAM_STATE=" + str(receipt.get("telegram_state", "UNKNOWN")))
    print("CHAT_ID_BOUND=true")
    print("CHAT_ID_PERSIST=" + str(receipt.get("chat_id_persist", "UNKNOWN")))
    print("QUALIFIED_COUNT=" + str(receipt["qualified_count"]))
    print("SELECTED_ID=" + selected.source_id)
    print("OUTGOING_SPEND_USD=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
