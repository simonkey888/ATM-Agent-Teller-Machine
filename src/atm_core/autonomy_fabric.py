from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping


AUTONOMY_SCHEMA = "ATM_AUTONOMY_FABRIC_V2"
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
    WorkerRole.SCOUT: WorkerSpec(WorkerRole.SCOUT, ("typed_read_only_source",), "ISOLATED_PROCESS_READ_ONLY_PUBLIC_HTTPS", False, 110),
    WorkerRole.FALSIFIER: WorkerSpec(WorkerRole.FALSIFIER, ("typed_authoritative_verify",), "ISOLATED_PROCESS_READ_ONLY_PUBLIC_HTTPS", False, 90),
    WorkerRole.DOCTOR: WorkerSpec(WorkerRole.DOCTOR, ("typed_durable_recovery",), "ISOLATED_PROCESS_NETWORK_DENY", False, 30),
    WorkerRole.SKILL_FORGE: WorkerSpec(WorkerRole.SKILL_FORGE, ("sandbox_receipt_read",), "SECRETLESS_SANDBOX_ONLY", False, 120),
    WorkerRole.WORKER: WorkerSpec(WorkerRole.WORKER, ("capability_tools",), "JOB_ALLOWLIST", False, 900),
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
    schema = AUTONOMY_SCHEMA
    single_controller = True
    cash_canon_authority = False
    effect_authority = False

    def dispatch(self, event: EventKind | str, payload: Mapping[str, Any] | None = None) -> tuple[EphemeralJob, ...]:
        kind = event if isinstance(event, EventKind) else EventKind(str(event))
        jobs = []
        for role in EVENT_ROLES[kind]:
            spec = ROLE_SPECS[role]
            if spec.mutation_authority:
                raise RuntimeError("AUTONOMY_ROLE_MUST_NOT_HAVE_EXTERNAL_MUTATION_AUTHORITY")
            jobs.append(EphemeralJob(kind, role, iso(), spec, dict(payload or {})))
        return tuple(jobs)


class ScoutFabric:
    """Removed compatibility surface. Runtime SCOUT is only atm_role_worker.py."""
    def __init__(self, max_concurrent: int = 3):
        self.max_concurrent = max(1, min(3, int(max_concurrent)))

    def scan(self, *args: Any, **kwargs: Any) -> tuple[Any, ...]:
        raise RuntimeError("IN_PROCESS_SCOUT_DISABLED_USE_ISOLATED_ROLE_RUNNER")


