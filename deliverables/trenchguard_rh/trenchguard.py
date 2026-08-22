"""TrenchGuard RH: deterministic Robinhood Chain token provenance and risk evidence.

Read-only intelligence only. This module never signs, trades, approves, transfers,
or submits blockchain transactions.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

CHAIN_ID = 4663
CHAIN_NAME = "Robinhood Chain"
BLOCKSCOUT = "https://robinhoodchain.blockscout.com"
BLOCKSCOUT_API = BLOCKSCOUT + "/api/v2"
ROBINHOOD_ASSETS = "https://api.robinhood.com/rhj/assets"
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
DEFAULT_TIMEOUT = 15
EARLY_HOURS = 72.0

JsonFetcher = Callable[[str], Any]


class EvidenceError(RuntimeError):
    """Raised when material evidence cannot be fetched or parsed safely."""


@dataclass(frozen=True)
class Source:
    """One source used by the evidence packet."""

    name: str
    url: str
    authority: str


SOURCES = (
    Source("Robinhood Chain docs", "https://docs.robinhood.com/chain/", "first_party"),
    Source("Robinhood stock-token API", "https://docs.robinhood.com/chain/stock-token-apis/", "first_party"),
    Source("Robinhood Chain Blockscout", BLOCKSCOUT, "chain_explorer"),
)


def _json_get(url: str) -> Any:
    """Fetch JSON using a bounded read-only HTTP GET."""
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json", "User-Agent": "TrenchGuard-RH/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as response:
            if int(response.status) != 200:
                raise EvidenceError(f"HTTP_{response.status}:{url}")
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise EvidenceError(f"HTTP_{exc.code}:{url}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"FETCH_FAILED:{url}:{type(exc).__name__}") from exc


def normalize_address(value: str) -> str:
    """Validate and normalize an EVM address without checksum assumptions."""
    value = value.strip()
    if not ADDRESS_RE.fullmatch(value):
        raise ValueError("token address must be a 0x-prefixed 40-hex EVM address")
    return value.lower()


def _items(payload: Any) -> list[dict[str, Any]]:
    """Return dictionary items from common paginated API shapes."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("items", "results", "data", "assets"):
            val = payload.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
    return []


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _token_meta(payload: Any) -> dict[str, Any]:
    p = payload if isinstance(payload, dict) else {}
    return {
        "name": p.get("name"),
        "symbol": p.get("symbol"),
        "decimals": p.get("decimals"),
        "total_supply": p.get("total_supply") or p.get("totalSupply"),
        "holders": p.get("holders") or p.get("holders_count"),
        "exchange_rate": p.get("exchange_rate"),
        "type": p.get("type"),
    }


def _contract_status(payload: Any) -> dict[str, Any]:
    p = payload if isinstance(payload, dict) else {}
    verified = p.get("is_verified")
    if verified is None:
        verified = bool(p.get("source_code") or p.get("file_path") or p.get("compiler_version"))
    return {
        "verified": bool(verified),
        "name": p.get("name"),
        "compiler_version": p.get("compiler_version"),
        "optimization_enabled": p.get("optimization_enabled"),
        "proxy_type": p.get("proxy_type"),
        "implementation_address": p.get("implementation_address") or (
            (p.get("implementations") or [{}])[0].get("address_hash")
            if isinstance(p.get("implementations"), list) and p.get("implementations") else None
        ),
    }


def _top_holder_stats(payload: Any, token_address: str) -> dict[str, Any]:
    holders = _items(payload)
    balances: list[tuple[str, float]] = []
    for row in holders:
        address = str((row.get("address") or {}).get("hash") if isinstance(row.get("address"), dict) else row.get("address") or "")
        raw = row.get("value") or row.get("value_raw") or row.get("balance")
        n = _num(raw)
        if n is not None and address.lower() != token_address.lower():
            balances.append((address, n))
    balances.sort(key=lambda x: x[1], reverse=True)
    total = sum(x[1] for x in balances)
    top10 = sum(x[1] for x in balances[:10])
    top1 = balances[0][1] if balances else 0.0
    return {
        "sampled_holders": len(balances),
        "top1_share_of_sample": round(top1 / total, 6) if total > 0 else None,
        "top10_share_of_sample": round(top10 / total, 6) if total > 0 else None,
        "sample_balance_total": str(int(total)) if total.is_integer() else str(total),
        "note": "Shares are over the returned holder sample, not guaranteed total supply.",
    }


