from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FundingStatus(str, Enum):
    VERIFIED = "VERIFIED"
    FUNDED_SIGNAL = "FUNDED_SIGNAL"
    UNVERIFIED = "UNVERIFIED"
    UNFUNDED = "UNFUNDED"
    UNKNOWN = "UNKNOWN"


class AgentPolicy(str, Enum):
    AGENT_ONLY = "AGENT_ONLY"
    AGENT_ALLOWED = "AGENT_ALLOWED"
    HUMAN_ONLY = "HUMAN_ONLY"
    PROHIBITED = "PROHIBITED"
    UNKNOWN = "UNKNOWN"


class ExecutorClass(str, Enum):
    HTTP_RESEARCH = "HTTP_RESEARCH"
    CONTENT_GENERATION = "CONTENT_GENERATION"
    DATA_EXTRACTION = "DATA_EXTRACTION"
    GITHUB_BOUNDED_PATCH = "GITHUB_BOUNDED_PATCH"
    API_INTEGRATION = "API_INTEGRATION"
    TEST_FIX = "TEST_FIX"
    DOCS = "DOCS"
    LIGHT_QA = "LIGHT_QA"
    HEAVY_BUILD = "HEAVY_BUILD"
    UNSUPPORTED = "UNSUPPORTED"


class RadarDisposition(str, Enum):
    ATTACK_NOW = "ATTACK_NOW"
    MONITOR = "MONITOR"
    HUMAN_GATE = "HUMAN_GATE"
    REJECT = "REJECT"


class OperationalState(str, Enum):
    """Canonical admission result used by ranking and every mutation boundary."""

    EXECUTABLE = "EXECUTABLE"
    WATCH_ONLY = "WATCH_ONLY"
    HUMAN_GATE = "HUMAN_GATE"
    UNVERIFIED = "UNVERIFIED"
    STALE = "STALE"
    REJECTED_SLOP = "REJECTED_SLOP"


