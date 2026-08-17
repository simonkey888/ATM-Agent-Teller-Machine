from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .execution_jobs import ProgressReceipt
from .workers import canonical_hash, canonical_json

PORTFOLIO_TOP_K = 5
MAX_PROBE_EPISODES = 3
MUTABLE_CLAIM_POLICY = "ONE_NORMAL_MUTABLE_CLAIM_LANE"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EpisodeState(StrEnum):
    OPTION = "OPTION"
    PROBING = "PROBING"
    COMMITTED = "COMMITTED"
    WAITING_EXTERNAL = "WAITING_EXTERNAL"
    MONITORING = "MONITORING"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PAID = "PAID"
    KILLED = "KILLED"


class EconomicEpisode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    episode_id: str
    canonical_opportunity_id: str
    source: str
    opportunity_type: str
    acceptance_contract_hash: str
    funding_evidence_refs: list[str] = Field(default_factory=list)
    posterior_snapshot: dict[str, Any] = Field(default_factory=dict)
    next_reversible_probe: str | None = None
    max_time_budget_seconds: int
    kill_condition: str
    economic_state: EpisodeState = EpisodeState.OPTION
    active_execution_jobs: list[str] = Field(default_factory=list)
    progress_summary: dict[str, Any] = Field(default_factory=dict)
    terminal_reason: str | None = None
    book: str = "CASHFLOW_BOOK"
    option_value: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utcnow_iso)
    updated_at: str = Field(default_factory=utcnow_iso)

    @field_validator("max_time_budget_seconds")
    @classmethod
    def bounded_budget(cls, value: int) -> int:
        if value <= 0 or value > 7 * 24 * 3600:
            raise ValueError("episode time budget outside bounded range")
        return value

    @field_validator("book")
    @classmethod
    def book_enum(cls, value: str) -> str:
        value = value.upper()
        if value not in {"CASHFLOW_BOOK", "OPTION_BOOK"}:
            raise ValueError("invalid episode book")
        return value


class ProgressDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    no_progress_streak: int
    repeated_error_streak: int
    reason: str


class ProgressPolicy:
    """Deterministic stall policy; workers cannot alter or extend it."""

    @staticmethod
    def decide(receipts: list[ProgressReceipt]) -> ProgressDecision:
        no_progress = 0
        for receipt in reversed(receipts):
            if receipt.measurable_progress:
                break
            no_progress += 1
        repeated = 0
        signature = None
        for receipt in reversed(receipts):
            if not receipt.error_signature:
                break
            if signature is None:
                signature = receipt.error_signature
            if receipt.error_signature != signature or receipt.new_evidence_refs:
                break
            repeated += 1
        if repeated >= 3:
            return ProgressDecision(action="KILL", no_progress_streak=no_progress, repeated_error_streak=repeated, reason="SAME_ERROR_3X_NO_NEW_EVIDENCE")
        if no_progress >= 3:
            return ProgressDecision(action="KILL", no_progress_streak=no_progress, repeated_error_streak=repeated, reason="NO_PROGRESS_3X")
        if no_progress >= 2:
            return ProgressDecision(action="RECOVERY", no_progress_streak=no_progress, repeated_error_streak=repeated, reason="MANDATORY_STRATEGY_CHANGE")
        if no_progress == 1:
            return ProgressDecision(action="REPLAN", no_progress_streak=1, repeated_error_streak=repeated, reason="NO_PROGRESS_WARN")
        return ProgressDecision(action="CONTINUE", no_progress_streak=0, repeated_error_streak=repeated, reason="MEASURABLE_PROGRESS")

    @staticmethod
    def recovery_checkpoint(receipts: list[ProgressReceipt]) -> ProgressReceipt | None:
        for receipt in reversed(receipts):
            if receipt.measurable_progress:
                return receipt
        return None


class EpisodeStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS episodes(
              episode_id TEXT PRIMARY KEY,
              canonical_opportunity_id TEXT NOT NULL UNIQUE,
              episode_json TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mutable_claims(
              canonical_opportunity_id TEXT PRIMARY KEY,
              episode_id TEXT NOT NULL,
              claimed_at TEXT NOT NULL
            );
            """
        )

    def close(self) -> None:
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.conn.close()

    def put(self, episode: EconomicEpisode) -> EconomicEpisode:
        episode = episode.model_copy(update={"updated_at": utcnow_iso()})
        self.conn.execute(
            "INSERT INTO episodes VALUES(?,?,?,?) ON CONFLICT(episode_id) DO UPDATE SET episode_json=excluded.episode_json,updated_at=excluded.updated_at",
            (episode.episode_id, episode.canonical_opportunity_id, canonical_json(episode.model_dump(mode="json")), episode.updated_at),
        )
        return episode

    def create_option(
        self,
        *,
        canonical_opportunity_id: str,
        source: str,
        opportunity_type: str,
        acceptance_contract_hash: str,
        funding_evidence_refs: list[str],
        max_time_budget_seconds: int,
        kill_condition: str,
        book: str,
        option_value: dict[str, Any] | None = None,
    ) -> EconomicEpisode:
        episode_id = "episode_" + canonical_hash({"id": canonical_opportunity_id, "acceptance": acceptance_contract_hash})[:40]
        episode = EconomicEpisode(
            episode_id=episode_id,
            canonical_opportunity_id=canonical_opportunity_id,
            source=source,
            opportunity_type=opportunity_type,
            acceptance_contract_hash=acceptance_contract_hash,
            funding_evidence_refs=funding_evidence_refs,
            max_time_budget_seconds=max_time_budget_seconds,
            kill_condition=kill_condition,
            book=book,
            option_value=option_value or {},
        )
        return self.put(episode)

    def get(self, episode_id: str) -> EconomicEpisode:
        row = self.conn.execute("SELECT episode_json FROM episodes WHERE episode_id=?", (episode_id,)).fetchone()
        if row is None:
            raise KeyError(episode_id)
        return EconomicEpisode.model_validate_json(str(row["episode_json"]))

    def top_options(self, limit: int = PORTFOLIO_TOP_K) -> list[EconomicEpisode]:
        limit = max(1, min(PORTFOLIO_TOP_K, int(limit)))
        rows = self.conn.execute("SELECT episode_json FROM episodes ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [EconomicEpisode.model_validate_json(str(row["episode_json"])) for row in rows]

    def begin_probe(self, episode_id: str) -> EconomicEpisode:
        active = self.conn.execute("SELECT episode_json FROM episodes").fetchall()
        probing = sum(EconomicEpisode.model_validate_json(str(row["episode_json"])).economic_state == EpisodeState.PROBING for row in active)
        if probing >= MAX_PROBE_EPISODES:
            raise ValueError("max probe episodes reached")
        episode = self.get(episode_id)
        if episode.economic_state != EpisodeState.OPTION:
            raise ValueError("only OPTION may enter PROBING")
        return self.put(episode.model_copy(update={"economic_state": EpisodeState.PROBING}))

    def commit_mutable_claim(self, episode_id: str) -> EconomicEpisode:
        episode = self.get(episode_id)
        if episode.economic_state not in {EpisodeState.OPTION, EpisodeState.PROBING}:
            raise ValueError("episode not claim-eligible")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute("SELECT episode_id FROM mutable_claims WHERE canonical_opportunity_id=?", (episode.canonical_opportunity_id,)).fetchone()
            if row is not None:
                raise ValueError("exclusive mutable claim already exists")
            self.conn.execute("INSERT INTO mutable_claims VALUES(?,?,?)", (episode.canonical_opportunity_id, episode_id, utcnow_iso()))
            committed = episode.model_copy(update={"economic_state": EpisodeState.COMMITTED, "updated_at": utcnow_iso()})
            self.conn.execute("UPDATE episodes SET episode_json=?,updated_at=? WHERE episode_id=?", (canonical_json(committed.model_dump(mode="json")), committed.updated_at, episode_id))
            self.conn.execute("COMMIT")
            return committed
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    @staticmethod
    def realized_money_contribution(episode: EconomicEpisode) -> int:
        # Option/reputation/information values are explicitly non-money.
        return 0
