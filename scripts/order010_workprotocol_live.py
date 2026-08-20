#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from atm_core.payments import PaymentNotFinal, PaymentValidationError, WorkProtocolPaymentAdapter

BASE = "https://workprotocol.ai"
JOB_ID = "f82a9ca9-4b7f-4bdf-91c1-b5ae0516b4eb"
EXPECTED_WALLET = "0xd89Ef03bC3105C538529AC2657Bc4488c94ff4E4"
ROOT = Path(__file__).resolve().parents[1]
DELIVERABLE_DIR = ROOT / "deliverables" / "workprotocol_gha_log_parser"
RECEIPT_PATH = ROOT / "order010-workprotocol-receipt.json"
ACTIVE = {"claimed", "accepted", "working", "in_progress", "delivered", "verifying", "verified", "pending_verification"}


class LiveLaneError(RuntimeError):
    pass


def _request(method: str, url: str, *, body: dict[str, Any] | None = None, token: str | None = None) -> dict[str, Any]:
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    headers = {"Accept": "application/json", "User-Agent": "ATM-Order010/1.0"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read().decode("utf-8")
            value = json.loads(raw) if raw else {}
            if not isinstance(value, dict):
                raise LiveLaneError("AUTHORITATIVE_RESPONSE_NOT_OBJECT")
            return value
    except urllib.error.HTTPError as exc:
        # Never include response bodies from authenticated writes: an upstream error
        # must not be able to reflect credentials into CI logs.
        raise LiveLaneError(f"HTTP_{exc.code}:{url.split('?')[0]}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise LiveLaneError(f"NETWORK_OR_JSON:{type(exc).__name__}") from exc


def _job() -> dict[str, Any]:
    return _request("GET", f"{BASE}/api/jobs/{JOB_ID}")


def _job_parts(payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    job = payload.get("job") if isinstance(payload.get("job"), dict) else payload
    claims = [x for x in (payload.get("claims") or []) if isinstance(x, dict)]
    payments = [x for x in (payload.get("payments") or []) if isinstance(x, dict)]
    return job, claims, payments


def _assert_target_open(payload: dict[str, Any]) -> None:
    job, claims, payments = _job_parts(payload)
    if str(job.get("status") or "").lower() != "open":
        raise LiveLaneError("TARGET_NOT_OPEN")
    if str(job.get("paymentAmount") or "") != "75.00" or str(job.get("paymentCurrency") or "").upper() != "USDC":
        raise LiveLaneError("TARGET_ECONOMICS_CHANGED")
    if str(job.get("paymentRail") or "").lower() != "base":
        raise LiveLaneError("TARGET_RAIL_CHANGED")
    if not bool(job.get("escrowFunded")):
        raise LiveLaneError("TARGET_ESCROW_NOT_FUNDED")
    escrow_tx = str(job.get("escrowTxHash") or "")
    if not escrow_tx.startswith("0x") or len(escrow_tx) != 66:
        raise LiveLaneError("TARGET_ESCROW_TX_MISSING")
    locked = any(
        str(p.get("escrowStatus") or "").lower() in {"locked", "funded", "escrowed"}
        and str(p.get("onchainTxHash") or p.get("escrowTxHash") or "") == escrow_tx
        for p in payments
    )
    if not locked:
        raise LiveLaneError("TARGET_ESCROW_LOCK_NOT_AUTHORITATIVE")
    active = [c for c in claims if str(c.get("status") or "").lower() in ACTIVE]
    if active:
        raise LiveLaneError("TARGET_ACTIVE_SLOT_TAKEN")


def _artifact_sha() -> str:
    digest = hashlib.sha256()
    for path in sorted(DELIVERABLE_DIR.iterdir(), key=lambda p: p.name):
        if not path.is_file() or path.is_symlink():
            continue
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _write_receipt(receipt: dict[str, Any]) -> None:
    receipt["outgoing_spend_usd"] = "0"
    receipt["generated_at"] = datetime.now(timezone.utc).isoformat()
    RECEIPT_PATH.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    safe = dict(receipt)
    safe.pop("api_key", None)
    print(json.dumps(safe, sort_keys=True))


def main() -> int:
    head = os.environ.get("ORDER010_HEAD") or os.environ.get("GITHUB_SHA") or "LOCAL"
    expected_head = os.environ.get("ORDER010_EXPECTED_HEAD") or head
    if head != expected_head:
        raise LiveLaneError("EXACT_HEAD_BINDING_FAILED")

    public_before = _job()
    _assert_target_open(public_before)

    repo = os.environ.get("GITHUB_REPOSITORY", "simonkey888/ATM-Agent-Teller-Machine")
    deliverable_url = f"https://github.com/{repo}/tree/{head}/deliverables/workprotocol_gha_log_parser"
    artifact_sha = _artifact_sha()

    agent_id = str(os.environ.get("WORKPROTOCOL_AGENT_ID") or "").strip()
    api_key = str(os.environ.get("WORKPROTOCOL_API_KEY") or "").strip()
    if bool(agent_id) != bool(api_key):
        raise LiveLaneError("WORKPROTOCOL_DURABLE_IDENTITY_HALF_PAIR")
    if not agent_id:
        raise LiveLaneError("WORKPROTOCOL_DURABLE_IDENTITY_REQUIRED")

    # Re-fetch immediately before claim. This lane must never register a per-run
    # economic identity; the same durable pair owns claim, delivery, and payout.
    preclaim = _job()
    _assert_target_open(preclaim)
    claimed = _request("POST", f"{BASE}/api/jobs/{JOB_ID}/claim", body={"agentId": agent_id}, token=api_key)
    claim = claimed.get("claim") if isinstance(claimed.get("claim"), dict) else claimed
    claim_id = str(claim.get("id") or "") if isinstance(claim, dict) else ""
    if not claim_id:
        raise LiveLaneError("WORKPROTOCOL_CLAIM_NO_ID")

    authoritative_claim = None
    for row in _job_parts(_job())[1]:
        if str(row.get("id") or "") == claim_id and str(row.get("agentId") or "") == agent_id:
            authoritative_claim = row
            break
    if not authoritative_claim or str(authoritative_claim.get("status") or "").lower() not in ACTIVE:
        raise LiveLaneError("WORKPROTOCOL_CLAIM_NOT_AUTHORITATIVE")

    delivery = _request(
        "POST",
        f"{BASE}/api/jobs/{JOB_ID}/deliver",
        body={"claimId": claim_id, "deliverable": {"type": "url", "url": deliverable_url}},
        token=api_key,
    )
    delivery_status = str((delivery.get("claim") or delivery).get("status") or "") if isinstance(delivery.get("claim") or delivery, dict) else ""

    # Official deterministic verifier; this is not a requester self-approval.
    verification: dict[str, Any]
    try:
        verification = _request(
            "POST", f"{BASE}/api/jobs/{JOB_ID}/verify-auto", body={"claimId": claim_id}, token=api_key
        )
    except LiveLaneError as exc:
        verification = {"result": "VERIFY_AUTO_REQUEST_FAILED", "error": str(exc)}

    receipt: dict[str, Any] = {
        "schema": "ATM_ORDER010_WORKPROTOCOL_LIVE_V1",
        "head": head,
        "job_id": JOB_ID,
        "agent_id": agent_id,
        "claim_id": claim_id,
        "deliverable_url": deliverable_url,
        "artifact_sha256": artifact_sha,
        "delivery_status": delivery_status or "RECORDED",
        "verify_auto_result": verification.get("result") or verification.get("status") or verification.get("overallVerdict") or "UNKNOWN",
        "claim_performed": True,
        "delivery_performed": True,
        "realized_withdrawable_usd": "0",
        "settlement_tx_hash": None,
    }

    # Poll only public authoritative state. If auto-verification pays immediately,
    # validate the Base USDC receipt before counting a cent as realized/withdrawable.
    adapter = WorkProtocolPaymentAdapter(base_url=BASE)
    final_payload: dict[str, Any] = {}
    for _ in range(60):
        final_payload = _job()
        job, claims, payments = _job_parts(final_payload)
        current = next((c for c in claims if str(c.get("id") or "") == claim_id), {})
        receipt["job_status"] = str(job.get("status") or "UNKNOWN")
        receipt["claim_status"] = str(current.get("status") or "UNKNOWN")
        settlement = next((p for p in payments if p.get("settlementTxHash")), None)
        if settlement:
            receipt["settlement_tx_hash"] = str(settlement.get("settlementTxHash"))
        try:
            proofs = adapter.fetch_payment(JOB_ID, EXPECTED_WALLET)
        except (PaymentNotFinal, PaymentValidationError):
            proofs = []
        if proofs:
            realized = sum((p.normalized_usd for p in proofs), Decimal("0"))
            receipt["realized_withdrawable_usd"] = str(realized)
            receipt["validated_payment_proofs"] = [
                {
                    "evidence_hash": p.evidence_hash,
                    "tx": p.payout_id_or_txid,
                    "amount": str(p.amount),
                    "currency": p.currency,
                    "recipient": p.recipient_public_identifier,
                    "status": p.status.value,
                }
                for p in proofs
            ]
            _write_receipt(receipt)
            return 0
        if str(job.get("status") or "").lower() in {"cancelled", "expired"} or str(current.get("status") or "").lower() == "rejected":
            break
        time.sleep(10)

    # A real submission is still material progress even when payout needs external
    # verification time. Preserve exact public state and exit successfully so the
    # nonblocking controller can continue other rails.
    _write_receipt(receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
