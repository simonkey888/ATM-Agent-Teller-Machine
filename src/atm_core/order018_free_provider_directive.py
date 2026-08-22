from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Any

_INSTALLED = False


@dataclass(frozen=True)
class DynamicCandidate:
    provider_id: str
    provider: str
    base_url: str
    model_id: str
    credential_env: str
    priority: int
    claimed_identity: str
    activation_capable: bool
    activation_rule: str
    official_terms_url: str
    official_pricing_url: str
    trust: str


CANDIDATES: tuple[DynamicCandidate, ...] = (
    DynamicCandidate(
        provider_id="opencode-zen-x-preview-f-free",
        provider="opencode-zen",
        base_url="https://opencode.ai/zen/v1",
        model_id="x-preview-f-free",
        credential_env="OPENCODE_API_KEY",
        priority=10,
        claimed_identity="UNKNOWN_STEALTH_MODEL",
        activation_capable=True,
        activation_rule="EXACT_MODEL_METADATA_ZERO_PLUS_FRESH_ZERO_COST_REQUEST_PLUS_CURRENT_TERMS",
        official_terms_url="https://opencode.ai/legal/terms-of-service",
        official_pricing_url="https://opencode.ai/docs/zen/",
        trust="AUD_DIRECTIVE_HIGH_VALUE_CANDIDATE",
    ),
    DynamicCandidate(
        provider_id="openrouter-stealth-ox-alpha",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        model_id="stealth/ox-alpha",
        credential_env="OPENROUTER_API_KEY",
        priority=20,
        claimed_identity="OX_ALPHA_STEALTH_IDENTITY_UNKNOWN",
        activation_capable=True,
        activation_rule="EXACT_STEALTH_MODEL_ZERO_PRICE_PLUS_FRESH_REQUEST_PLUS_MODEL_TERMS_METADATA",
        official_terms_url="https://openrouter.ai/terms",
        official_pricing_url="https://openrouter.ai/api/v1/models",
        trust="AUD_DIRECTIVE_LOWER_CONFIDENCE_THAN_OPENCODE",
    ),
    DynamicCandidate(
        provider_id="openrouter-free-router",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        model_id="openrouter/free",
        credential_env="OPENROUTER_API_KEY",
        priority=30,
        claimed_identity="ROUTER_DYNAMIC",
        activation_capable=False,
        activation_rule="CANDIDATE_ONLY_UNTIL_ROUTER_CAN_BE_FENCED_TO_EXACT_CURRENT_ZERO_PRICE_MODELS",
        official_terms_url="https://openrouter.ai/terms",
        official_pricing_url="https://openrouter.ai/api/v1/models",
        trust="DISCOVERY_ONLY_DYNAMIC_ALIAS",
    ),
    DynamicCandidate(
        provider_id="tokenrouter-deepseek-v4-pro",
        provider="tokenrouter",
        base_url="https://api.tokenrouter.com/v1",
        model_id="deepseek/deepseek-v4-pro",
        credential_env="TOKENROUTER_API_KEY",
        priority=40,
        claimed_identity="DEEPSEEK_V4_PRO",
        activation_capable=False,
        activation_rule="CANDIDATE_ONLY_PROMO_BALANCE_IS_NOT_FREE_TIER; HARD_OWNER_CHARGE_STOP_REQUIRED",
        official_terms_url="https://www.tokenrouter.com/",
        official_pricing_url="https://www.tokenrouter.com/",
        trust="PROMOTIONAL_CREDIT_CANDIDATE_ONLY",
    ),
    DynamicCandidate(
        provider_id="gorouter-opus-5",
        provider="gorouter",
        base_url="https://gorouter.app/v1",
        model_id="claude-opus-5",
        credential_env="GOROUTER_API_KEY",
        priority=50,
        claimed_identity="CLAUDE_OPUS_5_CLAIM_UNVERIFIED_UPSTREAM",
        activation_capable=False,
        activation_rule="CANDIDATE_ONLY_CREDIT_BALANCE_AND_PER_REQUEST_PRICE_DO_NOT_PROVE_ZERO_OWNER_SPEND",
        official_terms_url="https://gorouter.app/",
        official_pricing_url="https://gorouter.app/",
        trust="PROMOTIONAL_CREDIT_CANDIDATE_ONLY",
    ),
)

