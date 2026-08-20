from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .models import PaymentStatus, ValidatedPaymentProof
from .universal_radar import JsonHttpClient, source_hash


class CoinPayVerificationError(ValueError):
    pass


def _parse_signature(header: str) -> tuple[int, str]:
    parts: dict[str, str] = {}
    for item in str(header or "").split(","):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        parts[key.strip()] = value.strip()
    try:
        timestamp = int(parts["t"])
    except Exception as exc:
        raise CoinPayVerificationError("missing/invalid webhook timestamp") from exc
    signature = parts.get("v1", "")
    if not signature or not all(ch in "0123456789abcdefABCDEF" for ch in signature):
        raise CoinPayVerificationError("missing/invalid webhook signature")
    return timestamp, signature.lower()


def verify_coinpay_webhook(raw_body: bytes, signature_header: str, secret: str, *, now_unix: int | None = None, tolerance_seconds: int = 300) -> None:
    if not secret:
        raise CoinPayVerificationError("webhook secret missing")
    timestamp, expected = _parse_signature(signature_header)
    now = int(now_unix if now_unix is not None else time.time())
    if abs(now - timestamp) > max(1, int(tolerance_seconds)):
        raise CoinPayVerificationError("webhook timestamp outside replay window")
    signed = str(timestamp).encode("ascii") + b"." + raw_body
    computed = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed, expected):
        raise CoinPayVerificationError("webhook signature mismatch")


def coinpay_event_to_payment_proof(
    raw_body: bytes,
    *,
    signature_header: str,
    webhook_secret: str,
    expected_recipient: str,
    now_unix: int | None = None,
) -> ValidatedPaymentProof | None:
    verify_coinpay_webhook(raw_body, signature_header, webhook_secret, now_unix=now_unix)
    try:
        event = json.loads(raw_body.decode("utf-8"))
    except Exception as exc:
        raise CoinPayVerificationError("invalid webhook JSON") from exc
    if not isinstance(event, dict):
        raise CoinPayVerificationError("webhook payload must be object")
    event_type = str(event.get("type") or "")
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    # payment.confirmed is acceptance/settlement evidence but not proof the merchant
    # can withdraw. Only forwarded/settled events can increase withdrawable truth.
    if event_type not in {"payment.forwarded", "escrow.settled"}:
        return None
    txid = str(data.get("tx_hash") or data.get("settlement_tx") or "").strip()
    payment_id = str(data.get("payment_id") or data.get("escrow_id") or event.get("id") or "").strip()
    if not txid or not payment_id:
        raise CoinPayVerificationError("settlement event lacks payment id or on-chain transaction")
    recipient = str(data.get("forwarded_to") or data.get("beneficiary") or data.get("to") or expected_recipient).strip()
    if not expected_recipient or recipient.lower() != expected_recipient.lower():
        raise CoinPayVerificationError("settlement recipient mismatch")
    amount = Decimal(str(data.get("amount_usd") or data.get("amount") or "0"))
    if amount <= 0:
        raise CoinPayVerificationError("settlement amount must be positive")
    currency = str(data.get("currency") or data.get("asset") or "USD").upper()
    normalized = amount
    if currency not in {"USD", "USDC"}:
        fx = data.get("amount_usd")
        if fx in (None, ""):
            raise CoinPayVerificationError("non-USD settlement lacks authoritative USD amount")
        normalized = Decimal(str(fx))
    created = event.get("created_at") or data.get("confirmed_at") or data.get("settled_at")
    try:
        timestamp = datetime.fromisoformat(str(created).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        timestamp = datetime.fromtimestamp(int(now_unix if now_unix is not None else time.time()), tz=timezone.utc)
    unique = str(event.get("id") or data.get("delivery_id") or payment_id)
    proof = ValidatedPaymentProof(
        source="coinpay-webhook",
        platform="coinpayportal",
        payout_id_or_txid=txid,
        event_index_or_unique_id=unique,
        amount=normalized if currency not in {"USD", "USDC"} else amount,
        currency="USD" if currency not in {"USD", "USDC"} else currency,
        recipient_public_identifier=expected_recipient,
        status=PaymentStatus.WITHDRAWABLE,
        timestamp=timestamp,
        authoritative_url_or_api="https://coinpayportal.com/docs",
        evidence_hash=source_hash(event),
        normalized_usd=normalized,
    )
    return proof


class CoinPayClient:
    """Receive/verify-only CoinPay client. No x402 purchasing or spending wallet exists."""

    spending_enabled = False
    creates_wallets = False
    x402_purchasing_enabled = False

    def __init__(self, http: JsonHttpClient | None = None, base_url: str = "https://coinpayportal.com"):
        self.http = http or JsonHttpClient()
        self.base_url = base_url.rstrip("/")

    @property
    def enabled(self) -> bool:
        return bool(os.getenv("COINPAY_API_KEY", "").strip())

    def _headers(self) -> dict[str, str]:
        key = os.getenv("COINPAY_API_KEY", "").strip()
        if not key:
            raise PermissionError("COINPAY_API_KEY absent")
        return {"Authorization": f"Bearer {key}"}

    def get_payment(self, payment_id: str) -> dict[str, Any]:
        data = self.http.get_json(f"{self.base_url}/api/payments/{payment_id}", headers=self._headers(), timeout=10)
        if not isinstance(data, dict):
            raise CoinPayVerificationError("payment response malformed")
        return data

    def get_escrow(self, escrow_id: str) -> dict[str, Any]:
        data = self.http.get_json(f"{self.base_url}/api/escrow/{escrow_id}", headers=self._headers(), timeout=10)
        if not isinstance(data, dict):
            raise CoinPayVerificationError("escrow response malformed")
        return data

    def verify_x402_receipt(self, proof: str, *, amount: str, asset: str, network: str, pay_to: str) -> dict[str, Any]:
        # Verification is receive-side only. No settle/purchase call is exposed here.
        data, _, _ = self.http.request_json(
            "POST",
            f"{self.base_url}/api/x402/verify",
            headers=self._headers(),
            body={"proof": proof, "expectedAmount": amount, "expectedAsset": asset, "expectedNetwork": network, "expectedPayTo": pay_to},
            timeout=10,
        )
        if not isinstance(data, dict) or data.get("valid") is not True:
            raise CoinPayVerificationError("x402 proof invalid")
        if str(data.get("to") or "").lower() != pay_to.lower():
            raise CoinPayVerificationError("x402 recipient mismatch")
        return data