def _robinhood_deployments(asset: dict[str, Any]) -> Iterable[tuple[int | None, str]]:
    candidates: list[Any] = []
    for key in ("deployments", "chains", "contracts", "addresses"):
        val = asset.get(key)
        if isinstance(val, list):
            candidates.extend(val)
        elif isinstance(val, dict):
            for chain_key, row in val.items():
                if isinstance(row, dict):
                    row = dict(row)
                    row.setdefault("chainId", chain_key)
                    candidates.append(row)
                elif isinstance(row, str):
                    candidates.append({"chainId": chain_key, "address": row})
    for row in candidates:
        if not isinstance(row, dict):
            continue
        addr = row.get("address") or row.get("contractAddress") or row.get("contract_address")
        chain = row.get("chainId") or row.get("chain_id") or row.get("networkId")
        try:
            chain_int = int(chain) if chain is not None else None
        except (TypeError, ValueError):
            chain_int = None
        if isinstance(addr, str) and ADDRESS_RE.fullmatch(addr):
            yield chain_int, addr.lower()


def _canonical_stock_check(
    assets_payload: Any,
    token_address: str,
    symbol: str | None,
    name: str | None,
) -> dict[str, Any]:
    assets = _items(assets_payload)
    exact_assets: list[dict[str, Any]] = []
    ticker_collisions: list[dict[str, Any]] = []
    for asset in assets:
        a_symbol = str(asset.get("tokenSymbol") or asset.get("symbol") or asset.get("ticker") or "").strip()
        a_name = str(asset.get("tokenName") or asset.get("name") or "").strip()
        deployments = list(_robinhood_deployments(asset))
        is_exact = any(chain in (None, CHAIN_ID) and addr == token_address for chain, addr in deployments)
        if is_exact:
            exact_assets.append(asset)
        if symbol and a_symbol and a_symbol.casefold() == symbol.casefold() and not is_exact:
            ticker_collisions.append({"symbol": a_symbol, "name": a_name, "deployment_count": len(deployments)})
        elif name and a_name and a_name.casefold() == name.casefold() and not is_exact:
            ticker_collisions.append({"symbol": a_symbol, "name": a_name, "deployment_count": len(deployments)})
    return {
        "canonical_robinhood_stock_token": bool(exact_assets),
        "ticker_or_name_collision_with_robinhood_registry": bool(ticker_collisions),
        "collision_count": len(ticker_collisions),
        "registry_assets_seen": len(assets),
    }


def _risk_summary(packet: dict[str, Any]) -> dict[str, Any]:
    flags: list[str] = []
    quality: list[str] = []
    contract = packet["contract"]
    stock = packet["canonical_stock_registry"]
    holders = packet["holders"]
    age = packet["deployment"]["age_hours"]
    transfers = packet["activity"].get("transfers_count")

    if not contract.get("verified"):
        flags.append("UNVERIFIED_CONTRACT")
    if stock.get("ticker_or_name_collision_with_robinhood_registry") and not stock.get("canonical_robinhood_stock_token"):
        flags.append("CANONICAL_TICKER_OR_NAME_COLLISION")
    if holders.get("top10_share_of_sample") is not None and holders["top10_share_of_sample"] >= 0.80:
        flags.append("TOP10_SAMPLE_CONCENTRATION_GE_80PCT")
    if holders.get("top1_share_of_sample") is not None and holders["top1_share_of_sample"] >= 0.50:
        flags.append("TOP1_SAMPLE_CONCENTRATION_GE_50PCT")
    if transfers is not None and transfers < 10:
        flags.append("VERY_LOW_TRANSFER_COUNT")
    if age is not None and age <= EARLY_HOURS:
        quality.append("IN_EARLY_FORWARD_TEST_COHORT")
    if age is None:
        flags.append("DEPLOYMENT_AGE_UNKNOWN")

    if any(x in flags for x in ("CANONICAL_TICKER_OR_NAME_COLLISION", "UNVERIFIED_CONTRACT")):
        tier = "HIGH_CAUTION"
    elif any(x.startswith("TOP") or x == "VERY_LOW_TRANSFER_COUNT" for x in flags):
        tier = "CAUTION"
    else:
        tier = "NO_CRITICAL_FLAG_OBSERVED"

    return {
        "tier": tier,
        "flags": flags,
        "cohort_labels": quality,
        "not_a_return_prediction": True,
        "meaning": "Evidence triage only; absence of a flag is not evidence of safety or positive return.",
    }


