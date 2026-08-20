from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


AUTONOMY_SCHEMA = "ATM_AUTONOMY_FABRIC_V1"
GAP_LEDGER_SCHEMA = "ATM_CAPABILITY_GAP_LEDGER_V1"
PROMOTION_SCHEMA = "ATM_DETERMINISTIC_PROMOTION_V1"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None = None) -> str:
    return (value or utcnow()).astimezone(timezone.utc).isoformat()


class WorkerRole(str, Enum):
    SCOUT = "SCOUT"
    FALSIFIER = "FALSIFIER"
    DOCTOR = "DOCTOR"
    SKILL_FORGE = "SKILL_FORGE"
    WORKER = "WORKER"
    CHECKER = "CHECKER"


class EventKind(str, Enum):
    DISCOVERY_TICK = "DISCOVERY_TICK"
    NEW_CANDIDATE = "NEW_CANDIDATE"
    CAPABILITY_GAP_OBSERVED = "CAPABILITY_GAP_OBSERVED"
    PROVIDER_429 = "PROVIDER_429"
    PROVIDER_EXHAUSTED = "PROVIDER_EXHAUSTED"
    SOURCE_TIMEOUT = "SOURCE_TIMEOUT"
    SOURCE_LIED = "SOURCE_LIED"
    WORKER_CRASH = "WORKER_CRASH"
    CHECK_FAILED = "CHECK_FAILED"
    FORGE_CRASH = "FORGE_CRASH"
    HEARTBEAT_FAULT = "HEARTBEAT_FAULT"
    SUBMISSION_STATE_CHANGED = "SUBMISSION_STATE_CHANGED"
    PAYMENT_STATE_CHANGED = "PAYMENT_STATE_CHANGED"


class ProviderState(str, Enum):
    HEALTHY = "HEALTHY"
    COOLDOWN = "COOLDOWN"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    UNVERIFIED = "UNVERIFIED"
    DISABLED = "DISABLED"


class DemandClass(str, Enum):
    VERIFIED_MISSED_CASH = "VERIFIED_MISSED_CASH"
    CURRENT_UNVERIFIED_DEMAND_SIGNAL = "CURRENT_UNVERIFIED_DEMAND_SIGNAL"
    STALE_SIGNAL = "STALE_SIGNAL"
    SYNTHETIC_FIXTURE = "SYNTHETIC_FIXTURE"


@dataclass(frozen=True)
class WorkerSpec:
    role: WorkerRole
    tool_allowlist: tuple[str, ...]
    network_policy: str
    mutation_authority: bool = False
    max_runtime_seconds: int = 120


ROLE_SPECS: dict[WorkerRole, WorkerSpec] = {
    WorkerRole.SCOUT: WorkerSpec(WorkerRole.SCOUT, ("http_read", "github_read", "browser_read"), "READ_ONLY_ALLOWLIST", False, 90),
    WorkerRole.FALSIFIER: WorkerSpec(WorkerRole.FALSIFIER, ("http_read", "github_read", "chain_read"), "READ_ONLY_ALLOWLIST", False, 60),
    WorkerRole.DOCTOR: WorkerSpec(WorkerRole.DOCTOR, ("telemetry_read", "checkpoint_read", "bounded_retry"), "CONTROL_PLANE_ONLY", False, 60),
    WorkerRole.SKILL_FORGE: WorkerSpec(WorkerRole.SKILL_FORGE, ("sandbox_worker_dispatch", "fixture_evidence_read", "package_metadata_read"), "SECRETLESS_SANDBOX_ONLY", False, 600),
    WorkerRole.WORKER: WorkerSpec(WorkerRole.WORKER, ("workspace", "capability_tools"), "JOB_ALLOWLIST", False, 900),
    WorkerRole.CHECKER: WorkerSpec(WorkerRole.CHECKER, ("artifact_read", "test_runner"), "DENY_BY_DEFAULT", False, 300),
}


