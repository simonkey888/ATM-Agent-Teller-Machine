from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .workers import canonical_hash, canonical_json


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


class CalibrationObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observation_id: str
    source: str
    opportunity_class: str
    predicted_band: str
    observed_outcome: str
    evidence_refs: list[str]
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

    def put_skill(self, skill: SkillCapsule) -> SkillCapsule:
        skill = skill.model_copy(update={"updated_at": utcnow_iso()})
        self.conn.execute(
            "INSERT INTO skills VALUES(?,?,?) ON CONFLICT(skill_id) DO UPDATE SET skill_json=excluded.skill_json,updated_at=excluded.updated_at",
            (skill.skill_id, canonical_json(skill.model_dump(mode="json")), skill.updated_at),
        )
        return skill

    def get_skill(self, skill_id: str) -> SkillCapsule:
        row = self.conn.execute("SELECT skill_json FROM skills WHERE skill_id=?", (skill_id,)).fetchone()
        if row is None:
            raise KeyError(skill_id)
        return SkillCapsule.model_validate_json(str(row["skill_json"]))

    def advance_skill(self, skill_id: str, *, supervisor: str, evidence_ref: str, candidate_metric: float | None = None) -> SkillCapsule:
        if not supervisor.startswith("ATM_SUPERVISOR"):
            raise ValueError("skills cannot self-promote")
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
            if skill.baseline_metric is None or candidate_metric is None or not skill.metric_name:
                raise ValueError("promotion requires measured baseline and candidate metric")
            # Lower is better for time/error metrics; explicitly named success_rate uses higher-is-better.
            improved = candidate_metric > skill.baseline_metric if "success" in skill.metric_name.lower() else candidate_metric < skill.baseline_metric
            if not improved:
                raise ValueError("promotion requires measurable improvement against baseline")
            update["promoted_by"] = supervisor
        return self.put_skill(skill.model_copy(update=update))

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
        outcomes: dict[str, int] = {}
        for row in matched:
            outcomes[row.observed_outcome] = outcomes.get(row.observed_outcome, 0) + 1
        return {
            "opportunity_class": opportunity_class,
            "sample_size": len(matched),
            "outcomes": outcomes,
            "posterior_band": "UNKNOWN" if len(matched) < 5 else "EMPIRICAL_BOUNDED",
            "fake_precision": False,
        }