def scan_token(address: str, fetch_json: JsonFetcher = _json_get, now: datetime | None = None) -> dict[str, Any]:
    """Build one deterministic, read-only evidence packet for a Robinhood Chain token."""
    token = normalize_address(address)
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    urls = {
        "address": f"{BLOCKSCOUT_API}/addresses/{token}",
        "token": f"{BLOCKSCOUT_API}/tokens/{token}",
        "holders": f"{BLOCKSCOUT_API}/tokens/{token}/holders",
        "counters": f"{BLOCKSCOUT_API}/tokens/{token}/counters",
        "assets": ROBINHOOD_ASSETS,
    }

    evidence: dict[str, Any] = {}
    failures: dict[str, str] = {}
    for key, url in urls.items():
        try:
            evidence[key] = fetch_json(url)
        except Exception as exc:  # fail closed but preserve the packet
            failures[key] = str(exc)
            evidence[key] = None

    token_meta = _token_meta(evidence["token"])
    address_info = evidence["address"] if isinstance(evidence["address"], dict) else {}
    creation_tx = address_info.get("creation_transaction_hash")
    creation_time: datetime | None = None
    if creation_tx:
        tx_url = f"{BLOCKSCOUT_API}/transactions/{urllib.parse.quote(str(creation_tx))}"
        try:
            tx = fetch_json(tx_url)
            evidence["creation_tx"] = tx
            if isinstance(tx, dict):
                creation_time = _parse_timestamp(tx.get("timestamp"))
        except Exception as exc:
            failures["creation_tx"] = str(exc)

    age_hours = None
    if creation_time is not None:
        age_hours = max(0.0, (now - creation_time).total_seconds() / 3600.0)

    counters = evidence["counters"] if isinstance(evidence["counters"], dict) else {}
    holder_stats = _top_holder_stats(evidence["holders"], token)
    stock = _canonical_stock_check(
        evidence["assets"], token, token_meta.get("symbol"), token_meta.get("name")
    )
    packet: dict[str, Any] = {
        "schema": "trenchguard-rh.v1",
        "generated_at": now.isoformat(),
        "chain": {"name": CHAIN_NAME, "chain_id": CHAIN_ID},
        "token_address": token,
        "token": token_meta,
        "deployment": {
            "creator": (address_info.get("creator_address_hash") or
                        ((address_info.get("creator_address") or {}).get("hash")
                         if isinstance(address_info.get("creator_address"), dict) else None)),
            "creation_transaction_hash": creation_tx,
            "created_at": creation_time.isoformat() if creation_time else None,
            "age_hours": round(age_hours, 3) if age_hours is not None else None,
            "early_token_definition_hours": EARLY_HOURS,
        },
        "contract": _contract_status(address_info),
        "holders": holder_stats,
        "activity": {
            "transfers_count": int(counters["transfers_count"]) if str(counters.get("transfers_count", "")).isdigit() else None,
            "holders_count": int(counters["token_holders_count"]) if str(counters.get("token_holders_count", "")).isdigit() else token_meta.get("holders"),
        },
        "canonical_stock_registry": stock,
        "data_quality": {
            "material_fetch_failures": failures,
            "complete": not failures,
            "fail_closed": bool(failures),
        },
        "sources": [{"name": s.name, "url": s.url, "authority": s.authority} for s in SOURCES],
    }
    packet["risk_summary"] = _risk_summary(packet)
    return packet


def scan_many(addresses: Iterable[str], fetch_json: JsonFetcher = _json_get) -> list[dict[str, Any]]:
    """Scan addresses in stable input order."""
    return [scan_token(a, fetch_json=fetch_json) for a in addresses]


def _load_batch(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [str(x.get("address") if isinstance(x, dict) else x) for x in payload]
    raise ValueError("batch file must be a JSON list of addresses or {address: ...} objects")


def _dump(obj: Any) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, separators=(",", ": "), ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI parser."""
    parser = argparse.ArgumentParser(
        prog="trenchguard-rh",
        description="Read-only Robinhood Chain token provenance/risk evidence.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("scan", help="scan one token contract")
    one.add_argument("address")
    batch = sub.add_parser("batch", help="scan addresses from a JSON list")
    batch.add_argument("file", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = build_parser().parse_args(argv)
    try:
        if args.command == "scan":
            result: Any = scan_token(args.address)
        else:
            result = scan_many(_load_batch(args.file))
        _dump(result)
        return 0
    except (ValueError, EvidenceError, OSError, json.JSONDecodeError) as exc:
        _dump({"schema": "trenchguard-rh.error.v1", "error": str(exc), "fail_closed": True})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
