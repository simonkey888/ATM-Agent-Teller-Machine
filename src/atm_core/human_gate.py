from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse, parse_qsl

SCHEMA = "ATM_HUMAN_GATE_REQUEST_V1"
EVENT_SCHEMA = "ATM_HUMAN_GATE_EVENT_V1"
RESUME_SCHEMA = "ATM_HUMAN_GATE_RESUME_V1"
CANONICAL_WALLET = "0xd89Ef03bC3105C538529AC2657Bc4488c94ff4E4"
CLASSES = {
    "AUTH_LOGIN", "MFA", "CAPTCHA", "WALLET_CONNECT", "WALLET_MESSAGE_SIGNATURE",
    "WALLET_TYPED_DATA_SIGNATURE", "PLATFORM_APPROVAL", "KYC_IDENTITY", "LEGAL_TERMS",
    "IRREVERSIBLE_OWNER_DECISION", "PLATFORM_HARDSTOP_HUMAN_ONLY",
}
TERMINAL = {"VERIFIED_COMPLETE", "REJECTED_BY_OWNER", "EXPIRED", "INVALIDATED", "FAILED"}
SAFE_HOSTS = {
    "x402scan": {"x402scan.com", "www.x402scan.com"},
    "guru": {"guru.com", "www.guru.com"},
    "fixture": {"example.com"},
}
FORBIDDEN_TEXT = (
    "system prompt", "developer message", "chain of thought", "hidden context", "seed phrase",
    "private key", "password", "environment dump", "session cookie", "ignore previous",
)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class HumanGateRequest:
    request_id: str
    source: str
    source_object_id: str
    source_object_url: str
    rail_owner: str
    current_external_object_hash: str
    human_gate_class: str
    human_only_evidence: bool
    required_action: str
    short_title: str
    why_required: str
    exact_action_label: str
    safe_action_url: str | None
    owner_cost_usd: str
    financial_effect: str
    irreversibility: str
    risk_summary: str
    post_gate_resume: str
    external_readback_contract: str
    evidence_refs: tuple[str, ...] = ()
    opportunity_id: str | None = None
    wallet_chain: str | None = None
    wallet_expected_address: str | None = None
    signature_method: str | None = None
    signature_payload_digest: str | None = None
    created_at: str = ""
    updated_at: str = ""
    expires_at: str | None = None
    dedupe_key: str = ""
    state: str = "PENDING_OWNER"
    last_verified_at: str | None = None
    terminal_reason: str | None = None
    schema: str = SCHEMA

    def normalized(self) -> "HumanGateRequest":
        created = self.created_at or utcnow()
        updated = self.updated_at or created
        payload = replace(self, created_at=created, updated_at=updated)
        return replace(payload, dedupe_key=payload.dedupe_key or dedupe_key(payload))

    def public_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "human_gate_class": self.human_gate_class,
            "short_title": self.short_title,
            "state": self.state,
            "expires_at": self.expires_at,
            "owner_cost_usd": self.owner_cost_usd,
            "financial_effect": self.financial_effect,
        }

    def owner_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["evidence_refs"] = list(self.evidence_refs)
        return out


@dataclass(frozen=True)
class HumanGateEvent:
    request_id: str
    state: str
    action: str
    current_external_object_hash: str
    observed_at: str
    last_verified_at: str | None = None
    terminal_reason: str | None = None
    resume_event: dict[str, Any] | None = None
    nonce: str | None = None
    signature_digest: str | None = None
    recovered_wallet: str | None = None
    outgoing_spend_usd: str = "0"
    schema: str = EVENT_SCHEMA


def safe_action_url(source: str, raw: str) -> str:
    parsed = urlparse(str(raw or ""))
    allowed = SAFE_HOSTS.get(source.lower())
    if not allowed or parsed.scheme != "https" or parsed.hostname not in allowed or parsed.username or parsed.password:
        raise ValueError("SAFE_ACTION_URL_REJECTED")
    for key, _ in parse_qsl(parsed.query, keep_blank_values=True):
        if any(part in key.lower() for part in ("token", "secret", "password", "cookie", "session", "auth")):
            raise ValueError("SAFE_ACTION_URL_SECRET_QUERY_REJECTED")
    return parsed.geturl()


def qualify(request: HumanGateRequest, *, untrusted_source_text: str = "") -> tuple[bool, tuple[str, ...]]:
    errors: list[str] = []
    if request.schema != SCHEMA:
        errors.append("SCHEMA_INVALID")
    if request.human_gate_class not in CLASSES:
        errors.append("CLASS_NOT_HUMAN_GATE")
    if request.human_only_evidence is not True:
        errors.append("HUMAN_ONLY_EVIDENCE_REQUIRED")
    if request.owner_cost_usd != "0":
        errors.append("ZERO_SPEND_VIOLATION")
    if request.wallet_expected_address and request.wallet_expected_address.lower() != CANONICAL_WALLET.lower():
        errors.append("WRONG_CANONICAL_WALLET")
    lowered = untrusted_source_text.lower()
    if any(term in lowered for term in FORBIDDEN_TEXT):
        errors.append("PROMPT_INJECTION_REJECTED")
    if request.safe_action_url:
        try:
            safe_action_url(request.source, request.safe_action_url)
        except ValueError as exc:
            errors.append(str(exc))
    return not errors, tuple(sorted(set(errors)))


def dedupe_key(request: HumanGateRequest) -> str:
    material = {
        "source": request.source,
        "source_object_id": request.source_object_id,
        "human_gate_class": request.human_gate_class,
        "required_action": request.required_action,
        "current_external_object_hash": request.current_external_object_hash,
        "wallet_expected_address": (request.wallet_expected_address or CANONICAL_WALLET).lower(),
    }
    return _digest(material)