class SourceState(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class UniversalOpportunity(BaseModel):
    """Cross-rail opportunity contract. External strings remain untrusted data."""

    model_config = ConfigDict(extra="allow")

    source: str
    external_id: str
    canonical_url: str
    title: str
    description: str = ""
    category: str = "unknown"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deadline: datetime | None = None
    payout_gross: Decimal = Decimal("0")
    payout_currency: str = "USD"
    payout_usd: Decimal | None = None
    fees: Decimal = Decimal("0")
    payout_net: Decimal = Decimal("0")
    funding_status: FundingStatus = FundingStatus.UNKNOWN
    funding_proof: dict[str, Any] = Field(default_factory=dict)
    escrow: dict[str, Any] = Field(default_factory=dict)
    competition: int = 0
    claims: int = 0
    open_prs: int = 0
    eligibility: str = "UNKNOWN"
    agent_policy: AgentPolicy = AgentPolicy.UNKNOWN
    claim_method: str = "UNKNOWN"
    submission_method: str = "UNKNOWN"
    payment_rail: str = "UNKNOWN"
    payout_latency: str = "UNKNOWN"
    withdrawal_path: str = "UNKNOWN"
    human_gate: str | None = None
    estimated_execution_minutes: int = 60
    required_capabilities: list[str] = Field(default_factory=list)
    execution_cost_usd: Decimal = Decimal("0")
    source_state_hash: str
    batchable: bool = False
    executor_class: ExecutorClass = ExecutorClass.UNSUPPORTED
    disposition: RadarDisposition = RadarDisposition.MONITOR
    operational_state: OperationalState = OperationalState.UNVERIFIED
    rejection_reasons: list[str] = Field(default_factory=list)
    money_velocity_score: Decimal = Decimal("0")
    source_state: SourceState = SourceState.UNKNOWN
    source_checked_at: datetime | None = None
    source_ttl_seconds: int = 900
    external_object_exists: bool | None = None
    external_status: str = "UNKNOWN"
    submission_window_open: bool | None = None
    stake_required: bool | None = None
    paid_action_required: bool | None = None
    current_worker_action_available: bool | None = None
    canonical_identity_eligible: bool | None = None
    credential_boundary_ready: bool | None = None
    already_submitted_by_atm: bool | None = None
    provenance_tags: list[str] = Field(default_factory=list)

    @field_validator("canonical_url")
    @classmethod
    def canonical_https(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("canonical_url must use https")
        return value

    @field_validator("payout_gross", "fees", "payout_net", "execution_cost_usd")
    @classmethod
    def nonnegative_money(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("money fields cannot be negative")
        return value

    @property
    def canonical_id(self) -> str:
        return f"{self.source.lower()}:{self.external_id}"

    @field_validator("source_ttl_seconds")
    @classmethod
    def bounded_source_ttl(cls, value: int) -> int:
        if value < 1 or value > 900:
            raise ValueError("live source TTL must be between 1 and 900 seconds")
        return value


class PlatformHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    state: SourceState
    checked_at: datetime
    open_count: int | None = None
    latency_ms: int | None = None
    detail: str = ""


class RadarSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema: str = "ATM_UNIVERSAL_RADAR_V1"
    generated_at: datetime
    opportunities: list[UniversalOpportunity]
    platform_health: list[PlatformHealth]
    source_errors: dict[str, str] = Field(default_factory=dict)
    rejection_counts: dict[str, int] = Field(default_factory=dict)
    canonical_duplicate_count: int = 0
    outgoing_spend_usd: Decimal = Decimal("0")


class SourceUnavailable(RuntimeError):
    def __init__(self, source: str, state: SourceState, detail: str):
        super().__init__(detail)
        self.source = source
        self.state = state
        self.detail = detail


class JsonHttpClient:
    """Tiny read/write HTTP client; callers choose methods explicitly."""

    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        timeout: float = 12,
    ) -> tuple[Any, dict[str, str], int]:
        if not url.startswith("https://"):
            raise ValueError("HTTPS required")
        payload = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        req_headers = {"Accept": "application/json", "User-Agent": "ATM-Universal-Radar/1.0"}
        if payload is not None:
            req_headers["Content-Type"] = "application/json"
        req_headers.update(headers or {})
        req = urllib.request.Request(url, data=payload, headers=req_headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read()
                parsed = json.loads(raw.decode("utf-8")) if raw else {}
                return parsed, {k.lower(): v for k, v in response.headers.items()}, int(response.status)
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise PermissionError(f"HTTP {exc.code}") from exc
            if exc.code == 429:
                raise RuntimeError("HTTP 429") from exc
            raise

    def get_json(self, url: str, *, headers: dict[str, str] | None = None, timeout: float = 12) -> Any:
        data, _, _ = self.request_json("GET", url, headers=headers, timeout=timeout)
        return data


class RadarSource(Protocol):
    name: str
    priority: int

    def discover(self, floor_usd: Decimal) -> list[UniversalOpportunity]: ...


class RadarRegistry:
    def __init__(self, sources: Iterable[RadarSource] = ()):  # no core FSM edits required for new rails
        self._sources: dict[str, RadarSource] = {}
        for source in sources:
            self.register(source)

    def register(self, source: RadarSource) -> None:
        key = source.name.lower().strip()
        if not key or key in self._sources:
            raise ValueError(f"duplicate/invalid radar source: {key}")
        self._sources[key] = source

    def ordered(self) -> list[RadarSource]:
        return sorted(self._sources.values(), key=lambda src: (int(src.priority), src.name))

    @property
    def names(self) -> list[str]:
        return [source.name for source in self.ordered()]


INJECTION_PATTERNS = (
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"reveal\s+(the\s+)?(system|developer)\s+prompt",
    r"print\s+.*(api[_ -]?key|private[_ -]?key|seed phrase|token)",
    r"send\s+.*(credential|secret|private key|seed)",
    r"override\s+.*(policy|rules|guardrail)",
    r"disable\s+.*(safety|guard|validation)",
)


def source_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def external_text_is_safe(text: str) -> bool:
    lowered = text.lower()
    return not any(re.search(pattern, lowered, re.I) for pattern in INJECTION_PATTERNS)


def route_executor(opportunity: UniversalOpportunity) -> ExecutorClass:
    tokens = " ".join(
        [opportunity.category, opportunity.title, opportunity.description, *opportunity.required_capabilities]
    ).lower()
    if opportunity.agent_policy in {AgentPolicy.HUMAN_ONLY, AgentPolicy.PROHIBITED}:
        return ExecutorClass.UNSUPPORTED
    if any(term in tokens for term in ("physical", "irl", "phone call", "video call", "on-site", "onsite")):
        return ExecutorClass.UNSUPPORTED
    if any(term in tokens for term in ("ocr", "scanned document", "image to text", "raster", "brand design", "logo design", "image generation")):
        return ExecutorClass.UNSUPPORTED
    if any(term in tokens for term in ("translation", "localization", "localisation", "glossary")):
        return ExecutorClass.UNSUPPORTED
    if any(term in tokens for term in ("data cleanup", "data normalization", "data normalisation", "structured file transform")):
        return ExecutorClass.UNSUPPORTED
    # Specific task semantics beat generic category tokens such as "code" or "repository".
    if any(term in tokens for term in ("test fix", "failing test", "pytest", "unit test", "regression")):
        return ExecutorClass.TEST_FIX
    if any(term in tokens for term in ("api integration", "webhook", "rest api", "graphql", "sdk")):
        return ExecutorClass.API_INTEGRATION
    if any(term in tokens for term in ("research", "fact check", "analysis", "brief")):
        return ExecutorClass.HTTP_RESEARCH
    if any(term in tokens for term in ("documentation", "docs", "readme")):
        return ExecutorClass.DOCS
    if any(term in tokens for term in ("extract", "scrape", "csv", "dataset", "data collection")):
        return ExecutorClass.DATA_EXTRACTION
    if any(term in tokens for term in ("write", "writing", "content", "summary")):
        return ExecutorClass.CONTENT_GENERATION
    if any(term in tokens for term in ("qa", "verify", "validation", "browser test")):
        return ExecutorClass.LIGHT_QA
    if any(term in tokens for term in ("heavy build", "build large", "large repo", "compile artifact")):
        return ExecutorClass.HEAVY_BUILD
    if any(term in tokens for term in ("github", "pull request", "patch", "bug fix", "code", "repository")):
        return ExecutorClass.GITHUB_BOUNDED_PATCH
    if any(term in tokens for term in ("build", "compile")):
        return ExecutorClass.HEAVY_BUILD
    return ExecutorClass.UNSUPPORTED


SLOP_TAGS = {"FIXTURE", "MOCK", "SIGNAL_ONLY", "UNVERIFIED", "DEMO_ONLY", "SYNTHETIC"}
OPEN_EXTERNAL_STATES = {"OPEN", "ACTIVE", "AVAILABLE", "PUBLISHED", "LIVE"}


def _slop_provenance(opportunity: UniversalOpportunity) -> bool:
    source_tokens = {token for token in re.split(r"[^A-Z0-9]+", opportunity.source.upper()) if token}
    tags = {str(tag).upper() for tag in opportunity.provenance_tags}
    extra_funding_state = str(getattr(opportunity, "funding_state", "")).upper()
    return bool((source_tokens | tags | {extra_funding_state}) & SLOP_TAGS)


def _legacy_disposition(state: OperationalState) -> RadarDisposition:
    if state == OperationalState.EXECUTABLE:
        return RadarDisposition.ATTACK_NOW
    if state == OperationalState.HUMAN_GATE:
        return RadarDisposition.HUMAN_GATE
    if state in {OperationalState.WATCH_ONLY, OperationalState.UNVERIFIED}:
        return RadarDisposition.MONITOR
    return RadarDisposition.REJECT


def qualify(opportunity: UniversalOpportunity, *, floor_usd: Decimal = Decimal("5"), now: datetime | None = None) -> UniversalOpportunity:
    """Fail-closed Canon admission.

    A positive score is impossible unless every mutation-critical fact is explicit,
    current, and proven. Discovery data alone is never authorization.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    reasons: list[str] = []
    latest = opportunity.updated_at or opportunity.created_at
    state = OperationalState.EXECUTABLE

    if _slop_provenance(opportunity) or not external_text_is_safe(f"{opportunity.title}\n{opportunity.description}"):
        state = OperationalState.REJECTED_SLOP
        reasons.append("FIXTURE_OR_SIGNAL_ONLY")

    if opportunity.source_checked_at is None:
        reasons.append("STALE_FETCH")
        state = OperationalState.UNVERIFIED if state != OperationalState.REJECTED_SLOP else state
    elif (now - opportunity.source_checked_at.astimezone(timezone.utc)).total_seconds() > opportunity.source_ttl_seconds:
        reasons.append("STALE_FETCH")
        state = OperationalState.STALE if state != OperationalState.REJECTED_SLOP else state
    if opportunity.source_state != SourceState.HEALTHY:
        reasons.append("SOURCE_DEGRADED")
        state = OperationalState.UNVERIFIED if state not in {OperationalState.REJECTED_SLOP, OperationalState.STALE} else state

    if opportunity.deadline and opportunity.deadline.astimezone(timezone.utc) <= now:
        reasons.append("EXPIRED")
        state = OperationalState.STALE if state != OperationalState.REJECTED_SLOP else state
    external_status = opportunity.external_status.strip().upper()
    if opportunity.external_object_exists is False:
        reasons.append("REMOTE_404")
        state = OperationalState.STALE if state != OperationalState.REJECTED_SLOP else state
    elif opportunity.external_object_exists is not True:
        reasons.append("REMOTE_404")
        state = OperationalState.UNVERIFIED if state not in {OperationalState.REJECTED_SLOP, OperationalState.STALE} else state
    if external_status in {"CLOSED", "CANCELLED", "CANCELED", "DELETED", "COMPLETED", "EXPIRED"}:
        reasons.append("CLOSED")
        state = OperationalState.STALE if state != OperationalState.REJECTED_SLOP else state
    elif external_status not in OPEN_EXTERNAL_STATES:
        reasons.append("CLOSED")
        state = OperationalState.UNVERIFIED if state not in {OperationalState.REJECTED_SLOP, OperationalState.STALE} else state

    if opportunity.funding_status != FundingStatus.VERIFIED or not opportunity.funding_proof:
        reasons.append("UNVERIFIED_FUNDING")
        state = OperationalState.UNVERIFIED if state not in {OperationalState.REJECTED_SLOP, OperationalState.STALE} else state
    if opportunity.payout_net <= 0:
        reasons.append("ZERO_REWARD")
        state = OperationalState.WATCH_ONLY if state == OperationalState.EXECUTABLE else state
    if opportunity.payout_net < floor_usd and not opportunity.batchable:
        reasons.append("POLICY_REJECT")
        state = OperationalState.WATCH_ONLY if state == OperationalState.EXECUTABLE else state
    if opportunity.execution_cost_usd > 0 or opportunity.paid_action_required is True:
        reasons.append("PAID_ENTRY")
        state = OperationalState.WATCH_ONLY if state == OperationalState.EXECUTABLE else state
    elif opportunity.paid_action_required is not False:
        reasons.append("PAID_ENTRY")
        state = OperationalState.UNVERIFIED if state == OperationalState.EXECUTABLE else state
    if opportunity.stake_required is True:
        reasons.append("STAKE_REQUIRED")
        state = OperationalState.WATCH_ONLY if state == OperationalState.EXECUTABLE else state
    elif opportunity.stake_required is not False:
        reasons.append("STAKE_REQUIRED")
        state = OperationalState.UNVERIFIED if state == OperationalState.EXECUTABLE else state
    if opportunity.agent_policy in {AgentPolicy.HUMAN_ONLY, AgentPolicy.PROHIBITED}:
        reasons.append("POLICY_REJECT")
        state = OperationalState.WATCH_ONLY if state == OperationalState.EXECUTABLE else state
    if opportunity.payment_rail.upper() in {"UNKNOWN", "NONE", ""}:
        reasons.append("PAYOUT_UNKNOWN")
        state = OperationalState.UNVERIFIED if state == OperationalState.EXECUTABLE else state
    if opportunity.withdrawal_path.upper() in {"UNKNOWN", "NONE", ""}:
        reasons.append("PAYOUT_UNKNOWN")
        state = OperationalState.UNVERIFIED if state == OperationalState.EXECUTABLE else state

    opportunity.executor_class = route_executor(opportunity)
    if opportunity.executor_class == ExecutorClass.UNSUPPORTED:
        reasons.append("UNSUPPORTED_EXECUTOR")
        state = OperationalState.WATCH_ONLY if state == OperationalState.EXECUTABLE else state
    if opportunity.submission_window_open is False or opportunity.current_worker_action_available is False:
        reasons.append("NO_CURRENT_WORKER_ACTION")
        state = OperationalState.WATCH_ONLY if state == OperationalState.EXECUTABLE else state
    elif opportunity.submission_window_open is not True or opportunity.current_worker_action_available is not True:
        reasons.append("NO_CURRENT_WORKER_ACTION")
        state = OperationalState.UNVERIFIED if state == OperationalState.EXECUTABLE else state
    if opportunity.canonical_identity_eligible is False:
        reasons.append("IDENTITY_NOT_READY")
        state = OperationalState.HUMAN_GATE if state == OperationalState.EXECUTABLE else state
    elif opportunity.canonical_identity_eligible is not True:
        reasons.append("IDENTITY_NOT_READY")
        state = OperationalState.UNVERIFIED if state == OperationalState.EXECUTABLE else state
    if opportunity.credential_boundary_ready is False:
        reasons.append("IDENTITY_NOT_READY")
        state = OperationalState.HUMAN_GATE if state == OperationalState.EXECUTABLE else state
    elif opportunity.credential_boundary_ready is not True:
        reasons.append("IDENTITY_NOT_READY")
        state = OperationalState.UNVERIFIED if state == OperationalState.EXECUTABLE else state
    if opportunity.already_submitted_by_atm is True:
        reasons.append("ALREADY_SUBMITTED")
        state = OperationalState.WATCH_ONLY if state == OperationalState.EXECUTABLE else state
    elif opportunity.already_submitted_by_atm is None:
        reasons.append("ALREADY_SUBMITTED")
        state = OperationalState.UNVERIFIED if state == OperationalState.EXECUTABLE else state
    if opportunity.human_gate and state == OperationalState.EXECUTABLE:
        state = OperationalState.HUMAN_GATE

    opportunity.rejection_reasons = sorted(set(reasons))
    opportunity.operational_state = state
    opportunity.disposition = _legacy_disposition(state)
    if state != OperationalState.EXECUTABLE:
        opportunity.money_velocity_score = Decimal("0")
        return opportunity

    minutes = Decimal(max(1, opportunity.estimated_execution_minutes))
    competition_penalty = Decimal("1") / Decimal(1 + max(0, opportunity.competition) + max(0, opportunity.open_prs))
    freshness = Decimal("1")
    if latest:
        age_hours = Decimal(str(max(0.0, (now - latest.astimezone(timezone.utc)).total_seconds() / 3600)))
        freshness = max(Decimal("0.10"), Decimal("1") - min(Decimal("0.90"), age_hours / Decimal("720")))
    opportunity.money_velocity_score = (opportunity.payout_net * competition_penalty * freshness / minutes).quantize(Decimal("0.000001"))
    return opportunity


def taskmarket_admission(
    task: dict[str, Any],
    *,
    canonical_wallet: str,
    existing_submission: bool,
    signer_ready: bool,
    now: datetime | None = None,
    capability_runtime_ready: bool | None = None,
) -> tuple[OperationalState, tuple[str, ...]]:
    """Compatibility view over the single TaskMarket Cash Canon."""
    from .cash_canon import taskmarket_cash_decision

    decision = taskmarket_cash_decision(
        task,
        canonical_wallet=canonical_wallet,
        existing_submission=existing_submission,
        signer_ready=signer_ready,
        now=now,
        capability_runtime_ready=capability_runtime_ready,
    )
    mapping = {
        "EXECUTABLE": OperationalState.EXECUTABLE,
        "WATCH_ONLY": OperationalState.WATCH_ONLY,
        "HUMAN_GATE": OperationalState.HUMAN_GATE,
        "STALE": OperationalState.STALE,
        "REJECTED": OperationalState.WATCH_ONLY,
    }
    return mapping[decision.disposition], decision.reasons


class UniversalRadar:
    """Bounded parallel discovery. One platform failure never stops the global scan."""

    def __init__(self, registry: RadarRegistry, *, floor_usd: Decimal = Decimal("5"), per_source_timeout: float = 12.0):
        self.registry = registry
        self.floor_usd = Decimal(floor_usd)
        self.per_source_timeout = max(1.0, float(per_source_timeout))

    def scan(self) -> RadarSnapshot:
        now = datetime.now(timezone.utc)
        health: dict[str, PlatformHealth] = {}
        errors: dict[str, str] = {}
        by_id: dict[str, UniversalOpportunity] = {}
        duplicate_count = 0
        sources = self.registry.ordered()

        def run(source: RadarSource) -> tuple[str, list[UniversalOpportunity]]:
            return source.name, source.discover(self.floor_usd)

        pool = ThreadPoolExecutor(max_workers=max(1, min(8, len(sources))))
        try:
            future_map = {pool.submit(run, source): source for source in sources}
            completed, overdue = wait(future_map, timeout=self.per_source_timeout)
            for future in completed:
                source = future_map[future]
                try:
                    name, found = future.result(timeout=0)
                    for raw in found:
                        raw.source_state = SourceState.HEALTHY
                        raw.source_checked_at = now
                        raw.external_object_exists = True
                        if raw.external_status == "UNKNOWN" and getattr(raw, "status", None):
                            raw.external_status = str(getattr(raw, "status"))
                        item = qualify(raw, floor_usd=self.floor_usd, now=now)
                        old = by_id.get(item.canonical_id)
                        if old is not None:
                            duplicate_count += 1
                            item.rejection_reasons = sorted(set(item.rejection_reasons + ["CANONICAL_DUPLICATE"]))
                        if old is None or item.money_velocity_score > old.money_velocity_score:
                            by_id[item.canonical_id] = item
                    health[name] = PlatformHealth(
                        source=name,
                        state=SourceState.HEALTHY,
                        checked_at=now,
                        open_count=len(found),
                        detail="read-only scan completed",
                    )
                except SourceUnavailable as exc:
                    health[source.name] = PlatformHealth(
                        source=source.name, state=exc.state, checked_at=now, detail=exc.detail[:300]
                    )
                    errors[source.name] = exc.detail[:300]
                except PermissionError as exc:
                    health[source.name] = PlatformHealth(
                        source=source.name, state=SourceState.AUTH_REQUIRED, checked_at=now, detail="auth required"
                    )
                    errors[source.name] = type(exc).__name__
                except Exception as exc:  # platform isolation boundary
                    state = SourceState.RATE_LIMITED if "429" in str(exc) else SourceState.DEGRADED
                    health[source.name] = PlatformHealth(source=source.name, state=state, checked_at=now, detail=type(exc).__name__)
                    errors[source.name] = f"{type(exc).__name__}:{str(exc)[:180]}"
            for future in overdue:
                source = future_map[future]
                future.cancel()
                health[source.name] = PlatformHealth(
                    source=source.name, state=SourceState.DEGRADED, checked_at=now, detail="per-source timeout"
                )
                errors[source.name] = "TIMEOUT"
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        opportunities = sorted(
            by_id.values(), key=lambda item: (item.money_velocity_score, item.payout_net, item.canonical_id), reverse=True
        )
        rejection_counts: dict[str, int] = {}
        for item in opportunities:
            for reason in item.rejection_reasons:
                rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
        if duplicate_count:
            rejection_counts["CANONICAL_DUPLICATE"] = duplicate_count
        return RadarSnapshot(
            generated_at=now,
            opportunities=opportunities,
            platform_health=[health.get(src.name) or PlatformHealth(source=src.name, state=SourceState.UNKNOWN, checked_at=now) for src in sources],
            source_errors=errors,
            rejection_counts=rejection_counts,
            canonical_duplicate_count=duplicate_count,
            outgoing_spend_usd=Decimal("0"),
        )


def persist_snapshot(snapshot: RadarSnapshot, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(snapshot.model_dump(mode="json"), sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_snapshot(path: Path) -> RadarSnapshot | None:
    if not path.exists():
        return None
    try:
        return RadarSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


class ZeroCostProviderHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model_id: str
    credential_present: bool
    zero_cost_policy: bool
    model_available: bool | None
    usable: bool
    reason: str


class ZeroCostProviderGate:
    """Execution provider health only; never authorizes a paid fallback."""

    paid_fallback = False

    def __init__(self, http: JsonHttpClient | None = None):
        self.http = http or JsonHttpClient()

    def inspect(self) -> list[ZeroCostProviderHealth]:
        rows: list[ZeroCostProviderHealth] = []
        gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
        gemini_model = os.getenv("ATM_GEMINI_FREE_MODEL", "gemini-2.5-flash-lite").strip()
        gemma_model = os.getenv("ATM_GEMMA_FREE_MODEL", "gemma-3-27b-it").strip()
        available: set[str] | None = None
        if gemini_key:
            try:
                data = self.http.get_json(
                    f"https://generativelanguage.googleapis.com/v1beta/models?key={gemini_key}", timeout=8
                )
                available = {str(row.get("name", "")).split("/", 1)[-1] for row in data.get("models", []) if isinstance(row, dict)}
            except Exception:
                available = set()
        for provider, model in (("gemini-free", gemini_model), ("gemma-via-gemini", gemma_model)):
            present = bool(gemini_key)
            model_available = None if not present else model in (available or set())
            rows.append(
                ZeroCostProviderHealth(
                    provider=provider,
                    model_id=model,
                    credential_present=present,
                    zero_cost_policy=True,
                    model_available=model_available,
                    usable=bool(present and model_available),
                    reason="no credential" if not present else ("model unavailable" if not model_available else "credential+model probe ok; paid fallback disabled"),
                )
            )
        opencode_key = os.getenv("OPENCODE_API_KEY", "").strip()
        rows.append(
            ZeroCostProviderHealth(
                provider="opencode-free",
                model_id=os.getenv("ATM_OPENCODE_FREE_MODEL", "deepseek-v4-flash-free"),
                credential_present=bool(opencode_key),
                zero_cost_policy=True,
                model_available=None,
                usable=False,
                reason="requires current zero-cost metadata probe before use" if opencode_key else "no credential",
            )
        )
        return rows