EVENT_ROLES: dict[EventKind, tuple[WorkerRole, ...]] = {
    EventKind.DISCOVERY_TICK: (WorkerRole.SCOUT,),
    EventKind.NEW_CANDIDATE: (WorkerRole.FALSIFIER,),
    EventKind.CAPABILITY_GAP_OBSERVED: (WorkerRole.SKILL_FORGE,),
    EventKind.PROVIDER_429: (WorkerRole.DOCTOR,),
    EventKind.PROVIDER_EXHAUSTED: (WorkerRole.DOCTOR,),
    EventKind.SOURCE_TIMEOUT: (WorkerRole.DOCTOR,),
    EventKind.SOURCE_LIED: (WorkerRole.FALSIFIER, WorkerRole.DOCTOR),
    EventKind.WORKER_CRASH: (WorkerRole.DOCTOR,),
    EventKind.CHECK_FAILED: (WorkerRole.DOCTOR,),
    EventKind.FORGE_CRASH: (WorkerRole.DOCTOR,),
    EventKind.HEARTBEAT_FAULT: (WorkerRole.DOCTOR,),
    EventKind.SUBMISSION_STATE_CHANGED: (WorkerRole.CHECKER,),
    EventKind.PAYMENT_STATE_CHANGED: (WorkerRole.CHECKER,),
}


@dataclass(frozen=True)
class EphemeralJob:
    event: EventKind
    role: WorkerRole
    created_at: str
    spec: WorkerSpec
    payload: dict[str, Any]


class DeterministicSupervisor:
    """Typed event router only. It cannot become economic authority or mutate externally."""

    schema = AUTONOMY_SCHEMA
    single_controller = True
    cash_canon_authority = False
    effect_authority = False

    def dispatch(self, event: EventKind | str, payload: Mapping[str, Any] | None = None) -> tuple[EphemeralJob, ...]:
        kind = event if isinstance(event, EventKind) else EventKind(str(event))
        clean = dict(payload or {})
        jobs = []
        for role in EVENT_ROLES[kind]:
            spec = ROLE_SPECS[role]
            if spec.mutation_authority:
                raise RuntimeError("AUTONOMY_ROLE_MUST_NOT_HAVE_EXTERNAL_MUTATION_AUTHORITY")
            jobs.append(EphemeralJob(kind, role, iso(), spec, clean))
        return tuple(jobs)


@dataclass(frozen=True)
class ScoutResult:
    source: str
    observed_at: str
    candidates: tuple[Any, ...]
    state: str
    error_class: str | None = None


class ScoutFabric:
    """Bounded multi-source read-only discovery with failure isolation and provenance."""

    def __init__(self, max_concurrent: int = 3):
        self.max_concurrent = max(1, min(3, int(max_concurrent)))

    def scan(self, adapters: Mapping[str, Any], discovery_order: Iterable[str], minimum: Any) -> tuple[ScoutResult, ...]:
        selected = [(name, adapters[name]) for name in discovery_order if name in adapters][: self.max_concurrent]
        if not selected:
            return ()

        def one(name: str, adapter: Any) -> ScoutResult:
            observed = iso()
            try:
                return ScoutResult(name, observed, tuple(adapter.discover(minimum)), "OK")
            except Exception as exc:
                return ScoutResult(name, observed, (), "DEGRADED", type(exc).__name__)

        out: list[ScoutResult] = []
        with ThreadPoolExecutor(max_workers=len(selected)) as pool:
            futures = [pool.submit(one, name, adapter) for name, adapter in selected]
            for future in as_completed(futures):
                out.append(future.result())
        out.sort(key=lambda row: row.source)
        return tuple(out)


@dataclass(frozen=True)
class FalsifierDecision:
    verdict: str
    reason: str | None
    verified_at: str
    fresh_object_hash: str | None = None


