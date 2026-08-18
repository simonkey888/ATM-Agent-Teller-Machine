from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .workers import canonical_json


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SkillStage(StrEnum):
    SOLVE = "SOLVE"
    DISTILL = "DISTILL"
    REPLAY = "REPLAY"
    ADVERSARIAL_TEST = "ADVERSARIAL_TEST"
    SHADOW_LIVE = "SHADOW_LIVE"
    LOW_RISK_CANARY = "LOW_RISK_CANARY"
    PROMOTE = "PROMOTE"


SKILL_PIPELINE = [stage.value for stage in SkillStage]
MIN_CANARY_SAMPLE_SIZE = 1


class SkillCapsule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill_id: str
    capability: str
    code_refs: list[str]
    test_refs: list[str]
    fixture_refs: list[str]
    failure_signature_refs: list[str] = Field(default_factory=list)
    stage: SkillStage = SkillStage.SOLVE
    baseline_metric: float | None = None
    candidate_metric: float | None = None
    metric_name: str | None = None
    promoted_by: str | None = None
    created_at: str = Field(default_factory=utcnow_iso)
    updated_at: str = Field(default_factory=utcnow_iso)

    @field_validator("code_refs", "test_refs", "fixture_refs")
    @classmethod
    def nonempty_evidence(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("SkillCapsule requires code, tests and fixtures")
        return values


class CanaryEvidence(BaseModel):
    """Durable measured LOW_RISK_CANARY result required before PROMOTE."""

    model_config = ConfigDict(extra="forbid")
    skill_id: str
    evidence_ref: str
    sample_size: int
    success_count: int
    candidate_metric: float
    recorded_by: str
    created_at: str = Field(default_factory=utcnow_iso)

    @field_validator("sample_size")
    @classmethod
    def sample_positive(cls, value: int) -> int:
        if value < MIN_CANARY_SAMPLE_SIZE:
            raise ValueError("canary sample size below enforced minimum")
        return value

    @model_validator(mode="after")
    def successful_bounded_canary(self) -> "CanaryEvidence":
        if self.success_count < 0 or self.success_count > self.sample_size:
            raise ValueError("canary success_count outside sample")
        if self.success_count != self.sample_size:
            raise ValueError("promotion canary requires all bounded samples to pass")
        if not self.recorded_by.startswith("ATM_SUPERVISOR"):
            raise ValueError("canary evidence must be recorded by ATM supervisor")
        if not self.evidence_ref.strip():
            raise ValueError("canary evidence_ref required")
        return self


class CalibrationObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observation_id: str
    source: str
    opportunity_class: str
    predicted_band: str
    observed_outcome: str
    evidence_refs: list[str]
    synthetic: bool = False
    created_at: str = Field(default_factory=utcnow_iso)

    @field_validator("predicted_band")
    @classmethod
    def band(cls, value: str) -> str:
        value = value.upper()
        if value not in {"LOW", "MEDIUM", "HIGH", "UNKNOWN"}:
            raise ValueError("invalid bounded probability band")
        return value


class HighTicketCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    canonical_opportunity_id: str
    source: str
    explicit_paid_demand: bool
    funding_verified: bool
    reward_usd: float
    estimated_hours: float
    acceptance_contract_hash: str
    payment_evidence_refs: list[str]
    state: str = "SHADOW_ONLY"


class HighTicketRadar:
    """Read-only classifier. It never originates demand, claims or submits."""

    @staticmethod
    def classify(candidate: HighTicketCandidate) -> str:
        if not candidate.explicit_paid_demand:
            return "REJECT_NO_EXTERNAL_PAID_DEMAND"
        if not candidate.funding_verified or not candidate.payment_evidence_refs:
            return "REJECT_UNVERIFIED_PAYMENT_PATH"
        if candidate.estimated_hours <= 3:
            return "CASHFLOW_HORIZON"
        if not candidate.acceptance_contract_hash:
            return "REJECT_NO_DETERMINISTIC_ACCEPTANCE"
        return "OPTION_BOOK_HIGH_TICKET_SHADOW"


class LearningStore:
    """Canonical Skill Forge/calibration authority with immutable identity and promotion history."""

    IMMUTABLE_SKILL_FIELDS = (
        "skill_id",
        "capability",
        "code_refs",
        "test_refs",
        "fixture_refs",
        "baseline_metric",
        "metric_name",
        "created_at",
    )

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS skills(
              skill_id TEXT PRIMARY KEY,
              skill_json TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS skill_history(
              skill_id TEXT NOT NULL,
              seq INTEGER NOT NULL,
              from_stage TEXT NOT NULL,
              to_stage TEXT NOT NULL,
              evidence_ref TEXT NOT NULL,
              supervisor TEXT NOT NULL,
              created_at TEXT NOT NULL,
              PRIMARY KEY(skill_id,seq)
            );
            CREATE TABLE IF NOT EXISTS skill_canary_evidence(
              skill_id TEXT NOT NULL,
              evidence_ref TEXT NOT NULL,
              evidence_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              PRIMARY KEY(skill_id,evidence_ref)
            );
            CREATE TABLE IF NOT EXISTS calibration(
              observation_id TEXT PRIMARY KEY,
              observation_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            """
        )

    def close(self) -> None:
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.conn.close()

    def _write_skill(self, skill: SkillCapsule) -> SkillCapsule:
        skill = skill.model_copy(update={"updated_at": utcnow_iso()})
        self.conn.execute(
            "INSERT INTO skills VALUES(?,?,?) ON CONFLICT(skill_id) DO UPDATE SET skill_json=excluded.skill_json,updated_at=excluded.updated_at",
            (skill.skill_id, canonical_json(skill.model_dump(mode="json")), skill.updated_at),
        )
        return skill

    def put_skill(self, skill: SkillCapsule) -> SkillCapsule:
        """Register once. Re-registration cannot reset identity, baseline or promotion stage."""
        row = self.conn.execute("SELECT skill_json FROM skills WHERE skill_id=?", (skill.skill_id,)).fetchone()
        if row is None:
            if skill.stage != SkillStage.SOLVE or skill.candidate_metric is not None or skill.promoted_by is not None:
                raise ValueError("new skill registration must begin at SOLVE with no candidate/promoter state")
            return self._write_skill(skill)
        existing = SkillCapsule.model_validate_json(str(row["skill_json"]))
        for field in self.IMMUTABLE_SKILL_FIELDS:
            if getattr(existing, field) != getattr(skill, field):
                raise ValueError(f"skill identity/baseline mutation forbidden: {field}")
        if skill.stage != existing.stage or skill.promoted_by != existing.promoted_by:
            raise ValueError("skill re-registration cannot reset or advance promotion stage")
        if skill.candidate_metric != existing.candidate_metric or skill.failure_signature_refs != existing.failure_signature_refs:
            raise ValueError("skill semantic mutation requires supervisor advance_skill")
        return existing

    def get_skill(self, skill_id: str) -> SkillCapsule:
        row = self.conn.execute("SELECT skill_json FROM skills WHERE skill_id=?", (skill_id,)).fetchone()
        if row is None:
            raise KeyError(skill_id)
        return SkillCapsule.model_validate_json(str(row["skill_json"]))

    def history(self, skill_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM skill_history WHERE skill_id=? ORDER BY seq ASC", (skill_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def _append_history(self, skill_id: str, from_stage: SkillStage, to_stage: SkillStage, evidence_ref: str, supervisor: str) -> None:
        row = self.conn.execute("SELECT COALESCE(MAX(seq),0) AS max_seq FROM skill_history WHERE skill_id=?", (skill_id,)).fetchone()
        seq = int(row["max_seq"]) + 1
        self.conn.execute(
            "INSERT INTO skill_history VALUES(?,?,?,?,?,?,?)",
            (skill_id, seq, from_stage.value, to_stage.value, evidence_ref, supervisor, utcnow_iso()),
        )

    def record_canary_result(
        self,
        skill_id: str,
        *,
        supervisor: str,
        evidence_ref: str,
        sample_size: int,
        success_count: int,
        candidate_metric: float,
    ) -> CanaryEvidence:
        skill = self.get_skill(skill_id)
        if skill.stage != SkillStage.LOW_RISK_CANARY:
            raise ValueError("canary evidence may only be recorded at LOW_RISK_CANARY")
        evidence = CanaryEvidence(
            skill_id=skill_id,
            evidence_ref=evidence_ref,
            sample_size=sample_size,
            success_count=success_count,
            candidate_metric=candidate_metric,
            recorded_by=supervisor,
        )
        existing = self.conn.execute(
            "SELECT evidence_json FROM skill_canary_evidence WHERE skill_id=? AND evidence_ref=?",
            (skill_id, evidence_ref),
        ).fetchone()
        if existing:
            prior = CanaryEvidence.model_validate_json(str(existing["evidence_json"]))
            if prior != evidence:
                raise ValueError("conflicting canary evidence")
            return prior
        self.conn.execute(
            "INSERT INTO skill_canary_evidence VALUES(?,?,?,?)",
            (skill_id, evidence_ref, canonical_json(evidence.model_dump(mode="json")), evidence.created_at),
        )
        return evidence

    def latest_canary(self, skill_id: str) -> CanaryEvidence | None:
        row = self.conn.execute(
            "SELECT evidence_json FROM skill_canary_evidence WHERE skill_id=? ORDER BY created_at DESC LIMIT 1",
            (skill_id,),
        ).fetchone()
        return CanaryEvidence.model_validate_json(str(row["evidence_json"])) if row else None

    def advance_skill(self, skill_id: str, *, supervisor: str, evidence_ref: str, candidate_metric: float | None = None) -> SkillCapsule:
        if not supervisor.startswith("ATM_SUPERVISOR"):
            raise ValueError("skills cannot self-promote")
        if not evidence_ref:
            raise ValueError("promotion evidence_ref required")
        skill = self.get_skill(skill_id)
        index = SKILL_PIPELINE.index(skill.stage.value)
        if index >= len(SKILL_PIPELINE) - 1:
            raise ValueError("skill already promoted")
        next_stage = SkillStage(SKILL_PIPELINE[index + 1])
        failures = list(skill.failure_signature_refs)
        failures.append(f"promotion-evidence:{evidence_ref}")
        update: dict[str, Any] = {"stage": next_stage, "failure_signature_refs": failures}
        if candidate_metric is not None:
            update["candidate_metric"] = candidate_metric
        if next_stage == SkillStage.PROMOTE:
            history = self.history(skill_id)
            expected = [
                (SkillStage.SOLVE.value, SkillStage.DISTILL.value),
                (SkillStage.DISTILL.value, SkillStage.REPLAY.value),
                (SkillStage.REPLAY.value, SkillStage.ADVERSARIAL_TEST.value),
                (SkillStage.ADVERSARIAL_TEST.value, SkillStage.SHADOW_LIVE.value),
                (SkillStage.SHADOW_LIVE.value, SkillStage.LOW_RISK_CANARY.value),
            ]
            actual = [(row["from_stage"], row["to_stage"]) for row in history]
            if actual != expected:
                raise ValueError("promotion requires complete durable canary history")
            canary = self.latest_canary(skill_id)
            if canary is None:
                raise ValueError("promotion requires durable measured canary evidence")
            if candidate_metric is not None and candidate_metric != canary.candidate_metric:
                raise ValueError("promotion metric must match durable canary evidence")
            measured_metric = canary.candidate_metric
            if skill.baseline_metric is None or not skill.metric_name:
                raise ValueError("promotion requires measured baseline and candidate metric")
            improved = measured_metric > skill.baseline_metric if "success" in skill.metric_name.lower() else measured_metric < skill.baseline_metric
            if not improved:
                raise ValueError("promotion requires measurable improvement against baseline")
            update["candidate_metric"] = measured_metric
            update["promoted_by"] = supervisor
            failures.append(f"canary-evidence:{canary.evidence_ref}:{canary.success_count}/{canary.sample_size}")
            update["failure_signature_refs"] = failures
        updated = self._write_skill(skill.model_copy(update=update))
        self._append_history(skill_id, skill.stage, next_stage, evidence_ref, supervisor)
        return updated

    def observe(self, observation: CalibrationObservation) -> bool:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO calibration VALUES(?,?,?)",
            (observation.observation_id, canonical_json(observation.model_dump(mode="json")), observation.created_at),
        )
        return cur.rowcount == 1

    def calibration_summary(self, opportunity_class: str) -> dict[str, Any]:
        rows = self.conn.execute("SELECT observation_json FROM calibration").fetchall()
        observations = [CalibrationObservation.model_validate_json(str(row["observation_json"])) for row in rows]
        matched = [row for row in observations if row.opportunity_class == opportunity_class]
        production = [row for row in matched if not row.synthetic]
        synthetic = [row for row in matched if row.synthetic]
        outcomes: dict[str, int] = {}
        for row in production:
            outcomes[row.observed_outcome] = outcomes.get(row.observed_outcome, 0) + 1
        return {
            "opportunity_class": opportunity_class,
            "sample_size": len(production),
            "synthetic_sample_size": len(synthetic),
            "outcomes": outcomes,
            "posterior_band": "UNKNOWN" if len(production) < 5 else "EMPIRICAL_BOUNDED",
            "fake_precision": False,
            "synthetic_excluded_from_production_metrics": True,
        }
