from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from .zungun_worker import LinkDoctorFinding, LinkDoctorReceipt, ZungunWorkerAdapter


class ReliabilityJournal:
    """Small deterministic receiver/client model used only for worker qualification."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ops(
              operation_id TEXT PRIMARY KEY,
              state TEXT NOT NULL,
              progress INTEGER NOT NULL DEFAULT 0,
              total INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS effects(
              operation_id TEXT PRIMARY KEY,
              applied INTEGER NOT NULL DEFAULT 1
            );
            """
        )

    def close(self) -> None:
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.conn.close()

    def queue(self, operation_id: str, total: int = 1) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO ops(operation_id,state,progress,total) VALUES(?, 'PENDING', 0, ?)",
            (operation_id, total),
        )

    def state(self, operation_id: str) -> str:
        row = self.conn.execute("SELECT state FROM ops WHERE operation_id=?", (operation_id,)).fetchone()
        if row is None:
            raise KeyError(operation_id)
        return str(row[0])

    def apply_receiver(self, operation_id: str, *, ack_lost: bool = False) -> None:
        self.conn.execute("INSERT OR IGNORE INTO effects(operation_id) VALUES(?)", (operation_id,))
        self.conn.execute(
            "UPDATE ops SET state=? WHERE operation_id=?",
            ("UNKNOWN" if ack_lost else "COMPLETE", operation_id),
        )

    def reconcile(self, operation_id: str) -> None:
        exists = self.conn.execute("SELECT 1 FROM effects WHERE operation_id=?", (operation_id,)).fetchone() is not None
        self.conn.execute("UPDATE ops SET state=? WHERE operation_id=?", ("COMPLETE" if exists else "PENDING", operation_id))

    def effect_count(self, operation_id: str) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM effects WHERE operation_id=?", (operation_id,)).fetchone()[0])

    def checkpoint_progress(self, operation_id: str, progress: int) -> None:
        row = self.conn.execute("SELECT total FROM ops WHERE operation_id=?", (operation_id,)).fetchone()
        if row is None:
            raise KeyError(operation_id)
        total = int(row[0])
        if progress < 0 or progress > total:
            raise ValueError("progress outside authoritative range")
        state = "COMPLETE" if progress == total else "PENDING"
        self.conn.execute("UPDATE ops SET progress=?,state=? WHERE operation_id=?", (progress, state, operation_id))

    def progress(self, operation_id: str) -> tuple[int, int, str]:
        row = self.conn.execute("SELECT progress,total,state FROM ops WHERE operation_id=?", (operation_id,)).fetchone()
        if row is None:
            raise KeyError(operation_id)
        return int(row[0]), int(row[1]), str(row[2])


def _q7_link_doctor_contract() -> None:
    receipt = LinkDoctorReceipt(
        source_head="a325bc41233eba9e72b727e085965bb9136eaa73",
        findings=[
            LinkDoctorFinding(
                rule_id="ZL001_UNSAFE_SIDE_EFFECT_RETRY",
                severity="ERROR",
                path="fixture/unsafe.ts",
                evidence="retry behavior without visible idempotency/reconciliation contract",
                explanation="retry can duplicate effects after an ambiguous response",
                limitation="static heuristic",
                assurance_tier="L1",
                status="WARN",
            ),
            LinkDoctorFinding(
                rule_id="ZL015_NO_RECEIVER_RECONCILIATION_PATH",
                severity="WARNING",
                path="fixture/unsafe.ts",
                evidence="uncertain path without receiver reconciliation",
                explanation="ambiguous outcomes require receiver reconciliation",
                limitation="static heuristic",
                assurance_tier="L1",
                status="UNKNOWN",
            ),
        ],
        deterministic=True,
        overall_status="UNKNOWN",
    )
    ZungunWorkerAdapter.validate_link_doctor(receipt)


def run_core_qualification() -> dict[str, str]:
    """Q1-Q7 deterministic qualification; Q8 lives in resolver negative-control tests."""
    result: dict[str, str] = {}
    with tempfile.TemporaryDirectory() as directory:
        db = Path(directory) / "reliability.sqlite3"
        journal = ReliabilityJournal(db)

        # Q1: receiver effect happens, ACK is lost, retry does not duplicate.
        journal.queue("q1")
        journal.apply_receiver("q1", ack_lost=True)
        assert journal.state("q1") == "UNKNOWN"
        journal.apply_receiver("q1", ack_lost=True)
        assert journal.effect_count("q1") == 1
        journal.reconcile("q1")
        assert journal.state("q1") == "COMPLETE"
        result["Q1_retry_ambiguity"] = "PASS"

        # Q2: pending queue survives process death/reopen.
        journal.queue("q2")
        journal.close()
        journal = ReliabilityJournal(db)
        assert journal.state("q2") == "PENDING"
        result["Q2_process_death"] = "PASS"

        # Q3: offline pending operation converges once on reconnect.
        journal.queue("q3")
        assert journal.state("q3") == "PENDING"
        journal.apply_receiver("q3")
        assert journal.state("q3") == "COMPLETE" and journal.effect_count("q3") == 1
        result["Q3_reconnect"] = "PASS"

        # Q4: duplicate executors reuse stable operation identity.
        journal.queue("q4")
        journal.apply_receiver("q4", ack_lost=True)
        journal.apply_receiver("q4")
        assert journal.effect_count("q4") == 1 and journal.state("q4") == "COMPLETE"
        result["Q4_concurrent_retry"] = "PASS"

        # Q5: completion derives from authoritative persisted progress, not client-local assumption.
        journal.queue("q5", total=4)
        journal.checkpoint_progress("q5", 2)
        journal.close()
        journal = ReliabilityJournal(db)
        progress, total, state = journal.progress("q5")
        assert (progress, total, state) == (2, 4, "PENDING")
        journal.checkpoint_progress("q5", 4)
        assert journal.progress("q5") == (4, 4, "COMPLETE")
        result["Q5_resumable_transfer"] = "PASS"

        # Q6: blackout during an effect preserves UNKNOWN until receiver reconciliation.
        journal.queue("q6")
        journal.apply_receiver("q6", ack_lost=True)
        assert journal.state("q6") == "UNKNOWN"
        journal.reconcile("q6")
        assert journal.state("q6") == "COMPLETE"
        result["Q6_blackout"] = "PASS"
        journal.close()

    _q7_link_doctor_contract()
    result["Q7_link_doctor"] = "PASS"
    return result


def qualification_evidence() -> dict[str, Any]:
    return {
        "schema": "ATM_ZUNGUN_QUALIFICATION_V1",
        "results": run_core_qualification(),
        "semantic_locks": {
            "UNKNOWN_is_PASS": False,
            "AMBIGUOUS_is_SUCCESS": False,
            "transport_accepted_is_receiver_effect": False,
            "http_2xx_is_business_effect": False,
            "retryable_is_idempotent": False,
            "emulator_is_OEM_proof": False,
        },
        "outgoing_spend_usd": 0,
        "economic_authority": False,
    }
