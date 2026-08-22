from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deliverables" / "trenchguard_rh"))

import trenchguard as tg  # noqa: E402

TOKEN = "0x1111111111111111111111111111111111111111"
CANON = "0x2222222222222222222222222222222222222222"


def fixture_fetch(url: str):
    if "/addresses/" in url:
        return {
            "creation_transaction_hash": "0x" + "a" * 64,
            "creator_address_hash": "0x" + "9" * 40,
            "is_verified": False,
            "name": "Fake"
        }
    if "/transactions/" in url:
        return {"timestamp": "2026-08-17T12:00:00Z"}
    if "/tokens/" in url and url.endswith("/holders"):
        return {"items": [
            {"address": {"hash": "0x" + "3" * 40}, "value": "600"},
            {"address": {"hash": "0x" + "4" * 40}, "value": "300"},
            {"address": {"hash": "0x" + "5" * 40}, "value": "100"},
        ]}
    if "/tokens/" in url and url.endswith("/counters"):
        return {"transfers_count": "8", "token_holders_count": "3"}
    if "/tokens/" in url:
        return {"name": "Robinhood Example", "symbol": "RHEX", "decimals": "18"}
    if url == tg.ROBINHOOD_ASSETS:
        return {"assets": [{
            "tokenName": "Robinhood Example",
            "tokenSymbol": "RHEX",
            "deployments": [{"chainId": 4663, "address": CANON}],
        }]}
    raise AssertionError(url)


def test_address_validation():
    assert tg.normalize_address(TOKEN.upper().replace("0X", "0x")) == TOKEN
    try:
        tg.normalize_address("not-an-address")
    except ValueError:
        pass
    else:
        raise AssertionError("malformed address accepted")


def test_scan_is_read_only_and_deterministic():
    now = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    a = tg.scan_token(TOKEN, fetch_json=fixture_fetch, now=now)
    b = tg.scan_token(TOKEN, fetch_json=fixture_fetch, now=now)
    assert a == b
    assert a["chain"]["chain_id"] == 4663
    assert a["risk_summary"]["not_a_return_prediction"] is True


def test_collision_and_contract_flags():
    p = tg.scan_token(
        TOKEN,
        fetch_json=fixture_fetch,
        now=datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc),
    )
    assert p["canonical_stock_registry"]["canonical_robinhood_stock_token"] is False
    assert p["canonical_stock_registry"]["ticker_or_name_collision_with_robinhood_registry"] is True
    assert "CANONICAL_TICKER_OR_NAME_COLLISION" in p["risk_summary"]["flags"]
    assert "UNVERIFIED_CONTRACT" in p["risk_summary"]["flags"]


def test_early_cohort_is_label_not_alpha_claim():
    p = tg.scan_token(
        TOKEN,
        fetch_json=fixture_fetch,
        now=datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc),
    )
    assert p["deployment"]["age_hours"] == 24.0
    assert "IN_EARLY_FORWARD_TEST_COHORT" in p["risk_summary"]["cohort_labels"]
    assert p["risk_summary"]["not_a_return_prediction"]


def test_concentration_and_low_activity_flags():
    p = tg.scan_token(
        TOKEN,
        fetch_json=fixture_fetch,
        now=datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc),
    )
    assert p["holders"]["top1_share_of_sample"] == 0.6
    assert p["holders"]["top10_share_of_sample"] == 1.0
    assert "TOP10_SAMPLE_CONCENTRATION_GE_80PCT" in p["risk_summary"]["flags"]
    assert "VERY_LOW_TRANSFER_COUNT" in p["risk_summary"]["flags"]


def test_material_fetch_failure_fails_closed():
    def broken(url: str):
        if "/addresses/" in url:
            raise tg.EvidenceError("HTTP_503")
        return fixture_fetch(url)
    p = tg.scan_token(
        TOKEN,
        fetch_json=broken,
        now=datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc),
    )
    assert p["data_quality"]["fail_closed"] is True
    assert "address" in p["data_quality"]["material_fetch_failures"]


def test_canonical_address_not_marked_collision():
    def canonical_fetch(url: str):
        if "/tokens/" in url and not url.endswith(("/holders", "/counters")):
            return {"name": "Robinhood Example", "symbol": "RHEX"}
        if url == tg.ROBINHOOD_ASSETS:
            return {"assets": [{
                "tokenName": "Robinhood Example",
                "tokenSymbol": "RHEX",
                "deployments": [{"chainId": 4663, "address": TOKEN}],
            }]}
        return fixture_fetch(url)
    p = tg.scan_token(
        TOKEN,
        fetch_json=canonical_fetch,
        now=datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc),
    )
    assert p["canonical_stock_registry"]["canonical_robinhood_stock_token"] is True
    assert p["canonical_stock_registry"]["ticker_or_name_collision_with_robinhood_registry"] is False


def test_batch_preserves_order():
    other = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    got = tg.scan_many([TOKEN, other], fetch_json=fixture_fetch)
    assert [x["token_address"] for x in got] == [TOKEN, other]


def test_json_output_schema_roundtrips():
    p = tg.scan_token(
        TOKEN,
        fetch_json=fixture_fetch,
        now=datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc),
    )
    encoded = json.dumps(p, sort_keys=True)
    decoded = json.loads(encoded)
    assert decoded["schema"] == "trenchguard-rh.v1"