class HumanGateInbox:
    """One deterministic Human Gate truth object set with no economic authority."""

    def __init__(self) -> None:
        self._requests: dict[str, HumanGateRequest] = {}
        self._dedupe: dict[str, str] = {}
        self._events: list[HumanGateEvent] = []
        self._used_nonces: set[str] = set()

    @property
    def events(self) -> tuple[HumanGateEvent, ...]:
        return tuple(self._events)

    def submit(self, request: HumanGateRequest, *, untrusted_source_text: str = "") -> HumanGateRequest:
        request = request.normalized()
        ok, errors = qualify(request, untrusted_source_text=untrusted_source_text)
        if not ok:
            raise ValueError("|".join(errors))
        prior_id = self._dedupe.get(request.dedupe_key)
        if prior_id:
            prior = self._requests[prior_id]
            if prior.current_external_object_hash == request.current_external_object_hash and prior.state not in TERMINAL:
                return prior
            self._requests[prior_id] = replace(prior, state="INVALIDATED", terminal_reason="SUPERSEDED_EXTERNAL_OBJECT", updated_at=utcnow())
        self._requests[request.request_id] = request
        self._dedupe[request.dedupe_key] = request.request_id
        return request

    def get(self, request_id: str) -> HumanGateRequest:
        return self._requests[request_id]

    def pending_count(self) -> int:
        return sum(1 for row in self._requests.values() if row.state not in TERMINAL)

    def record_owner_action(self, request_id: str, *, action: str, nonce: str, recovered_wallet: str, signature_digest: str) -> HumanGateRequest:
        row = self.get(request_id)
        if nonce in self._used_nonces:
            raise ValueError("SIGNATURE_REPLAY")
        if recovered_wallet.lower() != CANONICAL_WALLET.lower():
            raise ValueError("WRONG_WALLET_ACCEPTANCE")
        self._used_nonces.add(nonce)
        mapping = {"OWNER_OPENED": "OWNER_OPENED", "OWNER_ACTION_RETURNED": "OWNER_ACTION_RETURNED", "REJECTED_BY_OWNER": "REJECTED_BY_OWNER", "SNOOZED": "SNOOZED"}
        if action not in mapping:
            raise ValueError("OWNER_ACTION_NOT_TYPED")
        updated = replace(row, state=mapping[action], updated_at=utcnow())
        self._requests[request_id] = updated
        self._events.append(HumanGateEvent(request_id=request_id, state=updated.state, action=action, current_external_object_hash=row.current_external_object_hash, observed_at=updated.updated_at, nonce=nonce, signature_digest=signature_digest, recovered_wallet=recovered_wallet.lower()))
        return updated

    def verify_external(self, request_id: str, readback: Callable[[HumanGateRequest], dict[str, Any]]) -> HumanGateRequest:
        row = self.get(request_id)
        checking = replace(row, state="VERIFYING_EXTERNAL_STATE", updated_at=utcnow())
        self._requests[request_id] = checking
        proof = readback(checking)
        authoritative = proof.get("authoritative") is True
        verified = proof.get("verified") is True
        if authoritative and verified:
            resume = {"schema": RESUME_SCHEMA, "request_id": request_id, "rail_owner": row.rail_owner, "opportunity_id": row.opportunity_id, "post_gate_resume": row.post_gate_resume, "verified_external_state": proof.get("evidence"), "outgoing_spend_usd": "0"}
            final = replace(checking, state="VERIFIED_COMPLETE", updated_at=utcnow(), last_verified_at=utcnow(), terminal_reason="AUTHORITATIVE_EXTERNAL_STATE_VERIFIED")
            event = HumanGateEvent(request_id=request_id, state=final.state, action="EXTERNAL_READBACK", current_external_object_hash=row.current_external_object_hash, observed_at=final.updated_at, last_verified_at=final.last_verified_at, terminal_reason=final.terminal_reason, resume_event=resume)
        else:
            final = replace(checking, state="AWAITING_EXTERNAL_READBACK", updated_at=utcnow(), terminal_reason="READBACK_NOT_AUTHORITATIVE")
            event = HumanGateEvent(request_id=request_id, state=final.state, action="EXTERNAL_READBACK", current_external_object_hash=row.current_external_object_hash, observed_at=final.updated_at, terminal_reason=final.terminal_reason)
        self._requests[request_id] = final
        self._events.append(event)
        return final


def fixture_request() -> HumanGateRequest:
    return HumanGateRequest(request_id="order021-fixture-human-gate", source="fixture", source_object_id="order021-security-fixture", source_object_url="https://example.com/order021-fixture", rail_owner="ORDER021_FIXTURE", current_external_object_hash="fixture-v1", human_gate_class="PLATFORM_APPROVAL", human_only_evidence=True, required_action="CONFIRM_NON_ECONOMIC_FIXTURE", short_title="ORDER-021 non-economic Human Gate fixture", why_required="Exercises owner authority without creating a real external effect.", exact_action_label="REVIEW FIXTURE", safe_action_url="https://example.com/order021-fixture", owner_cost_usd="0", financial_effect="NONE", irreversibility="NONE", risk_summary="Fixture only; no external mutation.", post_gate_resume="FIXTURE_RESUME_EVENT", external_readback_contract="FIXTURE_AUTHORITATIVE_READBACK", wallet_chain="eip155:8453", wallet_expected_address=CANONICAL_WALLET, signature_method="eth_signTypedData_v4", evidence_refs=("ORDER-021",)).normalized()
