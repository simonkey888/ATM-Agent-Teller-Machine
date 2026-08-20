from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from .models import COUNTABLE_PAYMENT_STATUSES, PaymentStatus, ValidatedPaymentProof


BASE_CHAIN_ID = 8453
BASE_PUBLIC_RPC = "https://mainnet.base.org"
BASE_USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
ERC20_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


class PaymentValidationError(ValueError):
    pass


class PaymentNotFinal(PaymentValidationError):
    pass


class HttpJsonClient:
    def __init__(self, timeout: int = 20):
        self.timeout = timeout

    def get(self, url: str, headers: dict[str, str] | None = None) -> Any:
        req = urllib.request.Request(url, headers=headers or {}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:1000]
            raise PaymentValidationError(f"HTTP {exc.code} from authoritative API: {body}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise PaymentValidationError(f"authoritative API read failed: {exc}") from exc

    def post_json(self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> Any:
        body = json.dumps(payload).encode("utf-8")
        hdrs = {"Content-Type": "application/json", **(headers or {})}
        req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")[:1000]
            raise PaymentValidationError(f"HTTP {exc.code} from authoritative API: {body_text}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise PaymentValidationError(f"authoritative API read failed: {exc}") from exc


class BaseReceiptVerifier:
    def __init__(self, rpc_url: str = BASE_PUBLIC_RPC, http: HttpJsonClient | None = None):
        self.rpc_url = rpc_url
        self.http = http or HttpJsonClient()

    @staticmethod
    def _topic_address(topic: str) -> str:
        if not isinstance(topic, str) or not topic.startswith("0x") or len(topic) != 66:
            raise PaymentValidationError("malformed address topic")
        return "0x" + topic[-40:].lower()

    def verify_usdc_transfer(self, tx_hash: str, recipient: str, amount_usdc: Decimal) -> dict[str, Any]:
        if not tx_hash.startswith("0x") or len(tx_hash) != 66:
            raise PaymentValidationError("invalid settlement tx hash")
        recipient_l = recipient.lower()
        if not recipient_l.startswith("0x") or len(recipient_l) != 42:
            raise PaymentValidationError("invalid expected recipient address")

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_getTransactionReceipt",
            "params": [tx_hash],
        }
        response = self.http.post_json(self.rpc_url, payload)
        receipt = response.get("result") if isinstance(response, dict) else None
        if not receipt:
            raise PaymentNotFinal("settlement transaction has no receipt yet")
        if receipt.get("status") != "0x1":
            raise PaymentValidationError("settlement transaction reverted")

        minimum_base_units = int((amount_usdc * Decimal(1_000_000)).to_integral_value())
        matches: list[dict[str, Any]] = []
        for idx, log in enumerate(receipt.get("logs", [])):
            if str(log.get("address", "")).lower() != BASE_USDC:
                continue
            topics = log.get("topics") or []
            if len(topics) < 3 or str(topics[0]).lower() != ERC20_TRANSFER_TOPIC:
                continue
            to_address = self._topic_address(topics[2])
            try:
                value = int(str(log.get("data", "0x0")), 16)
            except ValueError:
                continue
            if to_address == recipient_l and value >= minimum_base_units:
                matches.append({"log_index": idx, "amount_base_units": value})

        if not matches:
            raise PaymentValidationError("Base receipt lacks matching USDC transfer to expected recipient")
        return {"receipt": receipt, "matching_transfers": matches}


def _canonical_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_validated_proof(
    *,
    source: str,
    platform: str,
    payout_id_or_txid: str,
    event_index_or_unique_id: str,
    amount: Decimal,
    currency: str,
    recipient: str,
    status: PaymentStatus,
    timestamp: datetime,
    authoritative_url: str,
    normalized_usd: Decimal | None = None,
    chain_id: int | None = None,
    token_address: str | None = None,
    fx_source: str | None = None,
    fx_timestamp: datetime | None = None,
    fx_rate: Decimal | None = None,
) -> ValidatedPaymentProof:
    if source.lower() in {"screenshot", "html", "narrative", "llm", "model"}:
        raise PaymentValidationError("non-authoritative evidence source rejected")
    if status not in COUNTABLE_PAYMENT_STATUSES:
        raise PaymentNotFinal(f"payment status {status} is not countable")
    currency_u = currency.upper()
    if normalized_usd is None:
        normalized_usd = amount if currency_u in {"USD", "USDC"} else Decimal("0")
    canonical = {
        "source": source,
        "platform": platform,
        "payout_id_or_txid": payout_id_or_txid,
        "event_index_or_unique_id": event_index_or_unique_id,
        "amount": str(amount),
        "currency": currency_u,
        "recipient": recipient.lower(),
        "status": status.value,
        "timestamp": timestamp.astimezone(timezone.utc).isoformat(),
        "authoritative_url": authoritative_url,
        "normalized_usd": str(normalized_usd),
        "chain_id": chain_id,
        "token_address": token_address.lower() if token_address else None,
        "fx_source": fx_source,
        "fx_timestamp": fx_timestamp.astimezone(timezone.utc).isoformat() if fx_timestamp else None,
        "fx_rate": str(fx_rate) if fx_rate is not None else None,
    }
    return ValidatedPaymentProof(
        source=source,
        platform=platform,
        payout_id_or_txid=payout_id_or_txid,
        event_index_or_unique_id=event_index_or_unique_id,
        amount=amount,
        currency=currency_u,
        recipient_public_identifier=recipient.lower(),
        status=status,
        timestamp=timestamp,
        authoritative_url_or_api=authoritative_url,
        evidence_hash=_canonical_hash(canonical),
        normalized_usd=normalized_usd,
        fx_source=fx_source,
        fx_timestamp=fx_timestamp,
        fx_rate=fx_rate,
        chain_id=chain_id,
        token_address=token_address.lower() if token_address else None,
    )


class PaymentLedger:
    """Append-only validated-payment ledger. The model has no write path here."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> list[ValidatedPaymentProof]:
        if not self.path.exists():
            return []
        proofs: list[ValidatedPaymentProof] = []
        seen: set[str] = set()
        for line_no, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                proof = ValidatedPaymentProof.model_validate_json(line)
            except Exception as exc:
                raise PaymentValidationError(f"ledger corruption at line {line_no}: {exc}") from exc
            if proof.dedupe_key in seen:
                raise PaymentValidationError(f"ledger contains duplicate payment proof at line {line_no}")
            seen.add(proof.dedupe_key)
            proofs.append(proof)
        return proofs

    def append(self, proof: ValidatedPaymentProof) -> bool:
        existing = {p.dedupe_key for p in self.load()}
        if proof.dedupe_key in existing:
            return False
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(proof.model_dump_json() + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return True

    def realized_withdrawable_usd(self) -> Decimal:
        return sum((p.normalized_usd for p in self.load()), Decimal("0"))

    def payout_destinations(self, canonical_wallet: str, *, now: datetime | None = None) -> list[dict[str, str]]:
        return payout_destination_earnings(self.load(), canonical_wallet, now=now)


def payout_destination_earnings(
    proofs: list[ValidatedPaymentProof], canonical_wallet: str, *, now: datetime | None = None
) -> list[dict[str, str]]:
    """Attribute validated receipts once to public payout destinations without bank inference."""
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = observed_at - timedelta(days=30)
    wallet = canonical_wallet.lower()
    seen: set[str] = set()
    terminal: list[ValidatedPaymentProof] = []
    pending: list[ValidatedPaymentProof] = []
    for proof in proofs:
        if proof.dedupe_key in seen:
            continue
        seen.add(proof.dedupe_key)
        if (
            proof.recipient_public_identifier.lower() != wallet
            or proof.chain_id != BASE_CHAIN_ID
            or str(proof.token_address or "").lower() != BASE_USDC
        ):
            continue
        if proof.status in COUNTABLE_PAYMENT_STATUSES:
            terminal.append(proof)
        else:
            pending.append(proof)
    lifetime = sum((p.normalized_usd for p in terminal), Decimal("0"))
    last_30d = sum((p.normalized_usd for p in terminal if p.timestamp >= cutoff), Decimal("0"))
    pending_value = sum((p.normalized_usd for p in pending), Decimal("0")) if pending else None
    short_wallet = wallet[:6] + "…" + wallet[-4:]
    return [
        {
            "display_label": "MetaMask / Base USDC",
            "rail": f"Base USDC · {short_wallet}",
            "last_30d_usd": str(last_30d),
            "lifetime_usd": str(lifetime),
            "pending_or_unsettled_usd": str(pending_value) if pending_value is not None else "UNKNOWN",
            "verification_state": "VERIFIED_DESTINATION",
        },
        {
            "display_label": "Santander",
            "rail": "BANK_PAYOUT",
            "last_30d_usd": "UNKNOWN",
            "lifetime_usd": "UNKNOWN",
            "pending_or_unsettled_usd": "UNKNOWN",
            "verification_state": "NOT_CONNECTED",
        },
    ]


class PaymentAdapter(ABC):
    platform: str

    @abstractmethod
    def fetch_payment(self, external_id: str, expected_recipient: str) -> list[ValidatedPaymentProof]:
        raise NotImplementedError


class WorkProtocolPaymentAdapter(PaymentAdapter):
    platform = "workprotocol"

    def __init__(
        self,
        base_url: str = "https://workprotocol.ai",
        http: HttpJsonClient | None = None,
        chain: BaseReceiptVerifier | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.http = http or HttpJsonClient()
        self.chain = chain or BaseReceiptVerifier(http=self.http)

    def fetch_payment(self, external_id: str, expected_recipient: str) -> list[ValidatedPaymentProof]:
        url = f"{self.base_url}/api/jobs/{urllib.parse.quote(external_id)}"
        data = self.http.get(url)
        job = data.get("job") or {}
        payments = data.get("payments") or []
        if str(job.get("status", "")).lower() not in {"completed", "verified", "paid"}:
            raise PaymentNotFinal("WorkProtocol job is not completed/verified")

        proofs: list[ValidatedPaymentProof] = []
        for idx, payment in enumerate(payments):
            status_raw = str(payment.get("status") or payment.get("escrowStatus") or "").lower()
            if status_raw not in {"released", "withdrawable", "paid", "completed"}:
                continue
            settlement_tx = payment.get("settlementTxHash") or payment.get("settlement_tx_hash") or payment.get("txHash")
            escrow_tx = payment.get("escrowTxHash") or payment.get("escrow_tx_hash")
            if not settlement_tx:
                raise PaymentValidationError("WorkProtocol released payment lacks settlementTxHash")
            if escrow_tx and str(escrow_tx).lower() == str(settlement_tx).lower():
                raise PaymentValidationError("escrowTxHash must not be counted as settlementTxHash")

            recipient = str(
                payment.get("recipient")
                or payment.get("walletAddress")
                or payment.get("workerAddress")
                or payment.get("to")
                or ""
            ).lower()
            if recipient != expected_recipient.lower():
                continue
            amount = Decimal(str(payment.get("workerPayment") or payment.get("amount") or job.get("paymentAmount") or "0"))
            currency = str(payment.get("currency") or job.get("paymentCurrency") or "USDC").upper()
            if currency != "USDC":
                raise PaymentValidationError("WorkProtocol adapter currently counts only USDC Base settlement")
            chain_evidence = self.chain.verify_usdc_transfer(str(settlement_tx), expected_recipient, amount)
            transfer = min(chain_evidence["matching_transfers"], key=lambda row: int(row["log_index"]))
            ts_raw = payment.get("settledAt") or payment.get("releasedAt") or payment.get("updatedAt") or job.get("updatedAt")
            if not ts_raw:
                raise PaymentValidationError("WorkProtocol payment missing settlement timestamp")
            timestamp = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            status = PaymentStatus.PAID if status_raw == "paid" else PaymentStatus.RELEASED
            proofs.append(
                build_validated_proof(
                    source="platform_api+base_receipt",
                    platform=self.platform,
                    payout_id_or_txid=str(settlement_tx),
                    event_index_or_unique_id=f"log:{transfer['log_index']}",
                    amount=amount,
                    currency=currency,
                    recipient=expected_recipient,
                    status=status,
                    timestamp=timestamp,
                    authoritative_url=url,
                    chain_id=BASE_CHAIN_ID,
                    token_address=BASE_USDC,
                )
            )
        if not proofs:
            raise PaymentNotFinal("no released WorkProtocol payment for expected recipient")
        return proofs


class TaskmarketPaymentAdapter(PaymentAdapter):
    platform = "taskmarket"

    def __init__(
        self,
        base_url: str = "https://api.taskmarket.dev",
        http: HttpJsonClient | None = None,
        chain: BaseReceiptVerifier | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.http = http or HttpJsonClient()
        self.chain = chain or BaseReceiptVerifier(http=self.http)

    def fetch_payment(self, external_id: str, expected_recipient: str) -> list[ValidatedPaymentProof]:
        url = f"{self.base_url}/api/tasks/{urllib.parse.quote(external_id)}"
        task = self.http.get(url)
        if not task:
            raise PaymentValidationError("Taskmarket task not found")
        awards = task.get("awards") or []
        proofs: list[ValidatedPaymentProof] = []
        for idx, award in enumerate(awards):
            recipient = str(award.get("workerAddress") or "").lower()
            if recipient != expected_recipient.lower():
                continue
            settlement_tx = str(award.get("settlementTxHash") or "")
            settled_at = award.get("settledAt")
            worker_payment_raw = award.get("workerPayment")
            if not settlement_tx or not settled_at or worker_payment_raw is None:
                raise PaymentValidationError("Taskmarket award lacks canonical settlement fields")
            amount = Decimal(str(worker_payment_raw)) / Decimal(1_000_000)
            chain_evidence = self.chain.verify_usdc_transfer(settlement_tx, expected_recipient, amount)
            transfer = min(chain_evidence["matching_transfers"], key=lambda row: int(row["log_index"]))
            timestamp = datetime.fromisoformat(str(settled_at).replace("Z", "+00:00"))
            proofs.append(
                build_validated_proof(
                    source="taskmarket_award+base_receipt",
                    platform=self.platform,
                    payout_id_or_txid=settlement_tx,
                    event_index_or_unique_id=f"log:{transfer['log_index']}",
                    amount=amount,
                    currency="USDC",
                    recipient=expected_recipient,
                    status=PaymentStatus.RELEASED,
                    timestamp=timestamp,
                    authoritative_url=url,
                    chain_id=BASE_CHAIN_ID,
                    token_address=BASE_USDC,
                )
            )
        if not proofs:
            raise PaymentNotFinal("no settled Taskmarket award for expected recipient")
        return proofs


class AlgoraPaymentAdapter(PaymentAdapter):
    """Fail-closed: Algora board/SDK discovery is not accepted as payout truth.

    A live payout endpoint must be supplied explicitly and must return a terminal payout
    object. This avoids treating a bounty board or merged issue as payment evidence.
    """

    platform = "algora"

    def __init__(self, payout_api_template: str | None = None, http: HttpJsonClient | None = None):
        self.payout_api_template = payout_api_template
        self.http = http or HttpJsonClient()

    def fetch_payment(self, external_id: str, expected_recipient: str) -> list[ValidatedPaymentProof]:
        if not self.payout_api_template:
            raise PaymentNotFinal("Algora payout adapter is WATCH_ONLY until an authoritative payout API is configured")
        url = self.payout_api_template.format(id=urllib.parse.quote(external_id))
        data = self.http.get(url)
        status_raw = str(data.get("status") or "").lower()
        if status_raw not in {"released", "withdrawable", "paid"}:
            raise PaymentNotFinal("Algora payout is not terminal")
        recipient = str(data.get("recipient") or data.get("payee") or "").lower()
        if not recipient or recipient != expected_recipient.lower():
            raise PaymentValidationError("Algora payout recipient mismatch")
        payout_id = str(data.get("payout_id") or data.get("transaction_id") or "")
        if not payout_id:
            raise PaymentValidationError("Algora payout lacks authoritative payout id")
        amount = Decimal(str(data.get("amount") or "0"))
        currency = str(data.get("currency") or "USD").upper()
        ts_raw = data.get("paid_at") or data.get("released_at")
        if not ts_raw:
            raise PaymentValidationError("Algora payout lacks timestamp")
        timestamp = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        status = {
            "released": PaymentStatus.RELEASED,
            "withdrawable": PaymentStatus.WITHDRAWABLE,
            "paid": PaymentStatus.PAID,
        }[status_raw]
        return [
            build_validated_proof(
                source="algora_authoritative_payout_api",
                platform=self.platform,
                payout_id_or_txid=payout_id,
                event_index_or_unique_id=str(data.get("event_id") or payout_id),
                amount=amount,
                currency=currency,
                recipient=recipient,
                status=status,
                timestamp=timestamp,
                authoritative_url=url,
            )
        ]