class Falsifier:
    """Removed compatibility surface. Runtime Falsifier is only atm_role_worker.py."""
    def verify(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("IN_PROCESS_FALSIFIER_DISABLED_USE_ISOLATED_ROLE_RUNNER")


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
    """Demand evidence only. It has no payment/earnings authority."""
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.executescript(
            """CREATE TABLE IF NOT EXISTS capability_gaps(
              fingerprint TEXT PRIMARY KEY, canonical_id TEXT NOT NULL, source TEXT NOT NULL,
              work_class TEXT NOT NULL, observed_at TEXT NOT NULL, fresh_object_hash TEXT NOT NULL,
              reward_text TEXT, funding_state TEXT NOT NULL, competition INTEGER,
              rejection_reason TEXT NOT NULL, gap_reason TEXT NOT NULL, demand_class TEXT NOT NULL,
              synthetic INTEGER NOT NULL, payload_json TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_capability_gaps_work_class ON capability_gaps(work_class,observed_at);"""
        )

    def close(self) -> None:
        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self._conn.close()

    def record(self, gap: CapabilityGap) -> bool:
        payload = asdict(gap)
        payload["demand_class"] = gap.demand_class.value
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO capability_gaps VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (gap.fingerprint(), gap.canonical_opportunity_id, gap.source, gap.work_class_candidate, gap.observed_at,
             gap.fresh_object_hash, gap.net_reward_if_verified, gap.funding_verification_state, gap.competition_if_known,
             gap.rejection_reason, gap.capability_gap_reason, gap.demand_class.value, int(gap.synthetic), json.dumps(payload, sort_keys=True)),
        )
        return cur.rowcount == 1

    @staticmethod
    def priority(row: Mapping[str, Any]) -> tuple[int, int, str]:
        demand = str(row.get("demand_class") or "")
        rank = {DemandClass.VERIFIED_MISSED_CASH.value: 0, DemandClass.CURRENT_UNVERIFIED_DEMAND_SIGNAL.value: 1, DemandClass.STALE_SIGNAL.value: 2, DemandClass.SYNTHETIC_FIXTURE.value: 3}.get(demand, 4)
        competition = int(row.get("competition") or 999999)
        return rank, competition, str(row.get("observed_at") or "")

    def prioritized(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = [dict(row) for row in self._conn.execute("SELECT * FROM capability_gaps").fetchall()]
        rows.sort(key=self.priority)
        return rows[:max(1, min(100, int(limit)))]

    def stats(self) -> dict[str, Any]:
        total = int(self._conn.execute("SELECT COUNT(*) FROM capability_gaps").fetchone()[0])
        missed = int(self._conn.execute("SELECT COUNT(*) FROM capability_gaps WHERE demand_class=? AND synthetic=0", (DemandClass.VERIFIED_MISSED_CASH.value,)).fetchone()[0])
        synthetic = int(self._conn.execute("SELECT COUNT(*) FROM capability_gaps WHERE synthetic=1").fetchone()[0])
        return {"schema": GAP_LEDGER_SCHEMA, "total": total, "verified_missed_cash_signals": missed, "synthetic": synthetic, "earnings_authority": False}


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
    capability_id: str
    promoted: bool
    reasons: tuple[str, ...]
    decided_at: str
    schema: str = PROMOTION_SCHEMA


def deterministic_promotion(capability_id: str, evidence: PromotionEvidence) -> PromotionDecision:
    checks = {
        "ZERO_SPEND": str(evidence.owner_cost_usd) == "0", "COMMERCIAL_USE_OK": evidence.commercial_use_ok,
        "SUPPLY_CHAIN_PINNED": evidence.supply_chain_pinned, "NO_SECRET_REQUIREMENT": evidence.no_secret_requirement,
        "SANDBOX_PASS": evidence.sandbox_pass, "HAPPY_PASS": evidence.happy_pass, "EDGE_PASS": evidence.edge_pass,
        "ADVERSARIAL_PASS": evidence.adversarial_pass, "CHECKER_INDEPENDENT": evidence.checker_independent_enough,
        "ARTIFACT_CONTRACT_PASS": evidence.artifact_contract_pass, "RESOURCE_LIMIT_PASS": evidence.resource_limit_pass,
        "NO_EXISTING_CAPABILITY_DUPLICATE": evidence.no_existing_capability_duplicate, "LICENSE_VERIFIED": evidence.license_verified,
        "TOOL_CONTRACT_EXPLICIT": evidence.tool_contract_explicit, "NETWORK_CONTRACT_EXPLICIT": evidence.network_contract_explicit,
    }
    reasons = tuple(name for name, passed in checks.items() if not passed)
    return PromotionDecision(str(capability_id), not reasons, reasons, iso())


class SkillForge:
    def evaluate(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("IN_PROCESS_SKILL_FORGE_DISABLED_EXTERNAL_SECRETLESS_SANDBOX_REQUIRED")


class FreeProviderFabric:
    def __init__(self, *args: Any, **kwargs: Any):
        raise RuntimeError("IN_MEMORY_PROVIDER_FABRIC_DISABLED_USE_DURABLE_PROVIDER_FABRIC")


def classify_gap(*, synthetic: bool, current: bool, funding_verified: bool) -> DemandClass:
    if synthetic:
        return DemandClass.SYNTHETIC_FIXTURE
    if not current:
        return DemandClass.STALE_SIGNAL
    if funding_verified:
        return DemandClass.VERIFIED_MISSED_CASH
    return DemandClass.CURRENT_UNVERIFIED_DEMAND_SIGNAL


from .durable_doctor import DurableDoctor as Doctor  # noqa: E402
