"""ORDER-009-R1 read-only WorkProtocol + zero-cost Gemma 4 shadow verifier."""
from __future__ import annotations

import hashlib
import html.parser
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

JOB_ID = "f82a9ca9-4b7f-4bdf-91c1-b5ae0516b4eb"
JOB_URL = f"https://workprotocol.ai/jobs/{JOB_ID}"
JOB_API = f"https://workprotocol.ai/api/jobs/{JOB_ID}"
OPEN_JOBS_API = "https://workprotocol.ai/api/jobs?status=open&limit=100&offset=0"
PRICING_URL = "https://ai.google.dev/gemini-api/docs/pricing"
MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
GEMMA_MODELS = ("gemma-4-31b-it", "gemma-4-26b-a4b-it")
ACTIVE_CLAIM_STATES = {
    "claimed", "accepted", "working", "in_progress", "delivered", "verified", "pending_verification",
}
ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = ROOT / "order009-r1-shadow-evidence.json"


class _TextExtractor(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def _request(url: str, *, headers: dict[str, str] | None = None, body: dict[str, Any] | None = None) -> tuple[bytes, dict[str, str], int]:
    payload = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    request_headers = {"User-Agent": "ATM-Order009-R1/1.0", "Accept": "*/*"}
    request_headers.update(headers or {})
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=payload, headers=request_headers, method="POST" if payload is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return response.read(), {key.lower(): value for key, value in response.headers.items()}, int(response.status)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP_{exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"NETWORK_{type(exc.reason).__name__}") from exc


def _json_get(url: str) -> tuple[dict[str, Any], dict[str, str]]:
    raw, headers, status = _request(url, headers={"Accept": "application/json"})
    if status != 200:
        raise RuntimeError(f"HTTP_{status}")
    payload = json.loads(raw.decode())
    if not isinstance(payload, dict):
        raise RuntimeError("NON_OBJECT_JSON")
    return payload, headers


def _sha(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _decimal_text(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    try:
        return f"{float(text):.2f}"
    except ValueError:
        return text


def verify_workprotocol() -> dict[str, Any]:
    """Re-fetch and falsify the exact WorkProtocol target without mutation."""
    detail, headers = _json_get(JOB_API)
    job = detail.get("job") if isinstance(detail.get("job"), dict) else detail
    claims = detail.get("claims")
    if not isinstance(claims, list):
        claims = job.get("claims") if isinstance(job.get("claims"), list) else []
    payments = detail.get("payments") if isinstance(detail.get("payments"), list) else []

    checks = {
        "status_open": str(job.get("status") or "").lower() == "open",
        "amount_75_usdc": _decimal_text(job.get("paymentAmount")) == "75.00"
        and str(job.get("paymentCurrency") or "").upper() == "USDC",
        "rail_base": str(job.get("paymentRail") or "").lower() == "base",
        "max_workers_1": int(job.get("maxWorkers") or 0) == 1,
        "first_wins": str(job.get("competitionMode") or "").lower() == "first-wins",
        "min_reputation_0": float(job.get("minReputation") or 0) == 0.0,
        "no_deadline": job.get("deadline") in (None, ""),
    }
    escrow_tx = str(job.get("escrowTxHash") or "").strip()
    escrow_state = str(job.get("escrowStatus") or "").lower()
    escrow_funded = bool(job.get("escrowFunded")) or bool(escrow_tx) or escrow_state in {"funded", "locked", "escrowed"}
    checks["escrow_75_usdc"] = escrow_funded and checks["amount_75_usdc"]

    active_claims = [
        {"id": str(row.get("id") or ""), "status": str(row.get("status") or "").lower()}
        for row in claims
        if isinstance(row, dict) and str(row.get("status") or "").lower() in ACTIVE_CLAIM_STATES
    ]
    rejected_claims = [
        {"id": str(row.get("id") or ""), "status": str(row.get("status") or "").lower()}
        for row in claims
        if isinstance(row, dict) and str(row.get("status") or "").lower() == "rejected"
    ]
    checks["active_slot_available"] = len(active_claims) == 0

    open_payload, open_headers = _json_get(OPEN_JOBS_API)
    open_rows = open_payload.get("jobs") if isinstance(open_payload.get("jobs"), list) else []
    open_total = open_payload.get("total")
    if not isinstance(open_total, int):
        open_total = len(open_rows)
    checks["target_in_open_feed"] = any(isinstance(row, dict) and str(row.get("id")) == JOB_ID for row in open_rows)

    if not all(checks.values()):
        raise RuntimeError("WORKPROTOCOL_FALSIFIER_FAILED:" + ",".join(key for key, value in checks.items() if not value))

    return {
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "canonical_url": JOB_URL,
        "source_payload_sha256": _sha(detail),
        "etag": headers.get("etag") or None,
        "open_feed_etag": open_headers.get("etag") or None,
        "open_total": open_total,
        "checks": checks,
        "escrow_tx": escrow_tx or None,
        "escrow_state": escrow_state or "UNKNOWN",
        "active_claims": active_claims,
        "rejected_claims": rejected_claims,
        "claims_total": len(claims),
        "payments_total": len(payments),
    }


def _pricing_proves_gemma_free() -> bool:
    raw, _, status = _request(PRICING_URL, headers={"Accept": "text/html"})
    if status != 200:
        return False
    parser = _TextExtractor()
    parser.feed(raw.decode("utf-8", errors="replace"))
    text = " ".join(parser.parts)
    start = text.find("Gemma 4")
    if start < 0:
        return False
    end = text.find("Pricing for tools", start)
    section = text[start : end if end > start else start + 5000]
    return section.count("Free of charge") >= 4 and section.count("Not available") >= 4


def _select_gemini_key() -> tuple[str | None, dict[str, bool]]:
    names = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_AI_API_KEY")
    presence = {name: bool(os.getenv(name, "").strip()) for name in names}
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value, presence
    return None, presence


def _model_catalog(api_key: str) -> set[str]:
    url = MODELS_URL + "?" + urllib.parse.urlencode({"key": api_key})
    raw, _, status = _request(url, headers={"Accept": "application/json"})
    if status != 200:
        return set()
    payload = json.loads(raw.decode())
    rows = payload.get("models") if isinstance(payload, dict) else []
    return {
        str(row.get("name") or "").split("/", 1)[-1]
        for row in rows
        if isinstance(row, dict)
    }


def _gemma_generate(api_key: str, model: str, prompt: str, *, max_tokens: int = 256) -> str:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?"
        + urllib.parse.urlencode({"key": api_key})
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0},
    }
    raw, _, status = _request(url, headers={"Accept": "application/json"}, body=body)
    if status != 200:
        raise RuntimeError(f"GEMMA_HTTP_{status}")
    payload = json.loads(raw.decode())
    candidates = payload.get("candidates") if isinstance(payload, dict) else []
    if not candidates:
        raise RuntimeError("GEMMA_NO_CANDIDATE")
    parts = (((candidates[0] or {}).get("content") or {}).get("parts") or [])
    text = "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict)).strip()
    if not text:
        raise RuntimeError("GEMMA_EMPTY_OUTPUT")
    return text


def _artifact_hash() -> str:
    paths = [
        ROOT / "deliverables/workprotocol_gha_log_parser/cli.py",
        ROOT / "deliverables/workprotocol_gha_log_parser/README.md",
        ROOT / "tests/test_order009_r1_deliverable.py",
    ]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def probe_executor() -> dict[str, Any]:
    """Prove exactly one current no-paid-tier Gemma 4 route, or return owner gate."""
    api_key, presence = _select_gemini_key()
    result: dict[str, Any] = {
        "provider": "gemini-developer-api",
        "credential_presence": presence,
        "credential_present": bool(api_key),
        "paid_fallback": False,
        "outgoing_spend_usd": "0",
        "pricing_url": PRICING_URL,
        "zero_cost_currently_proven": False,
        "model_currently_available": False,
        "live_inference_probe": False,
        "model": None,
    }
    if not api_key:
        result["status"] = "OWNER_SECRET_REQUIRED"
        result["owner_action"] = "Add one free Google AI Studio Gemini API key as repository secret GEMINI_API_KEY."
        return result

    pricing_ok = _pricing_proves_gemma_free()
    result["zero_cost_currently_proven"] = pricing_ok
    if not pricing_ok:
        result["status"] = "FAIL_CLOSED_PRICING_NOT_PROVEN"
        return result

    models = _model_catalog(api_key)
    model = next((candidate for candidate in GEMMA_MODELS if candidate in models), None)
    result["model"] = model
    result["model_currently_available"] = model is not None
    if not model:
        result["status"] = "OWNER_SECRET_REQUIRED"
        result["owner_action"] = "Replace GEMINI_API_KEY with a free Google AI Studio key that exposes Gemma 4."
        return result

    probe = _gemma_generate(api_key, model, "Reply with exactly OK.", max_tokens=8)
    result["live_inference_probe"] = "OK" in probe.upper()
    if not result["live_inference_probe"]:
        result["status"] = "OWNER_SECRET_REQUIRED"
        result["owner_action"] = "Replace GEMINI_API_KEY with a working free Google AI Studio key."
        return result

    task_spec = (ROOT / "deliverables/workprotocol_gha_log_parser/README.md").read_text(encoding="utf-8")
    source = (ROOT / "deliverables/workprotocol_gha_log_parser/cli.py").read_text(encoding="utf-8")
    prompt = f"""You are the zero-cost execution route for a real shadow coding job. No claim or submission is allowed.
Acceptance criteria:
1 CLI accepts a GitHub Actions run URL and outputs JSON with failing step name, error message, stack trace, suggested fix category.
2 Handles pytest/jest, TypeScript/compilation, lint.
3 >=5 mocked GitHub API unit tests success/error.
4 README install/usage/example.
5 Public functions typed; pylint >=8.

Inspect this completed candidate. Return JSON only:
{{"verdict":"PASS"|"FAIL","missing":[short strings],"security":[short strings]}}
README:
{task_spec}
SOURCE:
{source}
"""
    review_text = _gemma_generate(api_key, model, prompt, max_tokens=384)
    cleaned = review_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        review = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError("GEMMA_REVIEW_NOT_JSON") from exc
    result["shadow_review"] = review
    result["status"] = "PASS" if str(review.get("verdict") or "").upper() == "PASS" else "FAIL"
    return result


def main() -> int:
    """Run the R1 read-only target and provider gates and persist sanitized evidence."""
    evidence: dict[str, Any] = {
        "schema": "ATM_ORDER009_R1_SHADOW_V1",
        "head": os.getenv("GITHUB_SHA", "LOCAL"),
        "workprotocol_job": JOB_ID,
        "claim_performed": False,
        "submission_performed": False,
        "merge": False,
        "outgoing_spend_usd": "0",
        "realized_withdrawable_usd": "0",
        "artifact_sha256": _artifact_hash(),
    }
    evidence["workprotocol"] = verify_workprotocol()
    executor = probe_executor()
    evidence["executor"] = executor
    if executor.get("status") == "PASS":
        evidence["terminal"] = "READY_FOR_AUD_MUTATION"
    elif executor.get("status") == "OWNER_SECRET_REQUIRED":
        evidence["terminal"] = "OWNER_SECRET_REQUIRED"
    else:
        evidence["terminal"] = "FAIL_CLOSED"
    EVIDENCE_PATH.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    safe = dict(evidence)
    if isinstance(safe.get("executor"), dict):
        safe["executor"] = {key: value for key, value in safe["executor"].items() if key != "shadow_review"}
    print(json.dumps(safe, sort_keys=True))
    return 0 if evidence["terminal"] in {"READY_FOR_AUD_MUTATION", "OWNER_SECRET_REQUIRED"} else 1


if __name__ == "__main__":
    sys.exit(main())