class Falsifier:
    """Authoritative final read before maker/effect allocation. Recommendation only."""

    def verify(self, opportunity: Any, adapter: Any, *, submitted_ids: Iterable[str] = ()) -> FalsifierDecision:
        cid = str(getattr(opportunity, "canonical_opportunity_id", "") or "")
        if cid and cid in {str(x) for x in submitted_ids}:
            return FalsifierDecision("KILL", "ALREADY_SUBMITTED", iso())
        status = str(getattr(opportunity, "upstream_status", "") or "").upper()
        if status not in {"OPEN", "ACTIVE", "AVAILABLE", "FUNDED"}:
            return FalsifierDecision("KILL", "STALE_OR_CLOSED", iso())
        if not getattr(opportunity, "funding_proof", None):
            return FalsifierDecision("KILL", "UNFUNDED", iso())
        try:
            snapshot = adapter.fetch_authoritative(opportunity)
            adapter.verify_freshness(opportunity, snapshot)
            adapter.verify_funding(opportunity, snapshot)
            adapter.verify_eligibility(opportunity, snapshot)
        except Exception as exc:
            return FalsifierDecision("KILL", type(exc).__name__, iso())
        digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, default=str).encode()).hexdigest()
        return FalsifierDecision("CONFIRM", None, iso(), digest)


@dataclass
class RecoveryCircuit:
    failures: int = 0
    opened_until: datetime | None = None
    quarantined: bool = False
    last_error: str | None = None


class Doctor:
    """Closed recovery catalogue. It never changes SLO/canon/effect truth to make RED disappear."""

    ALLOWED = {
        "RETRY_EPHEMERAL_WORKER",
        "REFRESH_STALE_STATE",
        "REOPEN_READ_ONLY_SOURCE_AFTER_BACKOFF",
        "QUARANTINE_BAD_SOURCE",
        "ROTATE_TO_VERIFIED_FREE_PROVIDER",
        "RESTORE_CHECKPOINT",
        "RESUME_NONCOMMITTED_JOB",
        "RECOVER_COMMITTED_EFFECT_FROM_EXTERNAL_RECEIPT",
        "REDISPATCH_CRASHED_SECRETLESS_WORKER",
        "REVERIFY_EXACT_HEAD",
    }
    FORBIDDEN = {
        "CHANGE_HEALTH_SLO_TO_HIDE_FAILURE",
        "DISABLE_CANON",
        "DISABLE_EFFECT_BOUNDARY",
        "DECLARE_GREEN_BY_LLM",
        "CREATE_WALLET",
        "CREATE_MARKETPLACE_IDENTITY",
        "SPEND_MONEY",
        "USE_PAID_PROVIDER",
        "EXPOSE_SECRETS",
        "BYPASS_PLATFORM_SECURITY",
        "ARBITRARY_SELF_PATCH_AND_DEPLOY",
    }

    def __init__(self, *, threshold: int = 3, cooldown_seconds: int = 300):
        self.threshold = max(1, int(threshold))
        self.cooldown_seconds = max(1, int(cooldown_seconds))
        self._lock = threading.RLock()
        self._circuits: dict[str, RecoveryCircuit] = {}

    def choose(self, fault: str) -> str:
        mapping = {
            "PROVIDER_429": "ROTATE_TO_VERIFIED_FREE_PROVIDER",
            "PROVIDER_EXHAUSTED": "ROTATE_TO_VERIFIED_FREE_PROVIDER",
            "SOURCE_TIMEOUT": "REOPEN_READ_ONLY_SOURCE_AFTER_BACKOFF",
            "SOURCE_LIED": "QUARANTINE_BAD_SOURCE",
            "WORKER_CRASH": "REDISPATCH_CRASHED_SECRETLESS_WORKER",
            "CHECK_FAILED": "RESUME_NONCOMMITTED_JOB",
            "FORGE_CRASH": "RESTORE_CHECKPOINT",
            "HEARTBEAT_FAULT": "REVERIFY_EXACT_HEAD",
            "STALE_STATE": "REFRESH_STALE_STATE",
            "EFFECT_RECEIPT_RECOVERY": "RECOVER_COMMITTED_EFFECT_FROM_EXTERNAL_RECEIPT",
        }
        return mapping.get(str(fault).upper(), "RETRY_EPHEMERAL_WORKER")

    def record_failure(self, key: str, error_class: str) -> dict[str, Any]:
        now = utcnow()
        with self._lock:
            row = self._circuits.setdefault(key, RecoveryCircuit())
            row.failures += 1
            row.last_error = error_class
            if row.failures >= self.threshold:
                row.opened_until = now + timedelta(seconds=self.cooldown_seconds)
            return self.status(key)

    def quarantine(self, key: str, error_class: str = "SOURCE_LIED") -> dict[str, Any]:
        with self._lock:
            row = self._circuits.setdefault(key, RecoveryCircuit())
            row.quarantined = True
            row.last_error = error_class
            return self.status(key)

    def available(self, key: str) -> bool:
        row = self._circuits.get(key)
        if not row:
            return True
        if row.quarantined:
            return False
        if row.opened_until and row.opened_until > utcnow():
            return False
        return True

    def status(self, key: str) -> dict[str, Any]:
        row = self._circuits.get(key, RecoveryCircuit())
        return {
            "failures": row.failures,
            "opened_until": row.opened_until.isoformat() if row.opened_until else None,
            "quarantined": row.quarantined,
            "last_error": row.last_error,
            "available": self.available(key),
        }

    def recover(self, fault: str, action: Callable[[], Any], *, attempts: int = 2, backoff_seconds: float = 0.0) -> dict[str, Any]:
        recovery = self.choose(fault)
        if recovery not in self.ALLOWED or recovery in self.FORBIDDEN:
            raise RuntimeError("DOCTOR_RECOVERY_NOT_ALLOWED")
        last: str | None = None
        for attempt in range(max(1, min(3, int(attempts)))):
            try:
                return {"state": "RECOVERED", "recovery": recovery, "attempt": attempt + 1, "result": action()}
            except Exception as exc:
                last = type(exc).__name__
                if backoff_seconds > 0 and attempt + 1 < attempts:
                    time.sleep(min(1.0, backoff_seconds * (2**attempt)))
        return {"state": "FAULT_ESCALATED", "recovery": recovery, "error_class": last}


