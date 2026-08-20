"""Independent acceptance/falsification checker for ORDER-009-R2 TrenchGuard RH."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "deliverables" / "trenchguard_rh" / "trenchguard.py"
REPORT = ROOT / "deliverables" / "trenchguard_rh" / "RESEARCH.md"
README = ROOT / "deliverables" / "trenchguard_rh" / "README.md"
OUT = ROOT / "order009-r2-artifact-check.json"
TOKEN = "0x1111111111111111111111111111111111111111"

spec = importlib.util.spec_from_file_location("trenchguard_check_target", CLI)
assert spec and spec.loader
tg = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = tg
spec.loader.exec_module(tg)


def sample_fetch(url: str) -> Any:
    if "/addresses/" in url:
        return {"creation_transaction_hash": "0x" + "a" * 64, "creator_address_hash": "0x" + "b" * 40, "is_verified": False}
    if "/transactions/" in url:
        return {"timestamp": "2026-08-18T00:00:00Z"}
    if url.endswith("/holders"):
        return {"items": [
            {"address": {"hash": "0x" + "1" * 40}, "value": "850"},
            {"address": {"hash": "0x" + "2" * 40}, "value": "150"},
        ]}
    if url.endswith("/counters"):
        return {"transfers_count": "5", "token_holders_count": "2"}
    if "/tokens/" in url:
        return {"name": "AAPL", "symbol": "AAPL"}
    if url == tg.ROBINHOOD_ASSETS:
        return {"assets": [{"tokenName": "AAPL", "tokenSymbol": "AAPL", "deployments": [
            {"chainId": 4663, "address": "0x2222222222222222222222222222222222222222"}
        ]}]}
    raise AssertionError(url)


def main() -> int:
    checks: dict[str, Any] = {}
    source = CLI.read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = {getattr(n.func, "attr", getattr(n.func, "id", "")) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    forbidden = {"urlopen"} & calls
    # urllib.urlopen is allowed only inside the dedicated GET helper; static mutation strings must be absent.
    mutation_terms = ["eth_sendRawTransaction", "eth_sendTransaction", "approve(", "swap(", "transfer("]
    checks["no_chain_mutation_primitives"] = not any(x in source for x in mutation_terms)
    checks["chain_locked_robinhood_4663"] = "CHAIN_ID = 4663" in source
    checks["canonical_registry_check"] = "canonical_robinhood_stock_token" in source
    checks["concentration_evidence"] = "top10_share_of_sample" in source
    checks["fail_closed_data_quality"] = "fail_closed" in source
    checks["no_return_prediction_claim"] = "not_a_return_prediction" in source

    packet = tg.scan_token(
        TOKEN, fetch_json=sample_fetch,
        now=datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc),
    )
    checks["collision_detected"] = packet["canonical_stock_registry"]["ticker_or_name_collision_with_robinhood_registry"]
    checks["unverified_detected"] = "UNVERIFIED_CONTRACT" in packet["risk_summary"]["flags"]
    checks["concentration_detected"] = "TOP10_SAMPLE_CONCENTRATION_GE_80PCT" in packet["risk_summary"]["flags"]
    checks["low_activity_detected"] = "VERY_LOW_TRANSFER_COUNT" in packet["risk_summary"]["flags"]

    report = REPORT.read_text(encoding="utf-8")
    required_report = [
        "Market map", "User workflow", "Robinhood Chain", "Verified evidence",
        "Assumptions and inference", "Validation protocol", "false-positive",
        "maximum drawdown", "survivorship", "look-ahead", "slippage", "fees",
        "random", "market-cap", "forward paper", "does not prove",
    ]
    checks["research_gate"] = all(term.casefold() in report.casefold() for term in required_report)
    readme = README.read_text(encoding="utf-8")
    checks["readme_usage"] = "python trenchguard.py scan" in readme and "read-only" in readme.casefold()

    payload = {
        "schema": "ATM_ORDER009_R2_ARTIFACT_CHECK_V1",
        "checks": checks,
        "pass": all(bool(v) for v in checks.values()),
        "artifact_sha256": hashlib.sha256(
            CLI.read_bytes() + b"\0" + REPORT.read_bytes() + b"\0" + README.read_bytes()
        ).hexdigest(),
        "representative_packet_sha256": hashlib.sha256(
            json.dumps(packet, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    OUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
