from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

BASE = "https://atm.simondalmasso44.workers.dev"
RESOURCE = BASE + "/x402/falsify"
CANONICAL = "0xd89Ef03bC3105C538529AC2657Bc4488c94ff4E4"
BASE_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
HOST = "atm.simondalmasso44.workers.dev"
UA = "ATM-ORDER020-Seller-Live/1.0"


def request(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: int = 25):
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Accept": "application/json,text/plain,*/*", "User-Agent": UA, "Cache-Control": "no-cache"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return response.status, dict(response.headers.items()), raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return exc.code, dict(exc.headers.items()), raw


def as_json(raw: str) -> Any:
    return json.loads(raw)


def wait_for_exact_head(expected_sha: str, seconds: int) -> dict[str, Any]:
    deadline = time.time() + seconds
    last: Any = None
    while time.time() < deadline:
        try:
            status, _, raw = request(BASE + "/health?order020=" + urllib.parse.quote(expected_sha))
            if status == 200:
                body = as_json(raw)
                last = body
                if body.get("ok") is True and str(body.get("git_sha") or "") == expected_sha:
                    return body
        except Exception as exc:
            last = {"error": type(exc).__name__}
        time.sleep(5)
    raise RuntimeError(f"exact_head_not_live expected={expected_sha} last={last}")


def verify_live_discovery(expected_sha: str) -> dict[str, Any]:
    health = wait_for_exact_head(expected_sha, int(os.getenv("ATM_ORDER020_WAIT_SECONDS", "540")))
    status, _, raw = request(BASE + "/openapi.json")
    if status != 200:
        raise RuntimeError(f"openapi_http_{status}")
    openapi = as_json(raw)
    if openapi.get("openapi") != "3.1.0":
        raise RuntimeError("openapi_version_mismatch")
    operation = (((openapi.get("paths") or {}).get("/x402/falsify") or {}).get("post") or {})
    payment_info = operation.get("x-payment-info") or {}
    price = payment_info.get("price") or {}
    if str(price.get("amount")) not in {"0.01", "0.010000"} or price.get("currency") != "USD":
        raise RuntimeError("openapi_price_mismatch")

    status, _, raw = request(BASE + "/.well-known/x402")
    if status != 200:
        raise RuntimeError(f"well_known_http_{status}")
    well_known = as_json(raw)
    if RESOURCE not in (well_known.get("resources") or []):
        raise RuntimeError("well_known_resource_missing")

    status, headers, raw = request(RESOURCE, method="POST", payload={"bad": True})
    if status != 402:
        raise RuntimeError(f"unpaid_probe_expected_402_got_{status}")
    challenge = as_json(raw)
    accepts = challenge.get("accepts") or []
    if len(accepts) != 1:
        raise RuntimeError("x402_accepts_not_single")
    req = accepts[0]
    if req.get("network") != "eip155:8453":
        raise RuntimeError("x402_network_mismatch")
    if str(req.get("asset") or "").lower() != BASE_USDC.lower():
        raise RuntimeError("x402_asset_mismatch")
    if str(req.get("payTo") or "").lower() != CANONICAL.lower():
        raise RuntimeError("x402_payto_mismatch")
    if str(req.get("amount")) != "10000":
        raise RuntimeError("x402_amount_mismatch")
    bazaar = ((challenge.get("extensions") or {}).get("bazaar") or {})
    if not isinstance(bazaar.get("info"), dict) or not isinstance(bazaar.get("schema"), dict):
        raise RuntimeError("bazaar_missing")
    payment_required = headers.get("Payment-Required") or headers.get("PAYMENT-REQUIRED") or headers.get("payment-required")
    if not payment_required:
        raise RuntimeError("payment_required_header_missing")

    status, _, raw = request(BASE + "/api/seller-funnel")
    if status != 200:
        raise RuntimeError(f"seller_funnel_http_{status}")
    funnel = as_json(raw)
    if str(funnel.get("outgoing_spend_usd")) != "0":
        raise RuntimeError("seller_funnel_zero_spend_failed")
    return {"health": health, "openapi": openapi, "well_known": well_known, "challenge": challenge, "funnel": funnel}


def refetch_registration_contracts() -> dict[str, str]:
    s402, _, d402 = request("https://402index.io/llms.txt")
    if s402 != 200 or "/api/v1/register" not in d402:
        raise RuntimeError("402index_contract_unavailable")
    sa, _, da = request("https://agent402.tools/sell")
    if sa != 200 or "/api/index/register" not in da:
        raise RuntimeError("agent402_contract_unavailable")
    return {"402index": "REFETCHED", "agent402": "REFETCHED"}


def register_402index() -> dict[str, Any]:
    payload = {
        "url": RESOURCE,
        "name": "ATM Base USDC Falsifier",
        "protocol": "x402",
        "http_method": "POST",
        "description": "Deterministic Base mainnet USDC funding, escrow, settlement and transfer claim falsifier.",
        "price_usd": 0.01,
        "payment_asset": "USDC",
        "payment_network": "Base",
        "category": "utility",
        "provider": "ATM",
    }
    status, _, raw = request("https://402index.io/api/v1/register", method="POST", payload=payload)
    if status not in {200, 201, 202, 409}:
        raise RuntimeError(f"402index_register_http_{status}:{raw[:500]}")
    try:
        body = as_json(raw)
    except Exception:
        body = {"raw": raw[:500]}
    return {"status": status, "body": body}


def register_agent402() -> dict[str, Any]:
    status, _, raw = request("https://agent402.tools/api/index/register", method="POST", payload={"origin": BASE})
    if status not in {200, 201, 202, 409}:
        raise RuntimeError(f"agent402_register_http_{status}:{raw[:500]}")
    try:
        body = as_json(raw)
    except Exception:
        body = {"raw": raw[:500]}
    return {"status": status, "body": body}


def _contains_host(raw: str) -> bool:
    text = raw.lower()
    return HOST.lower() in text or RESOURCE.lower() in text


def verify_index_readback(seconds: int) -> dict[str, Any]:
    deadline = time.time() + seconds
    last = {"402index": None, "agent402": None}
    while time.time() < deadline:
        q = urllib.parse.quote(HOST)
        s402, _, r402 = request(f"https://402index.io/api/v1/services?q={q}&protocol=x402&limit=100")
        sa, _, ra = request("https://agent402.tools/api/index")
        ok402 = s402 == 200 and _contains_host(r402)
        oka = sa == 200 and _contains_host(ra)
        last = {"402index": {"status": s402, "resolvable": ok402}, "agent402": {"status": sa, "resolvable": oka}}
        if ok402 and oka:
            return last
        time.sleep(10)
    raise RuntimeError("index_readback_not_resolvable:" + json.dumps(last, sort_keys=True))


def main() -> int:
    expected_sha = str(os.getenv("GITHUB_SHA") or os.getenv("ATM_SOURCE_SHA") or "").lower()
    if len(expected_sha) != 40:
        raise SystemExit("ORDER020_EXPECTED_SHA_MISSING")
    live = verify_live_discovery(expected_sha)
    contracts = refetch_registration_contracts()
    r402 = register_402index()
    ra = register_agent402()
    readback = verify_index_readback(int(os.getenv("ATM_ORDER020_INDEX_WAIT_SECONDS", "420")))
    status, _, raw = request(BASE + "/api/seller-funnel")
    funnel = as_json(raw) if status == 200 else {"error": f"http_{status}"}
    receipt = {
        "schema": "ATM_ORDER020_SELLER_DISTRIBUTION_V1",
        "sha": expected_sha,
        "resource": RESOURCE,
        "canonical_pay_to": CANONICAL,
        "network": "eip155:8453",
        "asset": BASE_USDC,
        "price_usdc": "0.01",
        "live": True,
        "bazaar": True,
        "contracts": contracts,
        "registration": {"402index": r402, "agent402": ra},
        "readback": readback,
        "seller_funnel": funnel,
        "outgoing_spend_usd": "0",
        "merge": "NO",
    }
    print("ATM_ORDER020_SELLER_DISTRIBUTION=" + json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
