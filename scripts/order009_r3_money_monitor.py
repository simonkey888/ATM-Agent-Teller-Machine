#!/usr/bin/env python3
"""ORDER-009-R3 read-only money monitor.

No credentials, signatures, writes, claims, submissions, or withdrawals. Positive
SETTLED/WITHDRAWABLE values require TaskMarket award evidence plus a successful
Base USDC Transfer log to the canonical ATM wallet.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from decimal import Decimal
from pathlib import Path

TASK_ID = "0x4c887264d5ede369de6e98c6214e6c03ee8708af108305ecafa0341a675e6147"
SUBMISSION_ID = "aa7240f5-a8c8-4e61-81eb-f85fad405de2"
WALLET = "0xd89Ef03bC3105C538529AC2657Bc4488c94ff4E4"
ARTIFACT_SHA256 = "50443350d31df9a44a5e4880d3885b897493a5b8f900b8a93875447bcde5da0f"
SUBMITTED_NET_MICRO = 9_250_000
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
BASE_RPC = "https://mainnet.base.org"
API = "https://api.taskmarket.dev/api"
OUT = Path(os.environ.get("ORDER009_R3_MONEY_OUT", "order009-r3-money-state.json"))


def get_json(url: str):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "ATM-ORDER009-R3-money-monitor/1"})
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.loads(response.read().decode())


def rpc(method: str, params: list):
    req = urllib.request.Request(
        BASE_RPC,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "ATM-ORDER009-R3-money-monitor/1"},
    )
    with urllib.request.urlopen(req, timeout=25) as response:
        body = json.loads(response.read().decode())
    if body.get("error"):
        raise RuntimeError(f"BASE_RPC_ERROR:{body['error']}")
    return body.get("result")


def micro_to_usdc(value: int) -> str:
    out = Decimal(value) / Decimal(1_000_000)
    return format(out.quantize(Decimal("0.000001")), "f").rstrip("0").rstrip(".") or "0"


def normalize_rows(raw):
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("submissions", "items", "data"):
            if isinstance(raw.get(key), list):
                return raw[key]
    return []


def find_submission(rows):
    for row in rows:
        if isinstance(row, dict) and str(row.get("id") or row.get("submissionId") or "") == SUBMISSION_ID:
            return row
    return None


def matching_award(task: dict):
    wallet = WALLET.lower()
    for award in task.get("awards") or []:
        if isinstance(award, dict) and str(award.get("workerAddress") or "").lower() == wallet:
            return award
    primary = task.get("primaryAward")
    if isinstance(primary, dict) and str(primary.get("workerAddress") or "").lower() == wallet:
        return primary
    return None


def verify_settlement(award: dict, expected_micro: int):
    tx_hash = str(award.get("settlementTxHash") or "")
    if not tx_hash.startswith("0x") or len(tx_hash) != 66 or not award.get("settledAt"):
        return False, 0, tx_hash, "NO_AUTHORITATIVE_SETTLEMENT_TX"
    try:
        receipt = rpc("eth_getTransactionReceipt", [tx_hash])
    except Exception as exc:
        return False, 0, tx_hash, f"RPC_UNAVAILABLE:{type(exc).__name__}"
    if not isinstance(receipt, dict) or receipt.get("status") != "0x1":
        return False, 0, tx_hash, "SETTLEMENT_TX_NOT_SUCCESSFUL"
    wallet_topic = "0x" + WALLET.lower().replace("0x", "").rjust(64, "0")
    received = 0
    for log in receipt.get("logs") or []:
        if not isinstance(log, dict) or str(log.get("address") or "").lower() != USDC_BASE.lower():
            continue
        topics = [str(x).lower() for x in (log.get("topics") or [])]
        if len(topics) < 3 or topics[0] != TRANSFER_TOPIC or topics[2] != wallet_topic:
            continue
        try:
            received += int(str(log.get("data") or "0x0"), 16)
        except ValueError:
            continue
    if received < expected_micro:
        return False, received, tx_hash, "USDC_TRANSFER_TO_CANONICAL_WALLET_NOT_PROVEN"
    return True, received, tx_hash, "BASE_USDC_TRANSFER_CONFIRMED"


def main() -> int:
    task_url = f"{API}/tasks/{TASK_ID}"
    submissions_url = f"{task_url}/submissions"
    manifest_url = f"{submissions_url}/{SUBMISSION_ID}/manifest"
    task = get_json(task_url)
    rows = normalize_rows(get_json(submissions_url))
    manifest = get_json(manifest_url)

    submission = find_submission(rows)
    submission_verified = bool(
        submission
        and str(submission.get("workerAddress") or "").lower() == WALLET.lower()
        and not submission.get("rejectedAt")
    )
    artifacts = manifest.get("artifacts") or [] if isinstance(manifest, dict) else []
    artifact_verified = any(
        isinstance(a, dict)
        and str(a.get("sha256Hash") or "").lower() == ARTIFACT_SHA256
        and str(a.get("fileName") or "") == "ROBOTICS_SUPPLY_CHAIN_THESIS.md"
        for a in artifacts
    )
    if not submission_verified or not artifact_verified:
        raise SystemExit("ROBOTICS_SUBMISSION_PROVENANCE_MISMATCH")

    award = matching_award(task)
    award_count = int(task.get("awardCount") or 0)
    if award:
        worker_payment_micro = int(award.get("workerPayment") or SUBMITTED_NET_MICRO)
        accepted_net = micro_to_usdc(worker_payment_micro)
        accepted_state = "ACCEPTED"
    else:
        worker_payment_micro = 0
        accepted_net = "0" if award_count >= 0 else "UNKNOWN"
        accepted_state = "NOT_AWARDED"

    settlement_verified = False
    settlement_received_micro = 0
    settlement_tx = ""
    settlement_reason = "NO_MATCHING_AWARD"
    if award:
        settlement_verified, settlement_received_micro, settlement_tx, settlement_reason = verify_settlement(award, worker_payment_micro)

    settled = micro_to_usdc(worker_payment_micro) if settlement_verified else "0"
    withdrawable = micro_to_usdc(worker_payment_micro) if settlement_verified else "0"
    state = "WITHDRAWABLE" if settlement_verified else (accepted_state if award else "SUBMITTED")

    result = {
        "schema": "ATM_ORDER009_R3_MONEY_STATE_V1",
        "task_id": TASK_ID,
        "submission_id": SUBMISSION_ID,
        "canonical_wallet": WALLET,
        "artifact_sha256": ARTIFACT_SHA256,
        "state": state,
        "opportunity_value_usdc": "9.25",
        "submitted_net_usdc": "9.25",
        "accepted_net_usdc": accepted_net,
        "settled_usdc": settled,
        "withdrawable_usdc": withdrawable,
        "submission_verified": submission_verified,
        "artifact_verified": artifact_verified,
        "task_status": str(task.get("status") or "UNKNOWN"),
        "award_count": award_count,
        "matching_award": bool(award),
        "settlement_verified": settlement_verified,
        "settlement_tx_hash": settlement_tx or None,
        "settlement_received_usdc": micro_to_usdc(settlement_received_micro),
        "settlement_evidence": settlement_reason,
        "evidence": {
            "task": task_url,
            "submissions": submissions_url,
            "manifest": manifest_url,
            "base_rpc": BASE_RPC if settlement_tx else None,
            "usdc_contract": USDC_BASE,
        },
        "non_usdc": {"dreams_estimated": "UNKNOWN"},
        "outgoing_spend_usd": "0",
    }
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