@dataclass(frozen=True)
class CapabilityGap:
    canonical_opportunity_id: str
    source: str
    external_id: str
    observed_at: str
    fresh_object_hash: str
    work_class_candidate: str
    requirements: tuple[str, ...]
    net_reward_if_verified: str | None
    funding_verification_state: str
    competition_if_known: int | None
    rejection_reason: str
    capability_gap_reason: str
    evidence_refs: tuple[str, ...]
    currentness_state: str
    demand_class: DemandClass
    synthetic: bool = False

    def fingerprint(self) -> str:
        payload = "|".join((self.canonical_opportunity_id, self.fresh_object_hash, self.rejection_reason, self.capability_gap_reason))
        return hashlib.sha256(payload.encode()).hexdigest()


class CapabilityGapLedger:
    """Demand evidence only. This ledger has no payment/earnings authority."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS capability_gaps(
              fingerprint TEXT PRIMARY KEY,
              canonical_id TEXT NOT NULL,
              source TEXT NOT NULL,
              work_class TEXT NOT NULL,
              observed_at TEXT NOT NULL,
              fresh_object_hash TEXT NOT NULL,
              reward_text TEXT,
              funding_state TEXT NOT NULL,
              competition INTEGER,
              rejection_reason TEXT NOT NULL,
              gap_reason TEXT NOT NULL,
              demand_class TEXT NOT NULL,
              synthetic INTEGER NOT NULL,
              payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_capability_gaps_work_class ON capability_gaps(work_class, observed_at);
            """
        )

    def close(self) -> None:
        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self._conn.close()

    def record(self, gap: CapabilityGap) -> bool:
        payload = asdict(gap)
        payload["demand_class"] = gap.demand_class.value
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO capability_gaps VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                gap.fingerprint(), gap.canonical_opportunity_id, gap.source, gap.work_class_candidate, gap.observed_at,
                gap.fresh_object_hash, gap.net_reward_if_verified, gap.funding_verification_state,
                gap.competition_if_known, gap.rejection_reason, gap.capability_gap_reason, gap.demand_class.value,
                1 if gap.synthetic else 0, json.dumps(payload, sort_keys=True, default=str),
            ),
        )
        return cur.rowcount == 1

    def rows(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._conn.execute("SELECT * FROM capability_gaps ORDER BY observed_at DESC")]

    @staticmethod
    def priority(row: Mapping[str, Any]) -> float:
        demand = str(row.get("demand_class") or "")
        base = {
            DemandClass.VERIFIED_MISSED_CASH.value: 6.0,
            DemandClass.CURRENT_UNVERIFIED_DEMAND_SIGNAL.value: 2.0,
            DemandClass.STALE_SIGNAL.value: -4.0,
            DemandClass.SYNTHETIC_FIXTURE.value: -8.0,
        }.get(demand, 0.0)
        if str(row.get("funding_state") or "").upper() == "VERIFIED":
            base += 3.0
        competition = row.get("competition")
        if competition is not None:
            base += 2.0 if int(competition) <= 2 else (-2.0 if int(competition) >= 8 else 0.0)
        if bool(row.get("synthetic")):
            base -= 10.0
        return base

    def prioritized(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.rows()
        rows.sort(key=lambda row: (self.priority(row), row.get("observed_at") or ""), reverse=True)
        return rows[: max(1, int(limit))]

    def stats(self) -> dict[str, Any]:
        total = int(self._conn.execute("SELECT COUNT(*) FROM capability_gaps").fetchone()[0])
        synthetic = int(self._conn.execute("SELECT COUNT(*) FROM capability_gaps WHERE synthetic=1").fetchone()[0])
        verified = int(self._conn.execute("SELECT COUNT(*) FROM capability_gaps WHERE demand_class=?", (DemandClass.VERIFIED_MISSED_CASH.value,)).fetchone()[0])
        return {"schema": GAP_LEDGER_SCHEMA, "total": total, "verified_missed_cash_signals": verified, "synthetic": synthetic, "earnings_authority": False}


@dataclass(frozen=True)
class PromotionEvidence:
    owner_cost_usd: str
    commercial_use_ok: bool
    supply_chain_pinned: bool
    no_secret_requirement: bool
    sandbox_pass: bool
    happy_pass: bool
    edge_pass: bool
    adversarial_pass: bool
    checker_independent_enough: bool
    artifact_contract_pass: bool
    resource_limit_pass: bool
    no_existing_capability_duplicate: bool
    license_verified: bool
    tool_contract_explicit: bool
    network_contract_explicit: bool


@dataclass(frozen=True)
class PromotionDecision:
    schema: str
    capability_id: str
    promoted: bool
    reasons: tuple[str, ...]
    decided_at: str


def deterministic_promotion(capability_id: str, evidence: PromotionEvidence, *, proposed_by: str = "SKILL_FORGE") -> PromotionDecision:
    checks = {
        "OWNER_COST_USD_ZERO": evidence.owner_cost_usd == "0",
        "COMMERCIAL_USE_OK": evidence.commercial_use_ok,
        "SUPPLY_CHAIN_PINNED": evidence.supply_chain_pinned,
        "NO_SECRET_REQUIREMENT": evidence.no_secret_requirement,
        "SANDBOX_PASS": evidence.sandbox_pass,
        "HAPPY_PASS": evidence.happy_pass,
        "EDGE_PASS": evidence.edge_pass,
        "ADVERSARIAL_PASS": evidence.adversarial_pass,
        "CHECKER_INDEPENDENT_ENOUGH": evidence.checker_independent_enough,
        "ARTIFACT_CONTRACT_PASS": evidence.artifact_contract_pass,
        "RESOURCE_LIMIT_PASS": evidence.resource_limit_pass,
        "NO_EXISTING_CAPABILITY_DUPLICATE": evidence.no_existing_capability_duplicate,
        "LICENSE_VERIFIED": evidence.license_verified,
        "TOOL_CONTRACT_EXPLICIT": evidence.tool_contract_explicit,
        "NETWORK_CONTRACT_EXPLICIT": evidence.network_contract_explicit,
    }
    failed = tuple(name for name, passed in checks.items() if not passed)
    return PromotionDecision(PROMOTION_SCHEMA, capability_id, not failed, failed, iso())


class SkillForge:
    """Compatibility surface intentionally disabled: candidate code may not run in the supervisor process."""

    def evaluate(self, *_args: Any, **_kwargs: Any) -> PromotionDecision:
        raise RuntimeError("IN_PROCESS_SKILL_FORGE_DISABLED_USE_SECRETLESS_SANDBOX_EVIDENCE")


@dataclass
class ProviderRuntime:
    provider_id: str
    state: ProviderState = ProviderState.UNVERIFIED
    quota_remaining: int | None = None
    failures: int = 0
    cooldown_until: datetime | None = None
    last_error: str | None = None


class FreeProviderFabric:
    """Zero-spend provider routing. No provider can silently cross into billing."""

    def __init__(self, verified_free_ids: Iterable[str], *, failure_threshold: int = 2, cooldown_seconds: int = 300):
        self.failure_threshold = max(1, int(failure_threshold))
        self.cooldown_seconds = max(1, int(cooldown_seconds))
        self.providers = {str(pid): ProviderRuntime(str(pid), ProviderState.HEALTHY) for pid in verified_free_ids}

    def register_unverified(self, provider_id: str) -> None:
        self.providers.setdefault(provider_id, ProviderRuntime(provider_id, ProviderState.UNVERIFIED))

    def record_success(self, provider_id: str, quota_remaining: int | None = None) -> None:
        row = self.providers[provider_id]
        row.failures = 0
        row.last_error = None
        row.cooldown_until = None
        row.quota_remaining = quota_remaining
        row.state = ProviderState.HEALTHY if quota_remaining != 0 else ProviderState.QUOTA_EXHAUSTED

    def record_failure(self, provider_id: str, error_class: str) -> None:
        row = self.providers[provider_id]
        row.failures += 1
        row.last_error = error_class
        if str(error_class).upper() in {"QUOTA_EXHAUSTED", "INSUFFICIENT_QUOTA"}:
            row.state = ProviderState.QUOTA_EXHAUSTED
            return
        if row.failures >= self.failure_threshold:
            row.state = ProviderState.COOLDOWN
            row.cooldown_until = utcnow() + timedelta(seconds=self.cooldown_seconds)

    def disable(self, provider_id: str) -> None:
        self.providers[provider_id].state = ProviderState.DISABLED

    def available(self) -> tuple[str, ...]:
        now = utcnow()
        good = []
        for pid, row in self.providers.items():
            if row.state == ProviderState.COOLDOWN and row.cooldown_until and row.cooldown_until <= now:
                row.state = ProviderState.HEALTHY
                row.cooldown_until = None
            if row.state == ProviderState.HEALTHY and row.quota_remaining != 0:
                good.append(pid)
        return tuple(sorted(good))

    def route(self) -> dict[str, Any]:
        good = self.available()
        if not good:
            return {"state": "DEGRADED_DETERMINISTIC_SEARCH", "provider": None, "paid_fallback": False}
        return {"state": "HEALTHY", "provider": good[0], "paid_fallback": False}

    def public_status(self) -> dict[str, Any]:
        return {
            "schema": "ATM_FREE_PROVIDER_FABRIC_V1",
            "paid_fallback": False,
            "route": self.route(),
            "providers": {
                pid: {
                    "state": row.state.value,
                    "failures": row.failures,
                    "quota_remaining": row.quota_remaining,
                    "cooldown_until": row.cooldown_until.isoformat() if row.cooldown_until else None,
                    "last_error": row.last_error,
                }
                for pid, row in sorted(self.providers.items())
            },
        }


def classify_gap(*, synthetic: bool, current: bool, funding_verified: bool) -> DemandClass:
    if synthetic:
        return DemandClass.SYNTHETIC_FIXTURE
    if not current:
        return DemandClass.STALE_SIGNAL
    if funding_verified:
        return DemandClass.VERIFIED_MISSED_CASH
    return DemandClass.CURRENT_UNVERIFIED_DEMAND_SIGNAL
