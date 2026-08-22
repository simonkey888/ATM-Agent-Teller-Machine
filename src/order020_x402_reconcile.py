from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import atm_cloud
import atm_v2
from atm_core.models import PaymentStatus
from atm_core.payments import (
    BASE_CHAIN_ID,
    BASE_PUBLIC_RPC,
    BASE_USDC,
    ERC20_TRANSFER_TOPIC,
    HttpJsonClient,
    PaymentLedger,
    build_validated_proof,
)

REPO = "simonkey888/ATM-Agent-Teller-Machine"
ORDER_ISSUE = 48
EVENT_MARKER = "ATM ORDER020 X402 PAYMENT EVENT"
EVENT_SCHEMA = "ATM_ORDER020_X402_PAYMENT_EVENT_V1"
EVENT_AUTH_CONTEXT = "ATM-ORDER020-EVENT-V1:"
EVENT_AUTH_FIELD = "event_auth_hmac_sha256"
RESOURCE_URL = "https://atm.simondalmasso44.workers.dev/x402/falsify"
CANONICAL_WALLET = "0xd89Ef03bC3105C538529AC2657Bc4488c94ff4E4"
AMOUNT_ATOMIC = 10_000
AMOUNT_USDC = Decimal("0.01")
TX_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
TRUSTED_OWNER = "simonkey888"


def _topic_address(topic: Any) -> str | None:
    raw = str(topic or "")
    if not re.fullmatch(r"0x[0-9a-fA-F]{64}", raw):
        return None
    return "0x" + raw[-40:].lower()


def _trusted_comment(row: dict[str, Any]) -> bool:
    login = str((row.get("user") or {}).get("login") or "")
    association = str(row.get("author_association") or "")
    return login == "github-actions[bot]" or (
        login == TRUSTED_OWNER and association in {"OWNER", "MEMBER", "COLLABORATOR"}
    )


def _parse_event(row: dict[str, Any]) -> dict[str, Any] | None:
    if not _trusted_comment(row):
        return None
    text = str(row.get("body") or "")
    if not text.startswith(EVENT_MARKER):
        return None
    match = re.search(r"```json\s*([\s\S]*?)\s*```", text)
    if not match:
        return None
    try:
        event = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict) or event.get("schema") != EVENT_SCHEMA:
        return None
    event["_comment_id"] = int(row.get("id") or 0)
    event["_comment_url"] = str(row.get("html_url") or "")
    event["_comment_created_at"] = str(row.get("created_at") or "")
    return event


def _event_auth_payload(event: dict[str, Any]) -> str:
    return "\n".join(
        [
            str(event.get("schema") or ""),
            str(event.get("payment_event_id") or ""),
            str(event.get("resource") or ""),
            str(event.get("settlement_tx") or "").lower(),
            str(event.get("payer") or "").lower(),
            str(event.get("recipient") or "").lower(),
            str(event.get("network") or ""),
            str(event.get("chain_id") if event.get("chain_id") is not None else ""),
            str(event.get("token") or "").lower(),
            str(event.get("amount_atomic") or ""),
            str(event.get("input_sha256") or "").lower(),
            str(event.get("source_sha") or "").lower(),
            str(event.get("observed_at") or ""),
            "1" if event.get("onchain_proven") is True else "0",
            "1" if event.get("external_buyer") is True else "0",
            str(event.get("outgoing_spend_usd") or ""),
        ]
    )


def _event_auth_tag(secret: str, event: dict[str, Any]) -> str:
    derived = hashlib.sha256((EVENT_AUTH_CONTEXT + secret).encode()).digest()
    return hmac.new(derived, _event_auth_payload(event).encode(), hashlib.sha256).hexdigest()


