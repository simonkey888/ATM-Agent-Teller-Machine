from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

MODEL = os.getenv("ATM_GEMMA4_FREE_MODEL", "gemma-4-31b-it").strip()
PRICING_URL = "https://ai.google.dev/gemini-api/docs/pricing?hl=en"
GITHUB_MODELS_DOC = "https://docs.github.com/en/github-models"
EXPECTED = {"status": "ATM_ZERO_COST_OK"}
OUT = Path("order010-maker-fallback-gate-receipt.json")
UA = "ATM-Order010-MakerFallbackGate/1.0"


def get_text(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _safe_candidate_diagnostic(candidate: object, text: str) -> str:
    if not isinstance(candidate, dict):
        return f"candidate_type={type(candidate).__name__} text_len={len(text)}"
    parts = ((candidate.get("content") or {}).get("parts") or []) if isinstance(candidate.get("content"), dict) else []
    part_shapes = []
    for part in parts[:8]:
        if not isinstance(part, dict):
            part_shapes.append(type(part).__name__)
            continue
        part_shapes.append(
            {
                "keys": sorted(str(k) for k in part.keys()),
                "text_len": len(str(part.get("text") or "")),
                "thought": bool(part.get("thought")),
            }
        )
    # The probe prompt contains no user data or credentials, so a short candidate
    # prefix is safe and lets CI distinguish a model-format issue from no output.
    return json.dumps(
        {
            "finish_reason": candidate.get("finishReason"),
            "candidate_keys": sorted(str(k) for k in candidate.keys()),
            "part_shapes": part_shapes,
            "text_len": len(text),
            "text_prefix": text[:160],
        },
        sort_keys=True,
    )


def google_generate(key: str) -> dict[str, str]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(MODEL, safe='')}:generateContent?key={urllib.parse.quote(key, safe='')}"
    body = json.dumps(
        {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": 'Return one JSON object with exactly one key named "status" and value "ATM_ZERO_COST_OK". No extra keys or prose.'
                        }
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": 64,
                "responseMimeType": "application/json",
            },
        }
    ).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"User-Agent": UA, "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    candidates = payload.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemma 4 live probe returned no candidate")
    candidate = candidates[0]
    parts = ((candidate.get("content") or {}).get("parts") or []) if isinstance(candidate, dict) else []
    text = "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict)).strip()
    if not text:
        raise RuntimeError("Gemma 4 structured probe returned no text: " + _safe_candidate_diagnostic(candidate, text))
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Gemma 4 structured probe invalid JSON: " + _safe_candidate_diagnostic(candidate, text)) from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("Gemma 4 structured probe did not return an object: " + _safe_candidate_diagnostic(candidate, text))
    return {str(k): str(v) for k, v in parsed.items()}


def main() -> int:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise SystemExit("GEMINI_API_KEY missing")

    pricing = re.sub(r"\s+", " ", get_text(PRICING_URL)).lower()
    gemma_at = pricing.find("gemma 4")
    if gemma_at < 0:
        raise SystemExit("Gemma 4 pricing section not found")
    segment = pricing[gemma_at : gemma_at + 24000]
    free_mentions = segment.count("free of charge")
    paid_unavailable_mentions = segment.count("not available")
    pricing_zero = free_mentions >= 2 and paid_unavailable_mentions >= 2
    if not pricing_zero:
        raise SystemExit(
            f"Gemma 4 current pricing no longer proves free-only: free={free_mentions} paid_unavailable={paid_unavailable_mentions}"
        )

    models_payload = json.loads(get_text(f"https://generativelanguage.googleapis.com/v1beta/models?key={urllib.parse.quote(key, safe='')}", timeout=20))
    available = {
        str(row.get("name") or "").split("/", 1)[-1]
        for row in (models_payload.get("models") or [])
        if isinstance(row, dict)
    }
    if MODEL not in available:
        raise SystemExit(f"Gemma 4 model unavailable:{MODEL}")

    exact = google_generate(key)
    if exact != EXPECTED:
        raise SystemExit(f"Gemma 4 exact structured-output probe failed:{exact!r}")

    github_doc = re.sub(r"\s+", " ", get_text(GITHUB_MODELS_DOC)).lower()
    github_retired = "github models has been retired" in github_doc and "july 30, 2026" in github_doc
    if not github_retired:
        raise SystemExit("GitHub Models retirement could not be revalidated")

    receipt = {
        "schema": "ATM_ORDER010_MAKER_FALLBACK_GATE_V1",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "gemini": {
            "credential_present": True,
            "model": MODEL,
            "model_available": True,
            "exact_output_probe": "PASS",
            "expected_output": EXPECTED,
            "published_pricing_free_only": True,
            "billed_usd": "0",
            "pricing_source": PRICING_URL,
            "status": "READY",
        },
        "github_models": {
            "status": "RETIRED",
            "retired_on": "2026-07-30",
            "models_read_permission_added": False,
            "reason": "GITHUB_MODELS_RETIRED_2026_07_30",
            "source": GITHUB_MODELS_DOC,
        },
        "groq": {"consumer_wired": False, "credential_requested": False, "status": "DO_NOT_REQUEST_FROM_OWNER_YET"},
        "cerebras": {"consumer_wired": False, "credential_requested": False, "status": "DO_NOT_REQUEST_FROM_OWNER_YET"},
        "paid_fallback": False,
        "marketplace_credentials_exposed_to_model": False,
        "outgoing_spend_usd": "0",
        "global_block": False,
    }
    OUT.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, sort_keys=True))
    print("ORDER010_MAKER_FALLBACK_GATE=PASS")
    print("OUTGOING_SPEND_USD=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
