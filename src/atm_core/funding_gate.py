from __future__ import annotations

import re
import os
from decimal import Decimal
from typing import Any

from .opportunities import OpportunityValidationError, WorkProtocolOpportunityAdapter
from .payments import BASE_CHAIN_ID, BASE_PUBLIC_RPC, BASE_USDC, ERC20_TRANSFER_TOPIC, PaymentValidationError

_TX_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_ORIGINAL_VERIFY_FUNDING = WorkProtocolOpportunityAdapter.verify_funding
_INSTALLED = False


def _base_escrow_tx(snapshot: dict[str, Any]) -> str | None:
    job = snapshot.get("job") or snapshot
    tx_hash = str(job.get("escrowTxHash") or "").strip()
    if tx_hash:
        return tx_hash
    for payment in snapshot.get("payments") or []:
        if str(payment.get("rail") or "").lower() != "base":
            continue
        candidate = str(payment.get("onchainTxHash") or payment.get("escrowTxHash") or "").strip()
        if candidate:
            return candidate
    return None


def verify_base_escrow_receipt(adapter: WorkProtocolOpportunityAdapter, snapshot: dict[str, Any]) -> dict[str, Any]:
    job = snapshot.get("job") or snapshot
    rail = str(job.get("paymentRail") or "base").lower()
    if rail != "base":
        return {"rail": rail, "chain_receipt_required": False}

    tx_hash = _base_escrow_tx(snapshot)
    if not tx_hash or not _TX_HASH_RE.fullmatch(tx_hash):
        raise OpportunityValidationError("WorkProtocol Base escrow lacks a valid transaction hash")

    contract = os.getenv("ATM_WORKPROTOCOL_ESCROW_CONTRACT", "").strip().lower()
    if not re.fullmatch(r"0x[0-9a-f]{40}", contract):
        raise OpportunityValidationError("WorkProtocol escrow contract is not configured for authoritative binding")
    try:
        chain = adapter.http.post_json(
            BASE_PUBLIC_RPC,
            {"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []},
        )
        if int(str(chain.get("result") or "0x0"), 16) != BASE_CHAIN_ID:
            raise OpportunityValidationError("WorkProtocol escrow receipt returned wrong chain")
        response = adapter.http.post_json(
            BASE_PUBLIC_RPC,
            {"jsonrpc": "2.0", "id": 1, "method": "eth_getTransactionReceipt", "params": [tx_hash]},
        )
    except PaymentValidationError as exc:
        raise OpportunityValidationError(f"WorkProtocol Base escrow receipt lookup failed: {exc}") from exc

    receipt = response.get("result") if isinstance(response, dict) else None
    if not isinstance(receipt, dict):
        raise OpportunityValidationError("WorkProtocol Base escrow transaction has no authoritative receipt")
    if str(receipt.get("status") or "").lower() != "0x1":
        raise OpportunityValidationError("WorkProtocol Base escrow transaction is not successful")
    if str(receipt.get("to") or "").lower() != contract:
        raise OpportunityValidationError("WorkProtocol escrow transaction targets an untrusted contract")
    reward = Decimal(str(job.get("paymentAmount") or "0"))
    minimum = int((reward * Decimal(1_000_000)).to_integral_value())
    funded = False
    for log in receipt.get("logs") or []:
        topics = log.get("topics") or []
        if str(log.get("address") or "").lower() != BASE_USDC or len(topics) < 3:
            continue
        if str(topics[0]).lower() != ERC20_TRANSFER_TOPIC or str(topics[2]).lower()[-40:] != contract[2:]:
            continue
        try:
            funded = int(str(log.get("data") or "0x0"), 16) >= minimum
        except ValueError:
            funded = False
        if funded:
            break
    if not funded:
        raise OpportunityValidationError("WorkProtocol escrow transaction lacks bound USDC funding")
    return {"rail": "base", "tx_hash": tx_hash.lower(), "status": "0x1", "contract": contract, "amount_base_units_min": minimum}


def _strict_verify_funding(self: WorkProtocolOpportunityAdapter, opportunity: Any, snapshot: dict[str, Any]) -> None:
    _ORIGINAL_VERIFY_FUNDING(self, opportunity, snapshot)


def install_workprotocol_chain_funding_gate() -> None:
    """Fail closed before economic ingress when WorkProtocol Base escrow is not onchain-verifiable."""
    global _INSTALLED
    if _INSTALLED:
        return
    WorkProtocolOpportunityAdapter.verify_funding = _strict_verify_funding
    _INSTALLED = True