def _github_get(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "ATM-ORDER020-X402-Reconcile/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def _events_since(ledger: PaymentLedger) -> list[dict[str, Any]]:
    proofs = [proof for proof in ledger.load() if proof.platform == "x402-seller"]
    params = {"per_page": "100", "sort": "created", "direction": "asc"}
    if proofs:
        latest = max(proof.timestamp for proof in proofs).astimezone(timezone.utc) - timedelta(minutes=10)
        params["since"] = latest.isoformat().replace("+00:00", "Z")
    events: list[dict[str, Any]] = []
    page = 1
    while page <= 20:
        params["page"] = str(page)
        url = (
            f"https://api.github.com/repos/{REPO}/issues/{ORDER_ISSUE}/comments?"
            + urllib.parse.urlencode(params)
        )
        rows = _github_get(url)
        if not isinstance(rows, list):
            break
        for row in rows:
            if isinstance(row, dict):
                event = _parse_event(row)
                if event:
                    events.append(event)
        if len(rows) < 100:
            break
        page += 1
    return events


def _validate_static_event(event: dict[str, Any], *, event_secret: str | None = None) -> tuple[str, str]:
    tx_hash = str(event.get("settlement_tx") or "")
    payer = str(event.get("payer") or "").lower()
    if not TX_RE.fullmatch(tx_hash):
        raise ValueError("settlement_tx_invalid")
    if not ADDRESS_RE.fullmatch(payer):
        raise ValueError("payer_invalid")
    if payer == CANONICAL_WALLET.lower():
        raise ValueError("owner_self_payment_rejected")
    if str(event.get("resource") or "") != RESOURCE_URL:
        raise ValueError("resource_mismatch")
    if str(event.get("network") or "") != "eip155:8453" or int(event.get("chain_id") or 0) != BASE_CHAIN_ID:
        raise ValueError("chain_mismatch")
    if str(event.get("token") or "").lower() != BASE_USDC:
        raise ValueError("token_mismatch")
    if str(event.get("recipient") or "").lower() != CANONICAL_WALLET.lower():
        raise ValueError("recipient_mismatch")
    if str(event.get("amount_atomic") or "") != str(AMOUNT_ATOMIC):
        raise ValueError("amount_mismatch")
    if event.get("onchain_proven") is not True or event.get("external_buyer") is not True:
        raise ValueError("seller_event_state_invalid")
    if str(event.get("outgoing_spend_usd") or "") != "0":
        raise ValueError("seller_event_spend_invalid")
    source_sha = str(event.get("source_sha") or "").lower()
    if not SHA_RE.fullmatch(source_sha):
        raise ValueError("source_sha_invalid")
    digest = str(event.get("input_sha256") or "").lower()
    if not DIGEST_RE.fullmatch(digest):
        raise ValueError("input_digest_invalid")
    event_id = str(event.get("payment_event_id") or "").lower()
    if not DIGEST_RE.fullmatch(event_id):
        raise ValueError("payment_event_id_invalid")
    secret = event_secret if event_secret is not None else str(os.getenv("ATM_ORDER020_EVENT_SECRET") or "")
    if not secret:
        raise ValueError("event_auth_secret_missing")
    actual = str(event.get(EVENT_AUTH_FIELD) or "").lower()
    if not DIGEST_RE.fullmatch(actual):
        raise ValueError("event_auth_missing")
    expected = _event_auth_tag(secret, event)
    if not hmac.compare_digest(actual, expected):
        raise ValueError("event_auth_invalid")
    return tx_hash, payer


def _matching_transfer(tx_hash: str, payer: str, http: HttpJsonClient) -> dict[str, Any]:
    response = http.post_json(
        BASE_PUBLIC_RPC,
        {"jsonrpc": "2.0", "id": 1, "method": "eth_getTransactionReceipt", "params": [tx_hash]},
    )
    receipt = response.get("result") if isinstance(response, dict) else None
    if not isinstance(receipt, dict):
        raise ValueError("receipt_not_final")
    if str(receipt.get("status") or "").lower() != "0x1":
        raise ValueError("receipt_reverted")
    for idx, log in enumerate(receipt.get("logs") or []):
        if str(log.get("address") or "").lower() != BASE_USDC:
            continue
        topics = log.get("topics") or []
        if len(topics) < 3 or str(topics[0]).lower() != ERC20_TRANSFER_TOPIC:
            continue
        from_address = _topic_address(topics[1])
        to_address = _topic_address(topics[2])
        if from_address != payer or to_address != CANONICAL_WALLET.lower():
            continue
        try:
            amount_atomic = int(str(log.get("data") or "0x0"), 16)
        except ValueError:
            continue
        if amount_atomic < AMOUNT_ATOMIC:
            continue
        return {
            "log_index": idx,
            "from_address": from_address,
            "to_address": to_address,
            "amount_atomic": amount_atomic,
            "block_number": str(receipt.get("blockNumber") or ""),
        }
    raise ValueError("matching_external_base_usdc_transfer_absent")


def reconcile_local_ledger(
    ledger: PaymentLedger,
    *,
    http: HttpJsonClient | None = None,
    event_secret: str | None = None,
) -> dict[str, Any]:
    http = http or HttpJsonClient(timeout=20)
    secret = event_secret if event_secret is not None else str(os.getenv("ATM_ORDER020_EVENT_SECRET") or "")
    appended = 0
    duplicates = 0
    rejected: dict[str, int] = {}
    for event in _events_since(ledger):
        try:
            tx_hash, payer = _validate_static_event(event, event_secret=secret)
            transfer = _matching_transfer(tx_hash, payer, http)
            created_at = datetime.fromisoformat(
                str(event.get("_comment_created_at") or "").replace("Z", "+00:00")
            ).astimezone(timezone.utc)
            proof = build_validated_proof(
                source="github_issue_x402_event_hmac+base_receipt",
                platform="x402-seller",
                payout_id_or_txid=tx_hash,
                event_index_or_unique_id=f"log:{transfer['log_index']}",
                amount=AMOUNT_USDC,
                currency="USDC",
                recipient=CANONICAL_WALLET,
                status=PaymentStatus.WITHDRAWABLE,
                timestamp=created_at,
                authoritative_url=str(event.get("_comment_url") or f"https://github.com/{REPO}/issues/{ORDER_ISSUE}"),
                chain_id=BASE_CHAIN_ID,
                token_address=BASE_USDC,
            )
            if ledger.append(proof):
                appended += 1
            else:
                duplicates += 1
        except Exception as exc:
            key = str(exc)[:120] or type(exc).__name__
            rejected[key] = rejected.get(key, 0) + 1
    x402 = [proof for proof in ledger.load() if proof.platform == "x402-seller"]
    settlements = len({proof.payout_id_or_txid.lower() for proof in x402})
    return {
        "schema": "ATM_ORDER020_X402_LEDGER_RECONCILE_V1",
        "appended": appended,
        "duplicates": duplicates,
        "rejected": rejected,
        "x402_payment_count": len(x402),
        "x402_settlement_count": settlements,
        "withdrawable_usdc": str(sum((proof.normalized_usd for proof in x402), Decimal("0"))),
        "outgoing_spend_usd": "0",
    }


def main() -> int:
    if os.getenv("GITHUB_ACTIONS") != "true":
        raise SystemExit("ORDER020_RECONCILE_REQUIRES_GITHUB_ACTIONS")
    if not os.getenv("ATM_ORDER020_EVENT_SECRET"):
        raise SystemExit("ORDER020_EVENT_AUTH_SECRET_MISSING")
    source_sha = atm_cloud.safe_source_sha()
    config_file = os.getenv("OCI_CLI_CONFIG_FILE") or str(atm_cloud.Path.home() / ".oci" / "config")
    profile = os.getenv("OCI_CLI_PROFILE") or "ATM_REMOTE"
    backend = atm_cloud.OCIBackend(config_file, profile)
    session = atm_cloud.CloudStateSession(backend, atm_cloud.STATE_DIR, source_sha)
    session.restore()
    authority = atm_cloud.AuthorityManager(
        backend,
        source_sha,
        atm_cloud._instance_state_factory(config_file, profile),
    )
    lease = authority.acquire_github(120)
    if lease is None:
        print(json.dumps({"schema": "ATM_ORDER020_X402_LEDGER_RECONCILE_V1", "state": "NOOP_OTHER_AUTHORITY", "outgoing_spend_usd": "0"}, sort_keys=True))
        return 0
    try:
        ledger = PaymentLedger(atm_v2.PAYMENT_LEDGER_FILE)
        result = reconcile_local_ledger(ledger)
        if result["appended"]:
            session.checkpoint()
        print(json.dumps(result, sort_keys=True))
        return 0
    finally:
        authority.release(lease)


if __name__ == "__main__":
    raise SystemExit(main())