FREE_FOR_DEV_FEED = {
    "feed_id": "ripienaar/free-for-dev",
    "url": "https://raw.githubusercontent.com/ripienaar/free-for-dev/master/README.md",
    "activation_authority": False,
    "rule": "DISCOVERY_ONLY; AUTHORITATIVE_PROVIDER_REVALIDATION_REQUIRED",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _request_json(method: str, url: str, *, key: str = "", body: dict[str, Any] | None = None, timeout: int = 12) -> tuple[dict[str, Any], int]:
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
    headers = {"Accept": "application/json", "User-Agent": "ATM-Zero-Spend-Provider-Probe/1.0"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read(2 * 1024 * 1024)
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
        if not isinstance(parsed, dict):
            raise ValueError("PROVIDER_NON_OBJECT_RESPONSE")
        return parsed, int(getattr(response, "status", response.getcode()))


def _model_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("data", "models"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return []


def _decimal_zero(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in {"free", "$0", "$0.00", "0", "0.0", "0.00"}:
        return True
    try:
        return Decimal(str(value).replace("$", "").strip()) == 0
    except (InvalidOperation, ValueError):
        return None


def _pricing_zero(row: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    pricing = row.get("pricing") if isinstance(row.get("pricing"), dict) else row.get("price") if isinstance(row.get("price"), dict) else {}
    probes: list[tuple[str, Any]] = []
    for name in ("prompt", "input", "input_usd", "input_price", "completion", "output", "output_usd", "output_price"):
        if name in pricing:
            probes.append((name, pricing.get(name)))
        elif name in row:
            probes.append((name, row.get(name)))
    input_values = [value for name, value in probes if name in {"prompt", "input", "input_usd", "input_price"}]
    output_values = [value for name, value in probes if name in {"completion", "output", "output_usd", "output_price"}]
    input_zero = any(_decimal_zero(value) is True for value in input_values)
    output_zero = any(_decimal_zero(value) is True for value in output_values)
    return input_zero and output_zero, {"observed_fields": {name: str(value)[:80] for name, value in probes}, "input_zero": input_zero, "output_zero": output_zero}


def _extract_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return str(message["content"])
        if isinstance(choices[0].get("text"), str):
            return str(choices[0]["text"])
    return ""


def _response_cost_zero(payload: dict[str, Any]) -> bool | None:
    candidates: list[Any] = []
    usage = payload.get("usage")
    if isinstance(usage, dict):
        for key in ("cost", "cost_usd", "total_cost", "total_cost_usd"):
            if key in usage:
                candidates.append(usage.get(key))
    for key in ("cost", "cost_usd", "request_cost", "request_cost_usd"):
        if key in payload:
            candidates.append(payload.get(key))
    if not candidates:
        return None
    values = [_decimal_zero(value) for value in candidates]
    return bool(values) and all(value is True for value in values)


def _terms_proof(candidate: DynamicCandidate) -> dict[str, Any]:
    req = urllib.request.Request(candidate.official_terms_url, headers={"User-Agent": "ATM-Zero-Spend-Terms-Probe/1.0"}, method="GET")
    with urllib.request.urlopen(req, timeout=8) as response:
        raw = response.read(1024 * 1024)
    text = raw.decode("utf-8", errors="ignore")
    digest = hashlib.sha256(raw).hexdigest()
    if candidate.provider == "opencode-zen":
        ownership = bool(re.search(r"own\s+the\s+Output|own\s+the\s+output", text, re.I))
        prohibited_noncommercial = bool(re.search(r"non[- ]commercial\s+(?:use\s+)?only", text, re.I))
        return {"fresh": True, "sha256": digest, "output_ownership_observed": ownership, "noncommercial_only_observed": prohibited_noncommercial, "usable_for_paid_task": ownership and not prohibited_noncommercial}
    if candidate.provider == "openrouter":
        stealth_terms = "Stealth Program" in text
        return {"fresh": True, "sha256": digest, "stealth_terms_flowdown_observed": stealth_terms, "usable_for_paid_task": stealth_terms}
    return {"fresh": True, "sha256": digest, "usable_for_paid_task": False}


def probe_candidate(candidate: DynamicCandidate) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "provider_id": candidate.provider_id,
        "provider": candidate.provider,
        "model_id": candidate.model_id,
        "claimed_identity": candidate.claimed_identity,
        "probed_at": _now(),
        "activation_capable": candidate.activation_capable,
        "paid_fallback": False,
        "auto_overage": False,
        "model_allowlist": [candidate.model_id],
        "hard_owner_spend_ceiling_usd": "0",
    }
    if not candidate.activation_capable:
        evidence.update({"state": "CANDIDATE_ONLY", "hard_zero_spend_proven": False, "reason": candidate.activation_rule})
        return evidence
    key = os.getenv(candidate.credential_env, "").strip()
    if not key:
        evidence.update({"state": "NO_CREDENTIAL", "hard_zero_spend_proven": False, "reason": "LIVE_PROBE_CREDENTIAL_ABSENT"})
        return evidence
    try:
        models, models_status = _request_json("GET", f"{candidate.base_url}/models", key=key, timeout=10)
        rows = _model_rows(models)
        exact = next((row for row in rows if str(row.get("id") or row.get("name") or "") == candidate.model_id), None)
        if exact is None:
            evidence.update({"state": "UNAVAILABLE", "hard_zero_spend_proven": False, "reason": "MODEL_ALIAS_DRIFT_OR_ABSENT", "models_status": models_status})
            return evidence
        zero, price_evidence = _pricing_zero(exact)
        evidence["model_metadata_sha256"] = hashlib.sha256(json.dumps(exact, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
        evidence["price_evidence"] = price_evidence
        if not zero:
            evidence.update({"state": "UNAVAILABLE", "hard_zero_spend_proven": False, "reason": "EXACT_ZERO_PRICE_NOT_PROVEN"})
            return evidence
        terms = _terms_proof(candidate)
        evidence["terms_evidence"] = terms
        if not terms.get("usable_for_paid_task"):
            evidence.update({"state": "UNAVAILABLE", "hard_zero_spend_proven": False, "reason": "CURRENT_TERMS_NOT_PROVEN_FOR_PAID_TASK_USE"})
            return evidence
        payload, status = _request_json(
            "POST",
            f"{candidate.base_url}/chat/completions",
            key=key,
            body={"model": candidate.model_id, "messages": [{"role": "user", "content": "Reply exactly OK."}], "max_tokens": 1, "temperature": 0},
            timeout=20,
        )
        actual_model = str(payload.get("model") or "")
        exact_response_model = actual_model == candidate.model_id
        response_cost = _response_cost_zero(payload)
        text_ok = bool(_extract_text(payload).strip())
        # If the provider omits per-request cost, the authoritative exact model
        # metadata is the price proof. Alias drift is impossible because both the
        # request and returned model must match the one-item allowlist.
        request_cost_zero = response_cost is True or (response_cost is None and zero and exact_response_model)
        hard = bool(status == 200 and text_ok and exact_response_model and request_cost_zero and zero and terms.get("usable_for_paid_task"))
        evidence.update({
            "state": "HEALTHY" if hard else "UNAVAILABLE",
            "hard_zero_spend_proven": hard,
            "request_status": status,
            "response_model_exact": exact_response_model,
            "request_cost_zero": request_cost_zero,
            "response_cost_field_zero": response_cost,
            "reason": "PASS_HARD_ZERO_SPEND_LIVE_PROBE" if hard else "LIVE_PROBE_DID_NOT_PROVE_HARD_ZERO_SPEND",
        })
        return evidence
    except urllib.error.HTTPError as exc:
        evidence.update({"state": "UNAVAILABLE", "hard_zero_spend_proven": False, "reason": f"HTTP_{int(exc.code)}"})
        return evidence
    except Exception as exc:
        evidence.update({"state": "UNAVAILABLE", "hard_zero_spend_proven": False, "reason": f"{type(exc).__name__}"})
        return evidence


@lru_cache(maxsize=1)
def free_for_dev_feed_status() -> dict[str, Any]:
    result = dict(FREE_FOR_DEV_FEED)
    result["checked_at"] = _now()
    try:
        req = urllib.request.Request(FREE_FOR_DEV_FEED["url"], headers={"User-Agent": "ATM-Free-For-Dev-Discovery/1.0"}, method="GET")
        with urllib.request.urlopen(req, timeout=5) as response:
            raw = response.read(4 * 1024 * 1024)
        text = raw.decode("utf-8", errors="ignore")
        candidate_lines = [line.strip()[:500] for line in text.splitlines() if re.search(r"\b(?:AI|LLM|inference|model API)\b", line, re.I)]
        result.update({"state": "HEALTHY", "sha256": hashlib.sha256(raw).hexdigest(), "candidate_signal_count": len(candidate_lines), "sample_signal_hashes": [hashlib.sha256(line.encode()).hexdigest() for line in candidate_lines[:20]]})
    except Exception as exc:
        result.update({"state": "UNAVAILABLE", "candidate_signal_count": 0, "reason": type(exc).__name__})
    return result


def _persist_dynamic_probe(fabric, candidate: DynamicCandidate, evidence: dict[str, Any]) -> dict[str, Any]:
    fabric.ensure_route(candidate.provider_id, candidate.model_id)
    if evidence.get("hard_zero_spend_proven") is True:
        fabric.conn.execute(
            "UPDATE provider_routes SET state='HEALTHY',failures=0,opened_until=NULL,last_error=NULL,last_probe_at=?,probe_evidence_json=? WHERE provider_id=? AND model_id=?",
            (str(evidence.get("probed_at") or _now()), json.dumps(evidence, sort_keys=True), candidate.provider_id, candidate.model_id),
        )
    elif evidence.get("state") not in {"NO_CREDENTIAL", "CANDIDATE_ONLY"}:
        fabric.record_failure(candidate.provider_id, candidate.model_id, str(evidence.get("reason") or "UNVERIFIED"))
        fabric.conn.execute(
            "UPDATE provider_routes SET probe_evidence_json=?,last_probe_at=? WHERE provider_id=? AND model_id=?",
            (json.dumps(evidence, sort_keys=True), str(evidence.get("probed_at") or _now()), candidate.provider_id, candidate.model_id),
        )
    else:
        fabric.conn.execute(
            "UPDATE provider_routes SET state='UNVERIFIED',last_error=?,last_probe_at=?,probe_evidence_json=? WHERE provider_id=? AND model_id=?",
            (str(evidence.get("reason") or evidence.get("state")), str(evidence.get("probed_at") or _now()), json.dumps(evidence, sort_keys=True), candidate.provider_id, candidate.model_id),
        )
    return fabric.status(candidate.provider_id, candidate.model_id)


def _dynamic_routes(fabric) -> list[tuple[DynamicCandidate, dict[str, Any]]]:
    rows: list[tuple[DynamicCandidate, dict[str, Any]]] = []
    for candidate in sorted(CANDIDATES, key=lambda row: row.priority):
        if fabric.circuit_open(candidate.provider_id, candidate.model_id):
            rows.append((candidate, fabric.status(candidate.provider_id, candidate.model_id)))
            continue
        evidence = probe_candidate(candidate)
        rows.append((candidate, _persist_dynamic_probe(fabric, candidate, evidence)))
    return rows


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    from . import maker_router
    from . import provider_fabric
    from . import provider_registry

    # Static registry visibility only. None of these candidates are admitted by
    # config alone; live dynamic proof is a separate authority-free health gate.
    existing = {row.provider_id for row in provider_registry.PROVIDERS}
    additions = []
    for candidate in CANDIDATES:
        if candidate.provider_id in existing:
            continue
        additions.append(provider_registry.ProviderContract(
            provider_id=candidate.provider_id,
            base_url=candidate.base_url,
            model_id=candidate.model_id,
            modalities=("text",),
            context="AUTHORITATIVE_MODEL_METADATA_AT_RUNTIME",
            rate_limits="DYNAMIC_FAIL_CLOSED_429_QUOTA_TIMEOUT",
            free_tier_type="DYNAMIC_CANDIDATE_HARD_ZERO_SPEND_PROOF_REQUIRED",
            commercial_use_allowed=False,
            paid_task_use_allowed=False,
            credit_card_required=False,
            auto_overage_possible=True,
            data_retention_training_disclosure="PROVIDER_AND_MODEL_SPECIFIC; PUBLIC_NONCONFIDENTIAL_TASK_INPUT_ONLY",
            region_restrictions="RUNTIME_PROBE_REQUIRED",
            credential_env=candidate.credential_env,
            last_official_verification="2026-08-21",
            official_terms_url=candidate.official_terms_url,
            official_pricing_url=candidate.official_pricing_url,
            admission="CANDIDATE_ONLY_UNTIL_RUNTIME_HARD_ZERO_SPEND_PROOF",
        ))
    provider_registry.PROVIDERS = tuple((*provider_registry.PROVIDERS, *additions))

    original_public_registry = provider_registry.public_registry

    def public_registry() -> dict[str, object]:
        data = original_public_registry()
        data["dynamic_runtime_candidates"] = [
            {
                "provider_id": row.provider_id,
                "provider": row.provider,
                "base_url": row.base_url,
                "model_id": row.model_id,
                "priority": row.priority,
                "claimed_identity": row.claimed_identity,
                "activation_capable": row.activation_capable,
                "activation_rule": row.activation_rule,
                "credential_present": bool(os.getenv(row.credential_env, "").strip()),
                "paid_fallback": False,
                "owner_spend_ceiling_usd": "0",
            }
            for row in CANDIDATES
        ]
        data["discovery_feeds"] = [dict(FREE_FOR_DEV_FEED)]
        return data

    provider_registry.public_registry = public_registry

    original_public_fabric = provider_fabric.public_provider_fabric
    original_route = provider_fabric.DurableFreeProviderFabric.route

    def _route(self, route_candidates: list[tuple[str, str]]) -> dict[str, Any]:
        static = original_route(self, route_candidates)
        dynamic_ids = {row.provider_id: row for row in CANDIDATES if row.activation_capable}
        for provider_id, model_id in route_candidates:
            candidate = dynamic_ids.get(provider_id)
            if candidate is None or candidate.model_id != model_id:
                continue
            status = self.status(provider_id, model_id)
            evidence = status.get("probe_evidence") or {}
            if status.get("available") and evidence.get("hard_zero_spend_proven") is True:
                return {"state": "READY", "provider_id": provider_id, "model_id": model_id, "paid_fallback": False}
        return static

    provider_fabric.DurableFreeProviderFabric.route = _route

    def public_provider_fabric(path=provider_fabric.DEFAULT_PROVIDER_STATE) -> dict[str, Any]:
        fabric = provider_fabric.DurableFreeProviderFabric(path)
        try:
            dynamic = _dynamic_routes(fabric)
        finally:
            fabric.close()
        data = original_public_fabric(path)
        data["directive"] = "ORDER-018_FREE_PROVIDER_FABRIC"
        data["dynamic_candidates"] = [
            {
                **asdict(candidate),
                "credential_present": bool(os.getenv(candidate.credential_env, "").strip()),
                "runtime_status": status,
            }
            for candidate, status in dynamic
        ]
        data["free_for_dev_feed"] = free_for_dev_feed_status()
        data["dynamic_activation_requires_hard_zero_spend_live_probe"] = True
        data["promotional_balance_is_free_tier"] = False
        data["paid_fallback"] = False
        data["auto_overage"] = False
        return data

    provider_fabric.public_provider_fabric = public_provider_fabric

    original_routes = maker_router.ZeroCostMakerRouter.routes
    original_request_route = maker_router.ZeroCostMakerRouter._request_route

    def routes(self, preferred_gemini_model: str, gemini_failovers: tuple[str, ...]):
        base_routes = original_routes(self, preferred_gemini_model, gemini_failovers)
        fabric = provider_fabric.DurableFreeProviderFabric(self.provider_state_path)
        try:
            dynamic = _dynamic_routes(fabric)
        finally:
            fabric.close()
        ready = []
        for candidate, status in dynamic:
            evidence = status.get("probe_evidence") or {}
            if not candidate.activation_capable or not status.get("available") or evidence.get("hard_zero_spend_proven") is not True:
                continue
            ready.append(maker_router.MakerRoute(
                provider=candidate.provider,
                provider_id=candidate.provider_id,
                model=candidate.model_id,
                ready=True,
                zero_cost_proven=True,
                billed_usd="0",
                reason="PASS_HARD_ZERO_SPEND_LIVE_PROBE",
            ))
        return [*ready, *base_routes]

    def _openai_generate(self, candidate: DynamicCandidate, prompt: str, max_output_tokens: int, structured_json: bool) -> str:
        key = os.getenv(candidate.credential_env, "").strip()
        if not key:
            raise maker_router.MakerRouteUnavailable(f"{candidate.credential_env} absent")
        body: dict[str, Any] = {
            "model": candidate.model_id,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_output_tokens,
            "temperature": 0 if structured_json else 0.2,
        }
        if structured_json:
            body["response_format"] = {"type": "json_object"}
        try:
            payload, status = _request_json("POST", f"{candidate.base_url}/chat/completions", key=key, body=body, timeout=300)
        except urllib.error.HTTPError as exc:
            if int(exc.code) == 429:
                raise maker_router.MakerRouteUnavailable("PROVIDER_429") from exc
            raise maker_router.MakerRouteUnavailable(f"PROVIDER_HTTP_{int(exc.code)}") from exc
        except TimeoutError as exc:
            raise maker_router.MakerRouteUnavailable("PROVIDER_TIMEOUT") from exc
        except Exception as exc:
            raise maker_router.MakerRouteUnavailable(f"PROVIDER_NETWORK:{type(exc).__name__}") from exc
        if status != 200 or str(payload.get("model") or "") != candidate.model_id:
            raise maker_router.MakerRouteUnavailable("MODEL_ALIAS_DRIFT")
        cost = _response_cost_zero(payload)
        if cost is False:
            raise maker_router.MakerRouteUnavailable("NONZERO_REQUEST_COST")
        text = _extract_text(payload).strip()
        if not text:
            raise maker_router.MakerRouteUnavailable("PROVIDER_EMPTY_RESPONSE")
        return text

    def request_route(self, route, prompt: str, max_output_tokens: int, structured_json: bool):
        candidate = next((row for row in CANDIDATES if row.provider_id == route.provider_id and row.model_id == route.model), None)
        if candidate is None:
            return original_request_route(self, route, prompt, max_output_tokens, structured_json)
        if not candidate.activation_capable:
            raise maker_router.MakerRouteUnavailable("CANDIDATE_ONLY_PROVIDER_BLOCKED")
        fabric = provider_fabric.DurableFreeProviderFabric(self.provider_state_path)
        try:
            status = fabric.status(candidate.provider_id, candidate.model_id)
        finally:
            fabric.close()
        evidence = status.get("probe_evidence") or {}
        if not status.get("available") or evidence.get("hard_zero_spend_proven") is not True:
            raise maker_router.MakerRouteUnavailable("HARD_ZERO_SPEND_LIVE_PROOF_MISSING")
        return _openai_generate(self, candidate, prompt, max_output_tokens, structured_json)

    maker_router.ZeroCostMakerRouter.routes = routes
    maker_router.ZeroCostMakerRouter._request_route = request_route
